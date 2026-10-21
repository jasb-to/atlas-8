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
