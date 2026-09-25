from __future__ import annotations
import pandas as pd

def run(df, long_sig, short_sig, fee_bps=5, slippage_bps=2, stop_atr=1.0, tp_r=1.0):
    # Signal generated on closed candle; entry occurs at next candle open.
    trades=[]
    pos=None
    entry=stop=tp=0.0
    entry_time=None
    for i in range(1,len(df)):
        row=df.iloc[i]
        prev=df.iloc[i-1]
        if pos is None:
            if bool(long_sig.iloc[i-1]):
                pos="long"; entry=row.open*(1+(fee_bps+slippage_bps)/10000)
                stop=entry-stop_atr*prev.atr; tp=entry+tp_r*(entry-stop)
                entry_time=row.time
            elif bool(short_sig.iloc[i-1]):
                pos="short"; entry=row.open*(1-(fee_bps+slippage_bps)/10000)
                stop=entry+stop_atr*prev.atr; tp=entry-tp_r*(stop-entry)
                entry_time=row.time
        else:
            exit_price=None; reason=None
            if pos=="long":
                if row.low<=stop: exit_price=stop; reason="SL"
                elif row.high>=tp: exit_price=tp; reason="TP"
            else:
                if row.high>=stop: exit_price=stop; reason="SL"
                elif row.low<=tp: exit_price=tp; reason="TP"
            if exit_price is not None:
                gross=(exit_price-entry)/entry if pos=="long" else (entry-exit_price)/entry
                net=gross-2*(fee_bps+slippage_bps)/10000
                trades.append({"entry_time":entry_time,"exit_time":row.time,"side":pos,"entry":entry,"exit":exit_price,"return":net,"r":net/(stop_atr*prev.atr/entry)})
                pos=None
    return pd.DataFrame(trades)

def stats(t):
    if t.empty:
        return {"trades":0,"win_rate":None,"profit_factor":None,"net_return":0,"expectancy":None,"max_drawdown":None}
    wins=t[t["return"]>0]["return"]; losses=t[t["return"]<=0]["return"]
    eq=(1+t["return"]).cumprod()
    dd=eq/eq.cummax()-1
    pf=wins.sum()/abs(losses.sum()) if len(losses) else float("inf")
    return {"trades":len(t),"win_rate":round(100*len(wins)/len(t),2),"profit_factor":round(pf,3),"net_return":round(100*(eq.iloc[-1]-1),2),"expectancy":round(t["return"].mean()*100,4),"max_drawdown":round(dd.min()*100,2)}
