"""Focused tests for the causal 4H break strategy preview."""
import unittest
from unittest.mock import patch

import pandas as pd

from research.strategy_preview import break_crossed, causal_replay, latest_setup_state, line_retest, momentum_context
from research.trendlines import Pivot, Trendline


class StrategyPreviewTests(unittest.TestCase):
    def line(self, kind="resistance"):
        return Trendline(
            kind=kind,
            start=Pivot(0, 100.0, kind, 1),
            end=Pivot(2, 100.0, kind, 3),
            slope_per_bar=0.0,
            tolerance=0.15,
            touch_indices=(0, 2, 4),
            violations=(),
        )

    def test_resistance_break_requires_closed_close_and_prior_close_below(self):
        frame = pd.DataFrame({
            "high": [101, 101, 102],
            "low": [98, 99, 100],
            "close": [99.5, 100.1, 100.3],
        })
        self.assertTrue(break_crossed(frame, self.line("resistance"), 2))
        self.assertFalse(break_crossed(frame, self.line("resistance"), 1))

    def test_support_break_is_directional_breakdown(self):
        frame = pd.DataFrame({
            "high": [101, 101, 100],
            "low": [99, 98, 97],
            "close": [100.5, 99.9, 99.7],
        })
        self.assertTrue(break_crossed(frame, self.line("support"), 2))

    def test_retest_is_only_checked_after_break(self):
        frame = pd.DataFrame({
            "high": [100.2, 100.1, 100.3, 101],
            "low": [99.8, 99.7, 100.0, 100.2],
            "close": [100, 99.9, 100.1, 100.4],
        })
        found, index, held = line_retest(frame, self.line("resistance"), 1, 3)
        self.assertTrue(found)
        self.assertEqual(index, 2)
        self.assertTrue(held)


    def test_momentum_context_reports_stoch_recross_and_macd(self):
        frame = pd.DataFrame({
            "close": [90, 91, 92, 93, 94, 95, 96, 97, 98, 99],
            "stoch_k": [10, 12, 15, 20, 25, 30, 35, 30, 30, 70],
            "stoch_d": [12, 13, 14, 18, 22, 27, 32, 35, 35, 60],
        })
        context = momentum_context(frame, len(frame)-1)
        self.assertTrue(context["stoch_bullish_cross"])
        self.assertIn("macd_bullish_expansion", context)

    def test_latest_setup_state_waits_for_retest_after_break_probe(self):
        now = pd.Timestamp("2026-10-21T12:00:00Z")
        events = [{
            "signal_time": "2026-10-21T04:00:00Z", "direction": "long",
            "line_kind": "resistance", "line_price_at_break": 1.25,
            "touch_count": 3, "daily_bias": "bearish", "ema_aligned": True,
            "outcome": "pending", "retest_deadline": "2026-10-24T04:00:00Z",
        }]
        state = latest_setup_state(events, now)
        self.assertEqual(state["state"], "ENTRY_PROBE_WAITING_RETEST")

    def test_latest_setup_state_marks_failed_break_invalidated(self):
        events = [{
            "signal_time": "2026-10-21T04:00:00Z", "direction": "short",
            "line_kind": "support", "line_price_at_break": 1.25,
            "touch_count": 3, "outcome": "failed_break",
        }]
        state = latest_setup_state(events, "2026-10-21T12:00:00Z")
        self.assertEqual(state["state"], "INVALIDATED")

    def test_retest_window_allows_multiple_days(self):
        from research.strategy_preview import RETEST_BARS
        self.assertEqual(RETEST_BARS * 4, 72)


    def test_live_preview_does_not_retain_stale_line_when_latest_has_none(self):
        n = 100
        frame = pd.DataFrame({
            "timestamp": pd.date_range("2026-10-01", periods=n, freq="4h", tz="UTC"),
            "open": [100.0 + i * 0.1 for i in range(n)],
            "high": [101.0 + i * 0.1 for i in range(n)],
            "low": [99.0 + i * 0.1 for i in range(n)],
            "close": [100.5 + i * 0.1 for i in range(n)],
            "volume": [1000.0] * n,
        })
        with (patch("research.strategy_preview.find_wick_pivots", return_value=[]),
              patch("research.strategy_preview.fit_touch_trendlines", return_value=[])):
            _, status, enriched = causal_replay(frame)
        self.assertEqual(status["state"], "no_qualifying_line")
        self.assertEqual(status["last_closed_candle"], enriched.iloc[-1].timestamp.isoformat())
        self.assertAlmostEqual(status["close"], float(enriched.iloc[-1].close), places=5)


if __name__ == "__main__":
    unittest.main()
