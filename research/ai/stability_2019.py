"""Descriptive (post hoc, flagged as such): where in 2019H2 does the cheap-label decile rule earn its gross pips?

    python -m research.ai.stability_2019
Splits h=4 LR decile trades of 2019H2 by decision hour (UTC) and by pair, and shows the share from rollover-hour decisions.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from research.ai.build import OUT
from research.ai.features import N_FEATURES, S_FEATURES
from research.ai.predictability import oof_direction


def run(h: int = 4) -> None:
    df = pd.read_parquet(OUT / "dataset_h1.parquet")
    df = df[np.isfinite(df[S_FEATURES + N_FEATURES].to_numpy(float)).all(axis=1)].reset_index(drop=True)
    sc = oof_direction(df, h, S_FEATURES, "LR")
    ok = np.isfinite(sc) & np.isfinite(df[f"fwd{h}_pips"].to_numpy())
    q_lo, q_hi = np.quantile(sc[ok], 0.1), np.quantile(sc[ok], 0.9)
    sign = np.where(sc >= q_hi, 1.0, np.where(sc <= q_lo, -1.0, 0.0))
    sel = ok & (sign != 0)
    d = pd.DataFrame({"T": df["T"], "sym": df["symbol"], "g": sign * df[f"fwd{h}_pips"].to_numpy(),
                      "ret1": df["ret1"], "hour": df["T"].dt.hour})[sel]
    d["period"] = np.where((d["T"] >= pd.Timestamp("2019-07-01", tz="UTC")) & (d["T"] < pd.Timestamp("2020-01-01", tz="UTC")),
                           "2019H2", "other")
    print(d.groupby("period")["g"].agg(["mean", "size"]).round(2))
    print(d[d["period"] == "2019H2"].groupby("hour")["g"].agg(["mean", "size"]).round(2).T.to_string())
    print(d[d["period"] == "2019H2"].groupby("sym")["g"].agg(["mean", "size"]).round(2).T.to_string())
    big = d[d["period"] == "2019H2"]
    print("share of 2019H2 gross pips from top 1% of trades:",
          round(float(np.sort(big["g"].to_numpy())[-max(1, len(big) // 100):].sum() / big["g"].sum()), 2))
    mon = big.groupby(big["T"].dt.strftime("%Y-%m"))["g"].agg(["mean", "size"]).round(2)
    print(mon.T.to_string())


if __name__ == "__main__":
    run()
