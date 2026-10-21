import unittest
from unittest.mock import patch
from tempfile import TemporaryDirectory
from pathlib import Path

from research.friday_monitor import _utc, run_monitor


class FridayMonitorTests(unittest.TestCase):
    def test_utc_normalizes_naive_timestamp(self):
        self.assertEqual(_utc("2026-10-21T12:00:00").isoformat(), "2026-10-21T12:00:00+00:00")

    def test_utc_normalizes_offset_timestamp(self):
        self.assertEqual(_utc("2026-10-21T13:00:00+01:00").isoformat(), "2026-10-21T12:00:00+00:00")

    def test_monitor_uses_preview_schema_and_writes_cron_artifact(self):
        preview = {
            "last_completed_candle_utc": "2026-10-21T12:00:00+00:00",
            "last_close": 100.0,
            "latest_setup_state": {"state": "NO_SETUP", "message": "none"},
            "friday_decision": {"state": "NO_TRADE"},
            "event_count": 0,
            "break_events": [],
        }
        with TemporaryDirectory() as tmp:
            with patch("research.friday_monitor.build_preview", return_value=preview):
                with patch("research.friday_monitor.SupabaseFridayStore") as store_cls:
                    store_cls.return_value.configured = False
                    result = run_monitor(["XBTUSD"], tmp)
            self.assertEqual(result["status"], "persistence_not_configured")
            self.assertEqual(result["pair_results"][0]["latest_setup_state"], "NO_SETUP")
            self.assertTrue(Path(tmp, "cron_run.json").exists())


if __name__ == "__main__":
    unittest.main()
