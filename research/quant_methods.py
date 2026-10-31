"""
Structural / method experiments on the cost and framework side (pre-registration: research/reports/quant_methods_prereg.md).

None of the arms predicts direction. Every arm is measured on the random-direction `reflect` null (40 seeds, the same flips for every
arm) and, as a second reading, on the one deterministic run of the real DEV signals. DEV only (2014-01-01 to 2021-06-01); the FINAL
window is refused. Nothing here touches MT5, sim.py, signals.py or live code: arms are SimConfig overrides, candidate-frame transforms
made BEFORE the simulator sees them, or a thin Simulator subclass defined in this file.

    python -m research.quant_methods geometry                         # stop distance in ATR units (no P/L)
    python -m research.quant_methods run --set methods --tag quant_m1 --workers 8
    python -m research.quant_methods analyse --set methods --tag quant_m1
    python -m research.quant_methods run --set gates --tag quant_g1 --workers 8 --seeds 20

Arm sets: `methods` (pre-registered grid), `gates` (counterfactual removal of one live gate at a time, descriptive).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from research.data import prepare_bars, load_specs
from research.nulltest import reflect, weekly_table
from research.run import OUT, _clean
from research.sim import Simulator, SimConfig
from research.stoploss_experiment import (Boot, build_sets, contrast, composition, pooled_stats, sim_row, day_stats,
                                          assert_dev_window, pip_size, CANDS_DEFAULT, base_config)

QOUT = OUT                                   # outputs go to research/output/quant_*
START, DEV_END = "2014-01-01", "2021-06-01"
CONTROL = "B0"
DELTA = 0.02          # R per trade: smallest cost-side change called an effect (same as the stop-floor study)
ATR_N = 14


# ── causal ATR of H1 bars ───────────────────────────────────────────────────────────────────

def h1_atr_series(h1: pd.DataFrame, n: int = ATR_N) -> tuple[np.ndarray, np.ndarray]:
    """(close_time_ns, atr) per H1 bar. ATR = simple mean of the true range of the last n bars, available when the bar has CLOSED
    (open time + 1 h). `h1` needs time (bar open, UTC), high, low, close."""
    t = pd.to_datetime(h1["time"], utc=True)
    hi, lo, cl = h1["high"].to_numpy(float), h1["low"].to_numpy(float), h1["close"].to_numpy(float)
    prev = np.concatenate([[np.nan], cl[:-1]])
    tr = np.maximum.reduce([hi - lo, np.abs(hi - prev), np.abs(lo - prev)])
    tr[0] = hi[0] - lo[0]
    atr = pd.Series(tr).rolling(n, min_periods=n).mean().to_numpy()
    close_ns = (t + pd.Timedelta(hours=1)).astype("int64").to_numpy()
    return close_ns, atr


def atr_at(close_ns: np.ndarray, atr: np.ndarray, T_ns: np.ndarray) -> np.ndarray:
    """ATR of the last H1 bar whose close time is <= T (NaN before the first full window). Uses closed bars only."""
    i = np.searchsorted(close_ns, T_ns, side="right") - 1
    out = np.full(len(T_ns), np.nan)
    ok = i >= 0
    out[ok] = atr[i[ok]]
    return out


def add_atr(cands: pd.DataFrame, h1_by_symbol: dict) -> pd.DataFrame:
    """Candidate frame plus `atr_h1` (price units) at the decision time T, from H1 bars closed at or before T."""
    c = cands.copy()
    c["atr_h1"] = np.nan
    for s, g in c.groupby("symbol"):
        close_ns, atr = h1_atr_series(h1_by_symbol[s])
        c.loc[g.index, "atr_h1"] = atr_at(close_ns, atr, pd.to_datetime(g["T"], utc=True).astype("int64").to_numpy())
    return c


def apply_atr_floor(cands: pd.DataFrame, k: Optional[float]) -> pd.DataFrame:
    """Stop at least k x ATR(H1, 14) from the entry price: widen `sl_raw` where it is tighter. The simulator's own 10-pip floor
    still applies after this. Candidates without an ATR (warm-up) are left alone. k=None: unchanged."""
    if k is None or not len(cands):
        return cands
    c = cands.copy()
    price, atr = c["price"].to_numpy(float), c["atr_h1"].to_numpy(float)
    dist = np.abs(price - c["sl_raw"].to_numpy(float))
    floor = k * atr
    widen = np.isfinite(floor) & (dist < floor)
    buy = (c["direction"] == "BUY").to_numpy()
    c["sl_raw"] = np.where(widen, np.where(buy, price - floor, price + floor), c["sl_raw"].to_numpy(float))
    return c


# ── a thin simulator subclass: time stop ─────────────────────────────────────────────────────

class TimeStopSim(Simulator):
    """Simulator + a maximum holding time (minutes) measured from the fill. At the end of the first M5 step with T - open_T >=
    time_stop_min a still-open position is closed at that bar's close (BUY at bid, SELL at ask = close + spread), AFTER the bar's own
    SL/TP check, reason 'TIME'. Applies to market and pending entries alike. Uses only information at T."""

    def __init__(self, *a, time_stop_min: Optional[float] = None, **k):
        super().__init__(*a, **k)
        self.time_stop_min = time_stop_min

    def _process_step(self, n: int, T: pd.Timestamp):
        super()._process_step(n, T)
        if self.time_stop_min is None:
            return
        for p in list(self.positions):
            if p.open_n == n or (T - p.open_T).total_seconds() / 60.0 < self.time_stop_min:
                continue
            j = self.idx[p.sym][n]
            if j < 0:
                continue
            c, sp = self.C[p.sym][j], self.S[p.sym][j]
            self._close(p, c if p.dir == "BUY" else c + sp, "TIME", T)


class _AllowAll:
    def check(self, *a, **k):
        return {"allowed": True, "reason": "correlation filter disabled (counterfactual)"}


class GateSim(TimeStopSim):
    """TimeStopSim + optional switch-off of the live correlation filter (counterfactual for the gate study)."""

    def __init__(self, *a, disable_correlation: bool = False, **k):
        super().__init__(*a, **k)
        self.disable_correlation = disable_correlation

    def run(self) -> dict:
        import research.sim as _sim
        orig = _sim.correlation_filter
        if self.disable_correlation:
            _sim.correlation_filter = _AllowAll()
        try:
            return super().run()
        finally:
            _sim.correlation_filter = orig


# ── arms ────────────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Arm:
    name: str
    family: str                                  # control | M1 | M2 | M3 | M4 | M5 | M6 | C | G
    sim_over: tuple = ()                         # ((SimConfig field, value), ...)
    atr_k: Optional[float] = None                # ATR floor multiple
    time_stop_min: Optional[float] = None
    no_corr: bool = False
    cand_file: Optional[str] = None              # alternative candidate parquet (e.g. regime off)
    desc: str = ""

    def cfg(self, base: SimConfig) -> SimConfig:
        return replace(base, **dict(self.sim_over)) if self.sim_over else base


def _a(name, family, desc="", **kw):
    over = kw.pop("over", {})
    return Arm(name, family, tuple(sorted(over.items())), desc=desc, **kw)


METHOD_ARMS: dict = {a.name: a for a in [
    _a("B0", "control", "live settings: fixed 0.25 % risk, Friday flatten 20:00 UTC, 10-pip floor, spread cap 10 % of the stop"),
    # M1 ATR-scaled stop floor (pair- and volatility-aware version of the stop-floor study)
    _a("M1_k10", "M1", "stop >= 1.0 x ATR(H1,14)", atr_k=1.0),
    _a("M1_k15", "M1", "stop >= 1.5 x ATR(H1,14)", atr_k=1.5),
    _a("M1_k20", "M1", "stop >= 2.0 x ATR(H1,14)", atr_k=2.0),
    # M2 spread-to-stop cap (live: 10 %)
    _a("M2_c7", "M2", "skip if spread > 7 % of the stop", over={"max_spread_to_stop_pct": 7.0}),
    _a("M2_c5", "M2", "skip if spread > 5 % of the stop", over={"max_spread_to_stop_pct": 5.0}),
    _a("M2_c3", "M2", "skip if spread > 3 % of the stop", over={"max_spread_to_stop_pct": 3.0}),
    # M3 no entries from a UTC hour (the live code allows entries until 22:00)
    _a("M3_h20", "M3", "no new orders / fills from 20:00 UTC", over={"no_entry_from_utc": 20.0}),
    _a("M3_h21", "M3", "no new orders / fills from 21:00 UTC", over={"no_entry_from_utc": 21.0}),
    # M4 concurrent positions (exposure arms, judged on R per month and drawdown, not as cost levers)
    _a("M4_o1", "M4", "max 1 open position", over={"max_open_trades": 1}),
    _a("M4_o2", "M4", "max 2 open positions", over={"max_open_trades": 2}),
    # M5 reward-to-risk multiple (theory-neutral on a driftless price; cost and convention arm)
    _a("M5_rr2", "M5", "target 2R", over={"rr_ratio": 2.0}),
    _a("M5_rr4", "M5", "target 4R", over={"rr_ratio": 4.0}),
    # M6 negative control: a 24 h time stop should do nothing but change conventions
    _a("M6_t24", "M6", "close after 24 h (negative control)", time_stop_min=24 * 60.0),
]}

GATE_ARMS: dict = {a.name: a for a in [
    _a("B0", "control", "live settings"),
    _a("G_spread_rel", "G", "no relative spread cap (abs cap stays)", over={"max_spread_to_stop_pct": 1e9}),
    _a("G_spread_all", "G", "no spread filter at all (abs and rel)", over={"use_spread_filter": False}),
    _a("G_corr", "G", "no correlation filter", no_corr=True),
    _a("G_maxopen", "G", "no max-open gate", over={"max_open_trades": 50}),
    _a("G_daily", "G", "no 3 % daily gate", over={"daily_dd_pct": 1e9}),
    _a("G_monthly", "G", "no monthly +5 % pause", over={"monthly_target_pct": None}),
    _a("G_cooldown", "G", "no same-direction cooldown", over={"cooldown_min": 0}),
    _a("G_flatten", "G", "no Friday flatten", over={"friday_flatten_utc": None}),
    _a("G_fresh", "G", "positions/pending re-read inside the cycle (stale_snapshot off)", over={"stale_snapshot": False}),
    _a("G_regime", "G", "regime filter off (candidates rebuilt with use_regime False)", cand_file=str(OUT / "quant_regimeoff" / "base_candidates.parquet")),
]}

EXT_ARMS: dict = {a.name: a for a in [          # parameter revision 1 (logged in the prereg): where does the ATR-floor gain end?
    _a("B0", "control", "live settings"),
    _a("M1_k25", "M1", "stop >= 2.5 x ATR(H1,14)", atr_k=2.5),
    _a("M1_k30", "M1", "stop >= 3.0 x ATR(H1,14)", atr_k=3.0),
]}

ARM_SETS = {"methods": METHOD_ARMS, "gates": GATE_ARMS, "ext": EXT_ARMS}


def combine_arms(name: str, a: "Arm", b: "Arm") -> "Arm":
    """The composite arm of the pre-registration (section 4): both arms' candidate transforms and SimConfig overrides together."""
    over = dict(a.sim_over)
    over.update(dict(b.sim_over))
    return Arm(name, "C", tuple(sorted(over.items())), atr_k=a.atr_k if a.atr_k is not None else b.atr_k,
               time_stop_min=a.time_stop_min or b.time_stop_min, no_corr=a.no_corr or b.no_corr, cand_file=a.cand_file or b.cand_file,
               desc=f"composite of {a.name} and {b.name}")


