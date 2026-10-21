"""Causal decision-layer regression tests for Friday."""
import unittest
import pandas as pd
from research.friday_decision import evaluate_friday_decision


class FridayDecisionTests(unittest.TestCase):
    def test_no_trade_when_price_is_away_from_prior_range_extremes(self):
        rows = []
        for i in range(40):
            base = 100 + (i % 5) * 0.1
            rows.append({"timestamp": pd.Timestamp("2026-01-01", tz="UTC") + pd.Timedelta(hours=4*i),
                         "open": base, "high": base + 1, "low": base - 1, "close": base + 0.2})
        result = evaluate_friday_decision(pd.DataFrame(rows))
        self.assertIn(result["state"], {"NO_TRADE", "WATCHING"})
        self.assertIn("reason", result)
        self.assertEqual(result["execution"], "no trade; no order submitted")

    def test_requires_valid_ohlc_columns(self):
        with self.assertRaises(ValueError):
            evaluate_friday_decision(pd.DataFrame({"close": [1, 2, 3, 4, 5]}))


if __name__ == "__main__":
    unittest.main()
