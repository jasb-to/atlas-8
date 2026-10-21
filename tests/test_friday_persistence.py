import unittest
from research.friday_persistence import (
    SupabaseFridayStore, event_key, make_archive_record, make_cron_run, make_trade_event,
)


class FridayPersistenceTests(unittest.TestCase):
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

    def test_unconfigured_store_does_not_claim_success(self):
        store = SupabaseFridayStore(url="", key="")
        self.assertFalse(store.configured)
        with self.assertRaises(RuntimeError):
            store.recent_50_trades()


if __name__ == "__main__":
    unittest.main()
