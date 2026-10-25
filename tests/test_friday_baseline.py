"""Tests for the alert-free FRIDAY baseline replay helpers."""
import unittest

import pandas as pd

from scripts.friday_break_baseline import _forward_returns, _replay_reversal_candidate


class FridayBaselineTests(unittest.TestCase):
    def frame(self):
        return pd.DataFrame({
            "timestamp": pd.date_range("2026-04-25", periods=24, freq="4h", tz="UTC"),
            "open": [100.0 + i for i in range(24)],
            "high": [102.0 + i for i in range(24)],
            "low": [99.0 + i for i in range(24)],
            "close": [101.0 + i for i in range(24)],
        })

    def test_forward_returns_are_calculated_even_for_terminal_events(self):
        frame = self.frame()
        returns = _forward_returns(frame, frame.iloc[2]["timestamp"].isoformat(), "long")
        self.assertAlmostEqual(returns["forward_3_bar_return_pct"], (104 / 103 - 1) * 100, places=5)
        self.assertIsNotNone(returns["forward_18_bar_return_pct"])

    def test_forward_returns_are_null_when_horizon_is_not_available(self):
        frame = self.frame()
        returns = _forward_returns(frame, frame.iloc[-2]["timestamp"].isoformat(), "short")
        self.assertIsNone(returns["forward_3_bar_return_pct"])

    def test_reversal_replay_enters_next_open_and_resolves_stop_first(self):
        frame = self.frame()
        frame.loc[3, ["high", "low"]] = [110.0, 90.0]
        candidate = {
            "pair": "TESTUSDT",
            "signal_time": frame.iloc[2]["timestamp"].isoformat(),
            "direction": "long",
            "stop": 100.0,
            "target": 110.0,
        }
        trade = _replay_reversal_candidate(candidate, frame, max_hold_bars=3)
        self.assertEqual(trade["entry_time"], frame.iloc[3]["timestamp"].isoformat())
        self.assertEqual(trade["outcome"], "stop")
        self.assertLess(trade["r_multiple"], 0)

    def test_reversal_replay_blocks_next_open_beyond_structural_stop(self):
        frame = self.frame()
        candidate = {
            "pair": "TESTUSDT",
            "signal_time": frame.iloc[2]["timestamp"].isoformat(),
            "direction": "long",
            "stop": 104.5,
            "target": 110.0,
        }
        trade = _replay_reversal_candidate(candidate, frame, max_hold_bars=3)
        self.assertFalse(trade["eligible"])
        self.assertEqual(trade["outcome"], "invalid_next_open_vs_structural_stop")


if __name__ == "__main__":
    unittest.main()
