# Friday research archive — testing only

This directory is reserved for Friday's research and paper-testing outputs. It is not a live-trading ledger.

## Layout

`runs/YYYY-MM-DD/run-<workflow-id>-attempt-<n>/<PAIR>/`

Each pair folder may contain:

- `strategy_preview.json` — current setup and trade-plan diagnostics
- `decision_journal.jsonl` — timestamped decisions and signal state
- `trade_replay.jsonl` — Entry 1 replay outcomes
- `structural_target_replay.jsonl` — structural-target replay outcomes
- `entry2_trade_replay.jsonl` — Entry 2 replay outcomes
- `entry2_structural_target_replay.jsonl` — Entry 2 structural-target outcomes
- `break_events.jsonl` — trendline break/retest lifecycle events
- `learning_report.json` — summary and walk-forward statistics

GitHub Actions writes these files only for runs on `main`. Raw candle snapshots and general runtime logs remain in the expiring Actions artifact, rather than being committed to the repository.

**Important:** these are candidate/setup and simulated replay records, not executed exchange trades. Friday remains research-only; no order execution is enabled. Treat the reports as experimental and do not infer profitability from passing tests.
