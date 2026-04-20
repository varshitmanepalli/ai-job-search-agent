# AI Job Search Agent

Automated AI agent that runs twice daily, finds relevant AI/ML job postings, tailors your Overleaf resume for each role, and emails you a formatted HTML report with PDF attachments — entirely in the cloud via GitHub Actions.

---

## How it works

```
GitHub Actions (cron: 6 AM + 6 PM ET)
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
          │  Gmail SMTP / SendGrid │
          └────────────────────────┘
```

---

## Cloud Deployment (GitHub Actions)

> **This is the recommended and primary deployment path.** No server, no Docker, no cron daemon. GitHub runs it free on a schedule.

### Step 1 — Fork or clone the repo

```bash
git clone https://github.com/varshitmanepalli/ai-job-search-agent.git
cd ai-job-search-agent
```

If you're setting this up for the first time, push it to your own GitHub account:

```bash
# Create a new private repo on github.com, then:
git remote set-url origin https://github.com/YOUR_USERNAME/ai-job-search-agent.git
git push -u origin master
```

---

### Step 2 — Upload your resume files

Resume files are stored in a dedicated **`resume-assets` branch** — a separate, isolated branch with no code, just your PDF and `.tex` file. This avoids GitHub Secrets size limits entirely and makes updating your resume as simple as a `git push`.

**One-time setup — clone the branch and add your files:**

```bash
# Clone only the resume-assets branch into a separate folder
git clone --branch resume-assets --single-branch \
  https://github.com/varshitmanepalli/ai-job-search-agent.git resume-assets
cd resume-assets

# Copy your resume files in
cp /path/to/your/resume.pdf resume.pdf
cp /path/to/your/resume.tex resume.tex   # from Overleaf → Menu → Source → Download

# Push to GitHub
git add resume.pdf resume.tex
git commit -m "Add resume files"
git push origin resume-assets
```

That's it. The workflow automatically checks out this branch and copies the files into `input/` before each run.

**Updating your resume later** — just push new files to the same branch:

```bash
cd resume-assets                         # the folder you cloned above
cp /path/to/updated-resume.pdf resume.pdf
cp /path/to/updated-resume.tex resume.tex
git add resume.pdf resume.tex
git commit -m "Update resume"
git push origin resume-assets
```

The next scheduled run picks them up automatically and rebuilds the profile cache.

---

### Step 3 — Add GitHub Secrets

Go to your repo on GitHub:
**Settings → Secrets and variables → Actions → New repository secret**

Resume files are stored in the `resume-assets` branch — **not** as secrets. Only API keys go here.

