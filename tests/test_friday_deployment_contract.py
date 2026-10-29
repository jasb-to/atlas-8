"""Guard FRIDAY's scan universe across the API, workflow and dashboard."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_ROUTE_PAIRS = [
    "XBTUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT", "AVAXUSDT",
    "BNBUSDT", "VIRTUALUSDT", "HYPEUSDT", "DOGEUSDT", "LINKUSDT",
    "ZECUSDT", "NEARUSDT", "SUIUSDT", "UNIUSDT",
]


class FridayDeploymentContractTests(unittest.TestCase):
    def test_scan_route_workflow_and_dashboard_share_the_approved_market_universe(self):
        route = (ROOT / "dashboard/app/api/friday/scan/route.ts").read_text(encoding="utf-8")
        workflow = (ROOT / ".github/workflows/friday-monitor.yml").read_text(encoding="utf-8")
        page = (ROOT / "dashboard/app/page.tsx").read_text(encoding="utf-8")

        route_block = route.split("const FRIDAY_SCAN_PAIRS = [", 1)[1].split("] as const;", 1)[0]
        route_pairs = re.findall(r'"([A-Z0-9]+)"', route_block)
        default_line = next(line.strip() for line in workflow.splitlines() if line.strip().startswith("default: XBTUSDT,"))
        workflow_pairs = default_line.removeprefix("default: ").split(",")
        dashboard_block = page.split("const THESIS_UNIVERSE = [", 1)[1].split("];", 1)[0]
        dashboard_pairs = re.findall(r'"([A-Z0-9]+)"', dashboard_block)
        route_display = ["BTCUSDT" if pair == "XBTUSDT" else pair for pair in route_pairs]

        self.assertEqual(route_pairs, EXPECTED_ROUTE_PAIRS)
        self.assertEqual(workflow_pairs, EXPECTED_ROUTE_PAIRS)
        self.assertEqual(route_display, dashboard_pairs)


if __name__ == "__main__":
    unittest.main()
