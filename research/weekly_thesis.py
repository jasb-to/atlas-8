"""Provisional Sheldon-style weekly/daily regime filter for FRIDAY.

This module is deliberately isolated from the live monitor. It is research-only:
callers can evaluate a pair, inspect the reasons, and backtest the rules before
wiring the returned thesis into 4H execution. Only completed candles are used.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from typing import Any, Callable, Mapping

import numpy as np
import pandas as pd

from research.friday_market_data import fetch_ohlc

SMA_LENGTH = 200
RSI_LENGTH = 14
STOCH_LENGTH = 14
STOCH_K_SMOOTH = 3
STOCH_D_SMOOTH = 3
DAILY_RECENT_CROSS_BARS = 3


def calculate_stoch_rsi(
    close: pd.Series,
    *,
    length: int = STOCH_LENGTH,
    rsi_length: int = RSI_LENGTH,
    k: int = STOCH_K_SMOOTH,
    d: int = STOCH_D_SMOOTH,
) -> tuple[pd.Series, pd.Series]:
    """Calculate Stoch RSI (14, 14, 3, 3) on a close series."""
    close = pd.to_numeric(close, errors="coerce").astype(float)
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / rsi_length, adjust=False, min_periods=rsi_length).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / rsi_length, adjust=False, min_periods=rsi_length).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    rolling_low = rsi.rolling(length, min_periods=length).min()
    rolling_high = rsi.rolling(length, min_periods=length).max()
    denominator = (rolling_high - rolling_low).replace(0, np.nan)
    raw_stoch = 100 * (rsi - rolling_low) / denominator
    stoch_k = raw_stoch.rolling(k, min_periods=k).mean()
    stoch_d = stoch_k.rolling(d, min_periods=d).mean()
    return stoch_k, stoch_d


def _completed_candles(
    candles: pd.DataFrame,
    *,
    interval_minutes: int,
    as_of: pd.Timestamp,
) -> pd.DataFrame:
    """Sort candles and remove any candle whose close time is still in the future."""
    if not isinstance(candles, pd.DataFrame) or candles.empty:
        return pd.DataFrame(columns=["timestamp", "close"])
    required = {"timestamp", "close"}
    missing = required.difference(candles.columns)
    if missing:
        raise ValueError(f"OHLC data is missing required columns: {sorted(missing)}")
    frame = candles.copy()
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, errors="coerce")
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    frame = frame.dropna(subset=["timestamp", "close"])
    frame = frame.sort_values("timestamp").drop_duplicates("timestamp", keep="last")
    candle_duration = pd.Timedelta(minutes=interval_minutes)
    frame = frame.loc[frame["timestamp"] + candle_duration <= as_of]
    return frame.reset_index(drop=True)


def _cross_recently_up(k: pd.Series, d: pd.Series, bars: int) -> bool:
    start = max(1, len(k) - bars)
    for index in range(start, len(k)):
        values = (k.iloc[index - 1], d.iloc[index - 1], k.iloc[index], d.iloc[index])
        if all(np.isfinite(value) for value in values):
            if values[0] <= values[1] and values[2] > values[3]:
                return True
    return False


def _cross_recently_down(k: pd.Series, d: pd.Series, bars: int) -> bool:
    start = max(1, len(k) - bars)
    for index in range(start, len(k)):
        values = (k.iloc[index - 1], d.iloc[index - 1], k.iloc[index], d.iloc[index])
        if all(np.isfinite(value) for value in values):
            if values[0] >= values[1] and values[2] < values[3]:
                return True
    return False


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if np.isfinite(result) else None


def _flat_result(
    pair: str,
    *,
    status: str,
    direction: str | None,
    reason: str,
    weekly_close: float | None = None,
    weekly_sma: float | None = None,
    weekly_stoch_k: float | None = None,
    weekly_stoch_d: float | None = None,
    daily_close: float | None = None,
    daily_sma: float | None = None,
    daily_stoch_k: float | None = None,
    daily_stoch_d: float | None = None,
    weekly_stoch_cross: bool = False,
    weekly_stoch_aligned: bool = False,
) -> dict[str, Any]:
    """Return the same flat schema on every evaluator path."""
    return {
        "pair": pair.upper(),
        "status": status,
        "direction": direction,
        "weekly_close": weekly_close,
        "weekly_sma": weekly_sma,
        "weekly_stoch_k": weekly_stoch_k,
        "weekly_stoch_d": weekly_stoch_d,
        "daily_close": daily_close,
        "daily_sma": daily_sma,
        "daily_stoch_k": daily_stoch_k,
        "daily_stoch_d": daily_stoch_d,
        "weekly_stoch_cross": bool(weekly_stoch_cross),
        "weekly_stoch_aligned": bool(weekly_stoch_aligned),
        "reason": reason,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
    }


def evaluate_sheldon_thesis(
    pair: str,
    *,
    weekly_candles: pd.DataFrame | None = None,
    daily_candles: pd.DataFrame | None = None,
    fetcher: Callable[..., pd.DataFrame] = fetch_ohlc,
    as_of: datetime | pd.Timestamp | None = None,
) -> dict[str, Any]:
    """Evaluate completed weekly regime and completed-daily alignment.

    Weekly cross (a fresh cross in the extreme zone) and weekly aligned state
    are deliberately reported separately. Every return has the same flat schema.
    """
    now = pd.Timestamp(as_of or datetime.now(timezone.utc))
    now = now.tz_localize("UTC") if now.tzinfo is None else now.tz_convert("UTC")

    weekly_raw = weekly_candles if weekly_candles is not None else fetcher(pair=pair, interval=10080)
    daily_raw = daily_candles if daily_candles is not None else fetcher(pair=pair, interval=1440)
    weekly = _completed_candles(weekly_raw, interval_minutes=10080, as_of=now)
    daily = _completed_candles(daily_raw, interval_minutes=1440, as_of=now)

    wc = _number(weekly["close"].iloc[-1]) if not weekly.empty else None
    dc = _number(daily["close"].iloc[-1]) if not daily.empty else None
    if len(weekly) < 201 or len(daily) < 201:
        return _flat_result(
            pair, status="insufficient_data", direction=None,
            reason=(f"Need at least 201 completed weekly and daily candles; "
                    f"available weekly={len(weekly)}, daily={len(daily)}."),
            weekly_close=wc, daily_close=dc,
        )

    weekly_close = weekly["close"].astype(float)
    daily_close = daily["close"].astype(float)
    weekly_sma = weekly_close.rolling(SMA_LENGTH, min_periods=SMA_LENGTH).mean()
    daily_sma = daily_close.rolling(SMA_LENGTH, min_periods=SMA_LENGTH).mean()
    weekly_k, weekly_d = calculate_stoch_rsi(weekly_close)
    daily_k, daily_d = calculate_stoch_rsi(daily_close)

    w_close, w_sma = float(weekly_close.iloc[-1]), _number(weekly_sma.iloc[-1])
    d_close, d_sma = float(daily_close.iloc[-1]), _number(daily_sma.iloc[-1])
    w_k, w_d = _number(weekly_k.iloc[-1]), _number(weekly_d.iloc[-1])
    d_k, d_d = _number(daily_k.iloc[-1]), _number(daily_d.iloc[-1])

    def result(status: str, direction: str | None, reason: str,
               cross: bool = False, aligned: bool = False) -> dict[str, Any]:
        return _flat_result(
            pair, status=status, direction=direction, reason=reason,
            weekly_close=w_close, weekly_sma=w_sma,
            weekly_stoch_k=w_k, weekly_stoch_d=w_d,
            daily_close=d_close, daily_sma=d_sma,
            daily_stoch_k=d_k, daily_stoch_d=d_d,
            weekly_stoch_cross=cross, weekly_stoch_aligned=aligned,
        )

    if None in (w_sma, d_sma, w_k, w_d, d_k, d_d):
        return result("insufficient_data", None, "The 200-SMA or Stoch RSI values are not yet valid.")

    w_k_prev, w_d_prev = _number(weekly_k.iloc[-2]), _number(weekly_d.iloc[-2])
    bull_cross = (
        w_k_prev is not None and w_d_prev is not None
        and w_k_prev <= w_d_prev and w_k > w_d and w_k < 20
    )
    bear_cross = (
        w_k_prev is not None and w_d_prev is not None
        and w_k_prev >= w_d_prev and w_k < w_d and w_k > 80
    )
    bull_aligned = w_k > w_d
    bear_aligned = w_k < w_d
    weekly_bullish, weekly_bearish = w_close > w_sma, w_close < w_sma

    if weekly_bullish and (bull_cross or bull_aligned):
        direction, cross, aligned = "long", bool(bull_cross), bool(bull_aligned)
    elif weekly_bearish and (bear_cross or bear_aligned):
        direction, cross, aligned = "short", bool(bear_cross), bool(bear_aligned)
    else:
        return result(
            "inactive", None,
            "Weekly 200-SMA trend and Stoch RSI direction are not aligned.",
            bool(bull_cross or bear_cross), bool(bull_aligned or bear_aligned),
        )

    daily_bullish = d_close > d_sma and (
        d_k > d_d or _cross_recently_up(daily_k, daily_d, DAILY_RECENT_CROSS_BARS)
    )
    daily_bearish = d_close < d_sma and (
        d_k < d_d or _cross_recently_down(daily_k, daily_d, DAILY_RECENT_CROSS_BARS)
    )
    daily_aligned = daily_bullish if direction == "long" else daily_bearish
    if not daily_aligned:
        return result(
            "veto", direction,
            f"Weekly {direction} regime, but completed daily 200-SMA/Stoch RSI alignment failed.",
            cross, aligned,
        )
    return result(
        "active", direction,
        f"Weekly {direction} regime aligned with completed daily 200-SMA and Stoch RSI.",
        cross, aligned,
    )
