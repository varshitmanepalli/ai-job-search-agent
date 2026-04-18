"""
Job Discovery Agent
===================
Searches multiple sources for recently-posted AI/ML jobs and returns a
de-duplicated list of JobPosting objects.

Sources:
  1. Adzuna API          – broad job board aggregator (free tier available)
  2. The Muse API        – startup/tech company roles
  3. YC Work at a Startup – via unofficial RSS / HTML parse
  4. Greenhouse embed    – direct company career pages (configurable list)

All sources are queried in parallel via concurrent.futures.
Only jobs posted within config.search.max_hours_old are returned.

Dependencies:
    pip install requests feedparser python-dateutil
"""

import concurrent.futures
import json
import os
import re
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Optional
from urllib.parse import urlencode

import feedparser
import requests
from dateutil import parser as dateutil_parser

from config.settings import config
from utils.logger import get_logger

logger = get_logger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Data model
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class JobPosting:
    id: str                      # Stable unique identifier (source + external id)
    title: str
    company: str
    location: str
    description: str             # Full JD text
    posted_at: datetime
    source: str                  # "adzuna" | "yc" | "greenhouse" | "muse"
    apply_url: str
    company_url: str = ""
    salary_range: str = ""
    job_type: str = ""           # "full-time" | "contract" | etc.
    relevance_score: float = 0.0
    hiring_manager_name: str = ""
    hiring_manager_email: str = ""
    cold_email_draft: str = ""


# ──────────────────────────────────────────────────────────────────────────────
# Seen-jobs deduplication
# ──────────────────────────────────────────────────────────────────────────────

def _load_seen_ids() -> set:
    path = config.paths.seen_jobs_log
    if os.path.exists(path):
        with open(path) as f:
            data = json.load(f)
        return set(data.get("seen_ids", []))
    return set()


def _save_seen_ids(seen: set):
    path = config.paths.seen_jobs_log
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    # Keep a rolling 30-day window (approx 4 runs/day * 30 = 120 run entries)
    with open(path) as f if os.path.exists(path) else open(os.devnull) as f:
        try:
            existing = json.load(f)
        except Exception:
            existing = {}
    ids_list = list(seen)
    with open(path, "w") as f:
        json.dump({"seen_ids": ids_list, "updated_at": datetime.now(timezone.utc).isoformat()}, f, indent=2)


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _is_recent(posted_at: datetime) -> bool:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=config.search.max_hours_old)
    if posted_at.tzinfo is None:
        posted_at = posted_at.replace(tzinfo=timezone.utc)
    return posted_at >= cutoff


def _title_matches(title: str) -> bool:
    """Quick pre-filter before LLM scoring."""
    title_lower = title.lower()
    keywords = [
        "machine learning", "ml engineer", "ai engineer", "artificial intelligence",
        "deep learning", "nlp", "llm", "large language", "data scientist",
        "applied scientist", "research engineer", "founding engineer",
        "computer vision", "reinforcement learning", "generative ai",
    ]
    return any(k in title_lower for k in keywords)


# ──────────────────────────────────────────────────────────────────────────────
# Source 1: Adzuna
# ──────────────────────────────────────────────────────────────────────────────

