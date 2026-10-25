import unittest
import json
import tempfile
from unittest.mock import Mock, patch
from research.friday_persistence import (
    RepoFolderFridayStore, SupabaseFridayStore, UpstashFridayStore, event_key, make_archive_record, make_cron_run, make_trade_event,
)


class FridayPersistenceTests(unittest.TestCase):
    def test_upstash_store_uses_atomic_alert_claim(self):
        session = Mock()
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"result": "OK"}
        session.post.return_value = response
        store = UpstashFridayStore(url="https://example.upstash.io", token="test-token")
        with patch("research.friday_persistence.requests.post", session.post):
            self.assertTrue(store.claim_alert(alert_key="a1", pair="XBTUSD", alert_type="ENTRY_1",
                                              signal_time="2026-10-21T12:00:00Z"))
        commands = [call.kwargs["json"] for call in session.post.call_args_list]
        claim = next(command for command in commands if command[0] == "SET")
        self.assertIn("NX", claim)
        self.assertNotIn("test-token", str(commands))

    def test_upstash_missing_credentials_is_not_configured(self):
        store = UpstashFridayStore(url="", token="")
        self.assertFalse(store.configured)

    def test_event_key_is_idempotent_for_same_signal(self):
        event = {"event":"BREAK_CLOSE_PROBE","signal_time":"2026-10-21T12:00:00Z",
                 "direction":"long","line_kind":"resistance",
                 "anchor_start_time":"a","anchor_end_time":"b"}
        self.assertEqual(event_key("XBTUSD","4H",event), event_key("xbtusd","4H",event))

    def test_trade_row_captures_lifecycle(self):
        event = {"event":"BREAK_CLOSE_PROBE","signal_time":"2026-10-21T12:00:00Z",
                 "direction":"long","line_kind":"resistance","close_at_break":100,
                 "outcome":"retest_held_waiting_continuation","retest_confirmed":True}
        row = make_trade_event(pair="XBTUSD", timeframe="4H", event=event, run_id="run-1")
        self.assertEqual(row["direction"], "LONG")
        self.assertTrue(row["retest_confirmed"])
        self.assertEqual(row["outcome"], "retest_held_waiting_continuation")
        self.assertEqual(len(row["event_key"]), 64)

    def test_archive_record_is_separate_from_bounded_recent_ledger(self):
        row = make_archive_record(record_key="stable", record_type="decision_snapshot",
            pair="XBTUSD", timeframe="4H", signal_time="2026-10-21T12:00:00Z",
            payload={"state": "NO_TRADE"})
        self.assertEqual(row["record_key"], "stable")
        self.assertEqual(row["payload"]["state"], "NO_TRADE")
        self.assertEqual(row["record_type"], "decision_snapshot")

    def test_run_log_includes_pairs_and_error(self):
        row = make_cron_run(run_id="run-1", status="partial_failure",
            pair_results=[{"pair":"XBTUSD","status":"ok"}],
            started_at="2026-10-21T12:00:00Z", error="VIRTUAL timeout")
        self.assertEqual(row["pairs_checked"], 1)
        self.assertEqual(row["error"], "VIRTUAL timeout")


    def test_repo_store_persists_and_refreshes_dashboard(self):
        with tempfile.TemporaryDirectory() as directory:
            store = RepoFolderFridayStore(root=directory)
            event = make_trade_event(
                pair="XBTUSDT", timeframe="4H",
                event={"event":"BREAK_CLOSE_PROBE","signal_time":"2026-10-24T12:00:00Z",
                       "direction":"long","line_kind":"resistance","close_at_break":100},
                run_id="run-1",
            )
            store.record_trade_event(event)
            store.record_archive(make_archive_record(
                record_key="snapshot-1", record_type="decision_snapshot", pair="XBTUSDT",
                timeframe="4H", signal_time="2026-10-24T12:00:00Z", payload={"state":"WATCH"},
            ))
            store.record_archive(make_archive_record(
                record_key="weekly-1", record_type="weekly_thesis_snapshot", pair="XBTUSDT",
                timeframe="1W+1D", signal_time="2026-10-24T12:00:00Z", payload={"status":"active","direction":"long"},
            ))
            store.record_cron_run(make_cron_run(
                run_id="run-1", status="success", pair_results=[{"pair":"XBTUSDT","status":"ok"}],
                started_at="2026-10-24T12:00:00Z",
            ))
            with open(f"{directory}/dashboard.json", encoding="utf-8") as handle:
                dashboard = json.load(handle)
            self.assertEqual(dashboard["storage_backend"], "github_repository_folder")
            self.assertEqual(len(dashboard["events"]), 1)
            self.assertEqual(len(dashboard["snapshots"]), 1)
            self.assertEqual(dashboard["runs"][0]["run_id"], "run-1")

    def test_repo_paper_trade_is_in_dashboard_and_alert_history_is_bounded_separately(self):
        with tempfile.TemporaryDirectory() as directory:
            store = RepoFolderFridayStore(root=directory)
            store.upsert_paper_trade({
                "trade_id": "trade-1", "pair": "HYPEUSDT", "status": "OPEN",
                "signal_time": "2026-10-24T12:00:00Z", "stop_loss": 84.0, "take_profit": 75.0,
            })
            with open(f"{directory}/dashboard.json", encoding="utf-8") as handle:
                dashboard = json.load(handle)
            self.assertEqual(dashboard["paper_trades"][0]["trade_id"], "trade-1")
            for index in range(12):
                key = f"alert-{index}"
                self.assertTrue(store.claim_alert(
                    alert_key=key, pair="HYPEUSD", alert_type="TEST",
                    signal_time=f"2026-10-22T{index:02d}:00:00Z",
                ))
            with open(f"{directory}/alerts.json", encoding="utf-8") as handle:
                alerts = json.load(handle)
            self.assertEqual(len(alerts), 12)

    def test_dashboard_filters_prelaunch_usd_snapshots(self):
        with tempfile.TemporaryDirectory() as directory:
            store = RepoFolderFridayStore(root=directory)
            store.record_archive(make_archive_record(
                record_key="old-usd", record_type="decision_snapshot", pair="XBTUSD",
                timeframe="4H", signal_time="2026-10-15T12:00:00Z", payload={"state":"OLD"},
            ))
            store.record_archive(make_archive_record(
                record_key="new-usdt", record_type="decision_snapshot", pair="XBTUSDT",
                timeframe="4H", signal_time="2026-10-24T12:00:00Z", payload={"state":"CURRENT"},
            ))
            store.record_cron_run({
                "run_id": "legacy-run", "status": "partial_failure",
                "started_at": "2026-10-24T12:00:00Z",
                "pair_results": [{"pair": "XBTUSDT", "status": "ok"}],
                "trade_events_seen": 186,
            })
            store.record_cron_run(make_cron_run(
                run_id="clean-run", status="success",
                pair_results=[{"pair": "XBTUSDT", "status": "ok"}],
                started_at="2026-10-24T12:00:00Z",
            ))
            with open(f"{directory}/dashboard.json", encoding="utf-8") as handle:
                dashboard = json.load(handle)
            self.assertEqual([row["record_key"] for row in dashboard["snapshots"]], ["new-usdt"])
            self.assertEqual([row["run_id"] for row in dashboard["runs"]], ["clean-run"])

    def test_dashboard_shows_all_15_scanned_market_snapshots(self):
        with tempfile.TemporaryDirectory() as directory:
            store = RepoFolderFridayStore(root=directory)
            for index in range(15):
                store.record_archive(make_archive_record(
                    record_key=f"snapshot-{index}",
                    record_type="decision_snapshot",
                    pair=f"COIN{index}USDT",
                    timeframe="4H",
                    signal_time="2026-10-24T12:00:00Z",
                    payload={"index": index},
                ))
            with open(f"{directory}/dashboard.json", encoding="utf-8") as handle:
                dashboard = json.load(handle)
            self.assertEqual(len(dashboard["snapshots"]), 15)
            self.assertEqual(
                {row["pair"] for row in dashboard["snapshots"]},
                {f"COIN{index}USDT" for index in range(15)},
            )

    def test_unconfigured_store_does_not_claim_success(self):
        store = SupabaseFridayStore(url="", key="")
        self.assertFalse(store.configured)
        with self.assertRaises(RuntimeError):
            store.recent_50_trades()


if __name__ == "__main__":
    unittest.main()
