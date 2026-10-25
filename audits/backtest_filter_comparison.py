"""Filter-off vs filter-on FRIDAY historical comparison (research only).

Run from the repository root. Uses the same causal 4H event detector and trade
replay for both scenarios. The only scenario difference is whether a setup is
retained when the completed-candle weekly/daily thesis is active at signal time.
The weekly filter uses EMA 8/21 plus Stoch RSI alignment. The evaluation window
starts in 2025 for a consistent, mature sample across AVAX, SOL and BTC; every
signal is evaluated using completed weekly and daily candles available at that time.
"""
from __future__ import annotations

import pandas as pd

from audits._historical_data import fetch_history
from research.strategy_preview import causal_replay
from research.sheldon_strategy import consolidate_setups, replay_trades
from research.weekly_thesis import evaluate_sheldon_thesis

START = pd.Timestamp("2025-01-01T00:00:00Z")
END = pd.Timestamp("2026-10-01T00:00:00Z")
DATA_START = "2018-01-01"
INDICATOR_START = "2018-01-01"
# Include extra candles beyond the last signal to resolve the 72-hour max hold.
DATA_END = "2026-10-06"


def metrics(trades: list[dict]) -> dict:
    returns = [float(t["net_equity_return_pct"]) / 100.0 for t in trades]
    equity, peak, max_dd = 1.0, 1.0, 0.0
    for ret in returns:
        equity *= 1.0 + ret
        peak = max(peak, equity)
        max_dd = min(max_dd, equity / peak - 1.0)
    wins = sum(float(t["net_r_multiple"]) > 0 for t in trades)
    return {
        "trades": len(trades),
        "win_rate_pct": 100.0 * wins / len(trades) if trades else 0.0,
        "net_r": sum(float(t["net_r_multiple"]) for t in trades),
        "max_drawdown_pct": abs(max_dd) * 100.0,
        "net_equity_return_pct": (equity - 1.0) * 100.0,
    }


def fmt(value: float) -> str:
    return f"{value:.2f}"


def main():
    results = []
    for pair in ("AVAXUSDT", "SOLUSDT", "BTCUSDT"):
        print(f"Fetching historical candles for {pair}...", flush=True)
        candles = fetch_history(pair, "4H", DATA_START, DATA_END)
        daily = fetch_history(pair, "1D", INDICATOR_START, DATA_END)
        weekly = fetch_history(pair, "1W", INDICATOR_START, DATA_END)

        events, _status, frame = causal_replay(candles)
        setups = consolidate_setups(events, frame)
        in_range = [
            dict(event) for event in setups
            if START <= pd.Timestamp(event["signal_time"]) < END
        ]
        scenario_a_trades = replay_trades(in_range, frame)

        retained, rejected = [], []
        status_counts: dict[str, int] = {}
        thesis_cache = {}
        for event in in_range:
            signal_time = pd.Timestamp(event["signal_time"])
            # The event timestamp identifies the 4H candle open; the break is
            # confirmed at that candle's close, four hours later.
            as_of = signal_time + pd.Timedelta(hours=4)
            key = as_of.floor("D").isoformat()
            if key not in thesis_cache:
                thesis_cache[key] = evaluate_sheldon_thesis(
                    pair,
                    weekly_candles=weekly,
                    daily_candles=daily,
                    as_of=as_of,
                )
            thesis = thesis_cache[key]
            status_counts[thesis["status"]] = status_counts.get(thesis["status"], 0) + 1
            if thesis["status"] == "active" and thesis["direction"] == event["direction"]:
                retained.append(event)
            else:
                rejected.append((event, thesis))
        scenario_b_trades = replay_trades(retained, frame)
        a, b = metrics(scenario_a_trades), metrics(scenario_b_trades)
        results.append((pair, a, b, len(in_range), len(retained), len(rejected), status_counts))

    print("\n## FRIDAY filter comparison")
    print(f"Date range: {START.date()} to 2026-09-30 (UTC signal timestamps)")
    print("Execution: existing causal 4H replay; next-open entry; 1 ATR stop; 2R target; 8 bps fee + 5 bps slippage per side; stop-first same-candle assumption.")
    print("Filter-on retains only setups whose thesis is active and direction-matched at the completed-candle signal timestamp.")
    print("\n| Pair | Scenario | Signals | Trades | Win rate | Net R | Max drawdown | Net equity return |")
    print("|---|---|---:|---:|---:|---:|---:|---:|")
    for pair, a, b, signals, retained, rejected, status_counts in results:
        print(f"| {pair} | A — filter off | {signals} | {a['trades']} | {fmt(a['win_rate_pct'])}% | {fmt(a['net_r'])}R | {fmt(a['max_drawdown_pct'])}% | {fmt(a['net_equity_return_pct'])}% |")
        print(f"| {pair} | B — filter on | {retained} | {b['trades']} | {fmt(b['win_rate_pct'])}% | {fmt(b['net_r'])}R | {fmt(b['max_drawdown_pct'])}% | {fmt(b['net_equity_return_pct'])}% |")
        print(f"  Thesis status counts for {pair}: {status_counts}")
    print("\nInterpretation: compare net R, drawdown and sample size alongside win rate. A higher win rate alone does not establish alpha.")
    print("Limitations: public exchange history, candle-based fills, no funding/borrow costs, no intrabar order-book data; research only, no orders.")


if __name__ == "__main__":
    main()
