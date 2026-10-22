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
- A trade-level diagnostic consolidates nearby same-direction break events, enters at the next 4H open, sizes to 0.5% account risk at a 1 ATR stop, targets 2R, and assumes 8 bps fee + 5 bps slippage per side. If stop and target are both inside one candle, stop is assumed first.\n- Stoch RSI bullish recross and positive/rising MACD expansion are soft context, not mandatory gates. A post-break retest is the preferred add signal; no impulse chasing.

## Run

```bash
python -m research.strategy_preview --pair XBTUSD --interval 240 --output-dir results/strategy-preview\npytest -q
```

Outputs `strategy_preview.json` and `strategy_preview.md`. The GitHub Actions workflow fetches fresh completed Kraken candles and uploads the preview artifact.

## Validation boundary

This is a live-data snapshot and causal replay, not a continuously running alert service or profitability claim. Trade replay is a first-pass OHLC approximation: it does not model concurrent positions, portfolio-wide exposure, partial fills, funding, spread variation, stop gaps, or realistic intrabar sequencing. The 720-candle sample is too short to validate an edge; walk-forward and multi-regime tests are required before production integration.
