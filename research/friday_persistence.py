"""Durable Friday persistence via Supabase REST, with explicit fail-closed semantics.

No trading actions. Store signal lifecycle events, a bounded recent-event view,
and cron-run diagnostics. Secrets are read only from environment variables.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Any, Mapping

import requests

TRADES_TABLE = "friday_trade_events"
RUNS_TABLE = "friday_cron_runs"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def event_key(pair: str, timeframe: str, event: Mapping[str, Any]) -> str:
    stable = {
        "pair": pair.upper(),
        "timeframe": timeframe,
        "signal_time": event.get("signal_time") or event.get("continuation_add_time"),
        "direction": event.get("direction"),
        "line_kind": event.get("line_kind"),
        "anchor_start_time": event.get("anchor_start_time"),
        "anchor_end_time": event.get("anchor_end_time"),
        "event_type": event.get("event_add") or event.get("event"),
    }
    return hashlib.sha256(json.dumps(stable, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class SupabaseFridayStore:
    """Minimal REST store; requires a private server-side service-role key."""

    def __init__(self, url: str | None = None, key: str | None = None, timeout: int = 8):
        self.url = (url or os.getenv("SUPABASE_URL", "")).rstrip("/")
        self.key = key or os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
        self.timeout = timeout

    @property
    def configured(self) -> bool:
        return bool(self.url and self.key)

    def _request(self, method: str, table: str, *, params=None, payload=None, prefer=None):
        if not self.configured:
            raise RuntimeError("Friday Supabase persistence is not configured")
        headers = {
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json",
        }
        if prefer:
            headers["Prefer"] = prefer
        response = requests.request(
            method, f"{self.url}/rest/v1/{table}", headers=headers,
            params=params, json=payload, timeout=self.timeout,
        )
        response.raise_for_status()
        if not response.content:
            return None
        return response.json()

    def record_trade_event(self, record: Mapping[str, Any]) -> dict[str, Any]:
        payload = dict(record)
        payload.setdefault("created_at", utc_now())
        result = self._request(
            "POST", TRADES_TABLE, params={"on_conflict": "event_key"},
            payload=payload, prefer="resolution=merge-duplicates,return=representation",
        )
        return {"persisted": True, "table": TRADES_TABLE, "result": result}

    def recent_50_trades(self) -> list[dict[str, Any]]:
        result = self._request(
            "GET", TRADES_TABLE,
            params={"select": "*", "order": "created_at.desc", "limit": "50"},
        )
        return result or []

    def record_cron_run(self, record: Mapping[str, Any]) -> dict[str, Any]:
        payload = dict(record)
        payload.setdefault("started_at", utc_now())
        result = self._request("POST", RUNS_TABLE, payload=payload, prefer="return=representation")
        return {"persisted": True, "table": RUNS_TABLE, "result": result}


def make_trade_event(*, pair: str, timeframe: str, event: Mapping[str, Any],
                     run_id: str, strategy_version: str = "friday-price-action-v1") -> dict[str, Any]:
    """Normalize a detected event into an idempotent row."""
    return {
        "event_key": event_key(pair, timeframe, event),
        "pair": pair.upper(),
        "timeframe": timeframe,
        "strategy_version": strategy_version,
        "event_type": event.get("event_add") or event.get("event") or "UNKNOWN",
        "direction": str(event.get("direction", "")).upper() or None,
        "signal_time": event.get("signal_time") or event.get("continuation_add_time"),
        "entry_reference": event.get("continuation_add_close") or event.get("close_at_break"),
        "line_kind": event.get("line_kind"),
        "line_price": event.get("line_price_at_break"),
        "outcome": event.get("outcome", "pending"),
        "retest_confirmed": bool(event.get("retest_confirmed")),
        "continuation_add_time": event.get("continuation_add_time"),
        "continuation_add_close": event.get("continuation_add_close"),
        "failure_time": event.get("failure_time"),
        "run_id": run_id,
        "evidence": dict(event),
    }


def make_cron_run(*, run_id: str, status: str, pair_results: list[Mapping[str, Any]],
                  started_at: str, finished_at: str | None = None,
                  error: str | None = None) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "started_at": started_at,
        "finished_at": finished_at or utc_now(),
        "status": status,
        "pairs_checked": len(pair_results),
        "pair_results": list(pair_results),
        "error": error,
    }
