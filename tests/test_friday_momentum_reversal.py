import unittest

import pandas as pd

from research.friday_momentum_reversal import scan_momentum_reversal


class FridayMomentumReversalTests(unittest.TestCase):
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
