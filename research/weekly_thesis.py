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

EMA_FAST_LENGTH = 8
EMA_SLOW_LENGTH = 21
RSI_LENGTH = 14
STOCH_LENGTH = 14
STOCH_K_SMOOTH = 3
STOCH_D_SMOOTH = 3
WEEKLY_RECENT_CROSS_BARS = 3


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
    weekly_ema8: float | None = None,
    weekly_ema21: float | None = None,
    weekly_ema200: float | None = None,
    weekly_stoch_k: float | None = None,
    weekly_stoch_d: float | None = None,
    weekly_macd_histogram: float | None = None,
    daily_close: float | None = None,
    daily_ema8: float | None = None,
    daily_ema21: float | None = None,
    daily_ema50: float | None = None,
    daily_stoch_k: float | None = None,
    daily_stoch_d: float | None = None,
    macro_regime: str | None = None,
    wave_state: str | None = None,
    momentum_bearish: bool = False,
    momentum_bullish: bool = False,
    daily_alignment: str = "unavailable",
    weekly_stoch_cross: bool = False,
    weekly_stoch_aligned: bool = False,
    data_source_weekly: str = "unknown",
    data_source_daily: str = "unknown",
) -> dict[str, Any]:
    """Return a stable, flat schema for active, vetoed and unavailable theses."""
    return {
        "pair": pair.upper(),
        "status": status,
        "direction": direction,
        "macro_regime": macro_regime,
        "wave_state": wave_state,
        "momentum_bearish": bool(momentum_bearish),
        "momentum_bullish": bool(momentum_bullish),
        "weekly_close": weekly_close,
        "weekly_ema8": weekly_ema8,
        "weekly_ema21": weekly_ema21,
        "weekly_ema200": weekly_ema200,
        "weekly_stoch_k": weekly_stoch_k,
        "weekly_stoch_d": weekly_stoch_d,
        "weekly_macd_histogram": weekly_macd_histogram,
        "daily_close": daily_close,
        "daily_ema8": daily_ema8,
        "daily_ema21": daily_ema21,
        "daily_ema50": daily_ema50,
        "daily_stoch_k": daily_stoch_k,
        "daily_stoch_d": daily_stoch_d,
        "daily_alignment": daily_alignment,
        "weekly_stoch_cross": bool(weekly_stoch_cross),
        "weekly_stoch_aligned": bool(weekly_stoch_aligned),
        "data_source_weekly": data_source_weekly,
        "data_source_daily": data_source_daily,
        "reason": reason,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
    }


def _macd_histogram(close: pd.Series) -> pd.Series:
    """MACD histogram (12, 26, 9), calculated only from the supplied closes."""
    fast = close.ewm(span=12, adjust=False, min_periods=12).mean()
    slow = close.ewm(span=26, adjust=False, min_periods=26).mean()
    macd = fast - slow
    signal = macd.ewm(span=9, adjust=False, min_periods=9).mean()
    return macd - signal


