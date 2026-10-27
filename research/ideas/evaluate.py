"""
Evaluation harness for the pre-registered ideas (research/reports/ideas_prereg.md).

    python -m research.ideas.evaluate dev      [--arms A B C Z] [--seeds 200]    # base config on DEV (+null, gross)
    python -m research.ideas.evaluate robust   [--arms ...]                      # +/-20 % one-at-a-time, DEV only
    python -m research.ideas.evaluate holdout  [--arms ...] [--seeds 200]        # locked holdout, ONCE per arm (ledger)
    python -m research.ideas.evaluate report                                     # Holm / Bonferroni / plateau / FTMO
Every simulation made here is appended to research/output/ideas_variants_log.csv.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd

from research.data import load_specs
from research.metrics import summarize, bootstrap_mean_ci, day_table
from research.run import _clean
from research.ideas import idea_a, idea_b, idea_c, idea_z
from research.ideas.common import SPLIT, load_symbol_bars, source_fingerprint
from research.ideas.simx import TimedSimulator, edge_sim_config, gross_sim_config, to_gross

ARMS = {"A": idea_a, "B": idea_b, "C": idea_c, "Z": idea_z}
HERE = Path(__file__).resolve().parent
OUT = HERE.parent / "output"
CACHE = HERE.parent / "cache"
LOG = OUT / "ideas_variants_log.csv"
LEDGER = OUT / "ideas_holdout" / "ledger.json"

# +/-20 % one-at-a-time perturbations, fixed in the pre-registration
ROBUST = {
    "A": {"confirm_bars": (5, 7), "buffer_pips": (1.6, 2.4), "rr": (1.6, 2.4), "excursion_cap": (0.8, 1.2)},
    "B": {"adx_thr": (16.0, 24.0), "zone_depth": (0.8, 1.2), "stop_mult": (1.2, 1.8), "rr": (1.6, 2.4)},
    "C": {"vol_filter": (0.8, 1.2), "stop_adr": (0.20, 0.30), "rr": (1.6, 2.4), "adr_days": (8, 12)},
    "Z": {"stop_mult": (1.2, 1.8), "rr": (1.6, 2.4)},
}


# ── logging / ledger ──────────────────────────────────────────────────────────────

def log_variant(arm: str, params: dict, window: str, kind: str, n: int, exp: float) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    row = pd.DataFrame([{"utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "arm": arm,
                         "params": json.dumps(params, sort_keys=True), "window": window, "kind": kind,
                         "trades": n, "net_expR": exp}])
    row.to_csv(LOG, mode="a", header=not LOG.exists(), index=False)


def ledger_read() -> dict:
    return json.loads(LEDGER.read_text()) if LEDGER.exists() else {}


def ledger_write(d: dict) -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    LEDGER.write_text(json.dumps(d, indent=1))


# ── candidates ────────────────────────────────────────────────────────────────────

def make_params(arm: str, **over):
    mod = ARMS[arm]
    return replace(mod.Params(), **over) if over else mod.Params()


def get_cands(arm: str, params, window: str, specs: dict, refresh: bool = False) -> pd.DataFrame:
    """window 'dev': data truncated to bars closed before SPLIT, T < SPLIT.  'holdout': full data, T >= SPLIT."""
    mod = ARMS[arm]
    CACHE.mkdir(exist_ok=True)
    fp = hashlib.md5(
        f"{arm}|{window}|{sorted(params.key().items())}|{source_fingerprint(HERE / 'common.py', Path(mod.__file__))}"
        .encode()).hexdigest()[:12]
    path = CACHE / f"ideas_cand_{arm}_{window}_{fp}.parquet"
    if path.exists() and not refresh:
        return pd.read_parquet(path)
    frames = []
    for sym in mod.SYMBOLS:
        bars = load_symbol_bars(sym, cut=SPLIT if window == "dev" else None)
        df = mod.generate(sym, bars, specs[sym]["point"], params)
        if len(df):
            frames.append(df)
    c = pd.concat(frames, ignore_index=True).sort_values(["T", "symbol"]).reset_index(drop=True) if frames \
        else pd.DataFrame()
    if len(c):
        c = c[c["T"] < SPLIT] if window == "dev" else c[c["T"] >= SPLIT]
        c = c.reset_index(drop=True)
    c.to_parquet(path, index=False)
    return c


def sim_bars(symbols: list, window: str) -> dict:
    from research.data import prepare_bars
    out = {}
    for s in symbols:
        m5 = prepare_bars(s)["M5"]
        if window == "dev":
            m5 = m5[m5["time"] + pd.Timedelta(minutes=5) <= SPLIT]
        else:
            m5 = m5[m5["time"] >= SPLIT]
        out[s] = m5.reset_index(drop=True)
    return out


def run_sim(cands, symbols, m5, specs, cfg):
    return TimedSimulator(symbols, m5, specs, cands, cfg).run()


# ── null (random direction) ───────────────────────────────────────────────────────

def mirror_market(c: pd.DataFrame, flip: np.ndarray) -> pd.DataFrame:
    """Flip direction on rows where flip is True; keep the stop DISTANCE; entry at the new side's price."""
    c = c.copy()
    f = np.asarray(flip, bool)
    dist = (c["price"] - c["sl_raw"]).abs().to_numpy()
    newdir = np.where(f, np.where(c["direction"] == "BUY", "SELL", "BUY"), c["direction"])
    newprice = np.where(newdir == "BUY", c["ask"], c["bid"])
    newsl = np.where(newdir == "BUY", newprice - dist, newprice + dist)
    c["direction"] = np.where(f, newdir, c["direction"])
    c["price"] = np.where(f, newprice, c["price"])
    c["sl_raw"] = np.where(f, newsl, c["sl_raw"])
    return c


