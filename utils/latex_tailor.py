# -*- coding: utf-8 -*-
"""
LaTeX Resume Tailor
===================
Uses an LLM to surgically edit ONLY the content nodes in a .tex resume —
experience bullets, skills list, summary — without touching ANY formatting,
preamble, custom commands, or structural LaTeX.

Strategy
--------
Instead of asking the LLM to rewrite the entire .tex file (which causes it
to accidentally alter or drop formatting macros), we:

1. Parse the .tex source into "content blocks" using regex anchors that
   identify where each section's text lives.
2. Feed ONLY the extracted plain-text content + the job description to the LLM.
3. Get back a structured JSON of replacements (old_text → new_text).
4. Apply each replacement as a precise string substitution in the original .tex.

This means the LaTeX preamble, \newcommand definitions, column layout,
font choices, spacing — everything formatting-related — is never sent to
or seen by the LLM.

Supported section patterns (auto-detected):
  - \resumeSubheading{...}{...}{...}{...} style (Jake's Resume, common template)
  - \section{Experience} ... \resumeItem{...} style
  - Plain paragraph text inside \begin{document}...\end{document}
  - Works with any template as long as the LLM can find the text content
"""

import json
import re
from typing import List, Dict, Tuple

from utils.llm_client import chat_completion
from utils.logger import get_logger

logger = get_logger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# Step 1: Extract content text from .tex source
# ──────────────────────────────────────────────────────────────────────────────

def _extract_content_blocks(tex: str) -> str:
    """
    Extract the human-readable text content from a .tex file,
    stripping LaTeX commands but preserving the text structure.
    Returns a readable plain-text representation for the LLM.
    """
    # Work only within \begin{document}...\end{document}
    doc_match = re.search(
        r"\\begin\{document\}(.*?)\\end\{document\}",
        tex, re.DOTALL
    )
    body = doc_match.group(1) if doc_match else tex

    # Remove comments
    body = re.sub(r"%.*$", "", body, flags=re.MULTILINE)

    # Extract text from common resume commands
    # \resumeItem{...} → bullet text
    # \resumeSubheading{Title}{Dates}{Company}{Location} → section header
    # \section{...} → section title
    # \textbf{...}, \textit{...}, \href{...}{...} → inline text

    readable_lines = []
    for line in body.split("\n"):
        line = line.strip()
        if not line:
            continue

        # Skip pure LaTeX structural lines
        if re.match(r"^\\(begin|end|vspace|hspace|newline|hfill|noindent|centering|"
                    r"resumeItemListStart|resumeItemListEnd|resumeSubHeadingListStart|"
                    r"resumeSubHeadingListEnd|resumeProjectHeading|small|normalsize|"
                    r"selectfont|setlength|pagestyle|thispagestyle)\b", line):
            continue

        # Keep lines that have actual words
        # Strip LaTeX commands to expose the text
        text = re.sub(r"\\[a-zA-Z]+\*?\{([^}]*)\}", r"\1", line)  # \cmd{text} → text
        text = re.sub(r"\\[a-zA-Z]+\s*", "", text)                  # remaining \cmd
        text = re.sub(r"[{}\[\]]", "", text)                        # braces
        text = text.strip()

        if len(text) > 10:  # skip very short fragments
            readable_lines.append(text)

    return "\n".join(readable_lines)


# ──────────────────────────────────────────────────────────────────────────────
# Step 2: Ask LLM for targeted content replacements
# ──────────────────────────────────────────────────────────────────────────────

TAILOR_SYSTEM = """
You are an expert ATS resume optimizer. You will receive:
1. The plain-text CONTENT of a resume (extracted from LaTeX — no formatting info).
2. A job description.

Your task: Return a JSON array of precise text replacements that make the resume
more relevant to this specific job description.

Rules (STRICT):
- Return ONLY a JSON array. No markdown fences, no commentary.
- Each item: {"old": "exact text from the resume", "new": "replacement text"}
- "old" must be a verbatim substring from the resume content (exact match).
- "new" must be the same semantic content, rephrased to align with the JD keywords.
- DO NOT invent experience, companies, dates, or skills the candidate does not have.
- DO NOT change proper nouns (company names, school names, degree titles).
- DO NOT change dates or durations.
- DO NOT alter the professional summary beyond rephrasing to match the role.
- Limit to 8–12 high-impact replacements. Prioritize bullet points and skills.
- Keep replacements roughly the same length to preserve layout.
- Make sure "old" is unique enough in the resume to match exactly one location.

Example output:
[
  {"old": "Built ML pipelines for data processing", "new": "Built end-to-end ML pipelines for large-scale LLM training data processing"},
  {"old": "Python, PyTorch, TensorFlow", "new": "Python, PyTorch, TensorFlow, JAX, CUDA"}
]
"""

def _get_replacements_from_llm(
    resume_content: str,
    job_title: str,
    job_company: str,
    job_description: str,
) -> List[Dict[str, str]]:
    prompt = f"""
## Target Job
**Role:** {job_title} at {job_company}

**Job Description:**
{job_description[:4000]}

## Resume Content (plain text, extracted from LaTeX)
{resume_content}

Return JSON array of replacements.
"""
    response = chat_completion(
        system=TAILOR_SYSTEM,
        user=prompt,
        response_format={"type": "json_object"},
        temperature=0.2,
    )

    # The model sometimes wraps the array in {"replacements": [...]}
    data = json.loads(response)
    if isinstance(data, list):
        return data
    # Unwrap any single-key envelope
    for v in data.values():
        if isinstance(v, list):
            return v
    return []


# ──────────────────────────────────────────────────────────────────────────────
# Step 3: Apply replacements to the raw .tex source
# ──────────────────────────────────────────────────────────────────────────────

def _apply_replacements(tex: str, replacements: List[Dict[str, str]]) -> Tuple[str, int]:
    """
    Apply each {old, new} replacement to the tex source.
    Returns (modified_tex, number_of_replacements_applied).
    """
    applied = 0
    for r in replacements:
        old = r.get("old", "").strip()
        new = r.get("new", "").strip()
        if not old or not new or old == new:
            continue
        if old in tex:
            tex = tex.replace(old, new, 1)
            applied += 1
            logger.debug(f"  Replaced: '{old[:60]}...' → '{new[:60]}...'")
        else:
            logger.debug(f"  Skipped (not found): '{old[:60]}'")
    return tex, applied


# ──────────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────────

def tailor_tex_for_job(
    tex_source: str,
    job_title: str,
    job_company: str,
    job_description: str,
) -> str:
    """
    Return a modified version of tex_source that is better aligned with the
    given job description, without altering any LaTeX formatting commands.

    Args:
        tex_source:      Full content of input/resume.tex
        job_title:       e.g. "Founding AI Engineer"
        job_company:     e.g. "Anthropic"
        job_description: Full JD text

    Returns:
        Modified .tex source string.
    """
    logger.info(f"Tailoring .tex for: {job_title} @ {job_company}")

    # Extract readable content
    content_text = _extract_content_blocks(tex_source)
    logger.debug(f"Extracted {len(content_text)} chars of content text")

    # Get replacement list from LLM
    replacements = _get_replacements_from_llm(
        resume_content=content_text,
        job_title=job_title,
        job_company=job_company,
        job_description=job_description,
    )
    logger.info(f"LLM returned {len(replacements)} replacement candidates")

    # Apply to raw .tex
    modified_tex, applied = _apply_replacements(tex_source, replacements)
    logger.info(f"Applied {applied}/{len(replacements)} replacements to .tex source")

    return modified_tex
