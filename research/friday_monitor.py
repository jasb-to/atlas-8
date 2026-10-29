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
from research.weekly_thesis import evaluate_sheldon_thesis
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


def _entry_filters_pass(
    event: dict,
    reversal: dict,
    weekly_direction: str | None = None,
) -> tuple[bool, list[str]]:
    """Require daily bias to agree with the entry and reject strong opposing exhaustion."""
    direction = str(event.get("direction", "")).strip().lower()
    if direction not in {"long", "short"}:
        return False, ["direction_missing"]

    thesis_direction = str(weekly_direction or "").strip().lower()
    if thesis_direction in {"long", "short"} and direction != thesis_direction:
        return False, ["direction_conflicts_with_weekly_thesis"]

    daily_bias = str(event.get("daily_bias", "")).strip().lower()
    if daily_bias not in {"bullish", "bearish"}:
        return False, ["daily_bias_unavailable"]
    expected_bias = "bullish" if direction == "long" else "bearish"
    if daily_bias != expected_bias:
        return False, ["direction_conflicts_with_daily_bias"]

    long_score = reversal.get("long_reversal_score", 0) if isinstance(reversal, dict) else 0
    short_score = reversal.get("short_reversal_score", 0) if isinstance(reversal, dict) else 0
    try:
        long_score, short_score = float(long_score or 0), float(short_score or 0)
    except (TypeError, ValueError):
        return False, ["invalid_reversal_scores"]
    opposing_score = short_score if direction == "long" else long_score
    aligned_score = long_score if direction == "long" else short_score
    if opposing_score >= 3 and opposing_score > aligned_score:
        return False, ["opposing_exhaustion_filter_blocked_entry"]
    return True, []


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
    if os.getenv("FRIDAY_AI_REVIEW_ENABLED", "false").strip().lower() not in {"1", "true", "yes", "on"}:
        return True, "ai_review_disabled_advisory_only"
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


def _scan_4h_if_active(thesis: dict, scanner, **kwargs):
    """Invoke the injected 4H scanner only when the weekly thesis is active."""
    if not _should_scan_4h(thesis):
        return None
    return scanner(**kwargs)


def _trade_plan_metrics(direction: str, entry: float, stop: float, target: float) -> tuple[bool, float]:
    """Validate directional stop/target geometry and return (valid, reward/risk)."""
    direction = str(direction).strip().lower()
    risk = abs(float(entry) - float(stop))
    reward = float(target) - float(entry) if direction == "long" else float(entry) - float(target)
    rr = reward / risk if risk > 0 else 0.0
    geometry_ok = (float(stop) < float(entry) < float(target)) if direction == "long" else (
        float(target) < float(entry) < float(stop)
    ) if direction == "short" else False
    return bool(geometry_ok and rr >= 1.5), rr


def _should_scan_4h(thesis: dict) -> bool:
    """The 4H scanner runs only for an active higher-timeframe thesis."""
    return str(thesis.get("status", "unavailable")).strip().lower() == "active"


def _run_dry_run(pairs: list[str]) -> dict:
    """Read-only smoke path: evaluate weekly/daily thesis and print only."""
    results = []
    for index, pair in enumerate(pairs, start=1):
        print(f"[FRIDAY DRY-RUN {index}/{len(pairs)}] START pair={_display_pair(pair)}", flush=True)
        thesis = evaluate_sheldon_thesis(pair, as_of=pd.Timestamp.now(tz="UTC"))
        row = {
            "pair": pair, "status": thesis.get("status", "unavailable"),
            "direction": thesis.get("direction"), "macro_regime": thesis.get("macro_regime"),
            "wave_state": thesis.get("wave_state"), "reason": thesis.get("reason"),
            "dry_run": True, "side_effects": "disabled",
        }
        results.append(row)
        print(json.dumps(row, allow_nan=False), flush=True)
    summary = {"status": "success", "dry_run": True, "markets": len(results), "results": results}
    print("[FRIDAY DRY-RUN FINISHED]", flush=True)
    print(json.dumps(summary, indent=2, allow_nan=False), flush=True)
    return summary


