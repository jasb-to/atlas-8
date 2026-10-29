"""Verify weekly/daily candle provenance survives persistence to dashboard JSON."""
from __future__ import annotations

import json
import tempfile
import unittest

import numpy as np
import pandas as pd

from research.friday_persistence import RepoFolderFridayStore, make_archive_record
from research.weekly_thesis import evaluate_sheldon_thesis


class WeeklyThesisPersistenceProvenanceTests(unittest.TestCase):
    def test_persisted_weekly_thesis_contains_both_candle_timestamps(self) -> None:
        as_of = pd.Timestamp("2026-10-29T12:00:00Z")
        weekly_times = pd.date_range(end=as_of - pd.Timedelta(days=14), periods=220, freq="7D")
        daily_times = pd.date_range(end=as_of - pd.Timedelta(days=1), periods=70, freq="D")
        weekly = pd.DataFrame({
            "timestamp": weekly_times,
            "close": np.linspace(1.0, 2.0, len(weekly_times)),
        })
        daily = pd.DataFrame({
            "timestamp": daily_times,
            "close": np.linspace(1.2, 2.4, len(daily_times)),
        })

        thesis = evaluate_sheldon_thesis(
            "ADAUSDT", weekly_candles=weekly, daily_candles=daily, as_of=as_of,
        )
        record = make_archive_record(
            record_key="weekly-provenance-test",
            record_type="weekly_thesis_snapshot",
            pair="ADAUSDT",
            timeframe="1W+1D",
            signal_time=thesis["generated_at_utc"],
            payload=thesis,
        )

        with tempfile.TemporaryDirectory() as directory:
            store = RepoFolderFridayStore(root=directory)
            store.record_archive(record)
            with open(f"{directory}/dashboard.json", encoding="utf-8") as handle:
                dashboard = json.load(handle)

        persisted = dashboard["weekly_theses"][0]["payload"]
        self.assertIn("weekly_candle_time_utc", persisted)
        self.assertIn("daily_candle_time_utc", persisted)
        self.assertEqual(persisted["weekly_candle_time_utc"], weekly_times[-1].isoformat())
        self.assertEqual(persisted["daily_candle_time_utc"], daily_times[-1].isoformat())
        self.assertNotEqual(persisted["weekly_candle_time_utc"], persisted["daily_candle_time_utc"])


if __name__ == "__main__":
    unittest.main()
