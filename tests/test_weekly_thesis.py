import unittest
import numpy as np
import pandas as pd
from unittest.mock import patch

from research.weekly_thesis import calculate_stoch_rsi, evaluate_sheldon_thesis


SCHEMA = {
    "pair", "status", "direction", "macro_regime", "wave_state",
    "momentum_bearish", "momentum_bullish", "weekly_close", "weekly_ema8",
    "weekly_ema21", "weekly_ema200", "weekly_stoch_k", "weekly_stoch_d",
    "weekly_macd_histogram", "daily_close", "daily_ema8", "daily_ema21",
    "daily_ema50", "daily_stoch_k", "daily_stoch_d", "daily_alignment",
    "weekly_stoch_cross", "weekly_stoch_aligned", "reason", "generated_at_utc",
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


    def test_history_boundary_requires_200_weekly_and_50_daily_candles(self):
        as_of = pd.Timestamp("2026-10-25T12:00:00Z")
        daily_times = pd.date_range(end="2026-10-24", periods=60, freq="D", tz="UTC")
        weekly_times = pd.date_range(end="2026-10-18", periods=200, freq="7D", tz="UTC")
        def frame(times):
            x = np.arange(len(times), dtype=float)
            return pd.DataFrame({"timestamp": times, "close": 100 + 0.1 * x + 3 * np.sin(x / 3)})
        daily, weekly = frame(daily_times), frame(weekly_times)
        boundary = evaluate_sheldon_thesis(
            "XBTUSDT", weekly_candles=weekly.iloc[:-1], daily_candles=daily, as_of=as_of
        )
        self.assertEqual(boundary["status"], "insufficient_data")
        self.assertIn("weekly=199", boundary["reason"])
        enough = evaluate_sheldon_thesis(
            "XBTUSDT", weekly_candles=weekly, daily_candles=daily, as_of=as_of
        )
        self.assertNotEqual(enough["status"], "insufficient_data", enough["reason"])


    def test_partial_weekly_history_displays_indicators_but_never_activates(self):
        as_of = pd.Timestamp("2026-10-27T12:00:00Z")
        weekly_times = pd.date_range(end="2026-10-18", periods=79, freq="7D", tz="UTC")
        daily_times = pd.date_range(end="2026-10-26", periods=552, freq="D", tz="UTC")
        def frame(times):
            x = np.arange(len(times), dtype=float)
            return pd.DataFrame({
                "timestamp": times,
                "close": 100 + 0.12 * x + 2 * np.sin(x / 4),
            })
        result = evaluate_sheldon_thesis(
            "BNBUSDT", weekly_candles=frame(weekly_times),
            daily_candles=frame(daily_times), as_of=as_of,
        )
        self.assertEqual(result["status"], "insufficient_data")
        self.assertIsNone(result["direction"])
        self.assertEqual(result["macro_regime"], "insufficient_history")
        self.assertNotIn(result["wave_state"], (None, ""))
        self.assertIn(result["daily_alignment"], {"bullish_bias", "bearish_bias", "at_ema50"})
        self.assertIsNotNone(result["weekly_close"])
        self.assertIsNotNone(result["daily_close"])
        self.assertIsNotNone(result["weekly_ema8"])
        self.assertIsNotNone(result["weekly_ema21"])
        self.assertIsNotNone(result["daily_ema50"])
        self.assertIn("available weekly=79, daily=552", result["reason"])
        self.assertIn("trading remains blocked", result["reason"].lower())


    def test_weekly_alignment_does_not_require_stoch_extreme_zone(self):
        as_of = pd.Timestamp("2026-10-25T12:00:00Z")
        daily_times = pd.date_range(end="2026-10-24", periods=230, freq="D", tz="UTC")
        weekly_times = pd.date_range(end="2026-10-18", periods=230, freq="7D", tz="UTC")
        def frame(times):
            x = np.arange(len(times), dtype=float)
            return pd.DataFrame({"timestamp": times, "close": 100 + x})
        def aligned_stoch(close, **kwargs):
            return (
                pd.Series(np.full(len(close), 60.0), index=close.index),
                pd.Series(np.full(len(close), 50.0), index=close.index),
            )
        with patch("research.weekly_thesis.calculate_stoch_rsi", side_effect=aligned_stoch):
            result = evaluate_sheldon_thesis(
                "AVAXUSDT", weekly_candles=frame(weekly_times),
                daily_candles=frame(daily_times), as_of=as_of,
            )
        self.assertEqual(result["status"], "active")
        self.assertEqual(result["direction"], "long")
        self.assertEqual(result["weekly_stoch_k"], 60.0)
        self.assertEqual(result["weekly_stoch_d"], 50.0)


    def test_bullish_macro_bearish_stoch_creates_active_short_pullback(self):
        weekly_values = np.linspace(100.0, 300.0, 260)
        weekly_values[-6:] = weekly_values[-7] * np.array([0.995, 0.985, 0.975, 0.965, 0.955, 0.945])
        daily_values = np.linspace(100.0, 180.0, 100)
        daily_values[-16:] = daily_values[-17] * np.array(
            [0.995, 0.990, 0.985, 0.980, 0.975, 0.970, 0.965, 0.960,
             0.955, 0.950, 0.945, 0.940, 0.935, 0.930, 0.925, 0.910]
        )
        weekly = pd.DataFrame({
            "timestamp": pd.date_range(start="2021-01-04", periods=260, freq="7D", tz="UTC"),
            "close": weekly_values,
        })
        daily = pd.DataFrame({
            "timestamp": pd.date_range(start="2026-01-01", periods=100, freq="D", tz="UTC"),
            "close": daily_values,
        })
        result = evaluate_sheldon_thesis(
            "BTCUSDT", weekly_candles=weekly, daily_candles=daily,
            as_of=pd.Timestamp("2027-01-01T00:00:00Z"),
        )
        self.assertEqual(result["macro_regime"], "bullish")
        self.assertEqual(result["wave_state"], "pullback")
        self.assertEqual(result["direction"], "short")
        self.assertEqual(result["status"], "active")
        self.assertEqual(result["daily_alignment"], "pass")

    def test_daily_ema50_conflict_vetoes_weekly_wave(self):
        weekly_values = np.linspace(100.0, 300.0, 260)
        weekly_values[-6:] = weekly_values[-7] * np.array([0.995, 0.985, 0.975, 0.965, 0.955, 0.945])
        daily_values = np.linspace(100.0, 200.0, 100)
        daily_values[-5:] = daily_values[-6] * np.array([1.01, 1.015, 1.02, 1.025, 1.03])
        weekly = pd.DataFrame({
            "timestamp": pd.date_range(start="2021-01-04", periods=260, freq="7D", tz="UTC"),
            "close": weekly_values,
        })
        daily = pd.DataFrame({
            "timestamp": pd.date_range(start="2026-01-01", periods=100, freq="D", tz="UTC"),
            "close": daily_values,
        })
        result = evaluate_sheldon_thesis(
            "BTCUSDT", weekly_candles=weekly, daily_candles=daily,
            as_of=pd.Timestamp("2027-01-01T00:00:00Z"),
        )
        self.assertEqual(result["direction"], "short")
        self.assertEqual(result["status"], "veto")
        self.assertEqual(result["reason"], "Daily conflicts with Weekly Wave")



if __name__ == "__main__":
    unittest.main()
