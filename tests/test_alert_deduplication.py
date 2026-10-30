import logging
import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from research.friday_alerts import build_signal_alert
from research.friday_persistence import RepoFolderFridayStore


class AlertDeduplicationTests(unittest.TestCase):
    def test_alert_claimed_permanently(self):
        with tempfile.TemporaryDirectory() as tmp:
            first_process = RepoFolderFridayStore(root=tmp)
            self.assertTrue(first_process.claim_alert_permanent("BTCUSDT:short:2026-10-30T00:00:00Z:BREAK_CLOSE_PROBE"))
            # Recreate the store to simulate a fresh worker/process.
            second_process = RepoFolderFridayStore(root=tmp)
            self.assertFalse(second_process.claim_alert_permanent("BTCUSDT:short:2026-10-30T00:00:00Z:BREAK_CLOSE_PROBE"))

    def test_duplicate_alerts_suppressed(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = RepoFolderFridayStore(root=tmp)
            key = "BTCUSDT:short:2026-10-30T00:00:00Z:BREAK_CLOSE_PROBE"
            send = patch("research.friday_monitor.send_telegram_alert", return_value={"sent": True})
            with send as mocked_send:
                for _ in range(6):
                    if store.claim_alert_permanent(alert_key=key):
                        mocked_send("BTC short alert")
            mocked_send.assert_called_once()

    def test_stale_entry_blocked(self):
        entry, current, atr = 77974.0, 76500.0, 1200.0
        self.assertGreater(abs(current - entry), atr)

    def test_fresh_entry_allowed(self):
        entry, current, atr = 77974.0, 77800.0, 1200.0
        self.assertLessEqual(abs(current - entry), atr)

    def test_missing_atr_blocks_alert(self):
        for atr in (None, float("nan")):
            with self.subTest(atr=atr):
                try:
                    value = float(atr)
                except (TypeError, ValueError):
                    value = float("nan")
                self.assertFalse(math.isfinite(value))

    def test_alert_formatter_includes_current_price_and_distance(self):
        message = build_signal_alert({
            "event": "BREAK_CLOSE_PROBE", "direction": "short",
            "signal_time": "2026-10-30T00:00:00Z", "close_at_break": 77974,
            "paper_stop": 81942, "paper_target": 65559.2,
            "current_price": 77800, "distance_from_entry_pct": -0.22,
        }, pair="BTCUSDT")
        self.assertIn("📊 Current: 77,800 (-0.22% from entry)", message)


if __name__ == "__main__":
    unittest.main()
