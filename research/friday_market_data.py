"""Kraken-first USDT OHLC adapter with targeted, gap-checked 4H-to-1D aggregation."""
from __future__ import annotations

import threading
import time

import pandas as pd
import requests

from research.validate_kraken_trendlines import fetch_ohlc as fetch_kraken_ohlc
from research.friday_historical_data import aggregate_daily_to_weekly, load_seeded_ohlc, merge_ohlc

# Space request starts to stay conservative with Kraken's public API rate limits.
MIN_PUBLIC_REQUEST_INTERVAL_SECONDS = 1.1
KRAKEN_ASSET_PAIRS_URL = "https://api.kraken.com/0/public/AssetPairs"
_pair_cache: dict[str, str] = {}
_asset_pairs_catalogue: dict[str, dict] | None = None
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
    global _asset_pairs_catalogue
    with _pair_cache_lock:
        cached = _pair_cache.get(canonical)
        if cached:
            return cached

        # Fetch AssetPairs once per process and reuse the full catalogue for every
        # ticker. Holding this lock during the first request prevents duplicate
        # catalogue downloads when several market scans start concurrently.
        if _asset_pairs_catalogue is None:
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
            _asset_pairs_catalogue = pairs
        pairs = _asset_pairs_catalogue

    quote = "USDT" if canonical.endswith("USDT") else "USD" if canonical.endswith("USD") else None
    if quote is None:
        raise RuntimeError(f"Unsupported quote asset for pair: {symbol}")

    requested_base = canonical[:-len(quote)] if quote else canonical

    def find_pair(requested_quote: str) -> tuple[str, str, str] | None:
        for pair_id, details in pairs.items():
            if not isinstance(details, dict):
                continue
            altname = str(details.get("altname", "")).upper()
            wsname = str(details.get("wsname", "")).upper()
            base_quote = wsname.split("/", 1) if "/" in wsname else ("", "")
            ws_base, ws_quote = base_quote
            # Kraken's REST catalogue can expose native asset IDs, alt names and
            # WebSocket names that differ. Check both human-readable and altname
            # forms, while matching the quote exactly (not by suffix alone).
            alt_base = altname[:-len(requested_quote)] if altname.endswith(requested_quote) else ""
            bases = {_normalise_pair(value) for value in (ws_base, alt_base) if value}
            actual_quote = ws_quote if ws_quote else (
                requested_quote if altname.endswith(requested_quote) else ""
            )
            if _normalise_pair(requested_base) in bases and actual_quote == requested_quote:
                return (altname or str(pair_id), str(pair_id), wsname)
        return None

    match = find_pair(quote)
    resolved_quote = quote
    if match is None and quote == "USDT":
        base = canonical[:-4]
        # USD fallback is for FRIDAY's fixed research universe only. It is for
        # structural analysis, never order routing; the source quote is surfaced.
        if base in {
            "XBT", "ETH", "SOL", "XRP", "ADA", "AVAX", "BNB", "VIRTUAL",
            "HYPE", "XDG", "LINK", "ZEC", "NEAR", "SUI", "UNI",
        }:
            match = find_pair("USD")
            if match:
                resolved_quote = "USD"
                print(
                    f"WARNING: No USDT market on Kraken for {base}. "
                    f"Falling back to {match[0]} ({match[2] or 'pair name unavailable'}) "
                    "for structural analysis only; no execution orders use this pair.",
                    flush=True,
                )
    if match is None:
        if canonical in {"UNIUSDT", "ZECUSDT"}:
            candidates = []
            for pair_id, details in pairs.items():
                if not isinstance(details, dict):
                    continue
                altname = str(details.get("altname", ""))
                wsname = str(details.get("wsname", ""))
                if requested_base in altname.upper() or requested_base in wsname.upper():
                    candidates.append(
                        f"{pair_id}(altname={altname},wsname={wsname})"
                    )
            print(
                f"[KRAKEN PAIR RESOLUTION] requested={canonical} "
                f"catalogue_candidates={candidates[:20]}",
                flush=True,
            )
        raise RuntimeError(f"Unknown asset pair: {symbol}")
    resolved, pair_id, wsname = match
    if canonical in {"UNIUSDT", "ZECUSDT"}:
        print(
            f"[KRAKEN PAIR RESOLUTION] requested={canonical} pair_id={pair_id} "
            f"altname={resolved} wsname={wsname or 'n/a'} quote={resolved_quote}",
            flush=True,
        )
    with _pair_cache_lock:
        _pair_cache[canonical] = resolved
    return resolved


