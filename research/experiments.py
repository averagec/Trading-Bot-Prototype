"""
Pre-registered rule/risk-framework experiments (see research/reports/rule_changes_prereg.md).

    python -m research.experiments dev                  # simulate all variants (actual + 40 random-direction nulls), report DEV window only
    python -m research.experiments unseal               # holdout + walk-forward F4/F5 + FTMO for the finalists chosen mechanically in `dev`
    python -m research.experiments log                  # print the variant log

Discipline: every simulation covers the whole history (causal, stateful), results are stored per variant in
output/rc/runs/<variant>/, but `dev` only ever computes/prints statistics for trades opened before CUT. `unseal`
appends to output/rc/holdout_log.csv each time it is run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from research.data import load_specs, prepare_bars
from research.metrics import bootstrap_mean_ci, max_drawdown_pct, day_table
from research.montecarlo import FtmoRules, ftmo_mc
from research.nulltest import mirror
from research.run import OUT, _clean, get_candidates
from research.signals import SignalConfig
from research.sim import Simulator, SimConfig

RC = OUT / "rc"
RUNS = RC / "runs"
CUT = pd.Timestamp("2026-05-01", tz="UTC")                     # DEV: open_T < CUT ; HOLDOUT: open_T >= CUT
FAR = pd.Timestamp("2100-01-01", tz="UTC")
START = pd.Timestamp("1900-01-01", tz="UTC")
FOLDS = [("F1", "2025-06-02", "2025-10-01"), ("F2", "2025-10-01", "2026-01-01"), ("F3", "2026-01-01", "2026-05-01"),
         ("F4", "2026-05-01", "2026-08-01"), ("F5", "2026-08-01", "2027-01-01")]
DEV_FOLDS = ("F1", "F2", "F3")
N_SEEDS = 40
BOOT_B = 10_000
BOOT_SEED = 12345

BASE = SimConfig()
_H1 = dict(friday_flatten_utc=20.5)
_H2 = dict(no_entry_from_utc=20.0)
_H3 = dict(breakeven=False)
# name -> (role, class, SimConfig kwargs)
VARIANTS: dict[str, tuple[str, str, dict]] = {
    "base": ("base", "-", {}),
    "H1_fri_flatten_2030": ("prereg", "T", _H1),
    "H2_no_entry_20utc": ("prereg", "D", _H2),
    "H3_no_breakeven": ("prereg", "D", _H3),
    "H4_limit_only": ("prereg", "D", dict(skip_stop_entries=True)),
    "H5_fixed_risk_025": ("prereg", "T", dict(fixed_risk_pct=0.25)),
    "H6_composite_H1H2H3": ("prereg", "D+T", {**_H1, **_H2, **_H3}),
    "O1_H6_plus_fixed_risk": ("overlay", "T", {**_H1, **_H2, **_H3, "fixed_risk_pct": 0.25}),
    "E1_be_1.5R": ("exploratory", "D", dict(be_trigger_rr=1.5)),
    "E2_be_2R": ("exploratory", "D", dict(be_trigger_rr=2.0)),
    "E3_H1_plus_fri_noentry_15": ("exploratory", "T", {**_H1, "friday_no_entry_from_utc": 15.0}),
    "E4_rr_2": ("exploratory", "D", dict(rr_ratio=2.0)),
    # post-hoc sensitivity (prereg section 6): TP limit fills at the gapped open instead of being capped at +RR
    "S1_base_gapfill": ("posthoc", "-", dict(tp_gap_fill=True)),
    "S2_H1_gapfill": ("posthoc", "T", {**_H1, "tp_gap_fill": True}),
    "S3_H2_gapfill": ("posthoc", "D", {**_H2, "tp_gap_fill": True}),
    "S4_H3_gapfill": ("posthoc", "D", {**_H3, "tp_gap_fill": True}),
    "S5_H6_gapfill": ("posthoc", "D+T", {**_H1, **_H2, **_H3, "tp_gap_fill": True}),
    # prereg section 6, second addition: also ignore the reopen-bar spread for exits
    "S6_base_gf_nosp": ("posthoc", "-", dict(tp_gap_fill=True, reopen_spread_zero=True)),
    "S7_H1_gf_nosp": ("posthoc", "T", {**_H1, "tp_gap_fill": True, "reopen_spread_zero": True}),
    "S8_H6_gf_nosp": ("posthoc", "D+T", {**_H1, **_H2, **_H3, "tp_gap_fill": True, "reopen_spread_zero": True}),
}
MAIN = [n for n, v in VARIANTS.items() if v[0] != "posthoc"]
GF = [n for n, v in VARIANTS.items() if v[0] == "posthoc"]
GF_PARENT = {"S2_H1_gapfill": "H1_fri_flatten_2030", "S3_H2_gapfill": "H2_no_entry_20utc",
             "S4_H3_gapfill": "H3_no_breakeven", "S5_H6_gapfill": "H6_composite_H1H2H3",
             "S7_H1_gf_nosp": "H1_fri_flatten_2030", "S8_H6_gf_nosp": "H6_composite_H1H2H3"}
SENS_GROUPS = [("S1_base_gapfill", ["S2_H1_gapfill", "S3_H2_gapfill", "S4_H3_gapfill", "S5_H6_gapfill"]),
               ("S6_base_gf_nosp", ["S7_H1_gf_nosp", "S8_H6_gf_nosp"])]
KEEP = ["symbol", "direction", "open_T", "close_T", "r", "net_pnl", "risk_usd", "commission", "exit_reason",
        "subtype", "ret_pct", "hold_min", "risk_pct"]


def cfg_of(name: str) -> SimConfig:
    return replace(BASE, **VARIANTS[name][2])


# ── simulation jobs ──────────────────────────────────────────────────────────────

_W: dict = {}


def _world(cand_path: str, symbols: tuple):
    if "m5" not in _W:
        _W["m5"] = {s: prepare_bars(s, start=None)["M5"] for s in symbols}
        _W["specs"] = load_specs()
        _W["cands"] = pd.read_parquet(cand_path)
    return _W


def _win_dd(equity: pd.DataFrame) -> dict:
    e = equity
    return {"dev": max_drawdown_pct(e.loc[e["T"] < CUT, "equity"].to_numpy()),
            "hold": max_drawdown_pct(e.loc[e["T"] >= CUT, "equity"].to_numpy()),
            "full": max_drawdown_pct(e["equity"].to_numpy())}


def _ftmo(res, n_paths):
    days = day_table(res["equity"], res["trades"])
    return {"p10": ftmo_mc(days, FtmoRules(profit_target_pct=10.0), n_paths=n_paths),
            "p5": ftmo_mc(days, FtmoRules(profit_target_pct=5.0), n_paths=n_paths)}


def _job(a):
    name, seed, cand_path, symbols = a
    w = _world(cand_path, symbols)
    cfg = cfg_of(name)
    c = w["cands"]
    if seed is not None:
        flip = np.random.default_rng(seed).random(len(c)) < 0.5
        c = mirror(c, flip)
    res = Simulator(list(symbols), w["m5"], w["specs"], c, cfg).run()
    t = res["trades"]
    out = {"name": name, "seed": seed, "trades": t[KEEP].copy() if len(t) else t, "dd": _win_dd(res["equity"]),
           "funnel": res["funnel"]}
    if seed is None:
        out["equity"] = res["equity"]
        out["trades_full"] = t
    elif seed < 10:
        out["ftmo"] = _ftmo(res, 4000)
    return out


def cfg_hash(name: str) -> str:
    return hashlib.md5(json.dumps(asdict(cfg_of(name)), sort_keys=True, default=str).encode()).hexdigest()[:10]


def simulate_all(names: list, workers: int, symbols: tuple, cand_path: str):
    """Run (actual + N_SEEDS nulls) for every variant that is not cached for the current config; log new variants."""
    RUNS.mkdir(parents=True, exist_ok=True)
    todo = []
    for n in names:
        d = RUNS / n
        if (d / "meta.json").exists() and json.loads((d / "meta.json").read_text())["hash"] == cfg_hash(n):
            continue
        todo.append(n)
    if not todo:
        return
    jobs = [(n, s, cand_path, symbols) for n in todo for s in [None] + list(range(N_SEEDS))]
    t0 = time.time()
    with ProcessPoolExecutor(max_workers=workers) as ex:
        results = list(ex.map(_job, jobs, chunksize=4))
    for n in todo:
        d = RUNS / n
        d.mkdir(exist_ok=True)
        rs = [r for r in results if r["name"] == n]
        act = next(r for r in rs if r["seed"] is None)
        nul = sorted([r for r in rs if r["seed"] is not None], key=lambda r: r["seed"])
        act["trades_full"].to_parquet(d / "trades.parquet", index=False)
        act["equity"].to_parquet(d / "equity.parquet", index=False)
        nt = pd.concat([r["trades"].assign(seed=r["seed"]) for r in nul], ignore_index=True)
        nt.to_parquet(d / "null_trades.parquet", index=False)
        extra = {"dd_actual": act["dd"], "funnel": act["funnel"],
                 "null_dd": {str(r["seed"]): r["dd"] for r in nul},
                 "null_ftmo": {str(r["seed"]): r["ftmo"] for r in nul if "ftmo" in r}}
        (d / "extra.json").write_text(json.dumps(_clean(extra)))
        (d / "meta.json").write_text(json.dumps({"hash": cfg_hash(n), "cfg_diff": VARIANTS[n][2]}))
        log_variant(n)
    print(f"simulated {len(jobs)} runs for {todo} in {time.time() - t0:.0f}s")


def log_variant(name: str):
    RC.mkdir(parents=True, exist_ok=True)
    fp = RC / "variant_log.csv"
    row = {"variant": name, "role": VARIANTS[name][0], "class": VARIANTS[name][1],
           "config_diff": json.dumps(VARIANTS[name][2]), "null_seeds": N_SEEDS,
           "logged_at": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")}
    old = pd.read_csv(fp) if fp.exists() else pd.DataFrame()
    if len(old) and name in set(old["variant"]) and (old[old["variant"] == name]["config_diff"] == row["config_diff"]).any():
        return
    pd.concat([old, pd.DataFrame([row])], ignore_index=True).to_csv(fp, index=False)


# ── loading ──────────────────────────────────────────────────────────────────────

def load_run(name: str) -> dict:
    d = RUNS / name
    return {"trades": pd.read_parquet(d / "trades.parquet"), "equity": pd.read_parquet(d / "equity.parquet"),
            "null": pd.read_parquet(d / "null_trades.parquet"), "extra": json.loads((d / "extra.json").read_text())}


def in_win(t: pd.DataFrame, lo, hi, col="open_T"):
    return t[(t[col] >= lo) & (t[col] < hi)]


# ── statistics ───────────────────────────────────────────────────────────────────

def trade_stats(t: pd.DataFrame) -> dict:
    """t: trades of one window (END-open trades excluded)."""
    t = t[t["exit_reason"] != "END"]
    if not len(t):
        return {"n": 0}
    r = t["r"].to_numpy(float)
    gp, gl = t.loc[t.net_pnl > 0, "net_pnl"].sum(), -t.loc[t.net_pnl < 0, "net_pnl"].sum()
    sun = t[t["close_T"].dt.dayofweek == 6]
    late = t[t["open_T"].dt.hour >= 20]
    flat = t[t["exit_reason"] == "FLAT"]
    return {"n": int(len(t)), "exp_r": float(np.nanmean(r)), "total_r": float(np.nansum(r)),
            "win_pct": float((t.net_pnl > 0).mean() * 100), "pf": float(gp / gl) if gl > 0 else float("nan"),
            "worst_r": float(np.nanmin(r)), "p01_r": float(np.nanquantile(r, 0.01)),
            "sun_n": int(len(sun)), "sun_r": float(sun["r"].sum()),
            "late_n": int(len(late)), "late_r": float(late["r"].sum()),
            "late_mean": float(late["r"].mean()) if len(late) else float("nan"),
            "flat_n": int(len(flat)), "flat_r": float(flat["r"].sum()),
            "mean_risk_pct": float(t["risk_pct"].mean()), "comm_r": float((t["commission"] / t["risk_usd"]).mean())}


def monday(s: pd.Series) -> pd.Series:
    d = s.dt.tz_convert("UTC").dt.floor("D")
    return d - pd.to_timedelta(d.dt.dayofweek, unit="D")


def week_arrays(t: pd.DataFrame, weeks: pd.DatetimeIndex):
    t = t[t["exit_reason"] != "END"]
    g = t.groupby(monday(t["open_T"]))["r"].agg(["sum", "count"]).reindex(weeks, fill_value=0)
    return g["sum"].to_numpy(float), g["count"].to_numpy(float)


def paired_boot(arrs: dict, base: str, lo, hi, trades_by: dict) -> dict:
    """Paired weekly-block bootstrap of delta = E[R|variant] - E[R|base] for every variant in `arrs` keys of trades_by."""
    weeks = pd.date_range(monday(pd.Series([max(lo, pd.Timestamp("2025-06-02", tz="UTC"))])).iloc[0],
                          monday(pd.Series([min(hi, pd.Timestamp("2026-10-26", tz="UTC"))])).iloc[0], freq="7D", tz="UTC")
    W = len(weeks)
    rng = np.random.default_rng(BOOT_SEED)
    idx = rng.integers(0, W, size=(BOOT_B, W))
    wa = {n: week_arrays(in_win(t, lo, hi), weeks) for n, t in trades_by.items()}

    def exp_boot(n):
        s, c = wa[n]
        return s[idx].sum(1) / np.maximum(c[idx].sum(1), 1)

    def exp_point(n):
        s, c = wa[n]
        return s.sum() / max(c.sum(), 1)

    eb_base, e0_base = exp_boot(base), exp_point(base)
    out = {}
    for n in trades_by:
        if n == base:
            continue
        d_star = exp_boot(n) - eb_base
        d_hat = exp_point(n) - e0_base
        p = float(np.mean((d_star - d_hat) >= d_hat)) if True else None
        out[n] = {"delta": float(d_hat), "se": float(d_star.std()), "ci_lo": float(np.quantile(d_star, .025)),
                  "ci_hi": float(np.quantile(d_star, .975)), "p_one_sided": p, "_dstar": d_star}
    return out, W


def holm(p: dict) -> dict:
    items = sorted(p.items(), key=lambda kv: kv[1])
    m, adj, run = len(items), {}, 0.0
    for i, (k, v) in enumerate(items):
        run = max(run, min(1.0, (m - i) * v))
        adj[k] = run
    return adj


def reality_check(boot: dict) -> dict:
    names = list(boot)
    sig = np.array([boot[n]["se"] for n in names])
    dhat = np.array([boot[n]["delta"] for n in names])
    dstar = np.stack([boot[n]["_dstar"] for n in names], axis=1)                  # B x m
    tstar = ((dstar - dhat) / sig).max(axis=1)
    tobs = (dhat / sig)
    return {"max_t_obs": float(tobs.max()), "best": names[int(tobs.argmax())],
            "p_reality_check": float(np.mean(tstar >= tobs.max()))}


def null_stats(nt: pd.DataFrame, lo, hi) -> pd.DataFrame:
    """per-seed stats for one variant in one window."""
    rows = []
    for sd, g in in_win(nt, lo, hi).groupby("seed"):
        s = trade_stats(g)
        s["seed"] = sd
        rows.append(s)
    return pd.DataFrame(rows).set_index("seed")


def window_dd(extra: dict, key: str):
    return extra["dd_actual"][key], np.array([v[key] for v in extra["null_dd"].values()])


def tdist_p_upper(z_mean: float, se: float, n: int) -> float:
    from scipy import stats
    if se <= 0:
        return 0.5
    return float(stats.t.sf(z_mean / se, df=n - 1))


# ── analysis of one window ───────────────────────────────────────────────────────

def analyse_window(names: list, lo, hi, ddkey: str, base: str = "base") -> dict:
    runs = {n: load_run(n) for n in names}
    trades_by = {n: runs[n]["trades"] for n in names}
    boot, W = paired_boot(None, base, lo, hi, trades_by)
    p_raw = {n: boot[n]["p_one_sided"] for n in boot}
    padj = holm(p_raw)
    rc = reality_check(boot)
    res = {"window": [str(lo), str(hi)], "weeks": W, "family_size": len(boot), "reality_check": rc, "variants": {},
           "base": base, "_boot": boot}

    # null per variant
    nstat = {n: null_stats(runs[n]["null"], lo, hi) for n in names}
    ndd = {n: np.array([runs[n]["extra"]["null_dd"][str(s)][ddkey] for s in nstat[n].index]) for n in names}
    null_p = {}
    for n in names:
        a = trade_stats(in_win(runs[n]["trades"], lo, hi))
        ci = bootstrap_mean_ci(in_win(runs[n]["trades"], lo, hi).query("exit_reason != 'END'")["r"].to_numpy())
        ns = nstat[n]
        v = {"actual": a, "actual_ci95": [ci["lo"], ci["hi"]], "dd_actual": runs[n]["extra"]["dd_actual"][ddkey],
             "null": {"exp_mean": float(ns.exp_r.mean()), "exp_sd": float(ns.exp_r.std()), "n_mean": float(ns.n.mean()),
                      "worst_r_mean": float(ns.worst_r.mean()), "p01_mean": float(ns.p01_r.mean()),
                      "sun_r_mean": float(ns.sun_r.mean()), "sun_n_mean": float(ns.sun_n.mean()),
                      "late_n_mean": float(ns.late_n.mean()), "late_mean_r": float(ns.late_r.sum() / max(ns.late_n.sum(), 1)),
                      "flat_r_mean": float(ns.flat_r.mean()), "dd_mean": float(ndd[n].mean()),
                      "dd_p05": float(np.quantile(ndd[n], .05)), "mean_risk_pct": float(ns.mean_risk_pct.mean())},
             "skill_actual_minus_null": float(a["exp_r"] - ns.exp_r.mean())}
        if n != base:
            v.update({"delta_actual": boot[n]["delta"], "delta_se": boot[n]["se"],
                      "delta_ci95": [boot[n]["ci_lo"], boot[n]["ci_hi"]], "p_raw": p_raw[n], "p_holm": padj[n],
                      "p_bonf": min(1.0, p_raw[n] * len(boot))})
            d = (ns.exp_r - nstat[base].exp_r).dropna()
            dd_d = ndd[n] - ndd[base]
            se = float(d.std() / np.sqrt(len(d)))
            v["null_delta"] = {"mean": float(d.mean()), "se": se, "z": float(d.mean() / se) if se > 0 else float("nan"),
                               "p_upper": tdist_p_upper(float(d.mean()), se, len(d)), "dd_delta_mean": float(dd_d.mean())}
            null_p[n] = v["null_delta"]["p_upper"]
        res["variants"][n] = v
    nadj = holm(null_p)
    for n, p in nadj.items():
        res["variants"][n]["null_delta"]["p_holm"] = p

    # folds
    res["folds"] = {}
    for fname, f0, f1 in FOLDS:
        a, b = pd.Timestamp(f0, tz="UTC"), pd.Timestamp(f1, tz="UTC")
        if b <= lo or a >= hi:
            continue
        a, b = max(a, lo), min(b, hi)
        fb = trade_stats(in_win(runs[base]["trades"], a, b))
        nb = null_stats(runs[base]["null"], a, b)
        row = {"base_exp": fb.get("exp_r"), "base_n": fb.get("n")}
        for n in names:
            if n == base:
                continue
            fv = trade_stats(in_win(runs[n]["trades"], a, b))
            nv = null_stats(runs[n]["null"], a, b)
            dn = (nv.exp_r - nb.exp_r).mean()
            row[n] = {"n": fv.get("n"), "exp": fv.get("exp_r"), "delta": fv.get("exp_r", np.nan) - fb.get("exp_r", np.nan),
                      "null_delta": float(dn)}
        res["folds"][fname] = row
    return res


# ── mechanical dev screen (pre-registered rules) ─────────────────────────────────

def dev_screen(dev: dict) -> dict:
    V = dev["variants"]
    out = {}

    def ok_d(n):
        v = V[n]
        return bool(v["delta_actual"] > 0 and v["null_delta"]["z"] > 2)

    def dd_norm(n):                                    # DD per unit of average risk, so smaller position size is not credited
        return V[n]["null"]["dd_mean"] / V[n]["null"]["mean_risk_pct"]

    for n in ("H2_no_entry_20utc", "H3_no_breakeven", "H4_limit_only"):
        out[n] = {"rule": "D: actual delta>0 and null z>2", "advance": ok_d(n)}
    v = V["H1_fri_flatten_2030"]
    base = V["base"]
    tail = (v["actual"]["worst_r"] > base["actual"]["worst_r"]) and (v["actual"]["sun_r"] > base["actual"]["sun_r"]) \
        and (v["null"]["dd_mean"] > base["null"]["dd_mean"])
    out["H1_fri_flatten_2030"] = {"rule": "T: actual&null delta>=-0.02, worst R & Sunday R better, null DD better",
                                  "advance": bool(v["delta_actual"] >= -0.02 and v["null_delta"]["mean"] >= -0.02 and tail),
                                  "tail_ok": bool(tail)}
    v = V["H5_fixed_risk_025"]
    tail = dd_norm("H5_fixed_risk_025") >= dd_norm("base")      # less negative = better
    out["H5_fixed_risk_025"] = {"rule": "T: actual&null delta>=-0.02, null DD per unit risk not worse",
                                "advance": bool(v["delta_actual"] >= -0.02 and v["null_delta"]["mean"] >= -0.02 and tail),
                                "tail_ok": bool(tail), "dd_per_risk": [dd_norm("H5_fixed_risk_025"), dd_norm("base")]}
    out["H6_composite_H1H2H3"] = {"rule": "always a finalist", "advance": True}
    return out


# ── commands ─────────────────────────────────────────────────────────────────────

def prep(workers):
    from config import PAIRS
    symbols = tuple(PAIRS)
    specs = load_specs()
    RC.mkdir(parents=True, exist_ok=True)
    cand_path = RC / "candidates.parquet"
    if not cand_path.exists():
        cands = get_candidates(list(symbols), specs, SignalConfig(), None, False, min(workers, 6))
        cands.to_parquet(cand_path, index=False)
    return symbols, str(cand_path)


def fmt(x, nd=3, pct=False):
    if x is None or (isinstance(x, float) and x != x):
        return "nan"
    return f"{x:.{nd}f}"


def table(res: dict, title: str) -> str:
    L = [f"### {title}", "",
         "| variant | n | expR | 95% CI | PF | maxDD% | worst R | p01 R | Sun-close R (n) | late R (n) | FLAT R (n) | d vs base | 95% CI | p raw | p Holm | null expR | null d | null z | skill (act-null) |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for n, v in res["variants"].items():
        a = v["actual"]
        d = v.get("delta_actual")
        nd = v.get("null_delta")
        L.append(f"| {n} | {a['n']} | {fmt(a['exp_r'])} | [{fmt(v['actual_ci95'][0],2)}, {fmt(v['actual_ci95'][1],2)}] | {fmt(a['pf'],2)} | "
                 f"{fmt(v['dd_actual'],1)} | {fmt(a['worst_r'],1)} | {fmt(a['p01_r'],2)} | {fmt(a['sun_r'],1)} ({a['sun_n']}) | "
                 f"{fmt(a['late_r'],1)} ({a['late_n']}) | {fmt(a['flat_r'],1)} ({a['flat_n']}) | "
                 + (f"{d:+.3f} | [{v['delta_ci95'][0]:+.3f}, {v['delta_ci95'][1]:+.3f}] | {v['p_raw']:.3f} | {v['p_holm']:.3f} | "
                    if d is not None else "- | - | - | - | ")
                 + f"{fmt(v['null']['exp_mean'])} | "
                 + (f"{nd['mean']:+.3f} (se {nd['se']:.3f}) | {nd['z']:.1f} | " if nd else "- | - | ")
                 + f"{fmt(v['skill_actual_minus_null'])} |")
    L += ["", f"Weeks in window: {res['weeks']}; family size (variants vs base): {res['family_size']}; "
          f"reality check: best={res['reality_check']['best']} p={res['reality_check']['p_reality_check']:.3f}", ""]
    return "\n".join(L)


def tail_table(res: dict) -> str:
    L = ["| variant | null worst R | null p01 R | null Sun-close R (n) | null late mean R (n) | null FLAT R | null maxDD% mean (p05) | null mean risk% | null DD / risk | null d maxDD |",
         "|---|---|---|---|---|---|---|---|---|---|"]
    for n, v in res["variants"].items():
        z = v["null"]
        L.append(f"| {n} | {fmt(z['worst_r_mean'],1)} | {fmt(z['p01_mean'],2)} | {fmt(z['sun_r_mean'],1)} ({z['sun_n_mean']:.1f}) | "
                 f"{fmt(z['late_mean_r'],2)} ({z['late_n_mean']:.0f}) | {fmt(z['flat_r_mean'],1)} | {fmt(z['dd_mean'],1)} ({fmt(z['dd_p05'],1)}) | "
                 f"{fmt(z['mean_risk_pct'],3)} | {fmt(z['dd_mean']/z['mean_risk_pct'],1)} | "
                 + (f"{v['null_delta']['dd_delta_mean']:+.1f} |" if 'null_delta' in v else "- |"))
    return "\n".join(L)


def fold_table(res: dict) -> str:
    names = [n for n in res["variants"] if n != res["base"]]
    L = ["| variant | " + " | ".join(f"{f} d (null d)" for f in res["folds"]) + " | folds improved (actual) | folds improved (null) |",
         "|---|" + "---|" * (len(res["folds"]) + 2)]
    L.append("| base expR (n) | " + " | ".join(f"{fmt(r['base_exp'])} ({r['base_n']})" for r in res["folds"].values()) + " | | |")
    for n in names:
        cells, ca, cn = [], 0, 0
        for f, r in res["folds"].items():
            x = r[n]
            cells.append(f"{x['delta']:+.3f} ({x['null_delta']:+.3f})")
            ca += x["delta"] > 0
            cn += x["null_delta"] > 0
        L.append(f"| {n} | " + " | ".join(cells) + f" | {ca}/{len(cells)} | {cn}/{len(cells)} |")
    return "\n".join(L)


SEP = chr(10) * 2


def combine_corrections(*results):
    """Holm / Bonferroni / reality check over the union of all comparisons made on the same window (same bootstrap draws)."""
    boot, tag = {}, {}
    for r in results:
        for n, b in r["_boot"].items():
            boot[n] = b
            tag[n] = r
    p = {n: b["p_one_sided"] for n, b in boot.items()}
    adj = holm(p)
    rc = reality_check(boot)
    for n in boot:
        tag[n]["variants"][n]["p_holm_all"] = adj[n]
        tag[n]["variants"][n]["p_bonf_all"] = min(1.0, p[n] * len(p))
    for r in results:
        r["family_size_all"] = len(p)
        r["reality_check_all"] = rc
    return rc


def strip(res):
    res = dict(res)
    res.pop("_boot", None)
    return res


def cmd_dev(a):
    symbols, cand_path = prep(a.workers)
    simulate_all(list(VARIANTS), a.workers, symbols, cand_path)
    dev = analyse_window(MAIN, START, CUT, "dev")
    sens = [analyse_window([b] + vs, START, CUT, "dev", base=b) for b, vs in SENS_GROUPS]
    combine_corrections(dev, *sens)
    screen = dev_screen(dev)
    (RC / "dev_results.json").write_text(json.dumps(_clean({"main": strip(dev), "sens": [strip(x) for x in sens]}), indent=1))
    (RC / "finalists.json").write_text(json.dumps(_clean({"screen": screen, "finalists": [k for k, v in screen.items() if v["advance"]]}), indent=1))
    md = [table(dev, "DEV window (open_T < 2026-05-01): actual signals + random-direction null"),
          "#### Null (random direction) tail / cost diagnostics, DEV window", tail_table(dev),
          "#### Walk-forward folds, DEV only: delta vs base in expectancy R, actual (null)", fold_table(dev)]
    for r in sens:
        md += [table(r, f"DEV, post-hoc sensitivity (reference = {r['base']})"), tail_table(r), fold_table(r)]
    md += [f"Combined family (all {dev['family_size_all']} comparisons): reality check best={dev['reality_check_all']['best']} p={dev['reality_check_all']['p_reality_check']:.3f}",
           "#### Mechanical dev screen (pre-registered, original sim)", "```", json.dumps(_clean(screen), indent=1), "```"]
    (RC / "dev_report.md").write_text(SEP.join(md), encoding="utf-8")
    print(SEP.join(md))


def cmd_unseal(a):
    fin = json.loads((RC / "finalists.json").read_text())["finalists"]
    groups = [(b, [v for v in vs if GF_PARENT[v] in fin]) for b, vs in SENS_GROUPS]
    groups = [(b, vs) for b, vs in groups if vs]
    sens_names = [x for b, vs in groups for x in [b] + vs]
    names = ["base"] + fin
    for n in fin + sens_names:
        pd.DataFrame([{"variant": n, "unsealed_at": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")}]).to_csv(
            RC / "holdout_log.csv", mode="a", header=not (RC / "holdout_log.csv").exists(), index=False)
    hold = analyse_window(names, CUT, FAR, "hold")
    hold_s = [analyse_window([b] + vs, CUT, FAR, "hold", base=b) for b, vs in groups]
    full = analyse_window(MAIN, START, FAR, "full")
    full_s = [analyse_window([b] + vs, START, FAR, "full", base=b) for b, vs in SENS_GROUPS]
    combine_corrections(full, *full_s)
    k = len(fin)
    for r in [hold] + hold_s:
        kk = k if r is hold else len(r["variants"]) - 1
        for n, v in r["variants"].items():
            if "p_raw" in v:
                v["p_bonf_k"] = min(1.0, v["p_raw"] * kk)
                v["mde_80pct_onesided_05"] = 2.49 * v["delta_se"]       # (1.645 + 0.84) * se
    ftmo = {}
    for n in sorted(set(names) | set(sens_names) | {"O1_H6_plus_fixed_risk"}):
        r = load_run(n)
        days = day_table(r["equity"], r["trades"])
        ftmo[n] = {"p10": ftmo_mc(days, FtmoRules(profit_target_pct=10.0), n_paths=20_000),
                   "p5": ftmo_mc(days, FtmoRules(profit_target_pct=5.0), n_paths=20_000)}
        nf = r["extra"]["null_ftmo"]
        ftmo[n]["null_mean"] = {k2: {kk: float(np.mean([x[k2][kk] for x in nf.values()]))
                                     for kk in ("p_pass", "p_fail_max_loss", "p_unresolved_at_horizon")} for k2 in ("p10", "p5")}
    out = {"holdout": strip(hold), "holdout_sens": [strip(x) for x in hold_s], "full": strip(full), "full_sens": [strip(x) for x in full_s],
           "ftmo": ftmo, "k_finalists": k, "finalists": fin}
    (RC / "holdout_results.json").write_text(json.dumps(_clean(out), indent=1))
    md = [table(hold, "HOLDOUT (open_T >= 2026-05-01), finalists only"), "#### Null tail diagnostics, HOLDOUT", tail_table(hold)]
    for r in hold_s:
        md += [table(r, f"HOLDOUT sensitivity (reference = {r['base']})"), tail_table(r)]
    md += ["#### Walk-forward folds (all five)", fold_table(full)] + [fold_table(r) for r in full_s]
    md += [table(full, "FULL sample (dev + holdout, descriptive)")] + [table(r, f"FULL sensitivity ({r['base']})") for r in full_s]
    md += ["#### FTMO (full-sample day-block bootstrap; null_mean = mean over 10 random-direction seeds)", "```",
           json.dumps(_clean(ftmo), indent=1), "```"]
    (RC / "holdout_report.md").write_text(SEP.join(md), encoding="utf-8")
    print(SEP.join(md))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["dev", "unseal", "log"])
    ap.add_argument("--workers", type=int, default=12)
    a = ap.parse_args()
    {"dev": cmd_dev, "unseal": cmd_unseal, "log": lambda a: print(pd.read_csv(RC / "variant_log.csv").to_string())}[a.cmd](a)


if __name__ == "__main__":
    main()
