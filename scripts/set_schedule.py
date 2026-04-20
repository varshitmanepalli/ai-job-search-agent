#!/usr/bin/env python3
"""
set_schedule.py — Update GitHub Actions cron schedule from config/settings.py
==============================================================================

Usage (run from the repo root):
    python scripts/set_schedule.py

What it does:
  1. Reads RUN_TIMES_ET from config/settings.py  (e.g. ["06:00", "18:00"])
  2. Converts each time from Eastern Time to UTC, accounting for DST
     automatically — no manual timezone math needed
  3. Rewrites the cron lines in .github/workflows/job-search.yml
  4. Prints a summary of what changed

After running, commit and push the updated workflow file:
    git add .github/workflows/job-search.yml
    git commit -m "Update schedule"
    git push
"""

import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo   # stdlib since Python 3.9

# ── Paths (resolved relative to this script, so works from any cwd) ──────────
REPO_ROOT    = Path(__file__).resolve().parent.parent
SETTINGS_PY  = REPO_ROOT / "config" / "settings.py"
WORKFLOW_YML = REPO_ROOT / ".github" / "workflows" / "job-search.yml"

# ── Marker that brackets the auto-generated cron block in the workflow ────────
CRON_START = "# <<< SCHEDULE_START — do not edit this line >>>"
CRON_END   = "# <<< SCHEDULE_END   — do not edit this line >>>"


def read_run_times() -> list[str]:
    """Parse RUN_TIMES_ET = [...] from settings.py without importing the module."""
    text = SETTINGS_PY.read_text(encoding="utf-8")
    match = re.search(r'^RUN_TIMES_ET\s*=\s*(\[.*?\])', text, re.MULTILINE | re.DOTALL)
    if not match:
        sys.exit("ERROR: Could not find RUN_TIMES_ET in config/settings.py")
    raw = match.group(1)
    times = re.findall(r'"(\d{2}:\d{2})"', raw)
    if not times:
        sys.exit("ERROR: RUN_TIMES_ET is empty or has no valid \"HH:MM\" entries")
    return times


def et_to_utc_cron(hhmm: str) -> tuple[str, str]:
    """
    Convert an HH:MM Eastern Time string to a UTC cron expression.

    Uses the *current* date to determine whether ET is EST (UTC-5) or
    EDT (UTC-4). Returns (cron_string, et_label) e.g. ('0 11 * * *', '6:00 AM ET').
    """
    hour, minute = map(int, hhmm.split(":"))

    # Build a timezone-aware datetime for today at the given ET time
    et_zone  = ZoneInfo("America/New_York")
    utc_zone = timezone.utc
    now_et   = datetime.now(et_zone)
    et_dt    = now_et.replace(hour=hour, minute=minute, second=0, microsecond=0)
    utc_dt   = et_dt.astimezone(utc_zone)

    # UTC cron: minute hour * * *
    cron = f"{utc_dt.minute} {utc_dt.hour} * * *"

    # Friendly label
    suffix   = "AM" if hour < 12 else "PM"
    disp_h   = hour if hour <= 12 else hour - 12
    disp_h   = 12 if disp_h == 0 else disp_h
    disp_min = f":{minute:02d}" if minute else ":00"
    tz_abbr  = "EDT" if et_dt.dst() else "EST"
    label    = f"{disp_h}{disp_min} {suffix} {tz_abbr}"

    return cron, label


def build_cron_block(times: list[str]) -> str:
    """Build the schedule: block lines to inject into the workflow YAML."""
    lines = [CRON_START, "  schedule:"]
    for t in times:
        cron, label = et_to_utc_cron(t)
        lines.append(f"    - cron: '{cron}'   # {label}")
    lines.append(CRON_END)
    return "\n".join(lines)


def update_workflow(new_block: str):
    """Replace everything between the START/END markers in the workflow file."""
    text = WORKFLOW_YML.read_text(encoding="utf-8")

    if CRON_START not in text or CRON_END not in text:
        sys.exit(
            f"ERROR: Marker lines not found in {WORKFLOW_YML}.\n"
            f"Expected:\n  {CRON_START}\n  {CRON_END}"
        )

    pattern = re.compile(
        re.escape(CRON_START) + r".*?" + re.escape(CRON_END),
        re.DOTALL,
    )
    updated = pattern.sub(new_block, text)
    WORKFLOW_YML.write_text(updated, encoding="utf-8")


def main():
    times = read_run_times()

    print(f"\nFound {len(times)} run time(s) in config/settings.py:")
    crons = []
    for t in times:
        cron, label = et_to_utc_cron(t)
        crons.append((cron, label))
        print(f"  {t} ET  →  UTC cron: '{cron}'  ({label})")

    new_block = build_cron_block(times)
    update_workflow(new_block)

    print(f"\nWorkflow updated: {WORKFLOW_YML.relative_to(REPO_ROOT)}")
    print("\nNext steps:")
    print("  git add .github/workflows/job-search.yml")
    print("  git commit -m \"Update schedule\"")
    print("  git push")
    print()


if __name__ == "__main__":
    main()