def _fetch_adzuna(query: str, location: str = "us") -> List[JobPosting]:
    app_id = os.getenv("ADZUNA_APP_ID", "")
    app_key = os.getenv("ADZUNA_APP_KEY", "")
    if not app_id or not app_key:
        logger.warning("Adzuna credentials not set; skipping.")
        return []

    jobs = []
    for page in range(1, 4):   # up to 3 pages × 50 results = 150
        params = {
            "app_id": app_id,
            "app_key": app_key,
            "results_per_page": 50,
            "page": page,
            "what": query,
            "content-type": "application/json",
            "sort_by": "date",
        }
        url = f"https://api.adzuna.com/v1/api/jobs/us/search/{page}?" + urlencode(params)
        try:
            resp = requests.get(url, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            results = data.get("results", [])
            if not results:
                break
            for r in results:
                posted_str = r.get("created", "")
                try:
                    posted_at = dateutil_parser.parse(posted_str)
                except Exception:
                    continue
                if not _is_recent(posted_at):
                    continue
                title = r.get("title", "")
                if not _title_matches(title):
                    continue
                job = JobPosting(
                    id=f"adzuna_{r.get('id', '')}",
                    title=title,
                    company=r.get("company", {}).get("display_name", "Unknown"),
                    location=r.get("location", {}).get("display_name", ""),
                    description=r.get("description", ""),
                    posted_at=posted_at,
                    source="Adzuna",
                    apply_url=r.get("redirect_url", ""),
                    salary_range=f"{r.get('salary_min','')}-{r.get('salary_max','')}".strip("-"),
                )
                jobs.append(job)
        except Exception as e:
            logger.error(f"Adzuna fetch error (page {page}): {e}")
            break
    logger.info(f"Adzuna returned {len(jobs)} recent matching jobs.")
    return jobs


# ──────────────────────────────────────────────────────────────────────────────
# Source 2: YC Work at a Startup (RSS)
# ──────────────────────────────────────────────────────────────────────────────

YC_RSS_URL = "https://www.workatastartup.com/rss/jobs.xml"

def _fetch_yc() -> List[JobPosting]:
    jobs = []
    try:
        feed = feedparser.parse(YC_RSS_URL)
        for entry in feed.entries:
            title = entry.get("title", "")
            if not _title_matches(title):
                continue
            published_parsed = entry.get("published_parsed")
            if published_parsed:
                posted_at = datetime(*published_parsed[:6], tzinfo=timezone.utc)
            else:
                posted_at = datetime.now(timezone.utc)
            if not _is_recent(posted_at):
                continue
            # Parse company from title (format: "Role @ Company")
            parts = title.split(" @ ")
            role = parts[0].strip() if parts else title
            company = parts[1].strip() if len(parts) > 1 else "YC Startup"
            job = JobPosting(
                id=f"yc_{entry.get('id', entry.get('link',''))[-40:]}",
                title=role,
                company=company,
                location=entry.get("tags", [{}])[0].get("term", "Remote") if entry.get("tags") else "Remote",
                description=entry.get("summary", ""),
                posted_at=posted_at,
                source="YC Work at a Startup",
                apply_url=entry.get("link", ""),
            )
            jobs.append(job)
    except Exception as e:
        logger.error(f"YC fetch error: {e}")
    logger.info(f"YC returned {len(jobs)} recent matching jobs.")
    return jobs


# ──────────────────────────────────────────────────────────────────────────────
# Source 3: Greenhouse (company career pages)
# ──────────────────────────────────────────────────────────────────────────────

# Companies using Greenhouse — extend this list freely
GREENHOUSE_COMPANIES = [
    "openai", "anthropic", "cohere", "mistralai", "huggingface",
    "scale", "weights-biases", "modal", "together", "anyscale",
    "prefect", "langchain", "weaviate", "pinecone", "chroma",
    "nvidia", "databricks", "snowflake",
]

def _fetch_greenhouse_company(company_slug: str) -> List[JobPosting]:
    url = f"https://boards-api.greenhouse.io/v1/boards/{company_slug}/jobs?content=true"
    jobs = []
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        for j in data.get("jobs", []):
            title = j.get("title", "")
            if not _title_matches(title):
                continue
            updated_at_str = j.get("updated_at", "")
            try:
                posted_at = dateutil_parser.parse(updated_at_str)
            except Exception:
                posted_at = datetime.now(timezone.utc)
            if not _is_recent(posted_at):
                continue
            location = j.get("location", {}).get("name", "")
            description = j.get("content", "")
            # Strip HTML
            description = re.sub(r"<[^>]+>", " ", description)
            job = JobPosting(
                id=f"gh_{company_slug}_{j.get('id','')}",
                title=title,
                company=company_slug.replace("-", " ").title(),
                location=location,
                description=description[:3000],
                posted_at=posted_at,
                source="Greenhouse (Direct)",
                apply_url=j.get("absolute_url", ""),
            )
            jobs.append(job)
    except Exception as e:
        logger.debug(f"Greenhouse {company_slug}: {e}")
    return jobs


def _fetch_greenhouse_all() -> List[JobPosting]:
    all_jobs = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(_fetch_greenhouse_company, slug): slug for slug in GREENHOUSE_COMPANIES}
        for fut in concurrent.futures.as_completed(futures):
            try:
                all_jobs.extend(fut.result())
            except Exception as e:
                logger.error(f"Greenhouse thread error: {e}")
    logger.info(f"Greenhouse returned {len(all_jobs)} recent matching jobs.")
    return all_jobs


