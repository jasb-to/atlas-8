import unittest
import pandas as pd
from research.fib_swings import confirmed_swings, latest_impulse_leg, retracement_levels


class FibSwingTests(unittest.TestCase):
    def candles(self):
        highs = [10, 11, 12, 14, 13, 12, 11, 12, 15, 14, 13, 12]
        lows  = [8, 9, 10, 11, 10, 9, 8, 9, 11, 10, 9, 8]
        return pd.DataFrame({
            "timestamp": pd.date_range("2026-01-01", periods=len(highs), freq="4h", tz="UTC"),
            "open": lows, "high": highs, "low": lows, "close": [(h+l)/2 for h,l in zip(highs,lows)]
        })

    def test_pivot_is_not_available_until_right_bars_close(self):
        f = self.candles()
        all_swings = confirmed_swings(f, left_bars=2, right_bars=2)
        self.assertTrue(all(s["confirmation_index"] > s["index"] for s in all_swings))

    def test_impulse_uses_confirmed_alternating_swing_pair(self):
        leg = latest_impulse_leg(self.candles(), left_bars=2, right_bars=2)
        self.assertIsNotNone(leg)
        self.assertIn(leg["direction"], {"long", "short"})
        levels = retracement_levels(leg)
        self.assertEqual([x["fib"] for x in levels], [0.382, 0.5, 0.618])
        self.assertTrue(all(x["price"] > 0 for x in levels))


if __name__ == "__main__":
    unittest.main()