def select_arms(aset: str, names: Optional[list] = None, composite: Optional[list] = None) -> dict:
    """The arms to run: all of the set, or the named subset (the control is always kept), plus an optional composite 'C1' of two named arms."""
    arms = ARM_SETS[aset]
    chosen = {n: arms[n] for n in arms if names is None or n in names or arms[n].family == "control"}
    if composite:
        chosen["C1"] = combine_arms("C1", arms[composite[0]], arms[composite[1]])
    return chosen
FAMILY_TREATMENTS = ["M1_k10", "M1_k15", "M1_k20", "M2_c7", "M2_c5", "M2_c3", "M3_h20", "M3_h21", "M5_rr2", "M5_rr4"]


# ── worker ──────────────────────────────────────────────────────────────────────────────────

_STATE: dict = {}


def _load(cand_path, symbols, start, end, sentinel=False):
    key = (str(cand_path), tuple(symbols), start, end, sentinel)
    if _STATE.get("key") != key:
        specs = load_specs()
        cands = pd.read_parquet(cand_path)
        bars = {s: prepare_bars(s, start=start, end=end, sentinel_missing=sentinel) for s in symbols}
        m5 = {s: bars[s]["M5"] for s in symbols}
        assert_dev_window(cands, m5, end)
        cands = add_atr(cands, {s: bars[s]["H1"] for s in symbols})
        _STATE.clear()
        _STATE.update(key=key, specs=specs, cands=cands, m5=m5, extra={})
    return _STATE["specs"], _STATE["cands"], _STATE["m5"]


