"""
Deduplication utilities.
Maintains a rolling log of previously sent job IDs so jobs are never re-sent.
Keeps a 30-day rolling window (~240 entries max at 8 runs/day).
"""

import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List

from config.settings import config
from utils.logger import get_logger

logger = get_logger(__name__)


def load_seen_ids() -> set:
    path = config.paths.seen_jobs_log
    if not os.path.exists(path):
        return set()
    with open(path) as f:
        data = json.load(f)
    # Purge entries older than 30 days
    entries = data.get("entries", [])
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    fresh = [e for e in entries if e.get("sent_at", "") >= cutoff]
    return {e["id"] for e in fresh}


def mark_jobs_seen(job_ids: List[str]):
    path = config.paths.seen_jobs_log
    Path(path).parent.mkdir(parents=True, exist_ok=True)

    existing_entries = []
    if os.path.exists(path):
        with open(path) as f:
            data = json.load(f)
        existing_entries = data.get("entries", [])

    # Purge >30 days
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    fresh = [e for e in existing_entries if e.get("sent_at", "") >= cutoff]

    now = datetime.now(timezone.utc).isoformat()
    for jid in job_ids:
        fresh.append({"id": jid, "sent_at": now})

    with open(path, "w") as f:
        json.dump({"entries": fresh, "updated_at": now}, f, indent=2)
    logger.info(f"Marked {len(job_ids)} job IDs as seen. Total tracked: {len(fresh)}")
