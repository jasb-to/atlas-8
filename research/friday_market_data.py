"""Kraken-first USDT OHLC adapter with targeted, gap-checked 4H-to-1D aggregation."""
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
    # Kraken's REST AssetPairs catalogue uses XBT for BTC and XDG for DOGE.
    # Preserve the public FRIDAY symbols while resolving Kraken's native alias.
    if compact.startswith("BTC"):
        return "XBT" + compact[3:]
    if compact.startswith("DOGE"):
        return "XDG" + compact[4:]
    return compact


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

    quote = "USDT" if canonical.endswith("USDT") else "USD" if canonical.endswith("USD") else None
    if quote is None:
        raise RuntimeError(f"Unsupported quote asset for pair: {symbol}")
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
        # Match the requested quote asset exactly: USD requests stay USD and
        # USDT requests stay USDT. Never silently substitute one for the other.
        if canonical in candidates and (
            altname.upper().endswith(quote) or wsname.upper().endswith("/" + quote)
        ):
            with _pair_cache_lock:
                _pair_cache[canonical] = altname or pair_id
            return altname or pair_id

    raise RuntimeError(f"Unknown asset pair: {symbol}")


# Only use 4H-to-1D aggregation for markets whose history has been a known
# coverage problem. Other markets keep their native daily feed.
TARGETED_RESAMPLE_PAIRS = {"BNBUSDT", "HYPEUSDT", "LITUSDT", "SUIUSDT", "VIRTUALUSDT", "ZECUSDT"}


def aggregate_ohlc(candles: pd.DataFrame, interval_minutes: int, target_minutes: int) -> pd.DataFrame:
    """Aggregate complete, gap-free lower-timeframe OHLCV into UTC candles."""
    if target_minutes <= interval_minutes or target_minutes % interval_minutes:
        raise ValueError("Target interval must be an integer multiple of source interval")
    required = {"timestamp", "open", "high", "low", "close", "volume"}
    missing = required.difference(candles.columns)
    if missing:
        raise ValueError(f"OHLC data is missing required columns: {sorted(missing)}")
    frame = candles.copy()
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, errors="coerce")
    for col in ("open", "high", "low", "close", "volume"):
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame = frame.dropna(subset=["timestamp", "open", "high", "low", "close", "volume"])
    frame = frame.sort_values("timestamp").drop_duplicates("timestamp", keep="last")
    now = pd.Timestamp.now(tz="UTC")
    frame = frame.loc[frame["timestamp"] + pd.Timedelta(minutes=interval_minutes) <= now]
    if frame.empty:
        return frame.assign(timestamp=pd.Series(dtype="datetime64[ns, UTC]"))

    step = pd.Timedelta(minutes=interval_minutes)
    target = pd.Timedelta(minutes=target_minutes)
    frame = frame.loc[frame["timestamp"].dt.floor(target) + target <= now]
    frame["bucket"] = frame["timestamp"].dt.floor(target)
    expected = target_minutes // interval_minutes
    rows = []
    for bucket, group in frame.groupby("bucket", sort=True):
        group = group.sort_values("timestamp")
        expected_times = pd.date_range(bucket, periods=expected, freq=step)
        # Reject partial days and any missing 4H bar; never invent OHLC values.
        if len(group) != expected or list(group["timestamp"]) != list(expected_times):
            continue
        row = {
            "timestamp": bucket,
            "open": float(group.iloc[0]["open"]),
            "high": float(group["high"].max()),
            "low": float(group["low"].min()),
            "close": float(group.iloc[-1]["close"]),
            "volume": float(group["volume"].sum()),
        }
        if "trades" in group.columns:
            row["trades"] = int(pd.to_numeric(group["trades"], errors="coerce").fillna(0).sum())
        if "vwap" in group.columns:
            volume = group["volume"].sum()
            row["vwap"] = float((group["vwap"] * group["volume"]).sum() / volume) if volume else float("nan")
        rows.append(row)
    result = pd.DataFrame(rows)
    if not result.empty:
        result = result.sort_values("timestamp").reset_index(drop=True)
    result.attrs["source"] = f"{candles.attrs.get('source', 'unknown')} 4H aggregated to 1D"
    result.attrs["source_pair"] = candles.attrs.get("source_pair", "")
    result.attrs["aggregation"] = "UTC; six complete 4H candles; gap-free"
    return result


def _fetch_kraken(pair: str, interval: int) -> pd.DataFrame:
    resolved_pair = _resolve_kraken_usdt_pair(pair)
    _throttle_public_request()
    frame = fetch_kraken_ohlc(pair=resolved_pair, interval=interval)
    frame.attrs["source"] = "Kraken Spot"
    frame.attrs["source_pair"] = resolved_pair
    return frame

def fetch_ohlc(pair: str = "XBTUSDT", interval: int = 240) -> pd.DataFrame:
    """Fetch Kraken USDT OHLC; targeted markets may aggregate complete 4H bars to 1D.

    Binance is intentionally not used as a fallback. Aggregation is only attempted
    for the known coverage-problem markets and never relaxes weekly/daily minimums.
    """
    symbol = pair.upper().strip()
    canonical = _normalise_pair(symbol)
    try:
        frame = _fetch_kraken(symbol, interval)
        if interval == 1440 and canonical in TARGETED_RESAMPLE_PAIRS and len(frame) < 50:
            four_hour = _fetch_kraken(symbol, 240)
            aggregated = aggregate_ohlc(four_hour, 240, 1440)
            if len(aggregated) > len(frame):
                return aggregated
        return frame
    except (RuntimeError, requests.RequestException, ValueError) as exc:
        raise RuntimeError(
            f"UNAVAILABLE — NO KRAKEN USDT OHLC HISTORY: {symbol} "
            f"(interval={interval}m); detail: {exc}"
        ) from exc
