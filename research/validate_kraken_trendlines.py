"""Fetch recent Kraken BTC/USD 4H candles and audit trendline geometry.

Run from the repository root:
    python -m research.validate_kraken_trendlines --output-dir results/trendline-audit

Uses Kraken's public spot OHLC endpoint; no API key is required. Kraken returns at most
720 OHLC rows and includes the current, possibly incomplete candle. The latter is dropped.
This audit reports observed geometry only; it is not a profitability backtest.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from datetime import datetime, timezone

import pandas as pd
import requests

from research.trendlines import (
    classify_line_state,
    find_wick_pivots,
    fit_touch_trendlines,
)

KRAKEN_OHLC_URL = "https://api.kraken.com/0/public/OHLC"


def fetch_ohlc(pair: str = "XBTUSD", interval: int = 240) -> pd.DataFrame:
    response = requests.get(
        KRAKEN_OHLC_URL,
        params={"pair": pair, "interval": interval},
        timeout=20,
        headers={"User-Agent": "atlas-8-research/1.0"},
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("error"):
        raise RuntimeError(f"Kraken API returned errors: {payload['error']}")
    result = payload.get("result", {})
    pair_key = next((key for key in result if key != "last"), None)
    if pair_key is None:
        raise RuntimeError(f"No OHLC pair data in Kraken response: {list(result)}")

    # Kraken row format: time, open, high, low, close, vwap, volume, trade_count.
    rows = result[pair_key]
    frame = pd.DataFrame(
        rows,
        columns=["timestamp", "open", "high", "low", "close", "vwap", "volume", "trades"],
    )
    frame["timestamp"] = pd.to_datetime(frame["timestamp"].astype(float), unit="s", utc=True)
    for col in ["open", "high", "low", "close", "vwap", "volume"]:
        frame[col] = pd.to_numeric(frame[col], errors="raise")
    frame["trades"] = pd.to_numeric(frame["trades"], errors="raise").astype(int)
    frame = frame.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)

    # REST OHLC always includes the current not-yet-committed candle as the final row.
    # Drop it explicitly; use only candles whose interval has fully elapsed.
    now = pd.Timestamp.now(tz="UTC")
    candle_end = frame["timestamp"] + pd.Timedelta(minutes=interval)
    frame = frame.loc[candle_end <= now].reset_index(drop=True)
    if len(frame) < 50:
        raise RuntimeError(f"Only {len(frame)} completed candles available; refusing to audit.")
    return frame


def audit(
    frame: pd.DataFrame, min_touches: int, tolerance_pct: float,
    pair: str = "XBTUSD", interval: int = 240,
) -> dict:
    pivots = find_wick_pivots(frame, left_bars=3, right_bars=3)
    lines = []
    for kind in ("support", "resistance"):
        lines.extend(
            fit_touch_trendlines(
                frame,
                pivots,
                kind=kind,
                min_touches=min_touches,
                tolerance_pct=tolerance_pct,
                max_penetration_pct=0.003,
                max_lines=20,
            )
        )
    lines.sort(key=lambda line: (len(line.touch_indices), line.end.index), reverse=True)

    line_rows = []
    for line in lines:
        state = classify_line_state(frame, line, tolerance_pct=tolerance_pct)
        line_rows.append(
            {
                "kind": line.kind,
                "start_index": line.start.index,
                "start_time": frame.iloc[line.start.index]["timestamp"].isoformat(),
                "start_wick_price": line.start.price,
                "start_confirmed_at": line.start.confirmed_at,
                "end_index": line.end.index,
                "end_time": frame.iloc[line.end.index]["timestamp"].isoformat(),
                "end_wick_price": line.end.price,
                "end_confirmed_at": line.end.confirmed_at,
                "slope_per_4h_candle": line.slope_per_bar,
                "touch_count": len(line.touch_indices),
                "touches": [
                    {
                        "index": idx,
                        "time": frame.iloc[idx]["timestamp"].isoformat(),
                        "wick_price": float(frame.iloc[idx]["low" if line.kind == "support" else "high"]),
                        "line_price": float(line.value_at(idx)),
                        "distance_pct": float(
                            (
                                float(frame.iloc[idx]["low" if line.kind == "support" else "high"])
                                - line.value_at(idx)
                            )
                            / abs(line.value_at(idx))
                            * 100
                        ),
                    }
                    for idx in line.touch_indices
                ],
                "current_line_price": state["line_value"],
                "current_close": state["close"],
                "current_distance_pct": state["distance_pct"],
                "current_state": state["state"],
                "current_near_line": state["near_line"],
                "current_retest_detected": state["retest_detected"],
            }
        )

    last = frame.iloc[-1]
    return {
        "source": "Kraken Spot public OHLC API",
        "pair": pair,
        "interval_minutes": interval,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "first_completed_candle_utc": frame.iloc[0]["timestamp"].isoformat(),
        "last_completed_candle_utc": last["timestamp"].isoformat(),
        "completed_candles": int(len(frame)),
        "last_close": float(last["close"]),
        "pivot_count": len(pivots),
        "candidate_line_count": len(line_rows),
        "settings": {
            "pivot_left_bars": 3,
            "pivot_right_bars": 3,
            "min_touches": min_touches,
            "touch_tolerance_pct": tolerance_pct,
            "max_penetration_pct": 0.003,
        },
        "lines": line_rows,
        "limitations": [
            "Kraken spot endpoint provides at most 720 candles per request.",
            "This is a geometry audit, not a trade-profitability test.",
            "Candidate ranking is diagnostic; it does not select a live trade.",
            "Retest state is preliminary and must be visually verified before strategy use.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pair", default="XBTUSD")
    parser.add_argument("--interval", type=int, default=240)
    parser.add_argument("--min-touches", type=int, default=3)
    parser.add_argument("--tolerance-pct", type=float, default=0.0015)
    parser.add_argument("--output-dir", default="results/trendline-audit")
    args = parser.parse_args()

    frame = fetch_ohlc(args.pair, args.interval)
    result = audit(frame, args.min_touches, args.tolerance_pct, args.pair, args.interval)
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    frame.to_csv(out / "kraken_ohlc.csv", index=False)
    (out / "trendline_audit.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "lines"}, indent=2))
    print(f"Detailed line/touch audit: {out / 'trendline_audit.json'}")
    print(f"Completed candles: {out / 'kraken_ohlc.csv'}")


if __name__ == "__main__":
    main()