def _null_job(args):
    cand_path, symbols, window, cfg, seeds = args
    specs = load_specs()
    cands = pd.read_parquet(cand_path)
    m5 = sim_bars(symbols, window)
    out = []
    for sd in seeds:
        flip = np.ones(len(cands), bool) if sd is None else (np.random.default_rng(sd).random(len(cands)) < 0.5)
        res = TimedSimulator(symbols, m5, specs, mirror_market(cands, flip), cfg).run()
        t = res["trades"]
        t = t[t["exit_reason"] != "END"] if len(t) else t
        row = {"seed": -1 if sd is None else int(sd), "n": int(len(t)),
               "mean_r": float(t["r"].mean()) if len(t) else float("nan")}
        if sd is None and len(t):
            row["trades"] = t[["symbol", "placed_T", "r"]].assign(placed_T=lambda d: d["placed_T"].astype(str)) \
                .to_dict("records")
        out.append(row)
    return out


_POOL = None


def pool(workers: int) -> ProcessPoolExecutor:
    global _POOL
    if _POOL is None:
        _POOL = ProcessPoolExecutor(max_workers=workers)
    return _POOL


def run_null(cand_path, symbols, window, cfg, seeds: list, workers: int) -> pd.DataFrame:
    allseeds = list(seeds) + [None]
    chunks = [allseeds[i::workers] for i in range(workers)]
    jobs = [(str(cand_path), symbols, window, cfg, ch) for ch in chunks if ch]
    rows = [r for part in pool(workers).map(_null_job, jobs) for r in part]
    return pd.DataFrame(rows)


# ── statistics ────────────────────────────────────────────────────────────────────

