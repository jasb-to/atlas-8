"""Persistent paper-trade lifecycle tracker for FRIDAY research candidates.

Tracks only simulated plans. Stop wins ties when OHLC data touches stop and target
inside the same candle, a conservative assumption when candle ordering is unknown.
"""
from __future__ import annotations

from typing import Any, Mapping
import hashlib
import pandas as pd

from research.friday_persistence import utc_now


def _key(pair: str, strategy: str, signal_time: str, direction: str) -> str:
    raw = f"{pair.upper()}|{strategy}|{signal_time}|{direction.lower()}"
    return hashlib.sha256(raw.encode()).hexdigest()


def register_paper_trade(store, *, pair: str, strategy: str, signal_time: str,
                         direction: str, entry: float, stop: float, target: float,
                         reward_risk: float, timeframe: str = "4H",
                         ai_review: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Idempotently register a risk-defined paper trade."""
    if direction.lower() not in {"long", "short"}:
        raise ValueError("direction must be long or short")
    entry, stop, target = float(entry), float(stop), float(target)
    if min(entry, stop, target) <= 0:
        raise ValueError("entry, stop and target must be positive")
    if direction.lower() == "long" and not stop < entry < target:
        raise ValueError("long plan must satisfy stop < entry < target")
    if direction.lower() == "short" and not target < entry < stop:
        raise ValueError("short plan must satisfy target < entry < stop")
    trade_id = _key(pair, strategy, signal_time, direction)
    rows = store.paper_trades()
    existing = next((r for r in rows if r.get("trade_id") == trade_id), None)
    if existing:
        return existing
    row = {
        "trade_id": trade_id, "pair": pair.upper(), "strategy": strategy,
        "timeframe": timeframe, "direction": direction.upper(),
        "signal_time": signal_time, "opened_at": utc_now(),
        "entry": entry, "stop_loss": stop, "take_profit": target,
        "reward_risk": float(reward_risk),
        "ai_review_status": (ai_review or {}).get("status", "not_attached"),
        "ai_assessment": ((ai_review or {}).get("response") or {}).get("assessment"),
        "ai_rationale": ((ai_review or {}).get("response") or {}).get("rationale"),
        "status": "OPEN",
        "last_checked_candle": None, "last_checked_at": utc_now(),
        "exit_time": None, "exit_price": None, "exit_reason": None,
        "realized_r_multiple": None, "research_only": True,
    }
    store.upsert_paper_trade(row)
    return row


def update_paper_trades(store, *, pair: str, candles: pd.DataFrame) -> list[dict[str, Any]]:
    """Advance open paper trades using candles strictly after their signal candle."""
    required = {"timestamp", "high", "low"}
    if candles is None or candles.empty or not required.issubset(candles.columns):
        return []
    frame = candles.sort_values("timestamp").reset_index(drop=True).copy()
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
    updated = []
    for trade in store.paper_trades():
        if trade.get("pair") != pair.upper() or trade.get("status") != "OPEN":
            continue
        signal_time = pd.to_datetime(trade["signal_time"], utc=True)
        last_checked = pd.to_datetime(trade["last_checked_candle"], utc=True) if trade.get("last_checked_candle") else signal_time
        later = frame[(frame.timestamp > signal_time) & (frame.timestamp > last_checked)]
        if later.empty:
            continue
        for candle in later.itertuples(index=False):
            high, low = float(candle.high), float(candle.low)
            direction = trade["direction"].lower()
            stop_hit = low <= float(trade["stop_loss"]) if direction == "long" else high >= float(trade["stop_loss"])
            target_hit = high >= float(trade["take_profit"]) if direction == "long" else low <= float(trade["take_profit"])
            exit_reason = None
            exit_price = None
            if stop_hit:  # conservative: stop takes precedence if both hit in one OHLC candle
                exit_reason, exit_price = "STOP_LOSS", float(trade["stop_loss"])
            elif target_hit:
                exit_reason, exit_price = "TAKE_PROFIT", float(trade["take_profit"])
            trade["last_checked_candle"] = pd.Timestamp(candle.timestamp).isoformat()
            trade["last_checked_at"] = utc_now()
            if exit_reason:
                risk = abs(float(trade["entry"]) - float(trade["stop_loss"]))
                pnl_distance = (exit_price - float(trade["entry"])) if direction == "long" else (float(trade["entry"]) - exit_price)
                trade.update({
                    "status": "CLOSED", "exit_time": pd.Timestamp(candle.timestamp).isoformat(),
                    "exit_price": exit_price, "exit_reason": exit_reason,
                    "realized_r_multiple": round(pnl_distance / risk, 4) if risk else None,
                })
                break
        store.upsert_paper_trade(trade)
        updated.append(trade)
    return updated
