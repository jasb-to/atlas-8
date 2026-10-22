"""Rank likely Fibonacci bounce zones from causal confluence (research-only).

Scores are heuristic ranks, NOT probabilities. Calibrated probabilities require
walk-forward historical outcomes for each asset/timeframe/regime.
"""
from __future__ import annotations

import math
import pandas as pd

from research.entry_confluence import find_reaction_levels


def rank_fib_bounce_levels(
    direction: str,
    levels: list[dict],
    prior_candles: pd.DataFrame,
    *,
    atr: float | None = None,
    current_price: float | None = None,
    tolerance_pct: float = 0.005,
    lookback: int = 180,
) -> list[dict]:
    """Rank Fib retracement candidates before/at touch using only supplied past data."""
    if direction not in {"long", "short"}:
        raise ValueError("direction must be long or short")
    if prior_candles.empty or not {"high", "low", "close"}.issubset(prior_candles.columns):
        raise ValueError("prior candles with high/low/close are required")
    if tolerance_pct <= 0:
        raise ValueError("tolerance_pct must be positive")

    prior = prior_candles.tail(lookback).copy()
    sr_levels = find_reaction_levels(prior, lookback=lookback,
                                    tolerance_pct=max(tolerance_pct, 0.0025),
                                    min_touches=2)
    # Confirmed swing points in supplied history are approximated by local
    # extrema with neighbours on both sides; only past bars are supplied.
    highs = prior.high.astype(float).tolist()
    lows = prior.low.astype(float).tolist()
    swing_prices = []
    for i in range(2, len(prior)-2):
        if highs[i] >= max(highs[i-2:i]) and highs[i] >= max(highs[i+1:i+3]):
            swing_prices.append(("resistance", highs[i]))
        if lows[i] <= min(lows[i-2:i]) and lows[i] <= min(lows[i+1:i+3]):
            swing_prices.append(("support", lows[i]))

    results = []
    for level in levels:
        price = float(level["price"])
        if price <= 0 or not math.isfinite(price):
            continue
        reasons = []
        score = 0.0
        target_kind = "support" if direction == "long" else "resistance"
        near_sr = [x for x in sr_levels
                   if x["kind"] in {target_kind, "both"}
                   and abs(x["price"]-price)/price <= tolerance_pct]
        if near_sr:
            best = max(near_sr, key=lambda x: x["touches"])
            score += min(3.0, 1.0 + 0.5 * max(0, best["touches"]-2))
            reasons.append(f"overlaps {target_kind} zone with {best['touches']} prior touches")
        near_swing = [x for x in swing_prices if x[0] == target_kind
                      and abs(x[1]-price)/price <= tolerance_pct]
        if near_swing:
            score += min(2.0, 0.75 + 0.25*len(near_swing))
            reasons.append("overlaps prior confirmed-style swing structure")
        distance_atr = abs(float(current_price)-price)/float(atr) if (
            current_price is not None and atr is not None and atr > 0
        ) else None
        if distance_atr is not None:
            if distance_atr <= 0.5:
                score += 1.0
                reasons.append("near enough to monitor for a reaction")
            elif distance_atr <= 1.5:
                score += 0.5
                reasons.append("within 1.5 ATR")
        # Reward a zone where the current market has not already decisively
        # invalidated it; this is context, not proof of a future bounce.
        if current_price is not None:
            intact = current_price >= price*(1-tolerance_pct) if direction == "long" else current_price <= price*(1+tolerance_pct)
            if intact:
                score += 0.5
                reasons.append("level not decisively lost at current close")
        results.append({
            **level,
            "confluence_score": round(score, 3),
            "rank": None,
            "heuristic_reasons": reasons,
            "historical_probability": None,
            "probability_status": "uncalibrated_requires_walk_forward_replay",
            "warning": "Ranking is not a statistically calibrated probability.",
            "distance_atr": round(distance_atr, 3) if distance_atr is not None else None,
        })
    results.sort(key=lambda x: (-x["confluence_score"], abs(float(x["fib"])-0.5)))
    for i, item in enumerate(results, 1):
        item["rank"] = i
    return results
