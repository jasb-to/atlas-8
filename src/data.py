"""CoinGecko historical data loader.

The runner intentionally keeps the raw API response separate from derived candles.
For long histories, CoinGecko requests are chunked by date range.
"""

from __future__ import annotations
import time
from datetime import datetime, timezone
from pathlib import Path
import requests
import pandas as pd

BASE = "https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart/range"

def fetch_range(coin_id: str, start: datetime, end: datetime, retries: int = 5) -> pd.DataFrame:
    params = {
        "vs_currency": "usd",
        "from": start.replace(tzinfo=timezone.utc).timestamp(),
        "to": end.replace(tzinfo=timezone.utc).timestamp(),
    }
    last = None
    for attempt in range(retries):
        try:
            r = requests.get(BASE.format(coin_id=coin_id), params=params, timeout=60)
            if r.status_code == 429:
                time.sleep(2 ** attempt)
                continue
            r.raise_for_status()
            j = r.json()
            prices = pd.DataFrame(j.get("prices", []), columns=["ts", "close"])
            volumes = pd.DataFrame(j.get("total_volumes", []), columns=["ts", "volume"])
            if prices.empty:
                return pd.DataFrame(columns=["time","close","volume"])
            prices["time"] = pd.to_datetime(prices.ts, unit="ms", utc=True)
            volumes["time"] = pd.to_datetime(volumes.ts, unit="ms", utc=True)
            out = prices[["time","close"]].merge(volumes[["time","volume"]], on="time", how="left")
            return out.sort_values("time").drop_duplicates("time")
        except Exception as e:
            last = e
            time.sleep(2 ** attempt)
    raise RuntimeError(f"CoinGecko request failed for {coin_id}: {last}")

def save(df: pd.DataFrame, path: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)
