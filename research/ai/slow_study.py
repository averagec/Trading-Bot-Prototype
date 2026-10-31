"""Driver for the slow-horizon learnability study A2' (research/reports/ai_slow_prereg.md).

    python -m research.ai.slow_study --stage build    [--variants H4_k10 ...]     # features + labels only (no model)
    python -m research.ai.slow_study --stage labels                              # direction-neutral label statistics
    python -m research.ai.slow_study --stage real --go LEAD_GO                   # model fits on real outcomes
    python -m research.ai.slow_study --stage perm --go LEAD_GO --n-perm 100
    python -m research.ai.slow_study --stage report --go LEAD_GO

The stages that fit a model to real outcomes refuse to run without the token the lead sends after reviewing the
pre-registration. The functions below are importable and tested on synthetic data.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

from research.ai import slow as sl
from research.ai.build import OUT, label_summary, stack_sides
from research.ai.cv import half_year_blocks
from research.ai.learnability import metrics, oof_predict, shift_directional
from research.ai.stats import auc_score, block_bootstrap, group_sums, logloss

SLOW_DIR = OUT / "slow"
GO_TOKEN = "LEAD_GO"
PRIMARY_MODEL = "LR"                       # the only model in the confirmatory family
DESCRIPTIVE_MODELS = ("HGB_S_slow", "HGB_M_slow")
K_FAMILY = 4
ALPHA = 0.05 / K_FAMILY                    # Holm level of the first (smallest) p-value; one-sided
Z_NULL_MIN = 2.5
AUC_FALSIFIER = 0.52
MIN_POSITIVE_YEARS = 0.70
ALL_FEATURES = sl.SLOW_S + sl.SLOW_N


def blocks():
    return half_year_blocks(sl.OOF_FIRST, "2021-06-01")


def stack_variant(df: pd.DataFrame) -> pd.DataFrame:
    return stack_sides(df, ALL_FEATURES, sl.SLOW_S, sl.SLOW_N, sl.mirror_slow)


def require_go(token: str | None) -> None:
    if token != GO_TOKEN:
        raise SystemExit("this stage fits models to real outcomes; it needs --go LEAD_GO (sent by the lead after the "
                         "pre-registration review). Nothing was run.")


# ── fitting and evaluation ────────────────────────────────────────────────────────────

def fit_oof(st: pd.DataFrame, spec: sl.SlowSpec, model: str, seed: int = 0, cols=None, blks=None) -> dict:
    return oof_predict(st, cols or ALL_FEATURES, model, blocks=blks or blocks(), embargo_days=spec.embargo_days, seed=seed)


def by_group_stats(st: pd.DataFrame, pF, pB, key: np.ndarray) -> dict:
    """D1 (mean log-loss gain) and directional AUC (point) per group value, plus leave-one-group-out versions."""
    ev = np.isfinite(pF) & np.isfinite(pB)
    y = st["y"].to_numpy(float)
    d = np.where(ev, logloss(y, pB) - logloss(y, pF), np.nan)
    bar = st["rid"].to_numpy() // 2
    side = st["side"].to_numpy()
    df = pd.DataFrame({"bar": bar, "side": side, "p": pF, "y": y, "g": key, "ev": ev})
    L = df[(df["side"] > 0) & df["ev"]].drop(columns=["side", "ev"])
    S = df[(df["side"] < 0) & df["ev"]][["bar", "p", "y"]]
    pr = L.merge(S, on="bar", suffixes=("L", "S"))
    out = {"groups": {}, "loo": {}}
    uniq = [g for g in pd.unique(key[ev])]
    for g in sorted(uniq):
        m = ev & (key == g)
        sub = pr[pr["g"] == g]
        dd = sub["yL"] - sub["yS"]
        s = dd != 0
        out["groups"][str(g)] = {"D1": float(np.nanmean(d[m])), "n": int(m.sum()),
                                 "dir_auc": auc_score((dd[s] > 0).to_numpy(), (sub["pL"] - sub["pS"])[s].to_numpy())
                                 if s.sum() > 30 else float("nan")}
        sub2 = pr[pr["g"] != g]
        dd2 = sub2["yL"] - sub2["yS"]
        s2 = dd2 != 0
        out["loo"][str(g)] = {"D1": float(np.nanmean(d[ev & (key != g)])),
                              "dir_auc": auc_score((dd2[s2] > 0).to_numpy(), (sub2["pL"] - sub2["pS"])[s2].to_numpy())}
    return out


def holm_lower_bound(st: pd.DataFrame, pF, pB, alpha: float = ALPHA, n_boot: int = 4000, seed: int = 11,
                     block: int = 1) -> dict:
    """C1: lower end of the one-sided (1 - alpha) block-bootstrap interval of D1. block = number of weeks per block."""
    ev = np.isfinite(pF) & np.isfinite(pB)
    y = st["y"].to_numpy(float)[ev]
    d = logloss(y, pB[ev]) - logloss(y, pF[ev])
    keys = st["week"].to_numpy()[ev] // block
    _, s = group_sums(keys, np.column_stack([np.ones(len(d)), d]))
    W = len(s)
    mult = np.random.default_rng(seed).multinomial(W, np.full(W, 1.0 / W), size=n_boot).astype(float)
    tot = mult @ s
    boots = tot[:, 1] / tot[:, 0]
    return {"D1": float(s[:, 1].sum() / s[:, 0].sum()), "alpha": alpha, "block_weeks": block,
            "lower_bound": float(np.quantile(boots, alpha)), "blocks": int(W),
            "p_boot": float((1 + (boots <= 0).sum()) / (1 + n_boot))}


def evaluate(st: pd.DataFrame, F: dict, B: dict, n_boot: int = 2000, auc_boot: int = 200) -> dict:
    """All pre-registered statistics for one variant and one model setting."""
    pF, pB, p0, thr = F["p"], B["p"], F["p0"], F["thr"]
    res = {"overall": metrics(st, pF, pB, p0, thr, n_boot=n_boot, auc_boot=auc_boot)}
    ev = np.isfinite(pF) & np.isfinite(pB) & np.isfinite(p0)
    res["eras"] = {}
    era = st["era"].to_numpy()
    for name, _, _ in sl.ERAS:
        m = ev & (era == name)
        if m.sum() > 200:
            e = metrics(st[m].reset_index(drop=True), pF[m], pB[m], p0[m], thr[m], n_boot=min(n_boot, 500),
                        auc_boot=min(auc_boot, 100))
            res["eras"][name] = {k: e[k] for k in ("rows", "D1_B1_minus_F", "dir_auc", "dir_auc_ci", "top")}
    year = st["year"].to_numpy()
    res["by_year"] = by_group_stats(st, pF, pB, year)
    pos = [v["D1"] > 0 for v in res["by_year"]["groups"].values()]
    res["stability"] = {"years": len(pos), "share_positive_years": float(np.mean(pos)) if pos else float("nan"),
                        "min_leave_one_year_out_D1": float(min(v["D1"] for v in res["by_year"]["loo"].values())),
                        "min_leave_one_year_out_dir_auc": float(np.nanmin([v["dir_auc"] for v in res["by_year"]["loo"].values()])),
                        "rule": f"share_positive_years >= {MIN_POSITIVE_YEARS} and min leave-one-year-out D1 > 0"}
    res["holm_week"] = holm_lower_bound(st, pF, pB, block=1)
    res["holm_4week"] = holm_lower_bound(st, pF, pB, block=4)
    era_auc = {k: v["dir_auc"] for k, v in res["eras"].items()}
    res["falsifier"] = {"era_dir_auc": era_auc, "eras_at_or_above_0.52": int(sum(a >= AUC_FALSIFIER for a in era_auc.values())),
                        "falsified": bool(sum(a >= AUC_FALSIFIER for a in era_auc.values()) < 2)}
    return res


def holm_rejections(pvals: dict, alpha: float = 0.05) -> dict:
    """Holm step-down over the family: sort p ascending, compare p_(i) with alpha / (m - i + 1), stop at the first failure."""
    order = sorted(pvals, key=lambda k: pvals[k])
    m = len(order)
    out = {k: False for k in pvals}
    for i, k in enumerate(order):
        if pvals[k] <= alpha / (m - i):
            out[k] = True
        else:
            break
    return out


def decide(res: dict, perm: dict | None, c1: bool | None = None) -> dict:
    """The pre-registered verdict for one variant (primary model). perm = {'obs','null_mean','null_sd','null_max','B'}."""
    c1 = bool(res["holm_week"]["lower_bound"] > 0) if c1 is None else bool(c1)
    c1 = bool(c1 and res["holm_4week"]["lower_bound"] > 0)     # the 4-week-block interval must agree (serial dependence of slow features)
    c2 = None
    if perm is not None:
        z = (perm["obs"] - perm["null_mean"]) / perm["null_sd"] if perm["null_sd"] > 0 else float("inf")
        c2 = bool(perm["obs"] > perm["null_max"] and z >= Z_NULL_MIN)
    stab = res["stability"]
    s_ok = bool(stab["share_positive_years"] >= MIN_POSITIVE_YEARS and stab["min_leave_one_year_out_D1"] > 0)
    f_ok = not res["falsifier"]["falsified"]
    top = res["overall"]["top"]["gain_vs_random_side"]["lo"] if "gain_vs_random_side" in res["overall"]["top"] else float("nan")
    stressed = res["overall"]["top"].get("net_r_cost_x1.5", {}).get("lo", float("nan"))
    learn = bool(c1 and c2 is True and s_ok and f_ok)
    return {"C1": c1, "C2": c2, "S_stability": s_ok, "F_falsifier_survived": f_ok, "learnable": learn,
            "top_gain_lower": top, "top_net_r_x1.5_lower": stressed,
            "worth_next_stage": bool(learn and stressed > 0)}


# ── permutation null ──────────────────────────────────────────────────────────────────

def run_perm_variant(df: pd.DataFrame, spec: sl.SlowSpec, base_oof: pd.DataFrame, n_perm: int, path: Path,
                     seed0: int = 2025, blks=None, min_shift: int = 26) -> None:
    """Shift SLOW_S by a random whole number of weeks (>= min_shift), refit F, evaluate. B1 predictions are reused."""
    T = pd.to_datetime(df["T"], utc=True)
    span_weeks = int((T.max() - T.min()) / pd.Timedelta(days=7))
    ks = np.random.default_rng(seed0).integers(min_shift, span_weeks - min_shift, size=n_perm)
    done = {json.loads(l)["i"] for l in path.read_text().splitlines()} if path.exists() else set()
    base = base_oof.set_index("rid")
    for i, k in enumerate(ks):
        if i in done:
            continue
        st = stack_variant(shift_directional(df, int(k), s_cols=sl.SLOW_S))
        F = fit_oof(st, spec, PRIMARY_MODEL, blks=blks)
        rid = st["rid"].to_numpy()
        m = metrics(st, F["p"], base["pB"].reindex(rid).to_numpy(), base["p0"].reindex(rid).to_numpy(), F["thr"],
                    full=False)
        rec = {"i": i, "k_weeks": int(k), "D1": m["D1_B1_minus_F"]["est"], "dir_auc": m["dir_auc"],
               "top_gain": m["top"]["gain"], "rows": m["rows"]}
        with open(path, "a") as fh:
            fh.write(json.dumps(rec) + "\n")


def perm_summary(path: Path, obs: float) -> dict | None:
    if not path.exists():
        return None
    v = np.array([json.loads(l)["D1"] for l in path.read_text().splitlines()], float)
    return {"obs": obs, "null_mean": float(v.mean()), "null_sd": float(v.std(ddof=1)), "null_max": float(v.max()),
            "B": int(len(v)), "p_emp": float((1 + (v >= obs).sum()) / (1 + len(v)))}


# ── drivers ───────────────────────────────────────────────────────────────────────────

def dataset_path(name: str) -> Path:
    return OUT / f"dataset_{name}.parquet"


def run_build(names) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    allv = {**sl.VARIANTS, **sl.SENSITIVITY}
    for n in names:
        t0 = time.time()
        df = sl.build_variant(allv[n])
        df.to_parquet(dataset_path(n), index=False)
        print(n, len(df), f"{time.time() - t0:.0f}s", flush=True)


def run_labels(names) -> dict:
    out = {}
    for n in names:
        df = pd.read_parquet(dataset_path(n))
        s = label_summary(df)
        dur = pd.concat([(df["exit_T_L"] - df["T"]), (df["exit_T_S"] - df["T"])]).dropna() / pd.Timedelta(days=1)
        s["label_days"] = {"max": float(dur.max()), "p99": float(dur.quantile(0.99)), "median": float(dur.median())}
        s["by_era"] = {}
        for era, g in df.groupby("era"):
            r = np.concatenate([g.loc[g["valid_L"], "r_L"], g.loc[g["valid_S"], "r_S"]])
            s["by_era"][era] = {"decisions": int(len(g)), "valid_labels": int(len(r)), "pooled_mean_net_r": float(r.mean()),
                                "win_rate": float((r > 0).mean()), "median_cost_r": float(g["cost_r_lab"].median()),
                                "median_stop_pips": float(g["stop_pips"].median())}
        out[n] = s
    SLOW_DIR.mkdir(parents=True, exist_ok=True)
    p = SLOW_DIR / "label_summary.json"
    old = json.loads(p.read_text()) if p.exists() else {}
    old.update(json.loads(json.dumps(out, default=float)))
    p.write_text(json.dumps(old, indent=1, default=float))
    return out


def run_real(names, models=(PRIMARY_MODEL,) + DESCRIPTIVE_MODELS) -> None:
    SLOW_DIR.mkdir(parents=True, exist_ok=True)
    for n in names:
        spec = {**sl.VARIANTS, **sl.SENSITIVITY}[n]
        df = pd.read_parquet(dataset_path(n))
        st = stack_variant(df)
        print(n, "rows", len(st), flush=True)
        for m in models:
            t0 = time.time()
            F = fit_oof(st, spec, m)
            B = fit_oof(st, spec, m, cols=sl.SLOW_N)
            res = evaluate(st, F, B)
            res.update({"variant": n, "model": m, "seconds": round(time.time() - t0)})
            pd.DataFrame({"rid": st["rid"], "pF": F["p"], "pB": B["p"], "p0": F["p0"], "thr": F["thr"]}).to_parquet(
                SLOW_DIR / f"oof_{n}_{m}.parquet", index=False)
            (SLOW_DIR / f"real_{n}_{m}.json").write_text(json.dumps(res, indent=1, default=float))
            d1 = res["overall"]["D1_B1_minus_F"]
            print(f"[{n} {m}] D1 {d1['est']:.3e} [{d1['lo']:.3e},{d1['hi']:.3e}] dirAUC {res['overall']['dir_auc']:.4f}",
                  flush=True)


def run_perm(names, n_perm: int) -> None:
    for n in names:
        spec = sl.VARIANTS[n]
        df = pd.read_parquet(dataset_path(n))
        base = pd.read_parquet(SLOW_DIR / f"oof_{n}_{PRIMARY_MODEL}.parquet")
        run_perm_variant(df, spec, base, n_perm, SLOW_DIR / f"perm_{n}.jsonl")


def run_report(names) -> dict:
    res = {n: json.loads((SLOW_DIR / f"real_{n}_{PRIMARY_MODEL}.json").read_text()) for n in names}
    holm = holm_rejections({n: r["holm_week"]["p_boot"] for n, r in res.items()}, 0.05)
    out = {}
    for n, r in res.items():
        perm = perm_summary(SLOW_DIR / f"perm_{n}.jsonl", r["overall"]["D1_B1_minus_F"]["est"])
        out[n] = {"decision": decide(r, perm, c1=holm[n]), "perm": perm, "p_boot": r["holm_week"]["p_boot"]}
    (SLOW_DIR / "report.json").write_text(json.dumps(out, indent=1, default=float))
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", choices=["build", "labels", "real", "perm", "report"], required=True)
    ap.add_argument("--variants", nargs="+", default=list(sl.VARIANTS))
    ap.add_argument("--go", default=None)
    ap.add_argument("--n-perm", type=int, default=100)
    a = ap.parse_args()
    if a.stage == "build":
        run_build(a.variants)
    elif a.stage == "labels":
        print(json.dumps(run_labels(a.variants), indent=1, default=float)[:6000])
    else:
        require_go(a.go)
        if a.stage == "real":
            run_real(a.variants)
        elif a.stage == "perm":
            run_perm(a.variants, a.n_perm)
        else:
            print(json.dumps(run_report(a.variants), indent=1, default=float))
