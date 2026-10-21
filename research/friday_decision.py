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

    # These are candidate origins, not declared swing pivots. They remain
    # provisional until reaction/structure evidence supports an entry.
    long_origin = float(prior.low.astype(float).min())
    short_origin = float(prior.high.astype(float).max())
    atr = None
    if {"high", "low", "close"}.issubset(frame.columns) and len(frame) >= 15:
        tr = pd.concat([
            frame.high.astype(float) - frame.low.astype(float),
            (frame.high.astype(float) - frame.close.astype(float).shift()).abs(),
            (frame.low.astype(float) - frame.close.astype(float).shift()).abs(),
        ], axis=1).max(axis=1)
        atr = float(tr.tail(14).mean())

    candidates = []
    for direction, origin in (("long", long_origin), ("short", short_origin)):
        evidence = evaluate_entry1_confluence(
            direction, origin,
            {"high": high, "low": low, "close": close},
            prior,
            atr=atr,
            fib_tolerance_pct=fib_tolerance_pct,
        )
        # Invalidation sits beyond the reaction candle's wick. No entry without
        # a structurally meaningful, bounded stop.
        stop = low * (1 - 0.0015) if direction == "long" else high * (1 + 0.0015)
        entry = close
        risk_pct = abs(entry - stop) / entry if entry else math.inf
        risk_ok = min_stop_pct <= risk_pct <= max_stop_pct
        candidates.append({
            "direction": direction,
            "origin_0_fib": origin,
            "entry_reference_close": close,
            "invalidation": stop,
            "risk_pct": risk_pct * 100,
            "risk_ok": risk_ok,
            "evidence": evidence,
            "eligible": bool(evidence["entry1_candidate"] and risk_ok),
        })

    eligible = [c for c in candidates if c["eligible"]]
    if eligible:
        chosen = max(eligible, key=lambda c: c["evidence"]["confluence_score"])
        direction = chosen["direction"]
        # Structural targets first: prior range extreme in the trade direction,
        # then extension candidates from the causal origin-to-close impulse.
        target = short_origin if direction == "long" else long_origin
        target_valid = target > close if direction == "long" else target < close
        plan = fib_entry_plan(direction, chosen["origin_0_fib"],
                              impulse_extreme=close if (close > chosen["origin_0_fib"] if direction == "long"
                                                        else close < chosen["origin_0_fib"]) else None)
        return {
            "state": "ENTRY_1_CANDIDATE",
            "decision_time": pd.Timestamp(current.timestamp).isoformat(),
            "direction": direction,
            "entry_reference_close": close,
            "invalidation": chosen["invalidation"],
            "risk_pct": chosen["risk_pct"],
            "target_candidates": ([{"type": "prior_range_structure", "price": target}]
                                  if target_valid else []),
            "fib_entry_plan": plan,
            "evidence": chosen["evidence"],
            "reason": "0-Fib touch plus support/resistance or sweep evidence, rejection, and bounded invalidation.",
            "execution": "paper/research only; no order submitted",
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
