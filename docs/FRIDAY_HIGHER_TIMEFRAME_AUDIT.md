# FRIDAY higher-timeframe thesis lifecycle audit

## Confirmed code-level findings

1. `research/friday_monitor.py` computes `mtf = analyse_pair(pair=pair)` and archives the result, but the Entry 1 / Entry 2 notification loop independently iterates `result.get("break_events", [])`. The active daily thesis is not used to associate a 4H execution event with its originating higher-timeframe setup.
2. The multi-timeframe scanner stores snapshots under a generated-at timestamp. The monitor does not retrieve the previous thesis snapshot to carry forward a daily break, retest, invalidation, or expiry state.
3. `research/friday_multitimeframe.py` limits candidate lines to `MAX_LINE_DISTANCE = 0.035` from the current close. A previously broken higher-timeframe line can disappear from the nearby-line output after price moves away.
4. The 4H preview independently rebuilds trendline candidates and selects a nearest dashboard line while scanning all valid candidates for breaks. Alert events need stable line identity (timeframe, anchors, touch count, line value at break) to make repeat scans auditable.
5. The ETH alert with reference close 2306.21 and structural stop 2530.8816 has a stop distance of about 9.74%, inconsistent with the intended 1% paper risk cap. PR #27 proposes a cap but is unmerged and not yet CI-verified.

## Required strategy lifecycle

- Stage 1: Establish a causal daily/higher-timeframe thesis from completed candles, preserving the line, anchors, confirmed touches and break event.
- Stage 2: Persist and update the original break/retest/hold/reject/invalidation/expiry lifecycle across scans.
- Stage 3: Associate 4H or explicitly configured lower-timeframe execution triggers with that same thesis; do not relabel an unrelated later 4H support break as the original higher-timeframe Entry 1.
- Stage 4: Apply existing AI, momentum, exhaustion, risk, reward/risk and deduplication safeguards; log each pass/block/failure. Paper-only; no exchange orders.

## Incident limitation

These findings establish a code-path/architecture defect. The precise historical cause of the two ETH notifications (whether separate valid 4H lines, a late scan, or both) still requires the run's `strategy_preview.json`, `multitimeframe_scan.json`, `monitor_summary.json`, and alert-delivery records.
