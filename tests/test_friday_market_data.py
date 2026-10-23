import json
import os
import tempfile
import unittest
from unittest.mock import Mock, patch

import pandas as pd

from research.friday_market_data import fetch_ohlc
from research.friday_persistence import RepoFolderFridayStore


class FridayMarketDataTests(unittest.TestCase):
    def test_rejects_non_usdt_pair(self):
        with self.assertRaisesRegex(ValueError, "USDT-quoted"):
            fetch_ohlc("HYPEUSD")

    @patch("research.friday_market_data.requests.get")
    def test_maps_xbt_to_binance_btc_and_drops_open_candle(self, get):
        now = pd.Timestamp.now(tz="UTC")
        open_ms = int((now - pd.Timedelta(hours=8)).timestamp() * 1000)
        rows = []
        for i in range(50):
            t = open_ms + i * 4 * 60 * 60 * 1000
            rows.append([t, "100", "105", "99", "103", "10", t + 4 * 60 * 60 * 1000 - 1, "1020", 10, "5", "510", "0"])
        get.return_value = Mock(status_code=200, json=lambda: rows, raise_for_status=lambda: None)
        frame = fetch_ohlc("XBTUSDT", 240)
        self.assertEqual(get.call_args.kwargs["params"]["symbol"], "BTCUSDT")
        self.assertEqual(len(frame), 50)
        self.assertIn("timestamp", frame.columns)
        self.assertIn("vwap", frame.columns)


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
