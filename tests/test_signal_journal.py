import json
import tempfile
import unittest
from pathlib import Path

from research.signal_journal import append_decision, build_decision_record, read_decisions


class SignalJournalTests(unittest.TestCase):
    def test_records_no_trade_and_explicit_model_not_configured(self):
        record = build_decision_record(
            pair="VIRTUALUSD",
            timeframe="4h",
            candle_timestamp=1792584000,
            strategy_version="friday-direction-change-v1",
            status="WAIT_FOR_CONFLUENCE",
            direction=None,
            evidence={"fib_zero_touched": False, "support_overlap": False},
            blockers=["no_support_or_liquidity_evidence"],
            recorded_at="2026-10-21T00:00:00+00:00",
        )
        self.assertEqual(record["status"], "WAIT_FOR_CONFLUENCE")
        self.assertEqual(record["model"]["status"], "not_configured")
        self.assertIsNone(record["outcome"])

    def test_jsonl_round_trip_and_append(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "signals.jsonl"
            first = build_decision_record(
                pair="XBTUSD", timeframe="4h", candle_timestamp=1792584000,
                strategy_version="v1", status="ENTRY_1_CANDIDATE",
                direction="LONG", entry=100.0, stop=97.0,
                targets=[{"price": 108.0, "reason": "prior_swing_high"}],
                evidence={"fib_zero_touched": True, "reclaim": True},
                model={"status": "skipped", "provider": "none",
                       "model_id": None, "response": None, "error": "not_configured"},
            )
            append_decision(path, first)
            append_decision(path, {**first, "status": "WAIT_FOR_CONFLUENCE"})
            rows = read_decisions(path)
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["direction"], "LONG")
            self.assertEqual(rows[1]["status"], "WAIT_FOR_CONFLUENCE")
            self.assertEqual(len(path.read_text(encoding="utf-8").splitlines()), 2)

    def test_rejects_invalid_direction_and_timestamp(self):
        with self.assertRaises(ValueError):
            build_decision_record(pair="XBTUSD", timeframe="4h",
                candle_timestamp=0, strategy_version="v1", status="WAIT")
        with self.assertRaises(ValueError):
            build_decision_record(pair="XBTUSD", timeframe="4h",
                candle_timestamp=1792584000, strategy_version="v1",
                status="WAIT", direction="MAYBE")

    def test_non_finite_numbers_become_null_json(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "signals.jsonl"
            record = build_decision_record(pair="XBTUSD", timeframe="4h",
                candle_timestamp=1792584000, strategy_version="v1",
                status="WAIT", evidence={"score": float("nan")})
            append_decision(path, record)
            raw = path.read_text(encoding="utf-8")
            self.assertNotIn("NaN", raw)
            self.assertIsNone(json.loads(raw)["evidence"]["score"])


if __name__ == "__main__":
    unittest.main()
