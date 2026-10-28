"""Causal 4H wick-trendline break preview; research-only, never places orders."""
from __future__ import annotations
import argparse, json, os
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
import pandas as pd
from research.trendlines import find_wick_pivots, fit_touch_trendlines
from research.friday_market_data import fetch_ohlc
from research.sheldon_strategy import consolidate_setups, replay_trades
from research.structure_lifecycle import evaluate_structure
from research.friday_decision import evaluate_friday_decision
from research.signal_journal import append_decision, build_decision_record
from research.friday_ai_review import review_decision
from research.friday_reversal import reversal_context
from research.friday_trade_plan import build_trade_plan
from research.friday_momentum_reversal import scan_momentum_reversal

LINE_TOL = 0.0015
BREAK_BUFFER = 0.0015
RETEST_TOL = 0.003
RETEST_BARS = 18              # 72 hours; allow the setup to take days
CONTINUATION_BARS = 6         # 24 hours after retest to confirm the reaction break
LOOKBACK = 180
MAX_LINE_AGE = 90
MAX_DISTANCE = 0.025


def momentum_reversal_enabled() -> bool:
    """Return whether the standalone reversal scanner is explicitly enabled."""
    return os.getenv("FRIDAY_MOMENTUM_REVERSAL_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}

def indicators(frame):
    out = frame.sort_values("timestamp").reset_index(drop=True).copy()
    close = out.close.astype(float)
    out["ema8_4h"] = close.ewm(span=8, adjust=False).mean()
    out["ema21_4h"] = close.ewm(span=21, adjust=False).mean()
    out["macd"] = close.ewm(span=12, adjust=False).mean() - close.ewm(span=26, adjust=False).mean()
    out["macd_signal"] = out["macd"].ewm(span=9, adjust=False).mean()
    out["macd_hist"] = out["macd"] - out["macd_signal"]
    prev = close.shift(1)
    tr = pd.concat([out.high-out.low, (out.high-prev).abs(), (out.low-prev).abs()], axis=1).max(axis=1)
    out["atr14"] = tr.ewm(alpha=1/14, adjust=False, min_periods=14).mean()
    daily = out.set_index("timestamp")[["open","high","low","close"]].resample("1D").agg(
        {"open":"first","high":"max","low":"min","close":"last"}).dropna()
    daily["ema8"] = daily.close.ewm(span=8, adjust=False).mean()
    daily["ema21"] = daily.close.ewm(span=21, adjust=False).mean()
    daily["bias"] = np.where(daily.ema8 >= daily.ema21, "bullish", "bearish")
    # Shift one full day to prevent using the current day's eventual close.
    prior_bias = daily.bias.shift(1)
    out["daily_bias"] = prior_bias.reindex(pd.DatetimeIndex(out.timestamp).normalize(), method="ffill").to_numpy()
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1/14, adjust=False, min_periods=14).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1/14, adjust=False, min_periods=14).mean()
    rsi = 100 - 100/(1 + gain/loss.replace(0, np.nan))
    lo, hi = rsi.rolling(14, min_periods=14).min(), rsi.rolling(14, min_periods=14).max()
    stoch = 100*(rsi-lo)/(hi-lo).replace(0, np.nan)
    out["stoch_k"] = stoch.rolling(3, min_periods=3).mean()
    out["stoch_d"] = out.stoch_k.rolling(3, min_periods=3).mean()
    return out

def break_crossed(frame, line, i):
    if i < 1: return False
    row, prev = frame.iloc[i], frame.iloc[i-1]
    lv, lp = line.value_at(i), line.value_at(i-1)
    if line.kind == "resistance":
        return float(row.close) > lv*(1+BREAK_BUFFER) and float(prev.close) <= lp*(1+BREAK_BUFFER)
    return float(row.close) < lv*(1-BREAK_BUFFER) and float(prev.close) >= lp*(1-BREAK_BUFFER)

def line_retest(frame, line, break_i, i, *, origin=0):
    """Return first post-break contact using absolute frame indices.

    Trendline pivot indices are relative to the prefix used to fit the line;
    origin maps those coordinates into frame. Pass zero when both share an origin.
    """
    def line_value(absolute_index):
        return line.value_at(absolute_index - origin)

    for j in range(break_i+1, min(i, break_i+RETEST_BARS)+1):
        row, lv = frame.iloc[j], line_value(j)
        tol = abs(lv)*RETEST_TOL
        if line.kind == "resistance":
            contact, held = float(row.low) <= lv+tol, float(row.close) >= lv
        else:
            contact, held = float(row.high) >= lv-tol, float(row.close) <= lv
        if contact: return True, j, held
    return False, None, False

