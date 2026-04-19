"""
Central configuration for the AI Job Search Agent.
All secrets are loaded from environment variables or a .env file.
"""

import os
from dataclasses import dataclass, field
from typing import List, Optional
from dotenv import load_dotenv

load_dotenv()


@dataclass
class LLMConfig:
    provider: str = "openai"                          # "openai" | "anthropic" | "gemini"
    model: str = "gpt-4o-mini"
    temperature: float = 0.2
    max_tokens: int = 4096
    api_key: str = field(default_factory=lambda: os.getenv("OPENAI_API_KEY", ""))


@dataclass
class JobSearchConfig:
    target_roles: List[str] = field(default_factory=lambda: [
        "AI Engineer",
        "ML Engineer",
        "Machine Learning Engineer",
        "Founding AI Engineer",
        "Founding ML Engineer",
        "AI/ML Engineer",
        "Applied AI Engineer",
        "Applied ML Engineer",
        "AI Research Engineer",
    ])
    location_preferences: List[str] = field(default_factory=lambda: [
        "Remote",
        "New York, NY",
        "San Francisco, CA",
        "Seattle, WA",
    ])
    max_hours_old: int = 12          # Only fetch jobs posted within the last N hours
    max_jobs_per_run: int = 25       # Cap to avoid LLM cost explosion
    min_relevance_score: float = 0.65  # 0.0–1.0; only jobs above this are included


@dataclass
class EmailConfig:
    sender_email: str = field(default_factory=lambda: os.getenv("SENDER_EMAIL", ""))
    sender_password: str = field(default_factory=lambda: os.getenv("SENDER_PASSWORD", ""))  # App password
    recipient_email: str = field(default_factory=lambda: os.getenv("RECIPIENT_EMAIL", "varshitmanepalli1810@gmail.com"))
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    # Optional: SendGrid
    sendgrid_api_key: str = field(default_factory=lambda: os.getenv("SENDGRID_API_KEY", ""))
    use_sendgrid: bool = False


@dataclass
class ScheduleConfig:
    # Times in Eastern Time (America/New_York)
    run_times_et: List[str] = field(default_factory=lambda: ["06:00", "18:00"])
    timezone: str = "America/New_York"


@dataclass
class PathConfig:
    resume_pdf: str = "input/resume.pdf"
    resume_tex: str = "input/resume.tex"      # Overleaf LaTeX source (optional but preferred)
    resume_profile_cache: str = "output/logs/resume_profile.json"
    seen_jobs_log: str = "output/logs/seen_jobs.json"
    tailored_resumes_dir: str = "output/resumes"
    reports_dir: str = "output/reports"


@dataclass
class AppConfig:
    llm: LLMConfig = field(default_factory=LLMConfig)
    search: JobSearchConfig = field(default_factory=JobSearchConfig)
    email: EmailConfig = field(default_factory=EmailConfig)
    schedule: ScheduleConfig = field(default_factory=ScheduleConfig)
    paths: PathConfig = field(default_factory=PathConfig)
    debug: bool = field(default_factory=lambda: os.getenv("DEBUG", "false").lower() == "true")


# Singleton
config = AppConfig()
