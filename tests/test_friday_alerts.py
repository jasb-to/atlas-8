import unittest
from research.friday_alerts import build_signal_alert, send_telegram_alert


class FridayAlertTests(unittest.TestCase):
    def test_entry1_alert_contains_decision_and_research_guard(self):
        message = build_signal_alert({
            "event": "BREAK_CLOSE_PROBE", "direction": "long",
            "signal_time": "2026-10-21T12:00:00+00:00", "close_at_break": 100.0,
            "line_price_at_break": 99.0, "line_kind": "resistance", "touch_count": 3,
            "daily_bias": "bullish", "ema_aligned": True,
            "momentum_context": {"stoch_bullish_cross": True},
        }, pair="VIRTUALUSD")
        self.assertIn("ENTRY 1", message)
        self.assertIn("VIRTUALUSD", message)
        self.assertIn("no trade submitted", message)

    def test_entry2_alert_only_after_continuation_event(self):
        message = build_signal_alert({
            "event": "BREAK_CLOSE_PROBE", "event_add": "CONTINUATION_ADD_CONFIRMED",
            "direction": "short", "signal_time": "2026-10-21T12:00:00+00:00",
            "continuation_add_time": "2026-10-21T16:00:00+00:00",
            "continuation_add_close": 90.0, "continuation_trigger": 91.0,
            "line_price_at_break": 95.0,
        }, pair="XBTUSD")
        self.assertIn("ENTRY 2", message)
        self.assertIn("retest reaction extreme", message)

    def test_missing_telegram_credentials_is_safe_noop(self):
        result = send_telegram_alert("test", environ={})
        self.assertFalse(result["sent"])
        self.assertEqual(result["status"], "not_configured")


if __name__ == "__main__":
    unittest.main()
