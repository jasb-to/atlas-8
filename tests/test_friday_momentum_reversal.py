import unittest

import pandas as pd

from research.friday_momentum_reversal import scan_momentum_reversal
from research.friday_monitor import _entry_filters_pass


class FridayMomentumReversalTests(unittest.TestCase):
    def test_directional_momentum_passes_for_long(self):
        ok, blockers = _entry_filters_pass(
            {"direction": "long", "momentum_context": {"macd_bullish_expansion": True}},
            {"long_reversal_score": 1, "short_reversal_score": 0},
        )
        self.assertTrue(ok)
        self.assertEqual(blockers, [])

    def test_directional_momentum_passes_for_short(self):
        ok, blockers = _entry_filters_pass(
            {"direction": "short", "momentum_context": {"stoch_bearish_cross": True}},
            {"long_reversal_score": 0, "short_reversal_score": 1},
        )
        self.assertTrue(ok)
        self.assertEqual(blockers, [])

    def test_opposing_exhaustion_blocks_entry(self):
        ok, blockers = _entry_filters_pass(
            {"direction": "long", "momentum_context": {"macd_bullish_expansion": True}},
            {"long_reversal_score": 1, "short_reversal_score": 3},
        )
        self.assertFalse(ok)
        self.assertIn("opposing_exhaustion_filter_blocked_entry", blockers)

    def test_long_is_blocked_by_bearish_daily_bias(self):
        ok, blockers = _entry_filters_pass(
            {"direction": "long", "daily_bias": "bearish",
             "momentum_context": {"macd_bullish_expansion": True}},
            {"long_reversal_score": 1, "short_reversal_score": 0},
        )
        self.assertFalse(ok)
        self.assertIn("direction_conflicts_with_daily_bias", blockers)

    def test_short_is_blocked_by_bullish_daily_bias(self):
        ok, blockers = _entry_filters_pass(
            {"direction": "short", "daily_bias": "bullish",
             "momentum_context": {"stoch_bearish_cross": True}},
            {"long_reversal_score": 0, "short_reversal_score": 1},
        )
        self.assertFalse(ok)
        self.assertIn("direction_conflicts_with_daily_bias", blockers)

    def test_missing_direction_is_blocked(self):
        ok, blockers = _entry_filters_pass({"momentum_context": {}}, {})
        self.assertFalse(ok)
        self.assertIn("direction_missing", blockers)

    def test_missing_ohlc_is_blocked_with_reason(self):
        result = scan_momentum_reversal(pd.DataFrame())
        self.assertEqual(result["state"], "BLOCKED")
        self.assertFalse(result["eligible"])
        self.assertIn("missing_ohlc_data", result["blockers"])

    def test_flat_market_is_blocked_when_stoch_rsi_is_undefined(self):
        count = 120
        frame = pd.DataFrame({
            "timestamp": pd.date_range("2026-01-01", periods=count, freq="4h", tz="UTC"),
            "open": [100.0] * count,
            "high": [101.0] * count,
            "low": [99.0] * count,
            "close": [100.0] * count,
            "volume": [1000.0] * count,
        })
        result = scan_momentum_reversal(frame, pair="HYPEUSD")
        self.assertEqual(result["state"], "BLOCKED")
        self.assertFalse(result["eligible"])
        self.assertIn("insufficient_indicator_history", result["blockers"])


if __name__ == "__main__":
    unittest.main()
