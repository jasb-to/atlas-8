from __future__ import annotations
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import json
from pathlib import Path
import pandas as pd
from src.strategies import prepare,trend_pullback,breakout,mean_reversion,hybrid
from src.backtest import run,stats
ROOT=Path(__file__).resolve().parents[1]
symbols=json.loads((ROOT/"config/universe.json").read_text())["symbols"]
strategies={"trend_pullback":trend_pullback,"breakout":breakout,"mean_reversion":mean_reversion,"hybrid":hybrid}
rows=[]
for sym in symbols:
  for tf in ["1d","4h_recent"]:
    p=ROOT/"data"/f"{sym}_{tf}.parquet"
    if not p.exists(): continue
    df=prepare(pd.read_parquet(p))
    for name,fn in strategies.items():
      lo,sh=fn(df)
      trades=run(df,lo,sh)
      rows.append({"asset":sym,"timeframe":tf,"strategy":name,**stats(trades)})
out=pd.DataFrame(rows)
(ROOT/"results").mkdir(exist_ok=True)
out.to_csv(ROOT/"results/summary.csv",index=False)
print(out.to_string(index=False))
