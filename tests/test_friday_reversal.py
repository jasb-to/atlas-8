import unittest
import numpy as np
import pandas as pd

from research.friday_reversal import reversal_context


def candles(n=40, slope=0.0):
    close = np.array([100 + slope * i for i in range(n)], dtype=float)
    return pd.DataFrame({
        "timestamp": pd.date_range("2026-01-01", periods=n, freq="4h", tz="UTC"),
        "open": close - 0.1,
        "high": close + 0.4,
        "low": close - 0.4,
        "close": close,
    })


class FridayReversalContextTests(unittest.TestCase):
    def test_requires_enough_history(self):
        result = reversal_context(candles(8))
        self.assertEqual(result["status"], "insufficient_history")
        self.assertEqual(result["long_reversal_score"], 0)

    def test_result_is_causal_context_not_probability(self):
        frame = candles(50, slope=-0.3)
        # Current candle rejects its low and closes above the previous candle high.
        frame.loc[49, ["open", "low", "high", "close"]] = [85.0, 83.0, 87.0, 86.8]
        result = reversal_context(frame)
        self.assertEqual(result["status"], "ok")
        self.assertIn("score_note", result)
        self.assertNotIn("probability", result)
        self.assertTrue(
            "lower_wick_rejection" in result["evidence"]["bullish"]
            or "closed_above_previous_candle_high" in result["evidence"]["bullish"]
        )

    def test_missing_columns_rejected(self):
        with self.assertRaises(ValueError):
            reversal_context(pd.DataFrame({"close": [1, 2, 3, 4, 5, 6, 7]}))


if __name__ == "__main__":
    unittest.main()
