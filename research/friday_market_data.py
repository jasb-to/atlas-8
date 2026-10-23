"""Kraken-first USDT spot OHLC adapter for FRIDAY.

Kraken remains the primary source. If Kraken does not expose a requested USDT
spot pair, use OKX's public market-data API for that same USDT pair. OKX's
HYPE/USDT spot listing has enough history for both 4H and daily analysis.
"""
from __future__ import annotations

import threading
import time

import pandas as pd
import requests

from research.validate_kraken_trendlines import fetch_ohlc as fetch_kraken_ohlc

OKX_CANDLES_URL = "https://www.okx.com/api/v5/market/candles"

# Space request starts to stay conservative with public-market-data rate limits.
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


OKX_INTERVALS = {1: "1m", 5: "5m", 15: "15m", 30: "30m", 60: "1H", 120: "2H", 240: "4H", 360: "6H", 720: "12H", 1440: "1Dutc", 10080: "1W"}


def _fetch_okx_ohlc(pair: str, interval: int) -> pd.DataFrame:
    symbol = pair.upper()
    if not symbol.endswith("USDT"):
        raise ValueError(f"OKX fallback only supports USDT-quoted spot symbols: {pair}")
    base = symbol[:-4]
    if base == "XBT":
        base = "BTC"
    bar = OKX_INTERVALS.get(int(interval))
    if not bar:
        raise ValueError(f"Unsupported OKX candle interval: {interval} minutes")
    _throttle_public_request()
    response = requests.get(
        OKX_CANDLES_URL,
        params={"instId": f"{base}-USDT", "bar": bar, "limit": 300},
        timeout=20,
        headers={"User-Agent": "atlas-8-friday-research/1.0"},
    )
    response.raise_for_status()
    body = response.json()
    if not isinstance(body, dict) or body.get("code") != "0":
        detail = body.get("msg", body.get("code")) if isinstance(body, dict) else "invalid response"
        raise RuntimeError(f"OKX OHLC error for {base}-USDT: {detail}")
    rows = body.get("data")
    if not isinstance(rows, list) or not rows:
        raise RuntimeError(f"No OKX OHLC rows returned for {base}-USDT")
    frame = pd.DataFrame(rows, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "quote_volume", "quote_volume_alt", "confirm",
    ])
    frame["timestamp"] = pd.to_datetime(pd.to_numeric(frame["open_time"]), unit="ms", utc=True)
    for col in ("open", "high", "low", "close", "volume", "quote_volume"):
        frame[col] = pd.to_numeric(frame[col], errors="raise")
    frame["vwap"] = frame["quote_volume"] / frame["volume"].replace(0, float("nan"))
    frame["trades"] = float("nan")
    frame = frame.loc[frame["confirm"].astype(str) == "1"].copy()
    frame = frame.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)
    if len(frame) < 50:
        raise RuntimeError(f"Only {len(frame)} completed OKX candles available for {base}-USDT; refusing to scan")
    return frame[["timestamp", "open", "high", "low", "close", "vwap", "volume", "trades"]]


def fetch_ohlc(pair: str = "XBTUSDT", interval: int = 240):
    """Use Kraken where the pair exists; fall back to OKX only for missing USDT pairs."""
    symbol = pair.upper().strip()
    try:
        _throttle_public_request()
        return fetch_kraken_ohlc(pair=symbol, interval=interval)
    except RuntimeError as exc:
        if not symbol.endswith("USDT") or "Unknown asset pair" not in str(exc):
            raise
        return _fetch_okx_ohlc(symbol, interval)
