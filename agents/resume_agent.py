"""
Resume Agent
============
Parses the user's PDF resume and builds a structured ResumeProfile.
The profile is cached to disk and reused across runs unless the PDF changes.

Dependencies:
    pip install pdfplumber openai
"""

import hashlib
import json
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import List, Optional

import re

import pdfplumber

from config.settings import config
from utils.llm_client import chat_completion
from utils.logger import get_logger

logger = get_logger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# Data model
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class WorkExperience:
    company: str
    title: str
    start_date: str
    end_date: str          # "Present" if current
    duration_months: int
    responsibilities: List[str]
    impact_bullets: List[str]   # quantified achievements
    tech_used: List[str]


@dataclass
class Project:
    name: str
    description: str
    tech_used: List[str]
    impact: str


@dataclass
class ResumeProfile:
    name: str
    email: str
    phone: str
    location: str
    summary: str
    total_years_experience: float
    skills_technical: List[str]       # Languages, frameworks, libraries
    skills_tools: List[str]           # Cloud, MLOps, DevOps, databases
    skills_domains: List[str]         # NLP, CV, RL, etc.
    experience: List[WorkExperience]
    projects: List[Project]
    education: List[dict]
    raw_text: str = ""                # Full plain-text for semantic search
    source_hash: str = ""             # MD5 of the PDF for cache invalidation


# ──────────────────────────────────────────────────────────────────────────────
# PDF extraction
# ──────────────────────────────────────────────────────────────────────────────

def extract_text_from_pdf(pdf_path: str) -> str:
    """Extract all text from a PDF using pdfplumber (layout-aware)."""
    text_parts = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text(x_tolerance=2, y_tolerance=2)
            if page_text:
                text_parts.append(page_text)
    return "\n".join(text_parts)


