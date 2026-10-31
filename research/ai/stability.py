"""Descriptive stability of the out-of-fold result (no new fits): directional AUC and D1 by half-year block and by pair.

    python -m research.ai.stability
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from research.ai.build import OUT, stack_sides
from research.ai.features import N_FEATURES, S_FEATURES
from research.ai.learnability import LEARN, pair_up
from research.ai.stats import auc_score, logloss


def run(models=("LR", "HGB_S", "HGB_M")) -> dict:
    df = pd.read_parquet(OUT / "dataset_h1.parquet")
    st = stack_sides(df, S_FEATURES + N_FEATURES)
    res = {}
    T = pd.to_datetime(st["T"], utc=True)
    blk = (T.dt.year * 10 + (T.dt.month > 6)).to_numpy()
    for m in models:
        o = pd.read_parquet(LEARN / f"oof_{m}.parquet").set_index("rid").reindex(st["rid"])
        pF, pB, p0 = o["pF"].to_numpy(), o["pB"].to_numpy(), o["p0"].to_numpy()
        ev = np.isfinite(pF)
        y = st["y"].to_numpy(float)
        d1_row = np.where(ev, logloss(y, pB) - logloss(y, pF), np.nan)
        rec = {"by_block": {}, "by_symbol": {}}
        pr = pair_up(st[ev].assign(_thr=0.0), pF[ev])
        sym = st["symbol"].to_numpy()[ev]
        rid_bar = st["rid"].to_numpy()[ev] // 2
        bar_sym = pd.Series(sym, index=rid_bar)
        bar_sym = bar_sym[~bar_sym.index.duplicated()]
        bar_blk = pd.Series(blk[ev], index=rid_bar)
        bar_blk = bar_blk[~bar_blk.index.duplicated()]
        pr["sym"] = bar_sym.reindex(pr["bar"]).to_numpy()
        pr["blk"] = bar_blk.reindex(pr["bar"]).to_numpy()
        diff = pr["yL"] - pr["yS"]
        for key, col in (("by_block", "blk"), ("by_symbol", "sym")):
            for v, g in pr.groupby(col):
                dd = (g["yL"] - g["yS"])
                s = dd != 0
                rec[key][str(v)] = {"dir_auc": auc_score((dd[s] > 0).to_numpy(), (g["pL"] - g["pS"])[s].to_numpy()),
                                    "n": int(s.sum())}
        for v in np.unique(blk[ev]):
            rec["by_block"].setdefault(str(v), {})["D1"] = float(np.nanmean(d1_row[blk == v]))
        for s_ in np.unique(sym):
            rec["by_symbol"].setdefault(str(s_), {})["D1"] = float(np.nanmean(d1_row[st["symbol"].to_numpy() == s_]))
        keep = pr["blk"] != 20191
        dd = (pr["yL"] - pr["yS"])[keep]
        s2 = dd != 0
        rec["excl_2019H2"] = {"dir_auc": auc_score((dd[s2] > 0).to_numpy(), (pr["pL"] - pr["pS"])[keep][s2].to_numpy()),
                              "D1": float(np.nanmean(d1_row[blk != 20191]))}
        res[m] = rec
    (LEARN / "stability.json").write_text(json.dumps(res, indent=1))
    return res


if __name__ == "__main__":
    r = run()
    for m, rec in r.items():
        print(m)
        print("  excl_2019H2", rec["excl_2019H2"])
        for k in ("by_block", "by_symbol"):
            print(" ", k, {a: (round(b.get("dir_auc", np.nan), 3), round(b.get("D1", np.nan) * 1e3, 2)) for a, b in rec[k].items()})