def cluster_boot(r: np.ndarray, cluster: np.ndarray, n: int = 5000, seed: int = 3) -> dict:
    """Day-cluster bootstrap CI of the mean (ratio-of-sums estimator over resampled clusters)."""
    r = np.asarray(r, float)
    g = pd.DataFrame({"r": r, "c": cluster}).groupby("c")["r"].agg(["sum", "count"])
    if len(g) < 3:
        return {"lo": np.nan, "hi": np.nan}
    s, k = g["sum"].to_numpy(), g["count"].to_numpy()
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(g), size=(n, len(g)))
    m = s[idx].sum(1) / k[idx].sum(1)
    return {"lo": float(np.quantile(m, .025)), "hi": float(np.quantile(m, .975))}


def trade_view(res) -> pd.DataFrame:
    t = res["trades"]
    if not len(t):
        return t
    return t[t["exit_reason"] != "END"].copy()


def tables(t: pd.DataFrame) -> dict:
    if not len(t):
        return {}
    t = t.copy()
    t["month"] = t["open_T"].dt.strftime("%Y-%m")

    def agg(g):
        return {"n": int(len(g)), "expR": float(g["r"].mean()), "sumR": float(g["r"].sum()),
                "win_pct": float((g["net_pnl"] > 0).mean() * 100)}
    by_sym = {k: agg(g) for k, g in t.groupby("symbol")}
    by_mon = {k: agg(g) for k, g in t.groupby("month")}
    by_dir = {k: agg(g) for k, g in t.groupby("direction")}
    by_exit = {k: agg(g) for k, g in t.groupby("exit_reason")}
    pos = sum(1 for v in by_mon.values() if v["expR"] > 0)
    return {"by_symbol": by_sym, "by_month": by_mon, "by_direction": by_dir, "by_exit": by_exit,
            "positive_months": f"{pos}/{len(by_mon)}"}


def power_table(r: np.ndarray) -> dict:
    r = np.asarray(r, float)
    r = r[~np.isnan(r)]
    if len(r) < 3:
        return {}
    sd = float(r.std(ddof=1))
    return {"sd_r": sd, "se_mean_r": sd / np.sqrt(len(r)), "n": int(len(r)),
            "n_needed_80pct_power": {f"{e:.2f}R": int(np.ceil((2.8 * sd / e) ** 2)) for e in (0.05, 0.10, 0.20)},
            "min_detectable_edge_at_this_n_R": float(2.8 * sd / np.sqrt(len(r)))}


