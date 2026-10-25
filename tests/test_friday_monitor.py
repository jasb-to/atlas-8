import unittest
from unittest.mock import patch
from tempfile import TemporaryDirectory
from pathlib import Path

from research.friday_monitor import _display_pair, _utc, run_monitor, validate_friday_pairs
from research.friday_persistence import RepoFolderFridayStore


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

    def test_new_liquid_markets_are_approved_and_btc_alias_is_canonical(self):
        self.assertEqual(
            validate_friday_pairs(["BTCUSDT", "XRPUSD", "ADAUSDT", "AVAXUSD", "BNBUSD"]),
            ["XBTUSDT", "XRPUSDT", "ADAUSDT", "AVAXUSDT", "BNBUSDT"],
        )

    def test_unknown_usd_pair_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "approved USDT universe"):
            validate_friday_pairs(["UNKNOWNUSD"])

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
            self.assertTrue(result["run_id"])
            self.assertEqual(result["markets_requested"], 1)
            self.assertEqual(result["markets_succeeded"], 1)
            # Every scanned market records an explicit outcome, including no-trade scans.
            self.assertEqual(result["alert_outcomes_total"], 1)
            self.assertEqual(result["pair_results"][0]["alert_results"][0]["status"], "no_actionable_signal")
            self.assertEqual(result["pair_results"][0]["run_id"], result["run_id"])
            self.assertTrue(Path(tmp, "cron_run.json").exists())

    def test_failed_market_writes_a_per_market_summary(self):
        with TemporaryDirectory() as tmp:
            with patch("research.friday_monitor.build_preview", side_effect=RuntimeError("fixture failure")):
                with patch("research.friday_monitor.create_friday_store") as store_factory:
                    store_factory.return_value.configured = False
                    result = run_monitor(["XBTUSDT"], tmp)
            summary_path = Path(tmp, "xbtusdt", "monitor_summary.json")
            self.assertTrue(summary_path.exists())
            import json
            summary = json.loads(summary_path.read_text())
            self.assertEqual(summary["status"], "error")
            self.assertEqual(summary["run_id"], result["run_id"])
            self.assertEqual(result["markets_failed"], 1)

    def test_alert_delivery_history_is_not_truncated_to_dashboard_window(self):
        import json
        with TemporaryDirectory() as tmp:
            store = RepoFolderFridayStore(root=tmp)
            for index in range(12):
                key = f"alert-{index}"
                self.assertTrue(store.claim_alert(
                    alert_key=key, pair="ETHUSDT", alert_type="ENTRY_1",
                    signal_time=f"2026-10-24T{index:02d}:00:00+00:00",
                ))
                store.finish_alert(key, status="sent", message_id=index + 1)
            durable = json.loads(Path(tmp, "alerts.json").read_text())
            dashboard = json.loads(Path(tmp, "dashboard.json").read_text())
            self.assertEqual(len(durable), 12)
            self.assertEqual(len(dashboard["alerts"]), 10)

    def test_alert_claim_prevents_duplicates_but_retries_known_failures(self):
        with TemporaryDirectory() as tmp:
            store = RepoFolderFridayStore(root=tmp)
            args = {
                "alert_key": "stable-alert-key", "pair": "BTCUSDT",
                "alert_type": "ENTRY_1", "signal_time": "2026-10-24T12:00:00+00:00",
            }
            self.assertTrue(store.claim_alert(**args))
            # A second scan must not reclaim a potentially in-flight Telegram send.
            self.assertFalse(store.claim_alert(**args))
            store.finish_alert(args["alert_key"], status="failed", error="Telegram timeout")
            # An explicit failed delivery is safe to retry on a later scan.
            self.assertTrue(store.claim_alert(**args))
            store.finish_alert(args["alert_key"], status="sent", message_id=123)
            self.assertFalse(store.claim_alert(**args))


if __name__ == "__main__":
    unittest.main()
