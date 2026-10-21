import unittest
import pandas as pd

from research.strategy_preview import latest_setup_state


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

    def test_failed_break_is_not_shown_as_waiting_entry(self):
        event = self.event("failed_break", failure_time="2026-01-01T08:00:00+00:00")
        result = latest_setup_state([event], pd.Timestamp("2026-01-10T00:00:00Z"))
        self.assertEqual(result["state"], "INVALIDATED")


if __name__ == "__main__":
    unittest.main()
