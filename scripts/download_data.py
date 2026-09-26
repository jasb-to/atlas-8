from __future__ import annotations
import json, time
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd
import requests

ROOT=Path(__file__).resolve().parents[1]
cfg=json.loads((ROOT/"config/universe.json").read_text())
OUT=ROOT/"data"; OUT.mkdir(exist_ok=True); (OUT/"metadata").mkdir(exist_ok=True)
BINANCE="https://data-api.binance.vision/api/v3/klines"
HYPER="https://api.hyperliquid.xyz/info"
STEP={"1d":86400000,"4h":14400000}

def binance(symbol, interval, start, end):
    rows=[]; cur=start
    while cur < end:
        p={"symbol":symbol,"interval":interval,"startTime":cur,"endTime":end,"limit":1500}
        for a in range(8):
            r=requests.get(BINANCE,params=p,timeout=30)
            if r.status_code==429:
                time.sleep(min(60,2**a)); continue
            r.raise_for_status(); batch=r.json(); break
        else: raise RuntimeError("Binance rate limit persisted")
        if not batch: break
        rows.extend(batch); nxt=int(batch[-1][0])+STEP[interval]
        if nxt<=cur: break
        cur=nxt
        time.sleep(.15)
        if len(batch)<1500: break
    if not rows: raise RuntimeError(f"No Binance data for {symbol} {interval}")
    d=pd.DataFrame(rows,columns=["ts","open","high","low","close","volume","ct","qv","trades","tb","tq","x"])
    d["time"]=pd.to_datetime(d.ts,unit="ms",utc=True)
    for c in ["open","high","low","close","volume"]: d[c]=pd.to_numeric(d[c])
    return d[["time","open","high","low","close","volume"]].drop_duplicates("time").sort_values("time")

def hyper(coin, interval, start, end):
    body={"type":"candleSnapshot","req":{"coin":coin,"interval":interval,"startTime":start,"endTime":end}}
    r=requests.post(HYPER,json=body,timeout=30); r.raise_for_status(); data=r.json()
    if not data: raise RuntimeError(f"No Hyperliquid data for {coin} {interval}")
    d=pd.DataFrame(data)
    d["time"]=pd.to_datetime(d.t,unit="ms",utc=True)
    for c in ["o","h","l","c","v"]: d[c]=pd.to_numeric(d[c])
    return d.rename(columns={"o":"open","h":"high","l":"low","c":"close","v":"volume"})[["time","open","high","low","close","volume"]].drop_duplicates("time").sort_values("time")

now=pd.Timestamp.now(tz="UTC")
end=int(now.timestamp()*1000); start=int(datetime(2017,1,1,tzinfo=timezone.utc).timestamp()*1000)
for sym,spec in cfg["symbols"].items():
    if spec["source"]=="binance_futures":
        daily=binance(spec["symbol"],"1d",start,end)
        four=binance(spec["symbol"],"4h",start,end)
    else:
        daily=hyper(spec["symbol"],"1d",start,end)
        four=hyper(spec["symbol"],"4h",end-5000*STEP["4h"],end)
    daily=daily[daily.time+pd.Timedelta(days=1)<=now]
    four=four[four.time+pd.Timedelta(hours=4)<=now]
    daily.to_parquet(OUT/f"{sym}_1d.parquet",index=False)
    four.to_parquet(OUT/f"{sym}_4h_recent.parquet",index=False)
    (OUT/"metadata"/f"{sym}.json").write_text(json.dumps({
        "asset":sym,"source":spec["source"],"market":spec["symbol"],
        "daily_rows":len(daily),"four_hour_rows":len(four),
        "daily_start":daily.time.min().isoformat() if not daily.empty else None,
        "daily_end":daily.time.max().isoformat() if not daily.empty else None,
        "four_hour_start":four.time.min().isoformat() if not four.empty else None,
        "four_hour_end":four.time.max().isoformat() if not four.empty else None,
        "note":"Exact exchange OHLCV candles. No CoinGecko API key required."
    },indent=2))
    print(f"{sym}: daily={len(daily):,} 4h={len(four):,} source={spec['source']}")
