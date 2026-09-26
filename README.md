# Atlas-8

Research-only crypto strategy backtesting project.

## Universe

BTC, ETH, SOL, HYPE, SUI, LINK, XRP, DOGE.

## Public market data

No CoinGecko account or API key is required.

- BTC, ETH, SOL, SUI, LINK, XRP and DOGE: Binance USDⓈ-M perpetual OHLCV.
- HYPE: native Hyperliquid perpetual OHLCV through its public candle API.
- Binance candles are paginated so the available history is downloaded.
- Hyperliquid exposes a maximum of 5,000 candles per interval; HYPE 4H therefore uses that maximum public window, while HYPE daily covers its available history.
- Only completed candles are included.

## Strategy families

1. Trend Pullback — 200 EMA regime + Supertrend + RSI recovery.
2. Breakout — Donchian breakout + ATR expansion + volume/regime confirmation.
3. Mean Reversion — range regime + VWAP/RSI reversion.
4. Hybrid — regime classifier selects trend, breakout, or mean-reversion logic.

## Research standards

- Exact exchange OHLCV candles; no CoinGecko pseudo-OHLC.
- No look-ahead.
- Signals evaluated only on closed candles.
- Fees and slippage are configurable.
- Long and short tested separately and combined.
- Results include win rate, profit factor, expectancy, net return, max drawdown and trade count.
- Walk-forward / out-of-sample testing is required before treating a result as robust.
- Published results identify the exact data source and window.
