"""Fetch and report completed weekly Kraken OHLC for requested FRIDAY markets."""
from __future__ import annotations

import sys

from research.friday_market_data import fetch_ohlc

PAIRS = ("HYPEUSDT", "SUIUSDT", "ZECUSDT", "NEARUSDT", "UNIUSDT")
WEEKLY_INTERVAL_MINUTES = 10080


def main() -> int:
    failures = []
    for pair in PAIRS:
        try:
            frame = fetch_ohlc(pair, WEEKLY_INTERVAL_MINUTES)
            if frame.empty:
                raise RuntimeError("Kraken returned no completed weekly candles")
            last = frame.iloc[-1]
            source_pair = frame.attrs.get("source_pair", "unknown")
            print(
                f"PASS {pair}: kraken_pair={source_pair} "
                f"completed_weekly_candles={len(frame)} "
                f"last_completed_week={last['timestamp'].isoformat()} "
                f"close={float(last['close']):.8g}"
            )
        except Exception as exc:
            failures.append((pair, str(exc)))
            print(f"FAIL {pair}: {exc}")
    print(f"SUMMARY: {len(PAIRS) - len(failures)}/{len(PAIRS)} pairs returned weekly OHLC")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
