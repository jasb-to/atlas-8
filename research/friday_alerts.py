"""Friday signal alerts: deterministic message formatting and Telegram delivery.

Notifications only. This module cannot place or manage exchange orders.
"""
from __future__ import annotations

import os
from typing import Any, Mapping

import requests


def build_signal_alert(event: Mapping[str, Any], *, pair: str, timeframe: str = "4H") -> str:
    """Render a compact human-readable alert from a research signal event."""
    direction = str(event.get("direction", "")).upper()
    if direction not in {"LONG", "SHORT"}:
        raise ValueError("event direction must be long or short")
    kind = event.get("event")
    if event.get("event_add") == "CONTINUATION_ADD_CONFIRMED":
        title = "ENTRY 2 — CONTINUATION CONFIRMED"
        entry = event.get("continuation_add_close")
        line = event.get("line_price_at_break")
        signal_time = event.get("continuation_add_time")
        why = [
            "Breakout retest held on a closed candle.",
            f"Price then closed beyond the retest reaction extreme ({event.get('continuation_trigger')}).",
            "This is a research alert, not an order.",
        ]
    elif kind == "BREAK_CLOSE_PROBE":
        title = "ENTRY 1 — TRENDLINE BREAK"
        entry = event.get("close_at_break")
        line = event.get("line_price_at_break")
        signal_time = event.get("signal_time")
        why = [
            f"Closed candle broke {event.get('line_kind')} with {event.get('touch_count')} line touches.",
            f"Daily bias: {event.get('daily_bias', 'unknown')}; EMA aligned: {event.get('ema_aligned', 'unknown')}.",
            f"Momentum context: {event.get('momentum_context', {})}.",
            "Next: monitor for retest; no automatic add on line contact.",
        ]
    else:
        raise ValueError("event is not an alertable Entry 1 or Entry 2 event")

    lines = [
        f"FRIDAY | {title}",
        f"Market: {pair} · Timeframe: {timeframe} · Direction: {direction}",
        f"Signal candle: {signal_time}",
        f"Reference close: {entry}",
        f"Trendline at break: {line}",
        *[f"• {item}" for item in why],
        "Risk: define structural invalidation and target before any manual action.",
        "Mode: research / paper only — no trade submitted.",
    ]
    return "\n".join(lines)


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
