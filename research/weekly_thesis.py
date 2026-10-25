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


def _inactive(status: str, reason: str, *, pair: str, weekly: pd.DataFrame,
              daily: pd.DataFrame) -> dict[str, Any]:
    return {
        "schema_version": "sheldon-weekly-daily-v1",
        "pair": pair.upper(),
        "status": status,
        "direction": "neutral",
        "reason": reason,
        "weekly_last_closed_candle_utc": weekly.iloc[-1]["timestamp"].isoformat() if not weekly.empty else None,
        "daily_last_closed_candle_utc": daily.iloc[-1]["timestamp"].isoformat() if not daily.empty else None,
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
    """Evaluate a weekly 200-SMA/Stoch RSI regime plus daily alignment/veto.

    Rule set is provisional and parameterized by module constants. The weekly
    Stoch RSI accepts either a fresh extreme-zone cross or already-aligned K/D,
    matching the requested initial specification. The daily accepts K/D
    alignment or a cross within the last three completed daily candles.
    """
    now = pd.Timestamp(as_of or datetime.now(timezone.utc))
    now = now.tz_localize("UTC") if now.tzinfo is None else now.tz_convert("UTC")

    weekly_raw = weekly_candles if weekly_candles is not None else fetcher(pair=pair, interval=10080)
    daily_raw = daily_candles if daily_candles is not None else fetcher(pair=pair, interval=1440)
    weekly = _completed_candles(weekly_raw, interval_minutes=10080, as_of=now)
    daily = _completed_candles(daily_raw, interval_minutes=1440, as_of=now)

    if len(weekly) < SMA_LENGTH or len(daily) < SMA_LENGTH:
        return _inactive(
            "data_insufficient",
            f"Need at least {SMA_LENGTH} completed weekly and daily candles for the 200-SMA filter.",
            pair=pair, weekly=weekly, daily=daily,
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

    if None in (w_sma, d_sma, w_k, w_d, d_k, d_d):
        return _inactive(
            "data_insufficient", "The 200-SMA or Stoch RSI values are not yet valid.",
            pair=pair, weekly=weekly, daily=daily,
        )

    w_k_prev, w_d_prev = _number(weekly_k.iloc[-2]), _number(weekly_d.iloc[-2])
    weekly_bull_cross = (
        w_k_prev is not None and w_d_prev is not None
        and w_k_prev <= w_d_prev and w_k > w_d and w_k < 20
    )
    weekly_bear_cross = (
        w_k_prev is not None and w_d_prev is not None
        and w_k_prev >= w_d_prev and w_k < w_d and w_k > 80
    )
    weekly_bullish = w_close > w_sma
    weekly_bearish = w_close < w_sma

    if weekly_bullish and (weekly_bull_cross or w_k > w_d):
        direction = "long"
    elif weekly_bearish and (weekly_bear_cross or w_k < w_d):
        direction = "short"
    else:
        return {
            **_inactive(
                "inactive", "Weekly 200-SMA trend and Stoch RSI direction are not aligned.",
                pair=pair, weekly=weekly, daily=daily,
            ),
            "weekly": {
                "close": w_close, "sma200": w_sma, "stoch_k": w_k, "stoch_d": w_d,
                "trend": "bullish" if weekly_bullish else "bearish" if weekly_bearish else "neutral",
                "stoch_extreme_cross": bool(weekly_bull_cross or weekly_bear_cross),
            },
        }

    daily_bullish = d_close > d_sma and (d_k > d_d or _cross_recently_up(daily_k, daily_d, DAILY_RECENT_CROSS_BARS))
    daily_bearish = d_close < d_sma and (d_k < d_d or _cross_recently_down(daily_k, daily_d, DAILY_RECENT_CROSS_BARS))
    aligned = daily_bullish if direction == "long" else daily_bearish
    if not aligned:
        return {
            "schema_version": "sheldon-weekly-daily-v1",
            "pair": pair.upper(),
            "status": "veto",
            "direction": direction,
            "reason": f"Weekly {direction} regime, but daily 200-SMA/Stoch RSI alignment failed.",
            "weekly_last_closed_candle_utc": weekly.iloc[-1]["timestamp"].isoformat(),
            "daily_last_closed_candle_utc": daily.iloc[-1]["timestamp"].isoformat(),
            "weekly": {"close": w_close, "sma200": w_sma, "stoch_k": w_k, "stoch_d": w_d},
            "daily": {"close": d_close, "sma200": d_sma, "stoch_k": d_k, "stoch_d": d_d},
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        }

    # Stable ID remains unchanged while price stays on the same side of its weekly
    # 200-SMA regime boundary; it does not churn every week.
    side = np.sign(weekly_close.to_numpy() - weekly_sma.to_numpy())
    valid_indices = np.flatnonzero(np.isfinite(weekly_sma.to_numpy()))
    regime_start = int(valid_indices[0])
    for idx in valid_indices[1:]:
        if side[idx] != side[idx - 1]:
            regime_start = int(idx)
    regime_timestamp = weekly.iloc[regime_start]["timestamp"].isoformat()
    stable_seed = f"{pair.upper()}|{direction}|{regime_timestamp}|sheldon-weekly-daily-v1"
    thesis_id = hashlib.sha256(stable_seed.encode()).hexdigest()[:20]

    return {
        "schema_version": "sheldon-weekly-daily-v1",
        "thesis_id": thesis_id,
        "pair": pair.upper(),
        "status": "active",
        "direction": direction,
        "reason": f"Weekly close {'above' if direction == 'long' else 'below'} 200-SMA with Stoch RSI aligned; daily 200-SMA and Stoch RSI aligned.",
        "weekly_last_closed_candle_utc": weekly.iloc[-1]["timestamp"].isoformat(),
        "daily_last_closed_candle_utc": daily.iloc[-1]["timestamp"].isoformat(),
        "regime_start_utc": regime_timestamp,
        "invalidation_level": w_sma,
        "weekly": {
            "close": w_close, "sma200": w_sma, "stoch_k": w_k, "stoch_d": w_d,
            "trend": "bullish" if direction == "long" else "bearish",
            "extreme_cross": bool(weekly_bull_cross if direction == "long" else weekly_bear_cross),
        },
        "daily": {
            "close": d_close, "sma200": d_sma, "stoch_k": d_k, "stoch_d": d_d,
            "trend": "bullish" if direction == "long" else "bearish",
            "recent_cross": bool(
                _cross_recently_up(daily_k, daily_d, DAILY_RECENT_CROSS_BARS)
                if direction == "long" else _cross_recently_down(daily_k, daily_d, DAILY_RECENT_CROSS_BARS)
            ),
        },
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
    }
