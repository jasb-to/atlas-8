import unittest

import pandas as pd

from research.structure_lifecycle import _line_break, evaluate_structure
from research.trendlines import Pivot, Trendline


class StructureLifecycleTests(unittest.TestCase):
    def test_resistance_break_uses_first_closed_candle_after_confirmation(self):
        frame = pd.DataFrame({
            "timestamp": pd.date_range("2026-01-01", periods=6, freq="4h", tz="UTC"),
            "open": [9, 9, 9, 9, 9, 9],
            "high": [10, 10, 10, 10, 10, 11],
            "low": [8, 8, 8, 8, 8, 8],
            "close": [9, 9, 9, 9, 10.2, 10.5],
        })
        start = Pivot(index=0, price=10.0, kind="resistance", confirmed_at=1)
        end = Pivot(index=2, price=10.0, kind="resistance", confirmed_at=3)
        line = Trendline(kind="resistance", start=start, end=end, slope_per_bar=0.0,
                         tolerance=0.01, touch_indices=(0, 2), violations=())
        result = _line_break(frame, line, buffer_pct=0.01)
        self.assertIsNotNone(result)
        self.assertEqual(result["break_index"], 4)
        self.assertEqual(result["direction"], "long")

    def test_support_break_is_short_and_no_future_data_required(self):
        frame = pd.DataFrame({
            "timestamp": pd.date_range("2026-01-01", periods=5, freq="4h", tz="UTC"),
            "open": [10] * 5, "high": [11] * 5, "low": [9, 9, 9, 8, 7],
            "close": [10, 10, 10, 8.8, 7.5],
        })
        start = Pivot(index=0, price=9.0, kind="support", confirmed_at=1)
        end = Pivot(index=2, price=9.0, kind="support", confirmed_at=3)
        line = Trendline(kind="support", start=start, end=end, slope_per_bar=0.0,
                         tolerance=0.01, touch_indices=(0, 2), violations=())
        result = _line_break(frame, line, buffer_pct=0.01)
        self.assertEqual(result["break_index"], 3)
        self.assertEqual(result["direction"], "short")

    def test_snapshot_rejects_missing_columns(self):
        with self.assertRaises(ValueError):
            evaluate_structure(pd.DataFrame({"close": [1.0]}))


if __name__ == "__main__":
    unittest.main()