def evaluate_sheldon_thesis(
    pair: str,
    *,
    weekly_candles: pd.DataFrame | None = None,
    daily_candles: pd.DataFrame | None = None,
    fetcher: Callable[..., pd.DataFrame] = fetch_ohlc,
    as_of: datetime | pd.Timestamp | None = None,
) -> dict[str, Any]:
    """Evaluate the 3-State Wave Model using completed weekly/daily candles only.

    Macro regime is weekly close versus EMA 200. Weekly momentum is Stoch RSI
    K/D with MACD histogram as an alternative signal. Bullish macro + bearish
    momentum is a SHORT pullback; bearish macro + bullish momentum is a LONG
    pullback. Daily close versus EMA 50 is the direction veto.
    """
    now = pd.Timestamp(as_of or datetime.now(timezone.utc))
    now = now.tz_localize("UTC") if now.tzinfo is None else now.tz_convert("UTC")

    weekly_raw = weekly_candles if weekly_candles is not None else fetcher(pair=pair, interval=10080)
    daily_raw = daily_candles if daily_candles is not None else fetcher(pair=pair, interval=1440)
    weekly = _completed_candles(weekly_raw, interval_minutes=10080, as_of=now)
    daily = _completed_candles(daily_raw, interval_minutes=1440, as_of=now)

    wc = _number(weekly["close"].iloc[-1]) if not weekly.empty else None
    dc = _number(daily["close"].iloc[-1]) if not daily.empty else None
    if len(weekly) < 200 or len(daily) < 50:
        return _flat_result(
            pair, status="insufficient_data", direction=None,
            reason=(f"Need at least 200 completed weekly and 50 completed daily candles; "
                    f"available weekly={len(weekly)}, daily={len(daily)}."),
            weekly_close=wc, daily_close=dc,
            data_source_weekly=str(weekly_raw.attrs.get("source", "unknown")),
            data_source_daily=str(daily_raw.attrs.get("source", "unknown")),
        )

    weekly_close = weekly["close"].astype(float)
    daily_close = daily["close"].astype(float)
    weekly_ema8 = weekly_close.ewm(span=EMA_FAST_LENGTH, adjust=False, min_periods=EMA_FAST_LENGTH).mean()
    weekly_ema21 = weekly_close.ewm(span=EMA_SLOW_LENGTH, adjust=False, min_periods=EMA_SLOW_LENGTH).mean()
    weekly_ema200 = weekly_close.ewm(span=200, adjust=False, min_periods=200).mean()
    daily_ema8 = daily_close.ewm(span=EMA_FAST_LENGTH, adjust=False, min_periods=EMA_FAST_LENGTH).mean()
    daily_ema21 = daily_close.ewm(span=EMA_SLOW_LENGTH, adjust=False, min_periods=EMA_SLOW_LENGTH).mean()
    daily_ema50 = daily_close.ewm(span=50, adjust=False, min_periods=50).mean()
    weekly_k, weekly_d = calculate_stoch_rsi(weekly_close)
    daily_k, daily_d = calculate_stoch_rsi(daily_close)
    weekly_macd_hist = _macd_histogram(weekly_close)

    w_close = float(weekly_close.iloc[-1])
    w_ema8, w_ema21 = _number(weekly_ema8.iloc[-1]), _number(weekly_ema21.iloc[-1])
    w_ema200 = _number(weekly_ema200.iloc[-1])
    d_close = float(daily_close.iloc[-1])
    d_ema8, d_ema21 = _number(daily_ema8.iloc[-1]), _number(daily_ema21.iloc[-1])
    d_ema50 = _number(daily_ema50.iloc[-1])
    w_k, w_d = _number(weekly_k.iloc[-1]), _number(weekly_d.iloc[-1])
    d_k, d_d = _number(daily_k.iloc[-1]), _number(daily_d.iloc[-1])
    w_macd_hist = _number(weekly_macd_hist.iloc[-1])

    bull_cross = _cross_recently_up(weekly_k, weekly_d, WEEKLY_RECENT_CROSS_BARS)
    bear_cross = _cross_recently_down(weekly_k, weekly_d, WEEKLY_RECENT_CROSS_BARS)

    def result(status: str, direction: str | None, reason: str, *,
               macro: str | None = None, wave: str | None = None,
               bearish: bool = False, bullish: bool = False,
               daily_state: str = "unavailable") -> dict[str, Any]:
        return _flat_result(
            pair, status=status, direction=direction, reason=reason,
            weekly_close=w_close, weekly_ema8=w_ema8, weekly_ema21=w_ema21,
            weekly_ema200=w_ema200, weekly_stoch_k=w_k, weekly_stoch_d=w_d,
            weekly_macd_histogram=w_macd_hist,
            daily_close=d_close, daily_ema8=d_ema8, daily_ema21=d_ema21,
            daily_ema50=d_ema50, daily_stoch_k=d_k, daily_stoch_d=d_d,
            macro_regime=macro, wave_state=wave,
            momentum_bearish=bearish, momentum_bullish=bullish,
            daily_alignment=daily_state,
            weekly_stoch_cross=bool(bull_cross or bear_cross),
            weekly_stoch_aligned=bool(w_k is not None and w_d is not None and w_k > w_d),
            data_source_weekly=str(weekly_raw.attrs.get("source", "unknown")),
            data_source_daily=str(daily_raw.attrs.get("source", "unknown")),
        )

    if None in (w_ema200, d_ema50, w_macd_hist):
        return result("insufficient_data", None,
                      "Weekly EMA 200, Stoch RSI/MACD, or daily EMA 50 is not valid.")

    macro_bull = w_close > w_ema200
    macro_bear = w_close < w_ema200
    if not macro_bull and not macro_bear:
        return result("inactive", None, "Weekly close equals the 200 EMA; no macro regime.")

    # OR-based momentum signals can overlap when Stoch RSI and MACD disagree.
    # Resolve that conflict deterministically in favour of Stoch RSI K/D; use
    # MACD histogram when K and D are equal. This avoids an impossible dual state.
    stoch_bear = (w_k < w_d) if w_k is not None and w_d is not None else (w_macd_hist < 0)
    stoch_bull = (w_k > w_d) if w_k is not None and w_d is not None else (w_macd_hist > 0)
    macd_bear = w_macd_hist < 0
    macd_bull = w_macd_hist > 0
    momentum_bearish = stoch_bear or macd_bear
    momentum_bullish = stoch_bull or macd_bull
    if stoch_bear != stoch_bull:
        chosen_bearish = stoch_bear
    elif macd_bear != macd_bull:
        chosen_bearish = macd_bear
    else:
        return result("inactive", None, "Weekly momentum is neutral; no impulse or pullback state.",
                      macro="bullish" if macro_bull else "bearish",
                      bearish=momentum_bearish, bullish=momentum_bullish)

    if macro_bull and not chosen_bearish:
        thesis_direction, wave_state = "long", "impulse"
    elif macro_bull and chosen_bearish:
        thesis_direction, wave_state = "short", "pullback"
    elif macro_bear and chosen_bearish:
        thesis_direction, wave_state = "short", "impulse"
    else:
        thesis_direction, wave_state = "long", "pullback"

    daily_aligned = d_close < d_ema50 if thesis_direction == "short" else d_close > d_ema50
    daily_state = "pass" if daily_aligned else "veto"
    macro = "bullish" if macro_bull else "bearish"
    if not daily_aligned:
        return result(
            "veto", thesis_direction,
            "Daily conflicts with Weekly Wave",
            macro=macro, wave=wave_state, bearish=momentum_bearish,
            bullish=momentum_bullish, daily_state=daily_state,
        )
    return result(
        "active", thesis_direction,
        f"Macro {macro}; {wave_state} wave ({thesis_direction}); daily close aligned with EMA 50.",
        macro=macro, wave=wave_state, bearish=momentum_bearish,
        bullish=momentum_bullish, daily_state=daily_state,
    )
