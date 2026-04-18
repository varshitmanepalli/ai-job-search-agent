"""
LLM Client — thin wrapper around OpenAI (or Anthropic / Gemini).
Handles retries, rate limiting, and provider switching.

Dependencies:
    pip install openai anthropic tenacity
"""

import os
import time
from typing import Optional

from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from config.settings import config
from utils.logger import get_logger

logger = get_logger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# OpenAI
# ──────────────────────────────────────────────────────────────────────────────

def _openai_chat(
    system: str,
    user: str,
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    response_format: Optional[dict] = None,
) -> str:
    from openai import OpenAI, RateLimitError, APIError
    client = OpenAI(api_key=config.llm.api_key)
    kwargs = {
        "model": model or config.llm.model,
        "temperature": temperature if temperature is not None else config.llm.temperature,
        "max_tokens": max_tokens or config.llm.max_tokens,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    if response_format:
        kwargs["response_format"] = response_format

    response = client.chat.completions.create(**kwargs)
    return response.choices[0].message.content


# ──────────────────────────────────────────────────────────────────────────────
# Anthropic fallback
# ──────────────────────────────────────────────────────────────────────────────

def _anthropic_chat(system: str, user: str, **kwargs) -> str:
    import anthropic
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY", ""))
    response = client.messages.create(
        model="claude-3-5-sonnet-20241022",
        max_tokens=kwargs.get("max_tokens", config.llm.max_tokens),
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return response.content[0].text


# ──────────────────────────────────────────────────────────────────────────────
# Public interface with retry
# ──────────────────────────────────────────────────────────────────────────────

@retry(
    retry=retry_if_exception_type(Exception),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    stop=stop_after_attempt(3),
    reraise=True,
)
def chat_completion(
    system: str,
    user: str,
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    response_format: Optional[dict] = None,
) -> str:
    provider = config.llm.provider
    try:
        if provider == "openai":
            return _openai_chat(system, user, model, temperature, max_tokens, response_format)
        elif provider == "anthropic":
            return _anthropic_chat(system, user, max_tokens=max_tokens)
        else:
            raise ValueError(f"Unknown LLM provider: {provider}")
    except Exception as e:
        logger.warning(f"LLM call failed ({provider}): {e} — retrying...")
        raise
