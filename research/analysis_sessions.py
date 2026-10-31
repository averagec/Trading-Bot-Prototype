"""
Session / pair / weekend analysis of the faithful backtest (DEV window only).

Pre-registration and results: research/reports/session_pair_weekend_analysis.md.
Read-only to the live code, sim.py and signals.py: this module imports them, subclasses `Simulator` only to count its
funnel by hour, and never edits anything.

    python -m research.analysis_sessions sim    --tag analyst_main     # actual + random-direction null per scenario -> parquet
    python -m research.analysis_sessions gates  --tag analyst_main     # signal-stage funnel by hour of day
    python -m research.analysis_sessions tables --tag analyst_main     # every table of the report -> tables.md / tables.json

Hard rules built in:
  * `assert_dev_window` refuses any run that would read bars from 2021-06-01 to 2025-04-30 (FINAL outcomes);
    only 2014-01 .. 2021-05 (DEV) or 2025-05 onward (already seen, descriptive) is allowed.
  * `assert_no_final_rows` refuses a trade frame with a decision time inside the FINAL window.
  * Everything is deterministic: null seeds 0..N-1, bootstrap seed 12345.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterable, Optional

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research.sim import Simulator                                   # noqa: E402

OUT = Path(__file__).resolve().parent / "output"

DEV_START = "2014-01-01"
DEV_END = "2021-06-01"                 # exclusive
SEEN_START = "2025-05-01"              # 2025-05 onward was already seen (descriptive only)
FINAL_START_TS = pd.Timestamp(DEV_END, tz="UTC")
FINAL_END_TS = pd.Timestamp(SEEN_START, tz="UTC")

BOOT_SEED = 12345
N_BOOT = 2000
CAND_PATH_DEFAULT = OUT / "sens_sentinel_R2" / "base_candidates.parquet"

SESSIONS = [("Asia", 0, 7), ("QT_manip", 7, 9), ("London", 9, 12), ("NewYork", 12, 18), ("Late", 18, 22), ("Closed", 22, 24)]
TRADEABLE_SESSIONS = ["London", "NewYork", "Late"]
USD_BASE = {"USDJPY", "USDCAD"}          # BUY = long USD
USD_QUOTE = {"EURUSD", "GBPUSD", "AUDUSD", "XAUUSD"}   # BUY = short USD
DOW_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


# ══════════════════════════════════════════════════════════════════════════════
# Guards
# ══════════════════════════════════════════════════════════════════════════════

def assert_dev_window(start: Optional[str], end: Optional[str]) -> None:
    """DEV (end <= 2021-06-01) or the already-seen window (start >= 2025-05-01). Anything else would read FINAL outcomes."""
    e = pd.Timestamp(end, tz="UTC") if end else None
    s = pd.Timestamp(start, tz="UTC") if start else None
    if e is not None and e <= FINAL_START_TS:
        return
    if s is not None and s >= FINAL_END_TS:
        return
    raise ValueError(f"refusing window start={start} end={end}: it would read FINAL (2021-06-01 to 2025-04-30) outcomes")


def assert_no_final_rows(df: pd.DataFrame, col: str = "placed_T") -> None:
    if not len(df):
        return
    t = pd.to_datetime(df[col], utc=True)
    bad = ((t >= FINAL_START_TS) & (t < FINAL_END_TS)).sum()
    if bad:
        raise ValueError(f"{bad} rows have {col} inside the FINAL window 2021-06-01..2025-04-30; refusing to analyse them")


# ══════════════════════════════════════════════════════════════════════════════
# Features
# ══════════════════════════════════════════════════════════════════════════════

def session_of_hour(h: int) -> str:
    for name, a, b in SESSIONS:
        if a <= h < b:
            return name
    raise ValueError(h)


def pip_size(spec: dict) -> float:
    """Live definition: point x 10 for 3- and 5-digit symbols, else the point (gold: one cent)."""
    return spec["point"] * (10 if spec["digits"] in (3, 5) else 1)


def usd_sign(symbol: str, direction: str) -> int:
    """+1 = long USD, -1 = short USD."""
    buy = direction == "BUY"
    if symbol in USD_BASE:
        return 1 if buy else -1
    if symbol in USD_QUOTE:
        return -1 if buy else 1
    raise ValueError(symbol)


def add_features(trades: pd.DataFrame, specs: dict) -> pd.DataFrame:
    """Decision-time buckets, stop geometry and cost in R. END trades are dropped (as `summarize` does)."""
    t = trades[trades["exit_reason"] != "END"].copy()
    for c in ("placed_T", "open_T", "close_T"):
        t[c] = pd.to_datetime(t[c], utc=True)
    t["placed_hour"] = t["placed_T"].dt.hour
    t["placed_dow"] = t["placed_T"].dt.dayofweek
    t["open_hour"] = t["open_T"].dt.hour
    t["open_dow"] = t["open_T"].dt.dayofweek
    t["month"] = t["placed_T"].dt.month
    t["year"] = t["placed_T"].dt.year
    t["session"] = t["placed_hour"].map(session_of_hour)
    t["day"] = t["placed_T"].dt.tz_localize(None).dt.floor("D")
    point = t["symbol"].map(lambda s: specs[s]["point"]).to_numpy(float)
    pips = t["symbol"].map(lambda s: pip_size(specs[s])).to_numpy(float)
    stop = (t["entry"] - t["sl0"]).abs().to_numpy(float)
    spread_price = t["spread_pts"].to_numpy(float) * point
    t["stop_dist"] = stop
    t["stop_pips"] = stop / pips
    t["spread_pips"] = spread_price / pips
    t["stop_over_spread"] = np.where(spread_price > 0, stop / np.where(spread_price > 0, spread_price, 1), np.nan)
    t["spread_r"] = np.where(stop > 0, spread_price / np.where(stop > 0, stop, 1), np.nan)
    t["comm_r"] = t["commission"] / t["risk_usd"]
    t["cost_r"] = t["spread_r"] + t["comm_r"]
    t["usd_sign"] = [usd_sign(s, d) for s, d in zip(t["symbol"], t["direction"])]
    return t.reset_index(drop=True)


# ══════════════════════════════════════════════════════════════════════════════
# Day-cluster bootstrap
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class BootCtx:
    days: pd.DatetimeIndex
    mult: np.ndarray            # (n_days, n_boot): how often each day is drawn in each resample


def make_ctx(frames: Iterable[pd.DataFrame], n_boot: int = N_BOOT, seed: int = BOOT_SEED) -> BootCtx:
    days = pd.DatetimeIndex(sorted(set().union(*[set(f["day"]) for f in frames if len(f)])))
    D = len(days)
    rng = np.random.default_rng(seed)
    draws = rng.integers(0, D, size=(n_boot, D))
    mult = np.zeros((D, n_boot))
    for b in range(n_boot):
        mult[:, b] = np.bincount(draws[b], minlength=D)
    return BootCtx(days=days, mult=mult)


def _didx(df: pd.DataFrame, ctx: BootCtx) -> np.ndarray:
    idx = ctx.days.get_indexer(df["day"])
    if (idx < 0).any():
        raise ValueError("a trade day is missing from the bootstrap context")
    return idx


def _dense(didx: np.ndarray, sidx: np.ndarray, S: int, D: int, values: np.ndarray, mask: np.ndarray):
    cell = sidx[mask] * D + didx[mask]
    n = np.bincount(cell, minlength=S * D).reshape(S, D).astype(float)
    s = np.bincount(cell, weights=values[mask], minlength=S * D).reshape(S, D)
    return n, s


def _boot_ratio(n: np.ndarray, s: np.ndarray, mult: np.ndarray):
    """Point estimate and bootstrap vector of the mean over rows (seeds) of (sum / count)."""
    with np.errstate(invalid="ignore", divide="ignore"):
        point = float(np.mean(s.sum(axis=1) / n.sum(axis=1)))
        boot = ((s @ mult) / (n @ mult)).mean(axis=0)
    return point, boot


def _pct(boot: np.ndarray, q=(2.5, 97.5)):
    b = boot[np.isfinite(boot)]
    if len(b) < 20:
        return float("nan"), float("nan")
    lo, hi = np.percentile(b, q)
    return float(lo), float(hi)


def mean_boot(df: pd.DataFrame, mask: np.ndarray, value: str, ctx: BootCtx, null: bool = False):
    """Point and bootstrap vector for mean(value) over the rows selected by `mask`. For a null frame (with a 'seed' column)
    the mean is taken per seed first and then averaged over seeds, exactly as in `nulltest.paired_skill`."""
    D = len(ctx.days)
    didx = _didx(df, ctx)
    vals = df[value].to_numpy(float)
    if null:
        seeds = np.sort(df["seed"].unique())
        sidx = pd.Index(seeds).get_indexer(df["seed"])
        S = len(seeds)
    else:
        sidx, S = np.zeros(len(df), int), 1
    ok = np.asarray(mask, bool) & np.isfinite(vals)
    n, s = _dense(didx, sidx, S, D, vals, ok)
    return _boot_ratio(n, s, ctx.mult)


def bucket_row(actual: pd.DataFrame, nulls: Optional[pd.DataFrame], mask_a: np.ndarray, mask_n: Optional[np.ndarray],
               ctx: BootCtx, z_bonf: Optional[float] = None) -> dict:
    """Everything one bucket needs: n, expectancy and CI, null expectancy, skill = actual - null, cost per R."""
    n_a = int(np.sum(mask_a))
    row = {"n": n_a}
    if n_a == 0:
        return row
    r_pt, r_b = mean_boot(actual, mask_a, "r", ctx)
    lo, hi = _pct(r_b)
    row.update({"exp_r": r_pt, "exp_lo": lo, "exp_hi": hi,
                "win_pct": float((actual.loc[mask_a, "net_pnl"] > 0).mean() * 100)})
    c_pt, c_b = mean_boot(actual, mask_a, "cost_r", ctx)
    clo, chi = _pct(c_b)
    row.update({"cost_r": c_pt, "cost_lo": clo, "cost_hi": chi,
                "stop_pips_med": float(actual.loc[mask_a, "stop_pips"].median()),
                "stop_over_spread_med": float(actual.loc[mask_a, "stop_over_spread"].median())})
    if nulls is not None and mask_n is not None and int(np.sum(mask_n)) > 0:
        nm_pt, nm_b = mean_boot(nulls, mask_n, "r", ctx, null=True)
        nlo, nhi = _pct(nm_b)
        k = nulls["seed"].nunique()
        sk_b = r_b - nm_b
        se = float(np.nanstd(sk_b, ddof=1))
        slo, shi = _pct(sk_b)
        row.update({"null_n": float(np.sum(mask_n)) / k, "null_r": nm_pt, "null_lo": nlo, "null_hi": nhi,
                    "skill": r_pt - nm_pt, "skill_se": se, "skill_lo": slo, "skill_hi": shi,
                    "mde80": 2.8 * se})
        if z_bonf is not None:
            row["skill_lb_bonf"] = (r_pt - nm_pt) - z_bonf * se
    return row


def bucket_table(actual: pd.DataFrame, nulls: Optional[pd.DataFrame], col: str, order: list, ctx: BootCtx,
                 z_bonf: Optional[float] = None) -> pd.DataFrame:
    rows = []
    for b in order:
        ma = (actual[col] == b).to_numpy()
        mn = (nulls[col] == b).to_numpy() if nulls is not None else None
        rows.append({"bucket": b, **bucket_row(actual, nulls, ma, mn, ctx, z_bonf)})
    return pd.DataFrame(rows)


def z_one_sided(alpha: float) -> float:
    """Inverse normal CDF for 1 - alpha (Acklam-free: bisection on erfc, no scipy dependency)."""
    import math
    lo, hi = 0.0, 10.0
    for _ in range(80):
        mid = (lo + hi) / 2
        tail = 0.5 * math.erfc(mid / math.sqrt(2))
        if tail > alpha:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


# ══════════════════════════════════════════════════════════════════════════════
# Weekend: closures, gaps, trades that cross them
# ══════════════════════════════════════════════════════════════════════════════

GAP_HOURS = 40         # same threshold the simulator uses for a weekly reopen


def weekend_windows(bar_times: pd.Series, bar_minutes: int = 5) -> pd.DataFrame:
    """One row per market closure longer than GAP_HOURS: `gap_close_T` = close time of the last bar before it,
    `reopen_T` = close time of the first bar after it (simulator cycle-time convention), plus the two bar indices."""
    t = pd.to_datetime(pd.Series(bar_times).reset_index(drop=True), utc=True)
    gaps = t.diff()
    idx = np.flatnonzero((gaps > pd.Timedelta(hours=GAP_HOURS)).to_numpy())
    step = pd.Timedelta(minutes=bar_minutes)
    return pd.DataFrame({"gap_close_T": (t.iloc[idx - 1] + step).reset_index(drop=True),
                         "reopen_T": (t.iloc[idx] + step).reset_index(drop=True),
                         "i_last": idx - 1, "i_first": idx})


def crosses_closure(open_T, close_T, windows: pd.DataFrame) -> np.ndarray:
    """True where the position was open across a closure: opened at or before the gap's last bar close and closed at or after the reopen."""
    gc = pd.to_datetime(windows["gap_close_T"], utc=True).to_numpy("datetime64[ns]")
    ro = pd.to_datetime(windows["reopen_T"], utc=True).to_numpy("datetime64[ns]")
    o = pd.to_datetime(pd.Series(open_T), utc=True).to_numpy("datetime64[ns]")
    c = pd.to_datetime(pd.Series(close_T), utc=True).to_numpy("datetime64[ns]")
    k = np.searchsorted(gc, o, side="left")
    ok = k < len(gc)
    out = np.zeros(len(o), bool)
    out[ok] = ro[k[ok]] <= c[ok]
    return out


