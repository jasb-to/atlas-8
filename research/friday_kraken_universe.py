"""Rate-limit-aware Kraken spot universe discovery for Friday.

Discovery is deliberately separate from per-market OHLC analysis: use the cheap
AssetPairs + batched Ticker endpoints to shortlist liquid USD/USDT spot markets,
then spend OHLC requests only on the shortlist. Research only; no trading calls.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any

import requests

BASE_URL = "https://api.kraken.com/0/public"
DEFAULT_MIN_QUOTE_VOLUME_USD = 5_000_000.0
DEFAULT_MAX_MARKETS = 40
DEFAULT_REQUEST_GAP_SECONDS = 1.25
DEFAULT_TICKER_BATCH_SIZE = 40


@dataclass(frozen=True)
class MarketCandidate:
    pair: str
    base: str
    quote: str
    last_price: float
    quote_volume_24h: float


class KrakenPublicClient:
    """Small paced public REST client; one shared gap applies to every request."""

    def __init__(self, min_request_gap: float | None = None, session: Any = None):
        self.min_request_gap = max(
            1.0,
            float(min_request_gap if min_request_gap is not None else os.getenv(
                "FRIDAY_KRAKEN_REQUEST_GAP_SECONDS", DEFAULT_REQUEST_GAP_SECONDS
            )),
        )
        self.session = session or requests.Session()
        self._last_request_at = 0.0

    def get(self, endpoint: str, params: dict | None = None) -> dict:
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self.min_request_gap:
            time.sleep(self.min_request_gap - elapsed)
        response = self.session.get(
            f"{BASE_URL}/{endpoint}",
            params=params or {},
            timeout=20,
            headers={"User-Agent": "friday-research/1.0"},
        )
        self._last_request_at = time.monotonic()
        if response.status_code == 429:
            # Respect Retry-After if present; otherwise use a conservative cooldown.
            try:
                cooldown = max(5.0, float(response.headers.get("Retry-After", "5")))
            except (TypeError, ValueError):
                cooldown = 5.0
            time.sleep(min(cooldown, 60.0))
            raise RuntimeError("Kraken public API rate limited this scan; retry next cycle.")
        response.raise_for_status()
        payload = response.json()
        if payload.get("error"):
            errors = payload["error"]
            if any("Rate limit" in str(error) for error in errors):
                time.sleep(5.0)
            raise RuntimeError(f"Kraken {endpoint} error: {errors}")
        result = payload.get("result")
        if not isinstance(result, dict):
            raise RuntimeError(f"Kraken {endpoint} returned an invalid result.")
        return result


def _is_spot_quote_market(pair_info: dict, allowed_quotes: set[str]) -> bool:
    status = str(pair_info.get("status", "online")).lower()
    if status != "online":
        return False
    quote = str(pair_info.get("quote", "")).upper()
    wsname = str(pair_info.get("wsname", ""))
    # Kraken's internal asset codes vary (e.g. ZUSD, USDT); use both quote
    # asset code and the human-facing pair label, never symbol substring alone.
    return quote in allowed_quotes or any(wsname.endswith(f"/{q}") for q in allowed_quotes)


def discover_liquid_spot_markets(
    client: KrakenPublicClient | None = None,
    *,
    allowed_quotes: tuple[str, ...] = ("USD", "USDT"),
    min_quote_volume_usd: float = DEFAULT_MIN_QUOTE_VOLUME_USD,
    max_markets: int = DEFAULT_MAX_MARKETS,
    ticker_batch_size: int = DEFAULT_TICKER_BATCH_SIZE,
) -> list[MarketCandidate]:
    """Return highest-turnover online USD/USDT spot pairs using batched ticker reads."""
    client = client or KrakenPublicClient()
    quotes = {str(q).upper() for q in allowed_quotes}
    pairs = client.get("AssetPairs")
    eligible: dict[str, dict] = {}
    for api_name, info in pairs.items():
        if not isinstance(info, dict) or not _is_spot_quote_market(info, quotes):
            continue
        # Exclude dark pools and unusual/leveraged symbols; require a usable
        # canonical REST pair key and normal online status.
        if ".d" in api_name.lower() or not info.get("wsname"):
            continue
        eligible[api_name] = info

    names = list(eligible)
    ticker_rows: list[tuple[str, dict]] = []
    for start in range(0, len(names), max(1, int(ticker_batch_size))):
        batch = names[start:start + max(1, int(ticker_batch_size))]
        tickers = client.get("Ticker", {"pair": ",".join(batch)})
        for ticker_name, ticker in tickers.items():
            if ticker_name == "last" or not isinstance(ticker, dict):
                continue
            ticker_rows.append((ticker_name, ticker))

    candidates = []
    for ticker_name, ticker in ticker_rows:
        info = eligible.get(ticker_name)
        if info is None:
            # Kraken can return a normalized alias for the same requested pair.
            info = next((v for k, v in eligible.items()
                         if k.replace("/", "") == ticker_name.replace("/", "")), None)
        if info is None:
            continue
        try:
            last = float(ticker["c"][0])
            base_volume_24h = float(ticker["v"][1])
            vwap_24h = float(ticker["p"][1])
            quote_volume = base_volume_24h * (vwap_24h if vwap_24h > 0 else last)
        except (KeyError, IndexError, TypeError, ValueError):
            continue
        if last <= 0 or quote_volume < float(min_quote_volume_usd):
            continue
        wsname = str(info.get("wsname") or ticker_name)
        base, _, quote = wsname.partition("/")
        candidates.append(MarketCandidate(
            pair=ticker_name, base=base, quote=quote, last_price=last,
            quote_volume_24h=round(quote_volume, 2),
        ))
    candidates.sort(key=lambda item: item.quote_volume_24h, reverse=True)
    return candidates[:max(1, int(max_markets))]
