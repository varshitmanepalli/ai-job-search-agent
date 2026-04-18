"""
Resume Tailor Agent
===================
For each selected job, generates an ATS-optimized, tailored version of the
user's resume as a PDF. The original formatting, structure, and truthfulness
are preserved — only keyword alignment and emphasis is adjusted.

Approach:
  1. LLM generates a MODIFIED resume in structured JSON matching the original schema.
  2. ReportLab renders the JSON to a pixel-perfect PDF.

Dependencies:
    pip install openai reportlab
"""

import json
import os
import re
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    HRFlowable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from agents.job_discovery_agent import JobPosting
from agents.resume_agent import ResumeProfile
from config.settings import config
from utils.llm_client import chat_completion
from utils.logger import get_logger

logger = get_logger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# LLM tailoring
# ──────────────────────────────────────────────────────────────────────────────

TAILOR_SYSTEM = """
You are an expert ATS resume optimizer and technical writer. 

Your task: Given a candidate's resume (JSON) and a job description, return a MODIFIED version
of the resume JSON that is better aligned with the job — while being 100% truthful.

Rules (strict):
1. DO NOT invent experience, projects, skills, or companies the candidate never had.
2. DO NOT change employment dates, job titles held, or company names.
3. You MAY reorder bullet points within a role to lead with most relevant ones.
4. You MAY rephrase existing bullets to use keywords from the JD (same meaning, better phrasing).
5. You MAY add skills the candidate genuinely has but didn't list (infer from their experience context).
6. You MAY reorder skills lists to put JD-relevant ones first.
7. You MAY adjust the professional summary to align with the specific role.
8. Keep the total length approximately the same as the original.

Return ONLY valid JSON (no markdown fences) following the exact same schema as the input.
"""

def _tailor_resume_with_llm(profile: ResumeProfile, job: JobPosting) -> dict:
    profile_json = json.dumps(asdict(profile), indent=2, default=str)
    prompt = f"""
## Job Description
**Title:** {job.title} at {job.company}
**Location:** {job.location}

{job.description[:5000]}

## Candidate's Current Resume (JSON)
{profile_json}

Tailor the resume JSON for this specific role. Return ONLY the modified JSON.
"""
    response = chat_completion(
        system=TAILOR_SYSTEM,
        user=prompt,
        response_format={"type": "json_object"},
        temperature=0.3,
        max_tokens=4096,
    )
    return json.loads(response)


# ──────────────────────────────────────────────────────────────────────────────
# PDF generation with ReportLab
# ──────────────────────────────────────────────────────────────────────────────

# Color scheme (professional, ATS-safe)
DARK_BLUE = colors.HexColor("#1a3a5c")
MEDIUM_GRAY = colors.HexColor("#555555")
LIGHT_GRAY = colors.HexColor("#888888")
BLACK = colors.black
WHITE = colors.white
RULE_COLOR = colors.HexColor("#c8d0d8")


def _build_styles() -> dict:
    base = getSampleStyleSheet()
    return {
        "name": ParagraphStyle(
            "Name",
            fontSize=20,
            fontName="Helvetica-Bold",
            textColor=DARK_BLUE,
            spaceAfter=2,
            alignment=TA_CENTER,
        ),
        "contact": ParagraphStyle(
            "Contact",
            fontSize=9,
            fontName="Helvetica",
            textColor=MEDIUM_GRAY,
            spaceAfter=6,
            alignment=TA_CENTER,
        ),
        "section_header": ParagraphStyle(
            "SectionHeader",
            fontSize=11,
            fontName="Helvetica-Bold",
            textColor=DARK_BLUE,
            spaceBefore=10,
            spaceAfter=2,
        ),
        "job_title": ParagraphStyle(
            "JobTitle",
            fontSize=10,
            fontName="Helvetica-Bold",
            textColor=BLACK,
            spaceAfter=1,
        ),
        "company_date": ParagraphStyle(
            "CompanyDate",
            fontSize=9,
            fontName="Helvetica-Oblique",
            textColor=MEDIUM_GRAY,
            spaceAfter=2,
        ),
        "bullet": ParagraphStyle(
            "Bullet",
            fontSize=9,
            fontName="Helvetica",
            textColor=BLACK,
            leftIndent=12,
            spaceAfter=2,
            bulletIndent=4,
        ),
        "body": ParagraphStyle(
            "Body",
            fontSize=9,
            fontName="Helvetica",
            textColor=BLACK,
            spaceAfter=4,
        ),
        "skills_label": ParagraphStyle(
            "SkillsLabel",
            fontSize=9,
            fontName="Helvetica-Bold",
            textColor=BLACK,
        ),
    }


