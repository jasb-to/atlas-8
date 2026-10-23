import unittest
import pandas as pd

from research.friday_paper_trades import register_paper_trade, update_paper_trades


class FakeStore:
    def __init__(self):
        self.rows = []

    def paper_trades(self):
        return [dict(row) for row in self.rows]

    def upsert_paper_trade(self, record):
        self.rows = [row for row in self.rows if row["trade_id"] != record["trade_id"]]
        self.rows.insert(0, dict(record))


class PaperTradeLifecycleTests(unittest.TestCase):
    def test_register_is_idempotent(self):
        store = FakeStore()
        args = dict(pair="HYPEUSD", strategy="test", signal_time="2026-10-22T00:00:00Z",
                    direction="short", entry=81.0, stop=84.0, target=75.0, reward_risk=2.0)
        first = register_paper_trade(store, **args)
        second = register_paper_trade(store, **args)
        self.assertEqual(first["trade_id"], second["trade_id"])
        self.assertEqual(len(store.rows), 1)

    def test_stop_loss_closes_trade_and_records_minus_one_r(self):
        store = FakeStore()
        trade = register_paper_trade(store, pair="HYPEUSD", strategy="test",
            signal_time="2026-10-22T00:00:00Z", direction="short",
            entry=81.0, stop=84.0, target=75.0, reward_risk=2.0)
        candles = pd.DataFrame([
            {"timestamp":"2026-10-22T04:00:00Z","high":82.0,"low":80.0},
            {"timestamp":"2026-10-22T08:00:00Z","high":84.2,"low":79.0},
        ])
        rows = update_paper_trades(store, pair="HYPEUSD", candles=candles)
        self.assertEqual(rows[0]["status"], "CLOSED")
        self.assertEqual(rows[0]["exit_reason"], "STOP_LOSS")
        self.assertEqual(rows[0]["realized_r_multiple"], -1.0)

    def test_target_closes_trade_and_records_positive_r(self):
        store = FakeStore()
        register_paper_trade(store, pair="HYPEUSD", strategy="test",
            signal_time="2026-10-22T00:00:00Z", direction="short",
            entry=81.0, stop=84.0, target=75.0, reward_risk=2.0)
        candles = pd.DataFrame([
            {"timestamp":"2026-10-22T04:00:00Z","high":80.0,"low":76.0},
            {"timestamp":"2026-10-22T08:00:00Z","high":80.0,"low":74.5},
        ])
        rows = update_paper_trades(store, pair="HYPEUSD", candles=candles)
        self.assertEqual(rows[0]["status"], "CLOSED")
        self.assertEqual(rows[0]["exit_reason"], "TAKE_PROFIT")
        self.assertEqual(rows[0]["realized_r_multiple"], 2.0)

    def test_same_candle_stop_and_target_uses_conservative_stop(self):
        store = FakeStore()
        register_paper_trade(store, pair="HYPEUSD", strategy="test",
            signal_time="2026-10-22T00:00:00Z", direction="short",
            entry=81.0, stop=84.0, target=75.0, reward_risk=2.0)
        rows = update_paper_trades(store, pair="HYPEUSD", candles=pd.DataFrame([
            {"timestamp":"2026-10-22T04:00:00Z","high":84.2,"low":74.5},
        ]))
        self.assertEqual(rows[0]["exit_reason"], "STOP_LOSS")
        self.assertEqual(rows[0]["realized_r_multiple"], -1.0)


if __name__ == "__main__":
    unittest.main()
