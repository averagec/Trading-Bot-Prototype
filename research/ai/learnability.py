"""The one pre-registered learnability test (research/reports/ai_edge_prereg.md).

Question: on DEV, do causal H1-bar features predict the sign of the NET-R outcome of a stateless triple-barrier trade,
out of fold, beyond what non-directional information (time, volatility state, spread, stop geometry) already explains?

    python -m research.ai.learnability --stage real  --models LR HGB_S HGB_M
    python -m research.ai.learnability --stage perm  --models LR --n-perm 100
    python -m research.ai.learnability --stage report

Design (all fixed before any model was fitted):
    rows        a long row and a mirrored short row per (pair, H1 decision bar); y = 1 if the side's net R > 0
    F  features all S (directional) + N (non-directional);  B1 = N only (identical for the two sides of a bar)
    folds       expanding walk-forward, 11 half-year test blocks 2016H1..2021H1, purge on label end + 7 day embargo
    primary     D1 = mean per-row log-loss(B1) - log-loss(F), pooled over all out-of-fold rows; week-block bootstrap CI
    secondary   directional AUC (within a bar: does p_long - p_short rank the winning side?), pooled AUC, top-decile net R
    null        S features shifted by a random whole number of weeks (>= 26) against everything else, same shift for all
                pairs; F refit, evaluated, per replicate. (B1 does not depend on S, so its predictions are reused.)
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

from research.ai import models as mdl
from research.ai.build import stack_sides, OUT
from research.ai.cv import half_year_blocks, walk_forward
from research.ai.features import N_FEATURES, S_FEATURES
from research.ai.stats import auc_score, block_bootstrap, group_sums, logloss, bootstrap_auc

LEARN = OUT / "learn"
WEEK_NS = 7 * 24 * 3600 * 10 ** 9
TOP_Q = 0.90
ALL_F = S_FEATURES + N_FEATURES


# ── out-of-fold prediction ────────────────────────────────────────────────────────────

def oof_predict(st: pd.DataFrame, cols: list[str], model: str, blocks=None, embargo_days: int = 7, seed: int = 0,
                thr_sample: int = 60_000, verbose: bool = False) -> dict:
    """Walk-forward predictions for the rows of `st` (sorted by T). Returns arrays aligned to st rows:
    p (NaN outside test blocks), p0 (training prevalence of the fold), thr (row-level TOP_Q quantile of the training
    prediction distribution of the fold; used to pick top-decile trades without looking at the test block)."""
    blocks = blocks or half_year_blocks()
    X = st[cols].to_numpy(float)
    y = st["y"].to_numpy(float)
    w = st["w"].to_numpy(float)
    p = np.full(len(st), np.nan)
    p0 = np.full(len(st), np.nan)
    thr = np.full(len(st), np.nan)
    rng = np.random.default_rng(seed)
    for (a, b), tr, te in walk_forward(st["T"], st["exit_T"], blocks, embargo_days):
        t0 = time.time()
        sub = rng.choice(tr, size=min(thr_sample, len(tr)), replace=False)
        pt, ps = mdl.fit_predict(model, X[tr], y[tr], w[tr], [X[te], X[sub]], seed=seed)
        p[te] = pt
        p0[te] = float(y[tr].mean())
        thr[te] = float(np.quantile(ps, TOP_Q))
        if verbose:
            print(f"  {model} {len(cols)}f {a.date()}..{b.date()} train {len(tr)} test {len(te)} {time.time() - t0:.1f}s",
                  flush=True)
    return {"p": p, "p0": p0, "thr": thr}


# ── metrics ───────────────────────────────────────────────────────────────────────────

def _weekly(week: np.ndarray, *cols) -> tuple[np.ndarray, np.ndarray]:
    return group_sums(week, np.column_stack(cols))


def mean_ci(week: np.ndarray, x: np.ndarray, n_boot: int = 2000, seed: int = 1) -> dict:
    """Mean of x with a week-block bootstrap CI (finite entries only)."""
    ok = np.isfinite(x)
    _, s = _weekly(week[ok], np.ones(ok.sum()), x[ok])
    return block_bootstrap(s, lambda t: t[1] / t[0], n_boot, seed)


def pair_up(st: pd.DataFrame, p: np.ndarray) -> pd.DataFrame:
    """One row per bar with both sides present: columns bar, week, pL, pS, yL, yS, rL, rS, cost."""
    d = pd.DataFrame({"bar": st["rid"].to_numpy() // 2, "side": st["side"].to_numpy(), "p": p,
                      "y": st["y"].to_numpy(), "r": st["r"].to_numpy(), "week": st["week"].to_numpy(),
                      "cost": st["cost_r_lab"].to_numpy(), "thr": st["_thr"].to_numpy() if "_thr" in st else np.nan})
    L = d[d["side"] > 0].drop(columns="side")
    S = d[d["side"] < 0].drop(columns=["side", "week", "cost", "thr"])
    m = L.merge(S, on="bar", suffixes=("L", "S"))
    return m[np.isfinite(m["pL"]) & np.isfinite(m["pS"])].reset_index(drop=True)


def metrics(st: pd.DataFrame, pF: np.ndarray, pB: np.ndarray, p0: np.ndarray, thr: np.ndarray,
            n_boot: int = 2000, auc_boot: int = 200, full: bool = True) -> dict:
    ev = np.isfinite(pF) & np.isfinite(pB) & np.isfinite(p0)
    y = st["y"].to_numpy(float)[ev]
    wk = st["week"].to_numpy()[ev]
    ll0, llB, llF = logloss(y, p0[ev]), logloss(y, pB[ev]), logloss(y, pF[ev])
    out = {"rows": int(ev.sum()), "base_rate": float(y.mean()), "ll_base": float(ll0.mean()), "ll_B1": float(llB.mean()),
           "ll_F": float(llF.mean())}
    out["D1_B1_minus_F"] = mean_ci(wk, llB - llF, n_boot if full else 1)
    out["D0_base_minus_B1"] = mean_ci(wk, ll0 - llB, n_boot if full else 1) if full else None
    out["auc_F"] = auc_score(y, pF[ev])
    out["auc_B1"] = auc_score(y, pB[ev])
    stv = st.loc[ev].copy()
    stv["_thr"] = thr[ev]
    pr = pair_up(stv, pF[ev])
    diff = pr["yL"] - pr["yS"]
    sel = diff != 0
    dir_auc = auc_score((diff[sel] > 0).to_numpy(), (pr["pL"] - pr["pS"])[sel].to_numpy())
    out["dir_auc"] = dir_auc
    out["dir_n"] = int(sel.sum())
    # top-decile trades: the side with the larger p, if it clears the fold's training threshold
    best_is_L = pr["pL"] >= pr["pS"]
    pmax = np.where(best_is_L, pr["pL"], pr["pS"])
    trade = pmax >= pr["thr"]
    r_ch = np.where(best_is_L, pr["rL"], pr["rS"])
    r_avg = (pr["rL"] + pr["rS"]) / 2
    gain = np.where(trade, r_ch - r_avg, np.nan)
    lvl = np.where(trade, r_ch, np.nan)
    stress = np.where(trade, r_ch - 0.5 * pr["cost"], np.nan)
    stress2 = np.where(trade, r_ch - 1.0 * pr["cost"], np.nan)
    out["top"] = {"trade_share": float(trade.mean()), "n": int(trade.sum())}
    if full:
        wkp = pr["week"].to_numpy()
        out["top"]["gain_vs_random_side"] = mean_ci(wkp, gain, n_boot)
        out["top"]["net_r"] = mean_ci(wkp, lvl, n_boot)
        out["top"]["net_r_cost_x1.5"] = mean_ci(wkp, stress, n_boot)
        out["top"]["net_r_cost_x2"] = mean_ci(wkp, stress2, n_boot)
        # AUC intervals: pooled AUC of F and of B1, and the directional AUC
        keys = st["week"].to_numpy()[ev]
        out["auc_F_ci"] = bootstrap_auc(y, pF[ev], keys, auc_boot, seed=3)
        out["auc_B1_ci"] = bootstrap_auc(y, pB[ev], keys, auc_boot, seed=4)
        out["dir_auc_ci"] = bootstrap_auc((diff[sel] > 0).to_numpy(), (pr["pL"] - pr["pS"])[sel].to_numpy(),
                                          pr["week"].to_numpy()[sel.to_numpy()], auc_boot, seed=5)
        # by half-year block (descriptive stability)
        blk = pd.to_datetime(st["T"], utc=True).dt.year.to_numpy()[ev] * 10 + (
            pd.to_datetime(st["T"], utc=True).dt.month.to_numpy()[ev] > 6)
        out["D1_by_half_year"] = {int(b): float((llB - llF)[blk == b].mean()) for b in np.unique(blk)}
    else:
        out["top"]["gain"] = float(np.nanmean(gain)) if trade.any() else float("nan")
    return out


# ── permutation null ──────────────────────────────────────────────────────────────────

def shift_directional(df: pd.DataFrame, k_weeks: int, min_t=None, max_t=None, s_cols=None) -> pd.DataFrame:
    """Return df with the S feature columns replaced by the values observed k_weeks (circularly) later/earlier in
    calendar time for the same pair and the same hour of the week. Everything else (labels, N features) is untouched,
    so the S features keep their own dynamics and cross-pair structure but carry no information about the labels."""
    T = pd.to_datetime(df["T"], utc=True).astype("int64").to_numpy()
    lo, hi = (T.min() if min_t is None else min_t), (T.max() if max_t is None else max_t)
    span = int(np.ceil((hi - lo + 1) / WEEK_NS)) * WEEK_NS
    src = lo + ((T - lo + int(k_weeks) * WEEK_NS) % span)
    s_cols = S_FEATURES if s_cols is None else s_cols
    right = df[["symbol"] + list(s_cols)].copy()
    right["_src"] = T
    left = pd.DataFrame({"symbol": df["symbol"].to_numpy(), "_src": src})
    got = left.merge(right, on=["symbol", "_src"], how="left", sort=False)
    out = df.copy()
    for c in s_cols:
        out[c] = got[c].to_numpy()
    return out


# ── drivers ───────────────────────────────────────────────────────────────────────────

def load_df() -> pd.DataFrame:
    return pd.read_parquet(OUT / "dataset_h1.parquet")


def run_real(models_: list[str], seed: int = 0) -> None:
    LEARN.mkdir(parents=True, exist_ok=True)
    df = load_df()
    st = stack_sides(df, ALL_F)
    print(f"stacked rows {len(st)} (dropped {2 * len(df) - len(st)} of {2 * len(df)} for invalid label / NaN feature)")
    for m in models_:
        t0 = time.time()
        F = oof_predict(st, ALL_F, m, seed=seed, verbose=True)
        B = oof_predict(st, N_FEATURES, m, seed=seed, verbose=True)
        res = metrics(st, F["p"], B["p"], F["p0"], F["thr"])
        res.update({"model": m, "seconds": round(time.time() - t0)})
        pd.DataFrame({"rid": st["rid"], "pF": F["p"], "pB": B["p"], "p0": F["p0"], "thr": F["thr"]}).to_parquet(
            LEARN / f"oof_{m}.parquet", index=False)
        (LEARN / f"real_{m}.json").write_text(json.dumps(res, indent=1, default=float))
        d1 = res["D1_B1_minus_F"]
        print(f"[{m}] D1 {d1['est']:.3e} CI [{d1['lo']:.3e}, {d1['hi']:.3e}]  dirAUC {res['dir_auc']:.4f}  "
              f"AUC F {res['auc_F']:.4f} B1 {res['auc_B1']:.4f}", flush=True)


def run_perm(model: str, n_perm: int, seed0: int = 2025) -> None:
    LEARN.mkdir(parents=True, exist_ok=True)
    path = LEARN / f"perm_{model}.jsonl"
    done = {json.loads(l)["i"] for l in path.read_text().splitlines()} if path.exists() else set()
    df = load_df()
    base = pd.read_parquet(LEARN / f"oof_{model}.parquet").set_index("rid")
    T = pd.to_datetime(df["T"], utc=True)
    span_weeks = int((T.max() - T.min()) / pd.Timedelta(days=7))
    rng = np.random.default_rng(seed0)
    ks = rng.integers(26, span_weeks - 26, size=n_perm)
    for i, k in enumerate(ks):
        if i in done:
            continue
        t0 = time.time()
        st = stack_sides(shift_directional(df, int(k)), ALL_F)
        F = oof_predict(st, ALL_F, model, seed=0)
        rid = st["rid"].to_numpy()
        pB = base["pB"].reindex(rid).to_numpy()
        p0 = base["p0"].reindex(rid).to_numpy()
        res = metrics(st, F["p"], pB, p0, F["thr"], full=False)
        rec = {"i": i, "k_weeks": int(k), "D1": res["D1_B1_minus_F"]["est"], "dir_auc": res["dir_auc"],
               "auc_F": res["auc_F"], "top_gain": res["top"]["gain"], "top_share": res["top"]["trade_share"],
               "rows": res["rows"], "sec": round(time.time() - t0)}
        with open(path, "a") as fh:
            fh.write(json.dumps(rec) + "\n")
        print(model, rec, flush=True)


def report(models_: list[str]) -> dict:
    out = {}
    for m in models_:
        real = json.loads((LEARN / f"real_{m}.json").read_text())
        p = LEARN / f"perm_{m}.jsonl"
        rec = {"real": real}
        if p.exists():
            pm = pd.DataFrame([json.loads(l) for l in p.read_text().splitlines()])
            B = len(pm)
            rec["perm_n"] = B
            for key, obs in (("D1", real["D1_B1_minus_F"]["est"]), ("dir_auc", real["dir_auc"]),
                             ("top_gain", real["top"]["gain_vs_random_side"]["est"])):
                v = pm[key].to_numpy(float)
                rec[f"perm_{key}"] = {"obs": obs, "null_mean": float(v.mean()), "null_sd": float(v.std(ddof=1)),
                                      "z": float((obs - v.mean()) / v.std(ddof=1)), "null_max": float(v.max()),
                                      "p_emp": float((1 + (v >= obs).sum()) / (1 + B))}
        out[m] = rec
    (LEARN / "report.json").write_text(json.dumps(out, indent=1, default=float))
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", choices=["real", "perm", "report"], required=True)
    ap.add_argument("--models", nargs="+", default=["LR", "HGB_S", "HGB_M"])
    ap.add_argument("--n-perm", type=int, default=40)
    a = ap.parse_args()
    if a.stage == "real":
        run_real(a.models)
    elif a.stage == "perm":
        for m in a.models:
            run_perm(m, a.n_perm)
    else:
        print(json.dumps(report(a.models), indent=1, default=float))


def holm_lower_bound(model: str, n_boot: int = 4000, alpha: float = 0.05 / 3, seed: int = 11) -> dict:
    """Pre-registered C1: lower end of the one-sided (1 - alpha) week-block bootstrap interval of D1 for one setting."""
    df = load_df()
    st = stack_sides(df, ALL_F)
    o = pd.read_parquet(LEARN / f"oof_{model}.parquet").set_index("rid").reindex(st["rid"])
    ev = np.isfinite(o["pF"].to_numpy())
    y = st["y"].to_numpy(float)[ev]
    d = logloss(y, o["pB"].to_numpy()[ev]) - logloss(y, o["pF"].to_numpy()[ev])
    _, s = group_sums(st["week"].to_numpy()[ev], np.column_stack([np.ones(len(d)), d]))
    W = len(s)
    mult = np.random.default_rng(seed).multinomial(W, np.full(W, 1.0 / W), size=n_boot).astype(float)
    boots = (mult @ s)[:, 1] / (mult @ s)[:, 0]
    return {"model": model, "D1": float(s[:, 1].sum() / s[:, 0].sum()), "alpha": alpha,
            "lower_bound": float(np.quantile(boots, alpha)), "n_boot": n_boot}