def _extra_cands(path, symbols, end):
    """Alternative candidate file (e.g. regime off); loaded once per worker, ATR added, DEV window asserted."""
    ex = _STATE["extra"]
    if path not in ex:
        c = pd.read_parquet(path)
        h1 = {s: prepare_bars(s, end=end)["H1"] for s in symbols}
        ex[path] = add_atr(c, h1)
    return ex[path]


def run_arm(arm: Arm, cs: pd.DataFrame, specs: dict, m5: dict, symbols: list, base: SimConfig) -> dict:
    cfg = arm.cfg(base)
    cs2 = apply_atr_floor(cs, arm.atr_k)
    sim = GateSim(symbols, m5, specs, cs2, cfg, time_stop_min=arm.time_stop_min, disable_correlation=arm.no_corr)
    return sim.run()


def _work(task: dict) -> dict:
    sd, symbols = task["seed"], task["symbols"]
    specs, cands, m5 = _load(task["cand_path"], symbols, task["start"], task["end"], task.get("sentinel", False))
    arms = select_arms(task["set"], task.get("arms"), task.get("composite"))
    base = base_config()
    out = {"rows": [], "weekly": [], "comp": []}
    kind = "A" if sd is None else "N"
    for name, arm in arms.items():
        c0 = _extra_cands(arm.cand_file, symbols, task["end"]) if arm.cand_file else cands
        if sd is None:
            cs = c0
        else:
            flip = np.random.default_rng(sd).random(len(c0)) < 0.5            # same recipe as nulltest._run_seeds
            cs = reflect(c0, flip)
        res = run_arm(arm, cs, specs, m5, symbols, base)
        row = {"seed": -1 if sd is None else sd, "variant": name, "kind": kind, **sim_row(res, base.initial_balance)}
        row.update(day_stats(res))
        out["rows"].append(row)
        w = weekly_table(res["trades"])
        w.insert(0, "seed", row["seed"]); w.insert(0, "variant", name); w.insert(0, "kind", kind)
        out["weekly"].append(w)
        cmp_ = composition(res["trades"], specs, 10.0, None)
        cmp_.insert(0, "seed", row["seed"]); cmp_.insert(0, "variant", name); cmp_.insert(0, "kind", kind)
        out["comp"].append(cmp_)
    return out


