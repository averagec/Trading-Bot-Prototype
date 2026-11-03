"""
ATR-floor confirmation experiment (pre-registration: research/reports/atr_floor_prereg.md, frozen k = 1.0).

Question: does a minimum stop distance of k x ATR(H1,14), applied on top of the structure stop and the 10-pip floor, reduce the cost drag of the
live rules? A cost / structure hypothesis, not a signal claim. Control = live rules, treated = same candidates with `sl_raw` widened by the floor
(`research.quant_methods.apply_atr_floor`); both on the sentinel-missing data; sizing, target, breakeven, gates exactly as the simulator has them.

    python -m research.atr_floor_confirm dev --tag atr_floor_dev --workers 8      # DEV only: simulate + analyse (the tag is never reused)
    python -m research.atr_floor_confirm analyse --tag atr_floor_dev              # re-read a finished run
    python -m research.atr_floor_confirm prereg-hash                              # sha256 of the pre-registration (hash line blanked)
    python -m research.atr_floor_confirm final                                    # DISABLED unless research/output/atr_floor/FINAL_GO exists

DEV = 2014-01-01 to 2021-06-01. The `dev` command refuses an `--end` later than 2021-06-01 and any `--start` on or after it, and the worker asserts that no
bar or candidate at or after the end is loaded. The `final` command (window 2021-06-01 to 2025-05-01) refuses to run unless the owner has created
FINAL_GO containing the sha256 of the final pre-registration; it runs once (the output tag must not exist) and there is no `--force`.
Nothing here touches MT5, sim.py, signals.py or live code.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from research.data import prepare_bars, load_specs
from research.nulltest import reflect, BOOT_SEED, weekly_table
from research.quant_methods import add_atr, apply_atr_floor, h1_atr_series, atr_at
from research.run import OUT, _clean
from research.sim import Simulator, SimConfig
from research.stoploss_experiment import base_config, sim_row, day_stats, assert_dev_window, matched_split

K = 1.0                                              # frozen by the pre-registration (section 3)
DEV_START, DEV_END = "2014-01-01", "2021-06-01"
FINAL_START, FINAL_END = "2021-06-01", "2025-05-01"
PREREG = Path(__file__).resolve().parent / "reports" / "atr_floor_prereg.md"
AF_DIR = OUT / "atr_floor"
FINAL_GO = AF_DIR / "FINAL_GO"
CANDS_DEV = OUT / "sens_sentinel_R2" / "base_candidates.parquet"
QUANT_S1 = OUT / "quant_s1"
N_SEEDS, N_SENS_SEEDS, N_BOOT = 40, 10, 5000
SWAP_FX, SWAP_XAU = 7.0, 20.0                        # stylised adverse swap, $ per lot per night (prereg section 4)
E1_MIN = 0.02                                        # smallest effect of interest (S2)
MIN_TRADE_RATIO = 0.80                               # S7
TRADE_COLS = ["symbol", "direction", "subtype", "placed_T", "open_T", "close_T", "entry", "sl0", "tp", "exit", "volume", "risk_usd",
              "gross_pnl", "commission", "net_pnl", "r", "exit_reason", "hold_min", "spread_pts", "be_moved"]


# ── windows and the FINAL lock ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Window:
    name: str
    start: str
    end: str


def dev_window(start: str = DEV_START, end: Optional[str] = DEV_END) -> Window:
    """Validate a DEV request: the end is required and may not be later than DEV_END; the start may not be inside FINAL."""
    if not end:
        sys.exit(f"--end must be given and no later than {DEV_END}: the FINAL window is not to be read")
    s, e = pd.Timestamp(start), pd.Timestamp(end)
    if e > pd.Timestamp(DEV_END):
        sys.exit(f"--end {end} is later than {DEV_END}: the FINAL window is not to be read")
    if s >= pd.Timestamp(FINAL_START):
        sys.exit(f"--start {start} is inside the FINAL window (from {FINAL_START}): refused")
    if s >= e:
        sys.exit("--start must be before --end")
    return Window("DEV", start, end)


FINAL_WINDOW = Window("FINAL", FINAL_START, FINAL_END)


def prereg_sha256(text: str) -> str:
    """sha256 of the pre-registration text with line endings as LF and the `Prereg-SHA256:` line replaced by `Prereg-SHA256: PENDING`."""
    t = text.replace("\r\n", "\n")
    t = re.sub(r"(?m)^Prereg-SHA256:.*$", "Prereg-SHA256: PENDING", t, count=1)
    return hashlib.sha256(t.encode("utf-8")).hexdigest()


def final_gate(go_path: Path = FINAL_GO, prereg_path: Path = PREREG) -> str:
    """Return the prereg hash if FINAL may run, else exit. FINAL_GO is created by the owner only and must contain the hash of the prereg as it stands now."""
    if not Path(go_path).exists():
        sys.exit(f"FINAL is disabled: {go_path} does not exist. Only the project owner creates it, after the final pre-registration is frozen.")
    sha = prereg_sha256(Path(prereg_path).read_text(encoding="utf-8"))
    if sha not in Path(go_path).read_text(encoding="utf-8"):
        sys.exit("FINAL is disabled: FINAL_GO does not contain the sha256 of the current pre-registration "
                 f"({sha}). The document changed after the owner's approval, or the approval names another version.")
    return sha


# ── arms ─────────────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Arm:
    name: str
    atr_k: Optional[float]
    over: tuple = ()
    max_seed: Optional[int] = None          # run null seeds 0..max_seed-1 only (None = all); the real run is always done

    def cfg(self) -> SimConfig:
        return replace(base_config(), **dict(self.over))


ARMS = {a.name: a for a in [
    Arm("B0", None), Arm("K10", K),
    Arm("B0N", None, (("intrabar", "neutral"),), N_SENS_SEEDS), Arm("K10N", K, (("intrabar", "neutral"),), N_SENS_SEEDS),
    Arm("B0S", None, (("spread_mult", 1.5), ("commission_per_lot_side", 3.125)), N_SENS_SEEDS),
    Arm("K10S", K, (("spread_mult", 1.5), ("commission_per_lot_side", 3.125)), N_SENS_SEEDS),
]}
PAIRS_ARMS = {"primary": ("B0", "K10"), "neutral": ("B0N", "K10N"), "stress": ("B0S", "K10S")}


# ── worker ───────────────────────────────────────────────────────────────────────────────────

_STATE: dict = {}


def atr_rel_at(h1: pd.DataFrame, T_ns: np.ndarray) -> np.ndarray:
    """ATR(H1,14) divided by its own 100-bar rolling median, from H1 bars closed at or before T (a descriptive cut only)."""
    close_ns, atr = h1_atr_series(h1)
    med = pd.Series(atr).rolling(100, min_periods=100).median().to_numpy()
    return atr_at(close_ns, atr / med, T_ns)


def _load(cand_path: str, symbols: tuple, window: Window):
    key = (cand_path, symbols, window)
    if _STATE.get("key") != key:
        specs = load_specs()
        cands = pd.read_parquet(cand_path)
        bars = {s: prepare_bars(s, start=window.start, end=window.end, sentinel_missing=True) for s in symbols}
        m5 = {s: bars[s]["M5"] for s in symbols}
        assert_dev_window(cands, m5, window.end)                      # nothing at or after the window end may be loaded
        if window.name == "FINAL" and len(cands):
            assert pd.to_datetime(cands["T"], utc=True).min() >= pd.Timestamp(window.start, tz="UTC")
        cands = add_atr(cands, {s: bars[s]["H1"] for s in symbols})
        feats = []
        for s, g in cands.groupby("symbol"):
            rel = atr_rel_at(bars[s]["H1"], pd.to_datetime(g["T"], utc=True).astype("int64").to_numpy())
            feats.append(pd.DataFrame({"symbol": s, "T": g["T"].to_numpy(), "atr_h1": g["atr_h1"].to_numpy(), "atr_rel": rel}))
        share = []
        for s in symbols:
            d = bars[s]["M5"]
            m = d["time"].dt.strftime("%Y-%m")
            share.append(pd.DataFrame({"symbol": s, "month": m, "zero": d["spread_was_zero"].astype(float),
                                       "sentinel": d["spread_was_sentinel"].astype(float)}).groupby(["symbol", "month"], as_index=False).mean())
        _STATE.clear()
        _STATE.update(key=key, specs=specs, cands=cands, m5=m5, feats=pd.concat(feats, ignore_index=True), share=pd.concat(share, ignore_index=True))
    return _STATE["specs"], _STATE["cands"], _STATE["m5"]


def run_arm(arm: Arm, cs: pd.DataFrame, specs: dict, m5: dict, symbols: list) -> tuple:
    cs2 = apply_atr_floor(cs, arm.atr_k)
    res = Simulator(symbols, m5, specs, cs2, arm.cfg()).run()
    widened = float((cs2["sl_raw"].to_numpy(float) != cs["sl_raw"].to_numpy(float)).mean()) if len(cs) else 0.0
    return res, widened


def _work(task: dict) -> dict:
    sd, symbols = task["seed"], list(task["symbols"])
    window = Window(*task["window"])
    specs, cands, m5 = _load(task["cand_path"], tuple(symbols), window)
    out = {"trades": [], "rows": [], "feats": None, "share": None}
    if sd is None:
        out["feats"], out["share"] = _STATE["feats"], _STATE["share"]
    for name, arm in ARMS.items():
        if sd is not None and arm.max_seed is not None and sd >= arm.max_seed:
            continue
        cs = cands if sd is None else reflect(cands, np.random.default_rng(sd).random(len(cands)) < 0.5)   # same recipe as nulltest / quant_methods
        res, widened = run_arm(arm, cs, specs, m5, symbols)
        row = {"arm": name, "seed": -1 if sd is None else sd, "widened_share": widened, **sim_row(res, arm.cfg().initial_balance), **day_stats(res)}
        out["rows"].append(row)
        tr = res["trades"]
        if tr is not None and len(tr):
            t = tr[TRADE_COLS].copy()
            t.insert(0, "seed", row["seed"])
            t.insert(0, "arm", name)
            out["trades"].append(t)
    return out


def _git_head() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=str(Path(__file__).resolve().parent)).stdout.strip()
    except Exception:
        return ""


def run_experiment(window: Window, tag: str, cand_path: Path, workers: int, symbols: list) -> Path:
    outdir = OUT / tag
    if outdir.exists():
        sys.exit(f"{outdir} exists: output tags are never reused")
    if not tag.startswith("atr_floor"):
        sys.exit("tags must start with atr_floor")
    outdir.mkdir(parents=True)
    tasks = [{"seed": sd, "cand_path": str(cand_path), "symbols": symbols, "window": (window.name, window.start, window.end)}
             for sd in [None] + list(range(N_SEEDS))]
    parts, t0 = [], time.time()
    with ProcessPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(_work, t) for t in tasks]
        for i, f in enumerate(as_completed(futs), 1):
            parts.append(f.result())
            print(f"[{i}/{len(tasks)}] {time.time() - t0:.0f}s", flush=True)
    trades = pd.concat([x for p in parts for x in p["trades"]], ignore_index=True)
    trades.to_parquet(outdir / "trades.parquet", index=False)
    pd.DataFrame([r for p in parts for r in p["rows"]]).to_csv(outdir / "rows.csv", index=False)
    feats = next(p["feats"] for p in parts if p["feats"] is not None)
    feats.to_parquet(outdir / "cand_features.parquet", index=False)
    next(p["share"] for p in parts if p["share"] is not None).to_csv(outdir / "imputed_share.csv", index=False)
    (outdir / "provenance.json").write_text(json.dumps(_clean({
        "argv": sys.argv[1:], "window": window.__dict__, "k": K, "cands": str(cand_path), "n_seeds": N_SEEDS, "n_sens_seeds": N_SENS_SEEDS,
        "prereg_sha256": prereg_sha256(PREREG.read_text(encoding="utf-8")), "git_head": _git_head(),
        "arms": {n: {"atr_k": a.atr_k, "over": dict(a.over), "max_seed": a.max_seed} for n, a in ARMS.items()},
        "base": base_config().__dict__}), indent=1))
    print(f"simulated in {time.time() - t0:.0f}s -> {outdir}")
    return outdir


# ── swap stress (post-processing) ────────────────────────────────────────────────────────────

def rollover_weights(open_T: pd.Series, close_T: pd.Series) -> np.ndarray:
    """Swap nights between fill and exit. The rollover is 17:00 America/New_York (21:00 UTC in US summer time, 22:00 UTC in winter). A night is charged
    for each rollover crossed on a weekday; the rollover into Thursday counts 3 (Wednesday triple), the ones into Saturday and Sunday count 0."""
    def tday(t):
        loc = pd.to_datetime(t, utc=True).dt.tz_convert("America/New_York") + pd.Timedelta(hours=7)      # trading day starts 17:00 NY
        return loc.dt.tz_localize(None).dt.floor("D")
    a, b = tday(open_T).reset_index(drop=True), tday(close_T).reset_index(drop=True)
    nights = (b - a).dt.days.to_numpy()
    w = np.zeros(len(a))
    for j in range(1, int(nights.max()) + 1 if len(nights) else 1):
        dest = (a + pd.Timedelta(days=j)).dt.dayofweek.to_numpy()                 # weekday of the trading day entered
        wt = np.where(dest == 3, 3.0, np.where(dest >= 5, 0.0, 1.0))
        w += np.where(nights >= j, wt, 0.0)
    return w


def swap_charge_r(trades: pd.DataFrame, scale: float = 1.0) -> np.ndarray:
    """Adverse stylised swap in R: nights x $/lot x lots / risk_usd (scale 1 = $7 FX / $20 XAUUSD per lot per night)."""
    w = rollover_weights(trades["open_T"], trades["close_T"])
    per_lot = np.where(trades["symbol"].to_numpy() == "XAUUSD", SWAP_XAU, SWAP_FX) * scale
    return w * per_lot * trades["volume"].to_numpy(float) / trades["risk_usd"].to_numpy(float)


# ── paired cluster bootstrap ─────────────────────────────────────────────────────────────────

def mult_iid(C: int, B: int, seed: int = BOOT_SEED) -> np.ndarray:
    draws = np.random.default_rng(seed).integers(0, C, size=(B, C))
    m = np.zeros((C, B))
    for b in range(B):
        m[:, b] = np.bincount(draws[b], minlength=C)
    return m


def mult_block(C: int, B: int, L: int = 4, seed: int = BOOT_SEED) -> np.ndarray:
    """Circular moving-block resample of L consecutive clusters, truncated to C clusters per resample."""
    nb = -(-C // L)
    starts = np.random.default_rng(seed).integers(0, C, size=(B, nb))
    idx = ((starts[:, :, None] + np.arange(L)) % C).reshape(B, -1)[:, :C]
    m = np.zeros((C, B))
    for b in range(B):
        m[:, b] = np.bincount(idx[b], minlength=C)
    return m


def to_panel(df: pd.DataFrame, arm: str, seeds: list, key_codes: np.ndarray, n_keys: int, mask: Optional[np.ndarray] = None) -> tuple:
    """(n, s): seeds x clusters arrays of trade count and sum of R for one arm. key_codes is aligned to df rows."""
    sel = (df["arm"].to_numpy() == arm) & np.isin(df["seed"].to_numpy(), seeds)
    if mask is not None:
        sel &= mask
    si = {sd: i for i, sd in enumerate(seeds)}
    n, s = np.zeros((len(seeds), n_keys)), np.zeros((len(seeds), n_keys))
    ri = df.loc[sel, "seed"].map(si).to_numpy()
    np.add.at(n, (ri, key_codes[sel]), 1.0)
    np.add.at(s, (ri, key_codes[sel]), df.loc[sel, "r"].to_numpy(float))
    return n, s


def est_mean(n: np.ndarray, s: np.ndarray, mult: np.ndarray) -> tuple:
    """Mean over seeds of (sum R / trades): point and bootstrap draws."""
    with np.errstate(invalid="ignore", divide="ignore"):
        point = float(np.mean(s.sum(axis=1) / n.sum(axis=1)))
        draws = ((s @ mult) / (n @ mult)).mean(axis=0)
    return point, draws


def est_total(n: np.ndarray, s: np.ndarray, mult: np.ndarray) -> tuple:
    return float(np.mean(s.sum(axis=1))), (s @ mult).mean(axis=0)


def summarise(point: float, draws: np.ndarray) -> dict:
    d = draws[np.isfinite(draws)]
    return {"est": float(point), "se": float(np.std(d, ddof=1)), "lo": float(np.percentile(d, 2.5)), "hi": float(np.percentile(d, 97.5))}


def delta(pc: tuple, pt: tuple, mult: np.ndarray, est=est_mean, scale: float = 1.0) -> dict:
    """Treated minus control with the same resampling for both (paired)."""
    p1, d1 = est(*pt, mult)
    p0, d0 = est(*pc, mult)
    return summarise((p1 - p0) * scale, (d1 - d0) * scale)


class Clusters:
    """Cluster keys of one trade table under the three definitions (day, ISO week, 4-week block of consecutive ISO weeks), keyed on placed_T."""

    def __init__(self, df: pd.DataFrame):
        t = pd.to_datetime(df["placed_T"], utc=True)
        day = t.dt.tz_localize(None).dt.floor("D")
        week = day - pd.to_timedelta(day.dt.dayofweek, unit="D")
        self.day_idx = pd.date_range(day.min(), day.max(), freq="D")
        self.week_idx = pd.date_range(week.min(), week.max(), freq="7D")
        self.day = self.day_idx.get_indexer(day)
        self.week = self.week_idx.get_indexer(week)
        self.week_start = week.to_numpy()

    def spec(self, which: str):
        return {"day": (self.day, len(self.day_idx)), "week": (self.week, len(self.week_idx)), "block": (self.week, len(self.week_idx))}[which]


def mult_for(which: str, n_keys: int, n_boot: int) -> np.ndarray:
    return mult_block(n_keys, n_boot) if which == "block" else mult_iid(n_keys, n_boot)


# ── analysis ─────────────────────────────────────────────────────────────────────────────────

def classify(r: dict) -> dict:
    """Decision table of the pre-registration (section 5). r: e1_lo (binding lower bound), e1_est, e2_est, n1_lo, n1_hi, n1_est, null_est, dd_ok,
    guards (dict of booleans), ratio_pooled, ratio_fx. Returns the conditions and the tier."""
    s = {"S1": r["e1_lo"] > 0, "S2": r["e1_est"] >= E1_MIN, "S3": bool(r["dd_ok"]), "S4": r["e2_est"] > 0, "S5": all(r["guards"].values()),
         "S6": r["null_est"] > 0, "S7": min(r["ratio_pooled"], r["ratio_fx"]) >= MIN_TRADE_RATIO}
    if all(s.values()):
        tier = "A"
    elif r["e1_est"] < E1_MIN or r["n1_hi"] < E1_MIN or not s["S3"]:
        tier = "D"
    elif r["n1_lo"] > 0 and r["n1_est"] >= E1_MIN:
        tier = "B"
    else:
        tier = "C"
    return {"conditions": s, "tier": tier}


def power(se: float, effect: float, z_alpha: float = 1.96) -> float:
    """One-sided power (alpha 0.025) of the lower-bound test for a true effect with standard error se."""
    from math import erf, sqrt
    return 0.5 * (1 + erf((effect / se - z_alpha) / sqrt(2)))


def _prep(outdir: Path) -> tuple:
    tr = pd.read_parquet(outdir / "trades.parquet")
    rows = pd.read_csv(outdir / "rows.csv")
    tr = tr[tr["exit_reason"] != "END"].reset_index(drop=True)
    for c in ("placed_T", "open_T", "close_T"):
        tr[c] = pd.to_datetime(tr[c], utc=True)
    return tr, rows


def acceptance_vs_quant_s1(tr: pd.DataFrame, quant_dir: Path = QUANT_S1) -> dict:
    """Prereg section 8: the real run and null seeds 0..19 must reproduce the stored quant_s1 weekly tables of B0 and M1_k10 (n and sum R, 1e-9)."""
    q = pd.read_parquet(quant_dir / "weekly.parquet")
    out, ok = {}, True
    for mine, theirs in (("B0", "B0"), ("K10", "M1_k10")):
        bad, checked = 0, 0
        for sd in [-1] + list(range(20)):
            a = weekly_table(tr[(tr.arm == mine) & (tr.seed == sd)])
            b = q[(q.variant == theirs) & (q.seed == sd)][["week", "symbol", "n", "sum_r"]]
            m = a.merge(b, on=["week", "symbol"], how="outer", suffixes=("_a", "_b")).fillna(0.0)
            checked += 1
            if not (np.allclose(m["n_a"], m["n_b"], atol=0) and np.allclose(m["sum_r_a"], m["sum_r_b"], atol=1e-9, rtol=0)):
                bad += 1
        out[mine] = {"compared_runs": checked, "mismatched_runs": bad}
        ok &= bad == 0
    out["reproduced"] = bool(ok)
    return out


def analyse(outdir: Path, n_boot: int = N_BOOT, check_quant: bool = True) -> dict:
    tr, rows = _prep(outdir)
    res: dict = {"outdir": str(outdir)}
    if check_quant:
        res["acceptance_vs_quant_s1"] = acceptance_vs_quant_s1(tr)
    cl = Clusters(tr)
    nulls = sorted(s for s in tr.seed.unique() if s >= 0)
    real = [-1]
    gold = (tr["symbol"] == "XAUUSD").to_numpy()
    gbp19 = ((tr["symbol"] == "GBPUSD") & (tr["placed_T"].dt.year == 2019)).to_numpy()
    months = float(rows[(rows.arm == "B0") & (rows.seed == -1)]["calendar_days"].iloc[0] / 30.4)
    res["months"] = months
    mults: dict = {}

    def get_mult(which):
        if which not in mults:
            mults[which] = mult_for(which, cl.spec(which)[1], n_boot)
        return mults[which]

    def pan(arm, seeds, which, mask=None):
        codes, n_keys = cl.spec(which)
        return to_panel(tr, arm, seeds, codes, n_keys, mask)

    for label, (c, t) in PAIRS_ARMS.items():
        d: dict = {}
        sens_nulls = [s for s in nulls if s < N_SENS_SEEDS] if label != "primary" else nulls
        for which in ("day", "week", "block"):
            m = get_mult(which)
            d[f"E1_real_{which}"] = delta(pan(c, real, which), pan(t, real, which), m)
            d[f"E2_real_{which}"] = delta(pan(c, real, which), pan(t, real, which), m, est=est_total, scale=1.0 / months)
            d[f"N1_{which}"] = delta(pan(c, sens_nulls, which), pan(t, sens_nulls, which), m)
        # the binding lower bound is the lowest of the three cluster definitions
        d["E1_real_binding_lo"] = min(d[f"E1_real_{w}"]["lo"] for w in ("day", "week", "block"))
        d["N1_binding_lo"] = min(d[f"N1_{w}"]["lo"] for w in ("day", "week", "block"))
        d["N1_binding_hi"] = max(d[f"N1_{w}"]["hi"] for w in ("day", "week", "block"))
        d["E2_real_est"] = d["E2_real_week"]["est"]
        # subsets (week clusters)
        m = get_mult("week")
        for nm, mask in (("no_gold", ~gold), ("gold_only", gold), ("no_gbp2019", ~gbp19)):
            d[f"E1_real_{nm}"] = delta(pan(c, real, "week", mask), pan(t, real, "week", mask), m)
            d[f"N1_{nm}"] = delta(pan(c, sens_nulls, "week", mask), pan(t, sens_nulls, "week", mask), m)
        # composition-standardised E1: per-pair deltas weighted by the control's real-run trade shares
        syms = sorted(tr.symbol.unique())
        w = np.array([pan(c, real, "week", (tr.symbol == s).to_numpy())[0].sum() for s in syms], float)
        w /= w.sum()
        pt = pd_ = 0.0
        for wi, s in zip(w, syms):
            mk = (tr.symbol == s).to_numpy()
            p1, d1 = est_mean(*pan(t, real, "week", mk), m)
            p0, d0 = est_mean(*pan(c, real, "week", mk), m)
            pt += wi * (p1 - p0)
            pd_ = pd_ + wi * (d1 - d0)
            d.setdefault("by_pair_real", {})[s] = {"w": float(wi), "delta": float(p1 - p0), "n_control": float(pan(c, real, "week", mk)[0].sum()),
                                                  "n_treated": float(pan(t, real, "week", mk)[0].sum())}
        d["E1_real_standardised"] = summarise(pt, pd_)
        # by ISO year (point estimates, descriptive) and positive-year counts
        yr = tr["placed_T"].dt.isocalendar().year.to_numpy()
        by_year = {}
        for y in sorted(set(yr)):
            mk = yr == y
            r1, r0 = pan(t, real, "week", mk), pan(c, real, "week", mk)
            n1, n0 = pan(t, sens_nulls, "week", mk), pan(c, sens_nulls, "week", mk)
            by_year[int(y)] = {"real": float(est_mean(*r1, m)[0] - est_mean(*r0, m)[0]), "null": float(est_mean(*n1, m)[0] - est_mean(*n0, m)[0])}
        d["by_year"] = by_year
        d["years_positive_real"] = int(sum(v["real"] > 0 for v in by_year.values()))
        d["years_positive_null"] = int(sum(v["null"] > 0 for v in by_year.values()))
        d["n_years"] = len(by_year)
        # weekday of the decision (point estimates)
        wd = tr["placed_T"].dt.dayofweek.to_numpy()
        d["by_weekday"] = {int(k): {"real": float(est_mean(*pan(t, real, "week", wd == k), m)[0] - est_mean(*pan(c, real, "week", wd == k), m)[0]),
                                    "null": float(est_mean(*pan(t, sens_nulls, "week", wd == k), m)[0] - est_mean(*pan(c, sens_nulls, "week", wd == k), m)[0])}
                           for k in range(5)}
        # drawdown (simulator equity, compounded) and trade counts
        rr = rows[rows.arm.isin([c, t])]
        def rv(arm, sd, col): return float(rr[(rr.arm == arm) & (rr.seed == sd)][col].iloc[0])
        d["max_dd_real"] = {"control": rv(c, -1, "max_dd_pct"), "treated": rv(t, -1, "max_dd_pct")}
        nn = rr[(rr.seed >= 0) & (rr.seed.isin(sens_nulls))]
        d["max_dd_null_mean"] = {c: float(nn[nn.arm == c]["max_dd_pct"].mean()), t: float(nn[nn.arm == t]["max_dd_pct"].mean())}
        d["dd_ok"] = bool(d["max_dd_real"]["treated"] >= d["max_dd_real"]["control"] and d["max_dd_null_mean"][t] >= d["max_dd_null_mean"][c])
        n_real = {a: float(rv(a, -1, "trades")) for a in (c, t)}
        fx = ~gold
        n_fx = {a: float(pan(a, real, "week", fx)[0].sum()) for a in (c, t)}
        n_null = {a: float(nn[nn.arm == a]["trades"].mean()) for a in (c, t)}
        n_null_fx = {a: float(pan(a, sens_nulls, "week", fx)[0].sum() / len(sens_nulls)) for a in (c, t)}
        d["trades"] = {"real": n_real, "real_fx": n_fx, "null_mean": n_null, "null_fx": n_null_fx,
                       "ratio_real": n_real[t] / n_real[c], "ratio_real_fx": n_fx[t] / n_fx[c], "ratio_null": n_null[t] / n_null[c],
                       "ratio_null_fx": n_null_fx[t] / n_null_fx[c],
                       "n_end": {a: int(rr[(rr.arm == a) & (rr.seed == -1)]["n_end"].iloc[0]) for a in (c, t)}}
        d["mean_r"] = {"real": {a: float(est_mean(*pan(a, real, "week"), m)[0]) for a in (c, t)},
                       "null": {a: float(est_mean(*pan(a, sens_nulls, "week"), m)[0]) for a in (c, t)}}
        d["r_per_month"] = {"real": {a: float(est_total(*pan(a, real, "week"), m)[0] / months) for a in (c, t)},
                            "null": {a: float(est_total(*pan(a, sens_nulls, "week"), m)[0] / months) for a in (c, t)}}
        d["same_exposure_dR_month_real"] = float((d["mean_r"]["real"][t] - d["mean_r"]["real"][c]) * n_real[c] / months)
        res[label] = d

    # swap stress, matched/unmatched decomposition, mechanism (primary pair only)
    c, t = PAIRS_ARMS["primary"]
    m = get_mult("week")
    tr2 = tr.copy()
    for nm, sc in (("swap_adverse", 1.0), ("swap_half", 0.5)):
        tr2["r"] = tr["r"] - swap_charge_r(tr, sc)
        codes, n_keys = cl.spec("week")
        for kind, seeds in (("real", real), ("null", nulls)):
            a, b = to_panel(tr2, c, seeds, codes, n_keys), to_panel(tr2, t, seeds, codes, n_keys)
            res.setdefault(nm, {})[kind] = delta(a, b, m)
    res["swap_nights_per_trade"] = {a: float(rollover_weights(tr[(tr.arm == a) & (tr.seed == -1)]["open_T"], tr[(tr.arm == a) & (tr.seed == -1)]["close_T"]).mean()) for a in (c, t)}
    res["matched"] = _matched(tr, c, t)
    res["mechanism"] = _mechanism(tr, rows, c, t, outdir)
    p = res["primary"]
    g = {"no_gold": p["E1_real_no_gold"]["est"] > 0, "standardised": p["E1_real_standardised"]["est"] > 0, "swap": res["swap_adverse"]["real"]["est"] > 0,
         "stressed_cost": res["stress"]["E1_real_week"]["est"] > 0, "neutral_intrabar": res["neutral"]["E1_real_week"]["est"] > 0}
    res["guards"] = g
    res["tier_illustration"] = classify({"e1_lo": p["E1_real_binding_lo"], "e1_est": p["E1_real_week"]["est"], "e2_est": p["E2_real_est"],
                                         "n1_lo": p["N1_binding_lo"], "n1_hi": p["N1_binding_hi"], "n1_est": p["N1_week"]["est"], "null_est": p["N1_week"]["est"],
                                         "dd_ok": p["dd_ok"], "guards": g, "ratio_pooled": min(p["trades"]["ratio_real"], p["trades"]["ratio_null"]),
                                         "ratio_fx": min(p["trades"]["ratio_real_fx"], p["trades"]["ratio_null_fx"])})
    # power for FINAL from the measured DEV standard errors (binding = largest SE of the three cluster definitions)
    f = float(np.sqrt(months / 47.0))
    se_real = max(p[f"E1_real_{w}"]["se"] for w in ("day", "week", "block")) * f
    se_null = max(p[f"N1_{w}"]["se"] for w in ("day", "week", "block")) * f
    res["power_final"] = {"se_real": se_real, "se_null": se_null, "mde_real": 2.8 * se_real, "mde_null": 2.8 * se_null,
                          "real": {str(e): power(se_real, e) for e in (0.01, 0.0215, 0.035, 0.045, 0.066)},
                          "null": {str(e): power(se_null, e) for e in (0.015, 0.02, 0.03, 0.0346, 0.044)}}
    return res


def _matched(tr: pd.DataFrame, c: str, t: str) -> dict:
    out = {}
    for kind, seeds in (("real", [-1]), ("null", sorted(s for s in tr.seed.unique() if s >= 0))):
        acc: dict = {}
        for sd in seeds:
            summ, _ = matched_split(tr[(tr.arm == t) & (tr.seed == sd)], tr[(tr.arm == c) & (tr.seed == sd)])
            for k, v in summ.items():
                acc[k] = acc.get(k, 0.0) + v / len(seeds)
        acc["mean_r_common_delta"] = (acc["sum_v_common"] - acc["sum_c_common"]) / acc["n_common"] if acc.get("n_common") else float("nan")
        acc["mean_r_only_treated"] = acc["sum_only_v"] / acc["n_only_v"] if acc.get("n_only_v") else float("nan")
        acc["mean_r_only_control"] = acc["sum_only_c"] / acc["n_only_c"] if acc.get("n_only_c") else float("nan")
        out[kind] = acc
    return out


def _mechanism(tr: pd.DataFrame, rows: pd.DataFrame, c: str, t: str, outdir: Path) -> dict:
    out = {}
    feats = pd.read_parquet(outdir / "cand_features.parquet")
    feats["T"] = pd.to_datetime(feats["T"], utc=True)
    for a in (c, t):
        for kind, mk in (("real", tr.seed == -1), ("null", tr.seed >= 0)):
            d = tr[(tr.arm == a) & mk]
            nrun = max(d.seed.nunique(), 1)
            exits = d["exit_reason"].value_counts(normalize=True).round(4).to_dict()
            rr = rows[(rows.arm == a) & ((rows.seed == -1) if kind == "real" else (rows.seed >= 0))]
            out[f"{a}_{kind}"] = {
                "trades_per_run": len(d) / nrun, "mean_r": float(d["r"].mean()), "avg_hold_h": float(d["hold_min"].mean() / 60),
                "commission_r": float((d["commission"] / d["risk_usd"]).mean()), "exit_mix": exits,
                "fillbar_stop_share": float(((d["hold_min"] == 0) & d["exit_reason"].isin(["SL", "BE"])).mean()),
                "worst_r": float(d["r"].min()), "widened_share_of_candidates": float(rr["widened_share"].mean()),
                "funnel": {col[2:]: float(rr[col].mean()) for col in rr.columns if col.startswith("f_") and rr[col].notna().any()}}
    # ATR-relative deciles on the real run (descriptive)
    d = tr[tr.seed == -1].merge(feats.rename(columns={"T": "placed_T"}), on=["symbol", "placed_T"], how="left")
    ok = d["atr_rel"].notna()
    if ok.sum() > 100:
        q = pd.qcut(d.loc[ok, "atr_rel"], 5, labels=False, duplicates="drop")
        dd = d[ok].assign(q=q)
        out["atr_rel_quintile_real"] = {int(k): {a: {"n": int(len(g[g.arm == a])), "mean_r": float(g[g.arm == a]["r"].mean())} for a in (c, t)} for k, g in dd.groupby("q")}
    return out


# ── CLI ──────────────────────────────────────────────────────────────────────────────────────

def _print(res: dict) -> None:
    p = res["primary"]
    if "acceptance_vs_quant_s1" in res:
        print("acceptance vs quant_s1:", res["acceptance_vs_quant_s1"])
    for k in ("E1_real_day", "E1_real_week", "E1_real_block", "E2_real_week", "N1_week", "E1_real_no_gold", "E1_real_standardised"):
        e = p[k]
        print(f"{k:22s} {e['est']:+.4f} (se {e['se']:.4f}) [{e['lo']:+.4f}, {e['hi']:+.4f}]")
    print("binding lower bounds: real E1", round(p["E1_real_binding_lo"], 4), " null N1", round(p["N1_binding_lo"], 4))
    print("trades", p["trades"], "\ndd", p["max_dd_real"], p["max_dd_null_mean"], "years positive real/null", p["years_positive_real"], p["years_positive_null"], "of", p["n_years"])
    print("guards", res["guards"], "| illustrative tier (DEV is not a test):", res["tier_illustration"])
    print("power for FINAL:", json.dumps(_clean(res["power_final"])))


def dev_cmd(a) -> None:
    window = dev_window(a.start, a.end)
    outdir = run_experiment(window, a.tag, Path(a.cands), a.workers, _symbols())
    res = analyse(outdir)
    (outdir / "summary.json").write_text(json.dumps(_clean(res), indent=1))
    _print(res)


def analyse_cmd(a) -> None:
    outdir = OUT / a.tag
    res = analyse(outdir)
    (outdir / "summary.json").write_text(json.dumps(_clean(res), indent=1))
    _print(res)


def _symbols() -> list:
    from config import PAIRS
    return list(PAIRS)


def final_cmd(a) -> None:
    """DISABLED unless the owner has created FINAL_GO (see final_gate). Builds the FINAL candidates (pre-registration section 9) and runs the same code path once."""
    sha = final_gate()
    tag = "atr_floor_final"
    if (OUT / tag).exists():
        sys.exit(f"{OUT / tag} exists: FINAL is used once")
    from research.run import get_candidates
    from research.signals import SignalConfig
    symbols, specs = _symbols(), load_specs()
    cands = get_candidates(symbols, specs, SignalConfig(), FINAL_START, False, a.workers, end=FINAL_END, sentinel_missing=True)
    stage = OUT / (tag + "_candidates")
    stage.mkdir(parents=True, exist_ok=True)
    cand_path = stage / "base_candidates.parquet"
    cands.to_parquet(cand_path, index=False)
    print("FINAL candidates:", len(cands), "sha256", hashlib.sha256(cand_path.read_bytes()).hexdigest(), "prereg", sha)
    outdir = run_experiment(FINAL_WINDOW, tag, cand_path, a.workers, symbols)
    res = analyse(outdir, check_quant=False)
    (outdir / "summary.json").write_text(json.dumps(_clean(res), indent=1))
    _print(res)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    d = sp.add_parser("dev")
    d.add_argument("--tag", required=True)
    d.add_argument("--workers", type=int, default=6)
    d.add_argument("--start", default=DEV_START)
    d.add_argument("--end", default=DEV_END)
    d.add_argument("--cands", default=str(CANDS_DEV))
    an = sp.add_parser("analyse")
    an.add_argument("--tag", required=True)
    sp.add_parser("prereg-hash")
    f = sp.add_parser("final")
    f.add_argument("--workers", type=int, default=6)
    a = ap.parse_args(argv)
    if a.cmd == "prereg-hash":
        print(prereg_sha256(PREREG.read_text(encoding="utf-8")))
        return
    {"dev": dev_cmd, "analyse": analyse_cmd, "final": final_cmd}[a.cmd](a)


if __name__ == "__main__":
    main()
