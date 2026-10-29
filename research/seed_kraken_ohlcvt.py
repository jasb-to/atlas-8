"""Build FRIDAY's compact persisted history from Kraken's official bulk OHLCVT ZIP.

The archive's CSVs are headerless and contain only traded intervals. This script
reads daily files for FRIDAY's fixed 15-symbol universe only, chooses USDT first
and USD only when no USDT history exists, then derives weekly OHLC from daily
candles. It never scans the exchange's live pair universe.
"""
from __future__ import annotations

import argparse
import io
import json
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

PAIRS = [
    "XBTUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT",
    "AVAXUSDT", "BNBUSDT", "VIRTUALUSDT", "HYPEUSDT", "DOGEUSDT",
    "LINKUSDT", "ZECUSDT", "NEARUSDT", "SUIUSDT", "UNIUSDT",
]
BASE_ALIASES = {"XBT": ["XBT", "XXBT"], "DOGE": ["XDG", "DOGE"]}
CSV_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume", "trades"]


def _candidate_names(pair: str) -> list[str]:
    quote = "USDT" if pair.endswith("USDT") else "USD"
    base = pair[:-4] if quote == "USDT" else pair[:-3]
    bases = BASE_ALIASES.get(base, [base])
    # Prefer USDT history; USD is still useful for structural analysis if USDT
    # history is absent. The quote is recorded and never silently relabelled.
    quotes = [quote, "USD"] if quote == "USDT" else [quote]
    names = []
    for candidate_quote in quotes:
        for candidate_base in bases:
            names.append(f"{candidate_base}{candidate_quote}_1440.csv")
    return names


def _read_csv(zf: zipfile.ZipFile, member: str) -> pd.DataFrame:
    with zf.open(member) as stream:
        frame = pd.read_csv(
            stream, header=None, names=CSV_COLUMNS, usecols=range(7),
            dtype={"timestamp": "float64", "open": "float64", "high": "float64",
                   "low": "float64", "close": "float64", "volume": "float64",
                   "trades": "float64"},
        )
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], unit="s", utc=True, errors="coerce")
    frame = frame.dropna(subset=["timestamp", "open", "high", "low", "close", "volume"])
    frame["trades"] = pd.to_numeric(frame["trades"], errors="coerce").fillna(0).astype(int)
    frame = frame.sort_values("timestamp").drop_duplicates("timestamp", keep="last").reset_index(drop=True)
    return frame


def _weekly_from_daily(daily: pd.DataFrame) -> pd.DataFrame:
    if daily.empty:
        return pd.DataFrame(columns=CSV_COLUMNS)
    frame = daily.copy()
    # Kraken's downloadable archive has daily, not weekly, CSVs. UTC weeks begin Monday.
    frame["week"] = frame["timestamp"].dt.to_period("W-SUN").dt.start_time.dt.tz_localize("UTC")
    rows = []
    now = pd.Timestamp.now(tz="UTC")
    for bucket, group in frame.groupby("week", sort=True):
        # Do not emit a week until its full seven-day window has closed.
        if bucket + pd.Timedelta(days=7) > now:
            continue
        group = group.sort_values("timestamp")
        rows.append({
            "timestamp": bucket.isoformat(),
            "open": float(group.iloc[0]["open"]),
            "high": float(group["high"].max()),
            "low": float(group["low"].min()),
            "close": float(group.iloc[-1]["close"]),
            "volume": float(group["volume"].sum()),
            "trades": int(group["trades"].sum()),
        })
    return pd.DataFrame(rows, columns=CSV_COLUMNS)


def seed(archive: Path, output: Path) -> dict:
    if not archive.is_file():
        raise FileNotFoundError(f"Kraken OHLCVT archive not found: {archive}")
    with zipfile.ZipFile(archive) as zf:
        by_basename: dict[str, str] = {}
        for member in zf.namelist():
            basename = Path(member).name
            if re.fullmatch(r"[A-Z0-9]+_1440\.csv", basename):
                by_basename.setdefault(basename, member)

        seeded = {}
        missing = []
        for pair in PAIRS:
            selected = next((name for name in _candidate_names(pair) if name in by_basename), None)
            if selected is None:
                missing.append(pair)
                print(f"[OHLCVT SEED] {pair}: no matching daily CSV in archive", flush=True)
                continue
            daily = _read_csv(zf, by_basename[selected])
            if daily.empty:
                missing.append(pair)
                print(f"[OHLCVT SEED] {pair}: {selected} is empty", flush=True)
                continue
            source_pair = selected.removesuffix("_1440.csv")
            quote = "USDT" if source_pair.endswith("USDT") else "USD"
            weekly = _weekly_from_daily(daily)
            def serialise(frame: pd.DataFrame) -> list[dict]:
                rows = frame.copy()
                rows["timestamp"] = pd.to_datetime(rows["timestamp"], utc=True).map(lambda x: x.isoformat())
                rows = rows.where(pd.notna(rows), None)
                return rows.to_dict(orient="records")
            seeded[pair] = {
                "source_pair": source_pair,
                "quote_currency": quote,
                "daily": serialise(daily),
                "weekly": serialise(weekly),
            }
            print(
                f"[OHLCVT SEED] {pair} <= {source_pair} | daily={len(daily)} "
                f"| weekly={len(weekly)} | first={daily.iloc[0]['timestamp'].isoformat()} "
                f"| last={daily.iloc[-1]['timestamp'].isoformat()}",
                flush=True,
            )

    if not seeded:
        raise RuntimeError("No FRIDAY market histories were found in the Kraken archive.")
    payload = {
        "schema_version": 1,
        "source": "Kraken downloadable historical OHLCVT",
        "source_archive": archive.name,
        "seeded_at_utc": datetime.now(timezone.utc).isoformat(),
        "pair_count": len(seeded),
        "missing_pairs": missing,
        "pairs": seeded,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, separators=(",", ":"), allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(output)
    print(f"[OHLCVT SEED] Wrote {output} | pairs={len(seeded)} | missing={','.join(missing) or 'none'}")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--output", default=Path("testing/friday/data/ohlc_history.json"), type=Path)
    args = parser.parse_args()
    seed(args.archive, args.output)


if __name__ == "__main__":
    main()
