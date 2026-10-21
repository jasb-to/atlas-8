"""Tests for causal Entry 1 liquidity and support/resistance confluence."""
import unittest

import pandas as pd

from research.entry_confluence import evaluate_entry1_confluence, find_reaction_levels


class EntryConfluenceTests(unittest.TestCase):
    def setUp(self):
        self.prior = pd.DataFrame({
            "high": [105.0, 104.8, 103.0, 102.0, 104.9, 101.5],
            "low":  [100.0, 100.1, 99.0, 98.0, 100.05, 97.5],
            "close":[102.0, 102.5, 100.0, 99.0, 102.2, 98.5],
        })

    def test_long_entry_requires_fib_plus_structure_or_sweep_and_rejection(self):
        # Current candle sweeps prior lows, then closes back above the Fib origin.
        current = {"high": 101.0, "low": 96.8, "close": 100.5}
        result = evaluate_entry1_confluence("long", 100.0, current, self.prior)
        self.assertTrue(result["fib_area_touched"])
        self.assertTrue(result["liquidity_sweep_and_reclaim"])
        self.assertTrue(result["rejection_or_reclaim_candle"])
        self.assertTrue(result["entry1_candidate"])

    def test_no_sweep_and_no_nearby_sr_means_wait(self):
        current = {"high": 110.0, "low": 108.0, "close": 109.0}
        result = evaluate_entry1_confluence("long", 100.0, current, self.prior)
        self.assertFalse(result["entry1_candidate"])
        self.assertEqual(result["status"], "WAIT_FOR_CONFLUENCE")

    def test_short_sweep_is_mirrored(self):
        prior = pd.DataFrame({
            "high": [100.0, 100.1, 101.0, 100.05, 99.5],
            "low": [95.0, 96.0, 97.0, 95.5, 96.5],
            "close": [98.0, 99.0, 98.5, 97.0, 98.0],
        })
        current = {"high": 102.0, "low": 98.0, "close": 98.5}
        result = evaluate_entry1_confluence("short", 100.0, current, prior)
        self.assertTrue(result["liquidity_sweep_and_reclaim"])
        self.assertTrue(result["rejection_or_reclaim_candle"])
        self.assertTrue(result["entry1_candidate"])

    def test_reaction_levels_require_multiple_distinct_candles(self):
        levels = find_reaction_levels(self.prior, min_touches=2)
        self.assertTrue(all(level["touches"] >= 2 for level in levels))

    def test_invalid_inputs_rejected(self):
        with self.assertRaises(ValueError):
            evaluate_entry1_confluence("flat", 100, {"high": 101, "low": 99, "close": 100}, self.prior)
        with self.assertRaises(ValueError):
            evaluate_entry1_confluence("long", 100, {"high": 99, "low": 101, "close": 100}, self.prior)


if __name__ == "__main__":
    unittest.main()
