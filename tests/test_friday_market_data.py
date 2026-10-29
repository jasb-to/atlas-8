import json
import os
import tempfile
import unittest
from unittest.mock import Mock, patch

import pandas as pd

from research.friday_market_data import fetch_ohlc, aggregate_ohlc, _pair_cache
from research.friday_monitor import validate_friday_pairs, _ai_candidate_pass, _is_live_lifecycle_event
from research.friday_persistence import RepoFolderFridayStore


class FridayMarketDataTests(unittest.TestCase):
    def setUp(self):
        _pair_cache.clear()
        import research.friday_market_data as market_data
        market_data._asset_pairs_catalogue = None

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
        self.assertEqual(validate_friday_pairs(["UNIUSDT"]), ["UNIUSDT"])
        with self.assertRaisesRegex(ValueError, "approved USDT universe"):
            validate_friday_pairs(["LITUSDT"])
        self.assertEqual(validate_friday_pairs(["XBTUSD"]), ["XBTUSDT"])
        with self.assertRaisesRegex(ValueError, "approved USDT universe"):
            validate_friday_pairs(["UNKNOWNUSD"])
        with self.assertRaisesRegex(ValueError, "At least one"):
            validate_friday_pairs([])

    @patch("research.friday_market_data.requests.get")
    @patch("research.friday_market_data.fetch_kraken_ohlc")
    def test_hype_usdt_request_uses_scoped_usd_fallback_and_marks_thesis_currency(self, kraken, get):
        import contextlib
        import io
        from research.weekly_thesis import evaluate_sheldon_thesis
        from research.friday_persistence import make_archive_record

        get.return_value = Mock(
            json=lambda: {"error": [], "result": {
                "HYPEUSD": {"altname": "HYPEUSD", "wsname": "HYPE/USD"}
            }},
            raise_for_status=lambda: None,
        )
        timestamps = pd.date_range("2026-10-01", periods=50, freq="4h", tz="UTC")
        frame = pd.DataFrame({
            "timestamp": timestamps, "open": [100.0] * 50, "high": [105.0] * 50,
            "low": [99.0] * 50, "close": [103.0] * 50, "vwap": [102.0] * 50,
            "volume": [10.0] * 50, "trades": [10] * 50,
        })
        kraken.return_value = frame
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = fetch_ohlc("HYPEUSDT", 240)
        self.assertIn(
            "WARNING: No USDT market on Kraken for HYPE. Falling back to HYPEUSD for structural analysis only.",
            output.getvalue(),
        )
        kraken.assert_called_once_with(pair="HYPEUSD", interval=240)
        self.assertEqual(result.attrs["source_pair"], "HYPEUSD")
        self.assertEqual(result.attrs["quote_currency"], "USD")
        self.assertEqual(result.attrs["source"], "Kraken Spot (USD)")

        weekly = result.iloc[:10].copy()
        weekly["timestamp"] = pd.date_range(end="2026-10-18", periods=10, freq="7D", tz="UTC")
        weekly.attrs.update(result.attrs)
        daily = result.iloc[:10].copy()
        daily["timestamp"] = pd.date_range(end="2026-10-24", periods=10, freq="D", tz="UTC")
        daily.attrs.update(result.attrs)
        thesis = evaluate_sheldon_thesis("HYPEUSDT", weekly_candles=weekly, daily_candles=daily)
        self.assertEqual(thesis["quote_currency"], "USD")
        record = make_archive_record(
            record_key="hype-weekly-usd", record_type="weekly_thesis_snapshot",
            pair="HYPEUSDT", timeframe="1W", signal_time=thesis["generated_at_utc"],
            payload=thesis,
        )
        self.assertEqual(record["payload"]["quote_currency"], "USD")
        self.assertEqual(record["payload"]["data_source_weekly"], "Kraken Spot (USD)")

    @patch("research.friday_market_data.requests.get")
    @patch("research.friday_market_data.fetch_kraken_ohlc")
    def test_usd_fallback_is_not_used_for_unapproved_asset(self, kraken, get):
        get.return_value = Mock(
            json=lambda: {"error": [], "result": {
                "FOOUSD": {"altname": "FOOUSD", "wsname": "FOO/USD"}
            }},
            raise_for_status=lambda: None,
        )
        with self.assertRaisesRegex(RuntimeError, "UNAVAILABLE — NO KRAKEN USDT OHLC HISTORY: FOOUSDT"):
            fetch_ohlc("FOOUSDT", 240)
        kraken.assert_not_called()

    def test_aggregate_4h_to_daily_uses_ohlcv_rules_and_complete_utc_day(self):
        timestamps = pd.date_range("2026-10-20T00:00:00Z", periods=12, freq="4h")
        frame = pd.DataFrame({
            "timestamp": timestamps,
            "open": [float(i + 1) for i in range(12)],
            "high": [float(i + 3) for i in range(12)],
            "low": [float(i) for i in range(12)],
            "close": [float(i + 2) for i in range(12)],
            "volume": [10.0] * 12,
            "vwap": [float(i + 2) for i in range(12)],
        })
        frame.attrs["source"] = "Kraken Spot"
        result = aggregate_ohlc(frame, 240, 1440)
        self.assertEqual(len(result), 2)
        self.assertEqual(result.iloc[0]["open"], 1.0)
        self.assertEqual(result.iloc[0]["high"], 8.0)
        self.assertEqual(result.iloc[0]["low"], 0.0)
        self.assertEqual(result.iloc[0]["close"], 7.0)
        self.assertEqual(result.iloc[0]["volume"], 60.0)
        self.assertEqual(result.attrs["source"], "Kraken Spot 4H aggregated to 1D")

    def test_aggregate_rejects_days_with_missing_4h_candles(self):
        timestamps = pd.date_range("2026-10-20T00:00:00Z", periods=6, freq="4h").delete(2)
        frame = pd.DataFrame({
            "timestamp": timestamps, "open": 1.0, "high": 2.0,
            "low": 0.5, "close": 1.5, "volume": 10.0,
        })
        result = aggregate_ohlc(frame, 240, 1440)
        self.assertTrue(result.empty)

    @patch("research.friday_market_data.requests.get")
    @patch("research.friday_market_data.fetch_kraken_ohlc")
    def test_resolves_dogecoin_to_kraken_xdg_alias(self, kraken, get):
        get.return_value = Mock(
            json=lambda: {"error": [], "result": {
                "XDGUSDT": {"altname": "XDGUSDT", "wsname": "XDG/USDT"}
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
        result = fetch_ohlc("DOGEUSDT", 240)
        self.assertEqual(len(result), 50)
        kraken.assert_called_once_with(pair="XDGUSDT", interval=240)
        self.assertEqual(result.attrs["source"], "Kraken Spot")

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


    @patch("research.friday_market_data.requests.get")
    @patch("research.friday_market_data.fetch_kraken_ohlc")
    def test_assetpairs_catalogue_is_fetched_once_for_multiple_standard_symbols(self, kraken, get):
        catalogue = {
            "HYPEUSDT": {"altname": "HYPEUSDT", "wsname": "HYPE/USDT"},
            "SUIUSDT": {"altname": "SUIUSDT", "wsname": "SUI/USDT"},
            "ZECUSDT": {"altname": "ZECUSDT", "wsname": "ZEC/USDT"},
            "NEARUSDT": {"altname": "NEARUSDT", "wsname": "NEAR/USDT"},
            "UNIUSDT": {"altname": "UNIUSDT", "wsname": "UNI/USDT"},
        }
        get.return_value = Mock(
            json=lambda: {"error": [], "result": catalogue},
            raise_for_status=lambda: None,
        )
        timestamps = pd.date_range("2026-10-20", periods=50, freq="7D", tz="UTC")
        frame = pd.DataFrame({
            "timestamp": timestamps, "open": [100.0] * 50, "high": [105.0] * 50,
            "low": [99.0] * 50, "close": [103.0] * 50, "vwap": [102.0] * 50,
            "volume": [10.0] * 50, "trades": [10] * 50,
        })
        kraken.return_value = frame

        for symbol in ("HYPEUSDT", "SUIUSDT", "ZECUSDT", "NEARUSDT", "UNIUSDT"):
            result = fetch_ohlc(symbol, 240)
            self.assertEqual(len(result), 50)
            self.assertEqual(result.attrs["source_pair"], symbol)

        self.assertEqual(get.call_count, 1)
        self.assertEqual(
            [call.kwargs["pair"] for call in kraken.call_args_list],
            ["HYPEUSDT", "SUIUSDT", "ZECUSDT", "NEARUSDT", "UNIUSDT"],
        )


    @patch("research.friday_market_data.requests.get")
    @patch("research.friday_market_data.fetch_kraken_ohlc")
    def test_uni_and_zec_resolve_exact_kraken_usd_pair_names(self, kraken, get):
        import contextlib
        import io

        get.return_value = Mock(json=lambda: {"error": [], "result": {
            "UNIUSD": {"altname": "UNIUSD", "wsname": "UNI/USD"},
            "ZECUSD": {"altname": "ZECUSD", "wsname": "ZEC/USD"},
        }}, raise_for_status=lambda: None)
        timestamps = pd.date_range("2025-01-06", periods=220, freq="7D", tz="UTC")
        frame = pd.DataFrame({
            "timestamp": timestamps, "open": 10.0, "high": 11.0,
            "low": 9.0, "close": 10.5, "vwap": 10.2, "volume": 100.0, "trades": 5,
        })
        kraken.side_effect = lambda pair, interval: frame.copy()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            uni = fetch_ohlc("UNIUSDT", 240)
            zec = fetch_ohlc("ZECUSDT", 240)
        self.assertEqual(uni.attrs["source_pair"], "UNIUSD")
        self.assertEqual(zec.attrs["source_pair"], "ZECUSD")
        self.assertIn("requested=UNIUSDT pair_id=UNIUSD altname=UNIUSD wsname=UNI/USD quote=USD", output.getvalue())
        self.assertIn("requested=ZECUSDT pair_id=ZECUSD altname=ZECUSD wsname=ZEC/USD quote=USD", output.getvalue())
        self.assertEqual(
            [call.kwargs["pair"] for call in kraken.call_args_list],
            ["UNIUSD", "ZECUSD"],
        )

    def test_loads_persisted_bulk_history_for_daily_and_weekly_timeframes(self):
        from research.friday_historical_data import load_seeded_ohlc

        with tempfile.TemporaryDirectory() as directory:
            data = {
                "schema_version": 1,
                "pairs": {
                    "SUIUSDT": {
                        "source_pair": "SUIUSD",
                        "quote_currency": "USD",
                        "daily": [{
                            "timestamp": "2024-01-01T00:00:00+00:00",
                            "open": 1.0, "high": 1.2, "low": 0.9,
                            "close": 1.1, "volume": 100.0, "trades": 10,
                        }],
                        "weekly": [{
                            "timestamp": "2024-01-01T00:00:00+00:00",
                            "open": 1.0, "high": 1.5, "low": 0.8,
                            "close": 1.2, "volume": 700.0, "trades": 70,
                        }],
                    }
                },
            }
            root = os.path.join(directory, "testing", "friday", "data")
            os.makedirs(root)
            with open(os.path.join(root, "ohlc_history.json"), "w", encoding="utf-8") as handle:
                json.dump(data, handle)
            daily = load_seeded_ohlc("SUIUSDT", 1440, root=root)
            weekly = load_seeded_ohlc("SUIUSDT", 10080, root=root)
            self.assertEqual(len(daily), 1)
            self.assertEqual(len(weekly), 1)
            self.assertEqual(daily.attrs["source_pair"], "SUIUSD")
            self.assertEqual(daily.attrs["quote_currency"], "USD")
            self.assertTrue(daily.attrs["seeded_history"])
            self.assertEqual(weekly.iloc[0]["close"], 1.2)


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
