# Atlas-8

Research-only crypto chart-reading and strategy-validation project. It is separate from production CX Switch and does not place live orders.

## Primary objective

Build and validate a simple, explainable 4H trendline-breakout strategy that recognises the kind of price structure a skilled human chart reader would recognise. Improve the strategy before adding alerts, dashboards, integrations, or other extras.

## Friday

Friday is the project's chart-reading research assistant (formerly referred to as Jarvis). Friday should behave like a careful human analyst: read price structure in context, identify plausible support and resistance from genuine wick touches, recognise compression and trendline breaks, assess retests, and explain both the evidence and uncertainty.

Friday must not invent a setup to satisfy a request. It should be allowed to conclude **no trade**. Its interpretation must be checked against the deterministic strategy engine and historical candles; language confidence is not evidence of trading edge.

For every candidate setup, Friday should explain:
- Higher-timeframe bias and current 4H market structure.
- The actual wick anchors and number/quality of touches for each trendline.
- Whether price is approaching, breaking, retesting, holding, or invalidating the line.
- EMA alignment, Stochastic RSI timing, MACD/momentum, and signs of exhaustion.
- What supports the setup, what argues against it, and what would invalidate it.
- A proposed entry, stop, target and reward-to-risk only when the data supports them.

## Initial strategy rules

Keep the first version focused:
1. Daily bias provides context, not an automatic veto on every counter-trend setup.
2. Use completed 4H candles for decisions.
3. Build trendlines from causal, confirmed swing wicks and require meaningful touches.
4. Distinguish an early probe from a confirmed breakout-and-retest add.
5. Use EMA 8/21, Stochastic RSI and MACD as context/timing—not as a pile of hard gates.
6. Include momentum exhaustion so the strategy avoids blindly chasing an extended first impulse.
7. Define invalidation and cap total trade risk.

Avoid adding more indicators or filters unless out-of-sample evidence shows they improve results.

## Validation standards

- Use real exchange OHLCV data and record its source, pair, timeframe and exact date window.
- Exclude incomplete candles and prevent look-ahead, including pivot-confirmation leakage.
- Simulate entries only after the relevant candle has closed; state how the next entry price is chosen.
- Include fees and slippage, stop/target rules, and conservative handling when both are touched in one candle.
- Merge duplicate signals describing the same underlying setup.
- Report trade count, net expectancy, profit factor, win rate, drawdown, and results by market regime—not win rate alone.
- Use chronological walk-forward/out-of-sample tests and compare with simple baselines.
- Treat one chart or one market run as a diagnostic, not proof of profitability.

## Public market data

The research repository uses public exchange candle data. Each validation output must state the actual source used by that run; source descriptions must not be assumed to apply to every script or exchange pair.

## Scope control

Current priority: **make the setup detection, entry timing, invalidation, and trade-quality assessment reliable**. Telegram alerts, a richer UI, autonomous trading, and other extras come later.


## Friday monitoring, persistence, and run logs

The monitor is research/paper-only and does not place exchange orders.

- Manual/scheduled runner: `.github/workflows/friday-monitor.yml`
- Runtime: `python -m research.friday_monitor --pairs XBTUSD,VIRTUALUSD`
- Telegram formatter/sender: `research/friday_alerts.py`
- Persistence adapter: `research/friday_persistence.py`
- Dedicated research schema: `supabase/friday_research_schema.sql`

### Persistence model

Use a dedicated research Supabase project, not the production CX Switch database or its KV namespace. Apply `supabase/friday_research_schema.sql` manually only after confirming the target project. Configure GitHub Actions secrets:

- `FRIDAY_SUPABASE_URL`
- `FRIDAY_SUPABASE_SERVICE_ROLE_KEY` (server-side secret only)
- `FRIDAY_TELEGRAM_BOT_TOKEN`
- `FRIDAY_TELEGRAM_CHAT_ID`
- `HUGGINGFACE_API_KEY` (optional; never put it in code or logs)

Set repository variable `FRIDAY_HF_MODEL_ID` to the Hugging Face router-supported model ID to enable shadow review. Optional repository variable `FRIDAY_HF_ENDPOINT` overrides the default router endpoint. Friday remains rule-led if these are absent.

The workflow maps these to runtime environment variables. Without Supabase persistence, the monitor refuses to send Telegram alerts so it cannot claim reliable deduplication. Run logs and snapshots are still uploaded as GitHub Actions artifacts, retained for 30 days.

### Last 50 trades/setups

`friday_trade_events` is the bounded recent setup ledger: one idempotent row per Entry 1 setup, updated as retest, continuation add, failure, and outcome evidence becomes available. A database trigger retains the latest 50 records. Entry 1 and Entry 2 notifications have separate idempotency keys in `friday_alert_deliveries`, so a workflow retry does not resend a completed alert. `friday_cron_runs` retains the latest 500 run summaries with per-pair status, candle timestamp, event counts, persistence status, and errors. `friday_research_archive` stores idempotent per-candle decision snapshots and setup lifecycle payloads without the 50-row pruning limit; it is the durable research record for later analysis.

The 50-row table is the operational recent-history view, not the complete backtest archive. Long-run performance analysis belongs in versioned artifacts/backtest datasets.

### Cron and validation

GitHub Actions run logs are the first-level cron diagnostics; the artifact contains the per-pair JSON previews and `cron_run.json`. Supabase stores compact persistent run summaries. Schedule triggers run only from the repository's default branch, so while this work remains on `feature/price-touch-trendlines`, use `workflow_dispatch` to test it; do not merge into production/default branch just to activate the schedule. Validate a manual run and alert dedupe before enabling a recurring schedule.


## Friday learning loop and optional model review

- `research/friday_learning.py` builds a repeatable JSON report from decision-journal JSONL and replay-result JSONL. It tracks NO_TRADE frequency, common blockers, model status, win rate, compounded net return, profit factor, drawdown, exits, target types, direction splits, and available forward-return diagnostics.
- Example: `python -m research.friday_learning --decisions results/strategy-preview/decision_journal.jsonl --trades results/strategy-preview/trade_replay.jsonl --events results/strategy-preview/break_events.jsonl --output results/friday-learning-report.json`. The trade input must be JSONL replay rows; `--events` accepts lifecycle events containing forward-return diagnostics. Absent inputs are treated as empty.
- Reports do not automatically tune or promote strategy rules. Candidate changes must beat the baseline in chronological walk-forward testing, with fees/slippage, and must be checked for drawdown and 10%/20% directional-move capture.
- `research/friday_huggingface.py` is an optional Hugging Face shadow reviewer, wired into the preview journal. It reads `HUGGINGFACE_API_KEY` (or `HF_TOKEN`) and `FRIDAY_HF_MODEL_ID`. Optional endpoint override: `FRIDAY_HF_ENDPOINT`. It uses the Hugging Face router's OpenAI-compatible chat-completions interface, validates the response schema, records status/model/latency/errors, and fails soft.
- If credentials/model ID are absent, the journal explicitly records `not_configured`. The model can review but cannot change the rule-engine decision, invent a setup, or trigger an order. Model output stays advisory until it demonstrates out-of-sample value.
- Tests: `python -m unittest discover -s tests -p 'test_*.py' -v`.
