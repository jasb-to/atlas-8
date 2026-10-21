import unittest
import pandas as pd
from research.fib_bounce_ranking import rank_fib_bounce_levels


class FibBounceRankingTests(unittest.TestCase):
    def test_ranks_support_confluence_and_does_not_fake_probability(self):
        rows = []
        for i in range(40):
            base = 100 + (i % 4) * 0.2
            rows.append({"high": base+1, "low": base-1, "close": base})
        rows[-1] = {"high": 102.0, "low": 99.8, "close": 100.5}
        prior = pd.DataFrame(rows)
        ranked = rank_fib_bounce_levels("long", [
            {"fib": 0.382, "price": 101.0},
            {"fib": 0.5, "price": 100.0},
            {"fib": 0.618, "price": 95.0},
        ], prior, atr=1.0, current_price=100.5)
        self.assertEqual([x["rank"] for x in ranked], [1, 2, 3])
        self.assertTrue(all(x["historical_probability"] is None for x in ranked))
        self.assertTrue(all(x["probability_status"] == "uncalibrated_requires_walk_forward_replay" for x in ranked))


if __name__ == "__main__":
    unittest.main()
