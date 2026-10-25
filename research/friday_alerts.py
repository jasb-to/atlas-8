"""Friday signal alerts: compact deterministic formatting and Telegram delivery.

Notifications only. This module cannot place or manage exchange orders.
"""
from __future__ import annotations

import os
from typing import Any, Mapping

import requests


def _fmt_price(value: Any) -> str:
    """Format prices compactly without losing small-token precision."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "n/a" if value is None else str(value)
    if not (number == number and abs(number) != float("inf")):
        return "n/a"
    return f"{number:,.6f}".rstrip("0").rstrip(".")


def _bias_label(value: Any, timeframe: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"bullish", "bull", "long"}:
        return f"{timeframe} bullish"
    if normalized in {"bearish", "bear", "short"}:
        return f"{timeframe} bearish"
    return f"{timeframe} filter not applied"


def build_signal_alert(
    event: Mapping[str, Any],
    *,
    pair: str,
    timeframe: str = "4H",
    weekly_thesis: Mapping[str, Any] | None = None,
    daily_context: Mapping[str, Any] | None = None,
) -> str:
    """Render a validated, risk-defined paper alert; invalid geometry fails closed."""
    direction = str(event.get("direction", "")).strip().lower()
    if direction not in {"long", "short"}:
        return "Invalid trade geometry: Direction must be LONG or SHORT."

    kind = event.get("event")
    if event.get("event_add") == "CONTINUATION_ADD_CONFIRMED":
        title = "ENTRY 2 CONFIRMED"
        entry = event.get("continuation_add_close")
        signal_time = event.get("continuation_add_time") or event.get("signal_time")
        execution_reason = "4H trendline break + retest held; continuation confirmed"
    elif kind in {"BREAK_CLOSE_PROBE", "EXECUTION_TRIGGER_ENTRY_1"}:
        title = "ENTRY 1 CONFIRMED"
        entry = event.get("close_at_break")
        signal_time = event.get("signal_time")
        execution_reason = "4H trendline break confirmed"
    else:
        return "Invalid trade geometry: Event is not an alertable Entry 1 or Entry 2 event."

    stop, target = event.get("paper_stop"), event.get("paper_target")
    try:
        entry_value, stop_value, target_value = float(entry), float(stop), float(target)
    except (TypeError, ValueError):
        return "Invalid trade geometry: Entry, stop and target must be numeric."
    values = (entry_value, stop_value, target_value)
    if not all(value == value and abs(value) != float("inf") and value > 0 for value in values):
        return "Invalid trade geometry: Entry, stop and target must be finite positive prices."

    risk = abs(entry_value - stop_value)
    if risk == 0:
        return "Invalid trade geometry: Stop cannot equal Entry."
    if (direction == "short" and stop_value < entry_value) or (
        direction == "long" and stop_value > entry_value
    ):
        return "Invalid trade geometry: Stop is on the wrong side of entry."
    if (direction == "short" and target_value >= entry_value) or (
        direction == "long" and target_value <= entry_value
    ):
        return "Invalid trade geometry: Target is on the wrong side of entry."

    tp1 = entry_value - (risk * 1.5) if direction == "short" else entry_value + (risk * 1.5)
    tp2_rr = abs(target_value - entry_value) / risk
    if not (tp2_rr > 0 and tp2_rr == tp2_rr and abs(tp2_rr) != float("inf")):
        return "Invalid trade geometry: Target reward/risk is invalid."

    weekly_direction = str((weekly_thesis or {}).get("direction", "")).lower()
    weekly_status = str((weekly_thesis or {}).get("status", "")).lower()
    if weekly_status == "active" and weekly_direction in {"long", "short", "bullish", "bearish"}:
        weekly_reason = _bias_label(weekly_direction, "Weekly")
    else:
        weekly_reason = "Weekly filter not applied"
    daily_bias = (daily_context or {}).get("bias", event.get("daily_bias"))
    daily_reason = _bias_label(daily_bias, "Daily")
    why = f"{weekly_reason}. {daily_reason}. {execution_reason}."

    lines = [
        f"🚨 FRIDAY | {title}",
        f"🪙 Token: {pair}",
        f"📉 Direction: {direction.upper()}",
        "",
        f"🎯 Entry: {_fmt_price(entry_value)}",
        f"🛑 Stop Loss: {_fmt_price(stop_value)}",
        f"💰 TP1: {_fmt_price(tp1)} (1.5R)",
        f"💰 TP2: {_fmt_price(target_value)} ({tp2_rr:.2f}R)",
        "",
        f"🧠 Why: {why}",
        "",
        "(Mode: Research / Paper Only)",
    ]
    return "\n".join(lines)

def build_momentum_reversal_alert(candidate: Mapping[str, Any], *, pair: str, timeframe: str = "4H") -> str:
    """Format a risk-defined momentum reversal candidate for notification."""
    direction = str(candidate.get("direction", "")).upper()
    if direction not in {"LONG", "SHORT"} or not candidate.get("eligible"):
        raise ValueError("candidate must be an eligible long or short plan")
    return "\n".join([
        f"🚨 FRIDAY | REVERSAL CANDIDATE",
        f"🪙 Token: {pair}",
        f"📉 Direction: {direction}",
        "",
        f"🎯 Entry: {_fmt_price(candidate.get('entry'))}",
        f"🛑 Stop Loss: {_fmt_price(candidate.get('stop'))}",
        f"💰 TP2: {_fmt_price(candidate.get('target'))} ({float(candidate.get('reward_risk', 0)):.1f}R)",
        "",
        "🧠 Why: Stoch RSI reversal from an extreme with momentum/structure confirmation.",
        "",
        "(Mode: Research / Paper Only)",
    ])


def send_telegram_alert(message: str, *, environ: Mapping[str, str] | None = None,
                        timeout_seconds: int = 8) -> dict[str, Any]:
    """Send a Telegram notification if credentials are configured; never logs secrets."""
    env = os.environ if environ is None else environ
    # Fail closed: never send validation failures or a trade with an absent stop.
    if message.startswith("Invalid trade geometry:") or "🛑 Stop Loss: n/a" in message:
        return {"sent": False, "status": "invalid_trade_geometry",
                "reason": "Refused to send an invalid trade alert"}
    token, chat_id = env.get("TELEGRAM_BOT_TOKEN"), env.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return {"sent": False, "status": "not_configured",
                "reason": "TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required"}
    response = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": message, "disable_web_page_preview": True},
        timeout=timeout_seconds,
    )
    response.raise_for_status()
    payload = response.json()
    if not payload.get("ok"):
        return {"sent": False, "status": "provider_error", "reason": "Telegram did not confirm delivery"}
    return {"sent": True, "status": "sent", "message_id": payload.get("result", {}).get("message_id")}
