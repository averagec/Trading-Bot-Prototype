"""
Null test: does the signal's DIRECTION (plus its structure-stop placement) carry information?

Takes the exact candidate set the live logic produced (same cycle times, same entry prices, same stop distances,
same sizing, same gates, same costs) and flips each trade's direction at random (stop/target mirrored about the entry
price). If the real strategy has directional skill, its result should sit in the upper tail of this null
distribution. If it sits in the lower tail, the signals are systematically wrong-way.

Also reports the deterministic 'mirror everything' result, and the stage-0 paired skill estimate
(research/reports/stage0_prereg.md): skill = actual mean R minus mean of the null seeds' mean R, with a paired
week-block bootstrap (whole ISO weeks resampled jointly across the actual run and every null seed).

    python -m research.nulltest --seeds 30
    python -m research.nulltest --tag stage0_R2 --start 2014-01-01 --end 2021-06-01 --seeds 200 --workers 12 --live-settings
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from research.data import prepare_bars, load_specs
from research.metrics import summarize
from research.run import get_candidates, OUT, _clean
from research.signals import SignalConfig
from research.sim import Simulator, SimConfig

KILL_UB_R = 0.05      # stage-0 kill threshold on the upper bound (stage0_prereg.md section 5)
Z_UB = 1.96           # two-sided 95 % bound used for the kill test
Z_LB = 2.24           # one-sided 0.0125 (Bonferroni over the two kill-set runs) used for the PROCEED test
N_BOOT = 2000
BOOT_SEED = 12345


def mirror(c: pd.DataFrame, flip: np.ndarray) -> pd.DataFrame:
    c = c.copy()
    f = np.asarray(flip, bool)
    c.loc[f, "direction"] = c.loc[f, "direction"].map({"BUY": "SELL", "SELL": "BUY"})
    c.loc[f, "sl_raw"] = 2 * c.loc[f, "price"] - c.loc[f, "sl_raw"]
    cur = np.where(c["direction"] == "BUY", c["ask"], c["bid"])
    sub = np.where(c["direction"] == "BUY",
                   np.where(c["price"] < cur, "LIMIT", "STOP"),
                   np.where(c["price"] > cur, "LIMIT", "STOP"))
    c.loc[f, "subtype"] = sub[f]
    return c


def reflect(c: pd.DataFrame, flip: np.ndarray) -> pd.DataFrame:
    """Symmetric random-direction null: flipped trades are reflected about the decision-time mid price, entry, stop and all.
    A BUY LIMIT 20 pips below the market becomes a SELL LIMIT 20 pips above it with the same stop distance, so the entry
    mechanics (limit vs stop, distance from the market, fill-bar exposure) are the same as the real trade's. `mirror` keeps the
    entry price and turns a far retracement limit into a far stop order in the other direction, a trade the strategy never
    takes, whose simulator artefacts (fill-bar stop-outs at the bar open, gap fills) the real trades barely touch."""
    c = c.copy()
    f = np.asarray(flip, bool)
    mid = ((c["ask"] + c["bid"]) / 2).to_numpy(float)
    c.loc[f, "direction"] = c.loc[f, "direction"].map({"BUY": "SELL", "SELL": "BUY"})
    c.loc[f, "price"] = (2 * mid - c["price"].to_numpy(float))[f]
    c.loc[f, "sl_raw"] = (2 * mid - c["sl_raw"].to_numpy(float))[f]
    cur = np.where(c["direction"] == "BUY", c["ask"], c["bid"])
    sub = np.where(c["direction"] == "BUY",
                   np.where(c["price"] < cur, "LIMIT", "STOP"),
                   np.where(c["price"] > cur, "LIMIT", "STOP"))
    c.loc[f, "subtype"] = sub[f]
    return c


NULL_GEOMETRIES = {"mirror": mirror, "reflect": reflect}


def build_sim_config(live_settings: bool, clean_conventions: bool) -> SimConfig:
    """`live_settings`: fixed 0.25 % risk and the Friday 20:00 UTC flatten. `clean_conventions`: neutral fill-bar rule and targets
    paid at gapped prices, the settings with the smallest residual bias in the frictionless random-direction null."""
    cfg = SimConfig(fixed_risk_pct=0.25, friday_flatten_utc=20.0) if live_settings else SimConfig()
    return replace(cfg, intrabar="neutral", tp_gap_fill=True) if clean_conventions else cfg


# ── stage-0 estimator ─────────────────────────────────────────────────────────────

def weekly_table(trades: pd.DataFrame) -> pd.DataFrame:
    """Per (ISO week start of open_T, symbol): trade count `n` and sum of R `sum_r`.
    END trades (still open when the data ended) are excluded, exactly as `summarize` excludes them."""
    cols = ["week", "symbol", "n", "sum_r"]
    if trades is None or not len(trades):
        return pd.DataFrame(columns=cols)
    t = trades[trades["exit_reason"] != "END"]
    if not len(t):
        return pd.DataFrame(columns=cols)
    day = pd.to_datetime(t["open_T"], utc=True).dt.tz_convert("UTC").dt.floor("D")
    week = (day - pd.to_timedelta(day.dt.dayofweek, unit="D")).dt.tz_localize(None)
    g = pd.DataFrame({"week": week.to_numpy(), "symbol": t["symbol"].to_numpy(), "r": t["r"].to_numpy(float)})
    out = g.groupby(["week", "symbol"], as_index=False).agg(n=("r", "size"), sum_r=("r", "sum"))
    return out[cols]


def _filter(tbl: pd.DataFrame, exclude_symbols=(), exclude_symbol_years=()) -> pd.DataFrame:
    if exclude_symbols:
        tbl = tbl[~tbl["symbol"].isin(exclude_symbols)]
    for sym, year in exclude_symbol_years:
        tbl = tbl[~((tbl["symbol"] == sym) & (pd.to_datetime(tbl["week"]).dt.year == year))]
    return tbl


def paired_skill(actual: pd.DataFrame, nulls: dict, n_boot: int = N_BOOT, seed: int = BOOT_SEED,
                 exclude_symbols=(), exclude_symbol_years=(), only_symbols=None, only_years=None) -> dict:
    """skill = actual mean R minus mean over null seeds of their mean R; paired week-block bootstrap.

    `actual` and every table in `nulls` ({seed: table}) are `weekly_table` outputs. Whole weeks are resampled with
    replacement, jointly for the actual run and all null seeds, so price-path noise shared by actual and null cancels.
    Subsetting (symbols / years / excluded symbol-years) filters the same stored tables, so by-pair and by-year
    estimates and the with/without-gold variants need no re-run (they are post-hoc subsets of one simulation)."""
    def prep(tbl):
        tbl = _filter(tbl, exclude_symbols, exclude_symbol_years)
        if only_symbols is not None:
            tbl = tbl[tbl["symbol"].isin(only_symbols)]
        if only_years is not None:
            tbl = tbl[pd.to_datetime(tbl["week"]).dt.year.isin(only_years)]
        return tbl

    actual = prep(actual)
    nulls = {k: prep(v) for k, v in nulls.items()}
    weeks = pd.Index(sorted(set(actual["week"]).union(*[set(v["week"]) for v in nulls.values()])))
    if len(weeks) < 2 or not len(actual) or not nulls:
        return {"trades": int(actual["n"].sum()) if len(actual) else 0, "skill_r": float("nan"), "ub95": float("nan"),
                "lb_bonf": float("nan"), "se_skill": float("nan"), "weeks": int(len(weeks))}

    def dense(tbl):
        g = tbl.groupby("week")[["n", "sum_r"]].sum().reindex(weeks, fill_value=0.0)
        return g["n"].to_numpy(float), g["sum_r"].to_numpy(float)

    a_n, a_s = dense(actual)
    ks = sorted(nulls)
    n_n = np.vstack([dense(nulls[k])[0] for k in ks])
    n_s = np.vstack([dense(nulls[k])[1] for k in ks])

    skill = a_s.sum() / a_n.sum() - np.mean(n_s.sum(axis=1) / n_n.sum(axis=1))

    rng = np.random.default_rng(seed)
    W = len(weeks)
    mult = np.zeros((W, n_boot))
    draws = rng.integers(0, W, size=(n_boot, W))
    for b in range(n_boot):
        mult[:, b] = np.bincount(draws[b], minlength=W)
    with np.errstate(invalid="ignore", divide="ignore"):
        a_mean = (a_s @ mult) / (a_n @ mult)
        null_mean = ((n_s @ mult) / (n_n @ mult)).mean(axis=0)
    boot = a_mean - null_mean
    se = float(np.nanstd(boot, ddof=1))
    return {"trades": int(a_n.sum()), "weeks": int(W), "null_seeds": int(len(ks)),
            "actual_expectancy_r": float(a_s.sum() / a_n.sum()),
            "null_mean_expectancy_r": float(np.mean(n_s.sum(axis=1) / n_n.sum(axis=1))),
            "skill_r": float(skill), "se_skill": se,
            "ci95": [float(skill - Z_UB * se), float(skill + Z_UB * se)],
            "ub95": float(skill + Z_UB * se), "lb_bonf": float(skill - Z_LB * se),
            "kill_threshold_r": KILL_UB_R}


def stage0_estimates(actual: pd.DataFrame, nulls: dict, n_boot: int = N_BOOT) -> dict:
    """The pre-registered estimates for one run: all pairs, without gold, and the clean subset (GBPUSD 2019 removed,
    the only genuine data defect left in DEV), plus the descriptive by-year and by-pair skill (no decisions)."""
    est = {
        "all": paired_skill(actual, nulls, n_boot),
        "no_gold": paired_skill(actual, nulls, n_boot, exclude_symbols=("XAUUSD",)),
        "clean": paired_skill(actual, nulls, n_boot, exclude_symbol_years=(("GBPUSD", 2019),)),
        "clean_no_gold": paired_skill(actual, nulls, n_boot, exclude_symbols=("XAUUSD",),
                                      exclude_symbol_years=(("GBPUSD", 2019),)),
    }
    years = sorted(set(pd.to_datetime(actual["week"]).dt.year)) if len(actual) else []
    syms = sorted(set(actual["symbol"])) if len(actual) else []
    est["by_year"] = {int(y): paired_skill(actual, nulls, 500, only_years=[y]) for y in years}
    est["by_symbol"] = {s: paired_skill(actual, nulls, 500, only_symbols=[s]) for s in syms}
    return est


# ── simulation workers ────────────────────────────────────────────────────────────

def _run_seeds(args):
    seeds, cand_path, symbols, specs, start, sim_cfg, end, *rest = args
    flipper = NULL_GEOMETRIES[rest[0] if rest else "mirror"]       # optional 8th element; old callers get the old geometry
    sentinel_missing = bool(rest[1]) if len(rest) > 1 else False   # optional 9th element
    cands = pd.read_parquet(cand_path)
    m5 = {s: prepare_bars(s, start=start, end=end, sentinel_missing=sentinel_missing)["M5"] for s in symbols}
    rows, weekly = [], []
    for sd in seeds:
        flip = np.ones(len(cands), bool) if sd is None else (np.random.default_rng(sd).random(len(cands)) < 0.5)
        res = Simulator(symbols, m5, specs, flipper(cands, flip), sim_cfg).run()
        s = summarize(res["trades"], res["equity"], sim_cfg.initial_balance)
        tr = res["trades"]
        done = tr[tr["exit_reason"] != "END"] if tr is not None and len(tr) else tr
        row = {"seed": -1 if sd is None else sd, "trades": s.get("trades"),
               "expectancy_r": s.get("expectancy_r"), "profit_factor": s.get("profit_factor"),
               "return_pct": s.get("return_pct"), "max_dd_pct": s.get("max_dd_equity_pct"),
               "win_rate_pct": s.get("win_rate_pct"),
               "buy_share": float((done["direction"] == "BUY").mean()) if done is not None and len(done) else None}
        for k, v in dict(res.get("funnel") or {}).items():
            row[f"f_{k}"] = v
        rows.append(row)
        w = weekly_table(tr)
        w.insert(0, "seed", -1 if sd is None else sd)
        weekly.append(w)
    return rows, weekly


def _provenance(args, sig, sim) -> dict:
    def git(*a):
        try:
            return subprocess.run(["git", *a], capture_output=True, text=True, timeout=30,
                                  cwd=Path(__file__).resolve().parent.parent).stdout
        except Exception:
            return ""
    diff = git("diff", "HEAD")
    return {"git_head": git("rev-parse", "HEAD").strip(), "git_dirty_diff_sha256": hashlib.sha256(diff.encode()).hexdigest(),
            "argv": sys.argv[1:], "signal_config": asdict(sig), "sim_config": asdict(sim),
            "bootstrap_seed": BOOT_SEED, "n_boot": N_BOOT, "kill_ub_r": KILL_UB_R, "z_ub": Z_UB, "z_lb": Z_LB}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=30)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--tag", default="null")
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None, help="drop every bar at or after this UTC date, on all timeframes (DEV-only runs)")
    ap.add_argument("--sweep-mode", default="faithful", choices=["faithful", "fixed"],
                    help="faithful = live code (sweep never fires); fixed = a working sweep (a different strategy)")
    ap.add_argument("--live-settings", action="store_true",
                    help="simulate the live bot's current settings: fixed 0.25 % risk and the Friday 20:00 UTC flatten")
    ap.add_argument("--symbols", nargs="*", default=None, help="default: every pair in config.PAIRS")
    ap.add_argument("--clean-conventions", action="store_true",
                    help="neutral fill-bar rule and targets paid at gapped prices (smallest residual null bias); default off")
    ap.add_argument("--sentinel-missing", action="store_true",
                    help="treat the broker's 50-point placeholder spread (2020-05..2021-09) as missing and impute it "
                         "(research.data.sentinel_mask); default off")
    ap.add_argument("--null-geometry", default="mirror", choices=sorted(NULL_GEOMETRIES),
                    help="mirror = stop mirrored about the entry (the original null, biased); reflect = whole trade reflected about the decision price")
    args = ap.parse_args()

    from config import PAIRS
    symbols, specs = list(args.symbols or PAIRS), load_specs()
    sig = SignalConfig(sweep_mode=args.sweep_mode)
    sim = build_sim_config(args.live_settings, args.clean_conventions)
    cands = get_candidates(symbols, specs, sig, args.start, False, args.workers, end=args.end,
                           sentinel_missing=args.sentinel_missing)
    outdir = OUT / args.tag
    outdir.mkdir(parents=True, exist_ok=True)
    cand_path = outdir / "base_candidates.parquet"
    cands.to_parquet(cand_path, index=False)

    # actual
    t0 = time.time()
    m5 = {s: prepare_bars(s, start=args.start, end=args.end, sentinel_missing=args.sentinel_missing)["M5"]
          for s in symbols}
    res = Simulator(symbols, m5, specs, cands, sim).run()
    actual = summarize(res["trades"], res["equity"], sim.initial_balance)

    # null + mirror
    seeds = list(range(args.seeds)) + [None]
    chunks = [seeds[i::args.workers] for i in range(args.workers)]
    jobs = [(ch, cand_path, symbols, specs, args.start, sim, args.end, args.null_geometry, args.sentinel_missing)
            for ch in chunks if ch]
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        parts = list(ex.map(_run_seeds, jobs))
    rows = [r for part_rows, _ in parts for r in part_rows]
    weekly = pd.concat([w for _, ws in parts for w in ws], ignore_index=True)
    df = pd.DataFrame(rows)
    null = df[df["seed"] >= 0]
    mir = df[df["seed"] < 0].iloc[0].to_dict()

    out = {
        "provenance": _provenance(args, sig, sim),
        "actual": {k: actual.get(k) for k in ("trades", "expectancy_r", "profit_factor", "return_pct",
                                              "max_dd_equity_pct", "win_rate_pct")},
        "mirror_all": mir,
        "null_n": int(len(null)),
        "null_expectancy_r": {"mean": float(null.expectancy_r.mean()), "sd": float(null.expectancy_r.std()),
                              "p05": float(null.expectancy_r.quantile(.05)), "p95": float(null.expectancy_r.quantile(.95))},
        "null_return_pct": {"mean": float(null.return_pct.mean()), "p05": float(null.return_pct.quantile(.05)),
                            "p95": float(null.return_pct.quantile(.95))},
        "p_value_upper (null >= actual)": float((null.expectancy_r >= actual["expectancy_r"]).mean()),
        "p_value_lower (null <= actual)": float((null.expectancy_r <= actual["expectancy_r"]).mean()),
        "z": float((actual["expectancy_r"] - null.expectancy_r.mean()) / null.expectancy_r.std()),
        # diagnostics the pre-registration asks for: does the null see the same funnel, and is it symmetric?
        "diagnostics": {
            "actual_funnel": dict(res.get("funnel") or {}),
            "null_funnel_mean": {c[2:]: float(null[c].mean()) for c in null.columns if c.startswith("f_")},
            "buy_share": {"actual": float((res["trades"].query("exit_reason != 'END'")["direction"] == "BUY").mean())
                          if len(res["trades"]) else None, "null_mean": float(null["buy_share"].mean())},
            "mirror_check": {"(actual+mirror_all)/2": float((actual["expectancy_r"] + mir["expectancy_r"]) / 2),
                             "null_mean": float(null.expectancy_r.mean())},
        },
    }
    tr = res["trades"]
    if len(tr) > 1 and len(null) > 1:
        tr.to_csv(outdir / "actual_trades.csv", index=False)
        weekly_actual = weekly_table(tr)
        weekly.to_parquet(outdir / "null_weekly.parquet", index=False)
        weekly_actual.to_parquet(outdir / "actual_weekly.parquet", index=False)
        nulls = {int(sd): g.drop(columns="seed") for sd, g in weekly[weekly["seed"] >= 0].groupby("seed")}
        out["skill"] = stage0_estimates(weekly_actual, nulls)
    df.to_csv(outdir / "null_runs.csv", index=False)
    (outdir / "null_summary.json").write_text(json.dumps(_clean(out), indent=1))
    print(json.dumps(_clean({k: v for k, v in out.items() if k not in ("provenance",)}), indent=1))
    print(f"done in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
