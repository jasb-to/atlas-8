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


def _display_pair(pair: str) -> str:
    """Use BTC for user-facing labels while retaining Kraken's XBT code internally."""
    value = str(pair).strip().upper()
    return "BTCUSDT" if value in {"XBTUSDT", "XBTUSD", "BTCUSD"} else value


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


def _ai_candidate_pass(
    review: dict | None,
    expected_strategy: str,
    expected_direction: str | None = None,
) -> tuple[bool, str]:
    """Use the AI review as a directional confirmation, not a string-name mismatch gate.

    The reviewer returns a direction such as "short" in priority_candidate, not
    necessarily the internal strategy ID. "Mixed" is allowed only when it explicitly
    selects the expected direction; caution/rejection/insufficient evidence remain blocked.
    """
    if not isinstance(review, dict) or review.get("status") != "ok":
        return False, "ai_review_not_completed"
    response = review.get("response") or {}
    assessment = str(response.get("assessment", "missing")).lower()
    if assessment not in {"supportive", "mixed"}:
        return False, f"ai_assessment_{assessment}"
    priority = response.get("priority_candidate")
    if not isinstance(priority, str):
        return False, "ai_priority_candidate_missing"
    priority_normalized = priority.strip().lower()
    priority_direction = priority_normalized.rsplit(".", 1)[-1]
    direction_normalized = str(expected_direction or "").strip().lower()
    strategy_selected = expected_strategy.lower() in priority_normalized
    direction_selected = bool(direction_normalized) and priority_direction == direction_normalized
    if not (strategy_selected or direction_selected):
        return False, "ai_did_not_select_expected_strategy_or_direction"
    if assessment == "mixed":
        return True, "ai_selected_direction_with_caveats"
    return True, "ai_selected_supportive"


def _is_live_lifecycle_event(pair: str, event: dict, tracking_start: str | None = None) -> bool:
    """Only count/persist post-launch events from the configured USDT market universe."""
    if not str(pair).upper().endswith("USDT"):
        return False
    signal_time = event.get("continuation_add_time") or event.get("signal_time")
    if not signal_time:
        return False
    cutoff = _utc(tracking_start or os.getenv("FRIDAY_TRACKING_START_UTC", "2026-10-23T17:00:00+00:00"))
    return _utc(signal_time) >= cutoff


def run_monitor(pairs: list[str], output_dir: str) -> dict:
    run_id = f"friday-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
    started_at = utc_now()
    store = create_friday_store()
    pair_results = []
    failures = []
    all_rows = []
    historical_rows_ignored = 0
    tracking_cutoff = _utc(os.getenv("FRIDAY_TRACKING_START_UTC", "2026-10-23T17:00:00+00:00"))
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
                ai_pass, ai_reason = _ai_candidate_pass(result.get("model_review"), "friday-price-action-v2", event.get("direction"))
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
                live_event = _is_live_lifecycle_event(pair, normalized, tracking_cutoff.isoformat())
                if live_event:
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
                else:
                    historical_rows_ignored += 1

                fresh_entry1 = event.get("event") == "BREAK_CLOSE_PROBE" and signal_ts == latest and signal_ts is not None and signal_ts >= tracking_cutoff
                fresh_entry2 = event.get("event_add") == "CONTINUATION_ADD_CONFIRMED" and add_ts == latest and add_ts is not None and add_ts >= tracking_cutoff
                # Make the lifecycle/notification distinction explicit. A break found
                # on an older candle must not be sent later as if it were a fresh entry.
                latest_setup = result.get("latest_setup_state", {}) or {}
                latest_setup_time = _utc(latest_setup["signal_time"]) if latest_setup.get("signal_time") else None
                if (
                    event.get("event") == "BREAK_CLOSE_PROBE"
                    and signal_ts is not None and signal_ts >= tracking_cutoff
                    and not fresh_entry1
                    and latest_setup_time == signal_ts
                    and str(latest_setup.get("state", "")).upper() == "RETEST_HELD_WAITING_CONTINUATION"
                ) and not any(
                    item.get("type") == "ENTRY_1"
                    and item.get("status") == "not_sent_break_no_longer_fresh"
                    and item.get("signal_time") == signal_ts.isoformat()
                    for item in row["alert_results"]
                ):
                    row["alert_results"].append({
                        "type": "ENTRY_1", "sent": False,
                        "status": "not_sent_break_no_longer_fresh",
                        "signal_time": signal_ts.isoformat(),
                        "reason": (
                            f"Break candle {signal_ts.isoformat()} is older than the latest completed candle "
                            f"{latest.isoformat()}; FRIDAY only sends Entry 1 alerts on the break candle, "
                            "so it will not send a delayed entry. Retest is held and continuation is still pending."
                        ),
                    })
                if fresh_entry1 or fresh_entry2:
                    passed, filter_blockers = _entry_filters_pass(event, result.get("reversal_context", {}))
                    ai_pass, ai_reason = _ai_candidate_pass(result.get("model_review"), "friday-price-action-v2")
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
                    message = build_signal_alert(alert_event, pair=_display_pair(pair), timeframe="4H")
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
            reversal_ts = _utc(reversal["signal_time"]) if reversal.get("signal_time") else None
            reversal_is_live = (
                reversal_ts is not None and reversal_ts >= tracking_cutoff
                and pair.upper().endswith("USDT")
            )
            ai_pass, ai_reason = _ai_candidate_pass(result.get("model_review"), "stoch-rsi-structural-reversal-v1", reversal.get("direction"))
            if reversal.get("eligible") and reversal_is_live and reversal_ts == latest and ai_pass and store.configured and hasattr(store, "paper_trades"):
                register_paper_trade(
                    store, pair=pair, strategy="friday-stoch-rsi-structural-reversal-v1",
                    signal_time=pd.Timestamp(reversal["signal_time"]).isoformat(),
                    direction=str(reversal["direction"]).lower(), entry=float(reversal["entry"]),
                    stop=float(reversal["stop"]), target=float(reversal["target"]),
                    reward_risk=float(reversal["reward_risk"]),
                    ai_review=result.get("model_review"),
                )
            if reversal.get("eligible") and reversal_ts == latest and reversal_is_live:
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
                                    build_momentum_reversal_alert(reversal, pair=_display_pair(pair), timeframe="4H")
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
        # Market scanning, persistence, and the run ledger must continue when the
        # optional AI reviewer is temporarily unavailable. Candidate promotion and
        # alerts remain blocked by _ai_candidate_pass unless a valid review exists.
        # This is a successful scan with a clearly degraded AI status, not an AI pass.
        if ai_statuses and any(value != "ok" for value in ai_statuses):
            status = "scan_complete_ai_unavailable"
    run_record = make_cron_run(
        run_id=run_id, status=status, pair_results=pair_results, started_at=started_at,
        error="; ".join(failures) if failures else None,
    )
    run_record["trade_events_seen"] = len(all_rows)
    run_record["live_events_persisted"] = len(all_rows) if store.configured else 0
    run_record["historical_replay_events_ignored"] = historical_rows_ignored
    run_record["persistence_configured"] = store.configured
    (out / "cron_run.json").write_text(json.dumps(run_record, indent=2, allow_nan=False))
    # Database logging must not be silently reported as successful if missing.
    if store.configured:
        store.record_cron_run(run_record)
    print(json.dumps(run_record, indent=2, allow_nan=False))
    return run_record


