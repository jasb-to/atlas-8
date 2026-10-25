"""Focused regression tests for the isolated trendline geometry module."""
import unittest

import pandas as pd

from research.trendlines import Pivot, Trendline, classify_line_state, fit_touch_trendlines


class TrendlineGeometryTests(unittest.TestCase):
    def candles(self, lows, highs=None, closes=None):
        highs = highs or [low + 1 for low in lows]
        closes = closes or [(low + high) / 2 for low, high in zip(lows, highs)]
        return pd.DataFrame({"low": lows, "high": highs, "close": closes})

    def test_support_line_counts_three_independent_wick_pivots(self):
        frame = self.candles(
            [10.2, 10.0, 10.2, 10.0, 10.2, 10.0, 9.7],
            highs=[11.0] * 7,
            closes=[10.5, 10.4, 10.5, 10.4, 10.5, 10.4, 9.8],
        )
        pivots = [
            Pivot(1, 10.0, "support", 2),
            Pivot(3, 10.0, "support", 4),
            Pivot(5, 10.0, "support", 6),
        ]
        lines = fit_touch_trendlines(
            frame, pivots, kind="support", min_touches=3, tolerance_pct=0.001
        )
        self.assertTrue(lines)
        line = lines[0]
        self.assertEqual(line.start.price, frame.iloc[1]["low"])
        self.assertEqual(line.end.price, frame.iloc[5]["low"])
        self.assertEqual(line.touch_indices, (1, 3, 5))
        state = classify_line_state(frame, line, index=6, tolerance_pct=0.001)
        self.assertEqual(state["state"], "breakdown")

    def test_clustered_near_line_candles_do_not_fake_three_touches(self):
        frame = self.candles(
            [10.0, 10.001, 10.002, 10.0, 10.001],
            highs=[11.0] * 5,
            closes=[10.5] * 5,
        )
        pivots = [Pivot(0, 10.0, "support", 1), Pivot(3, 10.0, "support", 4)]
        lines = fit_touch_trendlines(
            frame, pivots, kind="support", min_touches=3, tolerance_pct=0.001
        )
        self.assertEqual(lines, [])

    def test_default_touch_spacing_rejects_adjacent_ragged_pivots(self):
        frame = self.candles([10.0] * 8, highs=[11.0] * 8, closes=[10.5] * 8)
        pivots = [
            Pivot(1, 10.0, "support", 2),
            Pivot(3, 10.0, "support", 4),
            Pivot(5, 10.0, "support", 6),
        ]
        self.assertEqual(
            fit_touch_trendlines(frame, pivots, kind="support", min_touches=3),
            [],
        )

    def test_line_confirmation_uses_latest_qualifying_touch(self):
        frame = self.candles(
            [10.2, 10.0, 10.2, 10.0, 10.2, 10.0, 10.2, 10.0],
            highs=[11.0] * 8,
            closes=[10.5] * 8,
        )
        pivots = [
            Pivot(1, 10.0, "support", 2),
            Pivot(4, 10.0, "support", 5),
            Pivot(7, 10.0, "support", 8),
        ]
        lines = fit_touch_trendlines(
            frame, pivots, kind="support", min_touches=3, tolerance_pct=0.001
        )
        self.assertTrue(lines)
        self.assertEqual(lines[0].last_touch_index, 7)
        self.assertEqual(lines[0].confirmed_at, 8)

    def test_fit_requires_positional_range_index(self):
        frame = self.candles([10.0, 10.1, 10.2], highs=[11.0, 11.1, 11.2])
        frame.index = pd.date_range("2026-01-01", periods=len(frame), freq="4h")
        with self.assertRaisesRegex(ValueError, "RangeIndex"):
            fit_touch_trendlines(frame, kind="support")

    def test_sub_dollar_lines_accept_explicit_price_floor(self):
        frame = self.candles(
            [0.0100, 0.0101, 0.0102, 0.0103],
            highs=[0.0110, 0.0111, 0.0112, 0.0113],
            closes=[0.0105, 0.0106, 0.0107, 0.0108],
        )
        pivots = [
            Pivot(0, 0.0100, "support", 1),
            Pivot(3, 0.0103, "support", 4),
        ]
        lines = fit_touch_trendlines(
            frame, pivots, kind="support", min_touches=2,
            tolerance_pct=0.002, min_price_eps=1e-6,
        )
        self.assertTrue(lines)

    def test_line_value_is_exact_interpolation_between_wick_anchors(self):
        frame = self.candles([10, 10.1, 10.2, 10.3], highs=[11, 11.1, 11.2, 11.3])
        pivots = [Pivot(0, 10, "support", 1), Pivot(3, 10.3, "support", 4)]
        lines = fit_touch_trendlines(
            frame, pivots, kind="support", min_touches=2, tolerance_pct=0.002
        )
        self.assertTrue(lines)
        self.assertAlmostEqual(lines[0].value_at(2), 10.2, places=6)

    def test_pre_break_touch_is_not_reported_as_retest(self):
        frame = self.candles(
            [9.95, 9.96, 9.94, 9.97],
            highs=[10.0, 10.0, 10.0, 10.0],
            closes=[9.98, 9.97, 9.96, 9.98],
        )
        line = Trendline(
            kind="resistance",
            start=Pivot(0, 10.0, "resistance", 1),
            end=Pivot(1, 10.0, "resistance", 2),
            slope_per_bar=0.0,
            tolerance=0.01,
            touch_indices=(0, 1),
            violations=(),
        )
        state = classify_line_state(frame, line, index=3, tolerance_pct=0.001)
        self.assertFalse(state["retest_detected"])

    def test_retest_requires_breakout_then_later_reclaim(self):
        frame = self.candles(
            [9.95, 9.96, 9.94, 10.08, 10.0, 10.05],
            highs=[10.0, 10.0, 10.0, 10.25, 10.15, 10.2],
            closes=[9.98, 9.97, 9.96, 10.2, 10.1, 10.12],
        )
        line = Trendline(
            kind="resistance",
            start=Pivot(0, 10.0, "resistance", 1),
            end=Pivot(1, 10.0, "resistance", 2),
            slope_per_bar=0.0,
            tolerance=0.01,
            touch_indices=(0, 1),
            violations=(),
        )
        state = classify_line_state(frame, line, index=5, tolerance_pct=0.001)
        self.assertTrue(state["retest_detected"])
        self.assertEqual(state["retest_index"], 4)

    def test_broken_state_persists_after_initial_break_candle(self):
        frame = self.candles(
            [10.0, 9.8, 9.7, 9.6, 9.5],
            highs=[11.0, 10.5, 10.2, 10.0, 9.9],
            closes=[10.5, 9.9, 9.8, 9.7, 9.6],
        )
        line = Trendline(
            kind="support",
            start=Pivot(0, 10.0, "support", 1),
            end=Pivot(1, 10.0, "support", 2),
            slope_per_bar=0.0,
            tolerance=0.01,
            touch_indices=(0, 1),
            violations=(),
        )
        state = classify_line_state(frame, line, index=4, tolerance_pct=0.001)
        self.assertEqual(state["state"], "breakdown")


if __name__ == "__main__":
    unittest.main()
