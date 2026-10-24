"""Friday signal alerts: compact deterministic Telegram formatting.

Notifications only; this module cannot place or manage exchange orders.
"""
from __future__ import annotations

import os
from typing import Any, Mapping

import requests


def _price(value: Any, pair: str) -> str:
    """Format prices at a sensible precision for the market."""
    if value is None:
        return "—"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    if not __import__("math").isfinite(number):
        return "—"
    symbol = pair.upper().replace("USDT", "").replace("USD", "").replace("/", "")
    if symbol in {"BTC", "WBTC"} or abs(number) >= 1000:
        decimals = 0
    elif abs(number) >= 100:
        decimals = 1
    elif abs(number) >= 1:
        decimals = 2
    elif abs(number) >= 0.01:
        decimals = 4
    else:
        decimals = 6
    return f"{number:,.{decimals}f}"


def _take_profit_1(entry: Any, stop: Any, direction: str) -> float | None:
    """Use 1R as TP1; the structural target remains TP2."""
    try:
        e, s = float(entry), float(stop)
    except (TypeError, ValueError):
        return None
    if not all(__import__("math").isfinite(x) for x in (e, s)) or e <= 0 or s <= 0:
        return None
    risk = abs(e - s)
    if risk <= 0:
        return None
    return e + risk if direction == "LONG" else e - risk


def build_signal_alert(event: Mapping[str, Any], *, pair: str, timeframe: str = "4H") -> str:
    """Render the requested compact format: token, entry, TP1, TP2, SL, and reason."""
    direction = str(event.get("direction", "")).upper()
    if direction not in {"LONG", "SHORT"}:
        raise ValueError("event direction must be long or short")

    if event.get("event_add") == "CONTINUATION_ADD_CONFIRMED":
        entry = event.get("continuation_add_close")
        signal_time = event.get("continuation_add_time")
        reason = "Retest held on a closed candle; price then closed beyond the retest reaction extreme."
        trigger = event.get("continuation_trigger")
        if trigger is not None:
            reason = f"Retest held on a closed candle; price closed beyond the reaction level ({_price(trigger, pair)})."
    elif event.get("event") == "BREAK_CLOSE_PROBE":
        entry = event.get("close_at_break")
        signal_time = event.get("signal_time")
        line_kind = str(event.get("line_kind", "trendline")).replace("_", " ").lower()
        touches = event.get("touch_count")
        daily = str(event.get("daily_bias", "unknown")).lower()
        aligned = event.get("ema_aligned")
        momentum = event.get("momentum_context") or {}
        parts = [f"Closed candle broke {line_kind}"]
        if touches is not None:
            parts.append(f"{touches} line touches")
        if daily not in {"", "unknown", "none"}:
            parts.append(f"daily bias {daily}")
        if aligned is True:
            parts.append("EMAs aligned")
        if momentum.get("macd_bearish_expansion") and direction == "SHORT":
            parts.append("MACD bearish expansion")
        elif momentum.get("macd_bullish_expansion") and direction == "LONG":
            parts.append("MACD bullish expansion")
        reason = "; ".join(parts) + "."
    else:
        raise ValueError("event is not an alertable Entry 1 or Entry 2 event")

    stop = event.get("paper_stop")
    target = event.get("paper_target")
    tp1 = _take_profit_1(entry, stop, direction) if stop is not None else None
    lines = [
        f"FRIDAY | {pair} {direction} ({timeframe})",
        f"Entry: {_price(entry, pair)}",
        f"TP1: {_price(tp1, pair)}",
        f"TP2: {_price(target, pair)}",
        f"SL: {_price(stop, pair)}",
        f"Why: {reason}",
    ]
    return "\n".join(lines)


def build_momentum_reversal_alert(candidate: Mapping[str, Any], *, pair: str, timeframe: str = "4H") -> str:
    """Format a risk-defined momentum reversal candidate in the same compact style."""
    direction = str(candidate.get("direction", "")).upper()
    if direction not in {"LONG", "SHORT"} or not candidate.get("eligible"):
        raise ValueError("candidate must be an eligible long or short plan")
    entry, stop, target = candidate.get("entry"), candidate.get("stop"), candidate.get("target")
    tp1 = _take_profit_1(entry, stop, direction)
    return "\n".join([
        f"FRIDAY | {pair} {direction} ({timeframe})",
        f"Entry: {_price(entry, pair)}",
        f"TP1: {_price(tp1, pair)}",
        f"TP2: {_price(target, pair)}",
        f"SL: {_price(stop, pair)}",
        "Why: Fresh Stoch RSI cross from an extreme, with momentum and structure confirmation.",
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
