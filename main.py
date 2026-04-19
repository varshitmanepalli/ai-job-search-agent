"""
AI Job Search Agent — Main Orchestrator
========================================
Ties all agents together into a single pipeline run.

Usage:
    python main.py              # Run once immediately
    python main.py --scheduler  # Run on schedule (6 AM / 6 PM ET)
    python main.py --dry-run    # Parse resume + discover jobs, skip email
"""

import argparse
import concurrent.futures
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from config.settings import config
from agents.resume_agent import load_resume_profile, profile_to_markdown
from agents.job_discovery_agent import discover_jobs, JobPosting
from agents.relevance_agent import score_and_filter_jobs
from agents.resume_tailor_agent import tailor_resume
from agents.hiring_manager_agent import enrich_job_with_contact
from agents.email_reporter_agent import send_report
from utils.dedup import load_seen_ids, mark_jobs_seen, clear_history
from utils.logger import get_logger

logger = get_logger("orchestrator")


# ──────────────────────────────────────────────────────────────────────────────
# Pipeline
# ──────────────────────────────────────────────────────────────────────────────

def run_pipeline(dry_run: bool = False, since_hours: int = 0) -> dict:
    """Execute the full job search pipeline. Returns a summary dict."""
    start = time.time()
    run_time = datetime.now(timezone.utc)
    logger.info(f"\n{'='*60}")
    logger.info(f"Pipeline started at {run_time.isoformat()}")
    logger.info(f"{'='*60}")

    summary = {
        "run_at": run_time.isoformat(),
        "total_discovered": 0,
        "total_scored": 0,
        "total_sent": 0,
        "duration_seconds": 0,
        "error": None,
    }

    try:
        # ── Step 1: Resume Intelligence ──────────────────────────────────────
        logger.info("[1/6] Loading resume profile...")
        profile = load_resume_profile()
        logger.info(f"  → {profile.name} | {profile.total_years_experience} yrs | "
                    f"{len(profile.skills_technical)} technical skills")

        # ── Step 2: Job Discovery ─────────────────────────────────────────────
        logger.info("[2/6] Discovering new jobs...")
        seen_ids = load_seen_ids()
        all_jobs = discover_jobs(since_hours=since_hours)
        # Filter out seen
        new_jobs = [j for j in all_jobs if j.id not in seen_ids]
        summary["total_discovered"] = len(new_jobs)
        logger.info(f"  → {len(new_jobs)} new jobs to evaluate")

        if not new_jobs:
            logger.info("No new jobs found. Pipeline complete.")
            summary["duration_seconds"] = round(time.time() - start, 1)
            return summary

        # ── Step 3: Relevance Filtering ───────────────────────────────────────
        logger.info("[3/6] Scoring and filtering jobs...")
        scored_jobs = score_and_filter_jobs(profile, new_jobs)
        summary["total_scored"] = len(scored_jobs)
        logger.info(f"  → {len(scored_jobs)} jobs passed relevance threshold")

        if not scored_jobs:
            logger.info("No jobs met the relevance threshold. Pipeline complete.")
            summary["duration_seconds"] = round(time.time() - start, 1)
            return summary

        # ── Step 4: Tailor Resumes + Enrich Contact Info ─────────────────────
        #
        # Strategy:
        #   - LLM tailoring (.tex rewrite) and contact enrichment run in
        #     parallel across all jobs — both are I/O-bound and independent.
        #   - LaTeX compilation is serialized (one at a time) via a semaphore
        #     because TeXLive.net is a free public service that returns
        #     "Bad form type: no main document" when hit with concurrent
        #     requests from the same client.
        #
        logger.info("[4/6] Tailoring resumes and enriching contact info...")
        pdf_paths = []
        import threading
        _compile_lock = threading.Semaphore(1)  # one compile at a time

        def process_job(job: JobPosting):
            # Phase A (parallel): LLM tex rewrite + contact enrichment
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as inner:
                resume_future = inner.submit(tailor_resume, profile, job,
                                             compile_lock=_compile_lock)
                contact_future = inner.submit(enrich_job_with_contact, profile, job)
                pdf = resume_future.result()
                enriched_job = contact_future.result()
            return enriched_job, pdf

        enriched_jobs = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
            futures = {ex.submit(process_job, job): job for job in scored_jobs}
            for fut in concurrent.futures.as_completed(futures):
                try:
                    enriched_job, pdf = fut.result()
                    enriched_jobs.append(enriched_job)
                    if pdf:
                        pdf_paths.append(pdf)
                except Exception as e:
                    logger.error(f"Job processing error: {e}")

        logger.info(f"  → {len(pdf_paths)} tailored PDFs generated")
        logger.info(f"  → {sum(1 for j in enriched_jobs if j.hiring_manager_email)} jobs with contact info")

        # ── Step 5: Deduplication — Mark as Seen ─────────────────────────────
        logger.info("[5/6] Updating seen-jobs log...")
        mark_jobs_seen([j.id for j in enriched_jobs])
        summary["total_sent"] = len(enriched_jobs)

        # ── Step 6: Email Report ──────────────────────────────────────────────
        if dry_run:
            logger.info("[6/6] DRY RUN — skipping email send.")
            logger.info("Sample job:")
            if enriched_jobs:
                j = enriched_jobs[0]
                logger.info(f"  {j.title} @ {j.company} | Score: {j.relevance_score:.2f}")
        else:
            logger.info("[6/6] Sending email report...")
            run_label = "6:00 AM Run" if run_time.hour < 12 else "6:00 PM Run"
            send_report(enriched_jobs, pdf_paths, run_label)

    except Exception as e:
        logger.error(f"Pipeline error: {e}", exc_info=True)
        summary["error"] = str(e)

    summary["duration_seconds"] = round(time.time() - start, 1)
    logger.info(f"\nPipeline complete in {summary['duration_seconds']}s | "
                f"Found: {summary['total_discovered']} | "
                f"Scored: {summary['total_scored']} | "
                f"Sent: {summary['total_sent']}")
    return summary


