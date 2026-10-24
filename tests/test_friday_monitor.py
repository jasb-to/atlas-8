import unittest
from unittest.mock import patch
from tempfile import TemporaryDirectory
from pathlib import Path

from research.friday_monitor import _display_pair, _utc, run_monitor, validate_friday_pairs


class FridayMonitorTests(unittest.TestCase):
    def test_user_facing_btc_symbol_is_normalized(self):
        self.assertEqual(_display_pair("XBTUSDT"), "BTCUSDT")
        self.assertEqual(_display_pair("XBTUSD"), "BTCUSDT")
        self.assertEqual(_display_pair("ETHUSDT"), "ETHUSDT")

    def test_utc_normalizes_naive_timestamp(self):
        self.assertEqual(_utc("2026-10-21T12:00:00").isoformat(), "2026-10-21T12:00:00+00:00")

    def test_utc_normalizes_offset_timestamp(self):
        self.assertEqual(_utc("2026-10-21T13:00:00+01:00").isoformat(), "2026-10-21T12:00:00+00:00")

    def test_legacy_usd_inputs_are_canonicalized_to_usdt(self):
        self.assertEqual(
            validate_friday_pairs(["XBTUSD", "ETHUSD", "SOLUSD", "VIRTUALUSD", "HYPEUSD", "DOGEUSD", "LINKUSD", "ZECUSD", "NEARUSD", "SUIUSD", "LITUSD"]),
            ["XBTUSDT", "ETHUSDT", "SOLUSDT", "VIRTUALUSDT", "HYPEUSDT", "DOGEUSDT", "LINKUSDT", "ZECUSDT", "NEARUSDT", "SUIUSDT", "LITUSDT"],
        )

    def test_duplicate_aliases_do_not_create_duplicate_market_scans(self):
        self.assertEqual(validate_friday_pairs(["XBTUSD", "XBTUSDT", "ETHUSDT", "SUIUSDT", "SUIUSD"]), ["XBTUSDT", "ETHUSDT", "SUIUSDT"])

    def test_unknown_usd_pair_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "approved USDT universe"):
            validate_friday_pairs(["DOGEUSD"])

    def test_scan_exit_code_is_success_when_ai_review_is_temporarily_unavailable(self):
        # AI unavailability must not kill the scheduled market scan. It is still
        # a degraded scan, and candidate/alert gates remain closed without valid AI.
        from research.friday_monitor import _monitor_exit_code
        self.assertEqual(_monitor_exit_code("scan_complete_ai_unavailable"), 0)
        self.assertEqual(_monitor_exit_code("success"), 0)
        self.assertEqual(_monitor_exit_code("partial_failure"), 1)
        self.assertEqual(_monitor_exit_code("failure"), 1)

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
                with patch("research.friday_monitor.analyse_pair", return_value={"state": "WATCH_NEUTRAL", "thesis_direction": "neutral", "generated_at_utc": "2026-10-21T12:00:00Z", "four_hour_execution": {"last_closed_candle_utc": "2026-10-21T12:00:00Z"}}):
                    with patch("research.friday_monitor.create_friday_store") as store_factory:
                        store_factory.return_value.configured = False
                        result = run_monitor(["XBTUSD"], tmp)
            self.assertEqual(result["status"], "persistence_not_configured")
            self.assertEqual(result["pair_results"][0]["latest_setup_state"], "NO_SETUP")
            self.assertTrue(Path(tmp, "cron_run.json").exists())


if __name__ == "__main__":
    unittest.main()
