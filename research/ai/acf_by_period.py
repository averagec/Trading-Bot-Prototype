"""Descriptive: lag-1 autocorrelation of M5 / H1 returns and median recorded spread by half-year (pooled 5 FX pairs).

    python -m research.ai.acf_by_period
Purpose: check whether one period (2019H2 in the learnability result) has a different microstructure / data regime.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from research.ai import dataset as ds
from research.ai.inventory import lag_returns
from research.ai.stats import corr_stats, pearson_from_sums


def run() -> pd.DataFrame:
    rows = []
    for tf, step in (("M5", 5), ("H1", 60)):
        acc = {}
        for sym in ds.FX5:
            df = ds.load_bars(sym, tf, start=ds.DEV_START)
            t = df["time"].values.astype("datetime64[ns]")
            r1 = lag_returns(t, df["close"].to_numpy(), 1, step)
            ch = lag_returns(t, df["close"].to_numpy(), 2, step)
            x, y = np.where(np.isfinite(ch[1:]), r1[1:], np.nan), r1[:-1]
            per = (df["time"].dt.year * 10 + (df["time"].dt.month > 6)).to_numpy()[1:]
            # rank-transform within the pair-period would be nicer; Spearman-like robustness: clip at 8 robust sigmas
            sc = 1.4826 * np.nanmedian(np.abs(r1))
            x = np.clip(x, -8 * sc, 8 * sc)
            y = np.clip(y, -8 * sc, 8 * sc)
            cs = corr_stats(x, y)
            for p in np.unique(per):
                acc.setdefault(p, np.zeros(6))
                acc[p] += cs[per == p].sum(axis=0)
            sp = df.groupby((df["time"].dt.year * 10 + (df["time"].dt.month > 6)))["spread"].median()
            for p, v in sp.items():
                acc.setdefault(("sp", p), []).append(v)
        for p in sorted(k for k in acc if not isinstance(k, tuple)):
            rows.append({"tf": tf, "half_year": int(p), "rho1_clipped": float(pearson_from_sums(acc[p])),
                         "median_spread_pts_avg_pairs": float(np.mean(acc[("sp", p)]))})
    out = pd.DataFrame(rows)
    return out


if __name__ == "__main__":
    pd.set_option("display.width", 140)
    o = run()
    print(o.round(4).to_string(index=False))
    o.to_csv(ds.Path(__file__).resolve().parents[1] / "output" / "ai" / "inventory" / "acf_by_half_year.csv", index=False)
