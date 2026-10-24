"""Multi-timeframe swing opportunity scanner for Friday.

Daily candles establish the thesis; closed 4H candles refine the entry.
Research-only: this module never submits exchange orders.
"""
from __future__ import annotations

from datetime import datetime, timezone
import numpy as np
import pandas as pd

from research.trendlines import find_wick_pivots, fit_touch_trendlines
from research.friday_market_data import fetch_ohlc
from research.structure_lifecycle import evaluate_structure

BREAK_BUFFER = 0.0015
LINE_TOL = 0.0015
MAX_LINE_DISTANCE = 0.035


def _indicators(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.sort_values("timestamp").reset_index(drop=True).copy()
    close = out["close"].astype(float)
    for span in (5, 8, 13, 21):
        out[f"ema{span}"] = close.ewm(span=span, adjust=False).mean()
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    rsi = 100 - 100 / (1 + gain / loss.replace(0, np.nan))
    low = rsi.rolling(14, min_periods=14).min()
    high = rsi.rolling(14, min_periods=14).max()
    raw_stoch = 100 * (rsi - low) / (high - low).replace(0, np.nan)
    out["stoch_k"] = raw_stoch.rolling(3, min_periods=3).mean()
    out["stoch_d"] = out["stoch_k"].rolling(3, min_periods=3).mean()
    macd = close.ewm(span=12, adjust=False).mean() - close.ewm(span=26, adjust=False).mean()
    signal = macd.ewm(span=9, adjust=False).mean()
    out["macd"] = macd
    out["macd_signal"] = signal
    out["macd_hist"] = macd - signal
    out["volume_sma20"] = out["volume"].rolling(20, min_periods=5).mean()
    return out


def _line_candidates(frame: pd.DataFrame) -> list:
    # Use only candles available at the scan time; pivot confirmation is respected
    # by the trendline implementation and no incomplete Kraken candle is retained.
    sample = frame.tail(180).reset_index(drop=True)
    pivots = find_wick_pivots(sample, left_bars=3, right_bars=3)
    lines = []
    for kind in ("support", "resistance"):
        lines.extend(fit_touch_trendlines(
            sample, pivots, kind=kind, min_touches=3,
            tolerance_pct=LINE_TOL, max_penetration_pct=0.003, max_lines=20,
        ))
    last_i = len(sample) - 1
    close = float(sample.iloc[-1].close)
    return [line for line in lines
            if line.end.confirmed_at <= last_i
            and abs(line.value_at(last_i) - close) / max(abs(close), 1e-9) <= MAX_LINE_DISTANCE]




def _daily_thesis_lifecycle(daily: pd.DataFrame, previous: dict | None = None) -> dict:
    """Recover the latest confirmed daily trendline break and its subsequent lifecycle.

    Unlike the nearby-line dashboard, this intentionally retains broken lines up to
    20% from price so a valid thesis does not vanish merely because price moved away.
    All decisions use completed daily candles supplied by the caller.
    """
    frame = daily.sort_values("timestamp").tail(180).reset_index(drop=True).copy()
    if len(frame) < 60:
        return {"state": "NO_THESIS", "reason": "insufficient_daily_history"}
    structure = evaluate_structure(
        frame, lookback=180, min_touches=3, break_buffer_pct=BREAK_BUFFER,
        max_distance_pct=0.20,
    )
    latest_close = float(frame.iloc[-1].close)
    if not structure.broken_lines:
        # If the original line has moved outside the broad search window, carry
        # forward its last persisted lifecycle and still check the newest completed
        # daily candle for retest or invalidation.
        if isinstance(previous, dict) and previous.get("state") in {"WAITING_FOR_RETEST", "RETEST_HELD"}:
            try:
                anchor_time = pd.Timestamp(previous["anchor_start_time"])
                current_time = pd.Timestamp(frame.iloc[-1].timestamp)
                bars_from_anchor = int(round((current_time - anchor_time).total_seconds() / 86400))
                level = float(previous["anchor_start_wick"]) + float(previous["slope_per_candle"]) * bars_from_anchor
                close = latest_close
                last = frame.iloc[-1]
                direction = str(previous["direction"])
                invalid = close > level * 1.003 if direction == "short" else close < level * 0.997
                state = "INVALIDATED" if invalid else previous["state"]
                retest_time = previous.get("retest_time")
                if not invalid and state == "WAITING_FOR_RETEST":
                    touched = (float(last.high) >= level * 0.9985 and close <= level) if direction == "short" else (float(last.low) <= level * 1.0015 and close >= level)
                    if touched:
                        state, retest_time = "RETEST_HELD", pd.Timestamp(last.timestamp).isoformat()
                return {
                    **previous, "state": state, "line_price_current": float(level),
                    "invalidation_time": pd.Timestamp(last.timestamp).isoformat() if invalid else previous.get("invalidation_time"),
                    "retest_time": retest_time,
                    "last_evaluated_candle": pd.Timestamp(last.timestamp).isoformat(),
                    "carried_forward": True,
                }
            except (KeyError, TypeError, ValueError, OverflowError):
                pass
        return {"state": "NO_THESIS", "reason": "no_confirmed_daily_trendline_break"}

    candidates = []
    for line in structure.broken_lines:
        br = line.get("break") or {}
        break_index = int(br.get("break_index", -1))
        if break_index < 0 or break_index >= len(frame):
            continue
        direction = str(br.get("direction", ""))
        if direction not in {"long", "short"}:
            continue
        start_index = int(line.get("anchor_start_index", -1))
        start_price = float(line.get("anchor_start_wick", 0))
        slope = float(line.get("slope_per_bar", 0))
        if start_index < 0 or start_price <= 0:
            continue

        state = "WAITING_FOR_RETEST"
        retest_time = None
        invalidation_time = None
        for i in range(break_index + 1, len(frame)):
            row = frame.iloc[i]
            level = start_price + slope * (i - start_index)
            close = float(row.close)
            if direction == "short":
                invalid = close > level * 1.003
                retest = float(row.high) >= level * 0.9985 and close <= level
            else:
                invalid = close < level * 0.997
                retest = float(row.low) <= level * 1.0015 and close >= level
            if invalid:
                state = "INVALIDATED"
                invalidation_time = pd.Timestamp(row.timestamp).isoformat()
                break
            if retest and state == "WAITING_FOR_RETEST":
                state = "RETEST_HELD"
                retest_time = pd.Timestamp(row.timestamp).isoformat()

        current_level = start_price + slope * (len(frame) - 1 - start_index)
        if state != "INVALIDATED":
            if direction == "short" and latest_close > current_level * 1.003:
                state = "INVALIDATED"
                invalidation_time = pd.Timestamp(frame.iloc[-1].timestamp).isoformat()
            elif direction == "long" and latest_close < current_level * 0.997:
                state = "INVALIDATED"
                invalidation_time = pd.Timestamp(frame.iloc[-1].timestamp).isoformat()

        break_time = br.get("break_time") or pd.Timestamp(frame.iloc[break_index].timestamp).isoformat()
        candidates.append({
            "state": state, "direction": direction, "timeframe": "1D",
            "break_time": break_time, "break_close": float(br.get("close", frame.iloc[break_index].close)),
            "line_price_at_break": float(br.get("line_price", current_level)),
            "line_price_current": float(current_level),
            "line_kind": line.get("kind"), "touch_count": int(line.get("touch_count", 0)),
            "anchor_start_time": line.get("anchor_start_time"),
            "anchor_end_time": line.get("anchor_end_time"),
            "anchor_start_wick": start_price, "anchor_end_wick": line.get("anchor_end_wick"),
            "slope_per_candle": slope, "retest_time": retest_time,
            "invalidation_time": invalidation_time,
            "last_evaluated_candle": pd.Timestamp(frame.iloc[-1].timestamp).isoformat(),
            "thesis_id": "|".join([direction, str(break_time), str(line.get("anchor_start_time")), str(line.get("anchor_end_time"))]),
        })
    if not candidates:
        return {"state": "NO_THESIS", "reason": "no_valid_confirmed_break_records"}
    latest = max(candidates, key=lambda item: item["break_time"])
    return latest


def _snapshot(frame: pd.DataFrame, timeframe: str) -> dict:
    f = _indicators(frame)
    if len(f) < 60:
        return {"timeframe": timeframe, "state": "insufficient_history", "candles": len(f)}
    row, prev = f.iloc[-1], f.iloc[-2]
    close = float(row.close)
    bullish = float(row.ema5) > float(row.ema13) and float(row.ema8) > float(row.ema21)
    bearish = float(row.ema5) < float(row.ema13) and float(row.ema8) < float(row.ema21)
    k, d, pk, pd_ = row.stoch_k, row.stoch_d, prev.stoch_k, prev.stoch_d
    cross_up = all(np.isfinite(v) for v in (k, d, pk, pd_)) and pk <= pd_ and k > d
    cross_down = all(np.isfinite(v) for v in (k, d, pk, pd_)) and pk >= pd_ and k < d
    hist, old_hist = float(row.macd_hist), float(prev.macd_hist)
    macd_bullish_change = hist > old_hist
    macd_bearish_change = hist < old_hist
    volume_ratio = (float(row.volume) / float(row.volume_sma20)
                    if np.isfinite(row.volume_sma20) and float(row.volume_sma20) > 0 else None)
    # Trendline pivots and line indices are relative to this 180-candle sample.
    # Keep all line evaluation and anchor lookup in that same coordinate system;
    # indicator values above still use the full available history.
    line_frame = f.tail(180).reset_index(drop=True)
    lines = _line_candidates(f)
    nearby = []
    breaks = []
    for line in lines:
        i = len(line_frame) - 1
        level = float(line.value_at(i))
        distance = (close - level) / max(abs(level), 1e-9) * 100
        previous_level = float(line.value_at(i - 1))
        previous_close = float(line_frame.iloc[-2].close)
        if line.kind == "resistance":
            crossed = close > level * (1 + BREAK_BUFFER) and previous_close <= previous_level * (1 + BREAK_BUFFER)
            direction = "long"
        else:
            crossed = close < level * (1 - BREAK_BUFFER) and previous_close >= previous_level * (1 - BREAK_BUFFER)
            direction = "short"
        item = {
            "kind": line.kind, "price": round(level, 6), "distance_pct": round(distance, 3),
            "touches": len(line.touch_indices),
            "start_time": line_frame.iloc[line.start.index].timestamp.isoformat(),
            "end_time": line_frame.iloc[line.end.index].timestamp.isoformat(),
            "start_price": float(line.start.price), "end_price": float(line.end.price),
            "slope_per_candle": float(line.slope_per_bar),
        }
        nearby.append(item)
        if crossed:
            breaks.append({**item, "direction": direction})
    nearby.sort(key=lambda x: abs(x["distance_pct"]))
    state = "bullish" if bullish else "bearish" if bearish else "mixed"
    return {
        "timeframe": timeframe, "state": state, "last_closed_candle_utc": row.timestamp.isoformat(),
        "close": close, "ema5": float(row.ema5), "ema13": float(row.ema13),
        "ema8": float(row.ema8), "ema21": float(row.ema21),
        "ema_5_13_spread_pct": round((float(row.ema5) / float(row.ema13) - 1) * 100, 4),
        "ema_8_21_spread_pct": round((float(row.ema8) / float(row.ema21) - 1) * 100, 4),
        "stoch_k": _finite(k), "stoch_d": _finite(d), "stoch_cross_up": bool(cross_up),
        "stoch_cross_down": bool(cross_down), "macd": _finite(row.macd),
        "macd_signal": _finite(row.macd_signal), "macd_hist": _finite(hist),
        "macd_hist_change": "improving" if macd_bullish_change else "deteriorating" if macd_bearish_change else "flat",
        "volume": float(row.volume), "volume_vs_20_candle_average": _finite(volume_ratio),
        "nearby_trendlines": nearby[:8], "confirmed_breaks": breaks,
    }


def _finite(value):
    try:
        number = float(value)
        return round(number, 6) if np.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def analyse_pair(pair: str, daily: pd.DataFrame | None = None, four_hour: pd.DataFrame | None = None, previous_thesis: dict | None = None) -> dict:
    """Build a causal daily thesis and 4H trigger summary for one Kraken spot pair."""
    daily = daily if daily is not None else fetch_ohlc(pair=pair, interval=1440)
    four_hour = four_hour if four_hour is not None else fetch_ohlc(pair=pair, interval=240)
    d1 = _snapshot(daily, "1D")
    h4 = _snapshot(four_hour, "4H")
    daily_thesis_lifecycle = _daily_thesis_lifecycle(daily, previous_thesis)
    if daily_thesis_lifecycle.get("state") in {"WAITING_FOR_RETEST", "RETEST_HELD"}:
        thesis_direction = "bullish" if daily_thesis_lifecycle["direction"] == "long" else "bearish"
    else:
        thesis_direction = d1["state"] if d1["state"] in ("bullish", "bearish") else "neutral"
    daily_breaks = [x for x in d1.get("confirmed_breaks", []) if x["direction"] == thesis_direction.replace("bullish", "long").replace("bearish", "short")]
    matching_h4_breaks = [x for x in h4.get("confirmed_breaks", []) if x["direction"] == thesis_direction.replace("bullish", "long").replace("bearish", "short")]
    # A daily EMA regime can start the watch; a confirmed daily trendline break is
    # stronger evidence. A 4H Entry 1 requires a same-direction closed-candle break.
    daily_supportive_momentum = (
        (thesis_direction == "bullish" and (d1.get("stoch_cross_up") or d1.get("macd_hist_change") == "improving"))
        or (thesis_direction == "bearish" and (d1.get("stoch_cross_down") or d1.get("macd_hist_change") == "deteriorating"))
    )
    if thesis_direction == "neutral":
        state = "WATCH_NEUTRAL"
    elif matching_h4_breaks and daily_supportive_momentum:
        state = "ENTRY_1_CANDIDATE"
    elif daily_breaks or daily_supportive_momentum:
        state = "WATCH_MAJOR_MOVE"
    else:
        state = "WATCH_STRUCTURE"
    return {
        "pair": pair, "strategy": "daily-thesis-4h-execution-v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "state": state, "thesis_direction": thesis_direction,
        "daily_thesis": d1, "daily_thesis_lifecycle": daily_thesis_lifecycle,
        "four_hour_execution": h4,
        "daily_directional_breaks": daily_breaks,
        "aligned_4h_breaks": matching_h4_breaks,
        "entry1_alert_eligible": state == "ENTRY_1_CANDIDATE",
        "entry2_policy": "Wait for a post-break retest that holds, then a later closed 4H candle beyond the reaction extreme; never infer from an open candle.",
        "risk_policy": "No alert is actionable without structural invalidation, target/resistance room, and portfolio risk checks.",
        "limitations": [
            "Research signal only; it never executes trades.",
            "Indicator confluence is evidence, not a calibrated probability.",
            "Trendlines are algorithmic candidates and need walk-forward validation.",
            "Daily context uses only the latest completed daily candle.",
            "This scanner is a first implementation; target room, structural stops, and full retest lifecycle remain separate validation work.",
        ],
    }
