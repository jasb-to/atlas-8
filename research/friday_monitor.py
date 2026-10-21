"""Run Friday's research monitor for configured markets; never submits exchange orders."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from research.friday_alerts import build_signal_alert, send_telegram_alert
from research.friday_persistence import (
    SupabaseFridayStore, make_archive_record, make_cron_run, make_trade_event, utc_now,
)
from research.strategy_preview import build_preview


def _utc(value) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is None:
        return stamp.tz_localize("UTC")
    return stamp.tz_convert("UTC")


def run_monitor(pairs: list[str], output_dir: str) -> dict:
    run_id = f"friday-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
    started_at = utc_now()
    store = SupabaseFridayStore()
    pair_results = []
    failures = []
    all_rows = []
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    for pair in pairs:
        pair_started = utc_now()
        try:
            pair_dir = out / pair.lower()
            pair_dir.mkdir(parents=True, exist_ok=True)
            result = build_preview(pair=pair, interval=240, output_dir=str(pair_dir))
            latest = _utc(result["last_completed_candle_utc"])
            row = {
                "pair": pair, "status": "ok", "last_completed_candle_utc": latest.isoformat(),
                "last_close": result["last_close"], "latest_setup_state": result["latest_setup_state"].get("state", "UNKNOWN"),
                "friday_decision": result["friday_decision"].get("state"),
                "break_event_count": result["event_count"],
                "persistence": "configured" if store.configured else "not_configured",
                "alert_results": [],
            }
            if store.configured:
                decision_key = hashlib.sha256(f"{pair}|4H|friday-price-action-v1|{latest.isoformat()}|decision".encode()).hexdigest()
                store.record_archive(make_archive_record(
                    record_key=decision_key, record_type="decision_snapshot", pair=pair,
                    timeframe="4H", signal_time=latest.isoformat(),
                    payload={
                        "latest_setup_state": result.get("latest_setup_state"),
                        "friday_decision": result.get("friday_decision"),
                        "current_indicators": result.get("current_indicators"),
                        "structure_lifecycle": result.get("structure_lifecycle"),
                        "model_review": result.get("model_review"),
                    },
                ))
            # Persist the lifecycle ledger idempotently. Only alert events that
            # first become actionable on the latest closed candle.
            for event in result.get("break_events", []):
                normalized = dict(event)
                signal_ts = _utc(event["signal_time"]) if event.get("signal_time") else None
                add_ts = _utc(event["continuation_add_time"]) if event.get("continuation_add_time") else None
                row_event = make_trade_event(pair=pair, timeframe="4H", event=normalized, run_id=run_id)
                all_rows.append(row_event)
                if store.configured:
                    store.record_trade_event(row_event)
                    store.record_archive(make_archive_record(
                        record_key=row_event["event_key"], record_type="setup_lifecycle",
                        pair=pair, timeframe="4H", signal_time=row_event.get("signal_time"),
                        payload=normalized,
                    ))

                fresh_entry1 = event.get("event") == "BREAK_CLOSE_PROBE" and signal_ts == latest
                fresh_entry2 = event.get("event_add") == "CONTINUATION_ADD_CONFIRMED" and add_ts == latest
                if fresh_entry1 or fresh_entry2:
                    alert_type = "ENTRY_2" if fresh_entry2 else "ENTRY_1"
                    if not store.configured:
                        row["alert_results"].append({
                            "type": alert_type, "sent": False, "status": "persistence_not_configured",
                            "reason": "Persistent deduplication is required before alerts are sent.",
                        })
                        continue
                    alert_timestamp = event.get("continuation_add_time") if fresh_entry2 else event.get("signal_time")
                    alert_key = row_event["event_key"] + f":telegram:{alert_type}:{alert_timestamp}"
                    claimed = store.claim_alert(
                        alert_key=alert_key, pair=pair, alert_type=alert_type,
                        signal_time=(event.get("continuation_add_time") if fresh_entry2 else event.get("signal_time")),
                    )
                    if not claimed:
                        row["alert_results"].append({
                            "type": alert_type, "sent": False, "status": "duplicate_suppressed",
                        })
                        continue
                    alert_event = dict(event)
                    if fresh_entry2:
                        alert_event["event_add"] = "CONTINUATION_ADD_CONFIRMED"
                        alert_event["continuation_add_time"] = event["continuation_add_time"]
                    message = build_signal_alert(alert_event, pair=pair, timeframe="4H")
                    try:
                        alert = send_telegram_alert(message)
                        if alert.get("sent"):
                            store.finish_alert(alert_key, status="sent", message_id=alert.get("message_id"))
                        else:
                            store.finish_alert(alert_key, status="failed", error=alert.get("reason"))
                        row["alert_results"].append({
                            "type": alert_type, "sent": alert.get("sent", False), "status": alert.get("status"),
                            "reason": alert.get("reason"),
                        })
                    except Exception as alert_exc:
                        store.finish_alert(alert_key, status="failed", error=f"{type(alert_exc).__name__}: {alert_exc}")
                        raise
            (pair_dir / "monitor_summary.json").write_text(json.dumps(row, indent=2, allow_nan=False))
            pair_results.append(row)
        except Exception as exc:
            failures.append(f"{pair}: {type(exc).__name__}: {exc}")
            pair_results.append({
                "pair": pair, "status": "error", "started_at": pair_started,
                "error": f"{type(exc).__name__}: {exc}",
            })

    status = "success" if not failures else ("partial_failure" if len(failures) < len(pairs) else "failure")
    if not store.configured:
        status = "persistence_not_configured" if not failures else "partial_failure"
    run_record = make_cron_run(
        run_id=run_id, status=status, pair_results=pair_results, started_at=started_at,
        error="; ".join(failures) if failures else None,
    )
    run_record["trade_events_seen"] = len(all_rows)
    run_record["persistence_configured"] = store.configured
    (out / "cron_run.json").write_text(json.dumps(run_record, indent=2, allow_nan=False))
    # Database logging must not be silently reported as successful if missing.
    if store.configured:
        store.record_cron_run(run_record)
    print(json.dumps(run_record, indent=2, allow_nan=False))
    return run_record


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pairs", default="XBTUSD,VIRTUALUSD")
    parser.add_argument("--output-dir", default="results/friday-monitor")
    args = parser.parse_args()
    result = run_monitor([x.strip().upper() for x in args.pairs.split(",") if x.strip()], args.output_dir)
    return 0 if result["status"] == "success" else 1


if __name__ == "__main__":
    sys.exit(main())
