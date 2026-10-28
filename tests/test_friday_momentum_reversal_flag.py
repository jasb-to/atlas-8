"""Feature-flag contract tests for the standalone momentum reversal scanner."""
from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import patch

from research.strategy_preview import momentum_reversal_enabled


class MomentumReversalFlagTests(unittest.TestCase):
    def test_defaults_to_disabled(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(momentum_reversal_enabled())

    def test_explicit_false_disables(self):
        with patch.dict(os.environ, {"FRIDAY_MOMENTUM_REVERSAL_ENABLED": "false"}, clear=True):
            self.assertFalse(momentum_reversal_enabled())

    def test_truthy_values_are_explicit_opt_in(self):
        for value in ("1", "true", "yes", "on", "TRUE"):
            with self.subTest(value=value), patch.dict(
                os.environ, {"FRIDAY_MOMENTUM_REVERSAL_ENABLED": value}, clear=True
            ):
                self.assertTrue(momentum_reversal_enabled())

    def test_monitor_gates_candidate_persistence_and_alerts(self):
        source = (Path(__file__).resolve().parents[1] / "research" / "friday_monitor.py").read_text()
        self.assertIn(
            'reversal = result.get("momentum_reversal", {}) if momentum_reversal_enabled() else {}',
            source,
        )
        self.assertIn('"type": "MOMENTUM_REVERSAL"', source)

    def test_dashboard_uses_snapshot_record_type_and_collapses_inactive_markets(self):
        source = (
            Path(__file__).resolve().parents[1]
            / "dashboard" / "app" / "page.tsx"
        ).read_text()
        self.assertIn('record?.record_type ?? "MISSING"', source)
        self.assertIn('weeklyCards.filter(card => card.active).map(card =>', source)
        self.assertIn('<details className="debug-details inactive-theses">', source)
        self.assertIn('weeklyCards.filter(card => !card.active).map(card =>', source)


if __name__ == "__main__":
    unittest.main()
