from __future__ import annotations
import sys, json
from pathlib import Path
import pandas as pd
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
from src.strategies import prepare,trend_pullback,breakout,mean_reversion,hybrid
from src.backtest import run,stats

symbols=json.loads((ROOT/"config/universe.json").read_text())["symbols"]
strategies={"trend_pullback":trend_pullback,"breakout":breakout,"mean_reversion":mean_reversion,"hybrid":hybrid}

# One decisive, pre-declared research grid. Parameters are selected on the first
# 70% of each series and then evaluated once on the untouched final 30%.
grid=[(0.75,1.0),(0.75,1.5),(0.75,2.0),(1.0,1.0),(1.0,1.5),(1.0,2.0),(1.5,1.0),(1.5,1.5),(1.5,2.0)]
rows=[]

def evaluate(df, fn, stop, tp, mode="fixed"):
    lo,sh=fn(df)
    return stats(run(df,lo,sh,stop_atr=stop,tp_r=tp,exit_mode=mode))

for sym in symbols:
  for tf in ["1d","4h_recent"]:
    p=ROOT/"data"/f"{sym}_{tf}.parquet"
    if not p.exists(): continue
    df=prepare(pd.read_parquet(p)).dropna(subset=["atr"])
    split=max(100,int(len(df)*0.70))
    train=df.iloc[:split].copy(); test=df.iloc[split:].copy()
    for name,fn in strategies.items():
      # Mean reversion has a natural VWAP target; the grid still tests its stop.
      candidates=[]
      for stop,tp in grid:
        mode="vwap" if name=="mean_reversion" else "fixed"
        s=evaluate(train,fn,stop,tp,mode)
        if s["trades"]>=10 and s["profit_factor"] is not None:
          candidates.append((s["profit_factor"],s["expectancy_r"],stop,tp,mode,s["trades"]))
      if not candidates:
        continue
      candidates.sort(key=lambda z:(z[0],z[1]),reverse=True)
      _,_,stop,tp,mode,train_trades=candidates[0]
      ts=evaluate(test,fn,stop,tp,mode)
      rows.append({"asset":sym,"timeframe":tf,"strategy":name,
                   "stop_atr":stop,"tp_r":tp,"exit":mode,
                   "train_trades":train_trades,
                   "test_trades":ts["trades"],"test_win_rate":ts["win_rate"],
                   "test_profit_factor":ts["profit_factor"],
                   "test_expectancy_r":ts["expectancy_r"],
                   "test_max_dd_pct":ts["max_drawdown_r"]})

out=pd.DataFrame(rows)
(ROOT/"results").mkdir(exist_ok=True)
out.to_csv(ROOT/"results/walk_forward.csv",index=False)
print(out.to_string(index=False))