| Secret | Value | Where to get it |
|--------|-------|-----------------|
| `OPENAI_API_KEY` | `sk-...` | [platform.openai.com/api-keys](https://platform.openai.com/api-keys) |
| `SENDER_EMAIL` | Your Gmail address | The account used to send reports |
| `SENDER_PASSWORD` | 16-char App Password | [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords) — **not** your Gmail password |
| `RECIPIENT_EMAIL` | Email to receive reports | Can be the same as `SENDER_EMAIL` |
| `ADZUNA_APP_ID` | e.g. `0eb6bb03` | [developer.adzuna.com](https://developer.adzuna.com) — free tier |
| `ADZUNA_APP_KEY` | e.g. `266e2b6b...` | Same Adzuna dashboard |
| `HUNTER_API_KEY` | *(optional)* | [hunter.io](https://hunter.io) — 25 free searches/month for hiring manager lookup |

> **Gmail App Password setup:**
> Google Account → Security → 2-Step Verification → App passwords → Create one named "job-agent"

---

### Step 4 — Test the workflow manually

Before waiting for the scheduled run, trigger it manually:

1. Go to **Actions** tab in your GitHub repo
2. Click **AI Job Search Agent** in the left sidebar
3. Click **Run workflow** (top right)
4. Optional: check **Dry run** to test without sending email
5. Click **Run workflow**

Watch the run complete. Logs are visible in real time. After it finishes:
- **Email** arrives in your inbox with the report
- **Artifacts** (HTML report + agent.log) are downloadable from the run page for 7 days

---

### Step 5 — Change the run times (whenever you want)

Open `.github/workflows/job-search.yml` in the repo and find the two `cron:` lines near the top. The file has a full reference table built in.

**Quick reference — ET → UTC conversion:**

| Desired ET time | Winter cron (Nov–Mar) | Summer cron (Mar–Nov) |
|-----------------|-----------------------|-----------------------|
| 6:00 AM ET | `0 11 * * *` | `0 10 * * *` |
| 7:00 AM ET | `0 12 * * *` | `0 11 * * *` |
| 8:00 AM ET | `0 13 * * *` | `0 12 * * *` |
| 9:00 AM ET | `0 14 * * *` | `0 13 * * *` |
| 12:00 PM ET | `0 17 * * *` | `0 16 * * *` |
| 6:00 PM ET | `0 23 * * *` | `0 22 * * *` |
| 7:00 PM ET | `0 0 * * *` | `0 23 * * *` |
| 8:00 PM ET | `0 1 * * *` | `0 0 * * *` |

**Example — change from 6 AM / 6 PM to 8 AM / 8 PM (summer):**

```yaml
# Before
- cron: '0 11 * * *'   # 6:00 AM ET (winter EST)
- cron: '0 23 * * *'   # 6:00 PM ET (winter EST)

# After
- cron: '0 12 * * *'   # 8:00 AM ET (summer EDT)
- cron: '0 0 * * *'    # 8:00 PM ET (summer EDT)
```

Commit and push the change — GitHub picks it up immediately, no other steps needed.

---

### What runs in each job

| Step | What it does |
|------|-------------|
| Checkout | Pulls latest code |
| Python setup | Installs 3.11 with pip cache |
| Restore resume cache | Avoids re-parsing if PDF unchanged |
| Restore seen-jobs log | Prevents duplicate job alerts across runs |
| Checkout resume-assets branch | Checks out your private `resume-assets` branch into a staging folder |
| Copy resume files | Copies `resume.pdf` (required) and `resume.tex` (optional) into `input/` |
| Run agent | Full pipeline: discover → score → tailor → email |
| Upload artifacts | Saves HTML report + agent.log for 7 days |
| Save seen-jobs log | Persists dedup state so next run doesn't repeat jobs |

---

## Changing the schedule or job preferences

### Run times

Edit `.github/workflows/job-search.yml` — see Step 5 above.

### Target roles

Edit `config/settings.py`:

```python
target_roles = [
    "AI Engineer",
    "Founding AI Engineer",
    "ML Engineer",
    "Applied AI Engineer",
    # Add or remove any roles here
]
```

Commit and push. The change takes effect on the next run.

### Location preferences

```python
location_preferences = [
    "Remote",
    "New York, NY",
    "San Francisco, CA",
    # Add cities or leave empty for no filter
]
```

### Job freshness window

```python
max_hours_old = 12   # Only show jobs posted in the last 12 hours
```

For the first run (backfill), trigger manually with `since_hours = 72` from the workflow dispatch input.

### Relevance threshold

```python
min_relevance_score = 0.65   # 0.0–1.0; raise for stricter filtering
```

### LLM model

```python
llm.model = "gpt-4o-mini"   # Cheaper; good for scoring
llm.model = "gpt-4o"        # Better; recommended for tailoring
```

---

## Updating your resume

When you update your resume on Overleaf:

1. Download the PDF (`resume.pdf`) and the `.tex` source from Overleaf
2. Go to the `resume-assets` folder you cloned in Step 2 of the setup
3. Copy the new files in and push:
   ```bash
   cd resume-assets
   cp /path/to/updated-resume.pdf resume.pdf
   cp /path/to/updated-resume.tex resume.tex
   git add resume.pdf resume.tex
   git commit -m "Update resume"
   git push origin resume-assets
   ```
4. The next run automatically uses the new files and rebuilds the profile cache

---

## What the email report contains

Each job card in the report shows:

- **Role + company** with relevance score badge (Strong / Good / Moderate)
- **Salary, location, source, posted time**
- **Hiring manager name + email** (when found via Hunter.io)
- **Cold outreach draft** — personalized email ready to copy-paste
- **Resume edits made for this role** — bullet list of every change the LLM made to your resume for this specific job
- **Apply button** linking directly to the job posting

Tailored resume PDFs are attached to the email, one per job.

---

## LaTeX Resume Integration (Overleaf)

When `resume.tex` is present in the `resume-assets` branch, the agent uses your exact Overleaf LaTeX source instead of rebuilding a generic layout. Your fonts, column structure, custom commands, and spacing are preserved exactly.

### How tailoring works

The LLM never sees your LaTeX commands. The pipeline:

1. Extracts plain text from the `.tex` body (strips all `\command{...}` syntax)
2. Sends only the plain text + job description to GPT-4o
3. GPT-4o returns a list of `{old: "...", new: "..."}` surgical replacements
4. Each replacement is applied as a verbatim string substitution into the raw `.tex`
5. The modified `.tex` is compiled to PDF via [TeXLive.net](https://texlive.net) — free, full TeX Live, no install needed

Your preamble, `\newcommand` definitions, column layout, and fonts are never touched. The modified `.tex` is also saved in `output/resumes/` for inspection.

### Compiler options

Set `LATEX_COMPILER` in `.env` (local runs only — GitHub uses the default):

| Template type | Setting |
|---|---|
| Standard (RenderCV, Jake's Resume, etc.) | `pdflatex` (default — no need to set) |
| Uses `fontspec` / custom TTF fonts | `xelatex` |
| LuaLaTeX template | `lualatex` |

### Profile extraction

Resume extraction is hybrid — both sources are combined before the LLM parse:
- **PDF** — accurate contact info and layout, but may mis-order multi-column text
- **LaTeX source** — perfectly ordered, captures every bullet and skill that PDF extraction drops in two-column layouts

The profile cache invalidates automatically whenever either file changes.

---

## File structure

```
ai-job-search-agent/
├── .github/
│   └── workflows/
│       └── job-search.yml      # ← EDIT THIS to change run times
│
├── config/
│   └── settings.py             # ← EDIT THIS to change roles, thresholds, model
│
├── agents/
│   ├── resume_agent.py         # PDF + LaTeX hybrid extraction, profile cache
│   ├── job_discovery_agent.py  # Adzuna, YC RSS, Greenhouse, Lever
│   ├── relevance_agent.py      # GPT-4o scoring (0.0–1.0)
│   ├── resume_tailor_agent.py  # LaTeX surgical edits + TeXLive compile
│   ├── hiring_manager_agent.py # Hunter.io lookup + cold email draft
│   └── email_reporter_agent.py # HTML report builder + SMTP/SendGrid
│
├── utils/
│   ├── latex_compiler.py       # TeXLive.net REST API client
│   ├── latex_tailor.py         # LLM replacement extractor + applier
│   ├── llm_client.py           # OpenAI/Anthropic wrapper with retry
│   ├── dedup.py                # Rolling 30-day seen-jobs log
│   └── logger.py               # UTF-8 structured logging
│
├── input/                      # Not committed — restored from secrets at runtime
│   ├── resume.pdf
│   └── resume.tex
│
├── output/                     # Generated at runtime
│   ├── resumes/                # Tailored PDFs (one per job)
│   ├── reports/                # HTML email reports
│   └── logs/
│       ├── agent.log
│       ├── resume_profile.json # Profile cache
│       └── seen_jobs.json      # Dedup log
│
├── main.py                     # Orchestrator + CLI
├── requirements.txt
├── .env.example                # Copy to .env for local runs
└── .gitignore
```

---

## Running locally (optional)

```bash
git clone https://github.com/varshitmanepalli/ai-job-search-agent.git
cd ai-job-search-agent
python -m venv venv && source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# Fill in .env with your API keys
cp /path/to/resume.pdf input/resume.pdf
cp /path/to/resume.tex input/resume.tex  # optional but recommended

# Parse and inspect your resume profile only
python main.py --refresh-resume

# Full run without sending email
python main.py --dry-run

# Full run (sends email)
python main.py

# First run — look back 72 hours instead of default 12
python main.py --since-hours 72

# Clear dedup history (see all jobs again)
python main.py --clear-history
```

---

## Cost estimate

| Component | Per run | Notes |
|-----------|---------|-------|
| Resume parse | ~$0.02 | One-time; cached until resume changes |
| Job scoring (25 jobs) | ~$0.13 | GPT-4o; use `gpt-4o-mini` for ~10× savings |
| Resume tailoring (15 jobs) | ~$0.24 | LaTeX surgical edits via GPT-4o |
| Cold email drafting (15 jobs) | ~$0.09 | GPT-4o |
| **Total per run** | **~$0.46** | |
| **Total per day (2 runs)** | **~$0.92** | |
| **Total per month** | **~$28** | Switch scoring to `gpt-4o-mini` → ~$5/month |

---

## Tech stack

| Layer | Choice |
|---|---|
| LLM | GPT-4o (OpenAI) |
| Resume extraction | pdfplumber (PDF) + regex stripper (LaTeX) — hybrid |
| LaTeX compilation | [TeXLive.net](https://texlive.net) — free, full TeX Live, no install |
| Job sources | Adzuna API, YC RSS, Greenhouse API, Lever API |
| Email delivery | Gmail SMTP or SendGrid |
| Scheduling | GitHub Actions cron |
| Retry logic | Tenacity |
| Deduplication | Rolling 30-day JSON log |

---

## Ethical usage

- Uses only official public APIs and public RSS feeds
- Does not bypass authentication or access private data
- Hunter.io only exposes publicly listed business contacts
- All resume modifications are truthful — the LLM is explicitly instructed never to fabricate experience, dates, or companies
- Respects each platform's rate limits

---

## License

MIT
