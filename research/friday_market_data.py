"""Kraken OHLC adapter used by Friday and the shared replay tools."""
from __future__ import annotations

from research.validate_kraken_trendlines import fetch_ohlc as fetch_kraken_ohlc


def fetch_ohlc(pair: str = "XBTUSDT", interval: int = 240):
    """Use Kraken's existing spot candles; Friday callers supply USDT-quoted pairs."""
    return fetch_kraken_ohlc(pair=pair.upper().strip(), interval=interval)
