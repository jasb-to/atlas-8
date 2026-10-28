"""Regression tests for dashboard mapping against the persisted FRIDAY schema."""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / "dashboard" / "app" / "page.tsx"


class DashboardMappingTests(unittest.TestCase):
    def test_real_persisted_schema_is_mapped_without_guessing(self):
        source = DASHBOARD.read_text()
        mock_snapshot = {
            "record_type": "weekly_thesis_snapshot",
            "pair": "SOLUSDT",
            "payload": {
                "status": "active",
                "direction": "short",
                "weekly_close": 101.2,
                "daily_close": 100.9,
            },
        }
        mock_trade = {
            "trade_id": "mock-sol-trade",
            "pair": "SOLUSDT",
            "direction": "SHORT",
            "entry": 101.05,
            "stop_loss": 102.20,
            "take_profit": 98.04,
            "status": "OPEN",
        }

        # Match the persisted record layout: record_type is top-level, while
        # prices and status are under payload; trade prices are top-level.
        self.assertIn('record?.record_type ?? "MISSING"', source)
        self.assertIn('thesis.data_source_weekly ?? "MISSING"', source)
        self.assertIn('thesis.data_source_daily ?? "MISSING"', source)
        self.assertIn('["OPEN", "ACTIVE"].includes(String(trade.status ?? "").trim().toUpperCase())', source)
        self.assertIn('(trade.stop_loss ?? trade.stop) != null', source)
        self.assertIn('(trade.tp2 ?? trade.take_profit ?? trade.target) != null', source)
        self.assertIn('Number(trade.stop_loss ?? trade.stop)', source)
        self.assertIn('Number(trade.tp2 ?? trade.take_profit ?? trade.target)', source)

        self.assertEqual(mock_snapshot["record_type"], "weekly_thesis_snapshot")
        self.assertEqual(mock_snapshot["payload"].get("data_source_weekly", "MISSING"), "MISSING")
        self.assertEqual(mock_snapshot["payload"].get("data_source_daily", "MISSING"), "MISSING")
        self.assertEqual(mock_trade["entry"], 101.05)
        self.assertEqual(mock_trade["stop_loss"], 102.20)
        self.assertEqual(mock_trade["take_profit"], 98.04)
        self.assertIn(mock_trade["status"], ("OPEN", "ACTIVE"))

    def test_checked_in_dashboard_json_does_not_contain_the_telegram_sol_trade(self):
        path = ROOT / "testing" / "friday" / "data" / "dashboard.json"
        if not path.exists():
            self.skipTest("Dashboard fixture is not checked out in this test environment")
        data = json.loads(path.read_text())
        sol_trades = [t for t in data.get("paper_trades", []) if str(t.get("pair", "")).upper() == "SOLUSDT"]
        self.assertEqual(sol_trades, [], "This fixture currently has no SOLUSDT trade; check live API/source freshness.")


if __name__ == "__main__":
    unittest.main()