def evaluate_arm(arm: str, params, window: str, specs: dict, seeds: list, workers: int, kind: str,
                 outdir: Path | None = None, with_null: bool = True, with_gross: bool = True,
                 quiet: bool = False) -> dict:
    t0 = time.time()
    mod = ARMS[arm]
    symbols = mod.SYMBOLS
    cands = get_cands(arm, params, window, specs)
    cfg = edge_sim_config(params.rr)
    m5 = sim_bars(symbols, window)
    out = {"arm": arm, "window": window, "kind": kind, "params": params.key(), "n_candidates": int(len(cands))}
    if not len(cands):
        out["net"] = {"trades": 0}
        log_variant(arm, params.key(), window, kind, 0, float("nan"))
        return out

    res = run_sim(cands, symbols, m5, specs, cfg)
    t = trade_view(res)
    summ = summarize(res["trades"], res["equity"], cfg.initial_balance)
    r = t["r"].to_numpy(float) if len(t) else np.array([])
    out["funnel"] = res["funnel"]
    keep = ("trades", "win_rate_pct", "tp_rate_pct", "exit_mix_pct", "expectancy_r", "expectancy_r_ci95", "median_r",
            "std_r", "profit_factor", "avg_win_r", "avg_loss_r", "return_pct", "max_dd_equity_pct",
            "commission_in_r", "avg_hold_min", "trades_per_month", "calendar_days")
    out["net"] = {k: summ.get(k) for k in keep}
    if len(t):
        out["net"]["cluster_ci95"] = cluster_boot(r, t["open_T"].dt.strftime("%Y-%m-%d").to_numpy())
    out["power"] = power_table(r)
    out.update(tables(t))
    log_variant(arm, params.key(), window, kind, int(len(t)), float(r.mean()) if len(r) else float("nan"))

    if with_gross and len(t):
        gres = run_sim(to_gross(cands), symbols, m5, specs, gross_sim_config(cfg))
        gt = trade_view(gres)
        gs = summarize(gres["trades"], gres["equity"], cfg.initial_balance)
        mm = t.merge(gt, on=["symbol", "placed_T"], suffixes=("_n", "_g"))
        out["gross"] = {"trades": int(len(gt)), "expectancy_r": gs.get("expectancy_r"),
                        "profit_factor": gs.get("profit_factor"), "win_rate_pct": gs.get("win_rate_pct"),
                        "matched_trades": int(len(mm)),
                        "cost_in_R_matched": float((mm["r_g"] - mm["r_n"]).mean()) if len(mm) else None}
        log_variant(arm, params.key(), window, "gross", int(len(gt)), gs.get("expectancy_r") or float("nan"))

    if with_null and len(t):
        outdir = outdir or (OUT / f"ideas_{window}" / arm)
        outdir.mkdir(parents=True, exist_ok=True)
        cpath = CACHE / f"ideas_nullcand_{arm}_{window}.parquet"
        cands.to_parquet(cpath, index=False)
        nd = run_null(cpath, symbols, window, cfg, seeds, workers)
        null = nd[nd["seed"] >= 0]
        mir = nd[nd["seed"] < 0].iloc[0]
        actual = float(r.mean())
        nm, ns_ = float(null["mean_r"].mean()), float(null["mean_r"].std(ddof=1))
        out["null"] = {"K": int(len(null)), "mean": nm, "sd": ns_, "p05": float(null["mean_r"].quantile(.05)),
                       "p95": float(null["mean_r"].quantile(.95)), "mean_n_trades": float(null["n"].mean()),
                       "actual_minus_null": actual - nm, "z": (actual - nm) / ns_ if ns_ > 0 else None,
                       "p_one_sided": float((1 + (null["mean_r"] >= actual).sum()) / (len(null) + 1))}
        # paired directional skill: (R_actual - R_mirror)/2 on matched trades
        mt = pd.DataFrame(mir["trades"]) if isinstance(mir.get("trades"), list) else pd.DataFrame()
        if len(mt):
            mt["placed_T"] = pd.to_datetime(mt["placed_T"], utc=True)
            m = t[["symbol", "placed_T", "r", "open_T"]].merge(mt, on=["symbol", "placed_T"], suffixes=("_a", "_m"))
            d = ((m["r_a"] - m["r_m"]) / 2).to_numpy()
            out["skill"] = {"matched": int(len(m)), "mean_R": float(d.mean()),
                            "iid_ci95": bootstrap_mean_ci(d),
                            "cluster_ci95": cluster_boot(d, m["open_T"].dt.strftime("%Y-%m-%d").to_numpy()),
                            "mirror_all_expR": float(m["r_m"].mean())}
        log_variant(arm, params.key(), window, "null", int(len(null)), nm)
        null.to_csv(outdir / "null_runs.csv", index=False)

    out["seconds"] = round(time.time() - t0, 1)
    if outdir is not None or with_null:
        outdir = outdir or (OUT / f"ideas_{window}" / arm)
        outdir.mkdir(parents=True, exist_ok=True)
        t.to_csv(outdir / "trades.csv", index=False)
        res["equity"].to_parquet(outdir / "equity.parquet", index=False)
        (outdir / "summary.json").write_text(json.dumps(_clean(out), indent=1, default=str))
    return out


# ── stages ────────────────────────────────────────────────────────────────────────

