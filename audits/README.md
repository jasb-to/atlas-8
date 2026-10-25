# FRIDAY weekly thesis forensic audits

These audits are isolated research tooling. They do not wire the weekly filter into
friday_monitor.py, place orders, or authorize production alerts.

## 1. Alert geometry (local deterministic check)

~~~bash
python -m audits.audit_alert_math
~~~

Checks calculated TP1/TP2 R, malformed geometry, wrong-side stops, and sender refusal.
The exact input entry=8.822, stop=9.046 yields TP1 8.486; using stop
9.0467 instead yields 8.48495. Do not mix those examples.

## 2. Mid-week freeze

~~~bash
python -m audits.audit_midweek_freeze
~~~

Fetches recent weekly BTC candles and compares the last completed weekly close,
200-SMA and Stoch RSI at Wednesday noon versus Friday 23:59 in each eligible week.
It fails closed if fewer than 201 completed weekly candles are available. This
tests the completed-candle boundary; it does not prove every possible data adapter
or time-zone path is correct.

## 3. Daily flip-flop replay

~~~bash
python -m audits.audit_daily_flipflop
~~~

Fetches paginated OKX history for SOLUSDT and ETHUSDT, replays completed daily
closes across two years, and reports state transitions plus returns to the prior
state within five days. This is measurement, not an automatic hysteresis change.
The daily decision is sampled at each completed daily close and is therefore held
for the following 24 hours.

## 4. Trade-by-trade regression diff

Generate the same canonical trade_replay.jsonl output from main and from this
PR with weekly_thesis=None, keeping candles and replay settings identical. Save
the two files separately, then run:

~~~bash
python -m audits.audit_trade_diff \
  --baseline /tmp/main-trade_replay.jsonl \
  --candidate /tmp/pr-filter-disabled-trade_replay.jsonl
~~~

This is an exact byte comparison of the saved outputs. The script deliberately
does not claim a pass if either artifact is missing.

## 5. Filter-on versus filter-off comparison

~~~bash
python -m audits.backtest_filter_comparison
~~~

Uses the same causal 4H break detector and replay engine for AVAXUSDT, SOLUSDT and
BTCUSDT from 2023-01-01 through 2024-10-01. Scenario B keeps only direction-matched
setups with an active completed-candle weekly/daily thesis at signal time. The
report includes trade count, win rate, net R, maximum drawdown and net equity
return. Win-rate improvement alone is not proof of alpha; compare net R, drawdown,
trade count, and execution-cost assumptions.

## Important limitations

- These scripts fetch public market data and have not been run as part of this commit.
- Do not paste a PASS claim into the PR until the command was actually run and its
  output saved with the relevant code SHA and data window.
- The main vs PR diff tests regression safety. Filter-off vs filter-on tests the
  strategy hypothesis. They answer different questions.
- No live monitor integration, deployment, exchange orders, or main-channel alerts
  are authorized by these audits.
