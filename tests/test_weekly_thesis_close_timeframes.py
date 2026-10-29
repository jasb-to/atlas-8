"""Regression coverage for independent weekly and daily thesis closes."""
from __future__ import annotations

import unittest
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from research.weekly_thesis import evaluate_sheldon_thesis


class WeeklyDailyCloseRegressionTest(unittest.TestCase):
    def test_daily_close_comes_from_newer_completed_daily_candle(self) -> None:
        # The latest completed daily candle is newer than the latest completed
        # weekly candle and intentionally has a different close.
        as_of = pd.Timestamp(datetime(2026, 10, 29, 12, tzinfo=timezone.utc))
        weekly_times = pd.date_range(
            end=as_of - pd.Timedelta(days=14), periods=220, freq="7D"
        )
        daily_times = pd.date_range(
            end=as_of - pd.Timedelta(days=1), periods=70, freq="D"
        )
        weekly = pd.DataFrame({
            "timestamp": weekly_times,
            "close": np.linspace(1.0, 2.0, len(weekly_times)),
        })
        daily = pd.DataFrame({
            "timestamp": daily_times,
            "close": np.linspace(1.2, 2.4, len(daily_times)),
        })

        result = evaluate_sheldon_thesis(
            "ADAUSDT",
            weekly_candles=weekly,
            daily_candles=daily,
            as_of=as_of,
        )

        self.assertEqual(result["weekly_candle_time_utc"], weekly_times[-1].isoformat())
        self.assertEqual(result["daily_candle_time_utc"], daily_times[-1].isoformat())
        self.assertNotEqual(result["weekly_close"], result["daily_close"])
        self.assertEqual(result["weekly_close"], float(weekly["close"].iloc[-1]))
        self.assertEqual(result["daily_close"], float(daily["close"].iloc[-1]))


if __name__ == "__main__":
    unittest.main()
