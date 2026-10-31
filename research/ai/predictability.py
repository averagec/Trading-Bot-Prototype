"""Part 1c/1d: cheap-label predictability (direction and range of the next h H1 bars) and what survives the spread.

    python -m research.ai.predictability       # writes research/output/ai/predictability/*.csv|json

Cheap labels ignore costs and stop geometry; they say whether ANY directional information exists. They are the
feasibility screen only; the decision-relevant label is the net-R label of labels.py (learnability.py).
Family (pre-registered): 13 directional features x 3 horizons = 39 IC tests (Bonferroni z = 3.2), 7 volatility features
x 2 horizons = 14 IC tests, 2 models x 3 horizons = 6 out-of-fold AUC tests.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from research.ai import models as mdl
from research.ai.build import OUT
from research.ai.cv import half_year_blocks, walk_forward
from research.ai.features import N_FEATURES, S_FEATURES
from research.ai.stats import (auc_score, block_bootstrap, bootstrap_auc, corr_stats, group_sums, pearson_from_sums,
                               rank_unit)

PRED = OUT / "predictability"
HORIZONS = (1, 4, 24)
VOL_FEATURES = ["atr_pct", "vol_ratio", "h4_atr_pct", "hour_sin", "hour_cos", "spread_rel", "hrs_to_flat"]
COMM_PIPS = 0.5          # EURUSD-like round-trip commission, pips; other pairs differ by < 0.1 pip


def ic_table(df: pd.DataFrame, n_boot: int = 1000) -> pd.DataFrame:
    rows = []
    wk = df["week"].to_numpy()
    scale = df["atr_pips"].to_numpy()
    for h in HORIZONS:
        tgt = df[f"fwd{h}_pips"].to_numpy() / (scale * np.sqrt(h))
        ty = rank_unit(tgt)
        for f in S_FEATURES:
            fx = rank_unit(df[f].to_numpy(float))
            _, s = group_sums(wk, corr_stats(fx, ty))
            b = block_bootstrap(s, pearson_from_sums, n_boot, seed=h)
            rows.append({"family": "direction", "feature": f, "h": h, "n": int(s[:, 0].sum()), **b,
                         "z": b["est"] / b["se"]})
    for h in (4, 24):
        rng_rel = np.log(df[f"rng{h}_pips"].to_numpy() / df["atr_pips"].to_numpy())
        ty = rank_unit(rng_rel)
        for f in VOL_FEATURES:
            fx = rank_unit(df[f].to_numpy(float))
            _, s = group_sums(wk, corr_stats(fx, ty))
            b = block_bootstrap(s, pearson_from_sums, n_boot, seed=h)
            rows.append({"family": "range", "feature": f, "h": h, "n": int(s[:, 0].sum()), **b, "z": b["est"] / b["se"]})
    return pd.DataFrame(rows)


def oof_direction(df: pd.DataFrame, h: int, cols: list[str], model: str, blocks=None) -> np.ndarray:
    """Walk-forward out-of-fold P(next-h-bar move > 0). Rows lacking the outcome or a feature are skipped."""
    blocks = blocks or half_year_blocks()
    y = (df[f"fwd{h}_pips"].to_numpy() > 0).astype(float)
    ok = np.isfinite(df[f"fwd{h}_pips"].to_numpy()) & np.isfinite(df[cols].to_numpy(float)).all(axis=1)
    d = df.loc[ok, ["T"]].copy()
    d["exit_T"] = d["T"] + pd.Timedelta(hours=max(HORIZONS))                    # purge on the longest horizon
    X = df.loc[ok, cols].to_numpy(float)
    yy = y[ok]
    p = np.full(len(d), np.nan)
    for (a, b), tr, te in walk_forward(d["T"], d["exit_T"], blocks, 7):
        p[te] = mdl.fit_predict(model, X[tr], yy[tr], np.ones(len(tr)), [X[te]])[0]
    out = np.full(len(df), np.nan)
    out[np.flatnonzero(ok)] = p
    return out


def survive_spread(df: pd.DataFrame, h: int, score: np.ndarray, n_boot: int = 1000) -> dict:
    """Long the top decile / short the bottom decile of the OOF score; gross signed move vs spread + commission."""
    ok = np.isfinite(score) & np.isfinite(df[f"fwd{h}_pips"].to_numpy())
    s = score[ok]
    q_lo, q_hi = np.quantile(s, 0.1), np.quantile(s, 0.9)
    fwd = df[f"fwd{h}_pips"].to_numpy()[ok]
    cost = df["spread_pips"].to_numpy()[ok] + COMM_PIPS                       # one spread + round-trip commission
    atr = df["atr_pips"].to_numpy()[ok]
    wk = df["week"].to_numpy()[ok]
    sel = (s >= q_hi) | (s <= q_lo)
    sign = np.where(s >= q_hi, 1.0, -1.0)
    gross = np.where(sel, sign * fwd, np.nan)
    net = gross - np.where(sel, cost, np.nan)
    stop = 1.5 * atr
    res = {"h": h, "n_selected": int(sel.sum())}
    for name, x in (("gross_pips", gross), ("net_pips", net), ("gross_r_1.5atr", gross / stop),
                    ("net_r_1.5atr", net / stop)):
        okx = np.isfinite(x)
        _, st = group_sums(wk[okx], np.column_stack([np.ones(okx.sum()), x[okx]]))
        res[name] = block_bootstrap(st, lambda t: t[1] / t[0], n_boot, seed=h)
    res["mean_cost_pips"] = float(np.nanmean(np.where(sel, cost, np.nan)))
    res["mean_abs_move_over_cost"] = float(np.nanmean(np.abs(fwd)) / np.mean(cost))
    return res


def run(n_boot: int = 1000) -> dict:
    PRED.mkdir(parents=True, exist_ok=True)
    df = pd.read_parquet(OUT / "dataset_h1.parquet")
    df = df[np.isfinite(df[S_FEATURES + N_FEATURES].to_numpy(float)).all(axis=1)].reset_index(drop=True)
    ic = ic_table(df, n_boot)
    ic.to_csv(PRED / "ic.csv", index=False)
    summary = {"rows": int(len(df))}
    # unconditional size of the opportunity against cost
    summary["opportunity"] = {f"h{h}": {"mean_abs_move_pips": float(np.nanmean(np.abs(df[f'fwd{h}_pips']))),
                                        "median_spread_pips": float(df["spread_pips"].median()),
                                        "mean_range_pips": float(np.nanmean(df[f'rng{h}_pips']))} for h in HORIZONS}
    auc_rows, surv = [], []
    for model in ("LR", "HGB_S"):
        for h in HORIZONS:
            for tag, cols in (("S_only", S_FEATURES), ("S+N", S_FEATURES + N_FEATURES)):
                sc = oof_direction(df, h, cols, model)
                ok = np.isfinite(sc)
                y = (df[f"fwd{h}_pips"].to_numpy()[ok] > 0).astype(float)
                b = bootstrap_auc(y, sc[ok], df["week"].to_numpy()[ok], 200, seed=h)
                auc_rows.append({"model": model, "h": h, "features": tag, "n": int(ok.sum()), **b})
                if tag == "S_only":
                    r = survive_spread(df, h, sc, n_boot)
                    r["model"] = model
                    surv.append(r)
                print(model, h, tag, b, flush=True)
    pd.DataFrame(auc_rows).to_csv(PRED / "oof_auc.csv", index=False)
    summary["survive_spread"] = surv
    (PRED / "summary.json").write_text(json.dumps(summary, indent=1, default=float))
    return summary


if __name__ == "__main__":
    run()