def momentum_context(frame, i):
    """Causal, directional Stoch RSI and MACD momentum evidence."""
    if i < 2:
        return {"stoch_bullish_cross": False, "stoch_bearish_cross": False,
                "macd_bullish_expansion": False, "macd_bearish_expansion": False}
    row, prev = frame.iloc[i], frame.iloc[i-1]
    k, d, pk, pd_ = row.stoch_k, row.stoch_d, prev.stoch_k, prev.stoch_d
    valid = all(np.isfinite(x) for x in (k, d, pk, pd_))
    bull_cross = valid and pk <= pd_ and k > d
    bear_cross = valid and pk >= pd_ and k < d
    if {"macd", "macd_signal", "macd_hist"}.issubset(frame.columns):
        macd, signal, hist = frame["macd"], frame["macd_signal"], frame["macd_hist"]
    else:
        # Compatibility for standalone callers/tests; causal_replay precomputes once.
        close = frame.close.astype(float)
        macd = close.ewm(span=12, adjust=False).mean() - close.ewm(span=26, adjust=False).mean()
        signal = macd.ewm(span=9, adjust=False).mean()
        hist = macd - signal
    bull_expansion = hist.iloc[i] > 0 and hist.iloc[i] > hist.iloc[i-1] and macd.iloc[i] > signal.iloc[i]
    bear_expansion = hist.iloc[i] < 0 and hist.iloc[i] < hist.iloc[i-1] and macd.iloc[i] < signal.iloc[i]
    return {"stoch_bullish_cross": bool(bull_cross), "stoch_bearish_cross": bool(bear_cross),
            "macd_bullish_expansion": bool(bull_expansion), "macd_bearish_expansion": bool(bear_expansion),
            "macd_histogram": _num(hist.iloc[i]), "macd_histogram_previous": _num(hist.iloc[i-1])}


def structure_snapshot(prefix, candidates):
    """Find descending resistance + rising support whose projected apex is within 72 bars."""
    i = len(prefix)-1
    supports = [x for x in candidates if x.kind == "support" and x.slope_per_bar > 0]
    resistances = [x for x in candidates if x.kind == "resistance" and x.slope_per_bar < 0]
    pairs = []
    for sup in supports:
        for res in resistances:
            denom = sup.slope_per_bar - res.slope_per_bar
            if denom <= 0:
                continue
            cross_i = (res.start.price - sup.start.price + sup.slope_per_bar*sup.start.index - res.slope_per_bar*res.start.index) / denom
            bars_to_apex = cross_i - i
            if 0 < bars_to_apex <= 72:
                sv, rv = sup.value_at(i), res.value_at(i)
                if sv < rv and (rv-sv)/max(abs(float(prefix.iloc[-1].close)),1e-9) <= 0.08:
                    pairs.append({"support_price": float(sv), "resistance_price": float(rv),
                        "support_touches": len(sup.touch_indices), "resistance_touches": len(res.touch_indices),
                        "bars_to_apex": float(bars_to_apex),
                        "apex_time_estimate": (prefix.iloc[-1].timestamp + pd.Timedelta(hours=4*bars_to_apex)).isoformat()})
    pairs.sort(key=lambda x: (x["bars_to_apex"], -(x["support_touches"]+x["resistance_touches"])))
    return pairs[0] if pairs else None


def _num(x):
    try:
        v=float(x)
        return round(v,4) if np.isfinite(v) else None
    except (TypeError, ValueError): return None

