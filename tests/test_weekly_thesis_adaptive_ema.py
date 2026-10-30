import unittest
import numpy as np
import pandas as pd

from research.weekly_thesis import evaluate_sheldon_thesis


def make_candles(count, days):
    times = pd.date_range("2020-01-05", periods=count, freq=f"{days}D", tz="UTC")
    x = np.arange(count, dtype=float)
    close = 100 + 0.15 * x + 4 * np.sin(x / 3)
    return pd.DataFrame({"timestamp": times, "open": close, "high": close + 1,
                         "low": close - 1, "close": close, "volume": 1000.0})


class AdaptiveWeeklyEmaTests(unittest.TestCase):
    def evaluate(self, weeks):
        return evaluate_sheldon_thesis(
            "TESTUSDT", weekly_candles=make_candles(weeks, 7),
            daily_candles=make_candles(90, 1),
            as_of=pd.Timestamp("2025-01-01T00:00:00Z"),
        )

    def test_180_weeks_uses_150_week_ema(self):
        self.assertEqual(self.evaluate(180)["weekly_ema_period"], 150)

    def test_120_weeks_uses_100_week_ema(self):
        self.assertEqual(self.evaluate(120)["weekly_ema_period"], 100)

    def test_60_weeks_uses_50_week_ema(self):
        self.assertEqual(self.evaluate(60)["weekly_ema_period"], 50)

    def test_30_weeks_is_insufficient(self):
        result = self.evaluate(30)
        self.assertEqual(result["status"], "insufficient_data")
        self.assertIsNone(result["weekly_ema_period"])


if __name__ == "__main__":
    unittest.main()
