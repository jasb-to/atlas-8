"""Standalone two-year daily regime/veto flip-flop replay for SOL and ETH."""
from __future__ import annotations

import pandas as pd

from audits._historical_data import fetch_history
from research.weekly_thesis import calculate_stoch_rsi

START = "2020-01-01"
END = "2026-10-26"
REPLAY_START = pd.Timestamp("2024-10-24T00:00:00Z")
REPLAY_END = pd.Timestamp("2026-10-25T00:00:00Z")


def add_indicators(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True).copy()
    close = out["close"].astype(float)
    out["sma200"] = close.rolling(200, min_periods=200).mean()
    out["k"], out["d"] = calculate_stoch_rsi(close)
    return out


def weekly_direction(weekly: pd.DataFrame, as_of: pd.Timestamp) -> str | None:
    closed = weekly.loc[weekly["timestamp"] + pd.Timedelta(days=7) <= as_of]
    if len(closed) < 201:
        return None
    row = closed.iloc[-1]
    prev = closed.iloc[-2]
    if pd.isna(row.sma200) or pd.isna(row.k) or pd.isna(row.d):
        return None
    bull_cross = pd.notna(prev.k) and pd.notna(prev.d) and prev.k <= prev.d and row.k > row.d and row.k < 20
    bear_cross = pd.notna(prev.k) and pd.notna(prev.d) and prev.k >= prev.d and row.k < row.d and row.k > 80
    if row.close > row.sma200 and (row.k > row.d or bull_cross):
        return "long"
    if row.close < row.sma200 and (row.k < row.d or bear_cross):
        return "short"
    return None


def daily_state(daily: pd.DataFrame, index: int, direction: str | None) -> str:
    if direction is None:
        return "inactive"
    row = daily.iloc[index]
    if pd.isna(row.sma200) or pd.isna(row.k) or pd.isna(row.d):
        return "insufficient_data"
    start = max(1, index - 2)
    cross_up = cross_down = False
    for j in range(start, index + 1):
        prev, cur = daily.iloc[j - 1], daily.iloc[j]
        if pd.isna(prev.k) or pd.isna(prev.d) or pd.isna(cur.k) or pd.isna(cur.d):
            continue
        cross_up = cross_up or (prev.k <= prev.d and cur.k > cur.d)
        cross_down = cross_down or (prev.k >= prev.d and cur.k < cur.d)
    aligned = (
        row.close > row.sma200 and (row.k > row.d or cross_up)
        if direction == "long"
        else row.close < row.sma200 and (row.k < row.d or cross_down)
    )
    return "active" if aligned else "veto"


def main():
    summary = []
    transition_rows = []
    for pair in ("SOLUSDT", "ETHUSDT"):
        weekly = add_indicators(fetch_history(pair, "1W", START, END))
        daily = add_indicators(fetch_history(pair, "1D", START, END))
        replay = daily.loc[
            (daily["timestamp"] >= REPLAY_START) & (daily["timestamp"] < REPLAY_END)
        ].reset_index(drop=True)
        if len(replay) < 600:
            raise RuntimeError(f"{pair}: only {len(replay)} daily rows; expected roughly two years.")
        states = []
        for _, row in replay.iterrows():
            as_of = row.timestamp + pd.Timedelta(days=1)
            # Evaluate weekly regime only from weekly candles fully closed at this daily close.
            direction = weekly_direction(weekly, as_of)
            original_index = int(daily.index[daily.timestamp == row.timestamp][0])
            state = daily_state(daily, original_index, direction)
            states.append((as_of, state, direction))
        transitions = []
        for i in range(1, len(states)):
            if states[i][1] != states[i - 1][1]:
                transitions.append((states[i][0], states[i - 1][1], states[i][1], states[i][2]))
        rapid = 0
        for i in range(1, len(transitions)):
            if transitions[i][0] - transitions[i - 1][0] <= pd.Timedelta(days=5):
                # A genuine flip-flop returns to the prior state, not merely two unrelated transitions.
                if transitions[i][2] == transitions[i - 1][1]:
                    rapid += 1
        summary.append((pair, len(replay), len(transitions), rapid))
        for when, old, new, direction in transitions:
            transition_rows.append((pair, when.isoformat(), old, new, direction or "none"))
    print("| Pair | Daily candles | State transitions | Return flips within 5 days |")
    print("|---|---:|---:|---:|")
    for pair, candles, transitions, rapid in summary:
        print(f"| {pair} | {candles} | {transitions} | {rapid} |")
    print("\n| Pair | Completed daily close (UTC) | From | To | Weekly direction |")
    print("|---|---|---|---|---|")
    for row in transition_rows:
        print(f"| {row[0]} | {row[1]} | {row[2]} | {row[3]} | {row[4]} |")


if __name__ == "__main__":
    main()
