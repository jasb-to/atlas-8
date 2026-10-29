import unittest

import pandas as pd

from research.friday_monitor import (
    _btc_blocks_short,
    _daily_weekly_conflict,
    _entry_filters_pass,
)
from research.strategy_preview import break_crossed


class FlatLine:
    kind = "resistance"

    def __init__(self, kind="resistance", value=100.0):
        self.kind = kind
        self.value = value

    def value_at(self, index):
        return self.value


class FridayAlignmentRegressionTests(unittest.TestCase):
    def test_short_waits_when_daily_stoch_momentum_is_bullish(self):
        thesis = {"direction": "short", "daily_stoch_k": 62.0, "daily_stoch_d": 48.0}
        self.assertTrue(_daily_weekly_conflict(thesis))

    def test_short_suppressed_during_bullish_btc_weekly_impulse(self):
        btc = {"wave_state": "impulse", "direction": "long"}
        self.assertTrue(_btc_blocks_short(btc, "ETHUSDT", "short"))
        self.assertFalse(_btc_blocks_short(btc, "BTCUSDT", "short"))

    def test_short_allowed_when_daily_weekly_agree_and_btc_is_bearish(self):
        thesis = {"direction": "short", "daily_stoch_k": 25.0, "daily_stoch_d": 40.0}
        btc = {"wave_state": "impulse", "direction": "short"}
        event = {"direction": "short", "daily_bias": "bearish"}
        self.assertFalse(_daily_weekly_conflict(thesis))
        self.assertFalse(_btc_blocks_short(btc, "ETHUSDT", "short"))
        passed, blockers = _entry_filters_pass(event, {"long_reversal_score": 0, "short_reversal_score": 0}, "short")
        self.assertTrue(passed, blockers)

    def test_break_requires_two_consecutive_closes_beyond_trendline(self):
        one_close = pd.DataFrame({"close": [100.0, 101.0]})
        two_closes = pd.DataFrame({"close": [101.0, 102.0]})
        line = FlatLine("resistance")
        self.assertFalse(break_crossed(one_close, line, 1))
        self.assertTrue(break_crossed(two_closes, line, 1))

    def test_short_break_requires_two_consecutive_closes_below_trendline(self):
        one_close = pd.DataFrame({"close": [100.0, 99.0]})
        two_closes = pd.DataFrame({"close": [99.0, 98.0]})
        line = FlatLine("support")
        self.assertFalse(break_crossed(one_close, line, 1))
        self.assertTrue(break_crossed(two_closes, line, 1))


if __name__ == "__main__":
    unittest.main()
