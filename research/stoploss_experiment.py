"""
Minimum-stop-floor experiment (pre-registration: research/reports/stop_floor_prereg.md).

The live bot widens any structure stop tighter than MIN_SL_PIPS (10) to exactly that and sets TP = 3 x the stop. Most
trades are pinned at the floor, and cost per R scales as 1/stop. This experiment replays the stage-0 DEV candidates
(research/output/stage0_R2/base_candidates.parquet; the candidate set does not depend on the floor) at several floors:

    C10 (control = live), F15, F20, F30, S20 (floor = max(10 pips, 20 x the decision bar's spread), per trade)

with the live settings (fixed 0.25 % risk, Friday flatten 20:00 UTC), on 60 random-direction null seeds (same flips at
every floor), one real-signal run, and 20 frictionless seeds (L1: no costs; L2: no costs, no breakeven, no Friday flatten).

    python -m research.stoploss_experiment geometry                       # candidate stop distances vs each floor (no P/L)
    python -m research.stoploss_experiment run --tag sf_dev1 --workers 6  # simulate and save raw tables (never reuses a tag)
    python -m research.stoploss_experiment analyse --tag sf_dev1          # contrasts, decomposition, verdicts

DEV only: --end may not be later than 2021-06-01 and the worker asserts that no bar or candidate at/after it is loaded.
Read-only to the live code; nothing here connects to MT5.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict, replace
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from research.data import prepare_bars, load_specs
from research.metrics import day_table, max_drawdown_pct
from research.nulltest import mirror, reflect, weekly_table, paired_skill, BOOT_SEED, N_BOOT
from research.run import OUT, _clean
from research.sim import Simulator, SimConfig

STOP_OUT = OUT / "stoploss"
CANDS_DEFAULT = OUT / "stage0_R2" / "base_candidates.parquet"
START, DEV_END = "2014-01-01", "2021-06-01"
CONTROL = "C10"
# name -> (min_sl_pips handed to the simulator, spread multiple for a per-trade floor or None)
VARIANTS: dict = {"C10": (10.0, None), "F15": (15.0, None), "F20": (20.0, None), "F30": (30.0, None), "S20": (10.0, 20.0)}
TREATMENTS = [v for v in VARIANTS if v != CONTROL]
N_SEEDS, N_FL_SEEDS, N_MIRROR_SEEDS = 60, 20, 30
DELTA = 0.02            # R: smallest change called an effect (prereg section 7)
Z_BONF = 2.24           # one-sided 0.0125 = Bonferroni over the 4 treatments
PIN_TOL_PIPS = 0.15     # a stop within this of the floor counts as pinned (prices are rounded to the symbol's digits)
MIN_TPM_RATIO, MIN_YEARS_SAME_SIGN = 0.8, 5
STAGE0_NULL_R, STAGE0_ACTUAL_R, STAGE0_TRADES = -0.150, -0.131, 3970


# ── configuration ───────────────────────────────────────────────────────────────────────────

def base_config() -> SimConfig:
    """The `--live-settings` base of stage 0: fixed 0.25 % risk, Friday flatten at 20:00 UTC, everything else default."""
    return SimConfig(fixed_risk_pct=0.25, friday_flatten_utc=20.0)


def variant_config(name: str, base: Optional[SimConfig] = None) -> SimConfig:
    return replace(base or base_config(), min_sl_pips=VARIANTS[name][0])


def frictionless_config(cfg: SimConfig, level: str) -> SimConfig:
    """L1: no commission, no spread, no spread filter (breakeven and Friday flatten stay).
    L2: L1 plus no breakeven and no Friday flatten (pure stop-and-target geometry)."""
    c = replace(cfg, commission_per_lot_side=0.0, spread_mult=0.0, use_spread_filter=False)
    if level == "L2":
        c = replace(c, breakeven=False, friday_flatten_utc=None)
    elif level != "L1":
        raise ValueError(level)
    return c


def pip_size(spec: dict) -> float:
    """Same convention as sim.size_position: 10 points for 3/5-digit symbols, one point otherwise (so gold's pip is 0.01)."""
    return spec["point"] * (10 if spec["digits"] in (3, 5) else 1)


def apply_spread_floor(cands: pd.DataFrame, specs: dict, mult: Optional[float], min_pips: float) -> pd.DataFrame:
    """Per-trade floor max(min_pips, mult x decision-bar spread) by widening `sl_raw` in the candidate frame (the sim only takes
    a scalar floor). Rows whose spread floor is below the scalar floor are left alone so the sim does its own widening there."""
    if mult is None or not len(cands):
        return cands
    c = cands.copy()
    point = c["symbol"].map({s: sp["point"] for s, sp in specs.items()}).to_numpy(float)
    pip = c["symbol"].map({s: pip_size(sp) for s, sp in specs.items()}).to_numpy(float)
    floor = mult * c["spread_pts"].to_numpy(float) * point
    price = c["price"].to_numpy(float)
    dist = np.abs(price - c["sl_raw"].to_numpy(float))
    widen = (floor > min_pips * pip) & (dist < floor)
    buy = (c["direction"] == "BUY").to_numpy()
    c["sl_raw"] = np.where(widen, np.where(buy, price - floor, price + floor), c["sl_raw"].to_numpy(float))
    return c


def assert_dev_window(cands: pd.DataFrame, m5: dict, end: str) -> None:
    """No bar at or after `end` may be loaded and no candidate may be dated at or after it (FINAL stays untouched)."""
    cut = pd.Timestamp(end, tz="UTC")
    for s, df in m5.items():
        if len(df) and pd.to_datetime(df["time"], utc=True).max() >= cut:
            raise AssertionError(f"{s}: M5 bars at or after {end} were loaded")
    if len(cands):
        t = pd.to_datetime(cands["T"], utc=True)
        if t.max() >= cut:
            raise AssertionError(f"candidates dated at or after {end} (latest {t.max()})")


# ── per-simulation measurements ────────────────────────────────────────────────────────────

def enrich(trades: pd.DataFrame, specs: dict) -> pd.DataFrame:
    """Finished trades (END excluded, as summarize does) with stop size in pips and the R-components of the cost."""
    if trades is None or not len(trades):
        return pd.DataFrame()
    t = trades[trades["exit_reason"] != "END"].copy()
    if not len(t):
        return t
    pipm = {s: pip_size(sp) for s, sp in specs.items()}
    ptm = {s: sp["point"] for s, sp in specs.items()}
    pip = t["symbol"].map(pipm).to_numpy(float)
    point = t["symbol"].map(ptm).to_numpy(float)
    dist = (t["entry"] - t["sl0"]).abs().to_numpy(float)
    t["stop_pips"] = dist / pip
    t["comm_r"] = (t["commission"] / t["risk_usd"]).to_numpy(float)
    t["gross_r"] = (t["gross_pnl"] / t["risk_usd"]).to_numpy(float)
    t["spread_r"] = t["spread_pts"].to_numpy(float) * point / dist       # entry spread only: a proxy, not additive
    t["spread_pips"] = t["spread_pts"].to_numpy(float) * point / pip
    return t


COMP_SUMS = ["n", "sum_r", "sum_r2", "sum_comm_r", "sum_gross_r", "sum_spread_r", "sum_stop_pips", "n_pinned", "sum_hold",
             "n_fillbar_stop"]


def composition(trades: pd.DataFrame, specs: dict, floor_pips: float, spread_mult: Optional[float]) -> pd.DataFrame:
    """Sums per (symbol, entry subtype, exit reason); pooled over seeds they give means, shares and decompositions."""
    t = enrich(trades, specs)
    cols = ["symbol", "subtype", "exit_reason"] + COMP_SUMS
    if not len(t):
        return pd.DataFrame(columns=cols)
    floor_i = np.full(len(t), float(floor_pips))
    if spread_mult is not None:
        floor_i = np.maximum(floor_i, spread_mult * t["spread_pips"].to_numpy(float))
    t["pinned"] = (t["stop_pips"].to_numpy(float) <= floor_i + PIN_TOL_PIPS).astype(int)
    t["fillbar_stop"] = ((t["hold_min"] == 0) & t["exit_reason"].isin(["SL", "BE"])).astype(int)
    t["r2"] = t["r"] ** 2
    g = t.groupby(["symbol", "subtype", "exit_reason"], as_index=False).agg(
        n=("r", "size"), sum_r=("r", "sum"), sum_r2=("r2", "sum"), sum_comm_r=("comm_r", "sum"),
        sum_gross_r=("gross_r", "sum"), sum_spread_r=("spread_r", "sum"), sum_stop_pips=("stop_pips", "sum"),
        n_pinned=("pinned", "sum"), sum_hold=("hold_min", "sum"), n_fillbar_stop=("fillbar_stop", "sum"))
    return g[cols]


def matched_split(tv: pd.DataFrame, tc: pd.DataFrame):
    """Same order (symbol, placed_T) at floor v and at the control versus orders that exist at only one of them.
    Returns (summary dict, transition table by control exit reason and floor-v exit reason)."""
    def keyed(t):
        if t is None or not len(t):
            return pd.DataFrame(columns=["r", "exit_reason"], index=pd.MultiIndex.from_arrays([[], []], names=["symbol", "placed_T"]))
        t = t[t["exit_reason"] != "END"]
        k = t.set_index(["symbol", "placed_T"])[["r", "exit_reason"]]
        return k[~k.index.duplicated(keep="first")]
    a, b = keyed(tv), keyed(tc)
    common = a.index.intersection(b.index)
    only_v, only_c = a.index.difference(b.index), b.index.difference(a.index)
    m = a.loc[common].join(b.loc[common], lsuffix="_v", rsuffix="_c")
    summ = {"n_common": int(len(common)), "sum_v_common": float(m["r_v"].sum()), "sum_c_common": float(m["r_c"].sum()),
            "n_only_v": int(len(only_v)), "sum_only_v": float(a.loc[only_v, "r"].sum()),
            "n_only_c": int(len(only_c)), "sum_only_c": float(b.loc[only_c, "r"].sum())}
    if len(m):
        m["dr"] = m["r_v"].astype(float) - m["r_c"].astype(float)
        tr = m.groupby(["exit_reason_c", "exit_reason_v"], as_index=False).agg(n=("dr", "size"), sum_dr=("dr", "sum"))
    else:
        tr = pd.DataFrame(columns=["exit_reason_c", "exit_reason_v", "n", "sum_dr"])
    return summ, tr


def sim_row(res: dict, initial_balance: float) -> dict:
    """Headline numbers of one simulation, computed as metrics.summarize does (without its 10,000-draw bootstrap)."""
    tr, eq = res["trades"], res["equity"]
    done = tr[tr["exit_reason"] != "END"] if tr is not None and len(tr) else pd.DataFrame()
    row = {"trades": int(len(done)), "n_end": int(len(tr) - len(done)) if tr is not None and len(tr) else 0}
    if len(eq):
        row["return_pct"] = float((eq["balance"].iloc[-1] / initial_balance - 1) * 100)
        row["max_dd_pct"] = max_drawdown_pct(eq["equity"].to_numpy())
        row["calendar_days"] = int(max((eq["T"].iloc[-1] - eq["T"].iloc[0]).days, 1))
    if len(done):
        r = done["r"].to_numpy(float)
        wins, losses = done[done["net_pnl"] > 0], done[done["net_pnl"] < 0]
        gl = -losses["net_pnl"].sum()
        row.update(expectancy_r=float(np.nanmean(r)), sd_r=float(np.nanstd(r, ddof=1)) if len(r) > 1 else np.nan,
                   win_rate_pct=float(len(wins) / len(done) * 100),
                   profit_factor=float(wins["net_pnl"].sum() / gl) if gl > 0 else np.nan,
                   avg_hold_min=float(done["hold_min"].mean()), worst_r=float(np.nanmin(r)),
                   buy_share=float((done["direction"] == "BUY").mean()),
                   commission_in_r=float((done["commission"] / done["risk_usd"]).mean()))
    for k, v in dict(res.get("funnel") or {}).items():
        row[f"f_{k}"] = v
    return row


def day_stats(res: dict) -> dict:
    """FTMO-style daily exposure: Prague-midnight days, intraday equity low relative to the day's starting balance."""
    if not len(res["equity"]):
        return {}
    d = day_table(res["equity"], res["trades"])
    return {"n_days": int(len(d)), "worst_low_pct": float(d["low_pct"].min()), "worst_day_pnl_pct": float(d["pnl_pct"].min()),
            "days_low_le_3": int((d["low_pct"] <= -3).sum()), "days_low_le_5": int((d["low_pct"] <= -5).sum())}


# ── simulation worker ────────────────────────────────────────────────────────────────────

_STATE: dict = {}


def _load(cand_path, symbols, start, end):
    """Load specs, candidates and M5 bars once per worker process; the end date goes to prepare_bars for every symbol."""
    key = (str(cand_path), tuple(symbols), start, end)
    if _STATE.get("key") != key:
        specs = load_specs()
        cands = pd.read_parquet(cand_path)
        m5 = {s: prepare_bars(s, start=start, end=end)["M5"] for s in symbols}
        assert_dev_window(cands, m5, end)
        _STATE.clear()
        _STATE.update(key=key, specs=specs, cands=cands, m5=m5)
    return _STATE["specs"], _STATE["cands"], _STATE["m5"]


def _work(task: dict) -> dict:
    """One seed (None = the real signals) at every variant. Null kinds: N = `reflect` geometry (all seeds; primary), NM = the
    original `mirror` geometry (first n_mirror seeds, reported next to it), L1 / L2 = frictionless on `reflect` (first n_fl seeds)."""
    sd, symbols = task["seed"], task["symbols"]
    specs, cands, m5 = _load(task["cand_path"], symbols, task["start"], task["end"])
    base = base_config()
    if sd is None:
        cs_kind, kinds = {"A": cands}, ["A"]
    else:
        flip = np.random.default_rng(sd).random(len(cands)) < 0.5            # identical to nulltest._run_seeds
        cs_kind = {"N": reflect(cands, flip), "L1": None, "L2": None}
        cs_kind["L1"] = cs_kind["L2"] = cs_kind["N"]
        kinds = ["N"] + (["NM"] if sd < task["n_mirror"] else []) + (["L1", "L2"] if sd < task["n_fl"] else [])
        if "NM" in kinds:
            cs_kind["NM"] = mirror(cands, flip)
    out = {"rows": [], "weekly": [], "comp": [], "matched": [], "trans": []}
    for kind in kinds:
        control_trades, cs = None, cs_kind[kind]
        for name, (min_pips, smult) in VARIANTS.items():                    # control first
            cfg = variant_config(name, base)
            if kind in ("L1", "L2"):
                cfg = frictionless_config(cfg, kind)
            res = Simulator(symbols, m5, specs, apply_spread_floor(cs, specs, smult, min_pips), cfg).run()
            row = {"seed": -1 if sd is None else sd, "variant": name, "kind": kind, **sim_row(res, cfg.initial_balance)}
            if kind in ("N", "NM", "A"):
                row.update(day_stats(res) if kind != "NM" else {})
                w = weekly_table(res["trades"])
                w.insert(0, "seed", row["seed"]); w.insert(0, "variant", name); w.insert(0, "kind", kind)
                out["weekly"].append(w)
                c = composition(res["trades"], specs, min_pips, smult)
                c.insert(0, "seed", row["seed"]); c.insert(0, "variant", name); c.insert(0, "kind", kind)
                out["comp"].append(c)
                if name == CONTROL:
                    control_trades = res["trades"]
                else:
                    summ, tr = matched_split(res["trades"], control_trades)
                    out["matched"].append({"kind": kind, "variant": name, "seed": row["seed"], **summ})
                    tr.insert(0, "seed", row["seed"]); tr.insert(0, "variant", name); tr.insert(0, "kind", kind)
                    out["trans"].append(tr)
            out["rows"].append(row)
    return out


# ── orchestration ────────────────────────────────────────────────────────────────────────────

def _check_end(end: Optional[str]) -> str:
    if not end or pd.Timestamp(end) > pd.Timestamp(DEV_END):
        sys.exit(f"--end must be given and no later than {DEV_END}: the FINAL window is not to be read")
    return end


def _provenance(argv, cand_path: Path, n_seeds: int, n_fl: int, end: str, start: str, n_mirror: int = N_MIRROR_SEEDS) -> dict:
    def git(*a):
        try:
            return subprocess.run(["git", *a], capture_output=True, text=True, timeout=30,
                                  cwd=Path(__file__).resolve().parent.parent).stdout
        except Exception:
            return ""
    return {"git_head": git("rev-parse", "HEAD").strip(),
            "git_dirty_diff_sha256": hashlib.sha256(git("diff", "HEAD").encode()).hexdigest(),
            "candidates_sha256": hashlib.sha256(Path(cand_path).read_bytes()).hexdigest(), "candidates": str(cand_path),
            "argv": list(argv), "start": start, "end": end, "n_seeds": n_seeds, "n_fl_seeds": n_fl, "n_mirror_seeds": n_mirror,
            "null_geometry_primary": "reflect", "null_geometry_comparison": "mirror",
            "variants": {k: list(v) for k, v in VARIANTS.items()}, "sim_config_base": asdict(base_config()),
            "bootstrap_seed": BOOT_SEED, "n_boot": N_BOOT, "delta_r": DELTA, "z_bonf": Z_BONF}


def run_cmd(a) -> None:
    end = _check_end(a.end)
    outdir = STOP_OUT / a.tag
    if outdir.exists():
        sys.exit(f"{outdir} exists: output tags are never reused")
    from config import PAIRS
    symbols = list(PAIRS)
    outdir.mkdir(parents=True)
    tasks = [{"seed": sd, "cand_path": str(a.cands), "symbols": symbols, "start": a.start, "end": end, "n_fl": a.fl_seeds,
              "n_mirror": a.mirror_seeds}
             for sd in [None] + list(range(a.seeds))]
    parts, t0 = [], time.time()
    with ProcessPoolExecutor(max_workers=a.workers) as ex:
        futs = [ex.submit(_work, t) for t in tasks]
        for i, f in enumerate(as_completed(futs), 1):
            parts.append(f.result())
            print(f"[{i}/{len(tasks)}] {time.time() - t0:.0f}s", flush=True)
    save_outputs(parts, outdir, _provenance(sys.argv[1:], a.cands, a.seeds, a.fl_seeds, end, a.start, a.mirror_seeds))
    print(f"done in {time.time() - t0:.0f}s -> {outdir}")


def save_outputs(parts: list, outdir: Path, provenance: dict) -> None:
    cat = lambda key: pd.concat([x for p in parts for x in p[key]], ignore_index=True)   # noqa: E731
    pd.DataFrame([r for p in parts for r in p["rows"]]).to_csv(outdir / "rows.csv", index=False)
    cat("weekly").to_parquet(outdir / "weekly.parquet", index=False)
    cat("comp").to_parquet(outdir / "comp.parquet", index=False)
    cat("trans").to_parquet(outdir / "trans.parquet", index=False)
    pd.DataFrame([m for p in parts for m in p["matched"]]).to_csv(outdir / "matched.csv", index=False)
    (outdir / "provenance.json").write_text(json.dumps(_clean(provenance), indent=1))


def geometry_cmd(a) -> None:
    """Descriptive only (no P/L): how much of each pair's structure stops fall below each floor."""
    specs, cands = load_specs(), pd.read_parquet(a.cands)
    pipm = {s: pip_size(sp) for s, sp in specs.items()}
    pointm = {s: sp["point"] for s, sp in specs.items()}
    pips = (cands["price"] - cands["sl_raw"]).abs() / cands["symbol"].map(pipm)
    spread_floor = 20.0 * cands["spread_pts"] * cands["symbol"].map(pointm) / cands["symbol"].map(pipm)
    rows = []
    for s, g in cands.assign(pips=pips, sfl=spread_floor).groupby("symbol"):
        r = {"symbol": s, "candidates": len(g), "p10_pips": g.pips.quantile(.1), "median_pips": g.pips.median(),
             "p90_pips": g.pips.quantile(.9)}
        for f in (10, 15, 20, 30):
            r[f"below_{f}"] = float((g.pips < f).mean() * 100)
        r["S20_binds_pct"] = float(((g.sfl > 10) & (g.pips < g.sfl)).mean() * 100)
        rows.append(r)
    df = pd.DataFrame(rows).round(2)
    print(df.to_string(index=False))
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(a.out, index=False)


# ── analysis ────────────────────────────────────────────────────────────────────────────────

class Boot:
    """One set of week-resampling multiplicities shared by every contrast (paired across all runs and variants)."""

    def __init__(self, n_weeks: int, n_boot: int = N_BOOT, seed: int = BOOT_SEED):
        rng = np.random.default_rng(seed)
        draws = rng.integers(0, n_weeks, size=(n_boot, n_weeks))
        self.mult = np.zeros((n_weeks, n_boot))
        for b in range(n_boot):
            self.mult[:, b] = np.bincount(draws[b], minlength=n_weeks)

    def est(self, n: np.ndarray, s: np.ndarray):
        """(point, bootstrap draws) of the mean over seeds of (sum R / trades). n, s: seeds x weeks."""
        with np.errstate(invalid="ignore", divide="ignore"):
            point = float(np.mean(s.sum(axis=1) / n.sum(axis=1)))
            boots = ((s @ self.mult) / (n @ self.mult)).mean(axis=0)
        return point, boots


def build_sets(weekly: pd.DataFrame, weeks: pd.Index, exclude_symbols=(), only_symbols=None, only_years=None) -> dict:
    """{(variant, kind): (n, s)} dense seeds x weeks arrays from the stored weekly tables, optionally subset."""
    w = weekly
    if exclude_symbols:
        w = w[~w["symbol"].isin(exclude_symbols)]
    if only_symbols is not None:
        w = w[w["symbol"].isin(only_symbols)]
    if only_years is not None:
        w = w[(pd.to_datetime(w["week"]) + pd.Timedelta(days=3)).dt.year.isin(only_years)]      # ISO year: the week of 2013-12-30 is 2014
    out = {}
    for (kind, variant), g in w.groupby(["kind", "variant"]):
        seeds = sorted(g["seed"].unique())
        si = {sd: i for i, sd in enumerate(seeds)}
        n, s = np.zeros((len(seeds), len(weeks))), np.zeros((len(seeds), len(weeks)))
        wi = weeks.get_indexer(pd.to_datetime(g["week"]))
        ri = g["seed"].map(si).to_numpy()
        np.add.at(n, (ri, wi), g["n"].to_numpy(float))
        np.add.at(s, (ri, wi), g["sum_r"].to_numpy(float))
        out[(variant, kind)] = (n, s)
    return out


def contrast(boot: Boot, sets: dict, terms) -> dict:
    """sum_i coef_i x est(set_i) with a paired week-block bootstrap SE. terms: [((variant, kind), coef), ...]."""
    point, draws = 0.0, 0.0
    for key, coef in terms:
        p, b = boot.est(*sets[key])
        point += coef * p
        draws = draws + coef * b
    se = float(np.nanstd(draws, ddof=1))
    return {"est": float(point), "se": se, "lo": float(point - 1.96 * se), "hi": float(point + 1.96 * se)}


def cost_effect(d: float, se: float) -> str:
    if d >= DELTA and d - Z_BONF * se > 0:
        return "REDUCES"
    if d <= -DELTA and d + Z_BONF * se < 0:
        return "INCREASES"
    return "NONE"


def real_effect(d: float, se: float) -> str:
    if d >= DELTA and d - Z_BONF * se > 0:
        return "HELPS"
    if d <= -DELTA and d + Z_BONF * se < 0:
        return "HURTS"
    return "NEUTRAL (noise)" if se > DELTA else "NEUTRAL"


def verdict(cost: str, real: str) -> str:
    if real == "HELPS":
        return "HELPS"
    if real == "HURTS":
        return "HURTS"
    return "COST-ONLY" if cost == "REDUCES" else "NEUTRAL"


def nominate(real: str, tpm_ratio: float, years_same_sign: int) -> bool:
    return real == "HELPS" and tpm_ratio >= MIN_TPM_RATIO and years_same_sign >= MIN_YEARS_SAME_SIGN


def mix_decomposition(comp: pd.DataFrame, v: str, c: str = CONTROL, kind: str = "N") -> dict:
    """Change in pooled mean R between floor v and control c, split into the exit-mix effect and the within-reason payoff
    effect (midpoint weights, an exact identity: sum p m is the mean R), and into commission and gross (spread-inclusive) parts."""
    def pooled(name):
        g = comp[(comp["variant"] == name) & (comp["kind"] == kind)]
        n = g["n"].sum()
        by = g.groupby("exit_reason")[["n", "sum_r"]].sum()
        return n, by["n"] / n, by["sum_r"] / by["n"], g["sum_comm_r"].sum() / n, g["sum_gross_r"].sum() / n, g["sum_r"].sum() / n
    nv, pv, mv, cv, gv, rv = pooled(v)
    nc, pc, mc, cc, gc, rc = pooled(c)
    reasons = sorted(set(pv.index) | set(pc.index))
    pv, pc = pv.reindex(reasons).fillna(0.0), pc.reindex(reasons).fillna(0.0)
    mv, mc = mv.reindex(reasons).fillna(0.0), mc.reindex(reasons).fillna(0.0)
    pbar, mbar = (pv + pc) / 2, (mv + mc) / 2
    mix = ((pv - pc) * mbar), ((mv - mc) * pbar)
    return {"delta_mean_r": float(rv - rc), "delta_commission_r": float(-(cv - cc)), "delta_gross_r": float(gv - gc),
            "mix_effect": float(mix[0].sum()), "payoff_effect": float(mix[1].sum()),
            "by_reason": {r: {"share_c": float(pc[r]), "share_v": float(pv[r]), "mean_r_c": float(mc[r]), "mean_r_v": float(mv[r]),
                              "mix": float(mix[0][r]), "payoff": float(mix[1][r])} for r in reasons}}


def pooled_stats(comp: pd.DataFrame, by=None) -> pd.DataFrame:
    """Pooled per-trade means from the composition sums, optionally by a key (symbol / subtype / exit_reason)."""
    g = comp.groupby(by)[COMP_SUMS].sum() if by else comp[COMP_SUMS].sum().to_frame().T
    out = pd.DataFrame({"n": g["n"], "mean_r": g["sum_r"] / g["n"], "comm_r": g["sum_comm_r"] / g["n"],
                        "gross_r": g["sum_gross_r"] / g["n"], "spread_r": g["sum_spread_r"] / g["n"],
                        "stop_pips": g["sum_stop_pips"] / g["n"], "pinned_pct": 100 * g["n_pinned"] / g["n"],
                        "hold_min": g["sum_hold"] / g["n"], "fillbar_stop_pct": 100 * g["n_fillbar_stop"] / g["n"]})
    return out


def analyse(outdir: Path, n_boot: int = N_BOOT, small_boot: int = 500) -> dict:
    rows = pd.read_csv(outdir / "rows.csv")
    weekly = pd.read_parquet(outdir / "weekly.parquet")
    comp = pd.read_parquet(outdir / "comp.parquet")
    matched = pd.read_csv(outdir / "matched.csv")
    trans = pd.read_parquet(outdir / "trans.parquet")
    res: dict = {"provenance": json.loads((outdir / "provenance.json").read_text())}
    weeks = pd.Index(sorted(pd.to_datetime(weekly["week"]).unique()))
    boot = Boot(len(weeks), n_boot)
    sets = build_sets(weekly, weeks)
    months = rows[(rows.kind == "A") & (rows.variant == CONTROL)]["calendar_days"].iloc[0] / 30.4

    def sub(kind, v, col):
        g = rows[(rows.kind == kind) & (rows.variant == v)][col]
        return g
    # reproduction of stage 0 by the control
    nc = sub("NM", CONTROL, "expectancy_r")                 # stage 0 used the mirror geometry: that is what must be reproduced
    res["reproduction"] = {"geometry": "mirror", "n_seeds": int(len(nc)), "control_null_mean_r": float(nc.mean()), "stage0_null_r": STAGE0_NULL_R,
                           "control_actual_trades": int(sub("A", CONTROL, "trades").iloc[0]), "stage0_trades": STAGE0_TRADES,
                           "control_actual_r": float(sub("A", CONTROL, "expectancy_r").iloc[0]), "stage0_actual_r": STAGE0_ACTUAL_R}
    res["reproduction"]["ok"] = bool(abs(nc.mean() - STAGE0_NULL_R) <= 0.01 and res["reproduction"]["control_actual_trades"] == STAGE0_TRADES
                                     and abs(res["reproduction"]["control_actual_r"] - STAGE0_ACTUAL_R) <= 0.002)

    years = sorted(set((pd.to_datetime(weekly["week"]) + pd.Timedelta(days=3)).dt.year))
    tpm_c = float(sub("A", CONTROL, "trades").iloc[0] / months)
    variants = {}
    for v in VARIANTS:
        d: dict = {"null": {"mean_r": float(sub("N", v, "expectancy_r").mean()), "sd_seed_mean_r": float(sub("N", v, "expectancy_r").std()),
                            "trades": float(sub("N", v, "trades").mean()), "win_rate_pct": float(sub("N", v, "win_rate_pct").mean()),
                            "hold_min": float(sub("N", v, "avg_hold_min").mean()), "max_dd_pct": float(sub("N", v, "max_dd_pct").mean()),
                            "worst_r_mean": float(sub("N", v, "worst_r").mean()), "return_pct": float(sub("N", v, "return_pct").mean()),
                            "worst_low_pct_mean": float(sub("N", v, "worst_low_pct").mean()),
                            "days_low_le_3_mean": float(sub("N", v, "days_low_le_3").mean())},
                   "actual": {c: float(sub("A", v, c).iloc[0]) for c in
                              ("trades", "expectancy_r", "win_rate_pct", "profit_factor", "return_pct", "max_dd_pct", "worst_r",
                               "avg_hold_min", "worst_low_pct", "worst_day_pnl_pct", "days_low_le_3", "days_low_le_5")}}
        d["actual"]["trades_per_month"] = d["actual"]["trades"] / months
        d["actual"]["r_per_month"] = d["actual"]["trades"] * d["actual"]["expectancy_r"] / months
        d["null"]["r_per_month"] = d["null"]["trades"] * d["null"]["mean_r"] / months
        d["null_pooled"] = pooled_stats(comp[(comp.variant == v) & (comp.kind == "N")]).iloc[0].to_dict()
        d["actual_pooled"] = pooled_stats(comp[(comp.variant == v) & (comp.kind == "A")]).iloc[0].to_dict()
        for lv in ("L1", "L2"):
            x = sub(lv, v, "expectancy_r")
            d[lv] = {"mean_r": float(x.mean()), "sd": float(x.std()), "se": float(x.std() / np.sqrt(len(x))), "n_seeds": int(len(x)),
                     "win_rate_pct": float(sub(lv, v, "win_rate_pct").mean()), "trades": float(sub(lv, v, "trades").mean())}
        # skill via the repo's paired estimator, as stage 0 does
        a_t = weekly[(weekly.kind == "A") & (weekly.variant == v)][["week", "symbol", "n", "sum_r"]]
        nulls = {int(sd): g[["week", "symbol", "n", "sum_r"]] for sd, g in weekly[(weekly.kind == "N") & (weekly.variant == v)].groupby("seed")}
        d["skill_paired"] = paired_skill(a_t, nulls, n_boot)
        d["skill_paired_no_gold"] = paired_skill(a_t, nulls, n_boot, exclude_symbols=("XAUUSD",))
        d["null_mirror"] = {"mean_r": float(sub("NM", v, "expectancy_r").mean()), "n_seeds": int(len(sub("NM", v, "expectancy_r"))),
                            "sd_seed_mean_r": float(sub("NM", v, "expectancy_r").std()), "trades": float(sub("NM", v, "trades").mean()),
                            "win_rate_pct": float(sub("NM", v, "win_rate_pct").mean())}
        d["null_mirror_pooled"] = pooled_stats(comp[(comp.variant == v) & (comp.kind == "NM")]).iloc[0].to_dict()
        nm_nulls = {int(sd): g[["week", "symbol", "n", "sum_r"]] for sd, g in weekly[(weekly.kind == "NM") & (weekly.variant == v)].groupby("seed")}
        d["skill_vs_mirror"] = paired_skill(a_t, nm_nulls, n_boot) if nm_nulls else {}
        d["funnel_null_mean"] = {c[2:]: float(sub("N", v, c).mean()) for c in rows.columns if c.startswith("f_") and sub("N", v, c).notna().any()}
        d["funnel_actual"] = {c[2:]: float(sub("A", v, c).iloc[0]) for c in rows.columns if c.startswith("f_") and sub("A", v, c).notna().any()}
        variants[v] = d

    def contrasts(st, bt, v):
        return {"D_null": contrast(bt, st, [((v, "N"), 1), ((CONTROL, "N"), -1)]),
                "D_act": contrast(bt, st, [((v, "A"), 1), ((CONTROL, "A"), -1)]),
                "D_skill": contrast(bt, st, [((v, "A"), 1), ((v, "N"), -1), ((CONTROL, "A"), -1), ((CONTROL, "N"), 1)]),
                "skill": contrast(bt, st, [((v, "A"), 1), ((v, "N"), -1)])}
    sets_ng = build_sets(weekly, weeks, exclude_symbols=("XAUUSD",))
    for v in TREATMENTS:
        d = variants[v]
        d.update(contrasts(sets, boot, v))
        d["D_null_mirror"] = contrast(boot, sets, [((v, "NM"), 1), ((CONTROL, "NM"), -1)])
        d["null_decomposition_mirror"] = mix_decomposition(comp, v, kind="NM")
        d["cost_effect_mirror"] = cost_effect(d["D_null_mirror"]["est"], d["D_null_mirror"]["se"])
        d["no_gold"] = contrasts(sets_ng, boot, v)
        d["cost_effect"] = cost_effect(d["D_null"]["est"], d["D_null"]["se"])
        d["real_effect"] = real_effect(d["D_act"]["est"], d["D_act"]["se"])
        d["verdict"] = verdict(d["cost_effect"], d["real_effect"])
        # descriptive: by symbol and by year (500 resamples)
        d["by_symbol"], d["by_year"] = {}, {}
        sb = Boot(len(weeks), small_boot)
        for s in sorted(set(weekly["symbol"])):
            st = build_sets(weekly, weeks, only_symbols=[s])
            if (v, "N") in st and (CONTROL, "A") in st:
                c = contrasts(st, sb, v)
                d["by_symbol"][s] = {k: c[k] for k in ("D_null", "D_act")}
                d["by_symbol"][s]["N"] = boot.est(*st[(v, "N")])[0]
                d["by_symbol"][s]["A"] = boot.est(*st[(v, "A")])[0]
        for y in years:
            st = build_sets(weekly, weeks, only_years=[y])
            if (v, "N") in st and (CONTROL, "A") in st:
                c = contrasts(st, sb, v)
                d["by_year"][int(y)] = {k: c[k] for k in ("D_null", "D_act")}
        same = sum(1 for y in d["by_year"].values() if np.sign(y["D_act"]["est"]) == np.sign(d["D_act"]["est"]) and d["D_act"]["est"] != 0)
        d["years_same_sign_act"], d["n_years"] = int(same), len(d["by_year"])
        d["trades_per_month_ratio"] = d["actual"]["trades_per_month"] / tpm_c
        d["nominated_for_final"] = nominate(d["real_effect"], d["trades_per_month_ratio"], d["years_same_sign_act"])
        d["null_decomposition"] = mix_decomposition(comp, v)
        d["actual_decomposition"] = mix_decomposition(comp, v, kind="A")
        mm = matched[(matched.kind == "N") & (matched.variant == v)]
        d["matched_null"] = {k: float(mm[k].mean()) for k in ("n_common", "sum_v_common", "sum_c_common", "n_only_v", "sum_only_v",
                                                              "n_only_c", "sum_only_c")}
        ma = matched[(matched.kind == "A") & (matched.variant == v)]
        d["matched_actual"] = {k: float(ma[k].iloc[0]) for k in ("n_common", "sum_v_common", "sum_c_common", "n_only_v", "sum_only_v",
                                                                 "n_only_c", "sum_only_c")}
        tn = trans[(trans.kind == "N") & (trans.variant == v)].groupby(["exit_reason_c", "exit_reason_v"])[["n", "sum_dr"]].sum()
        ns = max(int(res["provenance"].get("n_seeds", 1)), 1)
        d["transitions_null_per_seed"] = {f"{a}->{b}": {"n": float(r.n / ns), "sum_dr": float(r.sum_dr / ns)} for (a, b), r in tn.iterrows()}
    res["variants"] = variants
    res["tables"] = {"by_subtype_null_mirror": {v: pooled_stats(comp[(comp.variant == v) & (comp.kind == "NM")], "subtype").round(4).to_dict("index")
                                                for v in VARIANTS},
                     "by_exit_null_mirror": {v: pooled_stats(comp[(comp.variant == v) & (comp.kind == "NM")], "exit_reason").round(4).to_dict("index")
                                             for v in VARIANTS},
                     "by_subtype_null": {v: pooled_stats(comp[(comp.variant == v) & (comp.kind == "N")], "subtype").round(4).to_dict("index")
                                         for v in VARIANTS},
                     "by_subtype_actual": {v: pooled_stats(comp[(comp.variant == v) & (comp.kind == "A")], "subtype").round(4).to_dict("index")
                                           for v in VARIANTS},
                     "by_symbol_null": {v: pooled_stats(comp[(comp.variant == v) & (comp.kind == "N")], "symbol").round(4).to_dict("index")
                                        for v in VARIANTS},
                     "by_symbol_actual": {v: pooled_stats(comp[(comp.variant == v) & (comp.kind == "A")], "symbol").round(4).to_dict("index")
                                          for v in VARIANTS},
                     "by_exit_null": {v: pooled_stats(comp[(comp.variant == v) & (comp.kind == "N")], "exit_reason").round(4).to_dict("index")
                                      for v in VARIANTS}}
    return res


def analyse_cmd(a) -> None:
    outdir = STOP_OUT / a.tag
    res = analyse(outdir)
    (outdir / "summary.json").write_text(json.dumps(_clean(res), indent=1))
    rep = res["reproduction"]
    print("reproduction of stage 0 by the control:", json.dumps(_clean(rep)))
    for v, d in res["variants"].items():
        line = (f"{v}: null(reflect) {d['null']['mean_r']:+.4f} (n={d['null']['trades']:.0f}) null(mirror) {d['null_mirror']['mean_r']:+.4f} actual {d['actual']['expectancy_r']:+.4f} "
                f"(n={d['actual']['trades']:.0f}) L1 {d['L1']['mean_r']:+.4f} L2 {d['L2']['mean_r']:+.4f}")
        if v != CONTROL:
            line += (f" | D_null {d['D_null']['est']:+.4f}+-{d['D_null']['se']:.4f} D_null_mirror {d['D_null_mirror']['est']:+.4f} D_act {d['D_act']['est']:+.4f}+-{d['D_act']['se']:.4f}"
                     f" -> {d['cost_effect']} / {d['real_effect']} / {d['verdict']}")
        print(line)
    print(f"summary -> {outdir / 'summary.json'}")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    r = sp.add_parser("run")
    r.add_argument("--tag", required=True)
    r.add_argument("--seeds", type=int, default=N_SEEDS)
    r.add_argument("--fl-seeds", type=int, default=N_FL_SEEDS)
    r.add_argument("--mirror-seeds", type=int, default=N_MIRROR_SEEDS, help="seeds also run with the original `mirror` null geometry")
    r.add_argument("--workers", type=int, default=6)
    r.add_argument("--cands", default=str(CANDS_DEFAULT))
    r.add_argument("--start", default=START)
    r.add_argument("--end", default=DEV_END)
    g = sp.add_parser("geometry")
    g.add_argument("--cands", default=str(CANDS_DEFAULT))
    g.add_argument("--out", default=None)
    an = sp.add_parser("analyse")
    an.add_argument("--tag", required=True)
    a = ap.parse_args(argv)
    {"run": run_cmd, "geometry": geometry_cmd, "analyse": analyse_cmd}[a.cmd](a)


if __name__ == "__main__":
    main()
