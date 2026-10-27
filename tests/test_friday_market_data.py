import json
import os
import tempfile
import unittest
from unittest.mock import Mock, patch

import pandas as pd

from research.friday_market_data import fetch_ohlc, _pair_cache
from research.friday_monitor import validate_friday_pairs, _ai_candidate_pass, _is_live_lifecycle_event
from research.friday_persistence import RepoFolderFridayStore


class FridayMarketDataTests(unittest.TestCase):
    def setUp(self):
        _pair_cache.clear()

    @patch.dict(os.environ, {"FRIDAY_AI_REVIEW_ENABLED": "true"}, clear=False)
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
            validate_friday_pairs(["UNKNOWNUSD"])
        with self.assertRaisesRegex(ValueError, "At least one"):
            validate_friday_pairs([])

    @patch("research.friday_market_data.requests.get")
    @patch("research.friday_market_data.fetch_kraken_ohlc")
    def test_missing_kraken_usdt_pair_is_unavailable_without_secondary_exchange(self, kraken, get):
        get.return_value = Mock(
            json=lambda: {"error": [], "result": {
                "XXBTZUSD": {"altname": "XBTUSD", "wsname": "XBT/USD"}
            }},
            raise_for_status=lambda: None,
        )
        with self.assertRaisesRegex(RuntimeError, "UNAVAILABLE — NO KRAKEN PAIR: HYPEUSDT"):
            fetch_ohlc("HYPEUSDT", 240)
        kraken.assert_not_called()
        get.assert_called_once()

    @patch("research.friday_market_data.requests.get")
    @patch("research.friday_market_data.fetch_kraken_ohlc")
    def test_resolves_requested_symbol_from_kraken_assetpairs_catalogue(self, kraken, get):
        get.return_value = Mock(
            json=lambda: {"error": [], "result": {
                "XXBTZUSDT": {"altname": "XBTUSDT", "wsname": "XBT/USDT"}
            }},
            raise_for_status=lambda: None,
        )
        timestamps = pd.date_range("2026-10-20", periods=50, freq="4h", tz="UTC")
        frame = pd.DataFrame({
            "timestamp": timestamps, "open": [100.0] * 50, "high": [105.0] * 50,
            "low": [99.0] * 50, "close": [103.0] * 50, "vwap": [102.0] * 50,
            "volume": [10.0] * 50, "trades": [10] * 50,
        })
        kraken.return_value = frame
        result = fetch_ohlc("BTCUSDT", 240)
        kraken.assert_called_once_with(pair="XBTUSDT", interval=240)
        self.assertEqual(len(result), 50)

    @patch("research.friday_market_data.requests.get")
    @patch("research.friday_market_data.fetch_kraken_ohlc")
    def test_passes_resolved_usdt_pair_to_kraken(self, fetch, get):
        get.return_value = Mock(
            json=lambda: {"error": [], "result": {
                "XXBTZUSDT": {"altname": "XBTUSDT", "wsname": "XBT/USDT"}
            }},
            raise_for_status=lambda: None,
        )
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
