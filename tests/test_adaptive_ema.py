import unittest

import numpy as np
import pandas as pd

from research.weekly_thesis import evaluate_sheldon_thesis


class AdaptiveWeeklyEmaTests(unittest.TestCase):
    AS_OF = pd.Timestamp("2026-10-25T12:00:00Z")

    def _evaluate(self, weeks: int):
        weekly_times = pd.date_range(end="2026-10-18", periods=weeks, freq="7D", tz="UTC")
        daily_times = pd.date_range(end="2026-10-24", periods=100, freq="D", tz="UTC")

        def frame(times):
            x = np.arange(len(times), dtype=float)
            return pd.DataFrame({
                "timestamp": times,
                "close": 100.0 + 0.1 * x + 3.0 * np.sin(x / 3.0),
            })

        return evaluate_sheldon_thesis(
            "TESTUSDT",
            weekly_candles=frame(weekly_times),
            daily_candles=frame(daily_times),
            as_of=self.AS_OF,
        )

    def test_180_weeks_uses_150_ema(self):
        result = self._evaluate(180)
        self.assertEqual(result["weekly_ema_period"], 150)
        self.assertNotEqual(result["status"], "insufficient_data", result["reason"])

    def test_120_weeks_uses_100_ema(self):
        result = self._evaluate(120)
        self.assertEqual(result["weekly_ema_period"], 100)
        self.assertNotEqual(result["status"], "insufficient_data", result["reason"])

    def test_60_weeks_uses_50_ema(self):
        result = self._evaluate(60)
        self.assertEqual(result["weekly_ema_period"], 50)
        self.assertNotEqual(result["status"], "insufficient_data", result["reason"])

    def test_30_weeks_returns_insufficient(self):
        result = self._evaluate(30)
        self.assertEqual(result["status"], "insufficient_data")


if __name__ == "__main__":
    unittest.main()
