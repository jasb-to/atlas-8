import json
import os
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

from research.friday_market_data import fetch_ohlc
from research.friday_persistence import RepoFolderFridayStore


class FridayMarketDataTests(unittest.TestCase):
    def test_rejects_non_usdt_pair(self):
        with self.assertRaisesRegex(ValueError, "USDT-quoted"):
            fetch_ohlc("HYPEUSD")

    @patch("research.friday_market_data.fetch_kraken_ohlc")
    def test_passes_usdt_pair_to_kraken(self, fetch):
        timestamps = pd.date_range("2026-10-20", periods=50, freq="4h", tz="UTC")
        frame = pd.DataFrame({
            "timestamp": timestamps,
            "open": [100.0] * 50, "high": [105.0] * 50,
            "low": [99.0] * 50, "close": [103.0] * 50,
            "vwap": [102.0] * 50, "volume": [10.0] * 50,
            "trades": [10] * 50,
        })
        fetch.return_value = frame
        result = fetch_ohlc("XBTUSDT", 240)
        fetch.assert_called_once_with(pair="XBTUSDT", interval=240)
        self.assertEqual(len(result), 50)
        self.assertIn("timestamp", result.columns)
        self.assertIn("vwap", result.columns)


class FridayLedgerTrackingTests(unittest.TestCase):
    def test_historical_backfill_is_not_persisted_but_new_events_are_idempotent(self):
        old_cutoff = os.environ.get("FRIDAY_TRACKING_START_UTC")
        os.environ["FRIDAY_TRACKING_START_UTC"] = "2026-10-23T17:00:00+00:00"
        try:
            with tempfile.TemporaryDirectory() as directory:
                store = RepoFolderFridayStore(directory)
                old = {"event_key": "old", "pair": "HYPEUSDT", "signal_time": "2026-10-15T00:00:00+00:00"}
                fresh = {"event_key": "fresh", "pair": "HYPEUSDT", "signal_time": "2026-10-23T20:00:00+00:00"}
                self.assertFalse(store.record_trade_event(old)["persisted"])
                self.assertTrue(store.record_trade_event(fresh)["persisted"])
                self.assertTrue(store.record_trade_event(fresh)["persisted"])
                rows = json.loads((store.root / "events.json").read_text())
                self.assertEqual([row["event_key"] for row in rows], ["fresh"])
        finally:
            if old_cutoff is None:
                os.environ.pop("FRIDAY_TRACKING_START_UTC", None)
            else:
                os.environ["FRIDAY_TRACKING_START_UTC"] = old_cutoff


if __name__ == "__main__":
    unittest.main()
