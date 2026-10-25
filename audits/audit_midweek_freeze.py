"""Standalone weekly mid-week freeze audit for completed-candle indicators."""
from __future__ import annotations

import pandas as pd

from research.friday_market_data import fetch_ohlc
from research.weekly_thesis import _completed_candles, calculate_stoch_rsi


def snapshot(raw: pd.DataFrame, as_of: pd.Timestamp) -> dict:
    closed = _completed_candles(raw, interval_minutes=10080, as_of=as_of)
    if len(closed) < 201:
        raise RuntimeError(f"Only {len(closed)} completed weekly candles at {as_of}; need 201.")
    close = closed["close"].astype(float)
    sma = close.rolling(200, min_periods=200).mean()
    k, d = calculate_stoch_rsi(close)
    return {
        "count": len(closed),
        "last_timestamp": closed.iloc[-1]["timestamp"].isoformat(),
        "close": float(close.iloc[-1]),
        "sma": float(sma.iloc[-1]),
        "stoch_k": float(k.iloc[-1]),
        "stoch_d": float(d.iloc[-1]),
    }


def main():
    raw = fetch_ohlc(pair="BTCUSDT", interval=10080)
    raw = raw.copy()
    raw["timestamp"] = pd.to_datetime(raw["timestamp"], utc=True)
    raw = raw.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)
    weeks = raw["timestamp"].drop_duplicates().tolist()
    checked = 0
    failures = []
    for week_start in weeks:
        # Select Wednesday noon and Friday 23:59 within the same weekly candle.
        wednesday = pd.Timestamp(week_start) + pd.Timedelta(days=2, hours=12)
        friday = pd.Timestamp(week_start) + pd.Timedelta(days=4, hours=23, minutes=59)
        try:
            wed = snapshot(raw, wednesday)
            fri = snapshot(raw, friday)
        except RuntimeError:
            continue
        checked += 1
        if wed != fri:
            failures.append({"week_start": pd.Timestamp(week_start).isoformat(), "wednesday": wed, "friday": fri})
    print("| Audit | Weeks checked | Result |")
    print("|---|---:|---|")
    print(f"| BTC weekly indicator mid-week freeze | {checked} | {'FAIL — values changed' if failures else 'PASS — identical' if checked else 'BLOCKED — no eligible historical week'} |")
    if failures:
        print("\nFirst discrepancy:")
        print(failures[0])
        raise SystemExit(1)
    if checked == 0:
        raise SystemExit("No eligible weeks: verify the data source provides at least 201 completed weekly candles.")


if __name__ == "__main__":
    main()