def _pdf_hash(pdf_path: str) -> str:
    with open(pdf_path, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()


def extract_text_from_tex(tex_path: str) -> str:
    """
    Extract human-readable plain text from a .tex resume source.

    Strips LaTeX commands while preserving all textual content: bullet points,
    section headers, dates, company names, tech stacks, etc.  This is used
    alongside PDF extraction so the LLM profile builder sees 100% of the
    resume content even when pdfplumber mis-orders multi-column layouts.
    """
    with open(tex_path, "r", encoding="utf-8") as f:
        src = f.read()

    # Work only inside \begin{document}...\end{document}
    doc_match = re.search(r"\\begin\{document\}(.*?)\\end\{document\}", src, re.DOTALL)
    body = doc_match.group(1) if doc_match else src

    # Strip comments
    body = re.sub(r"%.*$", "", body, flags=re.MULTILINE)

    # Expand common inline wrappers first (order matters)
    body = re.sub(r"\\hrefWithoutArrow\{[^}]*\}\{([^}]*)\}", r"\1", body)
    body = re.sub(r"\\href\{[^}]*\}\{([^}]*)\}", r"\1", body)
    body = re.sub(r"\\textbf\{([^}]*)\}",  r"\1", body)
    body = re.sub(r"\\textit\{([^}]*)\}",  r"\1", body)
    body = re.sub(r"\\emph\{([^}]*)\}",    r"\1", body)
    body = re.sub(r"\\underline\{([^}]*)\}", r"\1", body)

    # Section headers → readable labels
    body = re.sub(r"\\section\{([^}]+)\}", r"\n\n== \1 ==", body)

    # \begin{twocolentry}{DATE} → expose the date on its own line
    body = re.sub(r"\\begin\{twocolentry\}\{([^}]*)\}", r"\1", body)

    # Remove all remaining \begin{...} / \end{...} environment markers
    body = re.sub(r"\\(?:begin|end)\{[^}]*\}", "", body)

    # Remove remaining LaTeX commands (keep their arguments when single-braced)
    body = re.sub(r"\\[a-zA-Z@]+\*?\{([^}]*)\}", r"\1", body)
    body = re.sub(r"\\[a-zA-Z@]+\*?", " ", body)

    # \item → bullet dash
    body = body.replace(r"\item", "- ")

    # Clean up LaTeX special chars
    body = body.replace(r"~", " ")
    body = body.replace(r"\$", "$")
    body = body.replace(r"\&", "&")
    body = body.replace(r"\%", "%")
    body = body.replace(r"\_", "_")
    body = body.replace(r"\#", "#")

    # Remove leftover braces and backslashes
    body = re.sub(r"[{}]", "", body)
    body = re.sub(r"\\+", " ", body)

    # Collapse whitespace while preserving paragraph breaks
    body = re.sub(r"[ \t]+", " ", body)
    body = re.sub(r"\n{3,}", "\n\n", body)

    return body.strip()


# ──────────────────────────────────────────────────────────────────────────────
# LLM-based structured extraction
# ──────────────────────────────────────────────────────────────────────────────

EXTRACTION_SYSTEM_PROMPT = """
You are an expert resume parser. Given the raw text of a resume, extract ALL information
into a structured JSON object. Follow this exact schema:

{
  "name": "string",
  "email": "string",
  "phone": "string",
  "location": "string",
  "summary": "2–3 sentence professional summary, inferred if not present",
  "total_years_experience": float,
  "skills_technical": ["Python", "PyTorch", ...],
  "skills_tools": ["AWS", "Docker", "MLflow", ...],
  "skills_domains": ["NLP", "Computer Vision", "Reinforcement Learning", ...],
  "experience": [
    {
      "company": "string",
      "title": "string",
      "start_date": "MMM YYYY",
      "end_date": "MMM YYYY or Present",
      "duration_months": int,
      "responsibilities": ["string", ...],
      "impact_bullets": ["Reduced latency by 40%", ...],
      "tech_used": ["string", ...]
    }
  ],
  "projects": [
    {
      "name": "string",
      "description": "string",
      "tech_used": ["string", ...],
      "impact": "string"
    }
  ],
  "education": [
    {
      "institution": "string",
      "degree": "string",
      "field": "string",
      "graduation_year": "string",
      "gpa": "string or null"
    }
  ]
}

Rules:
- Be exhaustive — extract every skill mentioned anywhere in the resume.
- For impact_bullets, only include bullets with quantified results.
- Infer duration_months from dates.
- Return ONLY valid JSON — no markdown fences, no commentary.
"""


def _parse_profile_with_llm(raw_text: str) -> dict:
    response = chat_completion(
        system=EXTRACTION_SYSTEM_PROMPT,
        user=f"Parse this resume:\n\n{raw_text}",
        response_format={"type": "json_object"},
    )
    return json.loads(response)


# ──────────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────────

def load_resume_profile(force_refresh: bool = False) -> ResumeProfile:
    """
    Load the resume profile from cache if neither the PDF nor the .tex source
    has changed since last parse, otherwise re-parse and rebuild the profile.

    Hybrid extraction strategy
    --------------------------
    When input/resume.tex is present alongside the PDF, both sources are
    combined before the LLM parse:

      • PDF text (pdfplumber)  — accurate contact info and quantified bullets,
        but may mis-order text in multi-column RenderCV layouts.
      • LaTeX source text      — perfectly ordered, captures every bullet and
        skill even if pdfplumber drops them from two-column sections.

    Merging both sources gives the LLM the richest possible input so no
    experience, project, or skill detail is missed during extraction.
    """
    pdf_path = config.paths.resume_pdf
    tex_path = getattr(config.paths, "resume_tex", "input/resume.tex")
    cache_path = config.paths.resume_profile_cache

    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"Resume PDF not found at: {pdf_path}")

    # Build a composite hash so the cache is invalidated when either file changes
    pdf_hash = _pdf_hash(pdf_path)
    tex_hash = ""
    if os.path.exists(tex_path):
        with open(tex_path, "rb") as f:
            tex_hash = hashlib.md5(f.read()).hexdigest()
    current_hash = hashlib.md5((pdf_hash + tex_hash).encode()).hexdigest()

    # Check cache
    if not force_refresh and os.path.exists(cache_path):
        with open(cache_path) as f:
            cached = json.load(f)
        if cached.get("source_hash") == current_hash:
            logger.info("Resume profile loaded from cache.")
            return _dict_to_profile(cached)

    # ── PDF extraction ────────────────────────────────────────────────────────
    logger.info("Parsing resume PDF...")
    pdf_text = extract_text_from_pdf(pdf_path)
    logger.info(f"PDF extraction: {len(pdf_text):,} characters")

    # ── LaTeX extraction (hybrid) ─────────────────────────────────────────────
    tex_text = ""
    if os.path.exists(tex_path):
        logger.info("LaTeX source found — combining with PDF for hybrid extraction")
        try:
            tex_text = extract_text_from_tex(tex_path)
            logger.info(f"LaTeX extraction: {len(tex_text):,} characters")
        except Exception as e:
            logger.warning(f"LaTeX text extraction failed (using PDF only): {e}")

    # ── Merge ─────────────────────────────────────────────────────────────────
    if tex_text:
        raw_text = (
            "=== SOURCE: PDF (layout-aware extraction) ===\n"
            + pdf_text
            + "\n\n=== SOURCE: LaTeX source (complete, ordered) ===\n"
            + tex_text
        )
        logger.info(
            f"Hybrid extraction total: {len(raw_text):,} characters "
            f"(PDF: {len(pdf_text):,} + LaTeX: {len(tex_text):,})"
        )
    else:
        raw_text = pdf_text

    data = _parse_profile_with_llm(raw_text)
    data["raw_text"] = raw_text
    data["source_hash"] = current_hash

    # Persist cache
    Path(cache_path).parent.mkdir(parents=True, exist_ok=True)
    with open(cache_path, "w") as f:
        json.dump(data, f, indent=2)

    logger.info("Resume profile parsed and cached.")
    return _dict_to_profile(data)


