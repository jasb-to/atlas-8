import unittest
from research.friday_alerts import build_signal_alert, send_telegram_alert


class FridayAlertTests(unittest.TestCase):
    def test_entry1_alert_contains_compact_template_and_research_guard(self):
        message = build_signal_alert({
            "event": "BREAK_CLOSE_PROBE", "direction": "long",
            "signal_time": "2026-10-21T12:00:00+00:00", "close_at_break": 100.0,
            "line_price_at_break": 99.0, "line_kind": "resistance", "touch_count": 3,
            "daily_bias": "bullish", "ema_aligned": True,
            "momentum_context": {"stoch_bullish_cross": True},
            "paper_stop": 98.0, "paper_target": 106.0, "paper_reward_risk": 3.0,
        }, pair="VIRTUALUSDT")
        self.assertIn("🚨 FRIDAY | ENTRY 1 CONFIRMED", message)
        self.assertIn("🪙 Token: VIRTUALUSDT", message)
        self.assertIn("🎯 Entry: 100", message)
        self.assertIn("🛑 Stop Loss: 98", message)
        self.assertIn("💰 TP1: 103 (1.5R)", message)
        self.assertIn("💰 TP2: 106 (3.0R)", message)
        self.assertIn("Weekly filter not applied", message)
        self.assertIn("(Mode: Research / Paper Only)", message)

    def test_entry2_alert_only_after_continuation_event(self):
        message = build_signal_alert({
            "event": "BREAK_CLOSE_PROBE", "event_add": "CONTINUATION_ADD_CONFIRMED",
            "direction": "short", "signal_time": "2026-10-21T12:00:00+00:00",
            "continuation_add_time": "2026-10-21T16:00:00+00:00",
            "continuation_add_close": 8.822, "continuation_trigger": 8.828,
            "line_price_at_break": 9.0019, "paper_stop": 9.0467,
            "paper_target": 7.833, "paper_reward_risk": 4.4,
        }, pair="AVAXUSDT", weekly_thesis={"status": "active", "direction": "short"},
           daily_context={"bias": "bearish"})
        self.assertIn("🚨 FRIDAY | ENTRY 2 CONFIRMED", message)
        self.assertIn("🎯 Entry: 8.822", message)
        self.assertIn("🛑 Stop Loss: 9.0467", message)
        self.assertIn("💰 TP2: 7.833 (4.4R)", message)
        self.assertIn("Weekly bearish. Daily bearish.", message)
        self.assertIn("retest held; continuation confirmed", message)

    def test_invalid_event_rejected(self):
        with self.assertRaises(ValueError):
            build_signal_alert({"event": "NOT_AN_ENTRY", "direction": "short"}, pair="BTCUSDT")

    def test_missing_telegram_credentials_is_safe_noop(self):
        result = send_telegram_alert("test", environ={})
        self.assertFalse(result["sent"])
        self.assertEqual(result["status"], "not_configured")


if __name__ == "__main__":
    unittest.main()