def brief(o: dict) -> str:
    n = o.get("net", {})
    if not n.get("trades"):
        return f"{o['arm']} {o['window']}: no trades"
    s = (f"{o['arm']} {o['window']:7s} n={n['trades']:4d} expR={n['expectancy_r']:+.3f} "
         f"CI[{n['expectancy_r_ci95']['lo']:+.2f},{n['expectancy_r_ci95']['hi']:+.2f}] PF={n['profit_factor']:.2f} "
         f"win={n['win_rate_pct']:.0f}% DD={n['max_dd_equity_pct']:.0f}%")
    if "gross" in o:
        s += f" | gross={o['gross']['expectancy_r']:+.3f}"
    if "null" in o:
        s += f" | null={o['null']['mean']:+.3f}+-{o['null']['sd']:.3f} z={o['null']['z']:+.2f} p={o['null']['p_one_sided']:.3f}"
    return s


def stage_dev(arms, seeds, workers):
    specs = load_specs()
    for a in arms:
        o = evaluate_arm(a, make_params(a), "dev", specs, list(range(seeds)), workers, kind="base")
        print(brief(o), flush=True)


def stage_robust(arms, workers):
    specs = load_specs()
    rows = []
    for a in arms:
        base = make_params(a)
        for pname, (lo, hi) in ROBUST[a].items():
            for v in (lo, hi):
                p = make_params(a, **{pname: type(getattr(base, pname))(v)})
                o = evaluate_arm(a, p, "dev", specs, [], workers, kind="robust", with_null=False,
                                 outdir=OUT / "ideas_robust" / a / f"{pname}_{v}")
                n = o.get("net", {})
                rows.append({"arm": a, "param": pname, "value": v, "base_value": getattr(base, pname),
                             "trades": n.get("trades"), "net_expR": n.get("expectancy_r"),
                             "std_r": n.get("std_r"), "pf": n.get("profit_factor"),
                             "gross_expR": o.get("gross", {}).get("expectancy_r")})
                print(a, pname, v, rows[-1]["trades"], rows[-1]["net_expR"], flush=True)
    d = pd.DataFrame(rows)
    (OUT / "ideas_robust").mkdir(parents=True, exist_ok=True)
    d.to_csv(OUT / "ideas_robust" / "robust_summary.csv", index=False)


