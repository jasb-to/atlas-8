import json
import os
import tempfile
import unittest
from unittest.mock import Mock, patch

import pandas as pd

from research.friday_market_data import fetch_ohlc
from research.friday_monitor import validate_friday_pairs, _ai_candidate_pass, _is_live_lifecycle_event
from research.friday_persistence import RepoFolderFridayStore


class FridayMarketDataTests(unittest.TestCase):
    def test_ai_review_is_a_hard_gate_for_paper_promotion(self):
        supportive = {
            "status": "ok",
            "response": {
                "assessment": "supportive",
                "priority_candidate": "friday-price-action-v2",
            },
        }
        self.assertEqual(_ai_candidate_pass(supportive, "friday-price-action-v2"),
                         (True, "ai_selected_supportive"))
        mixed_short = {"status": "ok", "response": {"assessment": "mixed", "priority_candidate": "short"}}
        self.assertEqual(_ai_candidate_pass(mixed_short, "friday-price-action-v2", "short"),
                         (True, "ai_selected_direction_with_caveats"))
        qualified_short = {"status": "ok", "response": {"assessment": "mixed", "priority_candidate": "structural_trade_plans.short"}}
        self.assertEqual(_ai_candidate_pass(qualified_short, "friday-price-action-v2", "short"),
                         (True, "ai_selected_direction_with_caveats"))
        self.assertFalse(_ai_candidate_pass(mixed_short, "friday-price-action-v2", "long")[0])
        caution = {"status": "ok", "response": {"assessment": "caution", "priority_candidate": "short"}}
        self.assertFalse(_ai_candidate_pass(caution, "friday-price-action-v2", "short")[0])
        wrong_strategy = {"status": "ok", "response": {"assessment": "supportive", "priority_candidate": "stoch-rsi-structural-reversal-v1"}}
        self.assertFalse(_ai_candidate_pass(wrong_strategy, "friday-price-action-v2", "short")[0])
        self.assertFalse(_ai_candidate_pass({"status": "error"}, "friday-price-action-v2")[0])

    def test_only_post_launch_usdt_lifecycle_events_are_live(self):
        self.assertFalse(_is_live_lifecycle_event("HYPEUSDT", {"signal_time": "2026-10-15T00:00:00Z"}))
        self.assertFalse(_is_live_lifecycle_event("HYPEUSD", {"signal_time": "2026-10-24T00:00:00Z"}))
        self.assertTrue(_is_live_lifecycle_event("HYPEUSDT", {"signal_time": "2026-10-24T00:00:00Z"}))

    def test_friday_cli_canonicalizes_legacy_aliases_and_only_scans_usdt_pairs(self):
        self.assertEqual(validate_friday_pairs([" xbtusdt ", "ETHUSDT"]), ["XBTUSDT", "ETHUSDT"])
        self.assertEqual(validate_friday_pairs(["XBTUSD"]), ["XBTUSDT"])
        with self.assertRaisesRegex(ValueError, "approved USDT universe"):
            validate_friday_pairs(["AVAXUSD"])
        with self.assertRaisesRegex(ValueError, "At least one"):
            validate_friday_pairs([])

    @patch("research.friday_market_data.requests.get")
    @patch("research.friday_market_data.fetch_kraken_ohlc")
    def test_missing_kraken_usdt_pair_falls_back_to_okx_usdt(self, kraken, get):
        kraken.side_effect = RuntimeError("Kraken API returned errors: ['EQuery:Unknown asset pair']")
        now = pd.Timestamp.now(tz="UTC")
        start_ms = int((now - pd.Timedelta(hours=204)).timestamp() * 1000)
        rows = []
        for i in range(50):
            open_ms = start_ms + i * 4 * 60 * 60 * 1000
            rows.append([str(open_ms), "100", "105", "99", "103", "10", "1020", "1020", "1"])
        get.return_value = Mock(status_code=200, json=lambda: {"code": "0", "msg": "", "data": rows}, raise_for_status=lambda: None)
        frame = fetch_ohlc("HYPEUSDT", 240)
        self.assertEqual(get.call_args.kwargs["params"]["instId"], "HYPE-USDT")
        self.assertEqual(len(frame), 50)
        self.assertIn("vwap", frame.columns)

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
