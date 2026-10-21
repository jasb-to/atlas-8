"""Research-only setup consolidation and conservative OHLC trade replay."""
from __future__ import annotations
from collections.abc import Iterable
import pandas as pd


def consolidate_setups(events: Iterable[dict], candles: pd.DataFrame, *, cluster_bars: int = 3,
                       line_atr_tolerance: float = 0.75) -> list[dict]:
    """Collapse nearby same-direction line triggers in a short time cluster."""
    if cluster_bars < 0 or line_atr_tolerance < 0:
        raise ValueError("cluster parameters must be non-negative")
    if "timestamp" not in candles:
        raise ValueError("candles must include timestamp")
    ordered = sorted((dict(e) for e in events), key=lambda e: e["signal_time"])
    kept: list[dict] = []
    for event in ordered:
        found = candles.index[candles.timestamp == pd.Timestamp(event["signal_time"])]
        if not len(found):
            kept.append(event)
            continue
        idx = int(found[0])
        atr = float(candles.iloc[idx].get("atr14", 0) or 0)
        duplicate = None
        for j in range(len(kept)-1, -1, -1):
            prev = kept[j]
            pfound = candles.index[candles.timestamp == pd.Timestamp(prev["signal_time"])]
            if not len(pfound):
                continue
            pidx = int(pfound[0])
            gap = idx-pidx
            if gap > cluster_bars:
                break
            if event["direction"] != prev["direction"]:
                continue
            p_atr = float(candles.iloc[pidx].get("atr14", 0) or 0)
            scale = max(atr, p_atr, abs(float(event["line_price_at_break"]))*1e-6)
            if abs(float(event["line_price_at_break"])-float(prev["line_price_at_break"])) <= line_atr_tolerance*scale:
                duplicate = j
                break
        if duplicate is None:
            kept.append(event)
        else:
            prev = kept[duplicate]
            score = lambda e: (int(e.get("touch_count", 0)), bool(e.get("ema_aligned", False)))
            if score(event) > score(prev):
                event["consolidated_from"] = prev.get("consolidated_from", [prev["signal_time"]]) + [prev["signal_time"]]
                kept[duplicate] = event
            else:
                prev.setdefault("consolidated_from", []).append(event["signal_time"])
    return kept


def replay_trades(events: Iterable[dict], candles: pd.DataFrame, *, risk_fraction: float = 0.005,
                  reward_risk: float = 2.0, fee_bps: float = 8.0, slippage_bps: float = 5.0,
                  max_hold_bars: int = 18, stop_atr: float = 1.0, target_mode: str = "fixed_r") -> list[dict]:
    """Enter next candle open, size to fixed stop risk, and conservatively resolve OHLC hits."""
    if not 0 < risk_fraction <= 0.02 or reward_risk <= 0 or fee_bps < 0 or slippage_bps < 0 or max_hold_bars < 1 or stop_atr <= 0 or target_mode not in {"fixed_r", "structure_fib"}:
        raise ValueError("invalid replay parameters")
    required = {"timestamp", "open", "high", "low", "close", "atr14"}
    if not required.issubset(candles.columns):
        raise ValueError(f"candles missing columns: {sorted(required-set(candles.columns))}")
    out, cost = [], (fee_bps+slippage_bps)/10000
    for event in sorted(events, key=lambda e: e["signal_time"]):
        matches = candles.index[candles.timestamp == pd.Timestamp(event["signal_time"])]
        if not len(matches):
            continue
        si = int(matches[0]); ei = si+1
        if ei >= len(candles):
            continue
        atr = float(candles.iloc[si].atr14)
        if pd.isna(atr) or atr <= 0:
            continue
        side = 1 if event["direction"] == "long" else -1
        entry = float(candles.iloc[ei]["open"]) * (1+side*cost)
        stop = entry-side*stop_atr*atr
        target_type = "fixed_r"
        target = entry+side*reward_risk*stop_atr*atr
        if target_mode == "structure_fib":
            # Targets use candles strictly before the signal candle.
            prior = candles.iloc[max(0, si-90):si]
            if not prior.empty:
                if side == 1:
                    levels = sorted(float(x) for x in prior.high if float(x) > entry)
                    target = levels[0] if levels else float(prior.high.max())
                else:
                    levels = sorted((float(x) for x in prior.low if float(x) < entry), reverse=True)
                    target = levels[0] if levels else float(prior.low.min())
                target_type = "prior_structure"
                if (side == 1 and target <= entry) or (side == -1 and target >= entry):
                    lo, hi = float(prior.low.min()), float(prior.high.max())
                    span = max(hi-lo, atr)
                    target = hi + 0.272*span if side == 1 else lo - 0.272*span
                    target_type = "fib_1.272_extension"
            else:
                target = entry+side*1.272*atr
                target_type = "atr_proxy_extension_no_structure"
        exit_i = min(len(candles)-1, ei+max_hold_bars-1)
        exit_price = float(candles.iloc[exit_i]["close"]); reason = "time_exit"
        for i in range(ei, min(len(candles), ei+max_hold_bars)):
            row = candles.iloc[i]
            stop_hit = float(row.low) <= stop if side == 1 else float(row.high) >= stop
            target_hit = float(row.high) >= target if side == 1 else float(row.low) <= target
            if stop_hit:  # Stop wins if both levels occur in one OHLC candle.
                exit_i, exit_price, reason = i, stop, "stop"; break
            if target_hit:
                exit_i, exit_price, reason = i, target, "target"; break
        exit_fill = exit_price*(1-side*cost)
        unit_return = side*(exit_fill-entry)/entry
        position_fraction = risk_fraction*entry/abs(entry-stop)
        equity_pct = unit_return*position_fraction*100
        out.append({"signal_time":event["signal_time"],"direction":event["direction"],
            "entry_time":candles.iloc[ei].timestamp.isoformat(),"entry_price":entry,
            "stop_price":stop,"target_price":target,"target_type":target_type,"target_mode":target_mode,"exit_time":candles.iloc[exit_i].timestamp.isoformat(),
            "exit_price":exit_fill,"exit_reason":reason,"bars_held":exit_i-ei+1,
            "risk_fraction_pct":risk_fraction*100,"position_fraction_of_equity":position_fraction,
            "net_equity_return_pct":equity_pct,"net_r_multiple":equity_pct/(risk_fraction*100),
            "round_trip_cost_assumption_bps":2*(fee_bps+slippage_bps)})
    return out