def run_monitor(pairs: list[str], output_dir: str, *, dry_run: bool = False) -> dict:
    if dry_run:
        return _run_dry_run(pairs)
    run_id = os.getenv("FRIDAY_SCAN_RUN_ID", "").strip() or f"friday-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
    started_at = utc_now()
    store = create_friday_store()
    pair_results = []
    failures = []
    all_rows = []
    historical_rows_ignored = 0
    tracking_cutoff = _utc(os.getenv("FRIDAY_TRACKING_START_UTC", "2026-10-23T17:00:00+00:00"))
    momentum_reversal_enabled = os.getenv("FRIDAY_MOMENTUM_REVERSAL_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    print(
        f"[FRIDAY] RUN_START id={run_id} markets={len(pairs)} "
        f"pairs={','.join(_display_pair(pair).replace('USDT', '') for pair in pairs)} "
        f"started_at={started_at}",
        flush=True,
    )
    for index, pair in enumerate(pairs, start=1):
        pair_started = utc_now()
        display_pair = _display_pair(pair).replace("USDT", "")
        print(
            f"[FRIDAY MARKET {index}/{len(pairs)}] START pair={display_pair} "
            f"venue_symbol={pair} interval=4H thesis=1D+4H",
            flush=True,
        )
        row = None
        try:
            pair_dir = out / pair.lower()
            pair_dir.mkdir(parents=True, exist_ok=True)

            # HARD GATE: evaluate completed weekly/daily candles before invoking
            # any 4H scan. Inactive, vetoed, or unavailable thesis means no 4H work.
            try:
                weekly_thesis = evaluate_sheldon_thesis(pair, as_of=pd.Timestamp.now(tz="UTC"))
            except Exception as thesis_error:
                weekly_thesis = {
                    "pair": pair.upper(), "status": "unavailable", "direction": None,
                    "reason": f"weekly_thesis_evaluation_error: {type(thesis_error).__name__}: {thesis_error}",
                    "generated_at_utc": utc_now(),
                }
            thesis_status = str(weekly_thesis.get("status", "unavailable")).lower()
            if not _should_scan_4h(weekly_thesis):
                thesis_reason = str(weekly_thesis.get("reason") or f"Weekly thesis status is {thesis_status}.")
                row = {
                    "run_id": run_id, "pair": pair, "status": "weekly_inactive",
                    "market_started_at": pair_started, "market_finished_at": utc_now(),
                    "weekly_thesis": weekly_thesis, "weekly_thesis_status": thesis_status,
                    "latest_setup_state": "SKIPPED_WEEKLY_INACTIVE",
                    "friday_decision": "SKIPPED_WEEKLY_INACTIVE",
                    "scan_diagnostic": f"Weekly Inactive — 4H scan skipped. {thesis_reason}",
                    "ai_review_status": "not_run", "break_event_count": 0,
                    "persistence": "configured" if store.configured else "not_configured",
                    "alert_results": [{
                        "type": "SCAN", "sent": False, "status": "weekly_inactive",
                        "reason": thesis_reason,
                    }],
                }
                if store.configured:
                    thesis_key = hashlib.sha256(
                        f"{pair}|weekly_thesis|{weekly_thesis.get('generated_at_utc')}".encode()
                    ).hexdigest()
                    store.record_archive(make_archive_record(
                        record_key=thesis_key, record_type="weekly_thesis_snapshot",
                        pair=pair, timeframe="1W+1D",
                        signal_time=weekly_thesis.get("generated_at_utc"), payload=weekly_thesis,
                    ))
                row["duration_seconds"] = round(
                    (pd.Timestamp(row["market_finished_at"]) - pd.Timestamp(pair_started)).total_seconds(), 3
                )
                row["alert_outcomes_total"] = 1
                row["alert_outcomes_sent"] = 0
                row["alert_outcomes_blocked"] = 1
                (pair_dir / "monitor_summary.json").write_text(json.dumps(row, indent=2, allow_nan=False))
                pair_results.append(row)
                print(
                    f"[FRIDAY MARKET {index}/{len(pairs)}] WEEKLY INACTIVE pair={display_pair} "
                    f"status={thesis_status} action=SKIP_4H reason={thesis_reason}",
                    flush=True,
                )
                continue

            result = _scan_4h_if_active(weekly_thesis, build_preview, pair=pair, interval=240, output_dir=str(pair_dir))
            print(
                f"[FRIDAY MARKET {index}/{len(pairs)}] DATA_OK pair={display_pair} "
                f"close={result.get('last_close', 'n/a')} "
                f"last_closed_candle={result.get('last_completed_candle_utc', 'n/a')}",
                flush=True,
            )
            # Daily thesis + 4H execution scan uses completed candles from Kraken.
            mtf = analyse_pair(pair=pair)
            print(
                f"[FRIDAY MARKET {index}/{len(pairs)}] ANALYSIS_OK pair={display_pair} "
                f"setup={(result.get('latest_setup_state') or {}).get('state', 'UNKNOWN')} "
                f"decision={(result.get('friday_decision') or {}).get('state', 'UNKNOWN')} "
                f"daily_4h_state={mtf.get('state', 'UNKNOWN')} "
                f"direction={mtf.get('thesis_direction', 'none')} "
                f"ai_status={(result.get('model_review') or {}).get('status', 'missing')} "
                f"ai_assessment={((result.get('model_review') or {}).get('response') or {}).get('assessment', 'n/a')} "
                f"break_events={result.get('event_count', 0)}",
                flush=True,
            )
            (pair_dir / "multitimeframe_scan.json").write_text(json.dumps(mtf, indent=2, allow_nan=False))
            latest = _utc(result["last_completed_candle_utc"])
            # Persist the already-evaluated weekly/daily thesis alongside the 4H scan.
            if store.configured:
                thesis_key = hashlib.sha256(
                    f"{pair}|weekly_thesis|{weekly_thesis.get('generated_at_utc')}".encode()
                ).hexdigest()
                try:
                    store.record_archive(make_archive_record(
                        record_key=thesis_key, record_type="weekly_thesis_snapshot",
                        pair=pair, timeframe="1W+1D",
                        signal_time=weekly_thesis.get("generated_at_utc"), payload=weekly_thesis,
                    ))
                    print(
                        f"[FRIDAY MARKET {index}/{len(pairs)}] WEEKLY_THESIS_SAVED "
                        f"pair={display_pair} status=active "
                        f"direction={weekly_thesis.get('direction') or 'none'} "
                        f"reason={weekly_thesis.get('reason') or 'n/a'}",
                        flush=True,
                    )
                except Exception as thesis_store_error:
                    print(
                        f"[FRIDAY MARKET {index}/{len(pairs)}] WEEKLY_THESIS_SAVE_FAILED "
                        f"pair={display_pair} reason={type(thesis_store_error).__name__}: {thesis_store_error}",
                        flush=True,
                    )
            candles = pd.DataFrame(result.get("closed_candles", []))
            # Apply thesis invalidation before scanning later candles for stop/target,
            # so a failed structure cannot be retroactively treated as a live trade.
            # Close a paper thesis immediately when the original structural break
            # is confirmed failed, even if its wider stop has not been touched.
            if store.configured and hasattr(store, "paper_trades"):
                from research.friday_paper_trades import invalidate_paper_trade
                for failed_event in result.get("break_events", []):
                    if failed_event.get("outcome") == "failed_break":
                        invalidate_paper_trade(
                            store, pair=pair, signal_time=failed_event.get("signal_time"),
                            exit_time=failed_event.get("failure_time") or latest.isoformat(),
                            exit_price=float(failed_event.get("failure_close") or result["last_close"]),
                            reason="THESIS_INVALIDATED",
                        )
            paper_updates = update_paper_trades(store, pair=pair, candles=candles) if store.configured and hasattr(store, "paper_trades") else []
            # A retained thesis may become executable on a later closed 4H candle.
            # Keep the original break identity, but emit a distinct execution event
            # when a fresh stochastic cross aligns with the daily bias and all EMAs.
            monitor_events = list(result.get("break_events", []))
            execution = result.get("execution_trigger", {}) or {}
            latest_setup_for_execution = result.get("latest_setup_state", {}) or {}
            source_break = next((
                event for event in result.get("break_events", [])
                if event.get("signal_time") == latest_setup_for_execution.get("signal_time")
            ), None)
            if (
                execution.get("eligible")
                and str(latest_setup_for_execution.get("state", "")).upper() == "RETEST_HELD_WAITING_CONTINUATION"
                and source_break
                and source_break.get("outcome") == "retest_held_waiting_continuation"
                and str(source_break.get("direction", "")).lower() == str(execution.get("direction", "")).lower()
            ):
                monitor_events.append({
                    "event": "EXECUTION_TRIGGER_ENTRY_1",
                    "signal_time": execution.get("signal_time"),
                    "direction": execution.get("direction"),
                    "line_kind": source_break.get("line_kind"),
                    "line_price_at_break": source_break.get("line_price_at_break"),
                    "close_at_break": execution.get("close"),
                    "touch_count": source_break.get("touch_count"),
                    "daily_bias": execution.get("daily_bias"),
                    "ema_aligned": True,
                    "momentum_context": {
                        "stoch_bullish_cross": bool(execution.get("stoch_bullish_cross")),
                        "stoch_bearish_cross": bool(execution.get("stoch_bearish_cross")),
                        "macd_bullish_expansion": False,
                        "macd_bearish_expansion": False,
                    },
                    "anchor_start_time": source_break.get("anchor_start_time"),
                    "anchor_end_time": source_break.get("anchor_end_time"),
                    "original_break_signal_time": source_break.get("signal_time"),
                    "execution_trigger": execution,
                    "outcome": "execution_trigger_confirmed",
                    "retest_confirmed": True,
                    "research_only": True,
                })
            row = {
                "run_id": run_id,
                "pair": pair, "status": "ok",
                "market_started_at": pair_started,
                "last_completed_candle_utc": latest.isoformat(),
                "last_close": result["last_close"], "latest_setup_state": result["latest_setup_state"].get("state", "UNKNOWN"),
                "latest_setup_message": result["latest_setup_state"].get("message"),
                "friday_decision": result["friday_decision"].get("state"),
                "friday_decision_reason": result["friday_decision"].get("reason") or result["friday_decision"].get("message"),
                "scan_diagnostic": (
                    result["friday_decision"].get("reason")
                    or result["friday_decision"].get("message")
                    or result["latest_setup_state"].get("message")
                    or "No fresh actionable entry on the latest completed candle."
                ),
                "multitimeframe_state": mtf.get("state"),
                "multitimeframe_direction": mtf.get("thesis_direction"),
                "ai_review_status": result.get("model_review", {}).get("status", "missing"),
                "ai_provider": result.get("model_review", {}).get("provider"),
                "ai_review_latency_ms": result.get("model_review", {}).get("latency_ms"),
                "ai_review_error": result.get("model_review", {}).get("error"),
                "ai_review_error_category": result.get("model_review", {}).get("error_category"),
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
                        **({"momentum_reversal": result.get("momentum_reversal")} if momentum_reversal_enabled else {}),
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
            # Register a fresh 4H breakout when the weekly direction agrees and
            # valid structural stop/target geometry clears at least 1.5R.
            reversal_context_now = result.get("reversal_context", {})
            for event in monitor_events:
                event_time = _utc(event["signal_time"]) if event.get("signal_time") else None
                add_time = _utc(event["continuation_add_time"]) if event.get("continuation_add_time") else None
                is_fresh_entry = (event.get("event") in {"BREAK_CLOSE_PROBE", "EXECUTION_TRIGGER_ENTRY_1"} and event_time is not None and event_time >= latest - pd.Timedelta(hours=4)) or (
                    event.get("event_add") == "CONTINUATION_ADD_CONFIRMED" and add_time is not None and add_time >= latest - pd.Timedelta(hours=4)
                )
                if not is_fresh_entry or not store.configured or not hasattr(store, "paper_trades"):
                    continue
                passed, filter_blockers = _entry_filters_pass(event, reversal_context_now, weekly_thesis.get("direction"))
                # AI review is advisory only; it must never veto an aligned 4H breakout.
                if not passed:
                    row["alert_results"].append({"type": "PAPER_ENTRY", "sent": False, "status": "filtered", "reason": ", ".join(filter_blockers)})
                    continue
                direction = str(event.get("direction", "")).lower()
                plan = (result.get("trade_plans") or {}).get(direction, {})
                if plan.get("stop") is None or plan.get("target") is None:
                    row["alert_results"].append({"type": "PAPER_ENTRY", "sent": False, "status": "blocked_trade_plan", "reason": "missing structural stop or target"})
                    continue
                signal_time = event.get("continuation_add_time") if event.get("event_add") == "CONTINUATION_ADD_CONFIRMED" else event.get("signal_time")
                entry_price = float(event.get("continuation_add_close") or event.get("close_at_break") or result["last_close"])
                stop, target = float(plan["stop"]), float(plan["target"])
                plan_ok, rr = _trade_plan_metrics(direction, entry_price, stop, target)
                if not plan_ok:
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
            for event in monitor_events:
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

                fresh_entry1 = event.get("event") in {"BREAK_CLOSE_PROBE", "EXECUTION_TRIGGER_ENTRY_1"} and signal_ts is not None and signal_ts >= latest - pd.Timedelta(hours=4) and signal_ts >= tracking_cutoff
                fresh_entry2 = event.get("event_add") == "CONTINUATION_ADD_CONFIRMED" and add_ts is not None and add_ts >= latest - pd.Timedelta(hours=4) and add_ts >= tracking_cutoff
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
                latest_setup_add_time = _utc(latest_setup["add_signal_time"]) if latest_setup.get("add_signal_time") else None
                if (
                    event.get("event_add") == "CONTINUATION_ADD_CONFIRMED"
                    and add_ts is not None and add_ts >= tracking_cutoff
                    and not fresh_entry2
                    and latest_setup_add_time == add_ts
                    and str(latest_setup.get("state", "")).upper() == "CONTINUATION_ADD_CONFIRMED"
                ) and not any(
                    item.get("type") == "ENTRY_2"
                    and item.get("status") == "not_sent_continuation_no_longer_fresh"
                    and item.get("signal_time") == add_ts.isoformat()
                    for item in row["alert_results"]
                ):
                    row["alert_results"].append({
                        "type": "ENTRY_2", "sent": False,
                        "status": "not_sent_continuation_no_longer_fresh",
                        "signal_time": add_ts.isoformat(),
                        "reason": (
                            f"Continuation candle {add_ts.isoformat()} is older than the latest completed candle "
                            f"{latest.isoformat()}; FRIDAY does not send delayed Entry 2 alerts. "
                            "The historical continuation remains in the ledger, but is not a fresh entry."
                        ),
                    })
                if fresh_entry1 or fresh_entry2:
                    passed, filter_blockers = _entry_filters_pass(event, result.get("reversal_context", {}), weekly_thesis.get("direction"))
                    # AI review is advisory only; the HTF thesis and trade geometry govern entry.
                    direction = str(event.get("direction", "")).lower()
                    plan = (result.get("trade_plans") or {}).get(direction, {})
                    entry_ref = float(event.get("continuation_add_close") or event.get("close_at_break") or result["last_close"])
                    stop = plan.get("stop")
                    target = plan.get("target")
                    if not passed or stop is None or target is None:
                        reasons = filter_blockers + (["missing structural stop or target"] if stop is None or target is None else [])
                        row["alert_results"].append({
                            "type": "ENTRY_2" if fresh_entry2 else "ENTRY_1", "sent": False,
                            "status": "filtered_or_no_valid_plan",
                            "reason": ", ".join(reasons) or "structural trade plan is not eligible",
                        })
                        continue
                    plan_ok, rr = _trade_plan_metrics(direction, entry_ref, float(stop), float(target))
                    if not plan_ok:
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
                    alert_direction = str(weekly_thesis.get("direction") or "").lower()
                    daily_bias_for_alert = "bullish" if alert_direction == "long" else "bearish"
                    alert_event["daily_bias"] = daily_bias_for_alert
                    message = build_signal_alert(
                        alert_event, pair=_display_pair(pair), timeframe="4H",
                        weekly_thesis=weekly_thesis,
                        daily_context={"bias": daily_bias_for_alert},
                    )
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
                        row["alert_results"].append({
                            "type": alert_type, "sent": False, "status": "failed",
                            "reason": f"{type(alert_exc).__name__}: {alert_exc}",
                        })
                        raise
            if momentum_reversal_enabled:
                # Separate Stoch RSI reversal archetype: notify only when a fresh
                # completed-candle candidate also has a valid structural risk plan.
                reversal = result.get("momentum_reversal", {})
                reversal_ts = _utc(reversal["signal_time"]) if reversal.get("signal_time") else None
                reversal_is_live = (
                    reversal_ts is not None and reversal_ts >= tracking_cutoff
                    and pair.upper().endswith("USDT")
                )
                ai_pass, ai_reason = _ai_candidate_pass(result.get("model_review"), "stoch-rsi-structural-reversal-v1", reversal.get("direction"))
                if reversal.get("eligible") and reversal_is_live and reversal_ts == latest and store.configured and hasattr(store, "paper_trades"):
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
                                    row["alert_results"].append({
                                        "type": "MOMENTUM_REVERSAL", "sent": False, "status": "failed",
                                        "reason": f"{type(alert_exc).__name__}: {alert_exc}",
                                    })
                                    raise
                            else:
                                row["alert_results"].append({"type": "MOMENTUM_REVERSAL", "sent": False, "status": "duplicate_suppressed"})
                    else:
                        row["alert_results"].append({"type": "MOMENTUM_REVERSAL", "sent": False, "status": "persistence_not_configured"})
            # Ensure the most relevant lifecycle blocker is visible even if the
            # older event loop did not emit a notification outcome for this setup.
            latest_setup = result.get("latest_setup_state", {}) or {}
            latest_setup_state = str(latest_setup.get("state", "")).upper()
            latest_setup_time = _utc(latest_setup["signal_time"]) if latest_setup.get("signal_time") else None
            if latest_setup_state == "RETEST_HELD_WAITING_CONTINUATION" and latest_setup_time and latest_setup_time < latest:
                if not any(
                    item.get("type") == "ENTRY_1"
                    and item.get("status") == "not_sent_break_no_longer_fresh"
                    and item.get("signal_time") == latest_setup_time.isoformat()
                    for item in row["alert_results"]
                ):
                    row["alert_results"].append({
                        "type": "ENTRY_1",
                        "sent": False,
                        "status": "not_sent_break_no_longer_fresh",
                        "signal_time": latest_setup_time.isoformat(),
                        "reason": (
                            f"Break candle {latest_setup_time.isoformat()} is older than the latest completed candle "
                            f"{latest.isoformat()}; the retest is held but continuation has not been confirmed. "
                            "No delayed Entry 1 alert is sent; wait for a fresh continuation signal."
                        ),
                    })
            if not row["alert_results"]:
                row["alert_results"].append({
                    "type": "SCAN", "sent": False, "status": "no_actionable_signal",
                    "reason": (
                        "No fresh execution trigger passed thesis, momentum, structural-risk and AI gates. "
                        f"setup={latest_setup_state or 'unknown'}; "
                        f"execution_blockers={', '.join(execution.get('blockers', [])) or 'none'}"
                    ),
                })
            row["market_finished_at"] = utc_now()
            row["duration_seconds"] = round((pd.Timestamp(row["market_finished_at"]) - pd.Timestamp(pair_started)).total_seconds(), 3)
            row["alert_outcomes_total"] = len(row["alert_results"])
            row["alert_outcomes_sent"] = sum(1 for item in row["alert_results"] if item.get("sent") is True)
            row["alert_outcomes_blocked"] = sum(1 for item in row["alert_results"] if item.get("sent") is False)
            (pair_dir / "monitor_summary.json").write_text(json.dumps(row, indent=2, allow_nan=False))
            pair_results.append(row)
            alert_text = "; ".join(
                f"{item.get('type', 'candidate')}={item.get('status', 'unknown')}"
                + (f" reason={item['reason']}" if item.get("reason") else "")
                for item in row["alert_results"]
            ) or "no actionable candidate on latest completed candle"
            elapsed = (pd.Timestamp(utc_now()) - pd.Timestamp(pair_started)).total_seconds()
            print(
                f"[FRIDAY MARKET {index}/{len(pairs)}] COMPLETE pair={display_pair} "
                f"status=ok close={row['last_close']} candle={row['last_completed_candle_utc']} "
                f"setup={row['latest_setup_state']} decision={row['friday_decision']} "
                f"ai={row['ai_review_status']}/{row['ai_review_assessment'] or 'n/a'} "
                f"events={row['break_event_count']} outcome={alert_text} duration={elapsed:.1f}s",
                flush=True,
            )
        except Exception as exc:
            error_text = f"{type(exc).__name__}: {exc}"
            failed_at = utc_now()
            failures.append(f"{pair}: {error_text}")
            partial_alert_results = list((row or {}).get("alert_results") or [])
            failed_row = {
                "run_id": run_id, "pair": pair, "status": "error",
                "market_started_at": pair_started, "market_finished_at": failed_at,
                "duration_seconds": round((pd.Timestamp(failed_at) - pd.Timestamp(pair_started)).total_seconds(), 3),
                "error": error_text, "scan_diagnostic": error_text,
                "alert_results": partial_alert_results,
                "alert_outcomes_total": len(partial_alert_results),
                "alert_outcomes_sent": sum(1 for item in partial_alert_results if item.get("sent") is True),
                "alert_outcomes_blocked": sum(1 for item in partial_alert_results if item.get("sent") is False),
            }
            pair_results.append(failed_row)
            # Failed markets must have the same durable summary artifact as
            # successful markets, otherwise the workflow summary silently omits them.
            failure_dir = out / pair.lower()
            failure_dir.mkdir(parents=True, exist_ok=True)
            (failure_dir / "monitor_summary.json").write_text(json.dumps(failed_row, indent=2, allow_nan=False))
            print(
                f"[FRIDAY MARKET {index}/{len(pairs)}] FAILED pair={display_pair} "
                f"run_id={run_id} duration={failed_row['duration_seconds']:.1f}s error={error_text}",
                flush=True,
            )

    status = "success" if not failures else ("partial_failure" if len(failures) < len(pairs) else "failure")
    if not store.configured:
        status = "persistence_not_configured" if not failures else "partial_failure"
    elif not failures:
        ai_statuses = [
            str(row.get("ai_review_status", "missing")).strip().lower()
            for row in pair_results if row.get("status") == "ok"
        ]
        status = _ai_run_status(ai_statuses)
    run_record = make_cron_run(
        run_id=run_id, status=status, pair_results=pair_results, started_at=started_at,
        error="; ".join(failures) if failures else None,
    )
    run_record["trade_events_seen"] = len(all_rows)
    run_record["live_events_persisted"] = len(all_rows) if store.configured else 0
    run_record["historical_replay_events_ignored"] = historical_rows_ignored
    run_record["persistence_configured"] = store.configured
    run_record["markets_requested"] = len(pairs)
    run_record["markets_succeeded"] = sum(1 for row in pair_results if row.get("status") == "ok")
    run_record["markets_failed"] = sum(1 for row in pair_results if row.get("status") != "ok")
    all_alert_outcomes = [item for row in pair_results for item in (row.get("alert_results") or [])]
    run_record["alert_outcomes_total"] = len(all_alert_outcomes)
    run_record["alert_outcomes_sent"] = sum(1 for item in all_alert_outcomes if item.get("sent") is True)
    run_record["alert_outcomes_blocked"] = sum(1 for item in all_alert_outcomes if item.get("sent") is False)
    run_record["ai_reviews_ok"] = sum(1 for row in pair_results if row.get("status") == "ok" and str(row.get("ai_review_status", "")).lower() == "ok")
    run_record["ai_reviews_failed"] = sum(
        1 for row in pair_results
        if row.get("status") == "ok"
        and str(row.get("ai_review_status", "missing")).strip().lower() not in {"ok", "disabled", "not_run", "missing", ""}
    )
    (out / "cron_run.json").write_text(json.dumps(run_record, indent=2, allow_nan=False))
    # Database/repository logging failures must remain visible in the local
    # artifact and Actions log instead of aborting before RUN_FINISHED.
    run_record["run_ledger_persisted"] = bool(store.configured)
    if store.configured:
        try:
            store.record_cron_run(run_record)
        except Exception as persist_exc:
            run_record["run_ledger_persisted"] = False
            run_record["persistence_error"] = f"{type(persist_exc).__name__}: {persist_exc}"
            if status in {"success", "scan_complete", "scan_complete_ai_unavailable"}:
                status = "partial_failure"
            run_record["status"] = status
            run_record["error"] = "; ".join(filter(None, [run_record.get("error"), run_record["persistence_error"]]))
            print(f"[FRIDAY] RUN_LEDGER_PERSIST_FAILED id={run_id} error={run_record['persistence_error']}", flush=True)
    (out / "cron_run.json").write_text(json.dumps(run_record, indent=2, allow_nan=False))
    print(
        f"[FRIDAY] RUN_FINISHED id={run_id} status={status} "
        f"markets={len(pair_results)}/{len(pairs)} failed={len(failures)} "
        f"ai_ok={run_record['ai_reviews_ok']} ai_failed={run_record['ai_reviews_failed']} "
        f"alerts_sent={run_record['alert_outcomes_sent']} alert_outcomes={run_record['alert_outcomes_total']} "
        f"events={len(all_rows)} persisted={run_record['live_events_persisted']} "
        f"duration={(pd.Timestamp(run_record['finished_at']) - pd.Timestamp(started_at)).total_seconds():.1f}s",
        flush=True,
    )
    print(json.dumps(run_record, indent=2, allow_nan=False), flush=True)
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
        "SUIUSD": "SUIUSDT", "UNIUSD": "UNIUSDT",
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
            "SUIUSDT", "UNIUSDT",
        }:
            raise ValueError(
                "FRIDAY research scans only the approved USDT universe; rejected: " + pair
            )
        if pair not in normalized:
            normalized.append(pair)
    if not normalized:
        raise ValueError("At least one approved USDT-quoted pair is required")
    return normalized


