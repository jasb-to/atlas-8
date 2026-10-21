"""Causal 4H wick-trendline break preview; research-only, never places orders."""
from __future__ import annotations
import argparse, json
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
import pandas as pd
from research.trendlines import find_wick_pivots, fit_touch_trendlines
from research.validate_kraken_trendlines import fetch_ohlc
from research.sheldon_strategy import consolidate_setups, replay_trades
from research.structure_lifecycle import evaluate_structure
from research.friday_decision import evaluate_friday_decision

LINE_TOL = 0.0015
BREAK_BUFFER = 0.0015
RETEST_TOL = 0.003
RETEST_BARS = 18              # 72 hours; allow the setup to take days
LOOKBACK = 180
MAX_LINE_AGE = 90
MAX_DISTANCE = 0.025

def indicators(frame):
    out = frame.copy()
    close = out.close.astype(float)
    out["ema8_4h"] = close.ewm(span=8, adjust=False).mean()
    out["ema21_4h"] = close.ewm(span=21, adjust=False).mean()
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

def line_retest(frame, line, break_i, i):
    """Return first post-break line contact, contact index, and close-hold status."""
    for j in range(break_i+1, min(i, break_i+RETEST_BARS)+1):
        row, lv = frame.iloc[j], line.value_at(j)
        tol = abs(lv)*RETEST_TOL
        if line.kind == "resistance":
            contact, held = float(row.low) <= lv+tol, float(row.close) >= lv
        else:
            contact, held = float(row.high) >= lv-tol, float(row.close) <= lv
        if contact: return True, j, held
    return False, None, False

def momentum_context(frame, i):
    """Causal momentum confirmation: Stoch RSI recross and positive/rising MACD."""
    if i < 2:
        return {"stoch_bullish_cross": False, "macd_bullish_expansion": False}
    row, prev = frame.iloc[i], frame.iloc[i-1]
    k, d, pk, pd_ = row.stoch_k, row.stoch_d, prev.stoch_k, prev.stoch_d
    cross = all(np.isfinite(x) for x in (k, d, pk, pd_)) and pk <= pd_ and k > d
    close = frame.close.astype(float)
    macd = close.ewm(span=12, adjust=False).mean() - close.ewm(span=26, adjust=False).mean()
    signal = macd.ewm(span=9, adjust=False).mean()
    hist = macd - signal
    expansion = hist.iloc[i] > 0 and hist.iloc[i] > hist.iloc[i-1] and macd.iloc[i] > signal.iloc[i]
    return {"stoch_bullish_cross": bool(cross), "macd_bullish_expansion": bool(expansion),
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

def causal_replay(raw):
    frame = indicators(raw.sort_values("timestamp").reset_index(drop=True))
    events, active = [], []
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
            if len(prefix)-1-line.end.confirmed_at <= MAX_LINE_AGE
            and abs(line.value_at(len(prefix)-1)-close)/close <= MAX_DISTANCE]
        candidates.sort(key=lambda line:(abs(line.value_at(len(prefix)-1)-close)/close,
            -len(line.touch_indices),-line.end.confirmed_at))
        # Dashboard state uses nearest line, but break detection scans all valid lines.
        wedge = structure_snapshot(prefix, candidates)
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
                seen_keys={(e["line_kind"],e.get("anchor_start_time"),e.get("anchor_end_time")) for e in events}
                if duplicate_key not in seen_keys:
                    events.append(event); active.append({"event":event,"line":break_line,"origin":start,"break_i":i})
                    if i == len(frame)-1 and break_line.kind == line.kind and break_line.start.index == line.start.index and break_line.end.index == line.end.index:
                        status["state"]="break_close_probe"
        # Manage each break against its original line coordinates; only future candles are outcomes.
        for a in list(active):
            age=i-a["break_i"]
            if age <= 0: continue
            e,line=a["event"],a["line"]
            e["forward_bars_checked"]=age
            global_start=a["origin"]+line.start.index
            lv=line.start.price+line.slope_per_bar*(i-global_start)
            row=frame.iloc[i]; tol=abs(lv)*RETEST_TOL; close_i=float(row.close)
            if line.kind=="resistance":
                contact=float(row.low)<=lv+tol; held=close_i>=lv
                invalid=close_i<lv-abs(lv)*0.003
            else:
                contact=float(row.high)>=lv-tol; held=close_i<=lv
                invalid=close_i>lv+abs(lv)*0.003
            if age<=RETEST_BARS and contact and held:
                e.update({"event_add":"RETEST_HOLD_ADD","add_signal_time":row.timestamp.isoformat(),
                    "add_close":close_i,"outcome":"retest_held","outcome_bars":age})
                active.remove(a); continue
            if invalid:
                e.update({"outcome":"failed_break","outcome_bars":age,"failure_time":row.timestamp.isoformat()})
                active.remove(a); continue
            if age>=RETEST_BARS:
                e.update({"outcome":"no_retest_within_window","outcome_bars":age})
                active.remove(a)
    for e in events:
        matches=frame.index[frame.timestamp == pd.Timestamp(e["signal_time"])]
        if not len(matches): continue
        si=int(matches[0]); entry=float(frame.iloc[si].close)
        for h in (3,6,12,18):
            end=min(len(frame)-1,si+h)
            if end>si:
                ret=(float(frame.iloc[end].close)-entry)/entry*(1 if e["direction"]=="long" else -1)
                e[f"forward_{h}_bar_return_pct"]=round(ret*100,4)
                e[f"forward_{h}_bar_end_time"]=frame.iloc[end].timestamp.isoformat()
        e["forward_returns_note"]="Directional close-to-close diagnostic; no fees, slippage, stops or sizing."
    return events,status,frame

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
    if age_hours > RETEST_BARS * 4:
        return {**base, "state": "EXPIRED", "message": "The 72-hour retest window has elapsed; this is not a current entry."}
    if latest.get("outcome") == "failed_break":
        return {**base, "state": "INVALIDATED", "message": "The breakout failed its close-based invalidation rule."}
    if latest.get("outcome") == "no_retest_within_window":
        return {**base, "state": "EXPIRED", "message": "No qualifying retest held within 72 hours."}
    if latest.get("outcome") == "retest_held":
        return {**base, "state": "RETEST_ADD_CONFIRMED", "add_signal_time": latest.get("add_signal_time"),
                "add_close": latest.get("add_close"),
                "message": "A post-break retest held on a closed 4H candle; add confirmation is historical, not an order."}
    return {**base, "state": "ENTRY_PROBE_WAITING_RETEST",
            "retest_deadline": latest.get("retest_deadline"),
            "message": "Closed-candle breakout confirmed; paper probe state, waiting for a controlled retest."}


