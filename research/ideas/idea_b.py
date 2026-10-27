"""
Arm B - higher-timeframe trend pullback (deliberately simple).  Rules: research/reports/ideas_prereg.md.

H4 regime (EMA50/EMA200 + ADX14 > thr)  ->  H1 pullback zone around EMA20 (depth in H1 ATR14)  ->  M15 close trigger.
Decisions at M15 closes with T in (07:00, 20:00] UTC.  Stop = stop_mult x ATR14(H1).
"""

from __future__ import annotations

from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd

from research.ideas.common import (ns, make_row, to_frame, enforce_gap, ema, atr, adx, last_closed_idx,
                                   m5_index_closing_at, WARM_H4, WARM_H1)

NAME = "B"
SYMBOLS = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "XAUUSD"]


@dataclass(frozen=True)
class Params:
    adx_thr: float = 20.0
    zone_depth: float = 1.0       # in H1 ATRs
    stop_mult: float = 1.5        # in H1 ATRs
    rr: float = 2.0
    hold_hours: float = 48.0
    min_gap_hours: float = 4.0

    def key(self) -> dict:
        return asdict(self)


def h4_trend(h4: pd.DataFrame, adx_thr: float) -> np.ndarray:
    """+1 / -1 / 0 per H4 bar from that bar's close (to be indexed at the last CLOSED H4 bar)."""
    c, h, l = (h4[x].to_numpy(float) for x in ("close", "high", "low"))
    e50, e200, a = ema(c, 50), ema(c, 200), adx(h, l, c, 14)
    t = np.zeros(len(c), int)
    t[(e50 > e200) & (a > adx_thr)] = 1
    t[(e50 < e200) & (a > adx_thr)] = -1
    return t


def generate(symbol: str, bars: dict, point: float, p: Params = Params()) -> pd.DataFrame:
    m5, m15, h1, h4 = (bars[k] for k in ("M5", "M15", "H1", "H4"))
    if min(len(m5), len(m15), len(h1), len(h4)) < 10:
        return to_frame([])
    t5, t15, t1, t4 = ns(m5), ns(m15), ns(h1), ns(h4)
    c5, spread5 = m5["close"].to_numpy(float), m5["spread_pts"].to_numpy(float)
    o15, h15, l15, c15 = (m15[x].to_numpy(float) for x in ("open", "high", "low", "close"))

    trend4 = h4_trend(h4, p.adx_thr)
    ema20_1 = ema(h1["close"].to_numpy(float), 20)
    atr1 = atr(h1["high"].to_numpy(float), h1["low"].to_numpy(float), h1["close"].to_numpy(float), 14)

    T = t15 + np.timedelta64(15, "m")                   # the M15 bar k closes at T[k]
    i1 = last_closed_idx(t1, "H1", T)
    i4 = last_closed_idx(t4, "H4", T)
    j5 = m5_index_closing_at(t5, T)
    mins = (T - T.astype("datetime64[D]")).astype("timedelta64[m]").astype(int)
    wd = ((T.astype("datetime64[D]").astype(int) + 3) % 7)       # 1970-01-01 was Thursday -> Mon=0
    session = (mins > 7 * 60) & (mins <= 20 * 60) & (wd < 5)

    ok = (i1 >= WARM_H1) & (i4 >= WARM_H4) & (j5 >= 0) & session
    ok[0] = False
    contiguous = np.r_[False, (t15[1:] - t15[:-1]) == np.timedelta64(15, "m")]
    ok &= contiguous

    i1c, i4c = np.maximum(i1, 0), np.maximum(i4, 0)
    tr, E, A = trend4[i4c], ema20_1[i1c], atr1[i1c]
    pc = np.r_[np.nan, c15[:-1]]
    ph = np.r_[np.nan, h15[:-1]]
    pl = np.r_[np.nan, l15[:-1]]
    d = p.zone_depth * A
    long_ = ok & (tr == 1) & (pc <= E) & (pc >= E - d) & (c15 > o15) & (c15 > ph)
    short_ = ok & (tr == -1) & (pc >= E) & (pc <= E + d) & (c15 < o15) & (c15 < pl)

    rows = []
    for k in np.flatnonzero(long_ | short_):
        direction = "BUY" if long_[k] else "SELL"
        Tk = pd.Timestamp(T[k], tz="UTC")
        r = make_row(symbol, NAME, int(j5[k]), t5, c5, spread5, point, direction, sl_dist=p.stop_mult * A[k],
                     expire=Tk + pd.Timedelta(hours=p.hold_hours), checks=4, tag=direction.lower())
        if r is not None:
            rows.append(r)
    return to_frame(enforce_gap(rows, p.min_gap_hours))
