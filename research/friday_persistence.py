"""FRIDAY persistence adapters: Supabase and a temporary Git repository JSON store.

The repository store is intended only for low-volume testing; no trading actions
are performed. Secrets are read only from environment variables.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Any, Mapping

import requests
import pandas as pd

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
        self.url = (url or os.getenv("FRIDAY_SUPABASE_URL") or os.getenv("SUPABASE_URL", "")).rstrip("/")
        self.key = key or os.getenv("FRIDAY_SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
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

    def get_archive(self, record_key: str) -> dict[str, Any] | None:
        """Read one durable archive record by its stable key."""
        rows = self._request("GET", ARCHIVE_TABLE, params={
            "select": "*", "record_key": f"eq.{record_key}", "limit": "1",
        })
        return rows[0] if rows else None

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
            # A "sending" row may represent a Telegram request that succeeded
            # just before the worker crashed. Never reclaim it automatically:
            # duplicate notifications are worse than a manually reconciled stale row.
            if existing[0].get("status") in {"sent", "sending"}:
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
                     run_id: str, strategy_version: str = "friday-price-action-v2") -> dict[str, Any]:
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
        "trade_events_seen": 0,
        "live_events_persisted": 0,
        "historical_replay_events_ignored": 0,
        "error": error,
    })



def make_archive_record(*, record_key: str, record_type: str, pair: str,
                        timeframe: str, signal_time: str | None,
                        payload: Mapping[str, Any],
                        strategy_version: str = "friday-price-action-v2") -> dict[str, Any]:
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


class UpstashFridayStore:
    """Upstash Redis REST persistence. Secrets are read only from environment variables."""

    def __init__(self, url: str | None = None, token: str | None = None, timeout: int = 8):
        self.url = (url or os.getenv("UPSTASH_REDIS_REST_URL", "")).rstrip("/")
        self.token = token or os.getenv("UPSTASH_REDIS_REST_TOKEN", "")
        self.timeout = timeout

    @property
    def configured(self) -> bool:
        return bool(self.url and self.token)

    def _command(self, *parts):
        if not self.configured:
            raise RuntimeError("Friday Upstash persistence is not configured")
        response = requests.post(
            self.url,
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
            json=list(parts), timeout=self.timeout,
        )
        response.raise_for_status()
        body = response.json()
        if body.get("error"):
            raise RuntimeError(f"Upstash Redis command failed: {body['error']}")
        return body.get("result")

    @staticmethod
    def _json(value):
        return json.dumps(value, separators=(",", ":"), allow_nan=False)

    def _recent_json(self, key: str) -> list[dict[str, Any]]:
        values = self._command("LRANGE", key, "0", "-1") or []
        rows = []
        for value in values:
            try:
                row = json.loads(value)
                if isinstance(row, dict):
                    rows.append(row)
            except (TypeError, json.JSONDecodeError):
                continue
        return rows

    def _upsert_recent(self, index: str, record: Mapping[str, Any], identity: str, limit: int):
        row = dict(record)
        rows = [x for x in self._recent_json(index) if x.get(identity) != row.get(identity)]
        rows.insert(0, row)
        rows = rows[:limit]
        self._command("DEL", index)
        if rows:
            self._command("RPUSH", index, *[self._json(x) for x in rows])
        return rows

    def record_trade_event(self, record: Mapping[str, Any]) -> dict[str, Any]:
        self._upsert_recent("FRIDAY:trades", record, "event_key", 50)
        return {"persisted": True, "table": "FRIDAY:trades"}

    def recent_50_trades(self) -> list[dict[str, Any]]:
        return self._recent_json("FRIDAY:trades")[:50]

    def record_cron_run(self, record: Mapping[str, Any]) -> dict[str, Any]:
        self._upsert_recent("FRIDAY:runs", record, "run_id", 500)
        return {"persisted": True, "table": "FRIDAY:runs"}

    def record_archive(self, record: Mapping[str, Any]) -> dict[str, Any]:
        row = dict(record)
        key = f"FRIDAY:archive:{row['record_key']}"
        self._command("SET", key, self._json(row))
        self._upsert_recent("FRIDAY:archive_index", {
            "record_key": row["record_key"], "record_type": row.get("record_type"),
            "pair": row.get("pair"), "timeframe": row.get("timeframe"),
            "signal_time": row.get("signal_time"),
        }, "record_key", 1000)
        return {"persisted": True, "table": "FRIDAY:archive"}

    def recent_archive(self, record_type: str | None = None, limit: int = 12) -> list[dict[str, Any]]:
        index = self._recent_json("FRIDAY:archive_index")
        rows = []
        for item in index:
            if record_type and item.get("record_type") != record_type:
                continue
            stored = self._command("GET", f"FRIDAY:archive:{item['record_key']}")
            if stored:
                rows.append(json.loads(stored))
            if len(rows) >= limit:
                break
        return rows

    def claim_alert(self, *, alert_key: str, pair: str, alert_type: str,
                    signal_time: str | None) -> bool:
        row = {"alert_key": alert_key, "pair": pair, "alert_type": alert_type,
               "signal_time": signal_time, "status": "sending", "updated_at": utc_now()}
        result = self._command("SET", f"FRIDAY:alert:{alert_key}", self._json(row), "NX", "EX", "604800")
        if result == "OK":
            self._command("LPUSH", "FRIDAY:alerts", self._json(row))
            self._command("LTRIM", "FRIDAY:alerts", "0", "9")
            return True
        return False

    def finish_alert(self, alert_key: str, *, status: str,
                     message_id: int | None = None, error: str | None = None) -> None:
        key = f"FRIDAY:alert:{alert_key}"
        existing = self._command("GET", key)
        row = json.loads(existing) if existing else {"alert_key": alert_key}
        row.update({"status": status, "telegram_message_id": message_id,
                    "error": error, "updated_at": utc_now()})
        self._command("SET", key, self._json(row), "EX", "604800")
        alerts = self._recent_json("FRIDAY:alerts")
        alerts = [x for x in alerts if x.get("alert_key") != alert_key]
        alerts.insert(0, row)
        self._command("DEL", "FRIDAY:alerts")
        if alerts:
            self._command("RPUSH", "FRIDAY:alerts", *[self._json(x) for x in alerts[:10]])

    def recent_alerts(self, limit: int = 10) -> list[dict[str, Any]]:
        # Keep a compact alert index for dashboard visibility; alert claims remain atomic.
        return self._recent_json("FRIDAY:alerts")[:limit]



class RepoFolderFridayStore:
    """Temporary JSON-file store committed by GitHub Actions for low-volume testing."""
    def __init__(self, root: str | None = None):
        from pathlib import Path
        self.root = Path(root or os.getenv("FRIDAY_REPO_DATA_DIR", "testing/friday/data"))
        self.root.mkdir(parents=True, exist_ok=True)

    @property
    def configured(self) -> bool:
        return True

    def _read(self, name: str) -> list[dict[str, Any]]:
        path = self.root / name
        if not path.exists():
            return []
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, list) else []
        except (OSError, json.JSONDecodeError):
            return []

    def _write(self, name: str, rows: list[dict[str, Any]]) -> None:
        path = self.root / name
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(_json_safe(rows), indent=2, allow_nan=False) + "\n", encoding="utf-8")
        temporary.replace(path)

    def _refresh_dashboard(self) -> None:
        cutoff = pd.Timestamp(os.getenv("FRIDAY_TRACKING_START_UTC", "2026-10-23T17:00:00+00:00"))
        cutoff = cutoff.tz_localize("UTC") if cutoff.tzinfo is None else cutoff.tz_convert("UTC")

        def post_launch(value: Any) -> bool:
            if not value:
                return False
            try:
                timestamp = pd.Timestamp(value)
                timestamp = timestamp.tz_localize("UTC") if timestamp.tzinfo is None else timestamp.tz_convert("UTC")
                return timestamp >= cutoff
            except (TypeError, ValueError, OverflowError):
                return False

        snapshots = []
        for record in self._read("archive.json"):
            if record.get("record_type") != "decision_snapshot":
                continue
            if not str(record.get("pair", "")).upper().endswith("USDT"):
                continue
            if not post_launch(record.get("signal_time")):
                continue
            snapshots.append(record)
            if len(snapshots) == 25:
                break

        events = [
            record for record in self._read("events.json")
            if str(record.get("pair", "")).upper().endswith("USDT")
            and post_launch(record.get("continuation_add_time") or record.get("signal_time"))
        ][:50]

        runs = []
        for record in self._read("runs.json"):
            pair_results = record.get("pair_results") or []
            # Do not surface pre-cleanup run records whose event counts included
            # historical replay; the original records remain in runs.json for audit.
            if "historical_replay_events_ignored" not in record:
                continue
            if not post_launch(record.get("started_at")) or not pair_results:
                continue
            if not all(str(result.get("pair", "")).upper().endswith("USDT") for result in pair_results):
                continue
            runs.append(record)
            if len(runs) == 10:
                break

        alerts = [
            record for record in self._read("alerts.json")
            if str(record.get("pair", "")).upper().endswith("USDT")
            and post_launch(record.get("signal_time"))
        ][:10]
        paper_trades = [
            record for record in self._read("paper_trades.json")
            if str(record.get("pair", "")).upper().endswith("USDT")
            and post_launch(record.get("signal_time") or record.get("opened_at") or record.get("created_at"))
        ][:100]

        payload = {
            "connected": True, "status": "connected",
            "message": "Connected to FRIDAY's temporary Git repository research store; only post-launch USDT research is shown.",
            "storage_backend": "github_repository_folder",
            "snapshots": snapshots, "events": events, "runs": runs,
            "alerts": alerts, "paper_trades": paper_trades,
            "fetched_at": utc_now(),
        }
        target = self.root / "dashboard.json"
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(_json_safe(payload), indent=2, allow_nan=False) + "\n", encoding="utf-8")
        temporary.replace(target)

    def record_trade_event(self, record: Mapping[str, Any]) -> dict[str, Any]:
        # Historical candles are useful for replay/backtests but are not trades
        # first observed by FRIDAY. Only add lifecycle events at/after launch.
        if not str(record.get("pair", "")).upper().endswith("USDT"):
            return {"persisted": False, "skipped": "non_usdt_pair", "table": "events.json"}
        cutoff = pd.Timestamp(os.getenv("FRIDAY_TRACKING_START_UTC", "2026-10-23T17:00:00+00:00"))
        signal_time = record.get("continuation_add_time") or record.get("signal_time")
        if signal_time:
            event_time = pd.Timestamp(signal_time)
            if event_time.tzinfo is None:
                event_time = event_time.tz_localize("UTC")
            else:
                event_time = event_time.tz_convert("UTC")
            cutoff = cutoff.tz_localize("UTC") if cutoff.tzinfo is None else cutoff.tz_convert("UTC")
            if event_time < cutoff:
                return {"persisted": False, "skipped": "historical_backfill_before_tracking_start", "table": "events.json"}
        rows = [r for r in self._read("events.json") if r.get("event_key") != record.get("event_key")]
        rows.insert(0, dict(record))
        self._write("events.json", rows[:50])
        self._refresh_dashboard()
        return {"persisted": True, "table": "events.json"}

    def recent_50_trades(self) -> list[dict[str, Any]]:
        return self._read("events.json")[:50]

    def record_archive(self, record: Mapping[str, Any]) -> dict[str, Any]:
        rows = [r for r in self._read("archive.json") if r.get("record_key") != record.get("record_key")]
        rows.insert(0, dict(record))
        self._write("archive.json", rows[:1000])
        self._refresh_dashboard()
        return {"persisted": True, "table": "archive.json"}

    def record_cron_run(self, record: Mapping[str, Any]) -> dict[str, Any]:
        rows = [r for r in self._read("runs.json") if r.get("run_id") != record.get("run_id")]
        rows.insert(0, dict(record))
        self._write("runs.json", rows[:500])
        self._refresh_dashboard()
        return {"persisted": True, "table": "runs.json"}

    def paper_trades(self) -> list[dict[str, Any]]:
        return self._read("paper_trades.json")

    def upsert_paper_trade(self, record: Mapping[str, Any]) -> dict[str, Any]:
        rows = [r for r in self._read("paper_trades.json") if r.get("trade_id") != record.get("trade_id")]
        rows.insert(0, dict(record))
        self._write("paper_trades.json", rows[:1000])
        self._refresh_dashboard()
        return {"persisted": True, "table": "paper_trades.json"}

    def claim_alert(self, *, alert_key: str, pair: str, alert_type: str, signal_time: str | None) -> bool:
        rows = self._read("alerts.json")
        existing = next((r for r in rows if r.get("alert_key") == alert_key), None)
        if existing and existing.get("status") in {"sent", "sending"}:
            # A sending row may mean Telegram accepted the message before a crash.
            # Fail closed to avoid duplicate notifications on the next scan.
            return False
        if existing:
            existing.update({"status": "sending", "error": None, "updated_at": utc_now()})
        else:
            rows.insert(0, {"alert_key": alert_key, "pair": pair, "alert_type": alert_type,
                            "signal_time": signal_time, "status": "sending", "updated_at": utc_now()})
        self._write("alerts.json", rows[:5000])
        self._refresh_dashboard()
        return True

    def finish_alert(self, alert_key: str, *, status: str, message_id: int | None = None, error: str | None = None) -> None:
        rows = self._read("alerts.json")
        for row in rows:
            if row.get("alert_key") == alert_key:
                row.update({"status": status, "telegram_message_id": message_id, "error": error, "updated_at": utc_now()})
        # Keep the durable audit history; the dashboard projection independently
        # limits the visible alert list to the most recent ten records.
        self._write("alerts.json", rows[:5000])
        self._refresh_dashboard()


def create_friday_store():
    """Select the configured FRIDAY persistence backend."""
    backend = os.getenv("FRIDAY_STORAGE_BACKEND", "supabase").strip().lower()
    if backend in {"repo", "repository", "file"}:
        return RepoFolderFridayStore()
    if backend == "supabase":
        return SupabaseFridayStore()
    raise ValueError(f"Unsupported FRIDAY_STORAGE_BACKEND: {backend}")