def gap_table(m5: pd.DataFrame, spec: dict, horizons=(12, 48, 288, 1440)) -> pd.DataFrame:
    """Per weekly closure of one symbol: gap in pips (first open minus last close), first-bar spread, and whether price
    re-crossed the last close within `horizons` M5 bars (a gap 'fill'). Market data only; no P/L."""
    w = weekend_windows(m5["time"])
    o, h, lo, c = (m5[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    sp = m5["spread"].to_numpy(float) if "spread" in m5 else m5["spread_pts"].to_numpy(float)
    pip = pip_size(spec)
    rows = []
    for a, b in zip(w["i_last"], w["i_first"]):
        gap = (o[b] - c[a]) / pip
        row = {"close_T": m5["time"].iloc[a], "reopen_bar": m5["time"].iloc[b], "gap_pips": gap,
               "gap_hours": (m5["time"].iloc[b] - m5["time"].iloc[a]).total_seconds() / 3600,
               "first_spread_pts": sp[b], "last_close": c[a], "first_open": o[b]}
        for hz in horizons:
            seg = slice(b, min(b + hz, len(o)))
            if gap > 0:
                filled = bool(np.nanmin(lo[seg]) <= c[a])
            elif gap < 0:
                filled = bool(np.nanmax(h[seg]) >= c[a])
            else:
                filled = True
            row[f"fill_{hz}"] = filled
        rows.append(row)
    return pd.DataFrame(rows)


def clean_spread_mask(df: pd.DataFrame) -> np.ndarray:
    """Bars whose spread is a real recording: positive, not the 50-point placeholder, not a third-party synthetic value."""
    ok = (df["spread"].to_numpy(float) > 0)
    for c in ("spread_was_sentinel", "spread_synthetic"):
        if c in df:
            ok &= ~df[c].to_numpy(bool)
    return ok


def hourly_spread(m5: pd.DataFrame, spec: dict) -> pd.DataFrame:
    """Median and 90th-percentile recorded spread in pips by UTC hour of the bar open (clean bars only, Monday to Friday)."""
    ok = clean_spread_mask(m5) & (m5["time"].dt.dayofweek < 5).to_numpy()
    d = m5.loc[ok, ["time", "spread"]].copy()
    d["pips"] = d["spread"].astype(float) * spec["point"] / pip_size(spec)
    g = d.groupby(d["time"].dt.hour)["pips"]
    return pd.DataFrame({"median_pips": g.median(), "p90_pips": g.quantile(0.9), "n": g.size()})


def weekly_open_spread(m5: pd.DataFrame, spec: dict, n_bars: int = 12) -> pd.DataFrame:
    """Spread (pips) of the first `n_bars` M5 bars after each weekly reopen, by bar position, vs the pair's overall median clean spread."""
    w = weekend_windows(m5["time"])
    ok = clean_spread_mask(m5)
    pips = m5["spread"].to_numpy(float) * spec["point"] / pip_size(spec)
    med = float(np.median(pips[ok])) if ok.any() else float("nan")
    rows = []
    for k in range(n_bars):
        vals = [pips[b + k] for b in w["i_first"] if b + k < len(pips) and ok[b + k]]
        rows.append({"bar_after_reopen": k, "median_pips": float(np.median(vals)) if vals else float("nan"),
                     "ratio_to_overall_median": (float(np.median(vals)) / med) if vals else float("nan"), "n": len(vals)})
    return pd.DataFrame(rows)


# ══════════════════════════════════════════════════════════════════════════════
# Effective number of independent bets
# ══════════════════════════════════════════════════════════════════════════════

def eigen_neff(corr: np.ndarray) -> float:
    """(sum of eigenvalues)^2 / sum of squared eigenvalues of a correlation matrix: k for identity, 1 for all-ones."""
    ev = np.clip(np.linalg.eigvalsh(np.asarray(corr, float)), 0, None)
    return float(ev.sum() ** 2 / np.sum(ev ** 2))


def daily_r_matrix(trades: pd.DataFrame, symbols: list, all_days: Optional[pd.DatetimeIndex] = None) -> pd.DataFrame:
    p = trades.pivot_table(index="day", columns="symbol", values="r", aggfunc="sum").reindex(columns=symbols)
    if all_days is None:
        all_days = pd.bdate_range(p.index.min(), p.index.max())
    return p.reindex(all_days).fillna(0.0)


def icc_oneway(day: np.ndarray, r: np.ndarray) -> dict:
    """ANOVA estimator of the intra-day correlation of R, design effect and effective N of trades."""
    df = pd.DataFrame({"d": day, "r": r})
    g = df.groupby("d")["r"]
    n_j, m_j = g.size().to_numpy(float), g.mean().to_numpy(float)
    N, k = n_j.sum(), len(n_j)
    if k < 2 or N <= k:
        return {"icc": float("nan"), "deff": float("nan"), "n_eff": float("nan"), "n": int(N), "days": int(k)}
    grand = r.mean()
    ssb = float(np.sum(n_j * (m_j - grand) ** 2))
    ssw = float(np.sum((r - g.transform("mean").to_numpy()) ** 2))
    msb, msw = ssb / (k - 1), ssw / (N - k)
    n0 = (N - np.sum(n_j ** 2) / N) / (k - 1)
    icc = (msb - msw) / (msb + (n0 - 1) * msw)
    nbar = np.sum(n_j ** 2) / N                     # size-weighted mean cluster size
    deff = 1 + (nbar - 1) * icc
    return {"icc": float(icc), "deff": float(deff), "n_eff": float(N / deff), "n": int(N), "days": int(k), "mean_cluster": float(nbar)}


def day_agreement(df: pd.DataFrame, D: int, didx: np.ndarray):
    """Per day: number of same-day trade pairs on different symbols, and how many of those pairs share the USD side."""
    agree, total = np.zeros(D), np.zeros(D)
    for d, g in df.assign(_d=didx).groupby("_d"):
        sy, sg = g["symbol"].to_numpy(), g["usd_sign"].to_numpy()
        m = len(g)
        for i in range(m):
            for j in range(i + 1, m):
                if sy[i] != sy[j]:
                    total[d] += 1
                    agree[d] += sg[i] == sg[j]
    return agree, total


# ══════════════════════════════════════════════════════════════════════════════
# Simulation with a funnel counted by hour (subclass: sim.py is not edited)
# ══════════════════════════════════════════════════════════════════════════════

class _HourCounter(Counter):
    """Counter that also books every increment under the hour currently being simulated."""
    def __init__(self, *a, **k):
        self.cur_hour = 0
        self.by_hour: Counter = Counter()
        super().__init__(*a, **k)

    def __setitem__(self, key, value):
        old = dict.get(self, key, 0)
        super().__setitem__(key, value)
        self.by_hour[(self.cur_hour, key)] += value - old


class FunnelSim(Simulator):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.funnel = _HourCounter(self.funnel)

    def _process_step(self, n, T):
        self.funnel.cur_hour = T.hour
        return super()._process_step(n, T)

    def _cycle(self, n, T):
        self.funnel.cur_hour = T.hour
        return super()._cycle(n, T)

    def run(self) -> dict:
        res = super().run()
        res["funnel_by_hour"] = {f"{h}|{k}": v for (h, k), v in self.funnel.by_hour.items() if v}
        return res


def _all_pairs() -> list:
    from config import PAIRS
    return list(PAIRS)


def scenario_defs(seeds_main: int = 40, seeds_subset: int = 20) -> dict:
    allp = _all_pairs()
    d = {"L": dict(symbols=allp, flatten=20.0, seeds=seeds_main),
         "N": dict(symbols=allp, flatten=None, seeds=seeds_main)}
    for p in allp:
        d[f"S_no{p}"] = dict(symbols=[s for s in allp if s != p], flatten=20.0, seeds=seeds_subset)
    d["S_top3"] = dict(symbols=["EURUSD", "GBPUSD", "USDJPY"], flatten=20.0, seeds=seeds_subset)
    return d


_WORKER = {}


def _worker_state(start, end, cand_path, sentinel_missing=True):
    key = (start, end, str(cand_path), sentinel_missing)
    if _WORKER.get("key") != key:
        from research.data import prepare_bars, load_specs
        syms = _all_pairs()
        _WORKER["m5"] = {s: prepare_bars(s, start=start, end=end, sentinel_missing=sentinel_missing, check_quality=False)["M5"]
                         for s in syms}
        _WORKER["specs"] = load_specs()
        _WORKER["cands"] = pd.read_parquet(cand_path)
        _WORKER["key"] = key
    return _WORKER


def run_one(task: dict) -> dict:
    """One simulation: scenario (symbols, flatten) x seed (None = the real signals, int = random-direction null)."""
    from research.metrics import summarize
    from research.nulltest import build_sim_config, reflect
    assert_dev_window(task["start"], task["end"])
    st = _worker_state(task["start"], task["end"], task["cand_path"])
    cands = st["cands"]
    syms = task["symbols"]
    cands = cands[cands["symbol"].isin(syms)].reset_index(drop=True)
    cfg = replace(build_sim_config(True, True), friday_flatten_utc=task["flatten"])
    sd = task["seed"]
    if sd is not None:
        flip = np.random.default_rng(sd).random(len(cands)) < 0.5
        cands = reflect(cands, flip)
    m5 = {s: st["m5"][s] for s in syms}
    res = FunnelSim(syms, m5, st["specs"], cands, cfg).run()
    tr = res["trades"].copy()
    s = summarize(res["trades"], res["equity"], cfg.initial_balance)
    eq = res["equity"].set_index("T")["equity"].resample("1D").last().dropna()
    dr = eq.pct_change().dropna()
    metrics = {"scenario": task["scenario"], "seed": -1 if sd is None else sd,
               "trades": s.get("trades"), "expectancy_r": s.get("expectancy_r"), "return_pct": s.get("return_pct"),
               "max_dd_equity_pct": s.get("max_dd_equity_pct"), "win_rate_pct": s.get("win_rate_pct"),
               "daily_ret_sd_pct": float(dr.std() * 100), "worst_day_pct": float(dr.min() * 100),
               "calendar_days": s.get("calendar_days")}
    tr["scenario"], tr["seed"] = task["scenario"], metrics["seed"]
    return {"trades": tr, "metrics": metrics, "funnel": res["funnel"], "funnel_by_hour": res["funnel_by_hour"],
            "scenario": task["scenario"], "seed": metrics["seed"]}


def run_scenarios(tag: str, start: str = DEV_START, end: str = DEV_END, cand_path: Path = CAND_PATH_DEFAULT,
                  workers: int = 10, seeds_main: int = 40, seeds_subset: int = 20, only: Optional[list] = None) -> None:
    assert_dev_window(start, end)
    outdir = OUT / tag
    outdir.mkdir(parents=True, exist_ok=True)
    defs = scenario_defs(seeds_main, seeds_subset)
    if only:
        defs = {k: v for k, v in defs.items() if k in only}
    tasks = []
    for name, d in defs.items():
        for sd in [None] + list(range(d["seeds"])):
            tasks.append({"scenario": name, "symbols": d["symbols"], "flatten": d["flatten"], "seed": sd,
                          "start": start, "end": end, "cand_path": str(cand_path)})
    # biggest scenarios first so the pool stays busy
    tasks.sort(key=lambda t: -len(t["symbols"]))
    with ProcessPoolExecutor(max_workers=workers) as ex:
        results = list(ex.map(run_one, tasks, chunksize=1))
    by_scn: dict = {}
    for r in results:
        by_scn.setdefault(r["scenario"], []).append(r)
    for name, rs in by_scn.items():
        pd.concat([r["trades"] for r in rs], ignore_index=True).to_parquet(outdir / f"trades_{name}.parquet", index=False)
        pd.DataFrame([r["metrics"] for r in rs]).to_csv(outdir / f"runs_{name}.csv", index=False)
        actual = [r for r in rs if r["seed"] == -1][0]
        (outdir / f"funnel_{name}.json").write_text(json.dumps({"funnel": actual["funnel"], "by_hour": actual["funnel_by_hour"]}))
    print("scenarios written:", sorted(by_scn))


# ══════════════════════════════════════════════════════════════════════════════
# Signal-stage funnel by hour (diag mode of signals.build_candidates, evaluated over every cycle)
# ══════════════════════════════════════════════════════════════════════════════

def _gate_task(args):
    symbol, start, end = args
    from research.data import prepare_bars, load_specs
    from research.signals import SignalConfig, build_candidates
    assert_dev_window(start, end)
    bars = prepare_bars(symbol, start=start, end=end, sentinel_missing=True, check_quality=False)
    point = load_specs()[symbol]["point"]
    m5 = bars["M5"]
    T = (m5["time"].values.astype("datetime64[ns]") + np.timedelta64(5, "m"))
    rows = build_candidates(symbol, bars, SignalConfig(), point, diag_times=T)
    d = pd.DataFrame(rows)
    cyc = pd.DataFrame({"T": pd.to_datetime(T, utc=True)})
    cyc = cyc.iloc[499:]                                            # first 499 bars: indicators not warm (signals.py: i5 >= W - 1)
    cyc["hour"] = cyc["T"].dt.hour
    total = cyc.groupby("hour").size()
    out = d.assign(hour=d["T"].dt.hour).groupby(["hour", "status"]).size().unstack(fill_value=0) if len(d) else pd.DataFrame()
    out["total_cycles"] = total
    out["silent_drop"] = out["total_cycles"] - out.drop(columns="total_cycles").sum(axis=1)
    out.insert(0, "symbol", symbol)
    return out.reset_index()


def run_gates(tag: str, start: str = DEV_START, end: str = DEV_END, workers: int = 6) -> None:
    assert_dev_window(start, end)
    outdir = OUT / tag
    outdir.mkdir(parents=True, exist_ok=True)
    with ProcessPoolExecutor(max_workers=workers) as ex:
        parts = list(ex.map(_gate_task, [(s, start, end) for s in _all_pairs()]))
    pd.concat(parts, ignore_index=True).to_csv(outdir / "gates_by_hour.csv", index=False)
    print("gate diag written")


# ══════════════════════════════════════════════════════════════════════════════
# CLI (tables live in analysis_tables.py to keep this file readable)
# ══════════════════════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["sim", "gates", "tables"])
    ap.add_argument("--tag", default="analyst_main")
    ap.add_argument("--workers", type=int, default=10)
    ap.add_argument("--seeds", type=int, default=40)
    ap.add_argument("--subset-seeds", type=int, default=20)
    ap.add_argument("--only", nargs="*", default=None)
    args = ap.parse_args()
    if not args.tag.startswith("analyst_"):
        raise SystemExit("tag must start with analyst_")
    if args.cmd == "sim":
        run_scenarios(args.tag, workers=args.workers, seeds_main=args.seeds, seeds_subset=args.subset_seeds, only=args.only)
    elif args.cmd == "gates":
        run_gates(args.tag, workers=min(args.workers, 6))
    else:
        from research.analysis_tables import build_tables
        build_tables(args.tag)


if __name__ == "__main__":
    main()
