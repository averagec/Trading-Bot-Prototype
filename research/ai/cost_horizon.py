"""Descriptive: cost in R as a function of the decision timeframe (i.e. of the stop distance).

    python -m research.ai.cost_horizon

R is measured against a stop of max(10 pips, 1.5 x ATR14 of the decision timeframe); cost = spread + round-trip commission
(spread_clean_pts, commission 2 x $2.50 per lot). Wider stops (slower strategies) make the same pip cost a smaller part of R.
Also reports the share of bars where the 10-pip floor binds (the live bot's regime) and the mean absolute move of the
next bar / next day relative to cost. DEV only, no strategy P/L.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from research.ai import dataset as ds
from research.ai.build import OUT
from research.ai.indicators import atr
from research.ai.inventory import d1_from_h1


def run(k_atr: float = 1.5, floor_pips: float = 10.0) -> pd.DataFrame:
    specs = ds.load_specs()
    rows = []
    for sym in ds.FX5:
        spec = specs[sym]
        pip = ds.pip_size(spec)
        h1 = ds.load_bars(sym, "H1", start=ds.DEV_START)
        frames = {"M15": ds.load_bars(sym, "M15", start=ds.DEV_START), "H1": h1,
                  "H4": ds.load_bars(sym, "H4", start=ds.DEV_START)}
        d1 = d1_from_h1(h1)
        sp_day = h1.assign(day=d1_day_key(h1)).groupby("day")["spread_clean_pts"].median()
        d1["spread_clean_pts"] = sp_day.reindex(pd.to_datetime(d1["day"])).to_numpy()
        frames["D1"] = d1
        for tf, f in frames.items():
            a = atr(f["high"].to_numpy(), f["low"].to_numpy(), f["close"].to_numpy(), 14)
            stop = np.maximum(floor_pips * pip, k_atr * a)
            sp = f["spread_clean_pts"].to_numpy(float) * spec["point"]
            comm = ds.commission_pips(sym, spec, f["close"].to_numpy()) * pip
            cost_r = (sp + comm) / stop
            era = pd.cut(f["time"].dt.year, [2013, 2016, 2020, 2021], labels=["2014-16", "2017-20", "2021"])
            d = pd.DataFrame({"tf": tf, "symbol": sym, "era": era.astype(str), "cost_r": cost_r,
                              "stop_pips": stop / pip, "floor": stop <= floor_pips * pip * (1 + 1e-9)})
            rows.append(d[np.isfinite(d["cost_r"])])
    allr = pd.concat(rows)
    out = allr.groupby(["tf", "era"]).agg(cost_r_median=("cost_r", "median"), cost_r_mean=("cost_r", "mean"),
                                           stop_pips_median=("stop_pips", "median"), floor_share=("floor", "mean")).reset_index()
    tot = allr.groupby("tf").agg(cost_r_median=("cost_r", "median"), cost_r_mean=("cost_r", "mean"),
                                 stop_pips_median=("stop_pips", "median"), floor_share=("floor", "mean")).reset_index()
    tot["era"] = "ALL"
    out = pd.concat([out, tot], ignore_index=True)
    (OUT / "inventory").mkdir(parents=True, exist_ok=True)
    out.to_csv(OUT / "inventory" / "cost_by_horizon.csv", index=False)
    return out


def d1_day_key(h1: pd.DataFrame) -> pd.Series:
    from research.data import utc_to_server
    return pd.Series(utc_to_server(h1["time"]).dt.floor("D").to_numpy(), index=h1.index)


if __name__ == "__main__":
    pd.set_option("display.width", 160)
    print(run().round(4).to_string(index=False))
