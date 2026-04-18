"""
Relevance Agent
===============
Scores each job posting against the user's resume profile using an LLM.
Returns a 0.0–1.0 relevance score plus reasoning.

The scoring is done in parallel batches to keep latency low.

Dependencies:
    pip install openai
"""

import concurrent.futures
import json
from typing import List, Tuple

from agents.job_discovery_agent import JobPosting
from agents.resume_agent import ResumeProfile, profile_to_markdown
from config.settings import config
from utils.llm_client import chat_completion
from utils.logger import get_logger

logger = get_logger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Prompt
# ──────────────────────────────────────────────────────────────────────────────

SCORING_SYSTEM = """
You are a highly experienced technical recruiter and AI/ML hiring manager.
Your task: score how well a candidate's profile matches a job posting.

Return ONLY a JSON object with this exact schema:
{
  "score": float,           // 0.0 (no match) to 1.0 (perfect match)
  "match_tier": string,     // "Strong" | "Good" | "Weak" | "Poor"
  "top_matching_skills": [string],
  "missing_skills": [string],
  "reasoning": string       // 2–3 sentences
}

Scoring rubric:
- 0.85–1.0  Strong: Candidate is among the top 10% applicants. Ticks nearly every requirement.
- 0.65–0.84 Good:   Solid match with minor gaps; worth applying.
- 0.40–0.64 Weak:   Significant gaps but some overlap.
- 0.00–0.39 Poor:   Fundamentally misaligned.

Be rigorous. A score of 0.9 means the resume is a near-perfect fit for the exact role.
"""

def _build_scoring_prompt(profile_md: str, job: JobPosting) -> str:
    return f"""
## Candidate Profile
{profile_md}

## Job Posting
**Title:** {job.title}
**Company:** {job.company}
**Location:** {job.location}

**Description:**
{job.description[:4000]}
"""


# ──────────────────────────────────────────────────────────────────────────────
# Individual scoring
# ──────────────────────────────────────────────────────────────────────────────

def _score_job(profile_md: str, job: JobPosting) -> Tuple[JobPosting, dict]:
    prompt = _build_scoring_prompt(profile_md, job)
    try:
        response = chat_completion(
            system=SCORING_SYSTEM,
            user=prompt,
            response_format={"type": "json_object"},
            temperature=0.1,
        )
        result = json.loads(response)
        job.relevance_score = float(result.get("score", 0.0))
        return job, result
    except Exception as e:
        logger.error(f"Scoring failed for {job.id}: {e}")
        job.relevance_score = 0.0
        return job, {}


# ──────────────────────────────────────────────────────────────────────────────
# Batch scoring (parallel)
# ──────────────────────────────────────────────────────────────────────────────

def score_and_filter_jobs(
    profile: ResumeProfile,
    jobs: List[JobPosting],
) -> List[JobPosting]:
    """
    Score all jobs against the profile, filter below threshold, sort descending.
    Returns at most config.search.max_jobs_per_run results.
    """
    if not jobs:
        return []

    profile_md = profile_to_markdown(profile)
    threshold = config.search.min_relevance_score
    max_jobs = config.search.max_jobs_per_run

    logger.info(f"Scoring {len(jobs)} jobs (threshold={threshold})...")

    scored: List[JobPosting] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
        futures = {ex.submit(_score_job, profile_md, job): job for job in jobs}
        for fut in concurrent.futures.as_completed(futures):
            try:
                job, meta = fut.result()
                if job.relevance_score >= threshold:
                    scored.append(job)
                    logger.debug(f"  [{job.relevance_score:.2f}] {job.title} @ {job.company}")
            except Exception as e:
                logger.error(f"Scoring thread error: {e}")

    # Sort by score descending
    scored.sort(key=lambda j: j.relevance_score, reverse=True)
    final = scored[:max_jobs]
    logger.info(f"Relevance filtering: {len(jobs)} → {len(scored)} above threshold → top {len(final)} selected.")
    return final
