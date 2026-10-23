"""Kraken-first USDT spot OHLC adapter for FRIDAY.

Kraken remains the primary source. If a USDT market is not exposed by Kraken's
REST OHLC endpoint (currently HYPE/USDT), fall back to Binance's market-data-only
endpoint for that same USDT symbol rather than silently switching to USD.
"""
from __future__ import annotations

import pandas as pd
import requests

from research.validate_kraken_trendlines import fetch_ohlc as fetch_kraken_ohlc

BINANCE_KLINES_URL = "https://data-api.binance.vision/api/v3/klines"
INTERVALS = {1: "1m", 5: "5m", 15: "15m", 30: "30m", 60: "1h", 240: "4h", 1440: "1d", 10080: "1w"}


def _fetch_binance_ohlc(pair: str, interval: int) -> pd.DataFrame:
    symbol = pair.upper()
    if symbol.startswith("XBT"):
        symbol = "BTC" + symbol[3:]
    interval_name = INTERVALS.get(int(interval))
    if not interval_name:
        raise ValueError(f"Unsupported Binance candle interval: {interval} minutes")
    response = requests.get(
        BINANCE_KLINES_URL,
        params={"symbol": symbol, "interval": interval_name, "limit": 720},
        timeout=20,
        headers={"User-Agent": "atlas-8-friday-research/1.0"},
    )
    response.raise_for_status()
    payload = response.json()
    if isinstance(payload, dict) and payload.get("code") is not None:
        raise RuntimeError(f"Binance OHLC error for {symbol}: {payload.get('msg', payload.get('code'))}")
    if not isinstance(payload, list) or not payload:
        raise RuntimeError(f"No Binance OHLC rows returned for {symbol}")
    frame = pd.DataFrame(payload, columns=[
        "open_time", "open", "high", "low", "close", "volume", "close_time",
        "quote_volume", "trades", "taker_base_volume", "taker_quote_volume", "ignore",
    ])
    frame["timestamp"] = pd.to_datetime(pd.to_numeric(frame["open_time"]), unit="ms", utc=True)
    for col in ("open", "high", "low", "close", "volume", "quote_volume"):
        frame[col] = pd.to_numeric(frame[col], errors="raise")
    frame["vwap"] = frame["quote_volume"] / frame["volume"].replace(0, float("nan"))
    frame["trades"] = pd.to_numeric(frame["trades"], errors="raise").astype(int)
    now_ms = int(pd.Timestamp.now(tz="UTC").timestamp() * 1000)
    frame = frame.loc[pd.to_numeric(frame["close_time"]) < now_ms].copy()
    frame = frame.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)
    if len(frame) < 50:
        raise RuntimeError(f"Only {len(frame)} completed Binance candles available for {symbol}; refusing to scan")
    return frame[["timestamp", "open", "high", "low", "close", "vwap", "volume", "trades"]]


def fetch_ohlc(pair: str = "XBTUSDT", interval: int = 240):
    """Use Kraken where the pair exists; only fall back for missing USDT pairs."""
    symbol = pair.upper().strip()
    try:
        return fetch_kraken_ohlc(pair=symbol, interval=interval)
    except RuntimeError as exc:
        if not symbol.endswith("USDT") or "Unknown asset pair" not in str(exc):
            raise
        return _fetch_binance_ohlc(symbol, interval)