# ──────────────────────────────────────────────────────────────────────────────
# Source 4: Lever (another popular ATS)
# ──────────────────────────────────────────────────────────────────────────────

LEVER_COMPANIES = [
    "openai", "scale-ai", "perplexity-ai", "together-ai",
    "replit", "notion", "linear", "figma",
]

def _fetch_lever_company(company_slug: str) -> List[JobPosting]:
    url = f"https://api.lever.co/v0/postings/{company_slug}?mode=json"
    jobs = []
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        for j in data:
            title = j.get("text", "")
            if not _title_matches(title):
                continue
            created_at_ms = j.get("createdAt", 0)
            posted_at = datetime.fromtimestamp(created_at_ms / 1000, tz=timezone.utc)
            if not _is_recent(posted_at):
                continue
            location = j.get("categories", {}).get("location", "")
            description_parts = [
                j.get("description", ""),
                " ".join(l.get("content", "") for l in j.get("lists", [])),
                j.get("additional", ""),
            ]
            description = re.sub(r"<[^>]+>", " ", " ".join(description_parts))[:3000]
            job = JobPosting(
                id=f"lever_{company_slug}_{j.get('id','')}",
                title=title,
                company=company_slug.replace("-", " ").title(),
                location=location,
                description=description,
                posted_at=posted_at,
                source="Lever (Direct)",
                apply_url=j.get("hostedUrl", ""),
            )
            jobs.append(job)
    except Exception as e:
        logger.debug(f"Lever {company_slug}: {e}")
    return jobs


def _fetch_lever_all() -> List[JobPosting]:
    all_jobs = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(_fetch_lever_company, slug): slug for slug in LEVER_COMPANIES}
        for fut in concurrent.futures.as_completed(futures):
            try:
                all_jobs.extend(fut.result())
            except Exception as e:
                logger.error(f"Lever thread error: {e}")
    logger.info(f"Lever returned {len(all_jobs)} recent matching jobs.")
    return all_jobs


# ──────────────────────────────────────────────────────────────────────────────
# Master orchestrator
# ──────────────────────────────────────────────────────────────────────────────

def discover_jobs() -> List[JobPosting]:
    """
    Run all sources in parallel, de-duplicate, filter seen jobs, return fresh list.
    """
    seen_ids = _load_seen_ids()

    logger.info("Starting parallel job discovery across all sources...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
        futures = [
            ex.submit(_fetch_adzuna, "machine learning engineer AI"),
            ex.submit(_fetch_yc),
            ex.submit(_fetch_greenhouse_all),
            ex.submit(_fetch_lever_all),
        ]
        all_jobs: List[JobPosting] = []
        for fut in concurrent.futures.as_completed(futures):
            try:
                all_jobs.extend(fut.result())
            except Exception as e:
                logger.error(f"Discovery thread error: {e}")

    # De-duplicate by job ID
    seen_in_run = {}
    for job in all_jobs:
        if job.id not in seen_in_run:
            seen_in_run[job.id] = job
    unique_jobs = list(seen_in_run.values())

    # Filter out previously sent jobs
    new_jobs = [j for j in unique_jobs if j.id not in seen_ids]

    logger.info(f"Discovery complete: {len(all_jobs)} raw → {len(unique_jobs)} unique → {len(new_jobs)} new.")
    return new_jobs
