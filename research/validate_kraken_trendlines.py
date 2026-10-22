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

    # Kraken REST OHLC includes the current, potentially incomplete candle.
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


def plot_audit(frame: pd.DataFrame, result: dict, output_path: Path) -> None:
    """Plot a small, deduplicated set of recent lines near price, plus their real wick contacts."""
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt

    cutoff = frame["timestamp"].iloc[-1] - pd.Timedelta(days=30)
    cutoff_index = int(frame["timestamp"].searchsorted(cutoff))
    visible = frame.loc[frame["timestamp"] >= cutoff]
    last_close = float(frame.iloc[-1]["close"])

    # The full JSON retains historical diagnostics. The chart only shows recent,
    # price-relevant candidates so stale, distant projections cannot swamp the view.
    eligible = [
        row for row in result["lines"]
        if int(row["start_index"]) >= cutoff_index
        and int(row["end_index"]) >= cutoff_index
        and abs(float(row["current_line_price"]) - last_close) / last_close <= 0.06
    ]
    eligible.sort(
        key=lambda row: (
            -int(row["touch_count"]),
            abs(float(row["current_line_price"]) - last_close) / last_close,
            -int(row["end_index"]),
        )
    )
    selected = []
    for row in eligible:
        same_kind_too_close = any(
            existing["kind"] == row["kind"]
            and abs(float(existing["current_line_price"]) - float(row["current_line_price"]))
                / last_close < 0.005
            for existing in selected
        )
        if same_kind_too_close:
            continue
        selected.append(row)
        if sum(item["kind"] == row["kind"] for item in selected) >= 3:
            continue
        if len(selected) >= 6:
            break

    fig, ax = plt.subplots(figsize=(15, 7))
    for row in visible.itertuples(index=False):
        x = mdates.date2num(row.timestamp.to_pydatetime())
        color = "black" if row.close >= row.open else "grey"
        ax.vlines(x, row.low, row.high, color=color, linewidth=0.7)
        ax.vlines(x, min(row.open, row.close), max(row.open, row.close), color=color, linewidth=2)

    for line in selected:
        start_time = pd.Timestamp(line["start_time"])
        end_time = pd.Timestamp(line["end_time"])
        ax.plot(
            [start_time, frame["timestamp"].iloc[-1]],
            [line["start_wick_price"], line["current_line_price"]],
            linewidth=1.5,
            alpha=0.9,
            label=f'{line["kind"]} · {line["touch_count"]} touches · {line["current_distance_pct"]:.2f}%',
        )
        ax.scatter(
            [start_time, end_time],
            [line["start_wick_price"], line["end_wick_price"]],
            s=30,
        )
        for touch in line["touches"]:
            touch_time = pd.Timestamp(touch["time"])
            if touch_time >= cutoff:
                ax.scatter(touch_time, touch["wick_price"], marker="x", s=30)

    ax.set_xlim(cutoff, frame["timestamp"].iloc[-1] + pd.Timedelta(hours=4))
    ax.set_ylim(float(visible["low"].min()) * 0.98, float(visible["high"].max()) * 1.02)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))
    ax.grid(True, alpha=0.2)
    ax.set_title(
        f'{result["pair"]} {result["interval_minutes"]}m — recent relevant lines '
        f'({len(selected)} plotted; full candidates in JSON)'
    )
    ax.set_ylabel("Price (USD)")
    if selected:
        ax.legend(loc="upper left", fontsize=8, ncol=2)
    fig.tight_layout()
    fig.savefig(output_path, dpi=150)
    plt.close(fig)


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
    plot_audit(frame, result, out / "trendline_audit.png")
    (out / "trendline_audit.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "lines"}, indent=2))
    print(f"Detailed line/touch audit: {out / 'trendline_audit.json'}")
    print(f"Completed candles: {out / 'kraken_ohlc.csv'}")


if __name__ == "__main__":
    main()