def write_jsonl(path, rows):
    """Write one valid JSON object per physical line for downstream replay reports."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        "".join(json.dumps(row, sort_keys=True, allow_nan=False) + "\n" for row in rows),
        encoding="utf-8",
    )

def causal_replay(raw):
    frame = indicators(raw.sort_values("timestamp").reset_index(drop=True))
    events, active = [], []
    seen_keys = set()
    status={"state":"no_qualifying_line","message":"No recent three-touch line within 2.5% of close."}
    for i in range(max(90, LOOKBACK//2), len(frame)):
        start=max(0, i-LOOKBACK+1)
        prefix=frame.iloc[start:i+1].reset_index(drop=True)
        pivots=[p for p in find_wick_pivots(prefix, 3, 3) if p.confirmed_at <= len(prefix)-1]
        candidates=[]
        for kind in ("support","resistance"):
            candidates += fit_touch_trendlines(prefix,pivots,kind=kind,min_touches=3,
                tolerance_pct=LINE_TOL,max_penetration_pct=0.003,max_lines=12)
        close=float(prefix.iloc[-1].close)
        # Always refresh the dashboard state for this candle. Otherwise the last
        # valid line survives when the newest candles have no qualifying line,
        # making the "live" preview silently stale.
        status={"state":"no_qualifying_line",
            "message":"No recent three-touch line within 2.5% of the latest close.",
            "close":round(close,6),
            "last_closed_candle":prefix.iloc[-1].timestamp.isoformat()}
        candidates=[line for line in candidates
            if len(prefix)-1-line.confirmed_at <= MAX_LINE_AGE
            and abs(line.value_at(len(prefix)-1)-close)/close <= MAX_DISTANCE]
        candidates.sort(key=lambda line:(abs(line.value_at(len(prefix)-1)-close)/close,
            -len(line.touch_indices),-line.confirmed_at))
        # Dashboard state uses nearest line, but break detection scans all valid lines.
        wedge = structure_snapshot(prefix, candidates)
        break_recorded_this_candle = False
        if candidates:
            line=candidates[0]; lv=float(line.value_at(len(prefix)-1))
            status={"state":"watching_resistance" if line.kind=="resistance" else "watching_support",
                "line_kind":line.kind,"line_price":round(lv,6),"close":round(close,6),
                "distance_pct":round((close-lv)/abs(lv)*100,4),"touch_count":len(line.touch_indices),
                "anchor_start_time":prefix.iloc[line.start.index].timestamp.isoformat(),
                "anchor_end_time":prefix.iloc[line.end.index].timestamp.isoformat(),
                "anchor_start_wick":float(line.start.price),"anchor_end_wick":float(line.end.price),
                "converging_wedge":wedge,
                "structure_state": ("above_converging_resistance" if wedge and close > wedge["resistance_price"]*(1+BREAK_BUFFER)
                    else "inside_converging_structure" if wedge and wedge["support_price"] <= close <= wedge["resistance_price"]
                    else "below_converging_support" if wedge and close < wedge["support_price"]*(1-BREAK_BUFFER)
                    else "no_active_converging_break"),
                "last_closed_candle":prefix.iloc[-1].timestamp.isoformat()}
            for break_line in candidates:
                if not break_crossed(prefix,break_line,len(prefix)-1):
                    continue
                break_recorded_this_candle = True
                direction="long" if break_line.kind=="resistance" else "short"
                row=prefix.iloc[-1]
                aligned=(float(row.ema8_4h)>float(row.ema21_4h)) if direction=="long" else (float(row.ema8_4h)<float(row.ema21_4h))
                momentum=momentum_context(prefix,len(prefix)-1)
                event={"event":"BREAK_CLOSE_PROBE","signal_time":row.timestamp.isoformat(),"direction":direction,
                    "line_kind":break_line.kind,"line_price_at_break":float(break_line.value_at(len(prefix)-1)),
                    "close_at_break":close,"touch_count":len(break_line.touch_indices),"daily_bias":str(row.daily_bias),
                    "ema_aligned":bool(aligned),"stoch_k":_num(row.stoch_k),"stoch_d":_num(row.stoch_d),
                    "momentum_context":momentum,"converging_wedge":wedge,
                    "entry_plan":"small probe after closed-candle break; momentum/context are visible, not hard gates",
                    "retest_deadline":(row.timestamp+pd.Timedelta(hours=72)).isoformat(),
                    "outcome":"pending","forward_bars_checked":0}
                event["anchor_start_time"]=prefix.iloc[break_line.start.index].timestamp.isoformat()
                event["anchor_end_time"]=prefix.iloc[break_line.end.index].timestamp.isoformat()
                duplicate_key=(event["line_kind"],event["anchor_start_time"],event["anchor_end_time"])
                if duplicate_key not in seen_keys:
                    seen_keys.add(duplicate_key)
                    # Freeze line geometry in absolute candle coordinates at break time.
                    abs_start = start + break_line.start.index
                    abs_end = start + break_line.end.index
                    active.append({"event":event,"line":break_line,"origin":start,"break_i":i,
                                   "abs_start":abs_start,"abs_end":abs_end,
                                   "abs_slope":break_line.slope_per_bar})
                    events.append(event)
        if break_recorded_this_candle:
            status["state"] = "break_close_probe"
            status["break_recorded_this_candle"] = True
        # Manage each break against its original line coordinates; only future candles are outcomes.
        for a in list(active):
            age=i-a["break_i"]
            if age <= 0: continue
            e,line=a["event"],a["line"]
            e["forward_bars_checked"]=age
            # Evaluate the exact frozen line fitted at the original break candle.
            lv=line.start.price+a["abs_slope"]*(i-a["abs_start"])
            row=frame.iloc[i]; tol=abs(lv)*RETEST_TOL; close_i=float(row.close)
            if line.kind=="resistance":
                contact=float(row.low)<=lv+tol; held=close_i>=lv
                invalid=close_i<lv-abs(lv)*0.003
            else:
                contact=float(row.high)>=lv-tol; held=close_i<=lv
                invalid=close_i>lv+abs(lv)*0.003
            # A retest is a prerequisite, not an entry. Save its reaction extreme;
            # Entry 2 is valid only after a later closed candle breaks that extreme.
            if age<=RETEST_BARS and contact and held and not e.get("retest_confirmed"):
                e.update({"retest_confirmed":True,"retest_confirmed_time":row.timestamp.isoformat(),
                    "retest_confirmed_bar_index":i,
                    "retest_close":close_i,"retest_high":float(row.high),"retest_low":float(row.low),
                    "outcome":"retest_held_waiting_continuation","outcome_bars":age})
                continue
            # Invalidation always wins over a same-candle continuation trigger.
            # This avoids reporting an add after the breakout structure has failed.
            if invalid:
                e.update({"outcome":"failed_break","outcome_bars":age,"failure_time":row.timestamp.isoformat(),"failure_close":close_i})
                active.remove(a); continue
            if e.get("retest_confirmed") and not e.get("continuation_add_time"):
                trigger = float(e["retest_high"]) if e["direction"]=="long" else float(e["retest_low"])
                continued = float(row.close)>trigger if e["direction"]=="long" else float(row.close)<trigger
                if continued:
                    e.update({"event_add":"CONTINUATION_ADD_CONFIRMED",
                        "continuation_add_time":row.timestamp.isoformat(),
                        "continuation_add_close":close_i,"continuation_trigger":trigger,
                        "outcome":"continuation_add_confirmed","outcome_bars":age})
                    active.remove(a); continue
                retest_i = int(e.get("retest_confirmed_bar_index", i))
                if i - retest_i >= CONTINUATION_BARS:
                    e.update({"outcome":"no_continuation_within_window",
                        "outcome_bars":age,"continuation_expired_time":row.timestamp.isoformat()})
                    active.remove(a); continue
            elif age>=RETEST_BARS and not e.get("retest_confirmed"):
                e.update({"outcome":"no_retest_within_window","outcome_bars":age})
                active.remove(a)
    for e in events:
        matches=frame.index[frame.timestamp == pd.Timestamp(e["signal_time"])]
        if not len(matches): continue
        si=int(matches[0]); entry=float(frame.iloc[si].close)
        # Only pending events receive forward returns; terminal outcomes remain lifecycle labels.
        if e.get("outcome") == "pending":
            for h in (3,6,12,18):
                end=min(len(frame)-1,si+h)
                if end>si:
                    ret=(float(frame.iloc[end].close)-entry)/entry*(1 if e["direction"]=="long" else -1)
                    e[f"forward_{h}_bar_return_pct"]=round(ret*100,4)
                    e[f"forward_{h}_bar_end_time"]=frame.iloc[end].timestamp.isoformat()
        e["forward_returns_note"]="Raw directional close-to-close diagnostic for pending events only; no fees, slippage, stops or sizing."
    return events,status,frame

def entry2_replay_events(events):
    """Build a separate replay sample from confirmed continuation closes only."""
    return [
        {**event, "break_signal_time": event["signal_time"],
         "signal_time": event["continuation_add_time"], "entry_type": "ENTRY_2_CONTINUATION"}
        for event in events
        if event.get("outcome") == "continuation_add_confirmed" and event.get("continuation_add_time")
    ]


def latest_setup_state(events, last_closed_candle):
    """Summarise the newest recent probe/add lifecycle for monitoring output."""
    if not events:
        return {"state": "NO_SETUP", "message": "No confirmed break setup in the available replay."}
    now = pd.Timestamp(last_closed_candle)
    latest = max(events, key=lambda e: pd.Timestamp(e["signal_time"]))
    signal_time = pd.Timestamp(latest["signal_time"])
    age_hours = max(0.0, (now - signal_time).total_seconds() / 3600)
    base = {
        "direction": latest["direction"],
        "signal_time": latest["signal_time"],
        "line_kind": latest["line_kind"],
        "line_price_at_break": _num(latest["line_price_at_break"]),
        "touch_count": latest["touch_count"],
        "daily_bias": latest.get("daily_bias"),
        "ema_aligned": latest.get("ema_aligned"),
        "momentum_context": latest.get("momentum_context", {}),
        "age_hours": round(age_hours, 2),
        "outcome": latest.get("outcome", "pending"),
    }
    # Terminal outcomes take precedence over signal age: an old, completed
    # continuation is historical evidence, not an expired pending setup.
    if latest.get("outcome") == "failed_break":
        return {**base, "state": "INVALIDATED", "message": "The breakout failed its close-based invalidation rule."}
    if latest.get("outcome") == "no_retest_within_window":
        return {**base, "state": "EXPIRED", "message": "No qualifying retest held within 72 hours."}
    if latest.get("outcome") == "no_continuation_within_window":
        return {**base, "state": "EXPIRED", "message": "The retest held, but no continuation close broke its reaction extreme within the following 24 hours."}
    if latest.get("outcome") == "continuation_add_confirmed":
        return {**base, "state": "CONTINUATION_ADD_CONFIRMED", "retest_confirmed_time": latest.get("retest_confirmed_time"),
                "add_signal_time": latest.get("continuation_add_time"), "add_close": latest.get("continuation_add_close"),
                "message": "Retest held, then price closed beyond the retest reaction extreme; continuation add confirmed in research replay, not an order."}
    if age_hours > RETEST_BARS * 4:
        return {**base, "state": "EXPIRED", "message": "The 72-hour retest window has elapsed; this is not a current entry."}
    if latest.get("retest_confirmed"):
        return {**base, "state": "RETEST_HELD_WAITING_CONTINUATION", "retest_confirmed_time": latest.get("retest_confirmed_time"),
                "message": "Breakout retest held; waiting for a closed-candle continuation through the retest reaction extreme before Entry 2 add."}
    return {**base, "state": "ENTRY_PROBE_WAITING_RETEST",
            "retest_deadline": latest.get("retest_deadline"),
            "message": "Closed-candle breakout confirmed; paper probe state, waiting for a controlled retest."}


def build_preview(pair="XBTUSDT", interval=240, output_dir="results/strategy-preview"):
    raw=fetch_ohlc(pair=pair,interval=interval)
    events,status,frame=causal_replay(raw)
    setups=consolidate_setups(events,frame)
    split_index=min(max(int(len(frame)*0.7),1),len(frame)-1)
    walk_forward_split_time=frame.iloc[split_index].timestamp.isoformat()
    split_ts = pd.Timestamp(walk_forward_split_time)
    for setup in setups:
        setup["in_sample"] = pd.Timestamp(setup["signal_time"]) < split_ts
    trades=replay_trades(setups,frame)
    trades_structural=replay_trades(setups,frame,target_mode="structure_fib")
    # Entry 2 replay enters only after the retest reaction extreme is broken by
    # a later closed candle. This is a separate sample from the Entry 1 probe.
    entry2_setups=entry2_replay_events(setups)
    entry2_trades=replay_trades(entry2_setups,frame)
    entry2_trades_structural=replay_trades(entry2_setups,frame,target_mode="structure_fib")
    for replay_rows in (trades, trades_structural, entry2_trades, entry2_trades_structural):
        for trade in replay_rows:
            trade["in_sample"] = pd.Timestamp(trade["signal_time"]) < split_ts
    last=frame.iloc[-1]
    setup_state=latest_setup_state(events, last.timestamp)
    structure=evaluate_structure(raw, lookback=LOOKBACK, min_touches=3, break_buffer_pct=BREAK_BUFFER)
    friday_decision=evaluate_friday_decision(frame)
    structure_payload={"as_of":structure.as_of,
        "active_lines":list(structure.active_lines), "broken_lines":list(structure.broken_lines),
        "latest_support":structure.latest_support, "latest_resistance":structure.latest_resistance,
        "rebuild_policy":"recomputed from completed candles at each snapshot; broken lines retained as history, not active guides"}
    close_series = frame.close.astype(float)
    ema5_now = close_series.ewm(span=5, adjust=False).mean()
    ema13_now = close_series.ewm(span=13, adjust=False).mean()
    if len(frame) < 2:
        raise ValueError("At least two completed candles are required to build a strategy preview.")
    prev_bar, last_bar = frame.iloc[-2], frame.iloc[-1]
    stoch_down = bool(np.isfinite([prev_bar.stoch_k, prev_bar.stoch_d, last_bar.stoch_k, last_bar.stoch_d]).all()
                      and prev_bar.stoch_k >= prev_bar.stoch_d and last_bar.stoch_k < last_bar.stoch_d)
    stoch_up = bool(np.isfinite([prev_bar.stoch_k, prev_bar.stoch_d, last_bar.stoch_k, last_bar.stoch_d]).all()
                    and prev_bar.stoch_k <= prev_bar.stoch_d and last_bar.stoch_k > last_bar.stoch_d)
    short_ema_alignment = float(last.close) < min(float(ema5_now.iloc[-1]), float(last.ema8_4h), float(ema13_now.iloc[-1]), float(last.ema21_4h))
    long_ema_alignment = float(last.close) > max(float(ema5_now.iloc[-1]), float(last.ema8_4h), float(ema13_now.iloc[-1]), float(last.ema21_4h))
    daily_bias = str(last.daily_bias).lower()
    daily_bias_available = daily_bias in ("bullish", "bearish")
    # A completed 4H trendline break is the trigger. Stoch RSI, MACD and
    # short-term EMA positioning are context only, never entry gates.
    latest_break = next((
        event for event in reversed(events)
        if event.get("signal_time") == last.timestamp.isoformat()
        and event.get("event") == "BREAK_CLOSE_PROBE"
    ), None)
    execution_direction = str(latest_break.get("direction", "")).lower() if latest_break else None
    execution_blockers = []
    execution_trigger = {
        "signal_time": last.timestamp.isoformat(), "direction": execution_direction,
        "eligible": execution_direction is not None, "daily_bias": daily_bias,
        "close": float(last.close), "stoch_bearish_cross": stoch_down, "stoch_bullish_cross": stoch_up,
        "ema5": float(ema5_now.iloc[-1]), "ema8": float(last.ema8_4h),
        "ema13": float(ema13_now.iloc[-1]), "ema21": float(last.ema21_4h),
        "below_all_emas": bool(short_ema_alignment), "above_all_emas": bool(long_ema_alignment),
        "break_event": latest_break, "blockers": execution_blockers, "research_only": True,
    }
    result={"mode":"research_preview_no_orders","source":"Kraken public Spot OHLC API","pair":pair,
        "latest_setup_state":setup_state,
        "execution_trigger": execution_trigger,
        "interval_minutes":interval,"generated_at_utc":datetime.now(timezone.utc).isoformat(),
        "completed_candles":len(raw),"last_completed_candle_utc":last.timestamp.isoformat(),"walk_forward_split_time":walk_forward_split_time,"last_close":float(last.close),
        "trade_plans":{"long":build_trade_plan(frame, "long"),"short":build_trade_plan(frame, "short")},
        "closed_candles":[{"timestamp":pd.Timestamp(r.timestamp).isoformat(),"open":float(r.open),"high":float(r.high),"low":float(r.low),"close":float(r.close)} for r in raw.tail(100).itertuples(index=False)],
        "momentum_reversal": (
            scan_momentum_reversal(raw, pair=pair, thesis_direction=str(last.daily_bias))
            if momentum_reversal_enabled()
            else {"enabled": False, "eligible": False, "state": "DISABLED", "blockers": ["feature_disabled"]}
        ),
        "current_indicators":{"ema8_4h":_num(last.ema8_4h),"ema21_4h":_num(last.ema21_4h),
            "daily_bias":str(last.daily_bias),"stoch_rsi_k":_num(last.stoch_k),"stoch_rsi_d":_num(last.stoch_d),"atr14":_num(last.atr14)},
        "live_preview":status,"friday_decision":friday_decision,"reversal_context":reversal_context(frame),"structure_lifecycle":structure_payload,"break_events":events,"event_count":len(events),
        "consolidated_setups":setups,"consolidated_setup_count":len(setups),
        "trade_replay":trades,"structural_target_replay":trades_structural,"entry2_trade_replay":entry2_trades,"entry2_structural_target_replay":entry2_trades_structural,"structural_target_replay_summary":{
            "target_policy":"nearest prior structure; if unavailable/invalid, 1.272 Fib extension of prior range",
            "trades":len(trades_structural),"wins":sum(t["net_equity_return_pct"]>0 for t in trades_structural),
            "losses":sum(t["net_equity_return_pct"]<=0 for t in trades_structural),
            "net_equity_return_pct":round(sum(t["net_equity_return_pct"] for t in trades_structural),4),
            "mean_net_r":round(float(np.mean([t["net_r_multiple"] for t in trades_structural])),4) if trades_structural else None,
            "exit_reasons":{name:sum(t["exit_reason"]==name for t in trades_structural) for name in ("target","stop","time_exit")}},
        "trade_replay_summary":{
            "trades":len(trades),"wins":sum(t["net_equity_return_pct"]>0 for t in trades),
            "losses":sum(t["net_equity_return_pct"]<=0 for t in trades),
            "net_equity_return_pct":round(sum(t["net_equity_return_pct"] for t in trades),4),
            "mean_net_r":round(float(np.mean([t["net_r_multiple"] for t in trades])),4) if trades else None,
            "exit_reasons":{name:sum(t["exit_reason"]==name for t in trades) for name in ("target","stop","time_exit")}},
        "latest_momentum_context":momentum_context(frame,len(frame)-1),
        "event_summary":{name:sum(e["outcome"]==name for e in events) for name in
            ("retest_held_waiting_continuation","continuation_add_confirmed","failed_break","no_retest_within_window","pending","no_continuation_within_window")},
        "rules":{"probe":"closed 4H close >=0.15% beyond genuine three-touch line; Stoch RSI, MACD and 4H EMA alignment are context only",
            "entry_1":"small research probe candidate after a meaningful closed-candle trendline break; no live order",
            "retest":"within 18 bars / 72h, price revisits the broken line and closes on the breakout side",
            "entry_2":"only after a subsequent closed candle breaks the retest reaction high/low in breakout direction",
            "expiry":"if the retest does not happen in 72h, expire; if continuation does not confirm within 24h after retest, expire",
            "failure":"close crosses back through line by 0.30%","daily_bias_and_ema":"shown, not hard gates"},
        "trade_replay_rules":{"entry":"next 4H open after break signal","stop":"1.0 ATR from entry","baseline_target":"2R for comparison","alternative_target":"nearest prior structural level, otherwise 1.272 Fib extension of prior range","risk_per_trade_pct":0.5,
            "max_hold_bars":18,"costs_bps_per_side":{"fee":8,"slippage":5},
            "same_candle_stop_target":"stop assumed first; conservative OHLC approximation"},
        "limitations":["Kraken supplies at most 720 recent candles.","Walk-forward signals use only the prefix available at each candle.",
            "Daily bias is lagged one full UTC day; pivots must be confirmed before use.",
            "Forward returns are descriptive, not fee-aware. No stops, sizing, fees, slippage or orders.",
            "This is a fresh-data snapshot, not a continuously running alert service."]}
    out=Path(output_dir); out.mkdir(parents=True,exist_ok=True)
    decision_direction = {"long": "LONG", "short": "SHORT"}.get(friday_decision.get("direction"))
    decision_targets = friday_decision.get("target_candidates", [])
    if os.getenv("FRIDAY_AI_REVIEW_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}:
        model_review = review_decision({
            "pair": pair, "timeframe": f"{interval}m", "candle_time_utc": last.timestamp.isoformat(),
            "rule_decision": friday_decision, "setup_lifecycle": setup_state,
            "indicators": result["current_indicators"], "structure": structure_payload,
            "candidate_universe": {
                "latest_break_event": next((dict(e, strategy="friday-price-action-v2") for e in reversed(events) if e.get("signal_time") == last.timestamp.isoformat()), None),
                **({"momentum_reversal_candidate": result["momentum_reversal"]} if result["momentum_reversal"].get("enabled", True) else {}),
                "structural_trade_plans": result["trade_plans"],
                "exhaustion_context": result["reversal_context"],
                "momentum_context": result["latest_momentum_context"],
            },
        })
    else:
        model_review = {
            "status": "disabled", "provider": None, "model_id": None,
            "response": None, "latency_ms": 0, "error": None,
            "reason": "AI review disabled by FRIDAY_AI_REVIEW_ENABLED; deterministic rule gates remain authoritative.",
        }
    result["model_review"] = model_review
    journal_record = build_decision_record(
        pair=pair, timeframe=f"{interval}m",
        candle_timestamp=int(pd.Timestamp(last.timestamp).timestamp()),
        strategy_version="friday-price-action-v2",
        status=str(friday_decision.get("state", "NO_TRADE")),
        direction=decision_direction,
        entry=friday_decision.get("entry_reference_close"),
        stop=friday_decision.get("invalidation"),
        targets=decision_targets,
        evidence=friday_decision.get("evidence", {
            "candidate_origins": friday_decision.get("candidate_origins"),
            "candidate_evidence": friday_decision.get("candidate_evidence"),
        }),
        blockers=[str(friday_decision.get("reason", "No reason supplied"))],
        model=model_review,
    )
    append_decision(out/"decision_journal.jsonl", journal_record)
    (out/"strategy_preview.json").write_text(json.dumps(result,indent=2,allow_nan=False))
    # Snapshot JSONL datasets make the learning report reproducible from the same
    # candle window. The durable archive in Supabase retains lifecycle history.
    for filename, rows in (
        ("break_events.jsonl", events),
        ("consolidated_setups.jsonl", setups),
        ("trade_replay.jsonl", trades),
        ("structural_target_replay.jsonl", trades_structural),
        ("entry2_trade_replay.jsonl", entry2_trades),
        ("entry2_structural_target_replay.jsonl", entry2_trades_structural),
    ):
        write_jsonl(out / filename, rows)
    md=["# 4H Break Strategy Preview — "+pair,"",f"- Generated: {result['generated_at_utc']}",
        f"- Last completed candle: {last.timestamp.isoformat()}",f"- Last close: {float(last.close):.6f}",
        f"- Daily bias (lagged): {last.daily_bias}",f"- 4H EMA 8/21: {_num(last.ema8_4h)} / {_num(last.ema21_4h)}",
        f"- Stoch RSI K/D: {_num(last.stoch_k)} / {_num(last.stoch_d)}","","## Current preview",
        f"- State: {status.get('state')}",f"- Line: {status.get('line_kind','none')} at {status.get('line_price','n/a')}",
        f"- Latest setup lifecycle: {setup_state.get('state')} — {setup_state.get('message')}",
        f"- Friday decision: {friday_decision.get('state')} — {friday_decision.get('reason')}",
        f"- Fib bounce ranking: {friday_decision.get('bounce_level_ranking', [])}",
        f"- Distance: {status.get('distance_pct','n/a')}%",f"- Wick contacts: {status.get('touch_count','n/a')}",
        f"- Anchors: {status.get('anchor_start_time','n/a')} → {status.get('anchor_end_time','n/a')}","","## Replay summary",
        f"- Break-close events: {len(events)}"]+[f"- {k}: {v}" for k,v in result["event_summary"].items()]
    md += ["","| Break time | Direction | Daily bias | EMA aligned | Add time | Outcome | +12 bars |","|---|---|---|---:|---|---|---:|"]
    for e in events[-30:]:
        md.append(f"| {e['signal_time']} | {e['direction']} | {e['daily_bias']} | {e['ema_aligned']} | {e.get('add_signal_time','—')} | {e['outcome']} | {e.get('forward_12_bar_return_pct','n/a')}% |")
    md += ["","## Limits"]+["- "+x for x in result["limitations"]]
    (out/"strategy_preview.md").write_text("\n".join(md)+"\n")
    return result

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--pair",default="XBTUSD",choices=["XBTUSD","ETHUSD","SOLUSD","VIRTUALUSD"])
    p.add_argument("--interval",type=int,default=240)
    p.add_argument("--output-dir",default="results/strategy-preview")
    a=p.parse_args(); r=build_preview(a.pair,a.interval,a.output_dir)
    print(json.dumps({"pair":r["pair"],"completed_candles":r["completed_candles"],
        "last_completed_candle_utc":r["last_completed_candle_utc"],"last_close":r["last_close"],
        "live_preview":r["live_preview"],"event_count":r["event_count"],"event_summary":r["event_summary"],
        "output_dir":a.output_dir},indent=2))

if __name__=="__main__": main()
