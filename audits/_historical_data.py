"""Strict Kraken-only historical window adapter for offline FRIDAY audits.

Kraken's public OHLC endpoint has a bounded recent-history window. This adapter
never silently substitutes another exchange or pretends partial history covers
the requested interval: it raises a clear error when the requested window is not
fully represented by the candles returned by the live Kraken feed.
"""
from __future__ import annotations

import pandas as pd

from research.friday_market_data import fetch_ohlc

BAR_MAP = {"1D": 1440, "1W": 10080, "4H": 240}


def _utc(value: str | pd.Timestamp) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    return timestamp.tz_localize("UTC") if timestamp.tzinfo is None else timestamp.tz_convert("UTC")


def fetch_history(
    pair: str,
    bar: str,
    start: str,
    end: str,
    *,
    pause_seconds: float = 0.0,
) -> pd.DataFrame:
    """Return Kraken candles for the entire requested [start, end) window or fail."""
    del pause_seconds  # Retained only for compatibility with existing audit callers.
    if bar not in BAR_MAP:
        raise ValueError(f"Unsupported bar {bar!r}; choose one of {sorted(BAR_MAP)}")
    symbol = str(pair).strip().upper()
    if not symbol.endswith("USDT"):
        raise ValueError("Only USDT-quoted pairs are supported by this audit helper.")
    start_at, end_at = _utc(start), _utc(end)
    if end_at <= start_at:
        raise ValueError("The end of the requested history window must be after its start.")

    frame = fetch_ohlc(pair=symbol, interval=BAR_MAP[bar]).copy()
    if frame.empty or "timestamp" not in frame:
        raise RuntimeError(f"Kraken returned no {bar} candles for {symbol}.")

    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, errors="raise")
    frame = (
        frame.loc[(frame["timestamp"] >= start_at) & (frame["timestamp"] < end_at)]
        .sort_values("timestamp")
        .drop_duplicates("timestamp")
        .reset_index(drop=True)
    )
    if frame.empty:
        raise RuntimeError(
            f"Kraken history window unavailable for {symbol} {bar}: "
            f"no candles in [{start_at.isoformat()}, {end_at.isoformat()})."
        )

    interval = pd.Timedelta(minutes=BAR_MAP[bar])
    first, last = frame["timestamp"].iloc[0], frame["timestamp"].iloc[-1]
    if first > start_at + interval or last + interval < end_at - interval:
        raise RuntimeError(
            f"Incomplete Kraken history for {symbol} {bar}: requested "
            f"[{start_at.isoformat()}, {end_at.isoformat()}), returned "
            f"{len(frame)} candles spanning {first.isoformat()} to {last.isoformat()}. "
            "The audit is blocked rather than using partial history."
        )

    expected = int((end_at - start_at) / interval)
    if len(frame) < expected * 0.98:
        raise RuntimeError(
            f"Incomplete Kraken history for {symbol} {bar}: expected about "
            f"{expected} candles, received {len(frame)}. The audit is blocked."
        )
    frame.attrs["source"] = "Kraken Spot"
    frame.attrs["source_pair"] = symbol
    return frame[["timestamp", "open", "high", "low", "close", "volume"]]
