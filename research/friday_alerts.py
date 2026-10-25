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
    """Render a two-second, risk-defined paper alert.

    Weekly/daily context is deliberately optional so existing callers remain
    compatible. If the weekly gate has not been passed in, the alert says so
    rather than claiming an unverified weekly direction.
    """
    direction = str(event.get("direction", "")).upper()
    if direction not in {"LONG", "SHORT"}:
        raise ValueError("event direction must be long or short")

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
        raise ValueError("event is not an alertable Entry 1 or Entry 2 event")

    stop = event.get("paper_stop")
    target = event.get("paper_target")
    tp1 = None
    tp1_rr = 1.5
    if entry is not None and stop is not None:
        try:
            entry_value, stop_value = float(entry), float(stop)
            risk = abs(entry_value - stop_value)
            if risk > 0:
                tp1 = entry_value + (tp1_rr * risk if direction == "LONG" else -tp1_rr * risk)
        except (TypeError, ValueError):
            tp1 = None

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
        f"📉 Direction: {direction}",
        "",
        f"🎯 Entry: {_fmt_price(entry)}",
        f"🛑 Stop Loss: {_fmt_price(stop)}",
    ]
    if tp1 is not None and target is not None:
        lines.append(f"💰 TP1: {_fmt_price(tp1)} ({tp1_rr:.1f}R)")
    if target is not None:
        rr = event.get("paper_reward_risk")
        rr_text = f" ({float(rr):.1f}R)" if rr is not None else ""
        lines.append(f"💰 TP2: {_fmt_price(target)}{rr_text}")
    lines.extend(["", f"🧠 Why: {why}", "", "(Mode: Research / Paper Only)"])
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