def stage_holdout(arms, seeds, workers, force):
    specs = load_specs()
    led = ledger_read()
    for a in arms:
        if a in led and not force:
            print(f"{a}: holdout already evaluated at {led[a]['utc']} - refusing (use --force; it will be flagged)")
            continue
        o = evaluate_arm(a, make_params(a), "holdout", specs, list(range(seeds)), workers,
                         kind="holdout" if a not in led else "holdout_FORCED_RERUN")
        led[a] = {"utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                  "params": make_params(a).key(), "rerun": a in led, "trades": o.get("net", {}).get("trades")}
        ledger_write(led)
        print(brief(o), flush=True)


def holm(p: dict) -> dict:
    items = sorted(p.items(), key=lambda kv: kv[1])
    m, out, run = len(items), {}, 0.0
    for i, (k, v) in enumerate(items):
        run = max(run, min(1.0, (m - i) * v))
        out[k] = run
    return out


def plateau(arm: str) -> dict:
    f = OUT / "ideas_robust" / "robust_summary.csv"
    base_f = OUT / "ideas_dev" / arm / "summary.json"
    if not f.exists() or not base_f.exists():
        return {}
    d = pd.read_csv(f)
    d = d[d["arm"] == arm]
    b = json.loads(base_f.read_text())["net"]
    base, se = b["expectancy_r"], b["std_r"] / np.sqrt(b["trades"])
    v = d["net_expR"].dropna()
    same = float((np.sign(v) == np.sign(base)).mean()) if len(v) else np.nan
    swing = float((v - base).abs().max() / se) if len(v) else np.nan
    return {"base_expR": base, "base_se": se, "variants": int(len(v)), "share_same_sign": same,
            "max_swing_in_base_se": swing, "min": float(v.min()), "max": float(v.max()),
            "share_positive": float((v > 0).mean()),
            "plateau_by_prereg_rule": bool(same >= 0.75 and swing <= 2.0)}


def stage_report(arms):
    rows, rep = [], {}
    dev, hold = {}, {}
    for a in arms:
        pd_, ph = OUT / "ideas_dev" / a / "summary.json", OUT / "ideas_holdout" / a / "summary.json"
        dev[a] = json.loads(pd_.read_text()) if pd_.exists() else None
        hold[a] = json.loads(ph.read_text()) if ph.exists() else None
    pvals = {a: hold[a]["null"]["p_one_sided"] for a in arms if hold[a] and "null" in hold[a]}
    adj = holm(pvals) if pvals else {}
    log = pd.read_csv(LOG) if LOG.exists() else pd.DataFrame()
    n_variants = int(len(log[log["kind"].isin(["base", "robust", "holdout", "bugfix", "debug", "holdout_FORCED_RERUN"])]))
    for a in arms:
        d, h = dev[a], hold[a]
        clears = bool(d and d["net"].get("trades") and d["net"]["expectancy_r"] > 0 and
                      d.get("null", {}).get("p_one_sided", 1) <= 0.05)
        rec = {"arm": a, "clears_dev": clears, "plateau": plateau(a)}
        for nm, o in (("dev", d), ("holdout", h)):
            if o and o["net"].get("trades"):
                rec[nm] = {"n": o["net"]["trades"], "expR": o["net"]["expectancy_r"],
                           "ci": o["net"]["expectancy_r_ci95"], "cluster_ci": o["net"].get("cluster_ci95"),
                           "pf": o["net"]["profit_factor"], "win": o["net"]["win_rate_pct"],
                           "gross": o.get("gross", {}).get("expectancy_r"), "null": o.get("null"),
                           "skill": o.get("skill"), "power": o.get("power")}
        if a in pvals:
            rec["holdout_p"] = pvals[a]; rec["holdout_p_holm"] = adj[a]
            rec["holdout_p_bonf_all_variants"] = min(1.0, pvals[a] * max(n_variants, 1))
        rep[a] = rec
    rep["_meta"] = {"variants_simulated_for_selection_or_checks": n_variants, "family_size_primary": len(pvals)}
    (OUT / "ideas_report.json").write_text(json.dumps(_clean(rep), indent=1, default=str))
    print(json.dumps(_clean(rep), indent=1, default=str))


def stage_ftmo(arms):
    from research.montecarlo import ftmo_mc, FtmoRules
    rep = json.loads((OUT / "ideas_report.json").read_text())
    for a in arms:
        if not rep.get(a, {}).get("clears_dev"):
            print(f"{a}: does not clear dev - FTMO Monte Carlo not run (pre-registered rule)")
            continue
        days = []
        for w in ("dev", "holdout"):
            d = OUT / f"ideas_{w}" / a
            if (d / "equity.parquet").exists():
                eq = pd.read_parquet(d / "equity.parquet")
                tr = pd.read_csv(d / "trades.csv", parse_dates=["open_T"])
                tr["open_T"] = pd.to_datetime(tr["open_T"], utc=True)
                days.append(day_table(eq, tr))
        dt = pd.concat(days)
        for tgt in (10.0, 5.0):
            print(a, tgt, json.dumps(_clean(ftmo_mc(dt, FtmoRules(profit_target_pct=tgt))), indent=1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["dev", "robust", "holdout", "report", "ftmo"])
    ap.add_argument("--arms", nargs="+", default=list(ARMS))
    ap.add_argument("--seeds", type=int, default=200)
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    if a.stage == "dev":
        stage_dev(a.arms, a.seeds, a.workers)
    elif a.stage == "robust":
        stage_robust(a.arms, a.workers)
    elif a.stage == "holdout":
        stage_holdout(a.arms, a.seeds, a.workers, a.force)
    elif a.stage == "report":
        stage_report(a.arms)
    else:
        stage_ftmo(a.arms)


if __name__ == "__main__":
    main()
