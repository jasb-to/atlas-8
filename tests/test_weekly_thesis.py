import unittest
import numpy as np
import pandas as pd

from research.weekly_thesis import calculate_stoch_rsi, evaluate_sheldon_thesis


class WeeklyThesisTests(unittest.TestCase):
    def test_stoch_rsi_returns_aligned_series(self):
        close = pd.Series(100 + np.arange(100) * 0.2 + 2 * np.sin(np.arange(100) / 3))
        k, d = calculate_stoch_rsi(close)
        self.assertEqual(len(k), len(close))
        self.assertEqual(len(d), len(close))
        self.assertTrue(np.isfinite(k.iloc[-1]))
        self.assertTrue(np.isfinite(d.iloc[-1]))

    def test_insufficient_history_fails_closed(self):
        as_of = pd.Timestamp("2026-10-25T12:00:00Z")
        daily = pd.DataFrame({
            "timestamp": pd.date_range(end="2026-10-24", periods=100, freq="D", tz="UTC"),
            "close": np.linspace(100, 150, 100),
        })
        weekly = pd.DataFrame({
            "timestamp": pd.date_range(end="2026-10-18", periods=100, freq="7D", tz="UTC"),
            "close": np.linspace(100, 150, 100),
        })
        result = evaluate_sheldon_thesis(
            "XBTUSDT", weekly_candles=weekly, daily_candles=daily, as_of=as_of
        )
        self.assertEqual(result["status"], "data_insufficient")
        self.assertEqual(result["direction"], "neutral")

    def test_open_candles_are_not_used(self):
        as_of = pd.Timestamp("2026-10-25T12:00:00Z")
        daily = pd.DataFrame({
            "timestamp": pd.date_range(end="2026-10-24", periods=220, freq="D", tz="UTC").tolist()
                + [pd.Timestamp("2026-10-25T00:00:00Z")],
            "close": np.linspace(100, 180, 221),
        })
        weekly = pd.DataFrame({
            "timestamp": pd.date_range(end="2026-10-18", periods=220, freq="7D", tz="UTC").tolist()
                + [pd.Timestamp("2026-10-25T00:00:00Z")],
            "close": np.linspace(100, 180, 221),
        })
        result = evaluate_sheldon_thesis(
            "XBTUSDT", weekly_candles=weekly, daily_candles=daily, as_of=as_of
        )
        # The last daily and weekly rows are unfinished at the supplied as_of.
        self.assertEqual(result["daily_last_closed_candle_utc"], "2026-10-24T00:00:00+00:00")
        self.assertEqual(result["weekly_last_closed_candle_utc"], "2026-10-18T00:00:00+00:00")


if __name__ == "__main__":
    unittest.main()
