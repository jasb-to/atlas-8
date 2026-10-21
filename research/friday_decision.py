"""Friday's causal price-action decision layer (research-only).

This module emits a transparent WATCH/ENTRY_CANDIDATE/NO_TRADE decision.
It does not place orders. All swing references are derived from prior candles;
the current candle is used only for its own touch/reaction evidence.
"""
from __future__ import annotations

import math
from collections.abc import Mapping

import pandas as pd

from research.entry_confluence import evaluate_entry1_confluence
from research.fib_entries import fib_entry_plan
from research.fib_swings import latest_impulse_leg, retracement_levels


def evaluate_friday_decision(
    candles: pd.DataFrame,
    *,
    lookback: int = 30,
    fib_tolerance_pct: float = 0.005,
    min_stop_pct: float = 0.002,
    max_stop_pct: float = 0.04,
) -> dict:
    """Evaluate the last closed candle against causal prior-range 0-Fib candidates."""
    required = {"timestamp", "open", "high", "low", "close"}
    if not required.issubset(candles.columns):
        raise ValueError(f"candles missing columns: {sorted(required - set(candles.columns))}")
    if lookback < 5 or len(candles) < 5:
        raise ValueError("need at least five candles and lookback >= 5")
    frame = candles.sort_values("timestamp").reset_index(drop=True)
    current = frame.iloc[-1]
    prior = frame.iloc[max(0, len(frame) - lookback - 1):-1]
    close = float(current.close)
    high, low = float(current.high), float(current.low)
    candle_range = max(high - low, 1e-12)
    if prior.empty or close <= 0:
        return {"state": "NO_TRADE", "reason": "Insufficient prior candles for causal structure."}

    atr = None
    if len(frame) >= 15:
        tr = pd.concat([
            frame.high.astype(float) - frame.low.astype(float),
            (frame.high.astype(float) - frame.close.astype(float).shift()).abs(),
            (frame.low.astype(float) - frame.close.astype(float).shift()).abs(),
        ], axis=1).max(axis=1)
        atr = float(tr.tail(14).mean())

    leg = latest_impulse_leg(frame, left_bars=3, right_bars=3)
    if leg is None:
        return {
            "state": "NO_TRADE", "decision_time": pd.Timestamp(current.timestamp).isoformat(),
            "close": close, "reason": "No alternating confirmed swing pair yet; wait for a causal impulse leg.",
            "execution": "no trade; no order submitted",
        }

    direction = leg["direction"]
    origin = float(leg["origin"]["price"])
    extreme = float(leg["extreme"]["price"])
    plan = fib_entry_plan(direction, origin, impulse_extreme=extreme)
    levels = retracement_levels(leg)
    tolerance = fib_tolerance_pct
    candidates = []
    for level in levels:
        price = float(level["price"])
        # Current candle must overlap the Fib zone. A confluence score alone
        # cannot promote an untouched retracement into a candidate.
        touched = low <= price * (1 + tolerance) and high >= price * (1 - tolerance)
        evidence = evaluate_entry1_confluence(
            direction, price, {"high": high, "low": low, "close": close}, prior,
            atr=atr, fib_tolerance_pct=tolerance,
        )
        stop = low * (1 - 0.0015) if direction == "long" else high * (1 + 0.0015)
        risk_pct = abs(close - stop) / close * 100 if close else math.inf
        risk_ok = min_stop_pct * 100 <= risk_pct <= max_stop_pct * 100
        # For a long, a close back above the tested retracement is required;
        # for a short, a close back below it. Structure/sweep confluence is
        # still required by the shared evidence scorer.
        reaction = close > price if direction == "long" else close < price
        eligible = bool(touched and reaction and evidence["entry1_candidate"] and risk_ok)
        candidates.append({
            "fib": level["fib"], "price": price, "touched": touched,
            "reaction": reaction, "invalidation": stop, "risk_pct": risk_pct,
            "risk_ok": risk_ok, "evidence": evidence, "eligible": eligible,
        })

    eligible = [candidate for candidate in candidates if candidate["eligible"]]
    common = {
        "decision_time": pd.Timestamp(current.timestamp).isoformat(),
        "direction": direction, "impulse_origin": leg["origin"],
        "impulse_extreme": leg["extreme"], "fib_entry_plan": plan,
        "retracement_levels": candidates,
        "execution": "paper/research only; no order submitted",
    }
    if eligible:
        chosen = max(eligible, key=lambda candidate: candidate["evidence"]["confluence_score"])
        # Targets are the prior visible swing/structure in the trade direction.
        target = extreme
        return {
            **common, "state": "ENTRY_2_CANDIDATE",
            "entry_reference_close": close, "entry_fib": chosen["fib"],
            "entry_level": chosen["price"], "invalidation": chosen["invalidation"],
            "risk_pct": chosen["risk_pct"],
            "target_candidates": [{"type": "prior_impulse_extreme", "price": target}],
            "evidence": chosen["evidence"],
            "reason": f"Confirmed {direction} impulse; {chosen['fib']:.3f} retracement touched and reclaimed/rejected with structural or sweep evidence and bounded invalidation.",
        }

    touched = [candidate for candidate in candidates if candidate["touched"]]
    return {
        **common,
        "state": "WATCHING" if touched else "NO_TRADE",
        "close": close,
        "reason": ("Price is in a confirmed impulse retracement zone, but reaction/confluence or risk checks are incomplete."
                   if touched else "No confirmed 0.382/0.5/0.618 pullback zone touched; wait for the rally/pullback rather than treating a rolling lookback low as 0-Fib."),
        "execution": "no trade; no order submitted",
    }

    best = max(candidates, key=lambda c: c["evidence"]["confluence_score"])
    return {
        "state": "WATCHING" if any(c["evidence"]["fib_area_touched"] for c in candidates) else "NO_TRADE",
        "decision_time": pd.Timestamp(current.timestamp).isoformat(),
        "direction_watch": best["direction"],
        "close": close,
        "candidate_origins": {"long_0_fib": long_origin, "short_0_fib": short_origin},
        "candidate_evidence": [c["evidence"] for c in candidates],
        "reason": ("Fib origin touched but structural/reaction evidence is incomplete."
                   if any(c["evidence"]["fib_area_touched"] for c in candidates)
                   else "No qualified 0-Fib reaction on the latest closed candle; wait for price to reach a meaningful origin."),
        "execution": "no trade; no order submitted",
    }