# ──────────────────────────────────────────────────────────────────────────────
# Scheduler
# ──────────────────────────────────────────────────────────────────────────────

def run_scheduler():
    """
    Blocking scheduler that runs the pipeline at 6 AM and 6 PM Eastern Time.
    Deploy this as a long-running process (systemd, Docker, cloud run job, etc.)
    For production, prefer APScheduler or a cron job — see README.
    """
    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.cron import CronTrigger
    import pytz

    eastern = pytz.timezone("America/New_York")
    scheduler = BlockingScheduler(timezone=eastern)

    scheduler.add_job(
        run_pipeline,
        CronTrigger(hour=6, minute=0, timezone=eastern),
        id="morning_run",
        name="Morning Job Search (6 AM ET)",
        replace_existing=True,
    )
    scheduler.add_job(
        run_pipeline,
        CronTrigger(hour=18, minute=0, timezone=eastern),
        id="evening_run",
        name="Evening Job Search (6 PM ET)",
        replace_existing=True,
    )

    logger.info("Scheduler started. Runs at 6:00 AM and 6:00 PM Eastern Time.")
    logger.info("Press Ctrl+C to stop.")

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler stopped.")


# ──────────────────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AI Job Search Agent")
    parser.add_argument("--scheduler", action="store_true", help="Run on 6AM/6PM ET schedule")
    parser.add_argument("--dry-run", action="store_true", help="Run pipeline without sending email")
    parser.add_argument("--refresh-resume", action="store_true", help="Force re-parse resume PDF")
    parser.add_argument(
        "--clear-history",
        action="store_true",
        help="Wipe the seen-jobs log so all jobs appear as new on the next run. "
             "Use this to reset deduplication (e.g. after testing, or to resend a report).",
    )
    parser.add_argument(
        "--since-hours",
        type=int,
        default=0,
        metavar="N",
        help="Override recency window: only fetch jobs posted in the last N hours. "
             "Default 0 = use config value (12h). Use a larger value (e.g. 72 or 168) "
             "for a first run or backfill. Example: --since-hours 72",
    )
    args = parser.parse_args()

    # Ensure required directories exist
    for d in [config.paths.tailored_resumes_dir, config.paths.reports_dir, "output/logs", "input"]:
        Path(d).mkdir(parents=True, exist_ok=True)

    if args.refresh_resume:
        from agents.resume_agent import load_resume_profile
        load_resume_profile(force_refresh=True)
        logger.info("Resume profile refreshed.")
        sys.exit(0)

    if args.clear_history:
        clear_history()
        sys.exit(0)

    if args.scheduler:
        run_scheduler()
    else:
        summary = run_pipeline(dry_run=args.dry_run, since_hours=args.since_hours)
        sys.exit(0 if not summary.get("error") else 1)
