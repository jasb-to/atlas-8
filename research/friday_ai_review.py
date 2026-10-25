""""Optional direct-provider FRIDAY AI review (Groq primary, Gemini fallback).

AI is disabled by default. Provider calls run from the Python monitor directly;
there is no Vercel proxy dependency. Deterministic rules and risk gates remain
authoritative, and AI review never blocks candidate processing when disabled.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Mapping

import httpx

GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
_ALLOWED_ASSESSMENTS = {"supportive", "mixed", "caution", "insufficient_evidence"}


def _enabled() -> bool:
    return os.getenv("FRIDAY_AI_REVIEW_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}


def _prompt(decision: Mapping[str, Any]) -> str:
    return (
        "You are a trading research reviewer. Review this paper-trading setup. "
        "Return strict JSON only with keys assessment, priority_candidate, "
        "supporting_evidence, counter_evidence, missing_evidence, rationale. "
        "assessment must be supportive, mixed, caution, or insufficient_evidence. "
        "Evidence fields must be arrays of short strings; rationale must be concise. "
        "Do not override deterministic risk rules.\nSetup: "
        + json.dumps(dict(decision), default=str)[:7000]
    )


def _validate(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("assessment") not in _ALLOWED_ASSESSMENTS:
        raise ValueError("AI provider returned an invalid assessment schema")
    for field in ("supporting_evidence", "counter_evidence", "missing_evidence"):
        if not isinstance(value.get(field), list):
            value[field] = []
    value.setdefault("priority_candidate", None)
    value.setdefault("rationale", "")
    return value


def _groq(prompt: str, *, client: httpx.Client) -> dict[str, Any]:
    key = os.getenv("GROQ_API_KEY")
    if not key:
        raise RuntimeError("GROQ_API_KEY missing")
    response = client.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={
            "model": GROQ_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
        },
        timeout=25,
    )
    response.raise_for_status()
    return _validate(json.loads(response.json()["choices"][0]["message"]["content"]))


def _gemini(prompt: str, *, client: httpx.Client) -> dict[str, Any]:
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY missing")
    response = client.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent",
        params={"key": key},
        json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.1, "responseMimeType": "application/json"},
        },
        timeout=25,
    )
    response.raise_for_status()
    text = response.json()["candidates"][0]["content"]["parts"][0]["text"]
    return _validate(json.loads(text))


def review_decision(
    decision: Mapping[str, Any],
    *,
    timeout: int = 25,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Call Groq directly, then Gemini on failure; never raise to the monitor."""
    if not _enabled():
        return {
            "status": "disabled", "provider": None, "model_id": None,
            "response": None, "latency_ms": 0, "error": None,
            "reason": "FRIDAY_AI_REVIEW_ENABLED is false; deterministic rules remain authoritative.",
        }

    started = time.monotonic()
    errors: list[str] = []
    owned_client = client is None
    http = client or httpx.Client(timeout=timeout)
    try:
        for name, model, call in (
            ("groq", GROQ_MODEL, _groq),
            ("gemini", GEMINI_MODEL, _gemini),
        ):
            try:
                response = call(_prompt(decision), client=http)
                return {
                    "status": "ok", "provider": name, "model_id": model,
                    "response": response,
                    "latency_ms": round((time.monotonic() - started) * 1000),
                    "error": None,
                }
            except Exception as exc:
                errors.append(f"{name}: {type(exc).__name__}: {str(exc)[:180]}")
        return {
            "status": "error", "provider": None, "model_id": None,
            "response": None, "latency_ms": round((time.monotonic() - started) * 1000),
            "error_category": "all_providers_failed",
            "error": "; ".join(errors)[:500],
        }
    finally:
        if owned_client:
            http.close()
