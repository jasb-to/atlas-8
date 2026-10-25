"""Closed-candle Stoch RSI reversal candidate scanner for FRIDAY.

This is a research-only archetype. It creates a candidate only on a fresh
cross from an extreme zone, then requires a directional momentum/structure
context and a valid structural trade plan. It never places orders.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from research.friday_trade_plan import build_trade_plan
from research.friday_reversal import reversal_context


def scan_momentum_reversal(
    frame: pd.DataFrame, *, pair: str = "UNKNOWN", thesis_direction: str | None = None
) -> dict:
    """Evaluate the latest completed candle without looking into the future."""
    result = {
        "strategy": "stoch-rsi-structural-reversal-v1",
        "pair": pair.upper(),
        "state": "NO_SETUP",
        "direction": None,
        "signal_time": None,
        "entry": None,
        "stop": None,
        "target": None,
        "reward_risk": None,
        "eligible": False,
        "blockers": [],
        "research_only": True,
    }
    needed = {"timestamp", "open", "high", "low", "close"}
    if frame is None or frame.empty or not needed.issubset(frame.columns):
        result["state"] = "BLOCKED"
        result["blockers"] = ["missing_ohlc_data"]
        return result
    data = frame.sort_values("timestamp").reset_index(drop=True).copy()
    close = data.close.astype(float)
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    rsi = 100 - 100 / (1 + gain / loss.replace(0, np.nan))
    rsi_low = rsi.rolling(14, min_periods=14).min()
    rsi_high = rsi.rolling(14, min_periods=14).max()
    raw = 100 * (rsi - rsi_low) / (rsi_high - rsi_low).replace(0, np.nan)
    k = raw.rolling(3, min_periods=3).mean()
    d = k.rolling(3, min_periods=3).mean()
    if len(data) < 60 or not np.isfinite([k.iloc[-2], k.iloc[-1], d.iloc[-2], d.iloc[-1]]).all():
        result["state"] = "BLOCKED"
        result["blockers"] = ["insufficient_indicator_history"]
        return result

    cross_down = k.iloc[-2] >= d.iloc[-2] and k.iloc[-1] < d.iloc[-1] and k.iloc[-2] >= 75
    cross_up = k.iloc[-2] <= d.iloc[-2] and k.iloc[-1] > d.iloc[-1] and k.iloc[-2] <= 25
    if not (cross_down or cross_up):
        result["blockers"] = ["no_fresh_extreme_zone_stoch_rsi_cross"]
        result["stoch_rsi_k"] = round(float(k.iloc[-1]), 4)
        result["stoch_rsi_d"] = round(float(d.iloc[-1]), 4)
        return result

    direction = "short" if cross_down else "long"
    normalized_thesis = str(thesis_direction or "").strip().lower()
    expected_thesis = {"bearish": "short", "short": "short", "bullish": "long", "long": "long"}.get(normalized_thesis)
    if expected_thesis is not None and direction != expected_thesis:
        result.update({
            "direction": direction,
            "signal_time": pd.Timestamp(data.timestamp.iloc[-1]).isoformat(),
            "blockers": ["countertrend_reversal_blocked_by_daily_thesis"],
            "daily_thesis_direction": normalized_thesis,
        })
        return result

    ema5 = close.ewm(span=5, adjust=False).mean()
    ema8 = close.ewm(span=8, adjust=False).mean()
    ema13 = close.ewm(span=13, adjust=False).mean()
    ema21 = close.ewm(span=21, adjust=False).mean()
    close_now = float(close.iloc[-1])
    ema_aligned = (
        close_now < min(float(ema5.iloc[-1]), float(ema8.iloc[-1]), float(ema13.iloc[-1]), float(ema21.iloc[-1]))
        if direction == "short"
        else close_now > max(float(ema5.iloc[-1]), float(ema8.iloc[-1]), float(ema13.iloc[-1]), float(ema21.iloc[-1]))
    )
    macd = close.ewm(span=12, adjust=False).mean() - close.ewm(span=26, adjust=False).mean()
    macd_signal = macd.ewm(span=9, adjust=False).mean()
    hist = macd - macd_signal
    # Reversal context is a confluence gate, not a standalone oscillator trade.
    context_ok = (
        ema_aligned and (
            (direction == "short" and hist.iloc[-1] < hist.iloc[-2])
            or (direction == "long" and hist.iloc[-1] > hist.iloc[-2])
        )
    )
    result.update({
        "direction": direction,
        "signal_time": pd.Timestamp(data.timestamp.iloc[-1]).isoformat(),
        "entry": round(float(close.iloc[-1]), 10),
        "stoch_rsi_k": round(float(k.iloc[-1]), 4),
        "stoch_rsi_d": round(float(d.iloc[-1]), 4),
        "ema5": round(float(ema5.iloc[-1]), 10),
        "ema8": round(float(ema8.iloc[-1]), 10),
        "ema13": round(float(ema13.iloc[-1]), 10),
        "ema21": round(float(ema21.iloc[-1]), 10),
        "price_aligned_with_all_emas": bool(ema_aligned),
        "daily_thesis_direction": normalized_thesis or None,
        "macd_hist": round(float(hist.iloc[-1]), 10),
    })
    if not context_ok:
        result["state"] = "BLOCKED"
        result["blockers"] = ["momentum_structure_not_confirmed"]
        return result

    exhaustion = reversal_context(data)
    aligned_score = int(exhaustion.get("long_reversal_score" if direction == "long" else "short_reversal_score", 0))
    opposing_score = int(exhaustion.get("short_reversal_score" if direction == "long" else "long_reversal_score", 0))
    result["exhaustion_filter"] = {
        "state": exhaustion.get("state"),
        "aligned_score": aligned_score,
        "opposing_score": opposing_score,
        "evidence": exhaustion.get("evidence"),
        "passed": aligned_score >= 2 and aligned_score > opposing_score,
    }
    if not result["exhaustion_filter"]["passed"]:
        result["state"] = "BLOCKED"
        result["blockers"] = ["exhaustion_filter_not_confirmed"]
        return result

    plan = build_trade_plan(data, direction, entry=float(close.iloc[-1]), min_reward_risk=1.5)
    result["trade_plan"] = plan
    result["stop"] = plan.get("stop")
    result["target"] = plan.get("target")
    result["reward_risk"] = plan.get("reward_risk")
    result["eligible"] = bool(plan.get("eligible"))
    result["blockers"] = list(plan.get("blockers", []))
    result["state"] = "ENTRY_CANDIDATE" if result["eligible"] else "BLOCKED"
    return result