def _dict_to_profile(d: dict) -> ResumeProfile:
    experience = [WorkExperience(**e) for e in d.get("experience", [])]
    projects = [Project(**p) for p in d.get("projects", [])]
    return ResumeProfile(
        name=d.get("name", ""),
        email=d.get("email", ""),
        phone=d.get("phone", ""),
        location=d.get("location", ""),
        summary=d.get("summary", ""),
        total_years_experience=d.get("total_years_experience", 0),
        skills_technical=d.get("skills_technical", []),
        skills_tools=d.get("skills_tools", []),
        skills_domains=d.get("skills_domains", []),
        experience=experience,
        projects=projects,
        education=d.get("education", []),
        raw_text=d.get("raw_text", ""),
        source_hash=d.get("source_hash", ""),
    )


def profile_to_markdown(profile: ResumeProfile) -> str:
    """Serialize the profile to a compact Markdown string for LLM prompts."""
    lines = [
        f"# {profile.name}",
        f"**Location:** {profile.location} | **Experience:** {profile.total_years_experience} years",
        "",
        "## Technical Skills",
        f"**Languages/Frameworks:** {', '.join(profile.skills_technical)}",
        f"**Tools/Infra:** {', '.join(profile.skills_tools)}",
        f"**Domains:** {', '.join(profile.skills_domains)}",
        "",
        "## Work Experience",
    ]
    for exp in profile.experience:
        lines.append(f"### {exp.title} @ {exp.company} ({exp.start_date} – {exp.end_date})")
        for bullet in exp.impact_bullets:
            lines.append(f"  - {bullet}")
        lines.append(f"  *Tech:* {', '.join(exp.tech_used)}")
    lines.append("\n## Projects")
    for proj in profile.projects:
        lines.append(f"### {proj.name}")
        lines.append(f"  {proj.description}")
        lines.append(f"  *Impact:* {proj.impact}")
        lines.append(f"  *Tech:* {', '.join(proj.tech_used)}")
    return "\n".join(lines)