def _safe(text) -> str:
    if not text:
        return ""
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _render_resume_pdf(data: dict, output_path: str):
    """Render a resume dict to a PDF using ReportLab."""
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    styles = _build_styles()

    doc = SimpleDocTemplate(
        output_path,
        pagesize=LETTER,
        leftMargin=0.65 * inch,
        rightMargin=0.65 * inch,
        topMargin=0.5 * inch,
        bottomMargin=0.5 * inch,
    )
    story = []

    # ── Header ──
    story.append(Paragraph(_safe(data.get("name", "")), styles["name"]))
    contact_parts = filter(None, [
        data.get("email"), data.get("phone"), data.get("location")
    ])
    story.append(Paragraph(" · ".join(_safe(p) for p in contact_parts), styles["contact"]))
    story.append(HRFlowable(width="100%", thickness=1, color=RULE_COLOR, spaceAfter=4))

    # ── Summary ──
    if data.get("summary"):
        story.append(Paragraph("PROFESSIONAL SUMMARY", styles["section_header"]))
        story.append(HRFlowable(width="100%", thickness=0.5, color=RULE_COLOR, spaceAfter=4))
        story.append(Paragraph(_safe(data["summary"]), styles["body"]))

    # ── Skills ──
    skill_groups = [
        ("Languages & Frameworks", data.get("skills_technical", [])),
        ("Tools & Infrastructure", data.get("skills_tools", [])),
        ("AI/ML Domains", data.get("skills_domains", [])),
    ]
    any_skills = any(v for _, v in skill_groups)
    if any_skills:
        story.append(Paragraph("TECHNICAL SKILLS", styles["section_header"]))
        story.append(HRFlowable(width="100%", thickness=0.5, color=RULE_COLOR, spaceAfter=4))
        for label, items in skill_groups:
            if items:
                line = f"<b>{_safe(label)}:</b> {_safe(', '.join(items))}"
                story.append(Paragraph(line, styles["body"]))

    # ── Experience ──
    experience = data.get("experience", [])
    if experience:
        story.append(Paragraph("EXPERIENCE", styles["section_header"]))
        story.append(HRFlowable(width="100%", thickness=0.5, color=RULE_COLOR, spaceAfter=4))
        for exp in experience:
            story.append(Paragraph(_safe(exp.get("title", "")), styles["job_title"]))
            company_line = f"{_safe(exp.get('company',''))}  •  {_safe(exp.get('start_date',''))} – {_safe(exp.get('end_date',''))}"
            story.append(Paragraph(company_line, styles["company_date"]))
            for bullet in exp.get("impact_bullets", []) + exp.get("responsibilities", []):
                story.append(Paragraph(f"• {_safe(bullet)}", styles["bullet"]))
            if exp.get("tech_used"):
                story.append(Paragraph(f"<i>Stack: {_safe(', '.join(exp['tech_used']))}</i>", styles["body"]))
            story.append(Spacer(1, 4))

    # ── Projects ──
    projects = data.get("projects", [])
    if projects:
        story.append(Paragraph("PROJECTS", styles["section_header"]))
        story.append(HRFlowable(width="100%", thickness=0.5, color=RULE_COLOR, spaceAfter=4))
        for proj in projects:
            story.append(Paragraph(_safe(proj.get("name", "")), styles["job_title"]))
            story.append(Paragraph(_safe(proj.get("description", "")), styles["body"]))
            if proj.get("impact"):
                story.append(Paragraph(f"<b>Impact:</b> {_safe(proj['impact'])}", styles["body"]))
            if proj.get("tech_used"):
                story.append(Paragraph(f"<i>Tech: {_safe(', '.join(proj['tech_used']))}</i>", styles["body"]))
            story.append(Spacer(1, 4))

    # ── Education ──
    education = data.get("education", [])
    if education:
        story.append(Paragraph("EDUCATION", styles["section_header"]))
        story.append(HRFlowable(width="100%", thickness=0.5, color=RULE_COLOR, spaceAfter=4))
        for edu in education:
            line = f"<b>{_safe(edu.get('degree',''))} in {_safe(edu.get('field',''))}</b> — {_safe(edu.get('institution',''))} ({_safe(edu.get('graduation_year',''))})"
            if edu.get("gpa"):
                line += f"  GPA: {_safe(edu['gpa'])}"
            story.append(Paragraph(line, styles["body"]))

    doc.build(story)
    logger.info(f"PDF written: {output_path}")


# ──────────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────────

def tailor_resume(profile: ResumeProfile, job: JobPosting) -> Optional[str]:
    """
    Generate a tailored PDF resume for the given job.
    Returns the absolute path to the PDF, or None on failure.
    """
    safe_company = re.sub(r"[^\w\-]", "_", job.company)[:30]
    safe_title = re.sub(r"[^\w\-]", "_", job.title)[:30]
    filename = f"resume_{safe_company}_{safe_title}.pdf"
    output_path = os.path.join(config.paths.tailored_resumes_dir, filename)

    # Skip if already generated this run
    if os.path.exists(output_path):
        logger.info(f"Tailored resume already exists: {filename}")
        return output_path

    try:
        logger.info(f"Tailoring resume for: {job.title} @ {job.company}")
        tailored_data = _tailor_resume_with_llm(profile, job)
        _render_resume_pdf(tailored_data, output_path)
        return output_path
    except Exception as e:
        logger.error(f"Resume tailoring failed for {job.id}: {e}")
        # Fall back to rendering the original resume
        try:
            from dataclasses import asdict
            _render_resume_pdf(asdict(profile), output_path)
            logger.info(f"Fell back to original resume for {job.id}")
            return output_path
        except Exception as e2:
            logger.error(f"Fallback rendering also failed: {e2}")
            return None
