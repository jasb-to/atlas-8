"""Causal reversal/exhaustion context for Friday (research-only).

This module scores evidence of a possible directional turn without making it a
hard entry gate. Every feature uses the current and prior completed candles only;
no future pivots or hindsight labels are used in the live context.
"""
from __future__ import annotations

from typing import Any
import numpy as np
import pandas as pd


def _finite(value: Any) -> float | None:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if np.isfinite(value) else None


def reversal_context(frame: pd.DataFrame, *, lookback: int = 12) -> dict[str, Any]:
    """Return latest candle's reversal context and transparent evidence.

    Exhaustion is context, not a standalone signal. A candidate turn is stronger
    when a stretched move loses momentum and the candle closes back through a
    short EMA or breaks the previous candle's reaction extreme.
    """
    required = {"open", "high", "low", "close"}
    if not required.issubset(frame.columns):
        raise ValueError(f"candles missing columns: {sorted(required - set(frame.columns))}")
    if lookback < 4:
        raise ValueError("lookback must be at least 4")
    if len(frame) < lookback + 3:
        return {
            "status": "insufficient_history",
            "bars_required": lookback + 3,
            "bars_available": len(frame),
            "long_reversal_score": 0,
            "short_reversal_score": 0,
            "evidence": [],
        }

    data = frame.sort_values("timestamp").reset_index(drop=True) if "timestamp" in frame else frame.reset_index(drop=True)
    close = data.close.astype(float)
    high = data.high.astype(float)
    low = data.low.astype(float)
    open_ = data.open.astype(float)
    i = len(data) - 1
    tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    ema8 = close.ewm(span=8, adjust=False).mean()
    ema21 = close.ewm(span=21, adjust=False).mean()
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    rsi = 100 - 100 / (1 + gain / loss.replace(0, np.nan))

    current_atr = _finite(atr.iloc[i])
    current_close = float(close.iloc[i])
    evidence_long: list[str] = []
    evidence_short: list[str] = []
    if current_atr and current_atr > 0:
        prior = slice(max(0, i - lookback), i)
        prior_high = float(high.iloc[prior].max())
        prior_low = float(low.iloc[prior].min())
        # Extension is measured against the prior completed window, excluding
        # the current candle so the latest candle cannot define its own stretch.
        if current_close <= prior_low - 1.25 * current_atr:
            evidence_long.append("downside_extension_gt_1_25_atr")
        if current_close >= prior_high + 1.25 * current_atr:
            evidence_short.append("upside_extension_gt_1_25_atr")

    # Momentum deceleration: compare current RSI to three bars earlier. This is
    # a simple observable clue, not a claim of formal price/indicator divergence.
    rsi_now, rsi_prev = _finite(rsi.iloc[i]), _finite(rsi.iloc[i - 3])
    if rsi_now is not None and rsi_prev is not None:
        if rsi_now > rsi_prev + 3:
            evidence_long.append("rsi_recovering")
        if rsi_now < rsi_prev - 3:
            evidence_short.append("rsi_fading")

    candle_range = max(float(high.iloc[i] - low.iloc[i]), 1e-12)
    lower_wick = min(float(open_.iloc[i]), current_close) - float(low.iloc[i])
    upper_wick = float(high.iloc[i]) - max(float(open_.iloc[i]), current_close)
    if lower_wick / candle_range >= 0.4 and current_close > float(low.iloc[i]) + candle_range * 0.6:
        evidence_long.append("lower_wick_rejection")
    if upper_wick / candle_range >= 0.4 and current_close < float(low.iloc[i]) + candle_range * 0.4:
        evidence_short.append("upper_wick_rejection")

    if current_close > float(high.iloc[i - 1]):
        evidence_long.append("closed_above_previous_candle_high")
    if current_close < float(low.iloc[i - 1]):
        evidence_short.append("closed_below_previous_candle_low")
    if current_close > float(ema8.iloc[i]) and float(ema8.iloc[i]) <= float(ema8.iloc[i - 2]):
        evidence_long.append("close_reclaimed_ema8_while_ema_was_flat_or_falling")
    if current_close < float(ema8.iloc[i]) and float(ema8.iloc[i]) >= float(ema8.iloc[i - 2]):
        evidence_short.append("close_lost_ema8_while_ema_was_flat_or_rising")

    # Scores are evidence counts, deliberately not calibrated probabilities.
    long_score, short_score = len(evidence_long), len(evidence_short)
    if long_score >= 2 and long_score > short_score:
        state = "possible_bullish_turn"
    elif short_score >= 2 and short_score > long_score:
        state = "possible_bearish_turn"
    elif long_score or short_score:
        state = "mixed_early_reversal_evidence"
    else:
        state = "no_clear_reversal_evidence"

    return {
        "status": "ok",
        "as_of": data.iloc[i].timestamp.isoformat() if "timestamp" in data and hasattr(data.iloc[i].timestamp, "isoformat") else str(i),
        "state": state,
        "long_reversal_score": long_score,
        "short_reversal_score": short_score,
        "score_note": "Evidence count only; not a probability and not an entry gate.",
        "atr14": _finite(current_atr),
        "rsi14": _finite(rsi_now),
        "ema8": _finite(ema8.iloc[i]),
        "ema21": _finite(ema21.iloc[i]),
        "evidence": {"bullish": evidence_long, "bearish": evidence_short},
        "use_policy": "Context for Friday review; require a closed-candle structural break/retest for confirmation.",
    }
