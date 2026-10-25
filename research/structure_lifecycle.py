"""Causal trendline lifecycle helpers for Friday research.

Rebuild candidate structure from candles available at the decision timestamp.
A line is historical once a closed candle breaks it; it must not remain the
active guide merely because its original anchor pair still exists.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from research.trendlines import Trendline, find_wick_pivots, fit_touch_trendlines


@dataclass(frozen=True)
class StructureSnapshot:
    as_of: str
    active_lines: tuple[dict[str, Any], ...]
    broken_lines: tuple[dict[str, Any], ...]
    latest_support: dict[str, Any] | None
    latest_resistance: dict[str, Any] | None


def _line_break(candles: pd.DataFrame, line: Trendline, *, buffer_pct: float) -> dict[str, Any] | None:
    """Return the first post-confirmation close crossing; never inspect future bars."""
    first = max(line.confirmed_at, line.last_touch_index + 1)
    for i in range(first, len(candles)):
        close = float(candles.iloc[i]["close"])
        value = float(line.value_at(i))
        if line.kind == "resistance":
            crossed = close > value * (1 + buffer_pct)
        else:
            crossed = close < value * (1 - buffer_pct)
        if crossed:
            return {
                "kind": line.kind,
                "break_index": i,
                "break_time": pd.Timestamp(candles.iloc[i]["timestamp"]).isoformat(),
                "line_price": value,
                "close": close,
                "direction": "long" if line.kind == "resistance" else "short",
                "anchor_start_index": line.start.index,
                "anchor_end_index": line.end.index,
                "touch_count": len(line.touch_indices),
            }
    return None


def evaluate_structure(
    candles: pd.DataFrame,
    *,
    lookback: int = 180,
    left_bars: int = 3,
    right_bars: int = 3,
    min_touches: int = 3,
    tolerance_pct: float = 0.0015,
    max_penetration_pct: float = 0.003,
    break_buffer_pct: float = 0.0015,
    max_distance_pct: float = 0.025,
) -> StructureSnapshot:
    """Return current unbroken lines and lines already broken as of the last candle.

    Call once per closed candle. Because the input is truncated at the decision
    timestamp, a later candle cannot influence pivots, line selection, or status.
    """
    required = {"timestamp", "open", "high", "low", "close"}
    if not required.issubset(candles.columns):
        raise ValueError(f"candles missing columns: {sorted(required - set(candles.columns))}")
    if lookback < left_bars + right_bars + 3 or not 0 <= break_buffer_pct < 0.1:
        raise ValueError("invalid structure parameters")
    frame = candles.sort_values("timestamp").tail(lookback).reset_index(drop=True).copy()
    if frame.empty:
        raise ValueError("candles must not be empty")

    pivots = [p for p in find_wick_pivots(frame, left_bars, right_bars)
              if p.confirmed_at <= len(frame) - 1]
    candidates: list[Trendline] = []
    for kind in ("support", "resistance"):
        candidates.extend(fit_touch_trendlines(
            frame, pivots, kind=kind, min_touches=min_touches,
            tolerance_pct=tolerance_pct, max_penetration_pct=max_penetration_pct,
        ))

    close = float(frame.iloc[-1]["close"])
    active: list[dict[str, Any]] = []
    broken: list[dict[str, Any]] = []
    for line in candidates:
        current_value = float(line.value_at(len(frame) - 1))
        distance = abs(current_value - close) / max(abs(close), 1e-12)
        if distance > max_distance_pct:
            continue
        base = {
            "kind": line.kind,
            "line_price": current_value,
            "distance_pct": (close - current_value) / max(abs(current_value), 1e-12) * 100,
            "touch_count": len(line.touch_indices),
            "anchor_start_index": line.start.index,
            "anchor_end_index": line.end.index,
            "anchor_start_time": pd.Timestamp(frame.iloc[line.start.index]["timestamp"]).isoformat(),
            "anchor_end_time": pd.Timestamp(frame.iloc[line.end.index]["timestamp"]).isoformat(),
            "anchor_start_wick": line.start.price,
            "anchor_end_wick": line.end.price,
            "slope_per_bar": line.slope_per_bar,
        }
        crossing = _line_break(frame, line, buffer_pct=break_buffer_pct)
        if crossing is not None:
            broken.append({**base, "status": "broken", "break": crossing})
        else:
            active.append({**base, "status": "active"})

    # Nearest line is the dashboard guide; keep all candidates for analysis.
    active.sort(key=lambda x: (abs(x["distance_pct"]), -x["touch_count"], -x["anchor_end_index"]))
    supports = [x for x in active if x["kind"] == "support"]
    resistances = [x for x in active if x["kind"] == "resistance"]
    broken.sort(key=lambda x: x["break"]["break_index"])
    return StructureSnapshot(
        as_of=pd.Timestamp(frame.iloc[-1]["timestamp"]).isoformat(),
        active_lines=tuple(active),
        broken_lines=tuple(broken),
        latest_support=supports[0] if supports else None,
        latest_resistance=resistances[0] if resistances else None,
    )
