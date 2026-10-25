import unittest
import numpy as np
import pandas as pd

from research.weekly_thesis import calculate_stoch_rsi, evaluate_sheldon_thesis


SCHEMA = {
    "pair", "status", "direction", "weekly_close", "weekly_sma",
    "weekly_stoch_k", "weekly_stoch_d", "daily_close", "daily_sma",
    "daily_stoch_k", "daily_stoch_d", "weekly_stoch_cross",
    "weekly_stoch_aligned", "reason", "generated_at_utc",
}


class WeeklyThesisTests(unittest.TestCase):
    def test_stoch_rsi_returns_aligned_series(self):
        close = pd.Series(100 + np.arange(100) * 0.2 + 2 * np.sin(np.arange(100) / 3))
        k, d = calculate_stoch_rsi(close)
        self.assertEqual(len(k), len(close))
        self.assertEqual(len(d), len(close))
        self.assertTrue(np.isfinite(k.iloc[-1]))
        self.assertTrue(np.isfinite(d.iloc[-1]))

    def test_insufficient_history_fails_closed_with_flat_schema(self):
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
        self.assertEqual(result["status"], "insufficient_data")
        self.assertIsNone(result["direction"])
        self.assertEqual(set(result), SCHEMA)
        self.assertIn("weekly=100, daily=100", result["reason"])

    def test_open_candles_are_not_used_and_schema_is_flat(self):
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
        result_with_open = evaluate_sheldon_thesis(
            "XBTUSDT", weekly_candles=weekly, daily_candles=daily, as_of=as_of
        )
        result_without_open = evaluate_sheldon_thesis(
            "XBTUSDT", weekly_candles=weekly.iloc[:-1], daily_candles=daily.iloc[:-1], as_of=as_of
        )
        self.assertEqual(set(result_with_open), SCHEMA)
        for key in SCHEMA - {"generated_at_utc", "reason"}:
            self.assertEqual(result_with_open[key], result_without_open[key], key)


if __name__ == "__main__":
    unittest.main()
