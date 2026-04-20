# -*- coding: utf-8 -*-
"""
LaTeX Compiler
==============
Compiles a .tex file to a PDF using the TeXLive.net REST API.
No local LaTeX installation required.

Service: https://texlive.net  (run by Dante e.V., the German TeX users group)
  - Full TeXLive distribution — supports ALL packages including fontawesome5,
    paracol, charter, eso-pic, and any other package on CTAN.
  - Free, no account, no API key.
  - Endpoint: POST https://texlive.net/cgi-bin/latexcgi (multipart form)

Compiler options (set LATEX_COMPILER in .env):
  pdflatex  — default, works for most templates
  xelatex   — for templates using fontspec / custom TTF fonts
  lualatex  — for LuaLaTeX templates

Dependencies: pip install requests
"""

import os
import time
from pathlib import Path
from typing import Optional

import requests

from utils.logger import get_logger

logger = get_logger(__name__)

# TeXLive.net endpoint — full TeXLive, all packages, free
_TEXLIVE_URL = "https://texlive.net/cgi-bin/latexcgi"

# Compiler — override with LATEX_COMPILER env var
_COMPILER = os.getenv("LATEX_COMPILER", "pdflatex")


# TeXLive.net requires the main document to be named exactly "document.tex".
# Any other name causes "Bad form type: no main document".
# Source: https://davidcarlisle.github.io/latexcgi
_MAIN_TEX_FILENAME = "document.tex"


def compile_tex_to_pdf(
    tex_source: str,
    output_path: str,
    aux_dir: Optional[str] = None,
    main_filename: str = _MAIN_TEX_FILENAME,   # always overridden to main.tex below
    compiler: Optional[str] = None,
    retries: int = 2,
) -> str:
    """
    Compile LaTeX source to a PDF via TeXLive.net and write it to output_path.

    Args:
        tex_source:    Full content of the .tex file.
        output_path:   Where to write the resulting PDF.
        aux_dir:       Optional directory containing auxiliary files (.cls, .sty,
                       images, etc.). Each file in the directory is sent as a
                       separate field in the multipart form.
        main_filename: Ignored — always compiled as "main.tex" so TeXLive.net
                       reliably identifies the entry point regardless of the
                       original filename (resume.tex, cv.tex, etc.).
        compiler:      "pdflatex" | "xelatex" | "lualatex". Defaults to env var.
        retries:       Number of retry attempts on transient network errors.

    Returns:
        output_path on success.

    Raises:
        RuntimeError if compilation fails after all retries.
    """
    # Always use main.tex — never the caller-supplied name
    main_filename = _MAIN_TEX_FILENAME
    compiler = compiler or _COMPILER
    out_name = Path(output_path).name
    logger.info(f"Compiling LaTeX ({compiler}) via TeXLive.net → {out_name}")

    last_error = None
    for attempt in range(1, retries + 2):
        try:
            pdf_bytes = _compile(tex_source, main_filename, compiler, aux_dir)
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, "wb") as f:
                f.write(pdf_bytes)
            logger.info(f"PDF compiled successfully ({len(pdf_bytes):,} bytes): {output_path}")
            return output_path

        except RuntimeError:
            # LaTeX compile error (bad .tex) — do not retry, surface immediately
            raise

        except Exception as e:
            last_error = e
            if attempt <= retries:
                wait = 5 * attempt
                logger.warning(f"Compile attempt {attempt} failed ({e}). Retrying in {wait}s...")
                time.sleep(wait)
            else:
                raise RuntimeError(
                    f"LaTeX compile failed after {retries + 1} attempts: {last_error}"
                ) from last_error


def _compile(
    tex_source: str,
    main_filename: str,
    compiler: str,
    aux_dir: Optional[str],
) -> bytes:
    """
    POST .tex source (+ any aux files) to TeXLive.net and return raw PDF bytes.

    Protocol (per https://davidcarlisle.github.io/latexcgi):
      1. POST multipart form to https://texlive.net/cgi-bin/latexcgi
           - filecontents[] / filename[] pairs (interleaved, main file first)
           - filename[] value for the main file MUST be "document.tex"
           - engine field (pdflatex / xelatex / lualatex)
           - NO "return" field — any unrecognised field rejects the whole form
      2. On compile success: server returns HTTP 301 → Location header points
         to /pdfjs/web/viewer.html?file=/latexcgi/document_XXXX.pdf
      3. Extract the PDF path from the Location header and fetch it directly.
      4. On compile failure: server returns HTTP 200 with the plain-text log.

    IMPORTANT: do NOT follow the redirect (allow_redirects=False).
    """
    # Build form — all fields in one list to preserve multipart ordering
    form: list = []

    # Main .tex — MUST be named "document.tex" (TeXLive.net hard requirement)
    form.append(("filecontents[]", (main_filename, tex_source.encode("utf-8"), "text/plain")))
    form.append(("filename[]",     (None, main_filename)))

    # Auxiliary files (.cls, .sty, images, etc.)
    if aux_dir and os.path.isdir(aux_dir):
        for path in sorted(Path(aux_dir).iterdir()):
            if path.is_file() and path.suffix in (".cls", ".sty", ".bst", ".png", ".jpg", ".eps"):
                # .pdf excluded — resume.pdf next to resume.tex is the source
                # document, not a LaTeX resource.
                with open(str(path), "rb") as f:
                    content = f.read()
                form.append(("filecontents[]", (path.name, content, "application/octet-stream")))
                form.append(("filename[]",     (None, path.name)))
                logger.debug(f"  Including aux file: {path.name}")

    # Engine — only the engine field; no "return" field (unknown fields reject the form)
    form.append(("engine", (None, compiler)))

    # Step 1: POST — do NOT follow the redirect
    resp = requests.post(
        _TEXLIVE_URL,
        files=form,
        timeout=120,
        allow_redirects=False,   # we handle the redirect manually
    )

    # Step 2: 301 = compile succeeded; Location header has the PDF viewer URL
    if resp.status_code == 301:
        location = resp.headers.get("Location", "")
        # Location: /pdfjs/web/viewer.html?file=/latexcgi/document_XXXX.pdf
        # Extract the PDF path from the "file=" query parameter
        import re as _re
        match = _re.search(r"file=(/latexcgi/[^&]+\.pdf)", location)
        if not match:
            raise RuntimeError(
                f"TeXLive.net 301 but no PDF path in Location header: {location!r}"
            )
        pdf_url = f"https://texlive.net{match.group(1)}"
        logger.debug(f"Fetching compiled PDF from: {pdf_url}")
        pdf_resp = requests.get(pdf_url, timeout=60)
        if pdf_resp.status_code != 200 or pdf_resp.content[:4] != b"%PDF":
            raise RuntimeError(
                f"PDF fetch failed (HTTP {pdf_resp.status_code}): {pdf_resp.text[:400]}"
            )
        return pdf_resp.content

    # 200 = compile error — server returns the plain-text log
    if resp.status_code == 200:
        raise RuntimeError(f"LaTeX compile error (log):\n{resp.text[:2000]}")

    # Any other HTTP status
    raise RuntimeError(
        f"TeXLive.net HTTP {resp.status_code}:\n{resp.text[:800]}"
    )
