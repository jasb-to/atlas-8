"""Tests for Friday's Fibonacci entry planning."""
import unittest

from research.fib_entries import fib_entry_plan


class FibEntryPlanTests(unittest.TestCase):
    def test_long_entry_one_is_zero_fib_and_entry_two_is_impulse_retrace(self):
        plan = fib_entry_plan("long", 100.0, 120.0)
        self.assertEqual(plan["entry_1"]["fib"], 0.0)
        self.assertEqual(plan["entry_1"]["price"], 100.0)
        levels = {item["fib"]: item["price"] for item in plan["entry_2_candidates"]}
        self.assertAlmostEqual(levels[0.5], 110.0)
        self.assertAlmostEqual(levels[0.382], 112.36)
        self.assertAlmostEqual(levels[0.618], 107.64)
        self.assertEqual(plan["status"], "retest_levels_ready")

    def test_short_entry_two_retraces_up_from_impulse_low(self):
        plan = fib_entry_plan("short", 120.0, 100.0)
        levels = {item["fib"]: item["price"] for item in plan["entry_2_candidates"]}
        self.assertAlmostEqual(levels[0.5], 110.0)
        self.assertAlmostEqual(levels[0.382], 107.64)
        self.assertAlmostEqual(levels[0.618], 112.36)

    def test_waits_for_impulse_before_calculating_retest_levels(self):
        plan = fib_entry_plan("long", 100.0)
        self.assertEqual(plan["status"], "awaiting_impulse")
        self.assertEqual(plan["entry_2_candidates"], [])

    def test_rejects_invalid_direction_and_non_directional_impulse(self):
        with self.assertRaises(ValueError):
            fib_entry_plan("sideways", 100.0, 120.0)
        with self.assertRaises(ValueError):
            fib_entry_plan("long", 100.0, 99.0)
        with self.assertRaises(ValueError):
            fib_entry_plan("short", 100.0, 101.0)


if __name__ == "__main__":
    unittest.main()
