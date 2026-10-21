import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from research.strategy_preview import entry2_replay_events, latest_setup_state, write_jsonl


class FridayLifecycleStateTests(unittest.TestCase):
    def event(self, outcome, **extra):
        row = {
            "signal_time": "2026-01-01T00:00:00+00:00",
            "direction": "long",
            "line_kind": "resistance",
            "line_price_at_break": 100.0,
            "touch_count": 3,
            "outcome": outcome,
        }
        row.update(extra)
        return row

    def test_old_completed_continuation_remains_historical_not_expired(self):
        event = self.event(
            "continuation_add_confirmed",
            retest_confirmed=True,
            retest_confirmed_time="2026-01-01T04:00:00+00:00",
            continuation_add_time="2026-01-01T08:00:00+00:00",
            continuation_add_close=103.0,
        )
        result = latest_setup_state([event], pd.Timestamp("2026-01-10T00:00:00Z"))
        self.assertEqual(result["state"], "CONTINUATION_ADD_CONFIRMED")

    def test_no_continuation_after_held_retest_is_expired(self):
        event = self.event(
            "no_continuation_within_window",
            retest_confirmed=True,
            retest_confirmed_time="2026-01-01T04:00:00+00:00",
        )
        result = latest_setup_state([event], pd.Timestamp("2026-01-02T00:00:00Z"))
        self.assertEqual(result["state"], "EXPIRED")
        self.assertIn("no continuation", result["message"].lower())



    def test_entry2_replay_uses_only_confirmed_continuation_timestamp(self):
        events = [
            {"signal_time": "2026-01-01T00:00:00Z", "continuation_add_time": "2026-01-01T08:00:00Z", "outcome": "continuation_add_confirmed", "direction": "long"},
            {"signal_time": "2026-01-01T04:00:00Z", "outcome": "failed_break", "direction": "short"},
            {"signal_time": "2026-01-01T12:00:00Z", "continuation_add_time": None, "outcome": "pending", "direction": "long"},
        ]
        result = entry2_replay_events(events)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["break_signal_time"], "2026-01-01T00:00:00Z")
        self.assertEqual(result[0]["signal_time"], "2026-01-01T08:00:00Z")
        self.assertEqual(result[0]["entry_type"], "ENTRY_2_CONTINUATION")

    def test_jsonl_writer_uses_one_physical_line_per_record(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "nested" / "events.jsonl"
            rows = [{"event": "break", "n": 1}, {"event": "retest", "n": 2}]
            write_jsonl(path, rows)
            physical_lines = path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(physical_lines), 2)
            self.assertEqual([json.loads(line) for line in physical_lines], rows)

    def test_failed_break_is_not_shown_as_waiting_entry(self):
        event = self.event("failed_break", failure_time="2026-01-01T08:00:00+00:00")
        result = latest_setup_state([event], pd.Timestamp("2026-01-10T00:00:00Z"))
        self.assertEqual(result["state"], "INVALIDATED")


if __name__ == "__main__":
    unittest.main()
