"""
Arm A - London sweep-and-reverse of the Asian range (EURUSD, GBPUSD).  Rules: research/reports/ideas_prereg.md.

Clock: Europe/London local time (DST-aware).  Asian range = M5 bars with local open time in [00:00, 07:00).
Decisions at closes of M5 bars with local open time in [07:00, 09:55].
"""

from __future__ import annotations

from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd

from research.ideas.common import ns, make_row, to_frame, M5

NAME = "A"
SYMBOLS = ["EURUSD", "GBPUSD"]
MIN_ASIA_BARS = 72            # of 84; only rejects holiday / broken data
MIN_RANGE_PIPS = 10.0         # = the simulator's minimum stop
EXIT_LOCAL_MIN = 16 * 60      # time stop 16:00 London local


@dataclass(frozen=True)
class Params:
    confirm_bars: int = 6         # structure break: close below the lowest low of the previous N M5 bars
    buffer_pips: float = 2.0      # stop beyond the sweep extreme
    excursion_cap: float = 1.0    # sweep deeper than cap x Asian range = trend break, voided
    rr: float = 2.0

    def key(self) -> dict:
        return asdict(self)


def generate(symbol: str, bars: dict, point: float, p: Params = Params()) -> pd.DataFrame:
    m5 = bars["M5"]
    if len(m5) < 100:
        return to_frame([])
    t5 = ns(m5)
    o, h, l, c = (m5[x].to_numpy(float) for x in ("open", "high", "low", "close"))
    spread5 = m5["spread_pts"].to_numpy(float)
    pip = point * 10.0

    loc = m5["time"].dt.tz_convert("Europe/London")
    minute = (loc.dt.hour * 60 + loc.dt.minute).to_numpy()
    day = loc.dt.tz_localize(None).values.astype("datetime64[D]")
    # contiguous day blocks (bars are time-sorted so equal local dates are contiguous)
    change = np.flatnonzero(day[1:] != day[:-1]) + 1
    starts = np.r_[0, change]
    ends = np.r_[change, len(day)]

    rows = []
    for s, e in zip(starts, ends):
        idx = np.arange(s, e)
        mi = minute[idx]
        if pd.Timestamp(day[s]).dayofweek >= 5:
            continue
        asia = idx[mi < 420]
        if len(asia) < MIN_ASIA_BARS:
            continue
        ah, al = h[asia].max(), l[asia].min()
        w = ah - al
        if w < MIN_RANGE_PIPS * pip:
            continue
        win = idx[(mi >= 420) & (mi <= 595)]
        if len(win) == 0:
            continue
        sh, slo = -np.inf, np.inf
        up_active = dn_active = False
        up_void = dn_void = False
        done = False
        for k in win:
            if k < p.confirm_bars:
                continue
            if h[k] > ah and not up_void:
                sh = max(sh, h[k]); up_active = True
                if sh - ah > p.excursion_cap * w:
                    up_void, up_active = True, False
            if l[k] < al and not dn_void:
                slo = min(slo, l[k]); dn_active = True
                if al - slo > p.excursion_cap * w:
                    dn_void, dn_active = True, False
            # the previous N bars must be contiguous M5 bars
            if t5[k] - t5[k - p.confirm_bars] != p.confirm_bars * M5:
                continue
            prev_low = l[k - p.confirm_bars:k].min()
            prev_high = h[k - p.confirm_bars:k].max()
            direction = None
            if up_active and c[k] < ah and c[k] < prev_low:
                direction, lvl = "SELL", sh + p.buffer_pips * pip
            elif dn_active and c[k] > al and c[k] > prev_high:
                direction, lvl = "BUY", slo - p.buffer_pips * pip
            if direction is None:
                continue
            exp_local = pd.Timestamp(day[s]).tz_localize("Europe/London") + pd.Timedelta(minutes=EXIT_LOCAL_MIN)
            r = make_row(symbol, NAME, k, t5, c, spread5, point, direction, sl_level=lvl,
                         expire=exp_local.tz_convert("UTC"), checks=3, tag=direction.lower())
            if r is not None:
                rows.append(r)
            done = True
            break          # first valid trigger per pair per day
        if done:
            continue
    return to_frame(rows)
