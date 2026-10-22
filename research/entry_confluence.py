"""Causal Entry 1 confluence: 0-Fib proximity, S/R and liquidity sweep.

Research-only helpers. Input candles must end at the signal candle; callers must
not pass future-confirmed pivots or future candles. A sweep is inferred from
past visible highs/lows and the current candle's wick/close, not order-book data.
"""
from __future__ import annotations

import math
from collections.abc import Mapping

import pandas as pd


def find_reaction_levels(
    candles: pd.DataFrame,
    *,
    lookback: int = 180,
    tolerance_pct: float = 0.003,
    min_touches: int = 2,
) -> list[dict]:
    """Group prior candle highs/lows into simple horizontal reaction zones.

    This deliberately uses only candles supplied by the caller. It is a
    transparent baseline, not a substitute for a fully confirmed swing engine.
    Each candle's high/low is a potential reaction, and nearby prices cluster.
    """
    if lookback < 2 or tolerance_pct <= 0 or min_touches < 1:
        raise ValueError("invalid level detection parameters")
    required = {"high", "low"}
    if not required.issubset(candles.columns):
        raise ValueError(f"candles missing columns: {sorted(required-set(candles.columns))}")
    prior = candles.tail(lookback)
    points: list[tuple[float, str, int]] = []
    for idx, row in enumerate(prior.itertuples(index=False)):
        points.append((float(row.high), "resistance", idx))
        points.append((float(row.low), "support", idx))
    points.sort(key=lambda item: item[0])

    clusters: list[list[tuple[float, str, int]]] = []
    for point in points:
        if not math.isfinite(point[0]) or point[0] <= 0:
            continue
        if clusters:
            center = sum(p[0] for p in clusters[-1]) / len(clusters[-1])
            if abs(point[0] - center) / center <= tolerance_pct:
                clusters[-1].append(point)
                continue
        clusters.append([point])

    levels = []
    for cluster in clusters:
        distinct_bars = len({p[2] for p in cluster})
        if distinct_bars < min_touches:
            continue
        price = sum(p[0] for p in cluster) / len(cluster)
        kinds = {p[1] for p in cluster}
        levels.append({
            "price": float(price),
            "touches": distinct_bars,
            "kind": "both" if len(kinds) == 2 else next(iter(kinds)),
            "low": float(min(p[0] for p in cluster)),
            "high": float(max(p[0] for p in cluster)),
        })
    return levels


def evaluate_entry1_confluence(
    direction: str,
    fib_zero: float,
    current: Mapping,
    prior_candles: pd.DataFrame,
    *,
    atr: float | None = None,
    fib_tolerance_pct: float = 0.005,
    lookback: int = 180,
    level_tolerance_pct: float = 0.003,
) -> dict:
    """Score whether the 0-Fib area has structural/liquidity evidence.

    Long: seek prior support near Fib 0 and/or a sweep below prior lows followed
    by a close back above that liquidity reference.
    Short: mirror the logic at prior highs/resistance.

    Returns evidence and a transparent score; it does not itself authorize a
    trade. Caller still needs causal swing anchors, stop/size checks and setup
    invalidation rules.
    """
    if direction not in {"long", "short"}:
        raise ValueError("direction must be 'long' or 'short'")
    if not math.isfinite(fib_zero) or fib_zero <= 0:
        raise ValueError("fib_zero must be finite and positive")
    if fib_tolerance_pct <= 0 or level_tolerance_pct <= 0:
        raise ValueError("tolerances must be positive")
    required = {"high", "low", "close"}
    if not required.issubset(prior_candles.columns):
        raise ValueError(f"prior_candles missing columns: {sorted(required-set(prior_candles.columns))}")
    if prior_candles.empty:
        raise ValueError("prior_candles must contain candles before the signal candle")

    c_high, c_low, c_close = (float(current[k]) for k in ("high", "low", "close"))
    if not all(math.isfinite(v) and v > 0 for v in (c_high, c_low, c_close)):
        raise ValueError("current candle prices must be finite and positive")
    if c_low > c_high or not c_low <= c_close <= c_high:
        raise ValueError("current candle OHLC is inconsistent")

    prior = prior_candles.tail(lookback)
    levels = find_reaction_levels(prior, lookback=lookback,
                                  tolerance_pct=level_tolerance_pct, min_touches=2)
    target_kind = "support" if direction == "long" else "resistance"
    relevant = [level for level in levels if level["kind"] in {target_kind, "both"}]
    nearest = min(relevant, key=lambda level: abs(level["price"]-fib_zero)/fib_zero,
                  default=None)
    sr_overlap = bool(nearest and abs(nearest["price"]-fib_zero)/fib_zero <= fib_tolerance_pct)

    if direction == "long":
        liquidity_ref = float(prior.low.astype(float).min())
        swept = c_low < liquidity_ref and c_close > liquidity_ref
        fib_touched = c_low <= fib_zero * (1 + fib_tolerance_pct) and c_high >= fib_zero * (1 - fib_tolerance_pct)
        rejection = c_close > fib_zero and (c_close-c_low) >= 0.5 * max(c_high-c_low, 1e-12)
    else:
        liquidity_ref = float(prior.high.astype(float).max())
        swept = c_high > liquidity_ref and c_close < liquidity_ref
        fib_touched = c_high >= fib_zero * (1 - fib_tolerance_pct) and c_low <= fib_zero * (1 + fib_tolerance_pct)
        rejection = c_close < fib_zero and (c_high-c_close) >= 0.5 * max(c_high-c_low, 1e-12)

    evidence = {
        "fib_zero": float(fib_zero),
        "fib_area_touched": bool(fib_touched),
        "near_support_resistance": bool(sr_overlap),
        "nearest_level": nearest,
        "liquidity_reference": liquidity_ref,
        "liquidity_sweep_and_reclaim": bool(swept),
        "rejection_or_reclaim_candle": bool(rejection),
        "atr": float(atr) if atr is not None and math.isfinite(float(atr)) and float(atr) > 0 else None,
    }
    # Structure/liquidity are required evidence; candle reaction improves timing.
    score = 0
    score += 1 if fib_touched else 0
    score += 2 if sr_overlap else 0
    score += 2 if swept else 0
    score += 1 if rejection else 0
    evidence["confluence_score"] = score
    evidence["entry1_candidate"] = bool(fib_touched and (sr_overlap or swept) and rejection)
    evidence["status"] = "ENTRY_1_CONFLUENCE_CANDIDATE" if evidence["entry1_candidate"] else "WAIT_FOR_CONFLUENCE"
    return evidence
