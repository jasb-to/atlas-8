from __future__ import annotations
import sys, json
from pathlib import Path
import numpy as np
import pandas as pd
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
from src.strategies import prepare, trend_pullback, breakout, mean_reversion, hybrid
from src.backtest import run, stats

symbols=json.loads((ROOT/"config/universe.json").read_text())["symbols"]
families={"trend_pullback":trend_pullback,"breakout":breakout,"mean_reversion":mean_reversion,"hybrid":hybrid}

def fingerprint(df):
    x=df.copy()
    r=x.close.pct_change()
    out={}
    out["bars"]=len(x)
    out["ann_vol_pct"]=r.std()*np.sqrt(6*365)*100 if len(x)>2 else np.nan
    out["atr_pct_median"]=float((x.atr/x.close*100).median())
    for lag in [1,3,6,12,24]:
        out[f"ret_autocorr_{lag}"]=float(r.autocorr(lag=lag))
    # directional persistence: next bar follows current bar sign
    s=np.sign(r)
    out["sign_follow_1"]=float((s.shift(-1)==s).iloc[:-1].mean())
    # trend persistence after a 3-bar directional move
    rr=x.close.pct_change(3)
    out["3bar_follow_4"]=float((np.sign(rr).shift(-1)==np.sign(rr)).iloc[:-1].mean())
    # excursion relative to ATR
    rng=(x.high-x.low)/x.atr
    out["median_range_atr"]=float(rng.replace([np.inf,-np.inf],np.nan).median())
    # fraction above/below 200 EMA
    out["pct_above_ema200"]=float((x.close>x.ema200).mean()*100)
    out["pct_adx_ge25"]=float((x.adx>=25).mean()*100)
    out["pct_adx_lt18"]=float((x.adx<18).mean()*100)
    # 20-bar breakout follow-through: return 3 bars after close breakout
    prev_hi=x.high.rolling(20).max().shift(1)
    prev_lo=x.low.rolling(20).min().shift(1)
    lb=x.close>prev_hi
    sb=x.close<prev_lo
    fwd=x.close.shift(-3)/x.close-1
    out["break_long_3bar_med_pct"]=float(fwd[lb].median()*100) if lb.any() else np.nan
    out["break_short_3bar_med_pct"]=float((-fwd[sb]).median()*100) if sb.any() else np.nan
    return out

def candidate_grid(name, fn, df):
    # Small, pre-declared grid. Selection happens only on the first 60%;
    # middle 20% is a confirmation window; final 20% is untouched holdout.
    grid=[(0.75,1.5),(1.0,1.5),(1.0,2.0),(1.5,2.0),(1.5,3.0)]
    n=len(df); a=int(n*.60); b=int(n*.80)
    train=df.iloc[:a]; confirm=df.iloc[a:b]; test=df.iloc[b:]
    c=[]
    for stop,tp in grid:
        mode="vwap" if name=="mean_reversion" else "fixed"
        s=stats(run(train,*fn(train),stop_atr=stop,tp_r=tp,exit_mode=mode))
        if s["trades"]>=10 and s["profit_factor"] is not None:
            c.append((s["profit_factor"],s["expectancy_r"],stop,tp,mode))
    if not c: return None
    c.sort(reverse=True)
    _,_,stop,tp,mode=c[0]
    cs=stats(run(confirm,*fn(confirm),stop_atr=stop,tp_r=tp,exit_mode=mode))
    ts=stats(run(test,*fn(test),stop_atr=stop,tp_r=tp,exit_mode=mode))
    return {"stop_atr":stop,"tp_r":tp,"exit":mode,
            "confirm_trades":cs["trades"],"confirm_pf":cs["profit_factor"],
            "confirm_exp_r":cs["expectancy_r"],"test_trades":ts["trades"],
            "test_win_rate":ts["win_rate"],"test_pf":ts["profit_factor"],
            "test_exp_r":ts["expectancy_r"],"test_dd_pct":ts["max_drawdown_r"]}

fp_rows=[]; result_rows=[]
for sym in symbols:
  for tf in ["1d","4h_recent"]:
    p=ROOT/"data"/f"{sym}_{tf}.parquet"
    if not p.exists(): continue
    df=prepare(pd.read_parquet(p)).dropna(subset=["atr","ema200","adx"])
    fp=fingerprint(df); fp.update({"asset":sym,"timeframe":tf}); fp_rows.append(fp)
    for name,fn in families.items():
      z=candidate_grid(name,fn,df)
      if z:
        z.update({"asset":sym,"timeframe":tf,"strategy":name}); result_rows.append(z)

(ROOT/"results").mkdir(exist_ok=True)
pd.DataFrame(fp_rows).to_csv(ROOT/"results/asset_fingerprints.csv",index=False)
pd.DataFrame(result_rows).to_csv(ROOT/"results/asset_strategy_confirmation.csv",index=False)
print("\n=== ASSET FINGERPRINTS ===")
print(pd.DataFrame(fp_rows).to_string(index=False))
print("\n=== 60/20/20 CONFIRMATION + HOLDOUT ===")
print(pd.DataFrame(result_rows).to_string(index=False))
