from __future__ import annotations
import pandas as pd

def run(df, long_sig, short_sig, fee_bps=5, slippage_bps=2,
        stop_atr=1.0, tp_r=1.5, exit_mode="fixed", risk_pct=1.0):
    trades=[]; pos=None
    entry=stop=tp=risk=0.0; entry_time=None
    for i in range(1,len(df)):
        row=df.iloc[i]; prev=df.iloc[i-1]
        if pos is None:
            if bool(long_sig.iloc[i-1]):
                pos="long"; entry=row.open*(1+(fee_bps+slippage_bps)/10000)
                risk=stop_atr*prev.atr; stop=entry-risk; tp=entry+tp_r*risk; entry_time=row.time
            elif bool(short_sig.iloc[i-1]):
                pos="short"; entry=row.open*(1-(fee_bps+slippage_bps)/10000)
                risk=stop_atr*prev.atr; stop=entry+risk; tp=entry-tp_r*risk; entry_time=row.time
        else:
            exit_price=None; reason=None
            if exit_mode=="vwap" and pd.notna(row.vwap):
                if pos=="long" and row.high>=row.vwap and row.vwap>entry: exit_price=row.vwap; reason="VWAP"
                elif pos=="short" and row.low<=row.vwap and row.vwap<entry: exit_price=row.vwap; reason="VWAP"
            if exit_price is None:
                if pos=="long":
                    if row.low<=stop: exit_price=stop; reason="SL"
                    elif row.high>=tp: exit_price=tp; reason="TP"
                else:
                    if row.high>=stop: exit_price=stop; reason="SL"
                    elif row.low<=tp: exit_price=tp; reason="TP"
            if exit_price is not None:
                gross=(exit_price-entry)/entry if pos=="long" else (entry-exit_price)/entry
                net=gross-2*(fee_bps+slippage_bps)/10000
                trades.append({"entry_time":entry_time,"exit_time":row.time,"side":pos,
                               "entry":entry,"exit":exit_price,"return":net,
                               "r":net/(risk/entry)})
                pos=None
    return pd.DataFrame(trades)

def stats(t):
    if t.empty:
        return {"trades":0,"win_rate":None,"profit_factor":None,"expectancy_r":None,
                "avg_win_r":None,"avg_loss_r":None,"max_drawdown_r":None}
    wins=t[t["r"]>0]["r"]; losses=t[t["r"]<=0]["r"]
    eq=(1+t["r"]*0.01).cumprod()
    dd=eq/eq.cummax()-1
    pf=wins.sum()/abs(losses.sum()) if len(losses) else float("inf")
    return {"trades":len(t),"win_rate":round(100*len(wins)/len(t),2),
            "profit_factor":round(pf,3),"expectancy_r":round(t["r"].mean(),4),
            "avg_win_r":round(wins.mean(),3) if len(wins) else None,
            "avg_loss_r":round(losses.mean(),3) if len(losses) else None,
            "max_drawdown_r":round(dd.min()*100,2)}
