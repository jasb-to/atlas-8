"""Guard FRIDAY's source and dashboard deployment contract."""
from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class FridayDeploymentContractTests(unittest.TestCase):
    def test_only_kraken_is_referenced_as_a_market_data_venue_in_source(self):
        forbidden = "".join(("O", "K", "X")).lower()
        extensions = {".py", ".ts", ".tsx", ".js", ".mjs", ".cjs", ".yml", ".yaml", ".md", ".json"}
        excluded_parts = {".git", "node_modules", ".next", "__pycache__", "results"}
        excluded_prefixes = ("testing/friday/data/", "testing/friday/runs/")
        hits = []
        for path in ROOT.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in extensions:
                continue
            relative = path.relative_to(ROOT).as_posix()
            if any(part in excluded_parts for part in path.relative_to(ROOT).parts):
                continue
            if relative.startswith(excluded_prefixes):
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            if forbidden in text.lower():
                hits.append(relative)
        self.assertEqual(hits, [], f"Forbidden alternate venue reference found in: {hits}")

    def test_scan_route_workflow_and_dashboard_share_the_sixteen_market_universe(self):
        route = (ROOT / "dashboard/app/api/friday/scan/route.ts").read_text(encoding="utf-8")
        workflow = (ROOT / ".github/workflows/friday-monitor.yml").read_text(encoding="utf-8")
        page = (ROOT / "dashboard/app/page.tsx").read_text(encoding="utf-8")

        route_match = re.search(r"const FRIDAY_SCAN_PAIRS = \\[(.*?)\\] as const;", route, re.S)
        self.assertIsNotNone(route_match, "Dashboard scan route must define one canonical pair list")
        route_pairs = re.findall(r'"([A-Z0-9]+)"', route_match.group(1))

        default_match = re.search(r"^\\s*default: (XBTUSDT(?:,[A-Z0-9]+)+)\\s*$", workflow, re.M)
        self.assertIsNotNone(default_match, "Workflow must define the complete default market list")
        workflow_pairs = default_match.group(1).split(",")

        dashboard_match = re.search(r"const THESIS_UNIVERSE = \\[(.*?)\\];", page, re.S)
        self.assertIsNotNone(dashboard_match, "Dashboard must define all fixed thesis slots")
        dashboard_pairs = re.findall(r'"([A-Z0-9]+)"', dashboard_match.group(1))
        route_display = ["BTCUSDT" if pair == "XBTUSDT" else pair for pair in route_pairs]

        self.assertEqual(len(route_pairs), 16)
        self.assertIn("UNIUSDT", route_pairs)
        self.assertEqual(route_pairs, workflow_pairs)
        self.assertEqual(route_display, dashboard_pairs)


if __name__ == "__main__":
    unittest.main()