def run_cmd(a) -> None:
    if not a.end or pd.Timestamp(a.end) > pd.Timestamp(DEV_END):
        sys.exit(f"--end must be given and no later than {DEV_END}: the FINAL window is not to be read")
    outdir = QOUT / a.tag
    if outdir.exists():
        sys.exit(f"{outdir} exists: output tags are never reused")
    if not a.tag.startswith("quant_"):
        sys.exit("tags must start with quant_")
    from config import PAIRS
    symbols = list(PAIRS)
    outdir.mkdir(parents=True)
    tasks = [{"seed": sd, "cand_path": str(a.cands), "symbols": symbols, "start": a.start, "end": a.end, "set": a.set,
              "sentinel": a.sentinel_missing, "arms": a.arms.split(",") if a.arms else None,
              "composite": a.composite.split(",") if a.composite else None}
             for sd in [None] + list(range(a.seeds))]
    parts, t0 = [], time.time()
    with ProcessPoolExecutor(max_workers=a.workers) as ex:
        futs = [ex.submit(_work, t) for t in tasks]
        for i, f in enumerate(as_completed(futs), 1):
            parts.append(f.result())
            print(f"[{i}/{len(tasks)}] {time.time() - t0:.0f}s", flush=True)
    cat = lambda key: pd.concat([x for p in parts for x in p[key]], ignore_index=True)   # noqa: E731
    pd.DataFrame([r for p in parts for r in p["rows"]]).to_csv(outdir / "rows.csv", index=False)
    cat("weekly").to_parquet(outdir / "weekly.parquet", index=False)
    cat("comp").to_parquet(outdir / "comp.parquet", index=False)
    (outdir / "provenance.json").write_text(json.dumps(_clean({
        "argv": sys.argv[1:], "set": a.set, "cands": str(a.cands), "start": a.start, "end": a.end, "seeds": a.seeds,
        "sentinel_missing": a.sentinel_missing,
        "arms": {k: {"family": v.family, "over": dict(v.sim_over), "atr_k": v.atr_k, "time_stop_min": v.time_stop_min,
                     "no_corr": v.no_corr, "desc": v.desc}
                 for k, v in select_arms(a.set, a.arms.split(",") if a.arms else None, a.composite.split(",") if a.composite else None).items()},
        "base": base_config().__dict__}), indent=1))
    print(f"done in {time.time() - t0:.0f}s -> {outdir}")


