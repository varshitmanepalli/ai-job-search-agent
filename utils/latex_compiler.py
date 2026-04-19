# -*- coding: utf-8 -*-
"""
LaTeX Compiler
==============
Compiles a .tex file to a PDF using the latexonline.cc REST API.
No local LaTeX installation required.

API reference: https://github.com/aslushnikov/latex-online
  POST https://latexonline.cc/compile
    - Body: raw .tex content (text/plain)   — single-file documents
    - Or:   multipart tarball               — multi-file documents
  Response: PDF binary (200) or error log (4xx)

Optional: set LATEX_COMPILER=xelatex in .env if your template requires
XeLaTeX (e.g. uses fontspec, custom TTF fonts). Default is pdflatex.

Dependencies: pip install requests
"""

import os
import tarfile
import tempfile
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode

import requests

from utils.logger import get_logger

logger = get_logger(__name__)

# Public latexonline.cc instance (free, no auth required)
LATEXONLINE_URL = "https://latexonline.cc/compile"

# Configurable compiler — override with LATEX_COMPILER env var
_COMPILER = os.getenv("LATEX_COMPILER", "pdflatex")  # pdflatex | xelatex | lualatex


def _compile_single_file(tex_source: str, compiler: str) -> bytes:
    """
    POST raw .tex text to latexonline.cc and return the PDF bytes.
    Raises RuntimeError on compilation failure (response body contains the log).
    """
    params = {"command": compiler, "force": "true"}
    url = LATEXONLINE_URL + "?" + urlencode(params)

    resp = requests.post(
        url,
        data=tex_source.encode("utf-8"),
        headers={"Content-Type": "text/plain; charset=utf-8"},
        timeout=90,   # compilation can take 20-40 s on cold cache
    )

    if resp.status_code == 200:
        return resp.content   # raw PDF bytes

    # 4xx → compilation error; log body is the LaTeX error log
    log_snippet = resp.text[:1500]
    raise RuntimeError(
        f"LaTeX compilation failed (HTTP {resp.status_code}):\n{log_snippet}"
    )


def _compile_tarball(tex_dir: str, main_file: str, compiler: str) -> bytes:
    """
    Bundle a directory into a .tar.gz and POST it to latexonline.cc.
    Use this when the template has multiple files (cls, sty, images, etc.).
    """
    with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp:
        tarball_path = tmp.name

    try:
        with tarfile.open(tarball_path, "w:gz") as tar:
            for filepath in Path(tex_dir).rglob("*"):
                if filepath.is_file():
                    arcname = filepath.relative_to(tex_dir)
                    tar.add(str(filepath), arcname=str(arcname))

        params = {"command": compiler, "target": main_file, "force": "true"}
        url = LATEXONLINE_URL + "?" + urlencode(params)

        with open(tarball_path, "rb") as f:
            resp = requests.post(
                url,
                data=f,
                headers={"Content-Type": "application/x-tar"},
                timeout=120,
            )

        if resp.status_code == 200:
            return resp.content
        raise RuntimeError(
            f"LaTeX tarball compile failed (HTTP {resp.status_code}):\n{resp.text[:1500]}"
        )
    finally:
        os.unlink(tarball_path)


def compile_tex_to_pdf(
    tex_source: str,
    output_path: str,
    aux_dir: Optional[str] = None,
    main_filename: str = "resume.tex",
    compiler: Optional[str] = None,
    retries: int = 2,
) -> str:
    """
    Compile LaTeX source to a PDF and write it to output_path.

    Args:
        tex_source:    Full content of the .tex file.
        output_path:   Where to write the resulting PDF.
        aux_dir:       If provided, a directory containing auxiliary files
                       (e.g. .cls, .sty, images). Will be sent as a tarball.
        main_filename: The entry-point filename when sending as a tarball.
        compiler:      "pdflatex" | "xelatex" | "lualatex". Defaults to env var.
        retries:       Number of retry attempts on network error.

    Returns:
        output_path on success.

    Raises:
        RuntimeError if compilation fails after all retries.
    """
    compiler = compiler or _COMPILER
    logger.info(f"Compiling LaTeX ({compiler}) → {Path(output_path).name}")

    last_error = None
    for attempt in range(1, retries + 2):
        try:
            if aux_dir and os.path.isdir(aux_dir):
                # Write modified .tex into the aux directory as the main file
                tex_path = os.path.join(aux_dir, main_filename)
                with open(tex_path, "w", encoding="utf-8") as f:
                    f.write(tex_source)
                pdf_bytes = _compile_tarball(aux_dir, main_filename, compiler)
            else:
                pdf_bytes = _compile_single_file(tex_source, compiler)

            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, "wb") as f:
                f.write(pdf_bytes)
            logger.info(f"PDF compiled successfully ({len(pdf_bytes):,} bytes): {output_path}")
            return output_path

        except RuntimeError as e:
            # Compilation error (bad LaTeX) — do not retry
            logger.error(f"LaTeX compile error: {e}")
            raise

        except Exception as e:
            # Network / timeout — retry with backoff
            last_error = e
            if attempt <= retries:
                wait = 5 * attempt
                logger.warning(f"Compile attempt {attempt} failed: {e}. Retrying in {wait}s...")
                time.sleep(wait)
            else:
                raise RuntimeError(
                    f"LaTeX compile failed after {retries + 1} attempts: {last_error}"
                ) from last_error
