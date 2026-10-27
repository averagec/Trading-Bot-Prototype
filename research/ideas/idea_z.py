"""
Arm Z - trivial benchmark: every weekday at 07:00 UTC trade the sign of the H4 trend (EMA50 vs EMA200), ATR stop, 2R,
same exits as arm B.  Any complexity (B, C, A) has to beat this.  Rules: research/reports/ideas_prereg.md.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd

from research.ideas.common import (ns, make_row, to_frame, ema, atr, last_closed_idx, m5_index_closing_at, WARM_H4,
                                   WARM_H1)

NAME = "Z"
SYMBOLS = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "XAUUSD"]


@dataclass(frozen=True)
class Params:
    stop_mult: float = 1.5        # in H1 ATRs
    rr: float = 2.0
    hold_hours: float = 48.0
    decision_hour: int = 7

    def key(self) -> dict:
        return asdict(self)


def generate(symbol: str, bars: dict, point: float, p: Params = Params()) -> pd.DataFrame:
    m5, h1, h4 = bars["M5"], bars["H1"], bars["H4"]
    if min(len(m5), len(h1), len(h4)) < 10:
        return to_frame([])
    t5, t1, t4 = ns(m5), ns(h1), ns(h4)
    c5, spread5 = m5["close"].to_numpy(float), m5["spread_pts"].to_numpy(float)
    atr1 = atr(h1["high"].to_numpy(float), h1["low"].to_numpy(float), h1["close"].to_numpy(float), 14)
    c4 = h4["close"].to_numpy(float)
    e50, e200 = ema(c4, 50), ema(c4, 200)

    days = np.unique(t5.astype("datetime64[D]"))
    T = days.astype("datetime64[ns]") + np.timedelta64(p.decision_hour, "h")
    wd = (days.astype(int) + 3) % 7
    T = T[wd < 5]
    j = m5_index_closing_at(t5, T)
    i1 = last_closed_idx(t1, "H1", T)
    i4 = last_closed_idx(t4, "H4", T)
    rows = []
    for n in range(len(T)):
        if j[n] < 0 or i1[n] < WARM_H1 or i4[n] < WARM_H4:
            continue
        a = e50[i4[n]] - e200[i4[n]]
        if a == 0:
            continue
        direction = "BUY" if a > 0 else "SELL"
        Tk = pd.Timestamp(T[n], tz="UTC")
        r = make_row(symbol, NAME, int(j[n]), t5, c5, spread5, point, direction, sl_dist=p.stop_mult * atr1[i1[n]],
                     expire=Tk + pd.Timedelta(hours=p.hold_hours), checks=1, tag=direction.lower())
        if r is not None:
            rows.append(r)
    return to_frame(rows)
