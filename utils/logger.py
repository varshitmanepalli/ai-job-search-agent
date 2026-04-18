"""Structured logging setup."""

import io
import logging
import sys
from pathlib import Path


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.DEBUG)

    formatter = logging.Formatter(
        "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console handler — force UTF-8 on Windows (cp1252 can't render → arrows etc.)
    if hasattr(sys.stdout, "buffer"):
        utf8_stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    else:
        utf8_stdout = sys.stdout
    ch = logging.StreamHandler(utf8_stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(formatter)
    logger.addHandler(ch)

    # File handler — always UTF-8
    log_dir = Path("output/logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    fh = logging.FileHandler(log_dir / "agent.log", encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(formatter)
    logger.addHandler(fh)

    return logger
