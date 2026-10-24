"""Causal, research-only trade plan from confirmed swing structure and ATR."""
from __future__ import annotations

import numpy as np
import pandas as pd

from research.trendlines import find_wick_pivots


FIB_RETRACEMENTS = (0.382, 0.5, 0.618, 0.786)
FIB_EXTENSIONS = (1.272, 1.618)


def build_trade_plan(frame: pd.DataFrame, direction: str, entry: float | None = None,
                     min_reward_risk: float = 1.5) -> dict:
    """Build a non-executable plan; never submits or sizes an exchange order.

    Uses only pivots confirmed by the last supplied candle. A plan is blocked
    unless it has a structural stop, a forward target, and adequate reward/risk.
    """
    if direction not in {"long", "short"}:
        raise ValueError("direction must be 'long' or 'short'")
    if frame.empty or not {"high", "low", "close"}.issubset(frame.columns):
        return {"eligible": False, "blockers": ["missing_ohlc_data"]}
    data = frame.reset_index(drop=True).copy()
    close = data.close.astype(float)
    entry_price = float(close.iloc[-1] if entry is None else entry)
    if not np.isfinite(entry_price) or entry_price <= 0:
        return {"eligible": False, "blockers": ["invalid_entry_price"]}
    prev = close.shift(1)
    tr = pd.concat([(data.high-data.low).abs(), (data.high-prev).abs(),
                    (data.low-prev).abs()], axis=1).max(axis=1)
    atr_series = tr.ewm(alpha=1/14, adjust=False, min_periods=14).mean()
    atr = float(atr_series.iloc[-1]) if len(atr_series) and np.isfinite(atr_series.iloc[-1]) else float("nan")
    if not np.isfinite(atr) or atr <= 0:
        return {"eligible": False, "entry": entry_price, "blockers": ["insufficient_atr_history"]}

    pivots = find_wick_pivots(data, 3, 3)
    last_i = len(data) - 1
    confirmed = [p for p in pivots if p.confirmed_at <= last_i]
    highs = [p for p in confirmed if p.kind == "resistance"]
    lows = [p for p in confirmed if p.kind == "support"]
    latest_high = max(highs, key=lambda p: p.index) if highs else None
    latest_low = max(lows, key=lambda p: p.index) if lows else None
    blockers: list[str] = []
    stop = target = None
    swing_range = None
    fib = {}
    if latest_high and latest_low:
        swing_range = abs(float(latest_high.price) - float(latest_low.price))
        if swing_range > 0:
            lo, hi = sorted((float(latest_low.price), float(latest_high.price)))
            if direction == "long":
                fib = {"retracements": {str(level): hi - swing_range*level for level in FIB_RETRACEMENTS},
                       "extensions": {str(level): lo + swing_range*level for level in FIB_EXTENSIONS}}
            else:
                fib = {"retracements": {str(level): lo + swing_range*level for level in FIB_RETRACEMENTS},
                       "extensions": {str(level): hi - swing_range*level for level in FIB_EXTENSIONS}}
        if direction == "long" and latest_low.price < entry_price:
            stop = float(latest_low.price) - 0.15*atr
        elif direction == "short" and latest_high.price > entry_price:
            stop = float(latest_high.price) + 0.15*atr

    if stop is None:
        blockers.append("no_valid_confirmed_structural_stop")

    # Preserve the true structural invalidation for context, but cap the paper
    # risk stop at 1% from entry to match the requested 20x-risk specification.
    # This does not imply the structure itself is invalidated at the capped stop;
    # it deliberately means the paper position may stop before structural failure.
    structural_stop = float(stop) if stop is not None else None
    max_stop_distance = entry_price * 0.01
    if stop is not None:
        if direction == "long":
            stop = max(float(stop), entry_price - max_stop_distance)
        else:
            stop = min(float(stop), entry_price + max_stop_distance)

    risk = abs(entry_price-stop) if stop is not None else None
    stop_distance_pct = (risk / entry_price * 100.0) if risk is not None and entry_price > 0 else None
    if risk is not None and risk <= 0:
        blockers.append("non_positive_risk")
        risk = None

    # Prefer the nearest confirmed opposing structure only if it clears the
    # required reward/risk. A tiny nearby pivot must not hide a usable extension.
    if direction == "long":
        targets = sorted(float(p.price) for p in highs if p.price > entry_price)
        fallback = fib.get("extensions", {}).get("1.272")
        candidates = [(price, "confirmed_structure") for price in targets]
        if fallback is not None and fallback > entry_price:
            candidates.append((float(fallback), "fib_1.272_fallback"))
    else:
        targets = sorted((float(p.price) for p in lows if p.price < entry_price), reverse=True)
        fallback = fib.get("extensions", {}).get("1.272")
        candidates = [(price, "confirmed_structure") for price in targets]
        if fallback is not None and fallback < entry_price:
            candidates.append((float(fallback), "fib_1.272_fallback"))

    target, target_source, reward, rr = None, None, None, None
    for candidate_price, candidate_source in candidates:
        candidate_reward = (candidate_price-entry_price if direction == "long"
                            else entry_price-candidate_price)
        candidate_rr = candidate_reward/risk if risk is not None and risk > 0 else None
        if candidate_reward > 0 and candidate_rr is not None and candidate_rr >= min_reward_risk:
            target, target_source = candidate_price, candidate_source
            reward, rr = candidate_reward, candidate_rr
            break
    if target is None:
        if candidates:
            target, target_source = candidates[0]
            reward = (target-entry_price if direction == "long" else entry_price-target)
            rr = reward/risk if reward is not None and risk is not None and risk > 0 else None
        blockers.append("no_target_meets_minimum_reward_risk")
    if target is None:
        blockers.append("no_forward_structural_or_fibonacci_target")
    if reward is not None and reward <= 0:
        blockers.append("target_not_in_trade_direction")
    if target is not None and (rr is None or rr < min_reward_risk):
        blockers.append("reward_risk_below_minimum")

    return {
        "eligible": not blockers, "direction": direction, "entry": round(entry_price, 10),
        "atr14": round(atr, 10), "stop": round(stop, 10) if stop is not None else None,
        "structural_stop": round(structural_stop, 10) if structural_stop is not None else None,
        "stop_distance_pct": round(stop_distance_pct, 4) if stop_distance_pct is not None else None,
        "stop_policy": "risk stop capped at 1.0% from entry; structural_stop retained separately for context",
        "target": round(target, 10) if target is not None else None,
        "target_source": target_source, "risk_per_unit": round(risk, 10) if risk is not None else None,
        "reward_per_unit": round(reward, 10) if reward is not None else None,
        "reward_risk": round(rr, 4) if rr is not None else None,
        "min_reward_risk": min_reward_risk, "fib": fib,
        "confirmed_swing_high": {"index": latest_high.index, "price": float(latest_high.price)} if latest_high else None,
        "confirmed_swing_low": {"index": latest_low.index, "price": float(latest_low.price)} if latest_low else None,
        "blockers": blockers, "research_only": True,
    }
