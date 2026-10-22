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
- `alerts.json` — latest 10 alert-delivery records.

Git history is the audit trail for rolling state. GitHub Actions is the intended single writer; the monitor workflow is serialized to reduce concurrent write conflicts.

The Vercel Production environment needs `FRIDAY_GITHUB_TOKEN` with read access to repository contents. This variable is also used by the scan-dispatch endpoint, but its permissions must be checked without sharing the secret. Optional overrides are `FRIDAY_GITHUB_REPOSITORY` (default `jasb-to/atlas-8`) and `FRIDAY_GITHUB_DATA_BRANCH` (default `main`).

This is a temporary low-volume testing store, not a database. Each scan creates a Git commit and rolling JSON files have bounded retention. Repository contents may be visible to anyone with repository access. Do not write credentials, private user data, or exchange keys here. Revisit a proper database after the trial and after verifying the scan, persistence, and dashboard end to end.
