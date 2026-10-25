"""Standalone FRIDAY alert geometry audit; run from repository root."""
from __future__ import annotations

from research.friday_alerts import build_signal_alert, send_telegram_alert


def event(entry, stop, target, direction="short"):
    return {
        "event": "BREAK_CLOSE_PROBE",
        "direction": direction,
        "signal_time": "2024-06-01T00:00:00+00:00",
        "close_at_break": entry,
        "paper_stop": stop,
        "paper_target": target,
        # Deliberately misleading: alert must calculate its own R.
        "paper_reward_risk": 999,
    }


def main():
    good = build_signal_alert(event("8.822", "9.046", "7.833"), pair="AVAXUSDT")
    assert "🎯 Entry: 8.822" in good, good
    assert "🛑 Stop Loss: 9.046" in good, good
    assert "💰 TP1: 8.486 (1.5R)" in good, good
    assert "999" not in good, good
    assert "💰 TP2: 7.833 (4.42R)" in good, good

    wrong_side = build_signal_alert(event(100, 90, 80), pair="AVAXUSDT")
    assert wrong_side.startswith("Invalid trade geometry:"), wrong_side
    refused = send_telegram_alert(
        wrong_side,
        environ={"TELEGRAM_BOT_TOKEN": "not-used", "TELEGRAM_CHAT_ID": "not-used"},
    )
    assert refused["sent"] is False and refused["status"] == "invalid_trade_geometry", refused

    malformed = build_signal_alert(event("8.822", 0, "none"), pair="AVAXUSDT")
    assert malformed.startswith("Invalid trade geometry:"), malformed
    refused = send_telegram_alert(
        malformed,
        environ={"TELEGRAM_BOT_TOKEN": "not-used", "TELEGRAM_CHAT_ID": "not-used"},
    )
    assert refused["sent"] is False, refused

    missing_stop = build_signal_alert(event("8.822", None, "7.833"), pair="AVAXUSDT")
    assert missing_stop.startswith("Invalid trade geometry:"), missing_stop
    assert "🛑 Stop Loss: n/a" not in missing_stop
    print("| Case | Result |")
    print("|---|---|")
    print("| TP1 from entry 8.822 / stop 9.046 | PASS — 8.486 |")
    print("| TP2 R recalculated, supplied 999R ignored | PASS |")
    print("| Wrong-side short stop | PASS — rejected |")
    print("| String entry / zero stop / nonnumeric target | PASS — rejected |")
    print("| Sender refuses invalid geometry | PASS |")
    print("| Missing stop never produces a trade alert with n/a stop | PASS |")


if __name__ == "__main__":
    main()
