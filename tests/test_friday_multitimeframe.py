import unittest
import numpy as np
import pandas as pd

from research.friday_multitimeframe import analyse_pair, _snapshot


def candles(n=240, trend=1.0, phase=0.0):
    idx = pd.date_range("2025-01-01", periods=n, freq="4h", tz="UTC")
    close = 100 + np.arange(n) * trend + np.sin(np.arange(n) / 5) * 2 + phase
    return pd.DataFrame({
        "timestamp": idx, "open": close - 0.2, "high": close + 1.0,
        "low": close - 1.0, "close": close, "vwap": close,
        "volume": np.full(n, 100.0), "trades": np.full(n, 10),
    })


class FridayMultiTimeframeTests(unittest.TestCase):
    def test_snapshot_is_causal_and_has_indicator_and_structure_fields(self):
        frame = candles()
        result = _snapshot(frame, "4H")
        self.assertEqual(result["timeframe"], "4H")
        self.assertIn(result["state"], ("bullish", "bearish", "mixed"))
        self.assertIn("stoch_cross_up", result)
        self.assertIn("macd_hist_change", result)
        self.assertIn("nearby_trendlines", result)
        self.assertEqual(result["last_closed_candle_utc"], frame.iloc[-1].timestamp.isoformat())

    def test_pair_analysis_connects_daily_thesis_to_4h(self):
        daily = candles(240, trend=1.0)
        daily["timestamp"] = pd.date_range("2025-01-01", periods=240, freq="1D", tz="UTC")
        h4 = candles(240, trend=0.2)
        result = analyse_pair("XBTUSD", daily=daily, four_hour=h4)
        self.assertEqual(result["strategy"], "daily-thesis-4h-execution-v1")
        self.assertIn(result["state"], ("WATCH_NEUTRAL", "WATCH_MAJOR_MOVE", "WATCH_STRUCTURE", "ENTRY_1_CANDIDATE"))
        self.assertIn("daily_thesis", result)
        self.assertIn("four_hour_execution", result)
        self.assertIsInstance(result["entry1_alert_eligible"], bool)
        self.assertFalse(any("orders" in x.lower() for x in result["limitations"]))


if __name__ == "__main__":
    unittest.main()