def build_preview(pair="XBTUSD", interval=240, output_dir="results/strategy-preview"):
    raw=fetch_ohlc(pair=pair,interval=interval)
    events,status,frame=causal_replay(raw)
    setups=consolidate_setups(events,frame)
    trades=replay_trades(setups,frame)
    last=frame.iloc[-1]
    setup_state=latest_setup_state(events, last.timestamp)
    structure=evaluate_structure(raw, lookback=LOOKBACK, min_touches=3, break_buffer_pct=BREAK_BUFFER)
    friday_decision=evaluate_friday_decision(frame)
    structure_payload={"as_of":structure.as_of,
        "active_lines":list(structure.active_lines), "broken_lines":list(structure.broken_lines),
        "latest_support":structure.latest_support, "latest_resistance":structure.latest_resistance,
        "rebuild_policy":"recomputed from completed candles at each snapshot; broken lines retained as history, not active guides"}
    result={"mode":"research_preview_no_orders","source":"Kraken public Spot OHLC API","pair":pair,
        "latest_setup_state":setup_state,
        "interval_minutes":interval,"generated_at_utc":datetime.now(timezone.utc).isoformat(),
        "completed_candles":len(raw),"last_completed_candle_utc":last.timestamp.isoformat(),"last_close":float(last.close),
        "current_indicators":{"ema8_4h":_num(last.ema8_4h),"ema21_4h":_num(last.ema21_4h),
            "daily_bias":str(last.daily_bias),"stoch_rsi_k":_num(last.stoch_k),"stoch_rsi_d":_num(last.stoch_d),"atr14":_num(last.atr14)},
        "live_preview":status,"friday_decision":friday_decision,"structure_lifecycle":structure_payload,"break_events":events,"event_count":len(events),
        "consolidated_setups":setups,"consolidated_setup_count":len(setups),
        "trade_replay":trades,"trade_replay_summary":{
            "trades":len(trades),"wins":sum(t["net_equity_return_pct"]>0 for t in trades),
            "losses":sum(t["net_equity_return_pct"]<=0 for t in trades),
            "net_equity_return_pct":round(sum(t["net_equity_return_pct"] for t in trades),4),
            "mean_net_r":round(float(np.mean([t["net_r_multiple"] for t in trades])),4) if trades else None,
            "exit_reasons":{name:sum(t["exit_reason"]==name for t in trades) for name in ("target","stop","time_exit")}},
        "latest_momentum_context":momentum_context(frame,len(frame)-1),
        "event_summary":{name:sum(e["outcome"]==name for e in events) for name in
            ("retest_held","failed_break","no_retest_within_window","pending")},
        "rules":{"probe":"closed 4H close >=0.15% beyond genuine three-touch line",
            "add":"post-break retest within 18 bars / 72h, close holds breakout side",
            "failure":"close crosses back through line by 0.30%","daily_bias_and_ema":"shown, not hard gates"},
        "trade_replay_rules":{"entry":"next 4H open after break signal","stop":"1.0 ATR from entry","target":"2R","risk_per_trade_pct":0.5,
            "max_hold_bars":18,"costs_bps_per_side":{"fee":8,"slippage":5},
            "same_candle_stop_target":"stop assumed first; conservative OHLC approximation"},
        "limitations":["Kraken supplies at most 720 recent candles.","Walk-forward signals use only the prefix available at each candle.",
            "Daily bias is lagged one full UTC day; pivots must be confirmed before use.",
            "Forward returns are descriptive, not fee-aware. No stops, sizing, fees, slippage or orders.",
            "This is a fresh-data snapshot, not a continuously running alert service."]}
    out=Path(output_dir); out.mkdir(parents=True,exist_ok=True)
    (out/"strategy_preview.json").write_text(json.dumps(result,indent=2,allow_nan=False))
    md=["# 4H Break Strategy Preview — "+pair,"",f"- Generated: {result['generated_at_utc']}",
        f"- Last completed candle: {last.timestamp.isoformat()}",f"- Last close: {float(last.close):.6f}",
        f"- Daily bias (lagged): {last.daily_bias}",f"- 4H EMA 8/21: {_num(last.ema8_4h)} / {_num(last.ema21_4h)}",
        f"- Stoch RSI K/D: {_num(last.stoch_k)} / {_num(last.stoch_d)}","","## Current preview",
        f"- State: {status.get('state')}",f"- Line: {status.get('line_kind','none')} at {status.get('line_price','n/a')}",
        f"- Latest setup lifecycle: {setup_state.get('state')} — {setup_state.get('message')}",
        f"- Friday decision: {friday_decision.get('state')} — {friday_decision.get('reason')}",
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