# ── analysis ────────────────────────────────────────────────────────────────────────────────

def _months(rows: pd.DataFrame) -> float:
    return float(rows[(rows.kind == "A") & (rows.variant == CONTROL)]["calendar_days"].iloc[0] / 30.4)


def _arms_of_run(outdir: Path, aset: str) -> dict:
    """The arms a run actually contained (a subset and/or a composite are recorded in its provenance)."""
    prov = json.loads((outdir / "provenance.json").read_text()) if (outdir / "provenance.json").exists() else {}
    names = list(prov.get("arms", {}).keys()) or None
    arms = dict(ARM_SETS[aset])
    if names and "C1" in names:
        d = prov["arms"]["C1"]["desc"].replace("composite of ", "").split(" and ")
        arms["C1"] = combine_arms("C1", arms[d[0]], arms[d[1]])
    return {n: arms[n] for n in (names or arms)}


def analyse(outdir: Path, aset: str, n_boot: int = 2000, z: Optional[float] = None) -> dict:
    rows = pd.read_csv(outdir / "rows.csv")
    weekly = pd.read_parquet(outdir / "weekly.parquet")
    comp = pd.read_parquet(outdir / "comp.parquet")
    arms = _arms_of_run(outdir, aset)
    weeks = pd.Index(sorted(pd.to_datetime(weekly["week"]).unique()))
    boot = Boot(len(weeks), n_boot)
    sets = build_sets(weekly, weeks)
    sets_ng = build_sets(weekly, weeks, exclude_symbols=("XAUUSD",))
    sets_clean = build_sets(weekly[~((weekly.symbol == "GBPUSD") & (pd.to_datetime(weekly.week).dt.year == 2019))], weeks)
    years = sorted(set((pd.to_datetime(weekly["week"]) + pd.Timedelta(days=3)).dt.year))
    months = _months(rows)
    zq = z if z is not None else 2.61
    res = {"months": months, "z_bonferroni": zq, "arms": {}}

    def col(kind, v, c):
        return rows[(rows.kind == kind) & (rows.variant == v)][c]

    tpm_c = float(col("A", CONTROL, "trades").iloc[0] / months)
    ntr_c = float(col("N", CONTROL, "trades").mean())
    for v in arms:
        d = {"family": arms[v].family, "desc": arms[v].desc,
             "null": {"mean_r": float(col("N", v, "expectancy_r").mean()), "trades": float(col("N", v, "trades").mean()),
                      "win_pct": float(col("N", v, "win_rate_pct").mean()), "hold_min": float(col("N", v, "avg_hold_min").mean()),
                      "max_dd_pct": float(col("N", v, "max_dd_pct").mean()), "return_pct": float(col("N", v, "return_pct").mean()),
                      "worst_r": float(col("N", v, "worst_r").mean()), "worst_low_pct": float(col("N", v, "worst_low_pct").mean())},
             "actual": {c: float(col("A", v, c).iloc[0]) for c in ("trades", "expectancy_r", "win_rate_pct", "return_pct", "max_dd_pct",
                                                                    "worst_r", "worst_low_pct", "days_low_le_3", "avg_hold_min")}}
        d["null"]["trades_per_month"] = d["null"]["trades"] / months
        d["null"]["r_per_month"] = d["null"]["trades_per_month"] * d["null"]["mean_r"]
        d["actual"]["trades_per_month"] = d["actual"]["trades"] / months
        d["actual"]["r_per_month"] = d["actual"]["trades_per_month"] * d["actual"]["expectancy_r"]
        d["null_pooled"] = pooled_stats(comp[(comp.variant == v) & (comp.kind == "N")]).iloc[0].to_dict()
        d["exit_mix_null"] = pooled_stats(comp[(comp.variant == v) & (comp.kind == "N")], "exit_reason").round(4).to_dict("index")
        d["funnel_null_mean"] = {c[2:]: float(col("N", v, c).mean()) for c in rows.columns if c.startswith("f_") and col("N", v, c).notna().any()}
        if v != CONTROL:
            d["D_null"] = contrast(boot, sets, [((v, "N"), 1), ((CONTROL, "N"), -1)])
            d["D_act"] = contrast(boot, sets, [((v, "A"), 1), ((CONTROL, "A"), -1)])
            d["D_null_no_gold"] = contrast(boot, sets_ng, [((v, "N"), 1), ((CONTROL, "N"), -1)])
            d["D_null_clean"] = contrast(boot, sets_clean, [((v, "N"), 1), ((CONTROL, "N"), -1)])
            d["D_act_no_gold"] = contrast(boot, sets_ng, [((v, "A"), 1), ((CONTROL, "A"), -1)])
            sb = Boot(len(weeks), 500)
            by_year = {}
            for y in years:
                st = build_sets(weekly, weeks, only_years=[y])
                if (v, "N") in st and (CONTROL, "N") in st:
                    by_year[int(y)] = contrast(sb, st, [((v, "N"), 1), ((CONTROL, "N"), -1)])["est"]
            d["by_year_D_null"] = by_year
            d["years_same_sign_null"] = int(sum(1 for e in by_year.values() if np.sign(e) == np.sign(d["D_null"]["est"]) and e != 0))
            d["n_years"] = len(by_year)
            d["dR_month_null"] = d["null"]["r_per_month"] - res["arms"][CONTROL]["null"]["r_per_month"] if CONTROL in res["arms"] else None
            d["trades_per_month_ratio_null"] = d["null"]["trades"] / ntr_c
            d["trades_per_month_ratio_act"] = d["actual"]["trades_per_month"] / tpm_c
            e, se = d["D_null"]["est"], d["D_null"]["se"]
            d["cost_effect"] = "REDUCES" if (e >= DELTA and e - zq * se > 0) else ("INCREASES" if (e <= -DELTA and e + zq * se < 0) else "NONE")
            ea, sea = d["D_act"]["est"], d["D_act"]["se"]
            d["real_effect"] = "HELPS" if (ea >= DELTA and ea - zq * sea > 0) else ("HURTS" if (ea <= -DELTA and ea + zq * sea < 0) else
                                                                                     ("NEUTRAL (noise)" if sea > DELTA else "NEUTRAL"))
            d["mde_null"] = 2.8 * se
            d["mde_act"] = 2.8 * sea
        res["arms"][v] = d
    return res


