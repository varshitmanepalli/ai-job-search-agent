# AI Job Search Agent

An automated AI agent that runs on a schedule, discovers fresh AI/ML job postings, tailors your resume for each role, and emails you a formatted report with PDF attachments — all in the cloud with no server to manage.

---

## What it does

Every time it runs, the agent:

1. **Discovers jobs** from Adzuna, YC (Y Combinator), Greenhouse, and Lever — filtered to postings from the last 12 hours
2. **Scores each job** using GPT-4o against your resume and target roles (threshold: 0.65 out of 1.0)
3. **Tailors your resume** for each relevant job — surgically editing your LaTeX source so your formatting and fonts stay intact, then compiling it to PDF
4. **Finds hiring managers** (optional) via Hunter.io and drafts a cold outreach email
5. **Emails you a report** with job cards, scores, resume change summaries, cold email drafts, and tailored PDFs attached

---

## How it works

```
GitHub Actions (scheduled)
         │
         ▼
┌─────────────────┐     ┌─────────────────────────────────────────────────┐
│  Resume Agent   │     │  Job Discovery Agent                            │
│                 │     │                                                 │
│  PDF + .tex  ──►│     │  Adzuna API · YC RSS · Greenhouse · Lever       │
│  Hybrid LLM     │     │  Only jobs posted in the last 12 hours          │
│  extraction     │     └──────────────────────┬──────────────────────────┘
│  Profile cache  │                            │
└────────┬────────┘                            │
         └──────────────────┬─────────────────┘
                            ▼
               ┌─────────────────────┐
               │  Relevance Agent    │
               │  GPT-4o · 0.0–1.0   │
               │  Threshold: 0.65    │
               └──────────┬──────────┘
                          │
              ┌───────────┴───────────┐
              ▼                       ▼
   ┌──────────────────┐   ┌───────────────────────┐
   │  Resume Tailor   │   │  Hiring Manager Agent  │
   │                  │   │                        │
   │  LaTeX pipeline  │   │  Hunter.io lookup      │
   │  (Overleaf fmt)  │   │  Cold email draft      │
   │  or ReportLab    │   └───────────┬────────────┘
   │  fallback        │               │
   └────────┬─────────┘               │
            └──────────┬──────────────┘
                       ▼
          ┌────────────────────────┐
          │  Email Reporter Agent  │
          │                        │
          │  HTML report + scores  │
          │  Resume change bullets │
          │  Cold outreach drafts  │
          │  PDF attachments       │
          │  Gmail SMTP            │
          └────────────────────────┘
```

---

## Two-repo architecture

This project intentionally uses **two GitHub repositories**:

| Repository | Visibility | Contents |
|------------|-----------|---------|
| `ai-job-search-agent` | **Public** | All code, workflow, and config |
| `ai-job-search-resume` | **Private** | Your `resume.pdf` and `resume.tex` only |

Keeping the repos separate means you can share or fork the code publicly without ever exposing your resume. The workflow authenticates to the private repo using a Personal Access Token and pulls the files in at runtime.

---

## Prerequisites

Before starting, you'll need accounts and API keys for:

