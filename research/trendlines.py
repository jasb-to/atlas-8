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
MIN_TOUCH_SPACING = 3
DEFAULT_RETEST_LOOKBACK = 8


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
    last_touch_confirmed_at: int | None = None

    @property
    def last_touch_index(self) -> int:
        """Position of the latest qualifying confirmed pivot contact."""
        return self.touch_indices[-1] if self.touch_indices else self.end.index

    @property
    def confirmed_at(self) -> int:
        """Candle by which every touch used to qualify the line was confirmed."""
        if self.last_touch_confirmed_at is not None:
            return self.last_touch_confirmed_at
        return max(self.start.confirmed_at, self.end.confirmed_at)

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
    min_touch_spacing: int = MIN_TOUCH_SPACING,
    min_price_eps: float = 1e-8,
) -> list[Trendline]:
    """Build candidate lines through same-kind wick pivots and score real wick contacts.

    tolerance_pct is a fraction of line price (0.0015 = 0.15%). For support, lows
    must approach the line from above; for resistance, highs must approach from below.
    Candidate quality is checked only between the two anchors. Later breaks remain
    observable and must not erase a historically valid line from the research output.
    """
    _validate_ohlc(candles)
    if not candles.index.equals(pd.RangeIndex(len(candles))):
        raise ValueError("fit_touch_trendlines requires a zero-based positional RangeIndex; reset_index(drop=True) first")
    if min_touches < 2 or tolerance_pct < 0 or max_penetration_pct < 0 or min_touch_spacing < 1 or min_price_eps <= 0 or max_lines < 1:
        raise ValueError("Invalid touch, spacing, price floor, or tolerance parameters")
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
            # Validate the line only over its formation segment. A later breakout or
            # breakdown is an event to report, not a reason to erase the old trendline.
            idx = np.arange(start.index, end.index + 1)
            values = start.price + slope * (idx - start.index)
            relevant_wick = wick[start.index : end.index + 1]
            distance = relevant_wick - values
            tol = np.maximum(np.abs(values) * tolerance_pct, min_price_eps)
            penetration = np.maximum(np.abs(values) * max_penetration_pct, min_price_eps)

            if kind == "support":
                touch_mask = (distance >= -penetration) & (distance <= tol)
                violation_mask = distance < -penetration
            else:
                distance = -distance
                touch_mask = (distance >= -penetration) & (distance <= tol)
                violation_mask = distance < -penetration

            # Count independent swing-wick contacts, not every consecutive candle
            # that happens to sit inside the tolerance band. This prevents a 10-candle
            # grind along a line being reported as 10 separate touches.
            pivot_indices = np.array(
                [p.index for p in points if start.index <= p.index <= end.index],
                dtype=int,
            )
            pivot_wicks = wick[pivot_indices] if len(pivot_indices) else np.array([])
            pivot_line_values = start.price + slope * (pivot_indices - start.index)
            pivot_distances = pivot_wicks - pivot_line_values
            pivot_tolerances = np.maximum(np.abs(pivot_line_values) * tolerance_pct, min_price_eps)
            pivot_penetrations = np.maximum(np.abs(pivot_line_values) * max_penetration_pct, min_price_eps)
            if kind == "resistance":
                pivot_distances = -pivot_distances
            pivot_touch_mask = (pivot_distances >= -pivot_penetrations) & (pivot_distances <= pivot_tolerances)
            touch_indices = tuple(pivot_indices[np.flatnonzero(pivot_touch_mask)].tolist())
            violations = tuple((np.flatnonzero(violation_mask) + start.index).tolist())
            # Both anchor candles must count as contacts; all contacts are confirmed
            # same-side swing pivots, while every intervening candle is checked for penetration.
            if len(touch_indices) < min_touches or violations:
                continue
            # Keep distinct contacts structurally separate; nearby ragged pivots
            # should not satisfy the minimum-touch rule on their own.
            if any((b - a) < min_touch_spacing for a, b in zip(touch_indices, touch_indices[1:])):
                continue
            lines.append(
                Trendline(
                    kind=kind,
                    start=start,
                    end=end,
                    slope_per_bar=float(slope),
                    tolerance=float(max(np.abs(values).max() * tolerance_pct, min_price_eps)),
                    touch_indices=touch_indices,
                    violations=violations,
                    last_touch_confirmed_at=max(p.confirmed_at for p in points if p.index in touch_indices),
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
    deduped: list[Trendline] = []
    for candidate in lines:
        candidate_touches = set(candidate.touch_indices)
        if any(
            existing.kind == candidate.kind
            and len(candidate_touches.intersection(existing.touch_indices)) >= max(2, min_touches - 1)
            and abs(existing.slope_per_bar - candidate.slope_per_bar) <= max(abs(candidate.slope_per_bar), 1.0) * 1e-6
            and abs(existing.start.price - candidate.start.price) <= max(abs(candidate.start.price), min_price_eps) * tolerance_pct
            for existing in deduped
        ):
            continue
        deduped.append(candidate)
        if len(deduped) >= max_lines:
            break
    return deduped


def classify_line_state(
    candles: pd.DataFrame,
    line: Trendline,
    *,
    index: int | None = None,
    tolerance_pct: float = 0.0015,
    retest_lookback_bars: int = DEFAULT_RETEST_LOOKBACK,
) -> dict:
    """Describe approach/break/retest/hold on closed candles; no orders are generated."""
    _validate_ohlc(candles)
    i = len(candles) - 1 if index is None else index
    if i < 1 or i >= len(candles):
        raise IndexError("index must reference an existing candle after the first")
    row = candles.iloc[i]
    prev = candles.iloc[i - 1]
    if retest_lookback_bars < 1:
        raise ValueError("retest_lookback_bars must be >= 1")
    line_now = line.value_at(i)
    line_prev = line.value_at(i - 1)
    tol = abs(line_now) * tolerance_pct

    if line.kind == "resistance":
        # State is persistent while price remains beyond the line, not only on the
        # single candle that first crossed it.
        broken = float(row["close"]) > line_now + tol
        near = abs(float(row["high"]) - line_now) <= tol or abs(float(row["close"]) - line_now) <= tol
        state = "breakout" if broken else ("approaching" if float(row["close"]) < line_now and (near or line_now - float(row["close"]) <= tol * 3) else "watch")
        retest_side = "support"
    else:
        # Keep a broken line marked as broken until price closes back across it.
        broken = float(row["close"]) < line_now - tol
        near = abs(float(row["low"]) - line_now) <= tol or abs(float(row["close"]) - line_now) <= tol
        state = "breakdown" if broken else ("approaching" if float(row["close"]) > line_now and (near or float(row["close"]) - line_now <= tol * 3) else "watch")
        retest_side = "resistance"

    # A retest is only valid after a confirmed close through the line. Find the
    # latest such break first, then inspect only subsequent candles; otherwise an
    # ordinary pre-break touch can be mislabeled as a retest.
    retest = False
    retest_index = None
    scan_start = max(line.last_touch_index + 1, i - retest_lookback_bars)
    prior_breaks = []
    for k in range(scan_start, i):
        current = candles.iloc[k]
        previous = candles.iloc[k - 1]
        value = line.value_at(k)
        previous_value = line.value_at(k - 1)
        if line.kind == "resistance":
            crossed = (
                float(current["close"]) > value + abs(value) * tolerance_pct
                and float(previous["close"]) <= previous_value + abs(previous_value) * tolerance_pct
            )
        else:
            crossed = (
                float(current["close"]) < value - abs(value) * tolerance_pct
                and float(previous["close"]) >= previous_value - abs(previous_value) * tolerance_pct
            )
        if crossed:
            prior_breaks.append(k)

    if prior_breaks:
        break_index = prior_breaks[-1]
        for j in range(break_index + 1, i + 1):
            r = candles.iloc[j]
            v = line.value_at(j)
            if line.kind == "resistance":
                contacted = float(r["low"]) <= v + abs(v) * tolerance_pct
                held = float(r["close"]) >= v - abs(v) * tolerance_pct
                confirmed_side = float(r["close"]) > v
            else:
                contacted = float(r["high"]) >= v - abs(v) * tolerance_pct
                held = float(r["close"]) <= v + abs(v) * tolerance_pct
                confirmed_side = float(r["close"]) < v
            if contacted and held and confirmed_side:
                retest, retest_index = True, j
                break

    return {
        "index": i,
        "as_of_index": i,
        "is_latest": i == len(candles) - 1,
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
