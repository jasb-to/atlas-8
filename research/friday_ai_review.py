"""Optional FRIDAY AI reviewer proxy; no direct provider calls.

AI review is disabled by default. If explicitly enabled, the GitHub Actions
runner exchanges its short-lived OIDC identity for a Vercel-side review. The
Vercel endpoint is configured to use Gemini/Groq only. Deterministic rules and
risk gates remain authoritative.
"""
from __future__ import annotations

import os
import time
from typing import Any, Mapping

import requests


def _enabled() -> bool:
    return os.getenv("FRIDAY_AI_REVIEW_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}


def review_decision(
    decision: Mapping[str, Any],
    *,
    token: str | None = None,
    model_id: str | None = None,
    endpoint: str | None = None,
    timeout: int = 12,
    session=requests,
) -> dict[str, Any]:
    """Review via the authenticated Gemini/Groq proxy only when explicitly enabled."""
    if not _enabled():
        return {
            "status": "disabled", "provider": None, "model_id": None,
            "response": None, "latency_ms": 0, "error": None,
            "reason": "FRIDAY_AI_REVIEW_ENABLED is false; deterministic rules remain authoritative.",
        }

    proxy_url = endpoint or os.getenv("FRIDAY_AI_REVIEW_URL")
    oidc_url = os.getenv("ACTIONS_ID_TOKEN_REQUEST_URL")
    oidc_request_token = os.getenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN")
    if not oidc_url or not oidc_request_token:
        return {
            "status": "not_configured", "provider": None, "model_id": None,
            "response": None, "latency_ms": None,
            "error": "AI review proxy requires a GitHub Actions OIDC token; direct provider calls are disabled.",
        }

    started = time.monotonic()
    try:
        separator = "&" if "?" in oidc_url else "?"
        oidc_response = session.get(
            f"{oidc_url}{separator}audience=friday-ai-review",
            headers={"Authorization": f"Bearer {oidc_request_token}"},
            timeout=timeout,
        )
        oidc_response.raise_for_status()
        oidc_token = oidc_response.json()["value"]
        proxy_response = session.post(
            proxy_url,
            headers={"Authorization": f"Bearer {oidc_token}", "Content-Type": "application/json"},
            json={"decision": dict(decision)},
            timeout=max(timeout, 20),
        )
        latency = round((time.monotonic() - started) * 1000)
        if not proxy_response.ok:
            try:
                error_body = proxy_response.json()
            except Exception:
                error_body = {}
            if not isinstance(error_body, dict):
                error_body = {}
            detail = error_body.get("error", error_body.get("message", proxy_response.text))
            return {
                "status": "error",
                "provider": error_body.get("provider"),
                "model_id": error_body.get("model_id"),
                "response": None,
                "latency_ms": error_body.get("latency_ms", latency),
                "error_category": error_body.get("error_category"),
                "error": f"AI review proxy HTTP {proxy_response.status_code}: {str(detail)[:180]}",
            }
        body = proxy_response.json()
        if not isinstance(body, dict) or body.get("status") != "ok":
            raise ValueError("AI review proxy returned an invalid review response")
        parsed = body.get("response")
        allowed = {"supportive", "mixed", "caution", "insufficient_evidence"}
        if not isinstance(parsed, dict) or parsed.get("assessment") not in allowed:
            raise ValueError("AI review proxy response did not match FRIDAY review schema")
        for field in ("supporting_evidence", "counter_evidence", "missing_evidence"):
            if not isinstance(parsed.get(field), list):
                raise ValueError(f"AI review proxy field {field} must be an array")
        return {
            "status": "ok", "provider": body.get("provider"),
            "model_id": body.get("model_id"), "response": parsed,
            "latency_ms": body.get("latency_ms", latency), "error": None,
        }
    except Exception as exc:
        return {
            "status": "error", "provider": None, "model_id": None,
            "response": None, "latency_ms": round((time.monotonic() - started) * 1000),
            "error": f"AI review proxy: {type(exc).__name__}: {str(exc)[:200]}",
        }
