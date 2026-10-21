import pandas as pd
from research.sheldon_strategy import consolidate_setups, replay_trades


def frame():
    ts = pd.date_range("2026-01-01", periods=8, freq="4h", tz="UTC")
    return pd.DataFrame({"timestamp":ts,"open":[100,101,102,103,104,105,106,107],
        "high":[102,103,104,105,106,107,108,109],"low":[99,100,101,102,103,104,105,106],
        "close":[101,102,103,104,105,106,107,108],"atr14":[2.0]*8})


def event(i, direction="long", line=100.0, touches=3):
    return {"signal_time":frame().iloc[i].timestamp.isoformat(),"direction":direction,
        "line_price_at_break":line,"touch_count":touches,"ema_aligned":True}


def test_consolidates_nearby_breaks_but_keeps_later_setup():
    candles=frame()
    got=consolidate_setups([event(1),event(3,line=101,touches=4),event(7)],candles,
        cluster_bars=3,line_atr_tolerance=1.0)
    assert len(got)==2
    assert got[0]["touch_count"]==4


def test_opposite_direction_is_separate():
    assert len(consolidate_setups([event(1),event(2,"short")],frame()))==2


def test_replay_enters_next_open_and_stop_wins():
    candles=frame(); candles.loc[2,"low"]=90
    got=replay_trades([event(1)],candles)
    assert len(got)==1
    assert got[0]["entry_time"]==candles.iloc[2].timestamp.isoformat()
    assert got[0]["exit_reason"]=="stop"
    assert got[0]["net_equity_return_pct"]<0