def analyse_cmd(a) -> None:
    outdir = QOUT / a.tag
    res = analyse(outdir, a.set)
    (outdir / "summary.json").write_text(json.dumps(_clean(res), indent=1))
    for v, d in res["arms"].items():
        line = (f"{v:13s} null {d['null']['mean_r']:+.4f} n/run {d['null']['trades']:6.0f} R/mo {d['null']['r_per_month']:+.2f} | "
                f"actual {d['actual']['expectancy_r']:+.4f} n {d['actual']['trades']:5.0f} R/mo {d['actual']['r_per_month']:+.2f}")
        if v != CONTROL:
            line += (f" || D_null {d['D_null']['est']:+.4f}+-{d['D_null']['se']:.4f} D_act {d['D_act']['est']:+.4f}+-{d['D_act']['se']:.4f}"
                     f" yrs {d['years_same_sign_null']}/{d['n_years']} -> {d['cost_effect']}/{d['real_effect']}")
        print(line)
    print("summary ->", outdir / "summary.json")


def build_regime_off_cmd(a) -> None:
    """Candidates for DEV with the regime filter switched off (same other signal settings), for the gate counterfactual."""
    from config import PAIRS
    from research.run import get_candidates
    from research.signals import SignalConfig
    symbols, specs = list(PAIRS), load_specs()
    out = QOUT / "quant_regimeoff"
    out.mkdir(parents=True, exist_ok=True)
    c = get_candidates(symbols, specs, SignalConfig(use_regime=False), START, False, a.workers, end=DEV_END)
    assert pd.to_datetime(c["T"], utc=True).max() < pd.Timestamp(DEV_END, tz="UTC")
    c.to_parquet(out / "base_candidates.parquet", index=False)
    print(len(c), "candidates ->", out)


