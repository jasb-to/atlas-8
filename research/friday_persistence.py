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

from research.signal_journal import _json_safe

TRADES_TABLE = "friday_trade_events"
RUNS_TABLE = "friday_cron_runs"
ALERTS_TABLE = "friday_alert_deliveries"
ARCHIVE_TABLE = "friday_research_archive"


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
        # Let the database assign created_at on insert; preserving it on upsert
        # keeps the 50-row retention window ordered by first observation.
        result = self._request(
            "POST", TRADES_TABLE, params={"on_conflict": "event_key"},
            payload=payload, prefer="resolution=merge-duplicates,return=representation",
        )
        return {"persisted": True, "table": TRADES_TABLE, "result": result}

    def record_archive(self, record: Mapping[str, Any]) -> dict[str, Any]:
        """Upsert a durable research snapshot; this table is not subject to last-50 pruning."""
        result = self._request(
            "POST", ARCHIVE_TABLE, params={"on_conflict": "record_key"},
            payload=dict(record), prefer="resolution=merge-duplicates,return=minimal",
        )
        return {"persisted": True, "table": ARCHIVE_TABLE, "result": result}

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

    def claim_alert(self, *, alert_key: str, pair: str, alert_type: str,
                    signal_time: str | None) -> bool:
        """Claim a notification once; completed sends are never sent twice."""
        existing = self._request("GET", ALERTS_TABLE, params={
            "select": "alert_key,status", "alert_key": f"eq.{alert_key}", "limit": "1"
        })
        if existing:
            if existing[0].get("status") == "sent":
                return False
            self._request("PATCH", ALERTS_TABLE,
                params={"alert_key": f"eq.{alert_key}"},
                payload={"status": "sending", "error": None, "updated_at": utc_now()},
                prefer="return=minimal")
            return True
        try:
            self._request("POST", ALERTS_TABLE, payload={
                "alert_key": alert_key, "pair": pair, "alert_type": alert_type,
                "signal_time": signal_time, "status": "sending",
            }, prefer="return=minimal")
            return True
        except requests.HTTPError as exc:
            # A concurrent run may have inserted the unique key between GET and POST.
            if exc.response is not None and exc.response.status_code == 409:
                existing = self._request("GET", ALERTS_TABLE, params={
                    "select": "alert_key,status", "alert_key": f"eq.{alert_key}", "limit": "1"
                })
                return bool(existing and existing[0].get("status") != "sent")
            raise

    def finish_alert(self, alert_key: str, *, status: str,
                     message_id: int | None = None, error: str | None = None) -> None:
        self._request("PATCH", ALERTS_TABLE, params={"alert_key": f"eq.{alert_key}"},
            payload={"status": status, "telegram_message_id": message_id,
                     "error": error, "updated_at": utc_now()}, prefer="return=minimal")


def make_trade_event(*, pair: str, timeframe: str, event: Mapping[str, Any],
                     run_id: str, strategy_version: str = "friday-price-action-v1") -> dict[str, Any]:
    """Normalize a detected event into an idempotent row."""
    return _json_safe({
        "event_key": event_key(pair, timeframe, event),
        "pair": pair.upper(),
        "timeframe": timeframe,
        "strategy_version": strategy_version,
        "event_type": event.get("event") or "UNKNOWN",
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
    })


def make_cron_run(*, run_id: str, status: str, pair_results: list[Mapping[str, Any]],
                  started_at: str, finished_at: str | None = None,
                  error: str | None = None) -> dict[str, Any]:
    return _json_safe({
        "run_id": run_id,
        "started_at": started_at,
        "finished_at": finished_at or utc_now(),
        "status": status,
        "pairs_checked": len(pair_results),
        "pair_results": list(pair_results),
        "error": error,
    })



def make_archive_record(*, record_key: str, record_type: str, pair: str,
                        timeframe: str, signal_time: str | None,
                        payload: Mapping[str, Any],
                        strategy_version: str = "friday-price-action-v1") -> dict[str, Any]:
    """Build a JSON-safe, idempotent long-horizon research archive row."""
    return _json_safe({
        "record_key": record_key,
        "record_type": record_type,
        "pair": pair.upper(),
        "timeframe": timeframe,
        "strategy_version": strategy_version,
        "signal_time": signal_time,
        "payload": dict(payload),
    })
