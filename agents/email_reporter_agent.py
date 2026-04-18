# -*- coding: utf-8 -*-
"""
Email Reporter Agent
====================
Composes and sends a single consolidated HTML email report per run,
containing all matched jobs with tailored resumes attached as PDFs.

Delivery options:
  1. SMTP (Gmail with App Password) — default
  2. SendGrid — set USE_SENDGRID=true in .env

Dependencies:
    pip install sendgrid jinja2
"""

import os
import smtplib
from datetime import datetime, timezone
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import List, Optional

from agents.job_discovery_agent import JobPosting
from agents.resume_agent import ResumeProfile
from config.settings import config
from utils.logger import get_logger

logger = get_logger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# HTML template
# ──────────────────────────────────────────────────────────────────────────────

EMAIL_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
          background: #f0f4f8; color: #1a1a2e; }}
  .wrapper {{ max-width: 700px; margin: 0 auto; padding: 24px 16px; }}
  .header {{ background: linear-gradient(135deg, #1a3a5c, #2563eb);
             border-radius: 12px; padding: 28px 32px; color: white; margin-bottom: 24px; }}
  .header h1 {{ font-size: 22px; font-weight: 700; margin-bottom: 4px; }}
  .header p {{ font-size: 14px; opacity: 0.85; }}
  .stats {{ display: flex; gap: 16px; margin-top: 16px; }}
  .stat {{ background: rgba(255,255,255,0.15); border-radius: 8px;
           padding: 10px 16px; flex: 1; text-align: center; }}
  .stat .num {{ font-size: 24px; font-weight: 700; }}
  .stat .label {{ font-size: 11px; opacity: 0.8; text-transform: uppercase; }}
  .job-card {{ background: white; border-radius: 12px; padding: 24px;
               margin-bottom: 20px; box-shadow: 0 2px 8px rgba(0,0,0,0.06);
               border-left: 4px solid #2563eb; }}
  .job-card.strong {{ border-left-color: #16a34a; }}
  .job-card.good {{ border-left-color: #2563eb; }}
  .job-card.weak {{ border-left-color: #d97706; }}
  .job-header {{ display: flex; justify-content: space-between; align-items: flex-start;
                 margin-bottom: 12px; }}
  .job-title {{ font-size: 17px; font-weight: 700; color: #1a3a5c; }}
  .company {{ font-size: 14px; color: #555; margin-top: 2px; }}
  .score-badge {{ padding: 4px 10px; border-radius: 20px; font-size: 12px;
                  font-weight: 600; white-space: nowrap; }}
  .score-strong {{ background: #dcfce7; color: #16a34a; }}
  .score-good {{ background: #dbeafe; color: #1d4ed8; }}
  .score-weak {{ background: #fef9c3; color: #92400e; }}
  .meta {{ display: grid; grid-template-columns: 1fr 1fr; gap: 8px;
           margin: 12px 0; font-size: 13px; color: #555; }}
  .meta-item {{ display: flex; align-items: center; gap: 6px; }}
  .meta-label {{ font-weight: 600; color: #333; }}
  .section {{ margin-top: 14px; }}
  .section-title {{ font-size: 12px; font-weight: 700; color: #888;
                    text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 8px; }}
  .cold-email {{ background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px;
                 padding: 14px; font-size: 13px; white-space: pre-wrap;
                 font-family: monospace; line-height: 1.5; color: #334155; }}
  .actions {{ display: flex; gap: 10px; margin-top: 16px; flex-wrap: wrap; }}
  .btn {{ display: inline-block; padding: 8px 18px; border-radius: 6px;
          font-size: 13px; font-weight: 600; text-decoration: none; }}
  .btn-primary {{ background: #2563eb; color: white; }}
  .btn-secondary {{ background: #f1f5f9; color: #334155; border: 1px solid #e2e8f0; }}
  .footer {{ text-align: center; padding: 20px; font-size: 12px; color: #888; }}
  .divider {{ height: 1px; background: #e2e8f0; margin: 8px 0; }}
</style>
</head>
<body>
<div class="wrapper">
  <div class="header">
    <h1>🤖 AI Job Search Report</h1>
    <p>Run: {run_time} · {run_label}</p>
    <div class="stats">
      <div class="stat"><div class="num">{total_jobs}</div><div class="label">Jobs Found</div></div>
      <div class="stat"><div class="num">{strong_count}</div><div class="label">Strong Matches</div></div>
      <div class="stat"><div class="num">{with_contact}</div><div class="label">Contact Found</div></div>
    </div>
  </div>

  {job_cards}

  <div class="footer">
    <p>AI Job Search Agent · Varshit Manepalli · varshitmanepalli1810@gmail.com</p>
    <p>Tailored resume PDFs are attached to this email.</p>
  </div>
</div>
</body>
</html>
"""

JOB_CARD_TEMPLATE = """
<div class="job-card {tier_class}">
  <div class="job-header">
    <div>
      <div class="job-title">{title}</div>
      <div class="company">{company} · {location}</div>
    </div>
    <span class="score-badge {score_class}">{tier} · {score}%</span>
  </div>

  <div class="divider"></div>

  <div class="meta">
    <div class="meta-item"><span class="meta-label">Posted:</span> {posted_at}</div>
    <div class="meta-item"><span class="meta-label">Source:</span> {source}</div>
    {salary_row}
    {manager_row}
    {manager_email_row}
  </div>

  {cold_email_section}

  <div class="actions">
    <a href="{apply_url}" class="btn btn-primary" target="_blank">Apply Now</a>
    {resume_link}
  </div>
</div>
"""


def _score_to_tier(score: float) -> tuple:
    if score >= 0.85:
        return "Strong Match", "strong", "score-strong"
    elif score >= 0.65:
        return "Good Match", "good", "score-good"
    else:
        return "Moderate Match", "weak", "score-weak"


def _render_job_card(job: JobPosting) -> str:
    tier_label, tier_class, score_class = _score_to_tier(job.relevance_score)
    score_pct = int(job.relevance_score * 100)

    posted = job.posted_at.strftime("%b %d, %Y %I:%M %p UTC") if job.posted_at else "N/A"

    salary_row = f'<div class="meta-item"><span class="meta-label">Salary:</span> {job.salary_range}</div>' if job.salary_range else ""
    manager_row = f'<div class="meta-item"><span class="meta-label">Hiring Manager:</span> {job.hiring_manager_name}</div>' if job.hiring_manager_name else ""
    manager_email_row = f'<div class="meta-item"><span class="meta-label">Contact:</span> <a href="mailto:{job.hiring_manager_email}">{job.hiring_manager_email}</a></div>' if job.hiring_manager_email else ""

    cold_email_section = ""
    if job.cold_email_draft:
        cold_email_section = f"""
    <div class="section">
      <div class="section-title">Cold Outreach Draft</div>
      <div class="cold-email">{job.cold_email_draft.replace('<', '&lt;').replace('>', '&gt;')}</div>
    </div>
    """

    resume_link = ""  # Resumes are attached as PDFs

    return JOB_CARD_TEMPLATE.format(
        title=job.title,
        company=job.company,
        location=job.location or "Remote",
        tier=tier_label,
        tier_class=tier_class,
        score_class=score_class,
        score=score_pct,
        posted_at=posted,
        source=job.source,
        salary_row=salary_row,
        manager_row=manager_row,
        manager_email_row=manager_email_row,
        cold_email_section=cold_email_section,
        apply_url=job.apply_url,
        resume_link=resume_link,
    )


def build_email_html(jobs: List[JobPosting], run_label: str) -> str:
    run_time = datetime.now(timezone.utc).strftime("%B %d, %Y %I:%M %p UTC")
    job_cards = "\n".join(_render_job_card(j) for j in jobs)
    strong_count = sum(1 for j in jobs if j.relevance_score >= 0.85)
    with_contact = sum(1 for j in jobs if j.hiring_manager_email)

    return EMAIL_TEMPLATE.format(
        run_time=run_time,
        run_label=run_label,
        total_jobs=len(jobs),
        strong_count=strong_count,
        with_contact=with_contact,
        job_cards=job_cards,
    )


# ──────────────────────────────────────────────────────────────────────────────
# Email delivery
# ──────────────────────────────────────────────────────────────────────────────

def _send_via_smtp(subject: str, html: str, attachments: List[str]):
    cfg = config.email
    msg = MIMEMultipart("mixed")
    msg["Subject"] = subject
    msg["From"] = cfg.sender_email
    msg["To"] = cfg.recipient_email

    msg.attach(MIMEText(html, "html"))

    for pdf_path in attachments:
        if pdf_path and os.path.exists(pdf_path):
            with open(pdf_path, "rb") as f:
                part = MIMEBase("application", "pdf")
                part.set_payload(f.read())
            encoders.encode_base64(part)
            part.add_header(
                "Content-Disposition",
                f'attachment; filename="{Path(pdf_path).name}"',
            )
            msg.attach(part)

    with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port) as server:
        server.starttls()
        server.login(cfg.sender_email, cfg.sender_password)
        server.sendmail(cfg.sender_email, cfg.recipient_email, msg.as_string())
    logger.info(f"Email sent via SMTP to {cfg.recipient_email}")


def _send_via_sendgrid(subject: str, html: str, attachments: List[str]):
    import base64
    from sendgrid import SendGridAPIClient
    from sendgrid.helpers.mail import (
        Mail, Attachment, FileContent, FileName, FileType, Disposition,
    )
    cfg = config.email
    message = Mail(
        from_email=cfg.sender_email,
        to_emails=cfg.recipient_email,
        subject=subject,
        html_content=html,
    )
    for pdf_path in attachments:
        if pdf_path and os.path.exists(pdf_path):
            with open(pdf_path, "rb") as f:
                encoded = base64.b64encode(f.read()).decode()
            att = Attachment(
                FileContent(encoded),
                FileName(Path(pdf_path).name),
                FileType("application/pdf"),
                Disposition("attachment"),
            )
            message.add_attachment(att)
    sg = SendGridAPIClient(cfg.sendgrid_api_key)
    sg.send(message)
    logger.info(f"Email sent via SendGrid to {cfg.recipient_email}")


def send_report(jobs: List[JobPosting], pdf_paths: List[str], run_label: str = ""):
    """Send the consolidated job report email with PDF attachments."""
    if not jobs:
        logger.info("No jobs to report — skipping email.")
        return

    run_label = run_label or datetime.now().strftime("%I:%M %p ET")
    subject = f"[Job Alert] {len(jobs)} New AI/ML Roles · {datetime.now().strftime('%b %d')} {run_label}"
    html = build_email_html(jobs, run_label)

    # Save HTML report to disk
    report_path = os.path.join(
        config.paths.reports_dir,
        f"report_{datetime.now().strftime('%Y%m%d_%H%M')}.html",
    )
    Path(report_path).parent.mkdir(parents=True, exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(html)
    logger.info(f"HTML report saved: {report_path}")

    cfg = config.email
    if cfg.use_sendgrid and cfg.sendgrid_api_key:
        _send_via_sendgrid(subject, html, pdf_paths)
    elif cfg.sender_email and cfg.sender_password:
        _send_via_smtp(subject, html, pdf_paths)
    else:
        logger.warning("No email credentials configured. Report saved locally only.")