def geometry_cmd(a) -> None:
    """Descriptive only (no P/L): candidate stop distance in units of ATR(H1,14), by pair."""
    from config import PAIRS
    symbols, specs = list(PAIRS), load_specs()
    cands = pd.read_parquet(a.cands)
    bars = {s: prepare_bars(s, start=START, end=DEV_END) for s in symbols}
    cands = add_atr(cands, {s: bars[s]["H1"] for s in symbols})
    pipm = {s: pip_size(sp) for s, sp in specs.items()}
    cands["stop_pips"] = (cands["price"] - cands["sl_raw"]).abs() / cands["symbol"].map(pipm)
    cands["atr_pips"] = cands["atr_h1"] / cands["symbol"].map(pipm)
    cands["stop_atr"] = cands["stop_pips"] / cands["atr_pips"]
    rows = []
    for s, g in cands.groupby("symbol"):
        r = {"symbol": s, "n": len(g), "atr_pips_med": g.atr_pips.median(), "stop_pips_med": g.stop_pips.median(),
             "stop_atr_p10": g.stop_atr.quantile(.1), "stop_atr_med": g.stop_atr.median(), "stop_atr_p90": g.stop_atr.quantile(.9)}
        for k in (1, 2, 3):
            r[f"pct_below_{k}atr"] = float((g.stop_atr < k).mean() * 100)
        rows.append(r)
    df = pd.DataFrame(rows).round(2)
    print(df.to_string(index=False))
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(a.out, index=False)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    r = sp.add_parser("run")
    r.add_argument("--tag", required=True)
    r.add_argument("--set", required=True, choices=sorted(ARM_SETS))
    r.add_argument("--seeds", type=int, default=40)
    r.add_argument("--workers", type=int, default=6)
    r.add_argument("--cands", default=str(CANDS_DEFAULT))
    r.add_argument("--start", default=START)
    r.add_argument("--end", default=DEV_END)
    r.add_argument("--arms", default=None, help="comma list of arm names to run (the control is always kept)")
    r.add_argument("--composite", default=None, help="two arm names, e.g. M1_k15,M2_c7: adds the composite arm C1")
    r.add_argument("--sentinel-missing", action="store_true", help="treat the 50-point placeholder spread as missing (use with the sens_sentinel candidates)")
    b = sp.add_parser("build-regime-off")
    b.add_argument("--workers", type=int, default=6)
    g = sp.add_parser("geometry")
    g.add_argument("--cands", default=str(CANDS_DEFAULT))
    g.add_argument("--out", default=None)
    an = sp.add_parser("analyse")
    an.add_argument("--tag", required=True)
    an.add_argument("--set", required=True, choices=sorted(ARM_SETS))
    a = ap.parse_args(argv)
    {"run": run_cmd, "geometry": geometry_cmd, "analyse": analyse_cmd, "build-regime-off": build_regime_off_cmd}[a.cmd](a)


if __name__ == "__main__":
    main()
