"""Shared, auditable Entry 1 / Entry 2 eligibility gates.

This is a research-only alert gate. It does not submit or size exchange orders.
"""
from __future__ import annotations

from typing import Any, Mapping


def evaluate_entry_gate(
    event: Mapping[str, Any],
    *,
    entry_type: str,
    thesis: Mapping[str, Any],
    trade_plan: Mapping[str, Any],
) -> dict:
    """Return pass/block plus a complete list of named checks and reasons.

    Entry 1 is an early closed-candle break probe: it does not require a retest,
    but it must align with a directional daily thesis and pass the shared plan
    gate. Entry 2 requires a held retest and a later continuation close.
    """
    checks: list[dict[str, Any]] = []

    def check(name: str, passed: bool, reason: str) -> None:
        checks.append({"gate": name, "passed": bool(passed), "reason": reason})

    direction = event.get("direction")
    check(
        "directional_daily_thesis",
        direction in ("long", "short")
        and thesis.get("thesis_direction") in ("bullish", "bearish")
        and direction == {"bullish": "long", "bearish": "short"}.get(thesis.get("thesis_direction")),
        "Entry direction must match a non-neutral daily thesis.",
    )

    daily_breaks = thesis.get("daily_directional_breaks") or []
    matching_break = any(
        isinstance(item, Mapping) and item.get("direction") == direction
        for item in daily_breaks
    )
    check(
        "confirmed_daily_trendline_break",
        matching_break,
        "A same-direction confirmed daily trendline break is required to anchor the higher-timeframe thesis.",
    )

    anchors_present = bool(
        event.get("anchor_start_time")
        and event.get("anchor_end_time")
        and event.get("line_kind")
        and event.get("line_price_at_break") is not None
        and event.get("signal_time")
    )
    check(
        "thesis_identity",
        anchors_present,
        "The 4H break must retain its original line anchors, break level, and signal timestamp.",
    )

    if entry_type == "ENTRY_1":
        is_probe = event.get("event") == "BREAK_CLOSE_PROBE"
        check(
            "closed_candle_entry1_trigger",
            is_probe and event.get("outcome") not in ("failed_break", "no_retest_within_window", "no_continuation_within_window"),
            "Entry 1 is the original closed-candle break probe; a retest is not required.",
        )
    elif entry_type == "ENTRY_2":
        has_retest = bool(event.get("retest_confirmed") and event.get("retest_confirmed_time"))
        check(
            "retest_held",
            has_retest,
            "Entry 2 requires a recorded post-break retest that held on a closed candle.",
        )
        add_time = event.get("continuation_add_time")
        signal_time = event.get("signal_time")
        later = bool(add_time and signal_time and str(add_time) > str(signal_time))
        trigger = event.get("continuation_trigger")
        close = event.get("continuation_add_close")
        directionally_broken = (
            close is not None and trigger is not None
            and ((direction == "long" and float(close) > float(trigger))
                 or (direction == "short" and float(close) < float(trigger)))
        )
        check(
            "later_continuation_close",
            event.get("event_add") == "CONTINUATION_ADD_CONFIRMED"
            and later and directionally_broken,
            "A later completed candle must close beyond the retest reaction extreme in the trade direction.",
        )
    else:
        check("known_entry_type", False, "Entry type must be ENTRY_1 or ENTRY_2.")

    blockers = list(trade_plan.get("blockers") or [])
    plan_ok = trade_plan.get("eligible") is True
    check(
        "shared_structural_risk_reward_gate",
        plan_ok,
        "A confirmed structural stop, forward target, and minimum reward/risk must pass."
        if plan_ok else "Trade plan blocked: " + (", ".join(map(str, blockers)) or "plan_not_eligible"),
    )

    return {
        "entry_type": entry_type,
        "eligible": bool(checks) and all(item["passed"] for item in checks),
        "state": "PASS" if checks and all(item["passed"] for item in checks) else "BLOCKED",
        "checks": checks,
        "blockers": [item["gate"] + ": " + item["reason"] for item in checks if not item["passed"]],
        "research_only": True,
    }
