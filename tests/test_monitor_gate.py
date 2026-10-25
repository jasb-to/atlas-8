"""Regression tests for FRIDAY's weekly-thesis and paper-alert gates."""
import tempfile
import unittest
from unittest.mock import Mock, patch

from research.friday_alerts import send_telegram_alert
from research.friday_monitor import (
    _entry_filters_pass,
    _scan_4h_if_active,
    _trade_plan_metrics,
)


class MonitorGateTests(unittest.TestCase):
    def test_4h_scanner_runs_only_for_active_thesis(self):
        scanner = Mock(return_value={"scanned": True})
        thesis_evaluator = Mock(side_effect=[
            {"status": "inactive", "direction": None},
            {"status": "active", "direction": "long"},
            {"status": "active", "direction": "short"},
        ])
        for _ in range(3):
            thesis = thesis_evaluator("AVAXUSDT")
            result = _scan_4h_if_active(
                thesis, scanner, pair="AVAXUSDT", interval=240, output_dir=tempfile.gettempdir()
            )
            if thesis["status"] == "active":
                self.assertEqual(result, {"scanned": True})
            else:
                self.assertIsNone(result)
        self.assertEqual(thesis_evaluator.call_count, 3)
        self.assertEqual(scanner.call_count, 2)

    def test_conflicting_4h_direction_blocks_alert(self):
        event = {"direction": "long"}
        passed, blockers = _entry_filters_pass(event, {}, weekly_direction="short")
        self.assertFalse(passed)
        self.assertIn("direction_conflicts_with_weekly_thesis", blockers)

    def test_matching_direction_and_rr_at_least_1_5_is_alert_eligible(self):
        event = {"direction": "short"}
        passed, blockers = _entry_filters_pass(event, {}, weekly_direction="short")
        valid_plan, rr = _trade_plan_metrics("short", entry=8.822, stop=9.046, target=8.400)
        send_alert = Mock()
        if passed and valid_plan:
            send_alert("paper alert")
        self.assertTrue(passed, blockers)
        self.assertTrue(valid_plan)
        self.assertGreaterEqual(rr, 1.5)
        send_alert.assert_called_once_with("paper alert")

    @patch("research.friday_alerts.requests.post")
    def test_telegram_kill_switch_blocks_delivery_even_with_credentials(self, post):
        result = send_telegram_alert(
            "test message",
            environ={
                "FRIDAY_TELEGRAM_ENABLED": "false",
                "TELEGRAM_BOT_TOKEN": "test-token",
                "TELEGRAM_CHAT_ID": "test-chat",
            },
        )
        self.assertFalse(result["sent"])
        self.assertEqual(result["status"], "disabled")
        post.assert_not_called()

    def test_invalid_or_low_rr_plan_does_not_fire_alert(self):
        valid_plan, rr = _trade_plan_metrics("short", entry=8.822, stop=9.046, target=8.700)
        send_alert = Mock()
        if valid_plan:
            send_alert("paper alert")
        self.assertFalse(valid_plan)
        self.assertLess(rr, 1.5)
        send_alert.assert_not_called()


if __name__ == "__main__":
    unittest.main()
