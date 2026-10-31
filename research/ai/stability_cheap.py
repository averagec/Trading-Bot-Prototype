"""Descriptive: by-year gross pips of the cheap-label decile rule (LR, S features), to see whether it is one lucky period.

    python -m research.ai.stability_cheap
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from research.ai.build import OUT
from research.ai.features import N_FEATURES, S_FEATURES
from research.ai.predictability import COMM_PIPS, oof_direction


def run() -> pd.DataFrame:
    df = pd.read_parquet(OUT / "dataset_h1.parquet")
    df = df[np.isfinite(df[S_FEATURES + N_FEATURES].to_numpy(float)).all(axis=1)].reset_index(drop=True)
    rows = []
    for h in (1, 4, 24):
        sc = oof_direction(df, h, S_FEATURES, "LR")
        ok = np.isfinite(sc) & np.isfinite(df[f"fwd{h}_pips"].to_numpy())
        q_lo, q_hi = np.quantile(sc[ok], 0.1), np.quantile(sc[ok], 0.9)
        sign = np.where(sc >= q_hi, 1.0, np.where(sc <= q_lo, -1.0, 0.0))
        sel = ok & (sign != 0)
        g = sign * df[f"fwd{h}_pips"].to_numpy()
        c = df["spread_pips"].to_numpy() + COMM_PIPS
        yr = df["T"].dt.year.to_numpy()
        for y in np.unique(yr[sel]):
            m = sel & (yr == y)
            rows.append({"h": h, "year": int(y), "n": int(m.sum()), "gross_pips": float(g[m].mean()),
                         "net_pips": float((g[m] - c[m]).mean())})
    out = pd.DataFrame(rows)
    out.to_csv(OUT / "predictability" / "cheap_by_year.csv", index=False)
    return out


if __name__ == "__main__":
    print(run().round(2).to_string(index=False))
