"""Causal Fib entry planning helpers for Friday's direction-change research.

Research-only: these helpers calculate candidate levels; they do not place orders
or claim a reversal is confirmed. The caller must supply anchors known at the
signal time, never a future-confirmed swing.
"""
from __future__ import annotations

from collections.abc import Iterable
import math


DEFAULT_RETEST_RATIOS = (0.382, 0.5, 0.618)


def fib_entry_plan(
    direction: str,
    origin_price: float,
    impulse_extreme: float | None = None,
    *,
    retest_ratios: Iterable[float] = DEFAULT_RETEST_RATIOS,
) -> dict:
    """Return Entry 1 at the 0-Fib origin and Entry 2 retracement candidates.

    For a long, origin_price is the low/origin of the reversal and
    impulse_extreme is the subsequent impulse high. Entry 2 candidates are
    retracements down from that high. For a short, origin_price is the high
    from which the reversal starts and impulse_extreme is the subsequent low;
    Entry 2 candidates are retracements up from that low.

    The origin must be available causally at the time of use. In backtests,
    do not label a historical low/high as known until the algorithm could
    identify it using candles available then.
    """
    if direction not in {"long", "short"}:
        raise ValueError("direction must be 'long' or 'short'")
    if not math.isfinite(origin_price) or origin_price <= 0:
        raise ValueError("origin_price must be a finite positive price")

    ratios = tuple(float(r) for r in retest_ratios)
    if any(not math.isfinite(r) or not 0 < r < 1 for r in ratios):
        raise ValueError("retest ratios must be finite values between 0 and 1")
    if len(set(ratios)) != len(ratios):
        raise ValueError("retest ratios must be unique")

    plan = {
        "direction": direction,
        "entry_1": {
            "fib": 0.0,
            "price": float(origin_price),
            "role": "early_probe_area",
        },
        "entry_2_candidates": [],
        "impulse_range": None,
        "status": "awaiting_impulse",
    }
    if impulse_extreme is None:
        return plan
    if not math.isfinite(impulse_extreme) or impulse_extreme <= 0:
        raise ValueError("impulse_extreme must be a finite positive price")

    if direction == "long":
        span = impulse_extreme - origin_price
        if span <= 0:
            raise ValueError("long impulse extreme must be above the 0-Fib origin")
        levels = [impulse_extreme - span * r for r in ratios]
    else:
        span = origin_price - impulse_extreme
        if span <= 0:
            raise ValueError("short impulse extreme must be below the 0-Fib origin")
        levels = [impulse_extreme + span * r for r in ratios]

    plan["impulse_range"] = float(span)
    plan["entry_2_candidates"] = [
        {"fib": ratio, "price": float(price), "role": "retest_add_candidate"}
        for ratio, price in zip(ratios, levels)
    ]
    plan["status"] = "retest_levels_ready"
    return plan
