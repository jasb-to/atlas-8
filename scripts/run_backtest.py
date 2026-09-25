from __future__ import annotations
import json
from pathlib import Path
import pandas as pd
from src.strategies import prepare,trend_pullback,breakout,mean_reversion,hybrid
from src.backtest import run,stats

ROOT=Path(__file__).resolve().parents[1]
universe=json.loads((ROOT/"config/universe.json").read_text())["symbols"]
strategies={"trend_pullback":trend_pullback,"breakout":breakout,"mean_reversion":mean_reversion,"hybrid":hybrid}
rows=[]
for symbol in universe:
    p=ROOT/"data"/f"{symbol}.parquet"
    if not p.exists(): continue
    df=prepare(pd.read_parquet(p))
    for name,fn in strategies.items():
        lo,sh=fn(df)
        trades=run(df,lo,sh)
        s=stats(trades)
        rows.append({"asset":symbol,"strategy":name,**s})
out=pd.DataFrame(rows)
(ROOT/"results").mkdir(exist_ok=True)
out.to_csv(ROOT/"results/summary.csv",index=False)
print(out.to_string(index=False))
