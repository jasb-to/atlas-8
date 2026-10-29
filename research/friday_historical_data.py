"""Read the one-time Kraken OHLCVT seed persisted in FRIDAY's data branch."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pandas as pd

_HISTORY_CACHE: dict[str, tuple[int, dict[str, Any]]] = {}


def canonical_pair(value: str) -> str:
    compact = "".join(ch for ch in value.upper() if ch.isalnum())
    if compact.startswith("BTC"):
        compact = "XBT" + compact[3:]
    elif compact.startswith("DOGE"):
        compact = "XDG" + compact[4:]
    return compact


def _read_store(path: Path) -> dict[str, Any]:
    try:
        stat = path.stat()
    except OSError:
        return {}
    cached = _HISTORY_CACHE.get(str(path))
    if cached and cached[0] == stat.st_mtime_ns:
        return cached[1]
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(value, dict) or not isinstance(value.get("pairs"), dict):
        return {}
    _HISTORY_CACHE[str(path)] = (stat.st_mtime_ns, value)
    return value


def load_seeded_ohlc(pair: str, interval: int, root: str | None = None) -> pd.DataFrame:
    """Load persisted daily (1440) or weekly (10080) candles for one approved symbol."""
    if interval not in {1440, 10080}:
        return pd.DataFrame()
    data_root = Path(root or os.getenv("FRIDAY_REPO_DATA_DIR", "testing/friday/data"))
    payload = _read_store(data_root / "ohlc_history.json")
    row = payload.get("pairs", {}).get(canonical_pair(pair), {})
    timeframe = "daily" if interval == 1440 else "weekly"
    candles = row.get(timeframe, [])
    if not isinstance(candles, list) or not candles:
        return pd.DataFrame()
    frame = pd.DataFrame(candles)
    required = {"timestamp", "open", "high", "low", "close", "volume"}
    if not required.issubset(frame.columns):
        return pd.DataFrame()
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, errors="coerce")
    for column in ("open", "high", "low", "close", "volume"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["timestamp", "open", "high", "low", "close", "volume"])
    frame = frame.sort_values("timestamp").drop_duplicates("timestamp", keep="last").reset_index(drop=True)
    frame["trades"] = pd.to_numeric(frame.get("trades", 0), errors="coerce").fillna(0).astype(int)
    frame["vwap"] = pd.NA
    frame.attrs.update({
        "source": "Kraken downloadable historical OHLCVT (persisted seed)",
        "source_pair": row.get("source_pair", ""),
        "quote_currency": row.get("quote_currency", "USDT"),
        "seeded_history": True,
    })
    return frame


def merge_ohlc(history: pd.DataFrame, live: pd.DataFrame) -> pd.DataFrame:
    """Merge persisted history and current REST candles; live rows win duplicate timestamps."""
    if history is None or history.empty:
        return live.copy()
    if live is None or live.empty:
        return history.copy()
    history_quote = str(history.attrs.get("quote_currency", "USDT")).upper()
    live_quote = str(live.attrs.get("quote_currency", "USDT")).upper()
    if history_quote != live_quote:
        # USD and USDT candles are close but not identical; never splice their prices.
        return live.copy()
    combined = pd.concat([history, live], ignore_index=True, sort=False)
    combined["timestamp"] = pd.to_datetime(combined["timestamp"], utc=True, errors="coerce")
    combined = combined.dropna(subset=["timestamp"])
    combined = combined.sort_values("timestamp").drop_duplicates("timestamp", keep="last").reset_index(drop=True)
    combined.attrs.update(live.attrs)
    combined.attrs["source"] = "Kraken bulk OHLCVT seed + live Kraken REST"
    combined.attrs["seeded_history"] = True
    return combined


def aggregate_daily_to_weekly(daily: pd.DataFrame) -> pd.DataFrame:
    """Aggregate daily OHLCV into completed UTC Monday-start weeks without fabricating bars."""
    columns = ["timestamp", "open", "high", "low", "close", "volume", "trades"]
    if daily is None or daily.empty:
        return pd.DataFrame(columns=columns)
    frame = daily.copy()
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, errors="coerce")
    for column in ("open", "high", "low", "close", "volume"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["timestamp", "open", "high", "low", "close", "volume"])
    frame = frame.sort_values("timestamp").drop_duplicates("timestamp", keep="last")
    frame["trades"] = pd.to_numeric(frame.get("trades", pd.Series(0, index=frame.index)), errors="coerce").fillna(0)
    frame["week"] = frame["timestamp"].dt.tz_localize(None).dt.to_period("W-SUN").dt.start_time.dt.tz_localize("UTC")
    now = pd.Timestamp.now(tz="UTC")
    rows = []
    for bucket, group in frame.groupby("week", sort=True):
        if bucket + pd.Timedelta(days=7) > now:
            continue
        group = group.sort_values("timestamp")
        rows.append({
            "timestamp": bucket,
            "open": float(group.iloc[0]["open"]),
            "high": float(group["high"].max()),
            "low": float(group["low"].min()),
            "close": float(group.iloc[-1]["close"]),
            "volume": float(group["volume"].sum()),
            "trades": int(group["trades"].sum()),
        })
    result = pd.DataFrame(rows, columns=columns)
    if not result.empty:
        result = result.sort_values("timestamp").reset_index(drop=True)
    result.attrs.update(daily.attrs)
    result.attrs["source"] = "Kraken bulk daily OHLCVT + live REST aggregated to completed 1W"
    result.attrs["aggregation"] = "UTC Monday-start weeks; observed daily candles only"
    return result
