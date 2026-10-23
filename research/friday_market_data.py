"""USDT-quoted Kraken spot OHLC adapter for FRIDAY's research scanner."""
from __future__ import annotations

from research.validate_kraken_trendlines import fetch_ohlc as fetch_kraken_ohlc


def fetch_ohlc(pair: str = "XBTUSDT", interval: int = 240):
    """Fetch candles from Kraken while refusing USD-quoted FRIDAY markets."""
    symbol = pair.upper().strip()
    if not symbol.endswith("USDT"):
        raise ValueError(f"FRIDAY requires a USDT-quoted Kraken spot symbol, received {pair}")
    return fetch_kraken_ohlc(pair=symbol, interval=interval)
