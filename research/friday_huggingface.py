"""Optional Hugging Face shadow reviewer for Friday's frozen rule evidence.

Advisory only: it cannot create or veto signals, alter stops/targets, or place orders.
Set HUGGINGFACE_API_KEY to enable; the model and endpoint have safe defaults.
No secrets are logged.
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
    if not api_token:
        return {
            "status": "not_configured", "provider": "huggingface", "model_id": model,
            "response": None, "latency_ms": None, "error": None,
        }
    prompt = {
        "task": "Review this causal crypto setup as an independent research analyst.",
        "rules": [
            "Use only the supplied evidence; do not infer missing chart data.",
            "Do not invent prices, indicators, probabilities, or future outcomes.",
            "Do not change the deterministic rule engine's state, direction, stop, or targets.",
            "Identify missing evidence, contradictory evidence, and likely false-break risks.",
            "Return a JSON object with keys: assessment (one of supportive, mixed, caution, insufficient_evidence), "
            "confidence (low, medium, high; qualitative only), supporting_evidence (array), "
            "counter_evidence (array), missing_evidence (array), rationale (string).",
            "This is shadow research, not financial advice and not an execution instruction.",
        ],
        "frozen_decision": dict(decision),
    }
    started = time.monotonic()
    try:
        response = session.post(
            endpoint or os.getenv("FRIDAY_HF_ENDPOINT", DEFAULT_ENDPOINT),
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
