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


## Temporary dashboard store (two-week trial)

The monitor also writes rolling JSON state to `data/`:

- `dashboard.json` — latest payload read by the dashboard API.
- `events.json` — latest 50 deduplicated setup/lifecycle events.
- `runs.json` — latest 500 scan-run records.
- `archive.json` — latest 1,000 idempotent archive records.
- `alerts.json` — bounded alert-deduplication history (latest 10 shown on dashboard).
- `paper_trades.json` — risk-defined paper positions, their stop/target, open/closed status and simulated R result.

Git history is the audit trail for rolling state. GitHub Actions is the intended single writer; the monitor workflow is serialized to reduce concurrent write conflicts.

The dashboard reads the public `dashboard.json` file without a GitHub API token. The Vercel Production scan-dispatch endpoint still needs `FRIDAY_CRON_SECRET` and `FRIDAY_GITHUB_TOKEN` with Actions dispatch permission; verify presence and scope without sharing the secret. Optional overrides are `FRIDAY_GITHUB_REPOSITORY` (default `jasb-to/atlas-8`) and `FRIDAY_GITHUB_DATA_BRANCH` (default `main`).

This is a temporary low-volume testing store, not a database. Each scan creates a Git commit and rolling JSON files have bounded retention. Repository contents may be visible to anyone with repository access. Do not write credentials, private user data, or exchange keys here. Revisit a proper database after the trial and after verifying the scan, persistence, and dashboard end to end.


## Trade tracking and filters

A candidate is not paper-opened unless its direction-specific momentum evidence passes, no stronger opposing exhaustion evidence blocks it, and the structural plan has valid stop/target geometry with at least 1.5R. Stoch RSI reversal candidates additionally require aligned exhaustion evidence. Open paper trades are checked against subsequent completed 4H OHLC candles; stop takes precedence if stop and target both fall inside one candle. This is a conservative approximation, not tick-level execution or exchange order management.

AI review is advisory and evidence-bound: it ranks/reviews deterministic candidates but cannot create prices or override hard filters. A run is AI-verified only when `ai_review_status` is `ok`; `not_configured` means no model call occurred.
