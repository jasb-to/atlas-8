"""Six-month, alert-free baseline for FRIDAY's break/retest research path.

Input files are closed 4H OHLCV CSV or Parquet files named <PAIR>_4h.csv/.parquet.
The script never calls the monitor, sends alerts, invokes AI review, or places orders.
It refuses to claim a six-month baseline when any requested pair lacks full coverage.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd
import requests
import time

from research.friday_decision import evaluate_friday_decision
from research.friday_momentum_reversal import scan_momentum_reversal
from research.sheldon_strategy import consolidate_setups, replay_trades
from research.strategy_preview import (
    causal_replay,
    entry2_replay_events,
    indicators,
)
from research.trendlines import _validate_ohlc

DEFAULT_PAIRS = (
    "XBTUSDT,ETHUSDT,SOLUSDT,XRPUSDT,ADAUSDT,AVAXUSDT,BNBUSDT,"
    "VIRTUALUSDT,HYPEUSDT,DOGEUSDT,LINKUSDT,ZECUSDT,NEARUSDT,SUIUSDT,LITUSDT"
)
HORIZONS = (3, 6, 12, 18)


def _download_pair(data_dir: Path, pair: str, start: pd.Timestamp, end: pd.Timestamp) -> Path:
    """Download closed 4H candles from public Binance spot; HYPE may fall back to Hyperliquid."""
    data_dir.mkdir(parents=True, exist_ok=True)
    start_ms, end_ms = int(start.timestamp() * 1000), int(end.timestamp() * 1000)
    symbol = pair.replace("XBT", "BTC")
    step_ms = 4 * 60 * 60 * 1000
    rows: list[list[Any]] = []
    cursor = start_ms
    last_error: Exception | None = None
    while cursor < end_ms:
        try:
            response = requests.get(
                "https://data-api.binance.vision/api/v3/klines",
                params={"symbol": symbol, "interval": "4h", "startTime": cursor, "endTime": end_ms, "limit": 1000},
                timeout=30,
                headers={"User-Agent": "atlas-8-friday-baseline/1.0"},
            )
            response.raise_for_status()
            batch = response.json()
            if not isinstance(batch, list):
                raise RuntimeError(f"Unexpected Binance response for {symbol}: {batch}")
            if not batch:
                break
            rows.extend(batch)
            next_cursor = int(batch[-1][0]) + step_ms
            if next_cursor <= cursor:
                break
            cursor = next_cursor
            time.sleep(0.15)
            if len(batch) < 1000:
                break
        except (requests.RequestException, ValueError, RuntimeError) as exc:
            last_error = exc
            break

    if rows:
        frame = pd.DataFrame(rows, columns=[
            "open_time", "open", "high", "low", "close", "volume", "close_time",
            "quote_volume", "trades", "taker_buy_base", "taker_buy_quote", "ignore",
        ])
        frame["timestamp"] = pd.to_datetime(pd.to_numeric(frame["open_time"]), unit="ms", utc=True)
        for column in ("open", "high", "low", "close", "volume"):
            frame[column] = pd.to_numeric(frame[column], errors="raise")
        frame = frame[["timestamp", "open", "high", "low", "close", "volume"]]
    elif pair == "HYPEUSDT":
        # Hyperliquid is the fallback for HYPE if Binance spot history is unavailable.
        response = requests.post(
            "https://api.hyperliquid.xyz/info",
            json={"type": "candleSnapshot", "req": {"coin": "HYPE", "interval": "4h", "startTime": start_ms, "endTime": end_ms}},
            timeout=30,
            headers={"User-Agent": "atlas-8-friday-baseline/1.0"},
        )
        response.raise_for_status()
        payload = response.json()
        if not payload:
            raise RuntimeError(f"No 4H history for {pair}; Binance error was {last_error}")
        frame = pd.DataFrame(payload)
        frame["timestamp"] = pd.to_datetime(pd.to_numeric(frame["t"]), unit="ms", utc=True)
        for source, target in (("o", "open"), ("h", "high"), ("l", "low"), ("c", "close"), ("v", "volume")):
            frame[target] = pd.to_numeric(frame[source], errors="raise")
        frame = frame[["timestamp", "open", "high", "low", "close", "volume"]]
    else:
        raise RuntimeError(f"Could not download {pair} from Binance spot: {last_error}")
    frame = frame.drop_duplicates("timestamp").sort_values("timestamp").reset_index(drop=True)
    frame = frame[(frame["timestamp"] >= start) & (frame["timestamp"] + pd.Timedelta(hours=4) <= end)]
    if frame.empty:
        raise RuntimeError(f"Downloaded no closed 4H candles for {pair}")
    path = data_dir / f"{pair}_4h.parquet"
    frame.to_parquet(path, index=False)
    print(f"[baseline-data] {pair}: {len(frame)} candles from {frame.timestamp.iloc[0]} to {frame.timestamp.iloc[-1]}", flush=True)
    return path


def _read_pair(data_dir: Path, pair: str) -> pd.DataFrame:
    aliases = [pair, pair.replace("XBT", "BTC")]
    for alias in aliases:
        for suffix in (".parquet", ".csv"):
            path = data_dir / f"{alias}_4h{suffix}"
            if not path.exists():
                continue
            frame = pd.read_parquet(path) if suffix == ".parquet" else pd.read_csv(path)
            required = {"timestamp", "open", "high", "low", "close"}
            missing = required - set(frame.columns)
            if missing:
                raise ValueError(f"{path} missing columns: {sorted(missing)}")
            frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, errors="raise")
            for column in ("open", "high", "low", "close"):
                frame[column] = pd.to_numeric(frame[column], errors="raise")
            if "volume" not in frame:
                frame["volume"] = 0.0
            frame = frame.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)
            frame = frame[frame["timestamp"] < pd.Timestamp.now(tz="UTC").floor("4h")].reset_index(drop=True)
            _validate_ohlc(frame)
            return frame
    raise FileNotFoundError(
        f"No 4H CSV/Parquet for {pair} in {data_dir}; expected {pair}_4h.csv or .parquet"
    )


def _forward_returns(frame: pd.DataFrame, signal_time: str, direction: str) -> dict[str, Any]:
    matches = frame.index[frame["timestamp"] == pd.Timestamp(signal_time)]
    if not len(matches):
        return {f"forward_{h}_bar_return_pct": None for h in HORIZONS}
    index = int(matches[0])
    entry = float(frame.iloc[index]["close"])
    sign = 1.0 if direction == "long" else -1.0
    values: dict[str, Any] = {}
    for horizon in HORIZONS:
        end = index + horizon
        values[f"forward_{horizon}_bar_return_pct"] = (
            round((float(frame.iloc[end]["close"]) / entry - 1) * sign * 100, 6)
            if end < len(frame) and entry else None
        )
    return values


def _replay_reversal_candidate(candidate: dict[str, Any], frame: pd.DataFrame, *, max_hold_bars: int = 18) -> dict[str, Any] | None:
    """Conservatively replay a reversal signal after its candle has closed."""
    matches = frame.index[frame["timestamp"] == pd.Timestamp(candidate["signal_time"])]
    if not len(matches):
        return None
    signal_index = int(matches[0])
    entry_index = signal_index + 1
    if entry_index >= len(frame):
        return None
    direction = str(candidate.get("direction", "")).lower()
    if direction not in {"long", "short"} or candidate.get("stop") is None or candidate.get("target") is None:
        return None
    side = 1 if direction == "long" else -1
    entry = float(frame.iloc[entry_index]["open"])
    stop, target = float(candidate["stop"]), float(candidate["target"])
    risk = side * (entry - stop)
    if risk <= 0:
        return {
            "pair": candidate.get("pair"), "signal_time": candidate["signal_time"],
            "direction": direction, "eligible": False, "outcome": "invalid_next_open_vs_structural_stop",
            "entry_price": entry, "stop_price": stop, "target_price": target, "r_multiple": None,
        }
    exit_index = min(len(frame) - 1, entry_index + max_hold_bars - 1)
    exit_price = float(frame.iloc[exit_index]["close"])
    outcome = "time_exit"
    for i in range(entry_index, min(len(frame), entry_index + max_hold_bars)):
        row = frame.iloc[i]
        stop_hit = float(row["low"]) <= stop if side == 1 else float(row["high"]) >= stop
        target_hit = float(row["high"]) >= target if side == 1 else float(row["low"]) <= target
        if stop_hit:
            exit_index, exit_price, outcome = i, stop, "stop"
            break
        if target_hit:
            exit_index, exit_price, outcome = i, target, "target"
            break
    r_multiple = side * (exit_price - entry) / risk
    return {
        "pair": candidate.get("pair"), "signal_time": candidate["signal_time"],
        "direction": direction, "eligible": True, "entry_time": frame.iloc[entry_index]["timestamp"].isoformat(),
        "entry_price": entry, "stop_price": stop, "target_price": target,
        "exit_time": frame.iloc[exit_index]["timestamp"].isoformat(), "exit_price": exit_price,
        "outcome": outcome, "bars_held": exit_index - entry_index + 1,
        "r_multiple": round(r_multiple, 6), "win": bool(r_multiple > 0),
    }


def _safe_decision(frame: pd.DataFrame) -> dict[str, Any]:
    try:
        return evaluate_friday_decision(frame)
    except (ValueError, IndexError, KeyError, TypeError, ZeroDivisionError) as exc:
        return {"state": "ERROR", "reason": f"{type(exc).__name__}: {exc}"}


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    pd.DataFrame(rows).to_csv(path, index=False)


def run_baseline(
    *, data_dir: Path, output_dir: Path, pairs: list[str],
    start: pd.Timestamp, end: pd.Timestamp,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    all_events: list[dict[str, Any]] = []
    all_trades: list[dict[str, Any]] = []
    all_decisions: list[dict[str, Any]] = []
    all_reversals: list[dict[str, Any]] = []
    all_reversal_trades: list[dict[str, Any]] = []
    pair_summaries: list[dict[str, Any]] = []
    missing_coverage: list[str] = []

    for pair in pairs:
        raw = _read_pair(data_dir, pair)
        window = raw[(raw["timestamp"] >= start) & (raw["timestamp"] < end)].reset_index(drop=True)
        if window.empty or window["timestamp"].iloc[0] > start + pd.Timedelta(hours=4) or window["timestamp"].iloc[-1] < end - pd.Timedelta(days=2):
            missing_coverage.append(pair)
            continue

        events, _status, enriched, state_history = causal_replay(window, record_state_history=True)
        for event in events:
            row = {"pair": pair, **event, **_forward_returns(enriched, event["signal_time"], event["direction"])}
            all_events.append(row)

        setups = consolidate_setups(events, enriched)
        entry2_setups = entry2_replay_events(setups)
        entry1_trades = replay_trades(setups, enriched)
        entry2_trades = replay_trades(entry2_setups, enriched)
        for trade in entry1_trades:
            all_trades.append({"pair": pair, "entry_type": "ENTRY_1_BREAK_PROBE", **trade})
        for trade in entry2_trades:
            all_trades.append({"pair": pair, "entry_type": "ENTRY_2_CONTINUATION", **trade})

        state_by_time = {item["timestamp"]: item for item in state_history}
        for i in range(4, len(enriched)):
            prefix = enriched.iloc[:i + 1].reset_index(drop=True)
            candle_time = pd.Timestamp(prefix.iloc[-1]["timestamp"]).isoformat()
            decision = _safe_decision(prefix)
            lifecycle = state_by_time.get(candle_time, {"state": "NO_SETUP"})
            decision_direction = str(decision.get("direction") or "").lower()
            lifecycle_direction = str(lifecycle.get("direction") or "").lower()
            decision_state = str(decision.get("state") or "UNKNOWN")
            lifecycle_state = str(lifecycle.get("state") or "UNKNOWN")
            all_decisions.append({
                "pair": pair, "timestamp": candle_time,
                "friday_decision_state": decision_state,
                "friday_decision_direction": decision_direction or None,
                "friday_decision_reason": decision.get("reason"),
                "latest_setup_state": lifecycle_state,
                "latest_setup_direction": lifecycle_direction or None,
                "state_agreement": decision_state == lifecycle_state,
                "direction_agreement": bool(decision_direction and lifecycle_direction and decision_direction == lifecycle_direction),
                "decision_json": json.dumps(decision, sort_keys=True, default=str),
            })

            if i >= 60:
                reversal = scan_momentum_reversal(
                    prefix, pair=pair, thesis_direction=str(prefix.iloc[-1].get("daily_bias", ""))
                )
                if reversal.get("state") == "ENTRY_CANDIDATE":
                    candidate = {"pair": pair, "timestamp": candle_time, **reversal}
                    all_reversals.append(candidate)
                    replay = _replay_reversal_candidate(candidate, enriched)
                    if replay is not None:
                        all_reversal_trades.append(replay)

        pair_events = [event for event in all_events if event["pair"] == pair]
        pair_trades = [trade for trade in all_trades if trade["pair"] == pair]
        continuation = [event for event in pair_events if event.get("outcome") == "continuation_add_confirmed"]
        valid_forward = [event["forward_12_bar_return_pct"] for event in continuation if event.get("forward_12_bar_return_pct") is not None]
        pair_decisions = [row for row in all_decisions if row["pair"] == pair]
        pair_reversal_trades = [row for row in all_reversal_trades if row["pair"] == pair and row.get("eligible")]
        pair_summaries.append({
            "pair": pair,
            "candles": len(enriched),
            "first_candle": enriched.iloc[0]["timestamp"].isoformat(),
            "last_candle": enriched.iloc[-1]["timestamp"].isoformat(),
            "break_events": len(pair_events),
            "break_events_per_30_days": round(len(pair_events) / max((len(enriched) * 4 / 24) / 30, 1e-9), 4),
            "continuation_add_confirmed": len(continuation),
            "continuation_forward_12_bar_positive_rate_pct": round(sum(value > 0 for value in valid_forward) / len(valid_forward) * 100, 4) if valid_forward else None,
            "continuation_mean_forward_12_bar_return_pct": round(sum(valid_forward) / len(valid_forward), 6) if valid_forward else None,
            "entry1_trades": sum(t["entry_type"] == "ENTRY_1_BREAK_PROBE" for t in pair_trades),
            "entry2_trades": sum(t["entry_type"] == "ENTRY_2_CONTINUATION" for t in pair_trades),
            "mean_entry1_r": round(pd.Series([t["net_r_multiple"] for t in pair_trades if t["entry_type"] == "ENTRY_1_BREAK_PROBE"]).mean(), 6) if any(t["entry_type"] == "ENTRY_1_BREAK_PROBE" for t in pair_trades) else None,
            "mean_entry2_r": round(pd.Series([t["net_r_multiple"] for t in pair_trades if t["entry_type"] == "ENTRY_2_CONTINUATION"]).mean(), 6) if any(t["entry_type"] == "ENTRY_2_CONTINUATION" for t in pair_trades) else None,
            "momentum_reversal_replayed_trades": len(pair_reversal_trades),
            "momentum_reversal_hit_rate_pct": round(sum(row["win"] for row in pair_reversal_trades) / len(pair_reversal_trades) * 100, 4) if pair_reversal_trades else None,
            "momentum_reversal_mean_r": round(sum(row["r_multiple"] for row in pair_reversal_trades) / len(pair_reversal_trades), 6) if pair_reversal_trades else None,
            "friday_decision_lifecycle_state_agreement_pct": round(sum(row["state_agreement"] for row in pair_decisions) / len(pair_decisions) * 100, 4) if pair_decisions else None,
            "friday_decision_lifecycle_direction_agreement_pct_when_both_present": round(sum(row["direction_agreement"] for row in pair_decisions) / sum(bool(row["friday_decision_direction"] and row["latest_setup_direction"]) for row in pair_decisions) * 100, 4) if any(row["friday_decision_direction"] and row["latest_setup_direction"] for row in pair_decisions) else None,
        })

    _write_csv(output_dir / "break_events.csv", all_events)
    _write_csv(output_dir / "trade_replay.csv", all_trades)
    _write_csv(output_dir / "decision_alignment.csv", all_decisions)
    _write_csv(output_dir / "momentum_reversal_candidates.csv", all_reversals)
    _write_csv(output_dir / "momentum_reversal_replay.csv", all_reversal_trades)
    _write_csv(output_dir / "pair_summary.csv", pair_summaries)
    report = {
        "research_only": True,
        "alerts_enabled": False,
        "ai_review_enabled": False,
        "orders_placed": False,
        "window_start_utc": start.isoformat(),
        "window_end_utc": end.isoformat(),
        "requested_pairs": pairs,
        "pairs_completed": [row["pair"] for row in pair_summaries],
        "pairs_missing_full_window_coverage": missing_coverage,
        "break_events": len(all_events),
        "trade_replay_entries": len(all_trades),
        "friday_decision_rows": len(all_decisions),
        "momentum_reversal_candidates": len(all_reversals),
        "momentum_reversal_replayed_trades": len(all_reversal_trades),
        "momentum_reversal_mean_r": round(sum(row["r_multiple"] for row in all_reversal_trades if row.get("eligible")) / sum(bool(row.get("eligible")) for row in all_reversal_trades), 6) if any(row.get("eligible") for row in all_reversal_trades) else None,
        "outputs": ["break_events.csv", "trade_replay.csv", "decision_alignment.csv", "momentum_reversal_candidates.csv", "momentum_reversal_replay.csv", "pair_summary.csv"],
        "limitations": [
            "This harness requires a pre-downloaded full-window 4H OHLC file per pair; the live Kraken adapter's 720-row cap is insufficient for six months.",
            "Raw directional forward returns are descriptive, not fee-aware; trade_replay uses the existing conservative OHLC replay assumptions.",
            "Reversal replay enters at the next candle open, uses the candidate's structural stop/target, and assumes stop first if both are touched in one candle; costs are not included.",
        ],
    }
    (output_dir / "baseline_summary.json").write_text(json.dumps(report, indent=2, allow_nan=False))
    if missing_coverage:
        raise RuntimeError(f"Baseline incomplete: missing full six-month coverage for: {', '.join(missing_coverage)}")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("data/friday_baseline"))
    parser.add_argument("--output-dir", type=Path, default=Path("results/friday-baseline"))
    parser.add_argument("--pairs", default=DEFAULT_PAIRS)
    parser.add_argument("--download", action="store_true", help="Fetch the requested window from public market-data APIs before replay.")
    parser.add_argument("--start", default="2026-04-25T00:00:00Z")
    parser.add_argument("--end", default="2026-10-25T00:00:00Z")
    args = parser.parse_args()
    start = pd.Timestamp(args.start)
    end = pd.Timestamp(args.end)
    if start.tzinfo is None or end.tzinfo is None or start >= end:
        parser.error("--start and --end must be timezone-aware timestamps with start < end")
    pairs = [pair.strip().upper() for pair in args.pairs.split(",") if pair.strip()]
    if args.download:
        failed = []
        for pair in pairs:
            try:
                _download_pair(args.data_dir, pair, start.tz_convert("UTC"), end.tz_convert("UTC"))
            except Exception as exc:
                failed.append(f"{pair}: {type(exc).__name__}: {exc}")
        if failed:
            raise RuntimeError("Historical data download incomplete:\n" + "\n".join(failed))
    report = run_baseline(
        data_dir=args.data_dir, output_dir=args.output_dir,
        pairs=pairs,
        start=start.tz_convert("UTC"), end=end.tz_convert("UTC"),
    )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