def _ai_run_status(statuses: list[str]) -> str:
    """Map review outcomes to run status without treating intentional disablement as failure."""
    normalized = [str(status or "missing").strip().lower() for status in statuses]
    attempted = [status for status in normalized if status not in {"disabled", "not_run", "missing", ""}]
    if attempted and any(status != "ok" for status in attempted):
        return "scan_complete_ai_unavailable"
    if normalized and all(status in {"disabled", "not_run", "missing", ""} for status in normalized):
        return "scan_complete"
    return "scan_complete"


def _monitor_exit_code(status: str) -> int:
    """Return success when scanning/persistence completed, even if AI is degraded."""
    return 0 if status in {"success", "scan_complete", "scan_complete_ai_unavailable"} else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pairs", default="XBTUSDT,ETHUSDT,SOLUSDT,XRPUSDT,ADAUSDT,AVAXUSDT,BNBUSDT,VIRTUALUSDT,HYPEUSDT,DOGEUSDT,LINKUSDT,ZECUSDT,NEARUSDT,SUIUSDT,UNIUSDT")
    parser.add_argument("--output-dir", default="results/friday-monitor")
    parser.add_argument("--dry-run", action="store_true", help="Read-only smoke check: no DB writes, files, paper trades or Telegram sends")
    args = parser.parse_args()
    try:
        pairs = validate_friday_pairs(args.pairs.split(","))
    except ValueError as exc:
        parser.error(str(exc))
    result = run_monitor(pairs, args.output_dir, dry_run=args.dry_run)
    return _monitor_exit_code(result["status"])


if __name__ == "__main__":
    sys.exit(main())
