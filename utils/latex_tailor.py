# -*- coding: utf-8 -*-
r"""
LaTeX Resume Tailor
===================
Uses an LLM to surgically edit ONLY the content nodes in a .tex resume --
experience bullets, skills list, summary -- without touching ANY formatting,
preamble, custom commands, or structural LaTeX.

Strategy
--------
Instead of asking the LLM to rewrite the entire .tex file (which causes it
to accidentally alter or drop formatting macros), we:

1. Parse the .tex source into "content blocks" using regex anchors that
   identify where each section's text lives.
2. Feed ONLY the extracted plain-text content + the job description to the LLM.
3. Get back a structured JSON of replacements (old_text => new_text).
4. Apply each replacement as a precise string substitution in the original .tex.

This means the LaTeX preamble, \newcommand definitions, column layout,
font choices, spacing -- everything formatting-related -- is never sent to
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

    Handles RenderCV template patterns (twocolentry, onecolentry, highlights)
    as well as Jake's Resume style and plain LaTeX resumes.
    """
    # Work only within \begin{document}...\end{document}
    doc_match = re.search(
        r"\\begin\{document\}(.*?)\\end\{document\}",
        tex, re.DOTALL
    )
    body = doc_match.group(1) if doc_match else tex

    # Remove LaTeX comments
    body = re.sub(r"%.*$", "", body, flags=re.MULTILINE)

    readable_lines = []

    # ── RenderCV: \section{Title} ─────────────────────────────────────
    for m in re.finditer(r"\\section\{([^}]+)\}", body):
        readable_lines.append(f"\n### {m.group(1)}")

    # ── RenderCV: \begin{twocolentry}{DATE}\n  \textbf{TITLE}, COMPANY
    # captures "Title, Company" from the line after twocolentry
    for m in re.finditer(
        r"\\begin\{twocolentry\}\{([^}]*)\}\s*\n\s*(.*?)\\end\{twocolentry\}",
        body, re.DOTALL
    ):
        date = m.group(1).strip()
        header = m.group(2).strip()
        header_clean = re.sub(r"\\[a-zA-Z]+\*?\{([^}]*)\}", r"\1", header)
        header_clean = re.sub(r"\\[a-zA-Z@]+", "", header_clean).strip(" \\{}%")
        if header_clean:
            readable_lines.append(f"{header_clean}  [{date}]")

    # ── \item lines (bullet points) ───────────────────────────────────
    for m in re.finditer(r"\\item\s+(.+?)(?=\\item|\\end\{|$)", body, re.DOTALL):
        bullet = m.group(1).strip()
        # Expand \textbf{x} → x, \textit{x} → x, \hrefWithoutArrow{url}{text} → text
        bullet = re.sub(r"\\hrefWithoutArrow\{[^}]*\}\{([^}]*)\}", r"\1", bullet)
        bullet = re.sub(r"\\href\{[^}]*\}\{([^}]*)\}", r"\1", bullet)
        bullet = re.sub(r"\\textbf\{([^}]*)\}", r"\1", bullet)
        bullet = re.sub(r"\\textit\{([^}]*)\}", r"\1", bullet)
        bullet = re.sub(r"\\[a-zA-Z]+\*?\{([^}]*)\}", r"\1", bullet)
        bullet = re.sub(r"\\[a-zA-Z@]+\s*", " ", bullet)
        bullet = re.sub(r"\s+", " ", bullet).strip(" {}[]\\%")
        bullet = bullet.replace("~", " ").strip()
        if len(bullet) > 15:
            readable_lines.append(f"- {bullet}")

    # ── \begin{onecolentry} plain skill lines ─────────────────────────
    for m in re.finditer(
        r"\\begin\{onecolentry\}(.*?)\\end\{onecolentry\}",
        body, re.DOTALL
    ):
        content = m.group(1).strip()
        # Skip if already captured as bullet
        if r"\item" in content:
            continue
        content = re.sub(r"\\textbf\{([^}]*)\}", r"\1", content)
        content = re.sub(r"\\textit\{([^}]*)\}", r"\1", content)
        content = re.sub(r"\\[a-zA-Z]+\*?\{([^}]*)\}", r"\1", content)
        content = re.sub(r"\\[a-zA-Z@]+\s*", " ", content)
        content = re.sub(r"\s+", " ", content).strip(" {}[]\\%")
        if len(content) > 10:
            readable_lines.append(content)

    # ── Professional summary (plain paragraphs after \section{...Summary...})
    summary_match = re.search(
        r"\\section\{[^}]*[Ss]ummary[^}]*\}(.*?)(?=\\section\{|\\end\{document\})",
        body, re.DOTALL
    )
    if summary_match:
        para = summary_match.group(1).strip()
        para = re.sub(r"\\textbf\{([^}]*)\}", r"\1", para)
        para = re.sub(r"\\[a-zA-Z]+\*?\{([^}]*)\}", r"\1", para)
        para = re.sub(r"\\[a-zA-Z@]+\s*", " ", para)
        para = re.sub(r"\s+", " ", para).strip(" {}[]\\%")
        if len(para) > 20:
            readable_lines.insert(0, f"### Professional Summary\n{para}\n")

    # Final cleanup of LaTeX escape artifacts in the extracted text
    output = "\n".join(readable_lines)
    output = output.replace(r"\$", "$")
    output = output.replace(r"\&", "&")
    output = output.replace(r"\%", "%")
    output = output.replace(r"\_", "_")
    output = output.replace(r"\#", "#")
    output = output.replace(r"~", " ")
    # Remove stray single backslashes and leftover braces
    import re as _re
    output = _re.sub(r"\\\s", " ", output)
    output = _re.sub(r"(?<!\{)\{(?!\d)", "", output)   # lone { not part of \cmd{
    output = _re.sub(r"\s{2,}", " ", output)
    return output


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
    # Do NOT pass response_format={"type": "json_object"} here — OpenAI's
    # json_object mode requires the root to be an object, but we want a JSON
    # array. Forcing json_object causes the model to wrap the array in an
    # envelope object every time, making unwrapping fragile and error-prone.
    # Instead we strip markdown fences and parse the array directly.
    response = chat_completion(
        system=TAILOR_SYSTEM,
        user=prompt,
        temperature=0.2,
    )

    # Strip optional markdown code fences (```json ... ```)
    text = response.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    text = text.strip()

    data = json.loads(text)
    if isinstance(data, list):
        return data
    # Unwrap any single-key envelope (defensive, shouldn't be needed now)
    for v in data.values():
        if isinstance(v, list):
            return v
    return []


