import unittest

from research.friday_learning import build_learning_report


class FridayLearningReportTests(unittest.TestCase):
    def test_reports_no_trade_and_fee_aware_replay_metrics(self):
        decisions = [
            {"status": "NO_TRADE", "blockers": ["trend not confirmed"], "model": {"status": "not_configured"}},
            {"status": "ENTRY_1_CANDIDATE", "blockers": [], "model": {"status": "ok"}},
        ]
        trades = [
            {"direction": "long", "net_equity_return_pct": 1.0, "exit_reason": "target", "target_type": "prior_structure", "strategy_version": "v1"},
            {"direction": "short", "net_equity_return_pct": -0.5, "exit_reason": "stop", "target_type": "fib_1.272_extension", "strategy_version": "v1"},
        ]
        report = build_learning_report(decisions, trades)
        self.assertEqual(report["decision_count"], 2)
        self.assertEqual(report["no_trade_count"], 1)
        self.assertEqual(report["replay"]["trades"], 2)
        self.assertEqual(report["replay"]["win_rate_pct"], 50.0)
        self.assertEqual(report["replay"]["exit_reasons"], {"stop": 1, "target": 1})
        self.assertFalse(report["learning_policy"]["automatic_parameter_changes"])

    def test_empty_report_is_valid_json_data(self):
        report = build_learning_report([], [])
        self.assertEqual(report["replay"]["trades"], 0)
        self.assertIsNone(report["replay"]["win_rate_pct"])


if __name__ == "__main__":
    unittest.main()
