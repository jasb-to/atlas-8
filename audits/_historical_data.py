"""Paginated OKX historical candles for offline research audits (public market data only)."""
from __future__ import annotations

import time
import pandas as pd
import requests

BASE_URL = "https://www.okx.com/api/v5/market/history-candles"
BAR_MAP = {"1D": "1Dutc", "1W": "1W", "4H": "4H"}


def fetch_history(pair: str, bar: str, start: str, end: str, *, pause_seconds: float = 0.12) -> pd.DataFrame:
    """Fetch confirmed candles in [start, end) by paging backwards through OKX history."""
    if bar not in BAR_MAP:
        raise ValueError(f"Unsupported bar {bar!r}; choose one of {sorted(BAR_MAP)}")
    symbol = pair.upper()
    if not symbol.endswith("USDT"):
        raise ValueError("Only USDT-quoted pairs are supported by this audit helper.")
    inst_id = f"{symbol[:-4]}-USDT"
    start_ms = int(pd.Timestamp(start, tz="UTC").timestamp() * 1000)
    end_ms = int(pd.Timestamp(end, tz="UTC").timestamp() * 1000)
    cursor = end_ms
    rows_by_ts = {}
    stagnant = 0

    while cursor > start_ms:
        response = requests.get(
            BASE_URL,
            params={"instId": inst_id, "bar": BAR_MAP[bar], "after": cursor, "limit": 100},
            timeout=25,
            headers={"User-Agent": "atlas-8-friday-audit/1.0"},
        )
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, dict) or body.get("code") != "0":
            raise RuntimeError(f"OKX history error for {inst_id}: {body}")
        rows = body.get("data") or []
        if not rows:
            break
        before_count = len(rows_by_ts)
        oldest = None
        for row in rows:
            ts = int(row[0])
            oldest = ts if oldest is None else min(oldest, ts)
            if start_ms <= ts < end_ms and str(row[8]) == "1":
                rows_by_ts[ts] = row
        if oldest is None or oldest >= cursor or len(rows_by_ts) == before_count:
            stagnant += 1
            if stagnant >= 2:
                break
        else:
            stagnant = 0
        if oldest is None or oldest <= start_ms:
            break
        cursor = oldest
        time.sleep(pause_seconds)

    if not rows_by_ts:
        raise RuntimeError(f"No confirmed history returned for {inst_id} {bar} in [{start}, {end}).")
    frame = pd.DataFrame(
        [rows_by_ts[key] for key in sorted(rows_by_ts)],
        columns=["open_time", "open", "high", "low", "close", "volume",
                 "quote_volume", "quote_volume_alt", "confirm"],
    )
    frame["timestamp"] = pd.to_datetime(pd.to_numeric(frame["open_time"]), unit="ms", utc=True)
    for col in ("open", "high", "low", "close", "volume", "quote_volume"):
        frame[col] = pd.to_numeric(frame[col], errors="raise")
    return frame[["timestamp", "open", "high", "low", "close", "volume"]].reset_index(drop=True)
