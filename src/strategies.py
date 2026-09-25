from __future__ import annotations
import pandas as pd
from .indicators import ema,rsi,atr,supertrend,donchian_high,donchian_low,vwap,adx

def prepare(df):
    x=df.copy()
    x["ema200"]=ema(x.close,200)
    x["rsi"]=rsi(x.close,14)
    x["atr"]=atr(x,14)
    x["st"]=supertrend(x,10,3.0)
    x["dc_hi"]=donchian_high(x.high,20)
    x["dc_lo"]=donchian_low(x.low,20)
    x["vwap"]=vwap(x,24)
    x["adx"]=adx(x,14)
    x["atr_mean"]=x.atr.rolling(20).mean()
    return x

def trend_pullback(x):
    long_ = (x.close>x.ema200) & (x.st>0) & (x.rsi.shift(1)<=50) & (x.rsi>50)
    short_ = (x.close<x.ema200) & (x.st<0) & (x.rsi.shift(1)>=50) & (x.rsi<50)
    return long_,short_

def breakout(x):
    long_=(x.close>x.dc_hi)&(x.atr>x.atr_mean)&(x.close>x.ema200)
    short_=(x.close<x.dc_lo)&(x.atr>x.atr_mean)&(x.close<x.ema200)
    return long_,short_

def mean_reversion(x):
    long_=(x.adx<18)&(x.rsi<30)&(x.close<x.vwap)
    short_=(x.adx<18)&(x.rsi>70)&(x.close>x.vwap)
    return long_,short_

def hybrid(x):
    tp=trend_pullback(x); bo=breakout(x); mr=mean_reversion(x)
    trend=(x.adx>=25)
    range_=(x.adx<18)
    return (tp[0]&trend)|(bo[0]&trend)|(mr[0]&range_), (tp[1]&trend)|(bo[1]&trend)|(mr[1]&range_)
