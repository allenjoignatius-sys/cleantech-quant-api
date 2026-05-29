"""
Factory that builds a :class:`ResilientExtractor` wired to a structured-LLM
backend when one is configured (``settings.LLM_EXTRACTION_ENABLED`` + key),
otherwise rule-based only. The LLM client is created lazily and any failure to
build it degrades silently to the rule-based extractor.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from app.config import settings
from app.nlp.extraction import ResilientExtractor, LLMClient

logger = logging.getLogger(__name__)


def _make_llm_client() -> Optional[LLMClient]:
    if not (settings.LLM_EXTRACTION_ENABLED and settings.LLM_API_KEY and settings.LLM_PROVIDER):
        return None

    provider = settings.LLM_PROVIDER.lower()
    model = settings.LLM_MODEL
    key = settings.LLM_API_KEY

    def _client(prompt: str) -> str:
        import httpx  # local import keeps module import light
        if provider == "anthropic":
            resp = httpx.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                         "content-type": "application/json"},
                json={"model": model or "claude-sonnet-4-6", "max_tokens": 1024,
                      "messages": [{"role": "user", "content": prompt}]},
                timeout=30.0,
            )
            resp.raise_for_status()
            data = resp.json()
            return "".join(b.get("text", "") for b in data.get("content", []))
        if provider == "openai":
            resp = httpx.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {key}", "content-type": "application/json"},
                json={"model": model or "gpt-4o-mini",
                      "messages": [{"role": "user", "content": prompt}],
                      "response_format": {"type": "json_object"}},
                timeout=30.0,
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
        raise ValueError(f"unsupported LLM provider: {provider}")

    return _client


def build_extractor() -> ResilientExtractor:
    try:
        return ResilientExtractor(llm_client=_make_llm_client())
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("LLM client init failed; using rule-based extractor: %s", exc)
        return ResilientExtractor(llm_client=None)
