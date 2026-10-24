"""Hugging Face candidate reviewer for FRIDAY's frozen rule evidence.

AI may prioritize or block promotion of a rule-generated candidate, but it cannot
invent a trade, alter deterministic prices, or place orders.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Mapping

import requests

DEFAULT_ENDPOINT = "https://router.huggingface.co/v1/chat/completions"
DEFAULT_MODEL_ID = "openai/gpt-oss-120b:cheapest"


def review_decision(
    decision: Mapping[str, Any],
    *,
    token: str | None = None,
    model_id: str | None = None,
    endpoint: str | None = None,
    timeout: int = 12,
    session=requests,
) -> dict[str, Any]:
    api_token = token or os.getenv("HUGGINGFACE_API_KEY") or os.getenv("HF_TOKEN")
    model = model_id or os.getenv("FRIDAY_HF_MODEL_ID") or DEFAULT_MODEL_ID
    # In GitHub Actions, the Hugging Face key deliberately stays in Vercel.
    # Exchange the runner's short-lived OIDC identity for a server-side Vercel review.
    if not api_token:
        proxy_url = os.getenv("FRIDAY_AI_REVIEW_URL") or "https://atlas8-friday.vercel.app/api/friday/ai-review"
        oidc_url = os.getenv("ACTIONS_ID_TOKEN_REQUEST_URL")
        oidc_request_token = os.getenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN")
        if not oidc_url or not oidc_request_token:
            return {
                "status": "not_configured", "provider": "huggingface", "model_id": model,
                "response": None, "latency_ms": None,
                "error": "Vercel review proxy requires a GitHub Actions OIDC token",
            }
        started = time.monotonic()
        try:
            separator = "&" if "?" in oidc_url else "?"
            oidc_response = session.get(
                f"{oidc_url}{separator}audience=friday-huggingface-review",
                headers={"Authorization": f"Bearer {oidc_request_token}"},
                timeout=timeout,
            )
            oidc_response.raise_for_status()
            oidc_token = oidc_response.json()["value"]
            proxy_response = session.post(
                proxy_url,
                headers={"Authorization": f"Bearer {oidc_token}", "Content-Type": "application/json"},
                json={"decision": dict(decision), "model_id": model},
                timeout=max(timeout, 20),
            )
            if not proxy_response.ok:
                try:
                    error_body = proxy_response.json()
                    detail = error_body.get("error", error_body.get("message", error_body))
                except Exception:
                    detail = proxy_response.text
                raise RuntimeError(
                    f"Vercel review proxy HTTP {proxy_response.status_code}: {str(detail)[:180]}"
                )
            proxy_body = proxy_response.json()
            if not isinstance(proxy_body, dict) or proxy_body.get("status") != "ok":
                raise ValueError("Vercel review proxy returned an invalid review response")
            parsed = proxy_body.get("response")
            allowed = {"supportive", "mixed", "caution", "insufficient_evidence"}
            if not isinstance(parsed, dict) or parsed.get("assessment") not in allowed:
                raise ValueError("Vercel review proxy response did not match Friday review schema")
            for field in ("supporting_evidence", "counter_evidence", "missing_evidence"):
                if not isinstance(parsed.get(field), list):
                    raise ValueError(f"Vercel review proxy field {field} must be an array")
            return {
                "status": "ok", "provider": proxy_body.get("provider", "huggingface"),
                "model_id": proxy_body.get("model_id", model),
                "response": parsed,
                "latency_ms": proxy_body.get("latency_ms", round((time.monotonic() - started) * 1000)),
                "error": None,
            }
        except Exception as exc:
            return {
                "status": "error", "provider": "huggingface", "model_id": model,
                "response": None, "latency_ms": round((time.monotonic() - started) * 1000),
                "error": f"Vercel review proxy: {type(exc).__name__}: {str(exc)[:200]}",
            }
    prompt = {
        "task": "Review and rank the supplied causal crypto trade candidates as an independent research analyst. This is AI-assisted discovery/ranking over a deterministic candidate universe, not free-form price prediction.",
        "rules": [
            "Use only the supplied evidence; do not infer missing chart data.",
            "Do not invent prices, indicators, probabilities, or future outcomes.",
            "Do not invent candidates, prices, direction, stops, targets, or future outcomes. Only discuss candidates explicitly present in candidate_universe or frozen_decision.",
            "Evaluate candidate_universe independently from rule_decision. A NO_TRADE rule_decision does not automatically disqualify a separate candidate that is explicitly marked eligible in candidate_universe.",
            "Identify which supplied candidate deserves the most attention and why, using its supplied strategy and signal time in priority_candidate. If no eligible candidate exists, use null.",
            "Treat deterministic eligibility, directional momentum, exhaustion filters, stop/target geometry, and >=1.5R as hard gates.",
            "A supportive AI opinion cannot promote a blocked candidate or bypass any hard gate.",
            "Identify missing evidence, contradictory evidence, and likely false-break risks.",
            "Return a JSON object with keys: assessment (one of supportive, mixed, caution, insufficient_evidence), priority_candidate (string identifying the supplied candidate or null), "
            "confidence (low, medium, high; qualitative only), supporting_evidence (array), "
            "counter_evidence (array), missing_evidence (array), rationale (string).",
            "This is shadow research, not financial advice and not an execution instruction.",
        ],
        "frozen_decision": dict(decision),
    }
    started = time.monotonic()
    try:
        response = session.post(
            endpoint or os.getenv("FRIDAY_HF_ENDPOINT") or DEFAULT_ENDPOINT,
            headers={"Authorization": f"Bearer {api_token}", "Content-Type": "application/json"},
            json={
                "model": model,
                "temperature": 0,
                "messages": [
                    {"role": "system", "content": "You are Friday's cautious, evidence-bound research reviewer. Output JSON only."},
                    {"role": "user", "content": json.dumps(prompt, separators=(",", ":"), allow_nan=False)},
                ],
            },
            timeout=timeout,
        )
        latency = round((time.monotonic() - started) * 1000)
        response.raise_for_status()
        body = response.json()
        text = body["choices"][0]["message"]["content"]
        if isinstance(text, list):
            text = "".join(str(part.get("text", "")) for part in text if isinstance(part, dict))
        parsed = json.loads(text)
        allowed = {"supportive", "mixed", "caution", "insufficient_evidence"}
        if not isinstance(parsed, dict) or parsed.get("assessment") not in allowed:
            raise ValueError("model response did not match Friday review schema")
        for field in ("supporting_evidence", "counter_evidence", "missing_evidence"):
            if not isinstance(parsed.get(field), list):
                raise ValueError(f"model response field {field} must be an array")
        if parsed.get("confidence") not in {"low", "medium", "high"}:
            parsed["confidence"] = "low"
        return {"status": "ok", "provider": "huggingface", "model_id": model,
                "response": parsed, "latency_ms": latency, "error": None}
    except Exception as exc:
        return {"status": "error", "provider": "huggingface", "model_id": model,
                "response": None, "latency_ms": round((time.monotonic() - started) * 1000),
                "error": f"{type(exc).__name__}: {str(exc)[:240]}"}
