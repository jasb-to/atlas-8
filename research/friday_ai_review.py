"""Gemini/Groq-backed AI reviewer for FRIDAY's frozen rule evidence.

The reviewer is advisory only. Deterministic strategy and risk gates remain authoritative.
"""
from __future__ import annotations

import os
import time
from typing import Any, Mapping

import requests


def review_decision(
    decision: Mapping[str, Any],
    *,
    timeout: int = 12,
    session=requests,
) -> dict[str, Any]:
    proxy_url = os.getenv("FRIDAY_AI_REVIEW_URL") or "https://atlas8-friday.vercel.app/api/friday/ai-review"
    oidc_url = os.getenv("ACTIONS_ID_TOKEN_REQUEST_URL")
    oidc_request_token = os.getenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN")
    if not oidc_url or not oidc_request_token:
        return {
            "status": "not_configured", "provider": None, "model_id": None,
            "response": None, "latency_ms": None,
            "error": "AI review proxy requires a GitHub Actions OIDC token",
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
        elapsed = round((time.monotonic() - started) * 1000)
        try:
            body = proxy_response.json()
        except Exception:
            body = {}
        if not proxy_response.ok:
            detail = body.get("error", body.get("message", proxy_response.text)) if isinstance(body, dict) else proxy_response.text
            return {
                "status": "error",
                "provider": body.get("provider") if isinstance(body, dict) else None,
                "model_id": body.get("model_id") if isinstance(body, dict) else None,
                "response": None,
                "latency_ms": body.get("latency_ms", elapsed) if isinstance(body, dict) else elapsed,
                "error_category": body.get("error_category") if isinstance(body, dict) else None,
                "error": f"AI review proxy HTTP {proxy_response.status_code}: {str(detail)[:180]}",
            }
        if not isinstance(body, dict) or body.get("status") != "ok":
            raise ValueError("AI review proxy returned an invalid response")
        parsed = body.get("response")
        allowed = {"supportive", "mixed", "caution", "insufficient_evidence"}
        if not isinstance(parsed, dict) or parsed.get("assessment") not in allowed:
            raise ValueError("AI review response did not match Friday review schema")
        for field in ("supporting_evidence", "counter_evidence", "missing_evidence"):
            if not isinstance(parsed.get(field), list):
                raise ValueError(f"AI review response field {field} must be an array")
        return {
            "status": "ok", "provider": body.get("provider"), "model_id": body.get("model_id"),
            "response": parsed, "latency_ms": body.get("latency_ms", elapsed), "error": None,
        }
    except Exception as exc:
        return {
            "status": "error", "provider": None, "model_id": None,
            "response": None, "latency_ms": round((time.monotonic() - started) * 1000),
            "error": f"AI review proxy: {type(exc).__name__}: {str(exc)[:200]}",
        }