def validate_friday_pairs(pairs: list[str]) -> list[str]:
    """Canonicalize only known legacy quote aliases; never scan a USD market."""
    legacy_aliases = {
        "XBTUSD": "XBTUSDT", "BTCUSD": "XBTUSDT", "BTCUSDT": "XBTUSDT",
        "ETHUSD": "ETHUSDT", "SOLUSD": "SOLUSDT",
        "XRPUSD": "XRPUSDT", "ADAUSD": "ADAUSDT",
        "AVAXUSD": "AVAXUSDT", "BNBUSD": "BNBUSDT",
        "VIRTUALUSD": "VIRTUALUSDT", "HYPEUSD": "HYPEUSDT",
        "DOGEUSD": "DOGEUSDT", "LINKUSD": "LINKUSDT",
        "ZECUSD": "ZECUSDT", "NEARUSD": "NEARUSDT",
        "SUIUSD": "SUIUSDT", "LITUSD": "LITUSDT",
    }
    normalized = []
    for raw_pair in pairs:
        pair = str(raw_pair).strip().upper()
        if not pair:
            continue
        pair = legacy_aliases.get(pair, pair)
        if pair not in {
            "XBTUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT", "AVAXUSDT", "BNBUSDT",
            "VIRTUALUSDT", "HYPEUSDT", "DOGEUSDT", "LINKUSDT", "ZECUSDT", "NEARUSDT",
            "SUIUSDT", "LITUSDT",
        }:
            raise ValueError(
                "FRIDAY research scans only the approved USDT universe; rejected: " + pair
            )
        if pair not in normalized:
            normalized.append(pair)
    if not normalized:
        raise ValueError("At least one approved USDT-quoted pair is required")
    return normalized


def _monitor_exit_code(status: str) -> int:
    """Return success when scanning/persistence completed, even if AI is degraded."""
    return 0 if status in {"success", "scan_complete_ai_unavailable"} else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pairs", default="XBTUSDT,ETHUSDT,SOLUSDT,XRPUSDT,ADAUSDT,AVAXUSDT,BNBUSDT,VIRTUALUSDT,HYPEUSDT,DOGEUSDT,LINKUSDT,ZECUSDT,NEARUSDT,SUIUSDT,LITUSDT")
    parser.add_argument("--output-dir", default="results/friday-monitor")
    args = parser.parse_args()
    try:
        pairs = validate_friday_pairs(args.pairs.split(","))
    except ValueError as exc:
        parser.error(str(exc))
    result = run_monitor(pairs, args.output_dir)
    return _monitor_exit_code(result["status"])


if __name__ == "__main__":
    sys.exit(main())