# Only use 4H-to-1D aggregation for markets whose history has been a known
# coverage problem. Other markets keep their native daily feed.
TARGETED_RESAMPLE_PAIRS = {"BNBUSDT", "HYPEUSDT", "SUIUSDT", "VIRTUALUSDT", "ZECUSDT"}


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
    result.attrs["quote_currency"] = candles.attrs.get("quote_currency", "USDT")
    result.attrs["aggregation"] = "UTC; six complete 4H candles; gap-free"
    return result


def _fetch_kraken(pair: str, interval: int) -> pd.DataFrame:
    resolved_pair = _resolve_kraken_usdt_pair(pair)
    _throttle_public_request()
    frame = fetch_kraken_ohlc(pair=resolved_pair, interval=interval)
    quote_currency = "USD" if resolved_pair.upper().endswith("USD") and not resolved_pair.upper().endswith("USDT") else "USDT"
    frame.attrs["quote_currency"] = quote_currency
    frame.attrs["source"] = f"Kraken Spot ({quote_currency})" if quote_currency == "USD" else "Kraken Spot"
    frame.attrs["source_pair"] = resolved_pair
    return frame

def fetch_ohlc(pair: str = "XBTUSDT", interval: int = 240) -> pd.DataFrame:
    """Fetch Kraken candles, extending the 720-row REST window with persisted bulk history.

    The compact persisted seed covers daily and weekly structural analysis. Live
    REST candles are merged on timestamp (live wins) so the last completed bars
    stay current. The cache is never used for 4H execution timing.
    """
    symbol = pair.upper().strip()
    canonical = _normalise_pair(symbol)
    cached = load_seeded_ohlc(canonical, interval) if interval in {1440, 10080} else pd.DataFrame()
    if interval == 10080:
        # Kraken's bulk archive contains daily files, not weekly files. Build the
        # weekly series from merged daily history so its candle boundaries are
        # consistent and the 720-row weekly REST cap is irrelevant.
        daily_cache = load_seeded_ohlc(canonical, 1440)
        try:
            live_daily = _fetch_kraken(symbol, 1440)
            daily = merge_ohlc(daily_cache, live_daily) if not daily_cache.empty else live_daily
        except (RuntimeError, requests.RequestException, ValueError) as exc:
            if daily_cache.empty:
                if not cached.empty:
                    cached.attrs["source"] = "Kraken downloadable historical OHLCVT (persisted weekly seed; REST refresh unavailable)"
                    return cached
                raise RuntimeError(
                    f"UNAVAILABLE — NO KRAKEN OHLC HISTORY: {symbol} "
                    f"(interval={interval}m); detail: {exc}"
                ) from exc
            daily = daily_cache
            print(
                f"[FRIDAY HISTORY] {canonical} using {len(daily)} persisted daily candles "
                f"to build weekly history; live refresh failed: {exc}",
                flush=True,
            )
        weekly = aggregate_daily_to_weekly(daily)
        if weekly.empty and not cached.empty:
            return cached
        return weekly

    try:
        frame = _fetch_kraken(symbol, interval)
        if not cached.empty:
            frame = merge_ohlc(cached, frame)
        if interval == 1440 and canonical in TARGETED_RESAMPLE_PAIRS and len(frame) < 50:
            four_hour = _fetch_kraken(symbol, 240)
            aggregated = aggregate_ohlc(four_hour, 240, 1440)
            if len(aggregated) > len(frame):
                return merge_ohlc(cached, aggregated) if not cached.empty else aggregated
        return frame
    except (RuntimeError, requests.RequestException, ValueError) as exc:
        if not cached.empty:
            cached.attrs["source"] = "Kraken downloadable historical OHLCVT (persisted seed; REST refresh unavailable)"
            print(
                f"[FRIDAY HISTORY] {canonical} interval={interval}m using "
                f"{len(cached)} persisted bulk candles; live refresh failed: {exc}",
                flush=True,
            )
            return cached
        raise RuntimeError(
            f"UNAVAILABLE — NO KRAKEN OHLC HISTORY: {symbol} "
            f"(interval={interval}m); detail: {exc}"
        ) from exc
