# AI Job Search Agent

An automated AI agent that analyzes your resume, discovers highly relevant AI/ML job postings every 12 hours, customizes your resume for each role, and emails you a consolidated HTML report with PDF attachments — twice daily.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Scheduler (APScheduler)                       │
│                    6:00 AM ET  ·  6:00 PM ET                         │
└─────────────────────────────┬───────────────────────────────────────┘
                              │
                    ┌─────────▼──────────┐
                    │  Orchestrator       │  main.py
                    │  (Pipeline runner)  │
                    └─────────┬──────────┘
                              │
        ┌─────────────────────┼─────────────────────┐
        ▼                     ▼                     ▼
┌──────────────┐   ┌───────────────────┐   ┌────────────────────┐
│ Resume Agent │   │ Job Discovery      │   │ Dedup / Seen-Jobs  │
│              │   │ Agent              │   │ Log                │
│ • PDF parse  │   │                    │   │                    │
│ • LLM extract│   │ • Adzuna API       │   │ Rolling 30-day     │
│ • Profile    │   │ • YC RSS           │   │ JSON log           │
│   cache      │   │ • Greenhouse API   │   └────────────────────┘
└──────┬───────┘   │ • Lever API        │
       │           └────────┬──────────┘
       │                    │
       └──────────┬─────────┘
                  ▼
       ┌──────────────────────┐
       │ Relevance Agent      │
       │                      │
       │ • GPT-4o scoring     │
       │ • 0.0–1.0 score      │
       │ • Parallel batches   │
       │ • Threshold filter   │
       └──────────┬───────────┘
                  │
        ┌─────────┴──────────┐
        ▼                    ▼
┌───────────────┐   ┌──────────────────────┐
│ Resume Tailor │   │ Hiring Manager Agent  │
│ Agent         │   │                       │
│               │   │ • Hunter.io search    │
│ • LLM rewrite │   │ • Cold email draft    │
│ • ATS keyword │   │ • GPT-4o personalized │
│   alignment   │   └──────────┬────────────┘
│ • ReportLab   │              │
│   PDF render  │              │
└───────┬───────┘              │
        └──────────┬───────────┘
                   ▼
       ┌───────────────────────┐
       │ Email Reporter Agent  │
       │                       │
       │ • HTML report build   │
       │ • PDF attachments     │
       │ • SMTP / SendGrid     │
       │ • Local HTML save     │
       └───────────────────────┘
```

---

## Tech Stack

| Layer | Choice | Why |
|---|---|---|
| **LLM** | GPT-4o (OpenAI) | Best JSON extraction accuracy + cost balance |
| **Resume Parsing** | pdfplumber + LLM | pdfplumber preserves layout; LLM extracts structure |
| **Job Discovery** | Adzuna API + YC RSS + Greenhouse API + Lever API | All free/low-cost; official APIs (no scraping) |
| **PDF Generation** | ReportLab | Pure Python, no external dependencies, fully programmatic |
| **Email Delivery** | SMTP (Gmail) or SendGrid | SMTP = free; SendGrid = production reliability |
| **Scheduler** | APScheduler | Battle-tested Python scheduler, no infra needed |
| **Retry Logic** | Tenacity | Handles LLM/API rate limits gracefully |
| **Deduplication** | JSON log file | Simple, reliable, zero-dependency |

---

## Setup

### 1. Clone and install

```bash
git clone https://github.com/YOUR_USERNAME/job-search-agent.git
cd job-search-agent
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
```

### 2. Add your resume

```bash
mkdir -p input
cp /path/to/your/resume.pdf input/resume.pdf
```

### 3. Configure environment

```bash
cp .env.example .env
# Edit .env with your API keys
```

**Required keys:**
- `OPENAI_API_KEY` — from [platform.openai.com](https://platform.openai.com/api-keys)
- `SENDER_EMAIL` + `SENDER_PASSWORD` — Gmail + [App Password](https://myaccount.google.com/apppasswords)

**Optional (improve results):**
- `ADZUNA_APP_ID` + `ADZUNA_APP_KEY` — from [developer.adzuna.com](https://developer.adzuna.com) (free)
- `HUNTER_API_KEY` — from [hunter.io](https://hunter.io) (25 free searches/month)

### 4. Test your setup

```bash
# Parse resume only (no API calls, no email)
python main.py --refresh-resume

# Full pipeline without sending email
python main.py --dry-run

# Full pipeline (sends email)
python main.py
```

### 5. Run on schedule

```bash
# Runs at 6:00 AM and 6:00 PM Eastern Time indefinitely
python main.py --scheduler
```

---

## Configuration

All settings are in `config/settings.py`. Key options:

```python
# Target roles (edit to match your goals)
target_roles = [
    "AI Engineer",
    "Founding AI Engineer",
    "ML Engineer",
    ...
]

# Locations (empty list = no filter)
location_preferences = ["Remote", "New York, NY", "San Francisco, CA"]

# Only fetch jobs posted in the last N hours
max_hours_old = 12

# Minimum LLM relevance score (0.0–1.0) to include a job
min_relevance_score = 0.65