| Service | Required? | Purpose | Sign up |
|---------|-----------|---------|---------|
| GitHub | Required | Hosts code and runs the schedule | [github.com](https://github.com) |
| OpenAI | Required | GPT-4o for scoring and resume tailoring | [platform.openai.com](https://platform.openai.com) |
| Gmail | Required | Sends the daily email report | Any Gmail account |
| Adzuna | Required | Job listings API | [developer.adzuna.com](https://developer.adzuna.com) — free tier |
| Hunter.io | Optional | Finds hiring manager emails | [hunter.io](https://hunter.io) — 25 free/month |

---

## Setup

### Step 1 — Fork this repository

Click **Fork** on the top right of this page (or clone and push to your own GitHub account):

```bash
git clone https://github.com/ORIGINAL_OWNER/ai-job-search-agent.git
cd ai-job-search-agent

# Point it at your own GitHub account
git remote set-url origin https://github.com/YOUR_USERNAME/ai-job-search-agent.git
git push -u origin master
```

---

### Step 2 — Create your private resume repository

Your resume files need to live in a separate **private** repo so they stay off the public internet.

**Create the repo:**

1. Go to [github.com/new](https://github.com/new)
2. Name it exactly: `ai-job-search-resume`
3. Set visibility to **Private**
4. Click **Create repository**

**Add your resume files:**

```bash
# Clone the new empty repo
git clone https://github.com/YOUR_USERNAME/ai-job-search-resume.git
cd ai-job-search-resume

# Copy in your files
# resume.pdf  — export from Overleaf: Menu → PDF
# resume.tex  — export from Overleaf: Menu → Source → Download
cp /path/to/your/resume.pdf resume.pdf
cp /path/to/your/resume.tex resume.tex

# Push to GitHub
git add resume.pdf resume.tex
git commit -m "Add resume"
git push origin main
```

> `resume.tex` is optional but strongly recommended — it lets the agent preserve your exact Overleaf formatting when tailoring. Without it, the agent falls back to a generic PDF-based layout.

---

### Step 3 — Create a Personal Access Token

The workflow needs permission to read your private resume repo. A Personal Access Token (PAT) grants exactly that — and nothing more.

1. On GitHub, go to **Settings → Developer settings → Personal access tokens → Fine-grained tokens**
2. Click **Generate new token**
3. Fill in:
   - **Name:** `resume-repo-access`
   - **Expiration:** 1 year (recommended) or No expiration
4. Under **Repository access**, choose **Only select repositories** → select `ai-job-search-resume`
5. Under **Permissions → Repository permissions**, set **Contents** to **Read-only**
6. Click **Generate token**
7. **Copy the token immediately** — GitHub only shows it once

---

### Step 4 — Add secrets to your code repository

Go to your `ai-job-search-agent` repo on GitHub:
**Settings → Secrets and variables → Actions → New repository secret**

Add all of these:

| Secret name | What to put | How to get it |
|-------------|------------|---------------|
| `OPENAI_API_KEY` | `sk-...` | [platform.openai.com/api-keys](https://platform.openai.com/api-keys) |
| `SENDER_EMAIL` | your Gmail address | The account that will send the reports |
| `SENDER_PASSWORD` | 16-character App Password | See note below |
| `RECIPIENT_EMAIL` | where to receive reports | Can be the same as `SENDER_EMAIL` |
| `ADZUNA_APP_ID` | your Adzuna App ID | [developer.adzuna.com](https://developer.adzuna.com) dashboard |
| `ADZUNA_APP_KEY` | your Adzuna App Key | Same Adzuna dashboard |
| `RESUME_REPO_PAT` | the token from Step 3 | Paste what you copied |
| `HUNTER_API_KEY` | your Hunter.io key | [hunter.io](https://hunter.io) — optional |

> **Getting a Gmail App Password:**
> Gmail account → **Manage your Google Account** → **Security** → **2-Step Verification** → **App passwords** → create one named `job-agent`. Use the 16-character code it gives you as `SENDER_PASSWORD` — do not use your regular Gmail password.

---

### Step 5 — Set your target roles and preferences

Open `config/settings.py` and adjust to match your job search:

```python
# Who you're looking for
target_roles = [
    "AI Engineer",
    "Founding AI Engineer",
    "ML Engineer",
    "Applied AI Engineer",
]

# Where you want to work
location_preferences = [
    "Remote",
    "New York, NY",
    "San Francisco, CA",
]

# Only show jobs posted within this many hours
max_hours_old = 12

# Minimum relevance score to include a job (0.0–1.0)
min_relevance_score = 0.65
```

Commit and push after editing. Changes take effect on the next run.

---

### Step 6 — Set your run times

Open `config/settings.py` and find `RUN_TIMES_ET` at the very top. Set it to whenever you want the agent to run, using 24-hour Eastern Time — no cron syntax, no UTC conversion needed.

```python
RUN_TIMES_ET = ["06:00", "18:00"]   # 6:00 AM and 6:00 PM Eastern
```

More examples:

```python
RUN_TIMES_ET = ["08:00", "20:00"]          # 8 AM and 8 PM ET
RUN_TIMES_ET = ["09:00"]                   # once a day at 9 AM ET
RUN_TIMES_ET = ["07:00", "12:00", "19:00"] # three times a day
```

After editing, run this script from the repo root — it converts your times to UTC and updates the workflow automatically:

```bash
python scripts/set_schedule.py
```

Then commit and push the changes:

```bash
git add config/settings.py .github/workflows/job-search.yml
git commit -m "Set schedule"
git push
```

The new schedule takes effect immediately on GitHub.

---

### Step 7 — Test it manually

Before relying on the schedule, trigger a test run:

1. Go to the **Actions** tab of your repo
2. Click **AI Job Search Agent** in the left sidebar
3. Click **Run workflow** (top right)
4. Options you can set before running:
   - **Dry run** — runs the full pipeline but skips sending the email (good for first test)
   - **Since hours** — how far back to look for jobs (try `72` for a broader first run)
   - **Clear history** — wipes the seen-jobs log so all jobs appear fresh
5. Click **Run workflow**

Logs stream in real time. Once it finishes:
- Check your inbox for the report email
- Download the HTML report and `agent.log` from the run's **Artifacts** section (kept for 7 days)

---

## Updating your resume

Whenever you update your resume on Overleaf, push the new files to your private resume repo:

```bash
cd ai-job-search-resume    # the folder you cloned in Step 2

cp /path/to/updated-resume.pdf resume.pdf
cp /path/to/updated-resume.tex resume.tex

git add resume.pdf resume.tex
git commit -m "Update resume"
git push origin main
```

The next scheduled run fetches the new files automatically and rebuilds the profile cache.

---

## What's in the email report

Each job listed in the report includes:

- **Role and company** with a relevance score badge (Strong / Good / Moderate)
- **Salary, location, source, and time posted**
- **Hiring manager name and email** (if found via Hunter.io)
- **Cold outreach email draft** — ready to copy and send
- **Resume changes made for this role** — a short bullet list of every edit the AI made to your resume
- **Apply link** — one click to the job posting

Tailored PDF resumes are attached to the email, one per job.

---

## Clearing seen-jobs history

The agent tracks jobs it has already shown you (rolling 30-day window) so you don't see duplicates in every report. To reset this and see all current jobs fresh:

1. Go to **Actions → AI Job Search Agent → Run workflow**
2. Check **Clear seen-jobs history**
3. Click **Run workflow**

---

## How LaTeX tailoring works

If you provide `resume.tex` (from Overleaf), the agent tailors your resume without touching your formatting:

1. Strips all LaTeX commands from the `.tex` source to get plain text
2. Sends the plain text + job description to GPT-4o
3. GPT-4o returns a list of surgical `{old, new}` text replacements
4. Those replacements are applied verbatim to the raw `.tex` source
5. The modified `.tex` is compiled to PDF via [TeXLive.net](https://texlive.net)

Your preamble, custom commands, fonts, column layout — none of it is touched. Only the actual text content changes.

The agent is explicitly instructed never to fabricate experience, dates, or companies. Every change is truthful.

---

## Changing things later

| What to change | Where | How |
|----------------|-------|-----|
| Run times | `config/settings.py` → `RUN_TIMES_ET` | Edit, run `python scripts/set_schedule.py`, push |
| Target roles | `config/settings.py` → `target_roles` | Edit and push |
| Location filter | `config/settings.py` → `location_preferences` | Edit and push |
| Job freshness | `config/settings.py` → `max_hours_old` | Edit and push |
| Relevance cutoff | `config/settings.py` → `min_relevance_score` | Edit and push |
| Resume files | `ai-job-search-resume` repo | Push new `resume.pdf` / `resume.tex` |
| Secrets / API keys | Repo → Settings → Secrets | Update the relevant secret |

---

## Running locally (optional)

If you want to test on your own machine before deploying:

```bash
git clone https://github.com/YOUR_USERNAME/ai-job-search-agent.git
cd ai-job-search-agent

python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# Open .env and fill in your API keys

# Copy your resume files into input/
cp /path/to/resume.pdf input/resume.pdf
cp /path/to/resume.tex input/resume.tex   # optional

# Parse resume and check extraction
python main.py --refresh-resume

# Full run, no email sent
python main.py --dry-run

# Full run with email
python main.py

# Look back 72 hours for jobs (good for first run)
python main.py --since-hours 72

# Reset seen-jobs history
python main.py --clear-history
```

---

## Cost estimate

All costs come from OpenAI API usage. GitHub Actions is free.

| Component | Per run | Notes |
|-----------|---------|-------|
| Resume parsing | ~$0.02 | Cached — only charged when resume changes |
| Job scoring (25 jobs) | ~$0.13 | Switch to `gpt-4o-mini` for ~10× savings |
| Resume tailoring (15 jobs) | ~$0.24 | GPT-4o |
| Cold email drafting (15 jobs) | ~$0.09 | GPT-4o |
| **Total per run** | **~$0.46** | |
| **Per day (2 runs)** | **~$0.92** | |
| **Per month** | **~$28** | Use `gpt-4o-mini` for scoring → ~$5/month |

To switch models, edit `config/settings.py`:

```python
llm.model = "gpt-4o-mini"   # cheaper, good for scoring
llm.model = "gpt-4o"        # better quality, recommended for tailoring
```

---

## Tech stack

| Layer | Technology |
|-------|-----------|
| LLM | GPT-4o via OpenAI API |
| Resume extraction | pdfplumber (PDF) + regex parser (LaTeX) |
| LaTeX compilation | [TeXLive.net](https://texlive.net) — free, no install |
| Job sources | Adzuna API, YC RSS, Greenhouse API, Lever API |
| Email | Gmail SMTP |
| Scheduling | GitHub Actions cron |
| Deduplication | Rolling 30-day JSON log |

---

## File structure

```
ai-job-search-agent/          ← this repo (public)
├── .github/workflows/
│   └── job-search.yml          # GitHub Actions workflow (auto-managed — don't edit directly)
├── config/
│   └── settings.py             # ← all user config lives here
├── agents/
│   ├── resume_agent.py         # parses resume (PDF + LaTeX hybrid)
│   ├── job_discovery_agent.py  # fetches jobs from all sources
│   ├── relevance_agent.py      # scores jobs against your profile
│   ├── resume_tailor_agent.py  # edits and compiles resume per job
│   ├── hiring_manager_agent.py # Hunter.io lookup + cold email
│   └── email_reporter_agent.py # builds and sends the HTML report
├── utils/
│   ├── latex_compiler.py       # TeXLive.net API client
│   ├── latex_tailor.py         # LLM replacement logic
│   ├── llm_client.py           # OpenAI wrapper with retry
│   ├── dedup.py                # seen-jobs tracking
│   └── logger.py               # logging
├── scripts/
│   └── set_schedule.py         # converts RUN_TIMES_ET to UTC crons
├── input/                      # fetched at runtime — not committed
├── output/                     # generated at runtime — not committed
├── main.py                     # entry point
├── requirements.txt
└── .env.example

ai-job-search-resume/         ← separate private repo
├── resume.pdf
└── resume.tex
```

---

## License

MIT
