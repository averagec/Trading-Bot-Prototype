"""Assemble the DEV dataset of H1 decision bars: causal features + stateless net-R labels + uniqueness weights.

    python -m research.ai.build            # writes research/output/ai/dataset_h1.parquet and a label summary

One row per (symbol, decision bar). `stack_sides` turns it into the model frame (a long row and a mirrored short row
per bar). Nothing at or after 2021-06-01 is loaded (dataset.load_bars refuses).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from research.ai import dataset as ds
from research.ai.features import all_pair_features, mirror, forward_stats, S_FEATURES, N_FEATURES
from research.ai.labels import LabelSpec, label_pair
from research.ai.stats import week_index

OUT = Path(__file__).resolve().parents[1] / "output" / "ai"
FIRST_DECISION = "2014-03-01"      # H1/H4 history before 2014 is warm-up only (older clocks fail the quality report)


def build_dataset(symbols=ds.FX5, ls: LabelSpec = LabelSpec(), end: str = ds.DEV_END,
                  first_decision: str = FIRST_DECISION) -> pd.DataFrame:
    specs = ds.load_specs()
    h1s = {s: ds.load_bars(s, "H1", end=end) for s in symbols}
    h4s = {s: ds.load_bars(s, "H4", end=end) for s in symbols}
    feats = all_pair_features(h1s, h4s, ls, specs)
    parts = []
    for s in symbols:
        m5 = ds.load_bars(s, "M5", start=ds.DEV_START, end=end)
        lab = label_pair(s, h1s[s], m5, specs, ls, tf_min=60)
        f = feats[s].drop(columns=["time"]).reset_index(drop=True)
        pip = ds.pip_size(specs[s])
        fw = forward_stats(h1s[s], pip)
        d = pd.concat([lab.reset_index(drop=True), f, fw], axis=1)
        d["close"] = h1s[s]["close"].to_numpy()
        d["spread_pips"] = h1s[s]["spread_clean_pts"].to_numpy() * specs[s]["point"] / pip
        d["pip"] = pip
        d.insert(0, "symbol", s)
        parts.append(d[d["time"] >= pd.Timestamp(first_decision, tz="UTC")])
    df = pd.concat(parts, ignore_index=True).sort_values(["time", "symbol"]).reset_index(drop=True)
    df["week"] = week_index(df["T"])
    df["year"] = df["T"].dt.year
    return df


def stack_sides(df: pd.DataFrame, feature_cols: list[str], s_features=None, n_features=None, mirror_fn=None,
                extra=("era",)) -> pd.DataFrame:
    """Long rows as they are, short rows with mirrored features; y = 1 if the side's net R > 0. Rows with a missing
    feature or an invalid label are dropped (reported by the caller). Columns: symbol, T, week, year, side, y, r, w,
    exit_T, + feature_cols."""
    s_features = S_FEATURES if s_features is None else s_features
    n_features = N_FEATURES if n_features is None else n_features
    mirror_fn = mirror if mirror_fn is None else mirror_fn
    keep = ["symbol", "time", "T", "week", "year"] + [c for c in extra if c in df.columns]
    out = []
    X = df[s_features + n_features]
    for tag, side in (("L", 1), ("S", -1)):
        base = df[keep].copy()
        base["side"] = side
        base["rid"] = df.index.to_numpy() * 2 + (0 if side > 0 else 1)
        base["cost_r_lab"] = df["cost_r_lab"].to_numpy()
        base["r"] = df[f"r_{tag}"].to_numpy()
        base["y"] = (base["r"] > 0).astype(float)
        base["w"] = df[f"w_{tag}"].to_numpy()
        base["exit_T"] = df[f"exit_T_{tag}"].to_numpy()
        base["reason"] = df[f"reason_{tag}"].to_numpy()
        base["valid"] = df[f"valid_{tag}"].to_numpy()
        Xs = X if side > 0 else mirror_fn(X)
        for c in feature_cols:
            base[c] = Xs[c].to_numpy()
        out.append(base)
    st = pd.concat(out, ignore_index=True)
    ok = st["valid"] & np.isfinite(st[feature_cols].to_numpy(float)).all(axis=1) & st["r"].notna()
    return st[ok].drop(columns="valid").sort_values(["T", "symbol", "side"]).reset_index(drop=True)


def label_summary(df: pd.DataFrame) -> dict:
    """Direction-neutral description of the label: pooled over long and short (no direction is favoured)."""
    out = {"decision_rows": int(len(df))}
    rs, reasons, wts = [], [], []
    for tag in ("L", "S"):
        v = df[f"valid_{tag}"]
        rs.append(df.loc[v, f"r_{tag}"].to_numpy())
        reasons.append(df.loc[v, f"reason_{tag}"].to_numpy())
        wts.append(df.loc[v, f"w_{tag}"].to_numpy())
    r, why, w = np.concatenate(rs), np.concatenate(reasons), np.concatenate(wts)
    out.update({"valid_labels": int(len(r)), "pooled_mean_net_r": float(r.mean()), "sd_net_r": float(r.std(ddof=1)),
                "win_rate_net_r_gt0": float((r > 0).mean()),
                "exit_mix": {name: float((why == code).mean()) for code, name in
                             ((0, "TP"), (1, "SL"), (2, "TIME"), (3, "FLAT"))},
                "mean_uniqueness": float(np.nanmean(w)), "sum_uniqueness_weights": float(np.nansum(w)),
                "floor_binds_share": float(df["floor_binds"].mean()), "median_stop_pips": float(df["stop_pips"].median()),
                "median_cost_r": float(df["cost_r_lab"].median())})
    out["per_symbol"] = {s: {"rows": int(len(g)), "valid_L": int(g["valid_L"].sum()),
                             "mean_net_r_pooled": float(np.nanmean(np.concatenate([g["r_L"], g["r_S"]]))),
                             "mean_uniqueness": float(np.nanmean(np.concatenate([g["w_L"], g["w_S"]])))}
                         for s, g in df.groupby("symbol")}
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--k-atr", type=float, default=LabelSpec.k_atr)
    ap.add_argument("--horizon-min", type=int, default=LabelSpec.horizon_min)
    ap.add_argument("--spread-col", default=LabelSpec.spread_col)
    ap.add_argument("--tag", default="h1")
    a = ap.parse_args()
    ls = LabelSpec(k_atr=a.k_atr, horizon_min=a.horizon_min, spread_col=a.spread_col)
    OUT.mkdir(parents=True, exist_ok=True)
    d = build_dataset(ls=ls)
    d.to_parquet(OUT / f"dataset_{a.tag}.parquet", index=False)
    summ = label_summary(d)
    summ["label_spec"] = ls.__dict__
    (OUT / f"dataset_{a.tag}_summary.json").write_text(json.dumps(summ, indent=1))
    print(json.dumps(summ, indent=1))
