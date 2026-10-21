"""Research-only learning diagnostics for Friday.

Summarises frozen decision journals and replay outcomes. It never changes strategy
parameters or places trades; promotion decisions require separate walk-forward review.
"""
from __future__ import annotations

import argparse
import json
import math
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


def build_learning_report(decisions: list[dict[str, Any]], trades: list[dict[str, Any]],\n                         events: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    statuses = _counts(row.get("status", "unknown") for row in decisions)
    blockers = _counts(blocker for row in decisions for blocker in (row.get("blockers") or []))
    model_statuses = _counts((row.get("model") or {}).get("status", "unknown") for row in decisions)
    movement = {}
    for horizon in (3, 6, 12, 18):
        values = [_finite(row.get(f"forward_{horizon}_bar_return_pct")) for row in trades]
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
        "by_strategy_version": {key: _trade_stats(value) for key, value in sorted(by_version.items())},
        "by_direction": {key: _trade_stats(value) for key, value in sorted(by_direction.items())},
        "directional_forward_returns": movement,
        "learning_policy": {
            "automatic_parameter_changes": False,
            "model_is_advisory_until_walk_forward_validation": True,
            "warning": "Forward-return diagnostics are not equivalent to executable, fee-aware trade outcomes.",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--decisions", default="results/strategy-preview/decision_journal.jsonl")
    parser.add_argument("--trades", default=None, help="JSONL replay rows with net equity return / exit fields")\n    parser.add_argument("--events", default=None, help="JSONL breakout lifecycle events with forward-return diagnostics")
    parser.add_argument("--output", default="results/friday-learning-report.json")
    args = parser.parse_args()
    report = build_learning_report(read_jsonl(args.decisions), read_jsonl(args.trades), read_jsonl(args.events))
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(destination), "decisions": report["decision_count"],
                      "trades": report["replay"]["trades"], "no_trade": report["no_trade_count"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
