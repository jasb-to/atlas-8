# FRIDAY weekly thesis forensic audits

This directory is reserved for standalone audit scripts. Do not merge or integrate the weekly filter into the live monitor until the audits and historical comparison have been run and reviewed.

Required proofs:

1. Mid-week freeze: weekly indicators remain identical before the weekly candle closes.
2. Daily flip-flop replay: daily veto decisions are evaluated at completed daily closes and held for the next 24 hours.
3. Trade-by-trade diff: baseline `main` and PR branch with `weekly_thesis=None` produce identical canonical trade records.
4. Alert math: invalid trade geometry is rejected and never sent as a trade alert.
5. Filter comparison: compare filter-off and filter-on results on identical historical data; report trade count, win rate, net R, drawdown, fees, and slippage assumptions.

A green regression suite is not evidence of alpha. Keep all output paper/research-only.