# ──────────────────────────────────────────────────────────────────────────────
# Step 3: Apply replacements to the raw .tex source
# ──────────────────────────────────────────────────────────────────────────────

def _apply_replacements(tex: str, replacements: List[Dict[str, str]]) -> Tuple[str, int, List[str]]:
    """
    Apply each {old, new} replacement to the tex source.
    Returns (modified_tex, number_of_replacements_applied, human_readable_changes).
    """
    applied = 0
    changes: List[str] = []
    for r in replacements:
        old = r.get("old", "").strip()
        new = r.get("new", "").strip()
        if not old or not new or old == new:
            continue
        if old in tex:
            tex = tex.replace(old, new, 1)
            applied += 1
            # Build a short human-readable summary of this change
            old_preview = old[:60] + ("..." if len(old) > 60 else "")
            new_preview = new[:60] + ("..." if len(new) > 60 else "")
            changes.append(f"{old_preview} → {new_preview}")
            logger.debug(f"  Replaced: '{old_preview}' → '{new_preview}'")
        else:
            logger.debug(f"  Skipped (not found): '{old[:60]}'")
    return tex, applied, changes


# ──────────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────────

def tailor_tex_for_job(
    tex_source: str,
    job_title: str,
    job_company: str,
    job_description: str,
) -> Tuple[str, List[str]]:
    """
    Return a modified version of tex_source that is better aligned with the
    given job description, without altering any LaTeX formatting commands.

    Args:
        tex_source:      Full content of input/resume.tex
        job_title:       e.g. "Founding AI Engineer"
        job_company:     e.g. "Anthropic"
        job_description: Full JD text

    Returns:
        Tuple of (modified_tex, changes) where changes is a list of
        human-readable bullet strings describing what was edited.
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
    modified_tex, applied, changes = _apply_replacements(tex_source, replacements)
    logger.info(f"Applied {applied}/{len(replacements)} replacements to .tex source")

    return modified_tex, changes