# Max jobs to process per run (cost control)
max_jobs_per_run = 25
```

---

## Deployment Options

### Option A: Run locally (simplest)

```bash
# Keep it running in the background
nohup python main.py --scheduler > logs/scheduler.log 2>&1 &
```

### Option B: systemd service (Linux VPS)

```ini
# /etc/systemd/system/job-agent.service
[Unit]
Description=AI Job Search Agent

[Service]
WorkingDirectory=/path/to/job-search-agent
ExecStart=/path/to/venv/bin/python main.py --scheduler
Restart=always
EnvironmentFile=/path/to/job-search-agent/.env

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable job-agent && sudo systemctl start job-agent
```

### Option C: GitHub Actions (free, cloud-hosted)

```yaml
# .github/workflows/job-search.yml
name: Job Search Agent
on:
  schedule:
    - cron: '0 11 * * *'   # 6 AM ET (UTC-5)
    - cron: '0 23 * * *'   # 6 PM ET (UTC-5)
  workflow_dispatch:

jobs:
  run:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.11' }
      - run: pip install -r requirements.txt
      - run: python main.py
        env:
          OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
          SENDER_EMAIL: ${{ secrets.SENDER_EMAIL }}
          SENDER_PASSWORD: ${{ secrets.SENDER_PASSWORD }}
          RECIPIENT_EMAIL: ${{ secrets.RECIPIENT_EMAIL }}
          ADZUNA_APP_ID: ${{ secrets.ADZUNA_APP_ID }}
          ADZUNA_APP_KEY: ${{ secrets.ADZUNA_APP_KEY }}
          HUNTER_API_KEY: ${{ secrets.HUNTER_API_KEY }}
```

> GitHub Actions is the recommended production deployment — free, zero infrastructure, logs every run.

### Option D: Railway / Render / Fly.io

Deploy as a Docker container with a persistent volume for `output/`. Set environment variables in the platform dashboard.

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["python", "main.py", "--scheduler"]
```

---

## File Structure

```
job-search-agent/
├── main.py                     # Orchestrator + scheduler entry point
├── requirements.txt
├── .env.example                # Template — copy to .env
├── .gitignore
│
├── config/
│   └── settings.py             # All configuration in one place
│
├── agents/
│   ├── resume_agent.py         # PDF parse + LLM profile extraction
│   ├── job_discovery_agent.py  # Multi-source job fetching
│   ├── relevance_agent.py      # LLM job scoring + filtering
│   ├── resume_tailor_agent.py  # LLM rewrite + ReportLab PDF render
│   ├── hiring_manager_agent.py # Contact enrichment + cold email
│   └── email_reporter_agent.py # HTML report + SMTP/SendGrid send
│
├── utils/
│   ├── llm_client.py           # OpenAI/Anthropic wrapper with retry
│   ├── dedup.py                # Seen-jobs rolling log
│   └── logger.py               # Structured logging
│
├── input/
│   └── resume.pdf              # YOUR RESUME HERE (not committed)
│
└── output/
    ├── resumes/                # Tailored PDF resumes
    ├── reports/                # HTML email reports (local copy)
    └── logs/
        ├── agent.log           # Full debug log
        ├── resume_profile.json # Parsed resume cache
        └── seen_jobs.json      # Deduplication log
```

---

## Cost Estimate

| Component | Usage per run | Estimated cost |
|---|---|---|
| GPT-4o (resume parse) | ~4k tokens, once per PDF change | ~$0.02 one-time |
| GPT-4o (job scoring) | ~1k tokens × 25 jobs | ~$0.13/run |
| GPT-4o (resume tailor) | ~3k tokens × 15 jobs | ~$0.24/run |
| GPT-4o (cold email) | ~1k tokens × 15 jobs | ~$0.09/run |
| **Total per run** | | **~$0.46/run** |
| **Total per day (2 runs)** | | **~$0.92/day** |
| **Total per month** | | **~$28/month** |

Use `gpt-4o-mini` for scoring-only steps to reduce costs to ~$5/month with minimal quality loss.

---

## Extending the Agent

### Add a new job source

1. Add a `_fetch_mysource() -> List[JobPosting]` function in `job_discovery_agent.py`
2. Add it to the `futures` list in `discover_jobs()`

### Add more Greenhouse/Lever companies

In `job_discovery_agent.py`, add slugs to `GREENHOUSE_COMPANIES` or `LEVER_COMPANIES`.

### Adjust relevance threshold

In `config/settings.py`:
```python
min_relevance_score = 0.70  # Increase for stricter filtering
```

### Use a different LLM

In `config/settings.py`:
```python
llm.provider = "anthropic"   # or "openai"
llm.model = "claude-3-5-sonnet-20241022"
```

---

## Ethical Usage

- This agent only uses **official public APIs** and public RSS feeds
- It does **not bypass authentication** or access private data
- Hunter.io and similar services only expose **publicly listed** business contacts
- All resume modifications are **truthful** — the LLM is explicitly instructed never to fabricate experience
- Respect each platform's Terms of Service and rate limits

---

## License

MIT
