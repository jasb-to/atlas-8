"""Focused tests for the causal 4H break strategy preview."""
import unittest

import pandas as pd

from research.strategy_preview import break_crossed, line_retest, momentum_context
from research.trendlines import Pivot, Trendline


class StrategyPreviewTests(unittest.TestCase):
    def line(self, kind="resistance"):
        return Trendline(
            kind=kind,
            start=Pivot(0, 100.0, kind, 1),
            end=Pivot(2, 100.0, kind, 3),
            slope_per_bar=0.0,
            tolerance=0.15,
            touch_indices=(0, 2, 4),
            violations=(),
        )

    def test_resistance_break_requires_closed_close_and_prior_close_below(self):
        frame = pd.DataFrame({
            "high": [101, 101, 102],
            "low": [98, 99, 100],
            "close": [99.5, 100.1, 100.3],
        })
        self.assertTrue(break_crossed(frame, self.line("resistance"), 2))
        self.assertFalse(break_crossed(frame, self.line("resistance"), 1))

    def test_support_break_is_directional_breakdown(self):
        frame = pd.DataFrame({
            "high": [101, 101, 100],
            "low": [99, 98, 97],
            "close": [100.5, 99.9, 99.7],
        })
        self.assertTrue(break_crossed(frame, self.line("support"), 2))

    def test_retest_is_only_checked_after_break(self):
        frame = pd.DataFrame({
            "high": [100.2, 100.1, 100.3, 101],
            "low": [99.8, 99.7, 100.0, 100.2],
            "close": [100, 99.9, 100.1, 100.4],
        })
        found, index, held = line_retest(frame, self.line("resistance"), 1, 3)
        self.assertTrue(found)
        self.assertEqual(index, 2)
        self.assertTrue(held)


    def test_momentum_context_reports_stoch_recross_and_macd(self):
        frame = pd.DataFrame({
            "close": [90, 91, 92, 93, 94, 95, 96, 97, 98, 99],
            "stoch_k": [10, 12, 15, 20, 25, 30, 35, 40, 55, 70],
            "stoch_d": [12, 13, 14, 18, 22, 27, 32, 38, 45, 60],
        })
        context = momentum_context(frame, len(frame)-1)
        self.assertTrue(context["stoch_bullish_cross"])
        self.assertIn("macd_bullish_expansion", context)

    def test_retest_window_allows_multiple_days(self):
        from research.strategy_preview import RETEST_BARS
        self.assertEqual(RETEST_BARS * 4, 72)


if __name__ == "__main__":
    unittest.main()
