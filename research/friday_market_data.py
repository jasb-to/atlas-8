"""Kraken-only USDT spot OHLC adapter for FRIDAY.

All thesis and execution market data must come from Kraken spot. If Kraken
does not expose a requested pair, propagate the explicit unavailable error;
never substitute candles from another exchange.
"""
from __future__ import annotations

import threading
import time

import pandas as pd

from research.validate_kraken_trendlines import fetch_ohlc as fetch_kraken_ohlc

# Space request starts to stay conservative with Kraken's public API rate limits.
MIN_PUBLIC_REQUEST_INTERVAL_SECONDS = 1.1
_request_lock = threading.Lock()
_last_request_started = 0.0


def _throttle_public_request() -> None:
    global _last_request_started
    with _request_lock:
        now = time.monotonic()
        wait = MIN_PUBLIC_REQUEST_INTERVAL_SECONDS - (now - _last_request_started)
        if wait > 0:
            time.sleep(wait)
        _last_request_started = time.monotonic()


def fetch_ohlc(pair: str = "XBTUSDT", interval: int = 240) -> pd.DataFrame:
    """Fetch completed OHLC candles from Kraken only; never fall back to another venue."""
    symbol = pair.upper().strip()
    _throttle_public_request()
    try:
        return fetch_kraken_ohlc(pair=symbol, interval=interval)
    except RuntimeError as exc:
        if "Unknown asset pair" in str(exc):
            raise RuntimeError(
                f"UNAVAILABLE — NO KRAKEN PAIR: {symbol} (interval={interval}m)"
            ) from exc
        raise
