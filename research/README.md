# Atlas-8 trendline-break strategy preview

Research-only. This prototype is isolated from production CX Switch and never places orders.

## Strategy concept

- BTC/USD spot, completed 4-hour candles.
- Trendlines must pass through actual same-side wick pivots, with at least three independent contacts and no material penetration between anchors.
- Each replay decision rebuilds lines from only the candles then available. Pivots are eligible only after their right-hand confirmation candles have closed.
- Probe: a 4H candle closes at least 0.15% through resistance (long) or support (short); the prior close must not already be beyond the threshold.
- Add: after the break, price has up to 18 completed 4H candles (72 hours) to revisit the line within 0.30% and close holding the breakout side.
- Failure: a close crosses back through the line by 0.30%.
- Context is reported, not hard-gated: completed UTC daily EMA 8/21 bias (lagged one completed day), 4H EMA 8/21, Stoch RSI K/D and ATR.
- Outcomes report directional close-to-close returns after 3/6/12/18 bars (12/24/48/72 hours), excluding fees, slippage, stops and sizing.

## Run

```bash
python -m research.strategy_preview --pair XBTUSD --interval 240 --output-dir results/strategy-preview
```

Outputs `strategy_preview.json` and `strategy_preview.md`. The GitHub Actions workflow fetches fresh completed Kraken candles and uploads the preview artifact.

## Validation boundary

This is a live-data snapshot and causal event replay, not a continuously running alert service or profitability claim. Stops, ATR-based sizing, portfolio risk caps, fees, slippage, intrabar stop/target ordering and sustained-trend exits remain to be tested before production integration.
