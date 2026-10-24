"""Tests for causal structural trade-plan gating."""
import unittest
from unittest.mock import patch

import pandas as pd

from research.friday_trade_plan import build_trade_plan
from research.trendlines import Pivot


class FridayTradePlanTests(unittest.TestCase):
    def frame(self, n=40):
        close = [100.0 + ((i % 8) - 4) * 0.8 for i in range(n)]
        return pd.DataFrame({
            "open": close,
            "high": [x + 1.2 for x in close],
            "low": [x - 1.2 for x in close],
            "close": close,
            "volume": [1000.0] * n,
        })

    def test_invalid_direction_is_rejected(self):
        with self.assertRaises(ValueError):
            build_trade_plan(self.frame(), "sideways")

    def test_insufficient_history_blocks_plan(self):
        result = build_trade_plan(self.frame(5), "long")
        self.assertFalse(result["eligible"])
        self.assertIn("insufficient_atr_history", result["blockers"])

    def test_confirmed_structure_and_rr_are_reported(self):
        data = self.frame()
        data.loc[data.index[-1], "close"] = 100.0
        pivots = [
            Pivot(10, 110.0, "resistance", 13),
            Pivot(15, 90.0, "support", 18),
            Pivot(20, 108.0, "resistance", 23),
            Pivot(24, 92.0, "support", 27),
        ]
        with patch("research.friday_trade_plan.find_wick_pivots", return_value=pivots):
            result = build_trade_plan(data, "long")
        self.assertEqual(result["confirmed_swing_high"]["price"], 108.0)
        self.assertEqual(result["confirmed_swing_low"]["price"], 92.0)
        self.assertIsNotNone(result["stop"])
        self.assertIsNotNone(result["target"])
        self.assertEqual(result["target_source"], "confirmed_structure")
        self.assertIsNotNone(result["reward_risk"])
        self.assertTrue(result["research_only"])
        self.assertIsNotNone(result["structural_stop"])
        self.assertLessEqual(result["stop_distance_pct"], 1.0)
        self.assertEqual(result["stop_policy"], "risk stop capped at 1.0% from entry; structural_stop retained separately for context")

    def test_uses_fib_extension_when_nearby_structure_fails_minimum_rr(self):
        data = self.frame()
        data.loc[data.index[-1], "close"] = 101.5
        pivots = [
            Pivot(10, 103.0, "resistance", 13),
            Pivot(15, 100.0, "support", 18),
            Pivot(20, 104.0, "resistance", 23),
        ]
        with patch("research.friday_trade_plan.find_wick_pivots", return_value=pivots):
            result = build_trade_plan(data, "long")
        self.assertEqual(result["target_source"], "fib_1.272_fallback")
        self.assertGreaterEqual(result["reward_risk"], 1.5)
        self.assertTrue(result["eligible"])

    def test_no_valid_stop_or_target_blocks_plan(self):
        data = self.frame()
        pivots = [Pivot(20, 110.0, "support", 23)]
        with patch("research.friday_trade_plan.find_wick_pivots", return_value=pivots):
            result = build_trade_plan(data, "long")
        self.assertFalse(result["eligible"])
        self.assertIn("no_valid_confirmed_structural_stop", result["blockers"])
        self.assertIn("no_forward_structural_or_fibonacci_target", result["blockers"])

    def test_future_unconfirmed_pivots_are_ignored(self):
        data = self.frame()
        pivots = [
            Pivot(20, 110.0, "resistance", len(data) + 2),
            Pivot(22, 90.0, "support", len(data) + 2),
        ]
        with patch("research.friday_trade_plan.find_wick_pivots", return_value=pivots):
            result = build_trade_plan(data, "long")
        self.assertIsNone(result["confirmed_swing_high"])
        self.assertIsNone(result["confirmed_swing_low"])


if __name__ == "__main__":
    unittest.main()
