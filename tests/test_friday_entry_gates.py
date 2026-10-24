import unittest

from research.friday_entry_gates import evaluate_entry_gate


def thesis(direction="bullish", daily_break_direction="long"):
    return {
        "thesis_direction": direction,
        "daily_directional_breaks": [{"direction": daily_break_direction}],
    }


def plan(eligible=True, blockers=None):
    return {
        "eligible": eligible,
        "blockers": blockers or [],
        "stop": 95.0 if eligible else None,
        "target": 115.0 if eligible else None,
        "reward_risk": 2.0 if eligible else None,
    }


def event(**overrides):
    result = {
        "event": "BREAK_CLOSE_PROBE",
        "signal_time": "2026-10-24T12:00:00+00:00",
        "direction": "long",
        "line_kind": "resistance",
        "line_price_at_break": 100.0,
        "anchor_start_time": "2026-10-20T00:00:00+00:00",
        "anchor_end_time": "2026-10-22T00:00:00+00:00",
        "outcome": "pending",
        "retest_confirmed": False,
    }
    result.update(overrides)
    return result


class FridayEntryGateTests(unittest.TestCase):
    def test_early_entry1_can_pass_before_retest(self):
        result = evaluate_entry_gate(
            event(), entry_type="ENTRY_1", thesis=thesis(), trade_plan=plan()
        )
        self.assertTrue(result["eligible"], result["blockers"])
        self.assertEqual(result["state"], "PASS")
        self.assertFalse(any(x["gate"] == "retest_held" for x in result["checks"]))

    def test_entry1_blocked_when_daily_thesis_does_not_match(self):
        result = evaluate_entry_gate(
            event(), entry_type="ENTRY_1",
            thesis=thesis("bearish", "short"), trade_plan=plan()
        )
        self.assertFalse(result["eligible"])
        self.assertIn("directional_daily_thesis", [x["gate"] for x in result["checks"] if not x["passed"]])

    def test_entry1_blocked_when_daily_trendline_break_missing(self):
        result = evaluate_entry_gate(
            event(), entry_type="ENTRY_1",
            thesis={"thesis_direction": "bullish", "daily_directional_breaks": []},
            trade_plan=plan()
        )
        self.assertFalse(result["eligible"])
        self.assertIn("confirmed_daily_trendline_break", [x["gate"] for x in result["checks"] if not x["passed"]])

    def test_shared_risk_reward_gate_blocks_both_entries(self):
        result = evaluate_entry_gate(
            event(), entry_type="ENTRY_1", thesis=thesis(),
            trade_plan=plan(False, ["reward_risk_below_minimum"])
        )
        self.assertFalse(result["eligible"])
        self.assertTrue(any("reward_risk_below_minimum" in x["reason"] for x in result["checks"]))

    def test_entry2_requires_held_retest_and_later_reaction_break(self):
        candidate = event(
            event_add="CONTINUATION_ADD_CONFIRMED",
            retest_confirmed=True,
            retest_confirmed_time="2026-10-24T16:00:00+00:00",
            continuation_add_time="2026-10-24T20:00:00+00:00",
            continuation_trigger=102.0,
            continuation_add_close=103.0,
        )
        result = evaluate_entry_gate(
            candidate, entry_type="ENTRY_2", thesis=thesis(), trade_plan=plan()
        )
        self.assertTrue(result["eligible"], result["blockers"])

    def test_entry2_blocked_when_continuation_did_not_break_reaction_extreme(self):
        candidate = event(
            event_add="CONTINUATION_ADD_CONFIRMED",
            retest_confirmed=True,
            retest_confirmed_time="2026-10-24T16:00:00+00:00",
            continuation_add_time="2026-10-24T20:00:00+00:00",
            continuation_trigger=102.0,
            continuation_add_close=101.0,
        )
        result = evaluate_entry_gate(
            candidate, entry_type="ENTRY_2", thesis=thesis(), trade_plan=plan()
        )
        self.assertFalse(result["eligible"])
        self.assertIn("later_continuation_close", [x["gate"] for x in result["checks"] if not x["passed"]])


if __name__ == "__main__":
    unittest.main()
