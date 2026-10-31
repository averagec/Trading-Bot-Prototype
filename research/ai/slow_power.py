"""Power of the slow-horizon design by planted-signal simulation (research/reports/ai_slow_prereg.md section 7).

    python -m research.ai.slow_power --variants D1_k15 H4_k10 --reps 8

Design: keep the REAL row structure of a variant (decision times, pairs, weeks, label end times, uniqueness weights,
the real net-R outcomes of both sides and therefore their dependence across pairs and days). Replace every feature by
synthetic noise and plant one directional feature f = gamma x (yL - yS) + N(0,1) from the real outcomes (yL, yS = side
wins). With unit-variance noise, the within-bar directional AUC of f is Phi(sqrt(2) x gamma). Then run the real pipeline
(logistic model, the same folds, purge/embargo, blocks, bootstraps) and count how often the pre-registered criteria
fire. No real FEATURE is used and no real feature->outcome relation is learned: the only information is the planted one.
"""
from __future__ import annotations

import argparse
import json
import time
from math import sqrt

import numpy as np
import pandas as pd
from scipy.stats import norm

from research.ai import slow as sl
from research.ai.build import OUT
from research.ai.learnability import metrics
from research.ai.slow_study import (ALL_FEATURES, ALPHA, PRIMARY_MODEL, by_group_stats, dataset_path, fit_oof,
                                    holm_lower_bound, stack_variant, MIN_POSITIVE_YEARS, AUC_FALSIFIER)

TARGET_AUCS = (0.50, 0.52, 0.54, 0.56, 0.60)


def gamma_for_auc(auc: float) -> float:
    return float(norm.ppf(auc) / sqrt(2.0))


def noise_frame(df: pd.DataFrame, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    d = df.copy()
    for c in sl.SLOW_S + sl.SLOW_N:
        d[c] = rng.normal(size=len(d))
    return d


def plant(df: pd.DataFrame, gamma: float, seed: int) -> pd.DataFrame:
    """Replace the directional features by noise and plant f in the first one (see module docstring)."""
    rng = np.random.default_rng(seed)
    d = df.copy()
    yL = (d["r_L"].to_numpy() > 0).astype(float)
    yS = (d["r_S"].to_numpy() > 0).astype(float)
    ok = d["valid_L"].to_numpy() & d["valid_S"].to_numpy()
    diff = np.where(ok, yL - yS, 0.0)
    for c in sl.SLOW_S:
        d[c] = rng.normal(size=len(d))
    d[sl.SLOW_S[0]] = gamma * diff + d[sl.SLOW_S[0]].to_numpy()
    return d


def run_variant(name: str, reps: int = 8, aucs=TARGET_AUCS, n_boot: int = 300, auc_boot: int = 40, base_seed: int = 100,
                df: pd.DataFrame | None = None, blks=None) -> dict:
    spec = {**sl.VARIANTS, **sl.SENSITIVITY}[name]
    df = pd.read_parquet(dataset_path(name)) if df is None else df
    d0 = noise_frame(df, base_seed)                          # N features: pure noise, fixed across replications
    st0 = stack_variant(plant(d0, 0.0, base_seed))
    B = fit_oof(st0, spec, PRIMARY_MODEL, cols=sl.SLOW_N, blks=blks)
    out = {"variant": name, "rows": int(len(st0)), "weeks": int(st0["week"].nunique()), "reps": reps, "by_auc": {}}
    for auc in aucs:
        g = gamma_for_auc(auc)
        recs = []
        for r in range(reps):
            t0 = time.time()
            d = plant(d0, g, base_seed + 1000 * (r + 1))
            st = stack_variant(d)
            F = fit_oof(st, spec, PRIMARY_MODEL, blks=blks)
            m = metrics(st, F["p"], B["p"], F["p0"], F["thr"], n_boot=n_boot, auc_boot=auc_boot)
            c1 = holm_lower_bound(st, F["p"], B["p"], alpha=ALPHA, n_boot=1500)
            c1b = holm_lower_bound(st, F["p"], B["p"], alpha=ALPHA, n_boot=1500, block=4)
            yr = by_group_stats(st, F["p"], B["p"], st["year"].to_numpy())
            share = float(np.mean([v["D1"] > 0 for v in yr["groups"].values()]))
            loyo = float(min(v["D1"] for v in yr["loo"].values()))
            era = by_group_stats(st, F["p"], B["p"], st["era"].to_numpy())
            n_era_ok = int(sum(v["dir_auc"] >= AUC_FALSIFIER for v in era["groups"].values()))
            recs.append({"dir_auc": m["dir_auc"], "dir_auc_lo": m["dir_auc_ci"]["lo"], "D1": m["D1_B1_minus_F"]["est"],
                         "C1": bool(c1["lower_bound"] > 0 and c1b["lower_bound"] > 0), "C1_week_only": bool(c1["lower_bound"] > 0), "gain": m["top"]["gain_vs_random_side"]["est"],
                         "gain_lo": m["top"]["gain_vs_random_side"]["lo"], "share_pos_years": share, "min_loyo": loyo,
                         "eras_ok": n_era_ok, "S": bool(share >= MIN_POSITIVE_YEARS and loyo > 0),
                         "F": bool(n_era_ok >= 2), "sec": round(time.time() - t0)})
        R = pd.DataFrame(recs)
        out["by_auc"][f"{auc:.2f}"] = {
            "gamma": g, "realised_dir_auc_median": float(R["dir_auc"].median()),
            "p_dir_auc_ci_excludes_half": float((R["dir_auc_lo"] > 0.5).mean()),
            "p_C1": float(R["C1"].mean()), "p_C1_week_only": float(R["C1_week_only"].mean()), "p_S": float(R["S"].mean()), "p_F": float(R["F"].mean()),
            "p_C1_S_F": float((R["C1"] & R["S"] & R["F"]).mean()),
            "top_gain_R_median": float(R["gain"].median()), "p_top_gain_lo_gt0": float((R["gain_lo"] > 0).mean()),
            "mean_share_positive_years": float(R["share_pos_years"].mean()), "sec_per_rep": float(R["sec"].mean())}
        print(name, f"{auc:.2f}", out["by_auc"][f"{auc:.2f}"], flush=True)
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--variants", nargs="+", default=list(sl.VARIANTS))
    ap.add_argument("--reps", type=int, default=8)
    a = ap.parse_args()
    (OUT / "slow").mkdir(parents=True, exist_ok=True)
    for n in a.variants:
        res = run_variant(n, a.reps)
        (OUT / "slow" / f"power_{n}.json").write_text(json.dumps(res, indent=1))
