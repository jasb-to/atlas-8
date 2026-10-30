from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from research.weekly_thesis import evaluate_sheldon_thesis


def candles(count: int, days: int) -> pd.DataFrame:
    timestamps = pd.date_range("2020-01-05", periods=count, freq=f"{days}D", tz="UTC")
    # Deterministic oscillation with a mild trend keeps RSI/Stoch RSI and MACD valid.
    close = 100 + np.arange(count) * 0.15 + np.sin(np.arange(count) / 3) * 4
    return pd.DataFrame({
        "timestamp": timestamps,
        "open": close,
        "high": close + 1,
        "low": close - 1,
        "close": close,
        "volume": 1000.0,
    })


@pytest.mark.parametrize(
    ("weeks", "expected_period"),
    [(180, 150), (120, 100), (60, 50)],
)
def test_adaptive_weekly_ema_selects_longest_supported_period(weeks, expected_period):
    weekly = candles(weeks, 7)
    daily = candles(90, 1)
    as_of = pd.Timestamp("2025-01-01T00:00:00Z")

    result = evaluate_sheldon_thesis(
        "TESTUSDT", weekly_candles=weekly, daily_candles=daily, as_of=as_of
    )

    assert result["weekly_ema_period"] == expected_period
    assert result["status"] in {"active", "veto", "inactive"}


def test_fewer_than_50_completed_weeks_is_insufficient():
    weekly = candles(30, 7)
    daily = candles(90, 1)
    as_of = pd.Timestamp("2025-01-01T00:00:00Z")

    result = evaluate_sheldon_thesis(
        "TESTUSDT", weekly_candles=weekly, daily_candles=daily, as_of=as_of
    )

    assert result["status"] == "insufficient_data"
    assert result["weekly_ema_period"] is None
