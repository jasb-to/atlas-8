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
    create_friday_store, make_archive_record, make_cron_run, make_trade_event, utc_now,
)
from research.strategy_preview import build_preview
from research.friday_multitimeframe import analyse_pair
from research.friday_paper_trades import register_paper_trade, update_paper_trades


def _utc(value) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is None:
        return stamp.tz_localize("UTC")
    return stamp.tz_convert("UTC")


def _entry_filters_pass(event: dict, reversal: dict) -> tuple[bool, list[str]]:
    """Hard-gate new price-action entries on directional momentum and exhaustion."""
    direction = str(event.get("direction", "")).lower()
    momentum = event.get("momentum_context") or {}
    if direction == "long":
        momentum_ok = bool(momentum.get("stoch_bullish_cross") or momentum.get("macd_bullish_expansion"))
        aligned = int(reversal.get("long_reversal_score", 0) or 0)
        opposing = int(reversal.get("short_reversal_score", 0) or 0)
    elif direction == "short":
        momentum_ok = bool(momentum.get("stoch_bearish_cross") or momentum.get("macd_bearish_expansion"))
        aligned = int(reversal.get("short_reversal_score", 0) or 0)
        opposing = int(reversal.get("long_reversal_score", 0) or 0)
    else:
        return False, ["direction_missing"]
    blockers = []
    if not momentum_ok:
        blockers.append("directional_momentum_filter_failed")
    if opposing >= 2 and opposing > aligned:
        blockers.append("opposing_exhaustion_filter_blocked_entry")
    return not blockers, blockers


def _ai_candidate_pass(review: dict | None, expected_strategy: str) -> tuple[bool, str]:
    """Require AI to positively select this supplied strategy before paper promotion."""
    if not isinstance(review, dict) or review.get("status") != "ok":
        return False, "ai_review_not_completed"
    response = review.get("response") or {}
    if response.get("assessment") != "supportive":
        return False, f"ai_assessment_{response.get('assessment', 'missing')}"
    priority = response.get("priority_candidate")
    if not isinstance(priority, str) or expected_strategy not in priority:
        return False, "ai_did_not_select_this_strategy"
    return True, "ai_selected_supportive"


