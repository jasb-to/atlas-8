"""Kraken-only USDT spot OHLC adapter for FRIDAY.

All thesis and execution market data must come from Kraken spot. If Kraken
does not expose a requested pair, propagate the explicit unavailable error;
never substitute candles from another exchange.
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


def fetch_ohlc(pair: str = "XBTUSDT", interval: int = 240) -> pd.DataFrame:
    """Fetch completed USDT OHLC candles from Kraken, resolving Kraken's pair aliases."""
    symbol = pair.upper().strip()
    try:
        resolved_pair = _resolve_kraken_usdt_pair(symbol)
        _throttle_public_request()
        return fetch_kraken_ohlc(pair=resolved_pair, interval=interval)
    except RuntimeError as exc:
        if "Unknown asset pair" in str(exc):
            raise RuntimeError(
                f"UNAVAILABLE — NO KRAKEN PAIR: {symbol} (interval={interval}m)"
            ) from exc
        raise
