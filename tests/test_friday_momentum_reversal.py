import unittest
import os

import pandas as pd

from research.friday_momentum_reversal import scan_momentum_reversal
from research.friday_monitor import _entry_filters_pass


class FridayMomentumReversalTests(unittest.TestCase):
    def test_directional_momentum_passes_for_long(self):
        ok, blockers = _entry_filters_pass(
            {"direction": "long", "daily_bias": "bullish", "momentum_context": {"macd_bullish_expansion": True}},
            {"long_reversal_score": 1, "short_reversal_score": 0},
        )
        self.assertTrue(ok)
        self.assertEqual(blockers, [])

    def test_directional_momentum_passes_for_short(self):
        ok, blockers = _entry_filters_pass(
            {"direction": "short", "daily_bias": "bearish", "momentum_context": {"stoch_bearish_cross": True}},
            {"long_reversal_score": 0, "short_reversal_score": 1},
        )
        self.assertTrue(ok)
        self.assertEqual(blockers, [])

    def test_opposing_exhaustion_blocks_entry(self):
        ok, blockers = _entry_filters_pass(
            {"direction": "long", "daily_bias": "bullish", "momentum_context": {"macd_bullish_expansion": True}},
            {"long_reversal_score": 1, "short_reversal_score": 3},
        )
        self.assertFalse(ok)
        self.assertIn("opposing_exhaustion_filter_blocked_entry", blockers)

    def test_missing_daily_bias_is_blocked(self):
        ok, blockers = _entry_filters_pass(
            {"direction": "long", "momentum_context": {"macd_bullish_expansion": True}},
            {"long_reversal_score": 1, "short_reversal_score": 0},
        )
        self.assertFalse(ok)
        self.assertIn("daily_bias_unavailable", blockers)

    def test_long_is_blocked_by_bearish_daily_bias(self):
        ok, blockers = _entry_filters_pass(
            {"direction": "long", "daily_bias": "bearish",
             "momentum_context": {"macd_bullish_expansion": True}},
            {"long_reversal_score": 1, "short_reversal_score": 0},
        )
        self.assertFalse(ok)
        self.assertIn("direction_conflicts_with_daily_bias", blockers)

    def test_short_is_blocked_by_bullish_daily_bias(self):
        ok, blockers = _entry_filters_pass(
            {"direction": "short", "daily_bias": "bullish",
             "momentum_context": {"stoch_bearish_cross": True}},
            {"long_reversal_score": 0, "short_reversal_score": 1},
        )
        self.assertFalse(ok)
        self.assertIn("direction_conflicts_with_daily_bias", blockers)

    def test_missing_direction_is_blocked(self):
        ok, blockers = _entry_filters_pass({"momentum_context": {}}, {})
        self.assertFalse(ok)
        self.assertIn("direction_missing", blockers)

    def test_missing_ohlc_is_blocked_with_reason(self):
        result = scan_momentum_reversal(pd.DataFrame())
        self.assertEqual(result["state"], "BLOCKED")
        self.assertFalse(result["eligible"])
        self.assertIn("missing_ohlc_data", result["blockers"])

    def test_flat_market_is_blocked_when_stoch_rsi_is_undefined(self):
        count = 120
        frame = pd.DataFrame({
            "timestamp": pd.date_range("2026-01-01", periods=count, freq="4h", tz="UTC"),
            "open": [100.0] * count,
            "high": [101.0] * count,
            "low": [99.0] * count,
            "close": [100.0] * count,
            "volume": [1000.0] * count,
        })
        result = scan_momentum_reversal(frame, pair="HYPEUSD")
        self.assertEqual(result["state"], "BLOCKED")
        self.assertFalse(result["eligible"])
        self.assertIn("insufficient_indicator_history", result["blockers"])


    def test_disabled_momentum_reversal_is_completely_silent(self):
        """The kill switch must prevent reversal persistence and Telegram delivery."""
        from tempfile import TemporaryDirectory
        from contextlib import redirect_stdout
        from io import StringIO
        from unittest.mock import patch, MagicMock
        from research.friday_monitor import run_monitor

        candle_time = "2026-10-28T16:00:00+00:00"
        preview = {
            "last_completed_candle_utc": candle_time,
            "last_close": 101.05,
            "latest_setup_state": {"state": "NO_SETUP", "message": "no structural setup"},
            "friday_decision": {"state": "NO_TRADE", "reason": "no structural entry"},
            "event_count": 0,
            "break_events": [],
            "closed_candles": [],
            "model_review": {"status": "disabled", "response": None},
            "reversal_context": {"state": "neutral"},
            "current_indicators": {},
            "structure_lifecycle": {},
            "execution_trigger": {"eligible": False, "blockers": []},
            "trade_plans": {},
            "momentum_reversal": {
                "strategy": "stoch-rsi-structural-reversal-v1",
                "state": "CANDIDATE",
                "eligible": True,
                "direction": "short",
                "signal_time": candle_time,
                "entry": 101.05,
                "stop": 102.20,
                "target": 98.04,
                "reward_risk": 2.62,
            },
        }
        store = MagicMock()
        store.configured = True
        store.paper_trades = []
        output = StringIO()

        with TemporaryDirectory() as tmp, redirect_stdout(output), patch.dict(
            os.environ, {"FRIDAY_MOMENTUM_REVERSAL_ENABLED": "false", "FRIDAY_AI_REVIEW_ENABLED": "false"}, clear=False
        ):
            with patch("research.friday_monitor.create_friday_store", return_value=store):
                with patch("research.friday_monitor.evaluate_sheldon_thesis", return_value={
                    "status": "active", "direction": "short", "reason": "active test thesis",
                    "generated_at_utc": candle_time,
                }):
                    with patch("research.friday_monitor.build_preview", return_value=preview):
                        with patch("research.friday_monitor.analyse_pair", return_value={
                            "state": "WATCH_NEUTRAL", "thesis_direction": "short",
                            "generated_at_utc": candle_time,
                            "four_hour_execution": {"last_closed_candle_utc": candle_time},
                        }):
                            with patch("research.friday_monitor.update_paper_trades", return_value=[]):
                                with patch("research.friday_monitor.register_paper_trade") as register_trade:
                                    with patch("research.friday_monitor.send_telegram_alert") as send_alert:
                                        result = run_monitor(["SOLUSDT"], tmp)

        send_alert.assert_not_called()
        self.assertNotIn("MOMENTUM_REVERSAL", output.getvalue())
        register_trade.assert_not_called()
        store.record_trade_event.assert_not_called()
        store.claim_alert.assert_not_called()
        self.assertFalse(any(
            item.get("type") == "MOMENTUM_REVERSAL"
            for item in result["pair_results"][0]["alert_results"]
        ))
        snapshot_payloads = [
            call.args[0].get("payload", {})
            for call in store.record_archive.call_args_list
            if call.args and isinstance(call.args[0], dict)
        ]
        self.assertTrue(snapshot_payloads)
        self.assertTrue(all("momentum_reversal" not in payload for payload in snapshot_payloads))


if __name__ == "__main__":
    unittest.main()
