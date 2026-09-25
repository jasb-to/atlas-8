from __future__ import annotations
import json,time
from datetime import datetime,timezone,timedelta
from pathlib import Path
import requests,pandas as pd

ROOT=Path(__file__).resolve().parents[1]
cfg=json.loads((ROOT/"config/universe.json").read_text())
OUT=ROOT/"data"; OUT.mkdir(exist_ok=True); (OUT/"metadata").mkdir(exist_ok=True)
BASE="https://api.coingecko.com/api/v3/coins/{}/market_chart/range"

def get(coin,start,end,interval=None):
    p={"vs_currency":"usd","from":start.timestamp(),"to":end.timestamp()}
    if interval: p["interval"]=interval
    for attempt in range(8):
        r=requests.get(BASE.format(coin),params=p,timeout=60)
        if r.status_code==429:
            time.sleep(min(60,2**attempt)); continue
        r.raise_for_status()
        j=r.json()
        px=pd.DataFrame(j["prices"],columns=["ts","close"])
        vol=pd.DataFrame(j.get("total_volumes",[]),columns=["ts","volume"])
        px["time"]=pd.to_datetime(px.ts,unit="ms",utc=True)
        if not vol.empty:
            vol["time"]=pd.to_datetime(vol.ts,unit="ms",utc=True)
            px=px.merge(vol[["time","volume"]],on="time",how="left")
        else: px["volume"]=0.0
        return px[["time","close","volume"]].drop_duplicates("time").sort_values("time")
    raise RuntimeError("CoinGecko rate limit persisted")

def pseudo_ohlc(x,freq):
    # CoinGecko market_chart supplies sampled prices rather than exchange candles.
    # We aggregate sampled observations into OHLC for reproducible research.
    z=x.set_index("time")
    o=z["close"].resample(freq).first()
    h=z["close"].resample(freq).max()
    l=z["close"].resample(freq).min()
    c=z["close"].resample(freq).last()
    v=z["volume"].resample(freq).sum()
    return pd.DataFrame({"time":o.index,"open":o.values,"high":h.values,"low":l.values,"close":c.values,"volume":v.values}).dropna()

end=datetime.now(timezone.utc); start=end-timedelta(days=3650)
for sym,coin in cfg["symbols"].items():
    # Full history: daily auto-granularity.
    d=get(coin,start,end)
    daily=pseudo_ohlc(d,"1D")
    daily.to_parquet(OUT/f"{sym}_1d.parquet",index=False)

    # Recent 100 days: explicit hourly, then aggregate to 4H.
    s=end-timedelta(days=100)
    h=get(coin,s,end,interval="hourly")
    four=pseudo_ohlc(h,"4h")
    four.to_parquet(OUT/f"{sym}_4h_recent.parquet",index=False)

    (OUT/"metadata"/f"{sym}.json").write_text(json.dumps({
        "coin_id":coin,"daily_start":start.isoformat(),"end":end.isoformat(),
        "four_hour_start":s.isoformat(),"four_hour_end":end.isoformat(),
        "source":"CoinGecko /coins/{id}/market_chart/range",
        "four_hour_note":"4H candles are aggregated from CoinGecko hourly sampled market-chart observations."
    },indent=2))
