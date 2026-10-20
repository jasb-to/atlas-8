"""Focused regression tests for the isolated trendline geometry module."""
import unittest

import pandas as pd

from research.trendlines import Pivot, classify_line_state, fit_touch_trendlines


class TrendlineGeometryTests(unittest.TestCase):
    def candles(self, lows, highs=None, closes=None):
        highs = highs or [low + 1 for low in lows]
        closes = closes or [(low + high) / 2 for low, high in zip(lows, highs)]
        return pd.DataFrame({"low": lows, "high": highs, "close": closes})

    def test_support_line_requires_real_wick_contacts(self):
        frame = self.candles(
            [10.0, 10.0, 10.005, 10.0, 10.004],
            highs=[11, 11, 11, 11, 11],
            closes=[10.5, 10.5, 10.5, 10.5, 10.5],
        )
        pivots = [
            Pivot(1, 10.0, "support", 2),
            Pivot(3, 10.0, "support", 4),
        ]
        lines = fit_touch_trendlines(
            frame, pivots, kind="support", min_touches=3, tolerance_pct=0.001
        )
        self.assertTrue(lines)
        self.assertEqual(lines[0].start.price, frame.iloc[1]["low"])
        self.assertEqual(lines[0].end.price, frame.iloc[3]["low"])
        self.assertIn(2, lines[0].touch_indices)

    def test_later_break_does_not_erase_formed_line(self):
        frame = self.candles(
            [10, 10, 10.005, 10, 9.7],
            highs=[11, 11, 11, 11, 10.2],
            closes=[10.5, 10.5, 10.5, 10.5, 9.8],
        )
        pivots = [Pivot(1, 10, "support", 2), Pivot(3, 10, "support", 4)]
        lines = fit_touch_trendlines(
            frame, pivots, kind="support", min_touches=3, tolerance_pct=0.001
        )
        self.assertTrue(lines)
        state = classify_line_state(frame, lines[0], index=4, tolerance_pct=0.001)
        self.assertEqual(state["state"], "breakdown")

    def test_line_value_is_exact_interpolation_between_wick_anchors(self):
        frame = self.candles([10, 10.1, 10.2, 10.3], highs=[11, 11.1, 11.2, 11.3])
        pivots = [Pivot(0, 10, "support", 1), Pivot(3, 10.3, "support", 4)]
        lines = fit_touch_trendlines(
            frame, pivots, kind="support", min_touches=2, tolerance_pct=0.002
        )
        self.assertTrue(lines)
        self.assertAlmostEqual(lines[0].value_at(2), 10.2, places=6)


if __name__ == "__main__":
    unittest.main()
