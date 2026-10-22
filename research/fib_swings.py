"""Causal confirmed swing pivots and impulse legs for Friday Fib analysis.

A pivot is available only after its right-side confirmation candles have closed.
No future candles are used to label the latest currently knowable swing.
"""
from __future__ import annotations

import math
import pandas as pd


def confirmed_swings(candles: pd.DataFrame, *, left_bars: int = 3, right_bars: int = 3) -> list[dict]:
    required = {"timestamp", "high", "low"}
    if not required.issubset(candles.columns):
        raise ValueError(f"candles missing columns: {sorted(required - set(candles.columns))}")
    if left_bars < 1 or right_bars < 1:
        raise ValueError("pivot confirmation bars must be positive")
    frame = candles.sort_values("timestamp").reset_index(drop=True)
    swings = []
    for i in range(left_bars, len(frame) - right_bars):
        row = frame.iloc[i]
        left = frame.iloc[i-left_bars:i]
        right = frame.iloc[i+1:i+right_bars+1]
        high = float(row.high)
        low = float(row.low)
        # Strictly greater/less than at least one neighbour avoids flat runs
        # generating multiple equal-price pivots.
        if high >= float(left.high.max()) and high >= float(right.high.max()) and (
            high > float(left.high.max()) or high > float(right.high.max())
        ):
            swings.append({"kind": "high", "index": i, "price": high,
                           "pivot_time": pd.Timestamp(row.timestamp).isoformat(),
                           "confirmed_at": pd.Timestamp(frame.iloc[i+right_bars].timestamp).isoformat(),
                           "confirmation_index": i + right_bars})
        if low <= float(left.low.min()) and low <= float(right.low.min()) and (
            low < float(left.low.min()) or low < float(right.low.min())
        ):
            swings.append({"kind": "low", "index": i, "price": low,
                           "pivot_time": pd.Timestamp(row.timestamp).isoformat(),
                           "confirmed_at": pd.Timestamp(frame.iloc[i+right_bars].timestamp).isoformat(),
                           "confirmation_index": i + right_bars})
    return sorted(swings, key=lambda s: (s["index"], 0 if s["kind"] == "low" else 1))


def latest_impulse_leg(candles: pd.DataFrame, *, left_bars: int = 3, right_bars: int = 3) -> dict | None:
    """Return the latest chronologically valid confirmed low→high or high→low leg."""
    swings = confirmed_swings(candles, left_bars=left_bars, right_bars=right_bars)
    if len(swings) < 2:
        return None
    # Search backwards for the most recent adjacent alternating swing pair.
    for end in range(len(swings) - 1, 0, -1):
        a, b = swings[end-1], swings[end]
        if a["kind"] == b["kind"]:
            continue
        if a["confirmation_index"] > b["confirmation_index"]:
            continue
        if a["kind"] == "low" and b["kind"] == "high" and b["price"] > a["price"]:
            return {"direction": "long", "origin": a, "extreme": b,
                    "range": float(b["price"] - a["price"])}
        if a["kind"] == "high" and b["kind"] == "low" and b["price"] < a["price"]:
            return {"direction": "short", "origin": a, "extreme": b,
                    "range": float(a["price"] - b["price"])}
    return None


def retracement_levels(leg: dict, ratios=(0.382, 0.5, 0.618)) -> list[dict]:
    """Compute pullback levels for a confirmed impulse leg."""
    if leg["direction"] == "long":
        low, high = float(leg["origin"]["price"]), float(leg["extreme"]["price"])
        return [{"fib": float(r), "price": high - (high-low)*float(r)} for r in ratios]
    high, low = float(leg["origin"]["price"]), float(leg["extreme"]["price"])
    return [{"fib": float(r), "price": low + (high-low)*float(r)} for r in ratios]
