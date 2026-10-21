"""Research-only learning diagnostics for Friday.

Summarises frozen decision journals and replay outcomes. It never changes strategy
parameters or places trades; promotion decisions require separate walk-forward review.
"""
from __future__ import annotations

import argparse
import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


def read_jsonl(path: str | Path | None) -> list[dict[str, Any]]:
    if not path:
        return []
    source = Path(path)
    if not source.exists():
        return []
    rows = []
    with source.open(encoding="utf-8") as stream:
        for n, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSONL in {source} at line {n}") from exc
            if not isinstance(row, dict):
                raise ValueError(f"expected JSON object in {source} at line {n}")
            rows.append(row)
    return rows


def _finite(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _trade_stats(trades: list[dict[str, Any]]) -> dict[str, Any]:
    returns = [_finite(t.get("net_equity_return_pct")) for t in trades]
    returns = [x for x in returns if x is not None]
    wins = [x for x in returns if x > 0]
    losses = [x for x in returns if x < 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    equity = peak = 1.0
    max_drawdown_pct = 0.0
    for value in returns:
        equity *= 1 + value / 100
        peak = max(peak, equity)
        if peak > 0:
            max_drawdown_pct = max(max_drawdown_pct, (peak - equity) / peak * 100)
    return {
        "trades": len(returns),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": round(100 * len(wins) / len(returns), 2) if returns else None,
        "net_equity_return_pct_compounded": round((equity - 1) * 100, 4) if returns else 0.0,
        "mean_net_equity_return_pct": round(sum(returns) / len(returns), 4) if returns else None,
        "profit_factor": round(gross_profit / gross_loss, 4) if gross_loss else (None if not gross_profit else "infinite"),
        "max_drawdown_pct": round(max_drawdown_pct, 4),
        "exit_reasons": _counts(t.get("exit_reason", "unknown") for t in trades),
        "target_types": _counts(t.get("target_type", t.get("target_mode", "unknown")) for t in trades),
        "long_trades": sum(str(t.get("direction", "")).lower() in ("long", "bullish") for t in trades),
        "short_trades": sum(str(t.get("direction", "")).lower() in ("short", "bearish") for t in trades),
    }


def _counts(values: Iterable[Any]) -> dict[str, int]:
    result: dict[str, int] = {}
    for value in values:
        key = str(value or "unknown")
        result[key] = result.get(key, 0) + 1
    return dict(sorted(result.items()))


def _walk_forward_variant(trades: list[dict[str, Any]], split_time: str,
                          last_candle_time: str, max_hold_bars: int = 18) -> dict[str, Any]:
    """Chronological train/test summary; exclude trades crossing split or right-censored at data end."""
    split = datetime.fromisoformat(split_time.replace("Z", "+00:00"))
    last = datetime.fromisoformat(last_candle_time.replace("Z", "+00:00"))
    train, test, excluded = [], [], 0
    for trade in trades:
        try:
            entry = datetime.fromisoformat(str(trade["entry_time"]).replace("Z", "+00:00"))
            exit_time = datetime.fromisoformat(str(trade["exit_time"]).replace("Z", "+00:00"))
        except (KeyError, TypeError, ValueError):
            excluded += 1
            continue
        held = int(trade.get("bars_held", 0) or 0)
        censored = trade.get("exit_reason") == "time_exit" and held < max_hold_bars and exit_time >= last
        if censored or exit_time > last:
            excluded += 1
        elif exit_time < split:
            train.append(trade)
        elif entry >= split:
            test.append(trade)
        else:
            # A position spanning the split is not counted in either period.
            excluded += 1
    return {"train": _trade_stats(train), "test": _trade_stats(test), "excluded_cross_split_or_censored": excluded}


def build_learning_report(decisions: list[dict[str, Any]], trades: list[dict[str, Any]],
                         events: list[dict[str, Any]] | None = None,
                         structural_trades: list[dict[str, Any]] | None = None,
                         entry2_trades: list[dict[str, Any]] | None = None,
                         entry2_structural_trades: list[dict[str, Any]] | None = None,
                         split_time: str | None = None,
                         last_candle_time: str | None = None) -> dict[str, Any]:
    statuses = _counts(row.get("status", "unknown") for row in decisions)
    blockers = _counts(blocker for row in decisions for blocker in (row.get("blockers") or []))
    model_statuses = _counts((row.get("model") or {}).get("status", "unknown") for row in decisions)
    movement = {}
    event_rows = events if events is not None else []
    for horizon in (3, 6, 12, 18):
        values = [_finite(row.get(f"forward_{horizon}_bar_return_pct")) for row in event_rows]
        values = [v for v in values if v is not None]
        movement[f"{horizon}_bar"] = {
            "observations": len(values),
            "directional_positive_pct": round(100 * sum(v > 0 for v in values) / len(values), 2) if values else None,
            "mean_directional_return_pct": round(sum(values) / len(values), 4) if values else None,
        }
    by_version: dict[str, list[dict[str, Any]]] = {}
    by_direction: dict[str, list[dict[str, Any]]] = {}
    for trade in trades:
        by_version.setdefault(str(trade.get("strategy_version", "unknown")), []).append(trade)
        by_direction.setdefault(str(trade.get("direction", "unknown")).lower(), []).append(trade)
    return {
        "report_version": 1,
        "decision_count": len(decisions),
        "decision_statuses": statuses,
        "no_trade_count": sum(1 for row in decisions if str(row.get("status", "")).upper() == "NO_TRADE"),
        "common_blockers": blockers,
        "model_statuses": model_statuses,
        "replay": _trade_stats(trades),
        "structural_target_replay": _trade_stats(structural_trades or []),
        "entry2_replay": _trade_stats(entry2_trades or []),
        "entry2_structural_target_replay": _trade_stats(entry2_structural_trades or []),
        "replay_comparison_note": "Entry 1 probe and Entry 2 continuation replays, each with fixed-R and structural targets, are separate samples; compare only with matched periods, assumptions, and sample sizes.",
        "by_strategy_version": {key: _trade_stats(value) for key, value in sorted(by_version.items())},
        "by_direction": {key: _trade_stats(value) for key, value in sorted(by_direction.items())},
        "directional_forward_returns": movement,
        "walk_forward": ({
            "status": "ready",
            "split_time": split_time,
            "last_candle_time": last_candle_time,
            "variants": {
                "entry1_fixed_r": _walk_forward_variant(trades, split_time, last_candle_time),
                "entry1_structural_targets": _walk_forward_variant(structural_trades or [], split_time, last_candle_time),
                "entry2_fixed_r": _walk_forward_variant(entry2_trades or [], split_time, last_candle_time),
                "entry2_structural_targets": _walk_forward_variant(entry2_structural_trades or [], split_time, last_candle_time),
            },
        } if split_time and last_candle_time else {"status": "not_configured"}),
        "learning_policy": {
            "automatic_parameter_changes": False,
            "model_is_advisory_until_walk_forward_validation": True,
            "warning": "Forward-return diagnostics are not equivalent to executable, fee-aware trade outcomes.",
            "equity_curve_assumption": "Sequential trade-level compounding ignores overlapping positions and portfolio exposure; not a portfolio backtest.",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--decisions", default="results/strategy-preview/decision_journal.jsonl")
    parser.add_argument("--trades", default=None, help="JSONL replay rows with net equity return / exit fields")
    parser.add_argument("--events", default=None, help="JSONL breakout lifecycle events with forward-return diagnostics")
    parser.add_argument("--structural-trades", default=None, help="JSONL Entry 1 replay using prior-structure/Fibonacci-extension targets")
    parser.add_argument("--entry2-trades", default=None, help="JSONL replay for confirmed Entry 2 continuation triggers")
    parser.add_argument("--entry2-structural-trades", default=None, help="JSONL Entry 2 replay using prior-structure/Fibonacci-extension targets")
    parser.add_argument("--split-time", default=None, help="ISO timestamp splitting chronological train and test periods")
    parser.add_argument("--last-candle-time", default=None, help="ISO timestamp of final candle for censoring incomplete trades")
    parser.add_argument("--output", default="results/friday-learning-report.json")
    args = parser.parse_args()
    report = build_learning_report(
        read_jsonl(args.decisions), read_jsonl(args.trades), read_jsonl(args.events),
        read_jsonl(args.structural_trades), read_jsonl(args.entry2_trades),
        read_jsonl(args.entry2_structural_trades), args.split_time, args.last_candle_time,
    )
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({
        "output": str(destination),
        "decisions": report["decision_count"],
        "entry1_fixed_r": report["replay"],
        "entry1_structural_targets": report["structural_target_replay"],
        "entry2_fixed_r": report["entry2_replay"],
        "entry2_structural_targets": report["entry2_structural_target_replay"],
        "walk_forward": report["walk_forward"],
        "no_trade": report["no_trade_count"],
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
