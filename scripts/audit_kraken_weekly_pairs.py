"""Fetch completed weekly Kraken OHLC and report the actual quote market used.

FRIDAY's production fetcher remains strict about USDT pairs. This diagnostic audit
also checks each asset's USD market when no USDT-quoted market exists, and labels
that quote explicitly instead of pretending USD candles are USDT candles.
"""
from __future__ import annotations

import sys

import requests

from research.friday_market_data import (
    KRAKEN_ASSET_PAIRS_URL,
    _normalise_pair,
    _throttle_public_request,
)
from research.validate_kraken_trendlines import fetch_ohlc

PAIRS = ("HYPEUSDT", "SUIUSDT", "ZECUSDT", "NEARUSDT", "UNIUSDT")
WEEKLY_INTERVAL_MINUTES = 10080


def load_catalogue() -> dict:
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
    result = payload.get("result", {})
    if not isinstance(result, dict):
        raise RuntimeError("Kraken AssetPairs response has no result catalogue")
    return result


def find_market(pair: str, catalogue: dict) -> tuple[str, str] | None:
    requested_base = _normalise_pair(pair.removesuffix("USDT"))
    candidates = []
    for pair_id, details in catalogue.items():
        if not isinstance(details, dict):
            continue
        wsname = str(details.get("wsname", ""))
        if "/" not in wsname:
            continue
        base, quote = wsname.split("/", 1)
        if _normalise_pair(base) != requested_base or quote.upper() not in {"USDT", "USD"}:
            continue
        kraken_pair = str(details.get("altname") or pair_id)
        candidates.append((0 if quote.upper() == "USDT" else 1, kraken_pair, quote.upper()))
    if not candidates:
        return None
    _, kraken_pair, quote = sorted(candidates)[0]
    return kraken_pair, quote


def main() -> int:
    try:
        catalogue = load_catalogue()
    except Exception as exc:
        print(f"FAIL AssetPairs catalogue: {exc}")
        return 1

    failures = []
    for requested_pair in PAIRS:
        try:
            market = find_market(requested_pair, catalogue)
            if market is None:
                raise RuntimeError("asset has no Kraken USD- or USDT-quoted spot pair in AssetPairs")
            kraken_pair, quote = market
            _throttle_public_request()
            frame = fetch_ohlc(pair=kraken_pair, interval=WEEKLY_INTERVAL_MINUTES)
            if frame.empty:
                raise RuntimeError("Kraken returned no completed weekly candles")
            last = frame.iloc[-1]
            quote_note = "requested USDT quote" if quote == "USDT" else "USD quote fallback; not USDT"
            print(
                f"PASS {requested_pair}: kraken_pair={kraken_pair} quote={quote} "
                f"({quote_note}); completed_weekly_candles={len(frame)} "
                f"last_completed_week={last['timestamp'].isoformat()} "
                f"close={float(last['close']):.8g}"
            )
        except Exception as exc:
            failures.append((requested_pair, str(exc)))
            print(f"FAIL {requested_pair}: {exc}")
    print(f"SUMMARY: {len(PAIRS) - len(failures)}/{len(PAIRS)} assets returned weekly OHLC")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
