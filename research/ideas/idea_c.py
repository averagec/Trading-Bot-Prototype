"""
Arm C - prior-day-range breakout with a volatility-regime filter, in the direction of the H4 trend.
Rules: research/reports/ideas_prereg.md.  Day = UTC calendar day, decisions at H1 closes.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd

from research.ideas.common import ns, make_row, to_frame, ema, last_closed_idx, m5_index_closing_at, WARM_H4

NAME = "C"
SYMBOLS = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "XAUUSD"]
MIN_H1_BARS_PER_DAY = 18


@dataclass(frozen=True)
class Params:
    vol_filter: float = 1.0       # prior-day range >= vol_filter x ADR
    stop_adr: float = 0.25        # stop = stop_adr x ADR
    rr: float = 2.0
    adr_days: int = 10
    first_close_hour: int = 8     # tradable breakout closes: [08:00, 17:00] UTC
    last_close_hour: int = 17
    exit_hour: int = 21

    def key(self) -> dict:
        return asdict(self)


def generate(symbol: str, bars: dict, point: float, p: Params = Params()) -> pd.DataFrame:
    m5, h1, h4 = bars["M5"], bars["H1"], bars["H4"]
    if min(len(m5), len(h1), len(h4)) < 10:
        return to_frame([])
    t5, t1, t4 = ns(m5), ns(h1), ns(h4)
    c5, spread5 = m5["close"].to_numpy(float), m5["spread_pts"].to_numpy(float)
    h, l, c = (h1[x].to_numpy(float) for x in ("high", "low", "close"))
    e50, e200 = ema(h4["close"].to_numpy(float), 50), ema(h4["close"].to_numpy(float), 200)

    day = t1.astype("datetime64[D]")
    change = np.flatnonzero(day[1:] != day[:-1]) + 1
    starts, ends = np.r_[0, change], np.r_[change, len(day)]
    # per-day high/low/complete flag
    dhi = np.array([h[s:e].max() for s, e in zip(starts, ends)])
    dlo = np.array([l[s:e].min() for s, e in zip(starts, ends)])
    full = np.array([(e - s) >= MIN_H1_BARS_PER_DAY for s, e in zip(starts, ends)])
    dday = day[starts]
    drange = dhi - dlo
    complete_pos = np.flatnonzero(full)

    rows = []
    for di in range(len(starts)):
        # last complete day strictly before day di, and the ADR over the last adr_days complete days up to it
        prior = complete_pos[complete_pos < di]
        if len(prior) < p.adr_days:
            continue
        pdi = prior[-1]
        if (dday[di] - dday[pdi]).astype(int) > 5:           # stale: data hole
            continue
        adr = drange[prior[-p.adr_days:]].mean()
        if drange[pdi] < p.vol_filter * adr:
            continue
        if pd.Timestamp(dday[di]).dayofweek >= 5:
            continue
        pdh, pdl = dhi[pdi], dlo[pdi]
        s, e = starts[di], ends[di]
        for k in range(s, e):
            if c[k] > pdh or c[k] < pdl:                       # the breakout = first close outside the range
                Tk = t1[k] + np.timedelta64(60, "m")
                hr = int((Tk - Tk.astype("datetime64[D]")).astype("timedelta64[m]").astype(int)) // 60
                mn = int((Tk - Tk.astype("datetime64[D]")).astype("timedelta64[m]").astype(int)) % 60
                if mn != 0 or not (p.first_close_hour <= hr <= p.last_close_hour):
                    break
                i4 = int(last_closed_idx(t4, "H4", np.array([Tk]))[0])
                if i4 < WARM_H4:
                    break
                trend = 1 if e50[i4] > e200[i4] else (-1 if e50[i4] < e200[i4] else 0)
                direction = "BUY" if c[k] > pdh else "SELL"
                if (direction == "BUY") != (trend == 1) or trend == 0:
                    break
                j = int(m5_index_closing_at(t5, np.array([Tk]))[0])
                if j < 0:
                    break
                exp = pd.Timestamp(dday[di]).tz_localize("UTC") + pd.Timedelta(hours=p.exit_hour)
                r = make_row(symbol, NAME, j, t5, c5, spread5, point, direction, sl_dist=p.stop_adr * adr,
                             expire=exp, checks=3, tag=direction.lower())
                if r is not None:
                    rows.append(r)
                break
    return to_frame(rows)
