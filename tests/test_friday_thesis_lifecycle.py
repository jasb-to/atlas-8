import unittest
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

from research.friday_multitimeframe import _daily_thesis_lifecycle
from research.friday_monitor import _thesis_matches_event


class DailyThesisLifecycleTests(unittest.TestCase):
    def _daily(self, changes=None):
        dates = pd.date_range("2026-01-01", periods=100, freq="D", tz="UTC")
        rows = []
        changes = changes or {}
        for i, stamp in enumerate(dates):
            row = {"timestamp": stamp, "open": 99.0, "high": 99.8, "low": 98.0, "close": 98.5}
            row.update(changes.get(i, {}))
            rows.append(row)
        return pd.DataFrame(rows)

    def _structure(self):
        return SimpleNamespace(broken_lines=[{
            "kind": "support",
            "touch_count": 3,
            "anchor_start_index": 20,
            "anchor_end_index": 50,
            "anchor_start_time": "2026-01-21T00:00:00+00:00",
            "anchor_end_time": "2026-02-20T00:00:00+00:00",
            "anchor_start_wick": 100.0,
            "anchor_end_wick": 100.0,
            "slope_per_bar": 0.0,
            "break": {
                "break_index": 70,
                "break_time": "2026-03-12T00:00:00+00:00",
                "line_price": 100.0,
                "close": 98.5,
                "direction": "short",
            },
        }])

    def test_daily_break_is_retained_after_price_moves_away(self):
        with patch("research.friday_multitimeframe.evaluate_structure", return_value=self._structure()):
            thesis = _daily_thesis_lifecycle(self._daily())
        self.assertEqual(thesis["state"], "WAITING_FOR_RETEST")
        self.assertEqual(thesis["direction"], "short")
        self.assertEqual(thesis["line_price_at_break"], 100.0)
        self.assertEqual(thesis["thesis_id"], "short|2026-03-12T00:00:00+00:00|2026-01-21T00:00:00+00:00|2026-02-20T00:00:00+00:00")

    def test_daily_retest_and_hold_are_recorded(self):
        daily = self._daily({80: {"high": 100.0, "close": 99.5}})
        with patch("research.friday_multitimeframe.evaluate_structure", return_value=self._structure()):
            thesis = _daily_thesis_lifecycle(daily)
        self.assertEqual(thesis["state"], "RETEST_HELD")
        self.assertEqual(thesis["retest_time"], daily.iloc[80].timestamp.isoformat())

    def test_daily_close_back_through_line_invalidates_thesis(self):
        daily = self._daily({80: {"high": 101.0, "close": 100.5}})
        with patch("research.friday_multitimeframe.evaluate_structure", return_value=self._structure()):
            thesis = _daily_thesis_lifecycle(daily)
        self.assertEqual(thesis["state"], "INVALIDATED")
        self.assertEqual(thesis["invalidation_time"], daily.iloc[80].timestamp.isoformat())

    def test_execution_trigger_must_match_active_daily_thesis_direction(self):
        thesis = {"state": "WAITING_FOR_RETEST", "direction": "short"}
        self.assertTrue(_thesis_matches_event({"direction": "short"}, thesis))
        self.assertFalse(_thesis_matches_event({"direction": "long"}, thesis))
        self.assertFalse(_thesis_matches_event({"direction": "short"}, {"state": "INVALIDATED", "direction": "short"}))
        self.assertFalse(_thesis_matches_event({"direction": "short"}, {"state": "NO_THESIS"}))


if __name__ == "__main__":
    unittest.main()
