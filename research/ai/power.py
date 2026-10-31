"""Power arithmetic for the net-R label (direction-neutral: uses only the label distribution, never a model).

    python -m research.ai.power

Reports the week-block standard error of the mean net R of (a) every bar (both sides pooled), (b) a random 10 % of bars
(what a top-decile rule would be judged on if it selected at random), plus the minimum detectable edge at 80 % power
(two-sided 5 %: 2.8 x SE) and the effective number of independent labels implied by the SE.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from research.ai.build import OUT, stack_sides
from research.ai.features import N_FEATURES, S_FEATURES
from research.ai.stats import block_bootstrap, group_sums


def run() -> dict:
    df = pd.read_parquet(OUT / "dataset_h1.parquet")
    st = stack_sides(df, S_FEATURES + N_FEATURES)
    st = st[st["T"] >= pd.Timestamp("2016-01-01", tz="UTC")]            # the out-of-fold period
    r = st["r"].to_numpy(float)
    wk = st["week"].to_numpy()
    sd = float(r.std(ddof=1))
    out = {"oof_rows": int(len(st)), "sd_r": sd, "mean_r": float(r.mean()), "weeks": int(len(np.unique(wk)))}
    _, s = group_sums(wk, np.column_stack([np.ones(len(r)), r]))
    b = block_bootstrap(s, lambda t: t[1] / t[0], 2000, 1)
    out["se_mean_all_bars"] = b["se"]
    out["n_eff_all_bars"] = (sd / b["se"]) ** 2
    out["mde_80_all_bars"] = 2.8 * b["se"]
    rng = np.random.default_rng(0)
    pick = rng.random(len(r)) < 0.10
    _, s10 = group_sums(wk[pick], np.column_stack([np.ones(pick.sum()), r[pick]]))
    b10 = block_bootstrap(s10, lambda t: t[1] / t[0], 2000, 2)
    out["se_mean_random_10pct"] = b10["se"]
    out["n_eff_10pct"] = (sd / b10["se"]) ** 2
    out["mde_80_random_10pct"] = 2.8 * b10["se"]
    # AUC: with n_eff independent labels and base rate pi, SE(AUC) ~ sqrt((1/12)(1/(pi n)+1/((1-pi) n)))
    pi = float((r > 0).mean())
    n = out["n_eff_all_bars"]
    out["base_rate"] = pi
    out["se_auc_approx"] = float(np.sqrt((1 / 12) * (1 / (pi * n) + 1 / ((1 - pi) * n))))
    out["mde_auc_80"] = 2.8 * out["se_auc_approx"]
    (OUT / "power.json").write_text(json.dumps(out, indent=1))
    return out


if __name__ == "__main__":
    print(json.dumps(run(), indent=1))
