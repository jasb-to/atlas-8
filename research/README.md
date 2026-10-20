# Trendline geometry prototype

This is an isolated research module. It is not connected to live alerts, the production CX Switch strategy, or an order-execution path.

## What it does

- Finds strict wick-high and wick-low pivots from completed OHLC candles.
- Records the candle index where each pivot becomes confirmed (confirmed_at).
- Builds support/resistance candidates through actual wick prices.
- Counts distinct wick contacts within a configurable price tolerance.
- Rejects a candidate if candles between its anchor points materially penetrate the line.
- Classifies a selected line's current closed-candle state as approaching, watch, breakout, or breakdown, and exposes a preliminary retest flag.

## Minimal example

    from research.trendlines import find_wick_pivots, fit_touch_trendlines, classify_line_state
    pivots = find_wick_pivots(ohlcv, left_bars=3, right_bars=3)
    supports = fit_touch_trendlines(ohlcv, pivots, kind="support", min_touches=3, tolerance_pct=0.0015)
    if supports:
        print(supports[0].as_dict())
        print(classify_line_state(ohlcv, supports[0]))

ohlcv must be a chronologically ordered pandas DataFrame with high, low, and close columns. tolerance_pct=0.0015 means 0.15% of the line price.

## Important research constraints

1. A pivot is not knowable until confirmed_at; never backtest a signal as if the pivot were known at its historical wick candle.
2. Lines are validated between their anchors. Later breaks must remain visible rather than causing the historical line to disappear.
3. Retest classification is a first-pass diagnostic, not a complete trade rule. It must be tested against chart examples and historical replay before strategy integration.
4. This module deliberately does not decide trade direction, calculate size, or place orders.
5. The next validation step is to render anchor wicks, every counted touch, line price, and distance-to-line on representative BTC 4H charts and manually verify them.