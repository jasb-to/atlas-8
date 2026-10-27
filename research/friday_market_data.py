"""Kraken-first USDT OHLC adapter with a clearly labelled free Binance fallback.

Kraken is always attempted first. Binance Spot is used only when Kraken has no
matching USDT pair or fewer than 200 completed weekly candles. A per-pair
provider choice keeps weekly, daily and execution candles on the same venue
within one scan process. The caller still enforces the thesis candle minimums.
"""
from __future__ import annotations

import threading
import time

import pandas as pd
import requests

from research.validate_kraken_trendlines import fetch_ohlc as fetch_kraken_ohlc

# Space request starts to stay conservative with Kraken's public API rate limits.
MIN_PUBLIC_REQUEST_INTERVAL_SECONDS = 1.1
KRAKEN_ASSET_PAIRS_URL = "https://api.kraken.com/0/public/AssetPairs"
BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines"
_provider_cache: dict[str, str] = {}
_pair_cache: dict[str, str] = {}
_pair_cache_lock = threading.Lock()
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


def _normalise_pair(value: str) -> str:
    compact = "".join(character for character in value.upper() if character.isalnum())
    return "XBT" + compact[3:] if compact.startswith("BTC") else compact


def _resolve_kraken_usdt_pair(symbol: str) -> str:
    """Resolve requested symbols against Kraken's live AssetPairs catalogue."""
    canonical = _normalise_pair(symbol)
    with _pair_cache_lock:
        cached = _pair_cache.get(canonical)
    if cached:
        return cached

    _throttle_public_request()
    response = requests.get(
        KRAKEN_ASSET_PAIRS_URL,
        timeout=20,
        headers={"User-Agent": "atlas-8-research/1.0"},
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("error"):
        raise RuntimeError(f"Kraken AssetPairs API returned errors: {payload['error']}")
    pairs = payload.get("result", {})
    if not isinstance(pairs, dict):
        raise RuntimeError("Kraken AssetPairs response has no result catalogue")

    for pair_id, details in pairs.items():
        if not isinstance(details, dict):
            continue
        altname = str(details.get("altname", ""))
        wsname = str(details.get("wsname", ""))
        candidates = {
            _normalise_pair(altname),
            _normalise_pair(wsname),
            _normalise_pair(str(pair_id)),
        }
        # Do not accidentally accept a USD/EUR market for a requested USDT pair.
        if canonical in candidates and (
            altname.upper().endswith("USDT") or wsname.upper().endswith("/USDT")
        ):
            with _pair_cache_lock:
                _pair_cache[canonical] = altname or pair_id
            return altname or pair_id

    raise RuntimeError(f"Unknown asset pair: {symbol}")


def _fetch_binance_ohlc(symbol: str, interval: int) -> pd.DataFrame:
    """Fetch public Binance Spot candles; caller must label the source."""
    intervals = {1: "1m", 5: "5m", 15: "15m", 30: "30m", 60: "1h",
                 240: "4h", 1440: "1d", 10080: "1w"}
    if interval not in intervals:
        raise RuntimeError(f"Unsupported Binance candle interval: {interval}m")
    binance_symbol = _normalise_pair(symbol)
    if binance_symbol.startswith("XBT"):
        binance_symbol = "BTC" + binance_symbol[3:]
    response = requests.get(
        BINANCE_KLINES_URL,
        params={"symbol": binance_symbol, "interval": intervals[interval], "limit": 1000},
        timeout=20,
        headers={"User-Agent": "atlas-8-research/1.0"},
    )
    response.raise_for_status()
    rows = response.json()
    if not isinstance(rows, list) or not rows:
        raise RuntimeError(f"Binance returned no candles for {binance_symbol}")
    frame = pd.DataFrame(rows, columns=[
        "open_time", "open", "high", "low", "close", "volume", "close_time",
        "quote_volume", "trades", "taker_buy_base", "taker_buy_quote", "ignore",
    ])
    frame["timestamp"] = pd.to_datetime(frame["open_time"], unit="ms", utc=True)
    for col in ("open", "high", "low", "close", "volume", "quote_volume"):
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame["vwap"] = frame["quote_volume"] / frame["volume"].replace(0, float("nan"))
    frame["trades"] = pd.to_numeric(frame["trades"], errors="coerce").fillna(0).astype(int)
    frame = frame[["timestamp", "open", "high", "low", "close", "vwap", "volume", "trades"]]
    frame = frame.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)
    now = pd.Timestamp.now(tz="UTC")
    frame = frame.loc[frame["timestamp"] + pd.Timedelta(minutes=interval) <= now].reset_index(drop=True)
    frame.attrs["source"] = "Binance Spot (free fallback)"
    frame.attrs["source_pair"] = binance_symbol
    return frame


def fetch_ohlc(pair: str = "XBTUSDT", interval: int = 240) -> pd.DataFrame:
    """Fetch Kraken candles first; use Binance only when Kraken lacks a usable weekly history."""
    symbol = pair.upper().strip()
    canonical = _normalise_pair(symbol)
    with _pair_cache_lock:
        preferred = _provider_cache.get(canonical)
    if preferred == "binance":
        return _fetch_binance_ohlc(symbol, interval)

    try:
        resolved_pair = _resolve_kraken_usdt_pair(symbol)
        _throttle_public_request()
        frame = fetch_kraken_ohlc(pair=resolved_pair, interval=interval)
        frame.attrs["source"] = "Kraken Spot"
        frame.attrs["source_pair"] = resolved_pair
        if interval == 10080 and len(frame) < 200:
            try:
                fallback = _fetch_binance_ohlc(symbol, interval)
                if len(fallback) > len(frame):
                    with _pair_cache_lock:
                        _provider_cache[canonical] = "binance"
                    return fallback
            except (requests.RequestException, RuntimeError, ValueError):
                # Preserve Kraken's partial history so the evaluator can report the
                # exact shortfall rather than hiding usable candles.
                pass
        return frame
    except RuntimeError as exc:
        if "Unknown asset pair" not in str(exc):
            raise
        try:
            fallback = _fetch_binance_ohlc(symbol, interval)
            with _pair_cache_lock:
                _provider_cache[canonical] = "binance"
            return fallback
        except (requests.RequestException, RuntimeError, ValueError) as fallback_error:
            raise RuntimeError(
                f"UNAVAILABLE — NO KRAKEN PAIR OR BINANCE USDT MARKET: {symbol} "
                f"(interval={interval}m); fallback detail: {fallback_error}"
            ) from exc