def run_monitor(pairs: list[str], output_dir: str) -> dict:
    run_id = f"friday-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
    started_at = utc_now()
    store = create_friday_store()
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
            # Daily thesis + 4H execution scan uses completed candles from Kraken.
            mtf = analyse_pair(pair=pair)
            (pair_dir / "multitimeframe_scan.json").write_text(json.dumps(mtf, indent=2, allow_nan=False))
            latest = _utc(result["last_completed_candle_utc"])
            candles = pd.DataFrame(result.get("closed_candles", []))
            paper_updates = update_paper_trades(store, pair=pair, candles=candles) if store.configured and hasattr(store, "paper_trades") else []
            row = {
                "pair": pair, "status": "ok", "last_completed_candle_utc": latest.isoformat(),
                "last_close": result["last_close"], "latest_setup_state": result["latest_setup_state"].get("state", "UNKNOWN"),
                "friday_decision": result["friday_decision"].get("state"),
                "multitimeframe_state": mtf.get("state"),
                "multitimeframe_direction": mtf.get("thesis_direction"),
                "ai_review_status": result.get("model_review", {}).get("status", "missing"),
                "ai_model_id": result.get("model_review", {}).get("model_id"),
                "ai_review_assessment": (result.get("model_review", {}).get("response") or {}).get("assessment"),
                "reversal_context": result.get("reversal_context", {}).get("state", "unavailable"),
                "break_event_count": result["event_count"],
                "persistence": "configured" if store.configured else "not_configured",
                "alert_results": [],
            }
            if store.configured:
                decision_key = hashlib.sha256(f"{pair}|4H|friday-price-action-v2|{latest.isoformat()}|decision".encode()).hexdigest()
                store.record_archive(make_archive_record(
                    record_key=decision_key, record_type="decision_snapshot", pair=pair,
                    timeframe="4H", signal_time=latest.isoformat(),
                    payload={
                        "latest_setup_state": result.get("latest_setup_state"),
                        "friday_decision": result.get("friday_decision"),
                        "reversal_context": result.get("reversal_context"),
                        "current_indicators": result.get("current_indicators"),
                        "structure_lifecycle": result.get("structure_lifecycle"),
                        "model_review": result.get("model_review"),
                        "momentum_reversal": result.get("momentum_reversal"),
                        "multitimeframe_scan": mtf,
                    },
                ))
            # Persist the cross-timeframe thesis independently of 4H lifecycle events.
            if store.configured:
                mtf_key = hashlib.sha256(f"{pair}|1D-4H|{mtf.get('generated_at_utc')}|thesis".encode()).hexdigest()
                store.record_archive(make_archive_record(
                    record_key=mtf_key, record_type="multitimeframe_thesis", pair=pair,
                    timeframe="1D+4H", signal_time=mtf.get("four_hour_execution", {}).get("last_closed_candle_utc"),
                    payload=mtf,
                ))
            # Register fresh price-action entries only when directional momentum
            # and exhaustion gates pass and a structural plan clears at least 1.5R.
            reversal_context_now = result.get("reversal_context", {})
            for event in result.get("break_events", []):
                event_time = _utc(event["signal_time"]) if event.get("signal_time") else None
                add_time = _utc(event["continuation_add_time"]) if event.get("continuation_add_time") else None
                is_fresh_entry = (event.get("event") == "BREAK_CLOSE_PROBE" and event_time == latest) or (
                    event.get("event_add") == "CONTINUATION_ADD_CONFIRMED" and add_time == latest
                )
                if not is_fresh_entry or not store.configured or not hasattr(store, "paper_trades"):
                    continue
                passed, filter_blockers = _entry_filters_pass(event, reversal_context_now)
                ai_pass, ai_reason = _ai_candidate_pass(result.get("model_review"), "friday-price-action-v2")
                if not ai_pass:
                    row["alert_results"].append({"type": "PAPER_ENTRY", "sent": False, "status": "ai_review_blocked", "reason": ai_reason})
                    continue
                if not passed:
                    row["alert_results"].append({"type": "PAPER_ENTRY", "sent": False, "status": "filtered", "reason": ", ".join(filter_blockers)})
                    continue
                direction = str(event.get("direction", "")).lower()
                plan = (result.get("trade_plans") or {}).get(direction, {})
                if not plan.get("eligible"):
                    row["alert_results"].append({"type": "PAPER_ENTRY", "sent": False, "status": "blocked_trade_plan", "reason": ", ".join(plan.get("blockers", []))})
                    continue
                signal_time = event.get("continuation_add_time") if event.get("event_add") == "CONTINUATION_ADD_CONFIRMED" else event.get("signal_time")
                entry_price = float(event.get("continuation_add_close") or event.get("close_at_break") or result["last_close"])
                stop, target = float(plan["stop"]), float(plan["target"])
                risk = abs(entry_price - stop)
                reward = (target - entry_price) if direction == "long" else (entry_price - target)
                rr = reward / risk if risk > 0 else 0
                geometry_ok = (stop < entry_price < target) if direction == "long" else (target < entry_price < stop)
                if not geometry_ok or rr < 1.5:
                    row["alert_results"].append({"type": "PAPER_ENTRY", "sent": False, "status": "blocked_trade_plan", "reason": "entry-adjusted structural plan failed geometry or 1.5R minimum"})
                    continue
                register_paper_trade(
                    store, pair=pair, strategy="friday-price-action-v2",
                    signal_time=pd.Timestamp(signal_time).isoformat(), direction=direction,
                    entry=entry_price, stop=stop, target=target, reward_risk=rr,
                    ai_review=result.get("model_review"),
                )
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
                    lifecycle_state_key = hashlib.sha256(
                        f"{row_event['event_key']}|{normalized.get('outcome', 'pending')}|{normalized.get('event_add', '')}".encode()
                    ).hexdigest()
                    store.record_archive(make_archive_record(
                        record_key=lifecycle_state_key, record_type="setup_lifecycle",
                        pair=pair, timeframe="4H", signal_time=row_event.get("signal_time"),
                        payload=normalized,
                    ))

                fresh_entry1 = event.get("event") == "BREAK_CLOSE_PROBE" and signal_ts == latest
                fresh_entry2 = event.get("event_add") == "CONTINUATION_ADD_CONFIRMED" and add_ts == latest
                if fresh_entry1 or fresh_entry2:
                    passed, filter_blockers = _entry_filters_pass(event, result.get("reversal_context", {}))
                    ai_pass, ai_reason = _ai_candidate_pass(result.get("model_review"))
                    if not ai_pass:
                        row["alert_results"].append({"type": "ENTRY_2" if fresh_entry2 else "ENTRY_1", "sent": False, "status": "ai_review_blocked", "reason": ai_reason})
                        continue
                    direction = str(event.get("direction", "")).lower()
                    plan = (result.get("trade_plans") or {}).get(direction, {})
                    entry_ref = float(event.get("continuation_add_close") or event.get("close_at_break") or result["last_close"])
                    stop = plan.get("stop")
                    target = plan.get("target")
                    if not passed or not plan.get("eligible") or stop is None or target is None:
                        reasons = filter_blockers + list(plan.get("blockers", []))
                        row["alert_results"].append({
                            "type": "ENTRY_2" if fresh_entry2 else "ENTRY_1", "sent": False,
                            "status": "filtered_or_no_valid_plan",
                            "reason": ", ".join(reasons) or "structural trade plan is not eligible",
                        })
                        continue
                    risk = abs(entry_ref - float(stop))
                    reward = float(target) - entry_ref if direction == "long" else entry_ref - float(target)
                    rr = reward / risk if risk > 0 else 0
                    geometry_ok = (float(stop) < entry_ref < float(target)) if direction == "long" else (float(target) < entry_ref < float(stop))
                    if not geometry_ok or rr < 1.5:
                        row["alert_results"].append({
                            "type": "ENTRY_2" if fresh_entry2 else "ENTRY_1", "sent": False,
                            "status": "filtered_or_no_valid_plan",
                            "reason": "structural plan failed entry geometry or minimum 1.5R",
                        })
                        continue
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
                    alert_event["paper_stop"] = float(stop)
                    alert_event["paper_target"] = float(target)
                    alert_event["paper_reward_risk"] = round(rr, 3)
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
            # Separate Stoch RSI reversal archetype: notify only when a fresh
            # completed-candle candidate also has a valid structural risk plan.
            reversal = result.get("momentum_reversal", {})
            ai_pass, ai_reason = _ai_candidate_pass(result.get("model_review"), "stoch-rsi-structural-reversal-v1")
            if reversal.get("eligible") and reversal.get("signal_time") and ai_pass and store.configured and hasattr(store, "paper_trades"):
                register_paper_trade(
                    store, pair=pair, strategy="friday-stoch-rsi-structural-reversal-v1",
                    signal_time=pd.Timestamp(reversal["signal_time"]).isoformat(),
                    direction=str(reversal["direction"]).lower(), entry=float(reversal["entry"]),
                    stop=float(reversal["stop"]), target=float(reversal["target"]),
                    reward_risk=float(reversal["reward_risk"]),
                    ai_review=result.get("model_review"),
                )
            reversal_ts = _utc(reversal["signal_time"]) if reversal.get("signal_time") else None
            if reversal.get("eligible") and reversal_ts == latest:
                if not ai_pass:
                    row["alert_results"].append({"type": "MOMENTUM_REVERSAL", "sent": False, "status": "ai_review_blocked", "reason": ai_reason})
                reversal_event = {
                    "event": "MOMENTUM_REVERSAL_CANDIDATE",
                    "signal_time": reversal.get("signal_time"),
                    "direction": reversal.get("direction"),
                    "line_kind": "momentum_reversal",
                    "anchor_start_time": reversal.get("signal_time"),
                    "anchor_end_time": reversal.get("signal_time"),
                    "close_at_break": reversal.get("entry"),
                    "outcome": "candidate",
                    "retest_confirmed": False,
                    "evidence": reversal,
                }
                reversal_row = make_trade_event(
                    pair=pair, timeframe="4H", event=reversal_event, run_id=run_id,
                    strategy_version="friday-stoch-rsi-structural-reversal-v1",
                )
                all_rows.append(reversal_row)
                if store.configured:
                    store.record_trade_event(reversal_row)
                    store.record_archive(make_archive_record(
                        record_key=reversal_row["event_key"] + ":plan",
                        record_type="momentum_reversal_candidate", pair=pair,
                        timeframe="4H", signal_time=reversal.get("signal_time"),
                        payload=reversal,
                        strategy_version="friday-stoch-rsi-structural-reversal-v1",
                    ))
                    if ai_pass:
                        alert_key = reversal_row["event_key"] + ":telegram:REVERSAL:" + str(reversal.get("signal_time"))
                        claimed = store.claim_alert(
                            alert_key=alert_key, pair=pair, alert_type="MOMENTUM_REVERSAL",
                            signal_time=reversal.get("signal_time"),
                        )
                        if claimed:
                            from research.friday_alerts import build_momentum_reversal_alert
                            try:
                                alert = send_telegram_alert(
                                    build_momentum_reversal_alert(reversal, pair=pair, timeframe="4H")
                                )
                                if alert.get("sent"):
                                    store.finish_alert(alert_key, status="sent", message_id=alert.get("message_id"))
                                else:
                                    store.finish_alert(alert_key, status="failed", error=alert.get("reason"))
                                row["alert_results"].append({
                                    "type": "MOMENTUM_REVERSAL", "sent": alert.get("sent", False),
                                    "status": alert.get("status"), "reason": alert.get("reason"),
                                })
                            except Exception as alert_exc:
                                store.finish_alert(alert_key, status="failed", error=f"{type(alert_exc).__name__}: {alert_exc}")
                                raise
                        else:
                            row["alert_results"].append({"type": "MOMENTUM_REVERSAL", "sent": False, "status": "duplicate_suppressed"})
                else:
                    row["alert_results"].append({"type": "MOMENTUM_REVERSAL", "sent": False, "status": "persistence_not_configured"})
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
    elif not failures:
        ai_statuses = [row.get("ai_review_status", "missing") for row in pair_results if row.get("status") == "ok"]
        if ai_statuses and all(value == "not_configured" for value in ai_statuses):
            status = "ai_not_configured"
        elif any(value != "ok" for value in ai_statuses):
            status = "ai_review_failure"
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
    parser.add_argument("--pairs", default="XBTUSDT,ETHUSDT,SOLUSDT,VIRTUALUSDT,HYPEUSDT")
    parser.add_argument("--output-dir", default="results/friday-monitor")
    args = parser.parse_args()
    result = run_monitor([x.strip().upper() for x in args.pairs.split(",") if x.strip()], args.output_dir)
    return 0 if result["status"] == "success" else 1


if __name__ == "__main__":
    sys.exit(main())
