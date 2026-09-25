from __future__ import annotations
import numpy as np
import pandas as pd

def rsi(close, n=14):
    d = close.diff()
    up = d.clip(lower=0)
    dn = -d.clip(upper=0)
    rs = up.ewm(alpha=1/n, adjust=False).mean() / dn.ewm(alpha=1/n, adjust=False).mean()
    return 100 - 100/(1+rs)

def atr(df, n=14):
    pc = df.close.shift()
    tr = pd.concat([(df.high-df.low), (df.high-pc).abs(), (df.low-pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1/n, adjust=False).mean()

def ema(close, n):
    return close.ewm(span=n, adjust=False).mean()

def donchian_high(high, n):
    return high.shift(1).rolling(n).max()

def donchian_low(low, n):
    return low.shift(1).rolling(n).min()

def vwap(df, n=24):
    pv = df.close * df.volume
    return pv.rolling(n).sum() / df.volume.rolling(n).sum()

def adx(df, n=14):
    up = df.high.diff()
    dn = -df.low.diff()
    plus = np.where((up > dn) & (up > 0), up, 0.0)
    minus = np.where((dn > up) & (dn > 0), dn, 0.0)
    a = atr(df,n)
    pdi = 100 * pd.Series(plus,index=df.index).ewm(alpha=1/n,adjust=False).mean()/a
    mdi = 100 * pd.Series(minus,index=df.index).ewm(alpha=1/n,adjust=False).mean()/a
    dx = 100*(pdi-mdi).abs()/(pdi+mdi).replace(0,np.nan)
    return dx.ewm(alpha=1/n,adjust=False).mean()

def supertrend(df, atr_n=10, multiplier=3.0):
    a = atr(df, atr_n)
    hl2 = (df.high + df.low)/2
    upper = hl2 + multiplier*a
    lower = hl2 - multiplier*a
    fu = upper.copy()
    fl = lower.copy()
    direction = pd.Series(1,index=df.index,dtype="int64")
    for i in range(1,len(df)):
        fu.iloc[i] = upper.iloc[i] if (upper.iloc[i] < fu.iloc[i-1] or df.close.iloc[i-1] > fu.iloc[i-1]) else fu.iloc[i-1]
        fl.iloc[i] = lower.iloc[i] if (lower.iloc[i] > fl.iloc[i-1] or df.close.iloc[i-1] < fl.iloc[i-1]) else fl.iloc[i-1]
        if direction.iloc[i-1] < 0 and df.close.iloc[i] > fu.iloc[i]:
            direction.iloc[i] = 1
        elif direction.iloc[i-1] > 0 and df.close.iloc[i] < fl.iloc[i]:
            direction.iloc[i] = -1
        else:
            direction.iloc[i] = direction.iloc[i-1]
    return direction
