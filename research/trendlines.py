"""Price-touch trendline geometry for Atlas-8 research.

This module deliberately does not emit trade entries. It finds candidate lines from
confirmed OHLC wick pivots and reports measurable touches/breaks/retests so geometry
can be inspected before any strategy rules are attached.

Indices are integer candle positions. A pivot at i is only knowable after right_bars
subsequent candles have closed; callers must not act on it before then.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

import numpy as np
import pandas as pd

Side = Literal["support", "resistance"]


@dataclass(frozen=True)
class Pivot:
    index: int
    price: float
    kind: Side
    confirmed_at: int


@dataclass(frozen=True)
class Trendline:
    kind: Side
    start: Pivot
    end: Pivot
    slope_per_bar: float
    tolerance: float
    touch_indices: tuple[int, ...]
    violations: tuple[int, ...]

    def value_at(self, index: int) -> float:
        return self.start.price + self.slope_per_bar * (index - self.start.index)

    def as_dict(self) -> dict:
        result = asdict(self)
        result["touch_count"] = len(self.touch_indices)
        result["start_value"] = self.start.price
        result["end_value"] = self.end.price
        return result


def find_wick_pivots(
    candles: pd.DataFrame, left_bars: int = 3, right_bars: int = 3
) -> list[Pivot]:
    """Find strict local wick highs/lows; confirmed pivots are never backdated as known."""
    _validate_ohlc(candles)
    if left_bars < 1 or right_bars < 1:
        raise ValueError("left_bars and right_bars must both be >= 1")

    highs = candles["high"].to_numpy(dtype=float)
    lows = candles["low"].to_numpy(dtype=float)
    found: list[Pivot] = []
    for i in range(left_bars, len(candles) - right_bars):
        high_window = highs[i - left_bars : i + right_bars + 1]
        low_window = lows[i - left_bars : i + right_bars + 1]
        # Strict extrema avoid counting flat-topped/bottomed clusters as many pivots.
        if highs[i] == np.max(high_window) and np.count_nonzero(high_window == highs[i]) == 1:
            found.append(Pivot(i, float(highs[i]), "resistance", i + right_bars))
        if lows[i] == np.min(low_window) and np.count_nonzero(low_window == lows[i]) == 1:
            found.append(Pivot(i, float(lows[i]), "support", i + right_bars))
    return sorted(found, key=lambda p: (p.index, p.kind))


def fit_touch_trendlines(
    candles: pd.DataFrame,
    pivots: list[Pivot] | None = None,
    *,
    kind: Side,
    min_touches: int = 3,
    tolerance_pct: float = 0.0015,
    max_penetration_pct: float = 0.003,
    max_lines: int = 20,
) -> list[Trendline]:
    """Build candidate lines through same-kind wick pivots and score real wick contacts.

    tolerance_pct is a fraction of line price (0.0015 = 0.15%). For support, lows
    must approach the line from above; for resistance, highs must approach from below.
    A line is rejected when any candle penetrates it by more than max_penetration_pct.
    """
    _validate_ohlc(candles)
    if min_touches < 2 or tolerance_pct < 0 or max_penetration_pct < 0:
        raise ValueError("Invalid touch or tolerance parameters")
    pivots = pivots if pivots is not None else find_wick_pivots(candles)
    points = sorted(
        (p for p in pivots if p.kind == kind),
        key=lambda p: p.index,
    )
    wick = candles["low" if kind == "support" else "high"].to_numpy(dtype=float)
    lines: list[Trendline] = []
    seen: set[tuple[int, int]] = set()

    for a_pos, start in enumerate(points):
        for end in points[a_pos + 1 :]:
            if end.index <= start.index or (start.index, end.index) in seen:
                continue
            seen.add((start.index, end.index))
            slope = (end.price - start.price) / (end.index - start.index)
            idx = np.arange(start.index, len(candles))
            values = start.price + slope * (idx - start.index)
            relevant_wick = wick[start.index:]
            distance = relevant_wick - values
            tol = np.maximum(np.abs(values) * tolerance_pct, 1e-12)
            penetration = np.maximum(np.abs(values) * max_penetration_pct, 1e-12)

            if kind == "support":
                touch_mask = (distance >= -penetration) & (distance <= tol)
                violation_mask = distance < -penetration
            else:
                distance = -distance
                touch_mask = (distance >= -penetration) & (distance <= tol)
                violation_mask = distance < -penetration

            touch_indices = tuple((np.flatnonzero(touch_mask) + start.index).tolist())
            violations = tuple((np.flatnonzero(violation_mask) + start.index).tolist())
            # Both anchor candles must count as contacts, and touches are distinct candles.
            if len(touch_indices) < min_touches or violations:
                continue
            lines.append(
                Trendline(
                    kind=kind,
                    start=start,
                    end=end,
                    slope_per_bar=float(slope),
                    tolerance=float(max(np.abs(values).max() * tolerance_pct, 1e-12)),
                    touch_indices=touch_indices,
                    violations=violations,
                )
            )

    # Prefer independently touched lines; tie-break toward fewer penetrations (always 0)
    # and the most recent anchor. Keep candidates separate rather than silently choosing one.
    lines.sort(
        key=lambda line: (
            len(line.touch_indices),
            line.end.index,
            line.start.index,
        ),
        reverse=True,
    )
    return lines[:max_lines]


def classify_line_state(
    candles: pd.DataFrame,
    line: Trendline,
    *,
    index: int | None = None,
    tolerance_pct: float = 0.0015,
    retest_lookahead_bars: int = 8,
) -> dict:
    """Describe approach/break/retest/hold on closed candles; no orders are generated."""
    _validate_ohlc(candles)
    i = len(candles) - 1 if index is None else index
    if i < 1 or i >= len(candles):
        raise IndexError("index must reference an existing candle after the first")
    row = candles.iloc[i]
    prev = candles.iloc[i - 1]
    line_now = line.value_at(i)
    line_prev = line.value_at(i - 1)
    tol = abs(line_now) * tolerance_pct

    if line.kind == "resistance":
        broken = float(row["close"]) > line_now + tol and float(prev["close"]) <= line_prev + abs(line_prev) * tolerance_pct
        near = abs(float(row["high"]) - line_now) <= tol or abs(float(row["close"]) - line_now) <= tol
        state = "breakout" if broken else ("approaching" if float(row["close"]) < line_now and line_now - float(row["close"]) <= tol * 3 else "watch")
        retest_side = "support"
    else:
        broken = float(row["close"]) < line_now - tol and float(prev["close"]) >= line_prev - abs(line_prev) * tolerance_pct
        near = abs(float(row["low"]) - line_now) <= tol or abs(float(row["close"]) - line_now) <= tol
        state = "breakdown" if broken else ("approaching" if float(row["close"]) > line_now and float(row["close"]) - line_now <= tol * 3 else "watch")
        retest_side = "resistance"

    retest = False
    retest_index = None
    if line.kind == "resistance" and line.end.index < i:
        for j in range(max(line.end.index + 1, i - retest_lookahead_bars), i + 1):
            r = candles.iloc[j]
            v = line.value_at(j)
            contacted = float(r["low"]) <= v + abs(v) * tolerance_pct
            held = float(r["close"]) >= v - abs(v) * tolerance_pct
            if contacted and held and float(r["close"]) > v:
                retest, retest_index = True, j
    elif line.kind == "support" and line.end.index < i:
        for j in range(max(line.end.index + 1, i - retest_lookahead_bars), i + 1):
            r = candles.iloc[j]
            v = line.value_at(j)
            contacted = float(r["high"]) >= v - abs(v) * tolerance_pct
            held = float(r["close"]) <= v + abs(v) * tolerance_pct
            if contacted and held and float(r["close"]) < v:
                retest, retest_index = True, j

    return {
        "index": i,
        "line_kind": line.kind,
        "line_value": float(line_now),
        "close": float(row["close"]),
        "distance_pct": float((float(row["close"]) - line_now) / abs(line_now) * 100),
        "near_line": bool(near),
        "state": state,
        "retest_detected": bool(retest),
        "retest_index": retest_index,
        "retest_role_after_break": retest_side,
        "note": "Closed-candle geometry only; combine with regime/EMA/Stoch RSI separately.",
    }


def _validate_ohlc(candles: pd.DataFrame) -> None:
    required = {"high", "low", "close"}
    missing = required.difference(candles.columns)
    if missing:
        raise ValueError(f"OHLC dataframe missing columns: {sorted(missing)}")
    if candles.empty:
        raise ValueError("candles must not be empty")
    if not candles.index.is_monotonic_increasing:
        raise ValueError("candles must be in chronological order")
    if candles[["high", "low", "close"]].isna().any().any():
        raise ValueError("OHLC columns must not contain NaN values")
    if (candles["high"] < candles["low"]).any():
        raise ValueError("high must be >= low for every candle")
