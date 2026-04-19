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


def compile_tex_to_pdf(
    tex_source: str,
    output_path: str,
    aux_dir: Optional[str] = None,
    main_filename: str = "document.tex",
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
        main_filename: Filename for the main .tex entry point (default: document.tex).
        compiler:      "pdflatex" | "xelatex" | "lualatex". Defaults to env var.
        retries:       Number of retry attempts on transient network errors.

    Returns:
        output_path on success.

    Raises:
        RuntimeError if compilation fails after all retries.
    """
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
    POST .tex source (+ any aux files) to TeXLive.net as multipart form data.
    Returns raw PDF bytes on success, raises RuntimeError on compile failure.

    TeXLive.net requires filecontents[] and filename[] fields to be strictly
    interleaved in matching order (file1-content, file1-name, file2-content,
    file2-name, ...). We achieve this by sending EVERYTHING through the
    `files` parameter as a single ordered list — never splitting across
    `files` and `data`, which would reorder the parts.

    Field order (per TeXLive.net docs):
        filecontents[]  file body (text/plain for .tex)
        filename[]      matching filename string
        ... repeat for each file ...
        engine          pdflatex | xelatex | lualatex
        return          pdf
    """
    # All fields in a single list to guarantee multipart ordering
    form: list = []

    # Main .tex file — always first
    form.append(("filecontents[]", (main_filename, tex_source.encode("utf-8"), "text/plain")))
    form.append(("filename[]",     (None, main_filename)))

    # Auxiliary files (.cls, .sty, images, etc.)
    if aux_dir and os.path.isdir(aux_dir):
        for path in sorted(Path(aux_dir).iterdir()):
            if path.is_file() and path.suffix in (".cls", ".sty", ".bst", ".png", ".jpg", ".pdf", ".eps"):
                with open(str(path), "rb") as f:
                    content = f.read()
                form.append(("filecontents[]", (path.name, content, "application/octet-stream")))
                form.append(("filename[]",     (None, path.name)))
                logger.debug(f"  Including aux file: {path.name}")

    # Compiler settings — appended after all file pairs
    form.append(("engine", (None, compiler)))
    form.append(("return", (None, "pdf")))

    resp = requests.post(
        _TEXLIVE_URL,
        files=form,          # single list → preserves insertion order in multipart body
        timeout=120,
        allow_redirects=True,
    )

    # Success: 200 with PDF content
    if resp.status_code == 200 and resp.content[:4] == b"%PDF":
        return resp.content

    # 200 but got a log instead of PDF — compile error
    if resp.status_code == 200:
        raise RuntimeError(f"LaTeX compile error (log):\n{resp.text[:1500]}")

    # HTTP error
    raise RuntimeError(
        f"TeXLive.net HTTP {resp.status_code}:\n{resp.text[:800]}"
    )
