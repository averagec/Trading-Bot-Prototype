"""Price-only time-series momentum on D1 data, six instruments (stage 1 of GAMEPLAN track R3).

Pre-registration: research/reports/slow_momentum_prereg.md (read it first; every constant below is fixed there).
DEV ONLY. Every window is checked: an end after 2021-06-01 or a start on or after it is refused. FINAL mode (2021-06-01 to
2025-04-30) refuses to run unless research/output/slow_momentum/FINAL_GO exists and carries the sha256 of the frozen
candidate file; only the owner creates that file. No live-trading code is imported or touched.

    python -m research.slow_momentum dev          # the one pre-registered DEV run (output: research/output/slow_momentum_dev)
    python -m research.slow_momentum calibrate    # gate behaviour on synthetic panels (no project returns)
    python -m research.slow_momentum final        # refused unless FINAL_GO exists
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from research.ai import dataset as ds
from research.data import server_to_utc

ROOT = Path(__file__).resolve().parent
OUT_DEV = ROOT / "output" / "slow_momentum_dev"
OUT_FINAL = ROOT / "output" / "slow_momentum_final"
FINAL_DIR = ROOT / "output" / "slow_momentum"
FINAL_GO = FINAL_DIR / "FINAL_GO"
CANDIDATE = OUT_DEV / "frozen_candidate.json"
PREREG = ROOT / "reports" / "slow_momentum_prereg.md"

# ── fixed constants (prereg sections 2 to 6) ────────────────────────────────────────
DEV_START = "2003-01-01"
DEV_END = "2021-06-01"
FINAL_START = "2021-06-01"
FINAL_END = "2025-05-01"            # exclusive: through 2025-04-30
SYMBOLS = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "XAUUSD"]
FX5 = [0, 1, 2, 3, 4]
GOLD = 5
USD_BASE = {"USDJPY", "USDCAD"}
VARIANTS = {"V1": (21, 0), "V2": (63, 0), "V3": (126, 0), "V4": (252, 0), "V5": (126, 21), "V6": (252, 21)}
VNAMES = list(VARIANTS)
VOL_WIN, VOL_MIN, VOL_TARGET, CAP, ANN_DAYS = 60, 40, 0.10, 3.0, 260
STALE_DAYS, EXEC_WAIT_H = 3.0, 72.0
COMM = 0.25e-4                      # 0.25 bp per unit of weight traded
WEEKDAY = 2                         # Wednesday
GOLD_FROM = "2011-01-01"
GOLD_OFF_YEARS = (2011, 2013)       # FAIL-level H1 years (prereg pre-run note 1)
SWAP = {"S0": (0.0, 0.0), "S1": (0.015, 0.03), "S2": (0.035, 0.05)}     # (FX, gold) charge per year on gross notional
BLOCK_NULL = 26
N_PERM, SEED_PERM = 2000, 20261101
N_BOOT, SEED_BOOT, BOOT_BLOCKS = 5000, 20261031, (13, 26, 52)
ALPHA_HOLM = 0.05
ERAS = (("2003-2009", 2003, 2009), ("2010-2013", 2010, 2013), ("2014-2021", 2014, 2021))
G3B_MIN_POSITIVE_YEARS, G5_MIN_SR = 13, 0.30


# ── window guards ───────────────────────────────────────────────────────────────────

def _utc(x) -> pd.Timestamp:
    t = pd.Timestamp(x)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def check_dev_window(start, end) -> tuple[pd.Timestamp, pd.Timestamp]:
    """DEV guard: refuse a missing end, an end after 2021-06-01 and any start on or after 2021-06-01 (FINAL and later)."""
    if end is None:
        raise ValueError("an explicit end <= 2021-06-01 is required (FINAL must not be read)")
    e, s, lim = _utc(end), _utc(start), _utc(DEV_END)
    if e > lim:
        raise ValueError(f"end {e.date()} is after {DEV_END}: FINAL and later data are off limits")
    if s >= lim:
        raise ValueError(f"start {s.date()} is on or after {DEV_END}: that is the FINAL window")
    if s >= e:
        raise ValueError("start must be before end")
    return s, e


def sha256_file(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def require_final_go(go_path: Path | None = None, cand_path: Path | None = None) -> str:
    """FINAL guard: the owner-created FINAL_GO must exist and contain `candidate_sha256=<sha256 of the frozen candidate file>`."""
    go, cand = Path(go_path or FINAL_GO), Path(cand_path or CANDIDATE)
    if not go.exists():
        raise PermissionError(f"FINAL mode is disabled: {go} does not exist (only the owner creates it)")
    if not cand.exists():
        raise PermissionError(f"no frozen candidate at {cand}: there is nothing to run on FINAL")
    want = sha256_file(cand)
    lines = [ln.strip() for ln in go.read_text(encoding="utf-8").splitlines()]
    if f"candidate_sha256={want}" not in lines:
        raise PermissionError("FINAL_GO does not contain the sha256 of the frozen candidate file")
    return want


# ── instrument data ─────────────────────────────────────────────────────────────────

@dataclass
class Inst:
    sym: str
    usd_base: bool
    d1_T: np.ndarray        # datetime64[ns] naive UTC, close time of each complete D1 bar
    d1_close: np.ndarray
    h1_t: np.ndarray        # datetime64[ns] naive UTC, H1 bar OPEN time
    h1_open: np.ndarray
    h1_close: np.ndarray
    h1_hs: np.ndarray       # full spread as a fraction of the open


def _naive(t: pd.Series) -> np.ndarray:
    return t.dt.tz_convert("UTC").dt.tz_localize(None).to_numpy(dtype="datetime64[ns]")


def inst_from_h1(sym: str, h1: pd.DataFrame, point: float) -> Inst:
    from research.ai.slow import build_d1
    d1 = build_d1(h1[["time", "open", "high", "low", "close"]])
    hs = h1["spread_clean_pts"].to_numpy(float) * point / h1["open"].to_numpy(float)
    return Inst(sym, sym in USD_BASE, _naive(d1["T"]), d1["close"].to_numpy(float), _naive(h1["time"]),
                h1["open"].to_numpy(float), h1["close"].to_numpy(float), hs)


def load_dev_insts(end=DEV_END, data_dir=None) -> dict:
    """The six instruments' H1 history through `end` (<= 2021-06-01; ds.load_bars is itself guarded)."""
    kw = {} if data_dir is None else {"data_dir": Path(data_dir)}
    specs = ds.load_specs(**kw)
    out = {}
    for s in SYMBOLS:
        h1 = ds.load_bars(s, "H1", end=end, **kw)
        out[s] = inst_from_h1(s, h1, specs[s]["point"])
    return out


def load_final_insts(end=FINAL_END, go_path=None, cand_path=None, data_dir=None) -> dict:
    """FINAL loader: only after require_final_go; end capped at 2025-05-01."""
    require_final_go(go_path, cand_path)
    if _utc(end) > _utc(FINAL_END):
        raise ValueError("end after 2025-05-01 is not part of FINAL")
    root = Path(data_dir) if data_dir else ds.DATA_DIR
    specs = ds.load_specs(root)
    out = {}
    for s in SYMBOLS:
        df = pd.read_parquet(root / f"{s}_H1.parquet", filters=[("time", "<", _utc(end))])
        df = ds.add_clean_spread(ds.impute_spread(df.sort_values("time").reset_index(drop=True)), s)
        out[s] = inst_from_h1(s, df, specs[s]["point"])
    return out


# ── the panel: everything that does not depend on the sign path ─────────────────────

@dataclass
class Panel:
    R: np.ndarray                       # (K,) rebalance instants, datetime64[ns]
    syms: list
    S: dict                             # variant -> (K,N) float, sign of the trailing return (history only; 0 if unavailable)
    volw: np.ndarray                    # (K,N) min(CAP, target/sigma) where the instrument is tradable at k, else 0
    live: np.ndarray                    # (K,N) bool, instrument in the universe at k
    ret: np.ndarray                     # (K,N) return from execution k to execution k+1 (last row 0)
    hs: np.ndarray                      # (K,N) full spread fraction at the execution bar
    days: np.ndarray                    # (K,) calendar days of the period
    counts: dict = field(default_factory=dict)

    @property
    def K(self) -> int:
        return len(self.R)

    @property
    def Kp(self) -> int:
        return len(self.R) - 1

    @property
    def years(self) -> np.ndarray:
        return self.R.astype("datetime64[Y]").astype(int)[: self.Kp] + 1970


def rebalance_times(start, end, weekday: int = WEEKDAY) -> np.ndarray:
    """R_k: the UTC instant of the broker-server midnight that ends the chosen weekday, for every ISO week in [start, end)."""
    s, e = _utc(start), _utc(end)
    names = ["MON", "TUE", "WED", "THU", "FRI"]
    days = pd.date_range(s.tz_localize(None).normalize(), e.tz_localize(None), freq=f"W-{names[weekday]}")
    srv = pd.Series(days + pd.Timedelta(days=1))
    R = server_to_utc(srv)
    R = R[(R >= s) & (R < e)]
    return R.dt.tz_convert("UTC").dt.tz_localize(None).to_numpy(dtype="datetime64[ns]")


def build_panel(insts: dict, start, end, weekday: int = WEEKDAY, exec_delay_days: float = 0.0,
                gold_from: str = GOLD_FROM, gold_off_years=GOLD_OFF_YEARS) -> Panel:
    R = rebalance_times(start, end, weekday)
    K, N = len(R), len(SYMBOLS)
    day = np.timedelta64(1, "D")
    S = {v: np.zeros((K, N)) for v in VNAMES}
    volw = np.zeros((K, N))
    live = np.zeros((K, N), bool)
    ret = np.zeros((K, N))
    hs = np.zeros((K, N))
    cnt = {"stale_signal": 0, "no_signal_bar": 0, "no_exec": 0, "marked_at_last_close": 0, "fallback_to_earlier_bar": 0}
    Ry = R.astype("datetime64[Y]").astype(int) + 1970
    for j, sym in enumerate(SYMBOLS):
        it = insts[sym]
        if sym == "XAUUSD":
            lv = (R >= np.datetime64(pd.Timestamp(gold_from))) & ~np.isin(Ry, list(gold_off_years))
        else:
            lv = np.ones(K, bool)
        live[:, j] = lv
        T, lc = it.d1_T, np.log(it.d1_close)
        idx = np.searchsorted(T, R, side="right") - 1
        have = idx >= 0
        idc = np.where(have, idx, 0)
        stale = have & ((R - T[idc]) > np.timedelta64(int(STALE_DAYS * 24), "h"))
        r1 = np.concatenate([[np.nan], np.diff(lc)])
        sd = pd.Series(r1).rolling(VOL_WIN, min_periods=VOL_MIN).std(ddof=1).to_numpy() * np.sqrt(ANN_DAYS)
        sigma = sd[idc]
        # execution: first H1 open at or after R_k (+ optional delay), within 72 h
        Rx = R + np.timedelta64(int(exec_delay_days * 24), "h")
        pos = np.searchsorted(it.h1_t, Rx, side="left")
        okx = pos < len(it.h1_t)
        posc = np.where(okx, pos, 0)
        okx &= (it.h1_t[posc] - Rx) <= np.timedelta64(int(EXEC_WAIT_H), "h")
        p_open = np.where(okx, it.h1_open[posc], np.nan)
        hs_j = np.where(okx, it.h1_hs[posc], np.nan)
        hs[:, j] = np.where(np.isfinite(hs_j), hs_j, np.nanmedian(hs_j) if np.isfinite(hs_j).any() else 0.0)
        # exit price for period k: execution price of k+1, else the last available H1 close before R_(k+1)
        p_exit = np.full(K, np.nan)
        p_exit[:-1] = p_open[1:]
        miss = np.zeros(K, bool)
        miss[:-1] = ~np.isfinite(p_open[1:])
        if miss.any():
            lastbar = np.searchsorted(it.h1_t, Rx[1:], side="left") - 1
            lb = np.clip(lastbar, 0, len(it.h1_t) - 1)
            p_exit[:-1] = np.where(miss[:-1], it.h1_close[lb], p_exit[:-1])
        cnt["marked_at_last_close"] += int((miss[:-1] & okx[:-1]).sum())
        with np.errstate(invalid="ignore", divide="ignore"):
            rr = (1.0 - p_open / p_exit) if it.usd_base else (p_exit / p_open - 1.0)
        rr[-1] = 0.0
        ret[:, j] = np.where(np.isfinite(rr), rr, 0.0)
        ok_sigma = np.isfinite(sigma) & (sigma > 0)
        avail = lv & have & ~stale & okx & ok_sigma
        cnt["stale_signal"] += int((lv & have & stale).sum())
        cnt["no_signal_bar"] += int((lv & ~have).sum())
        cnt["no_exec"] += int((lv & ~okx).sum())
        cnt["fallback_to_earlier_bar"] += int((lv & have & (np.timedelta64(0, "h") < (R - T[idc])) & ~stale).sum())
        with np.errstate(invalid="ignore", divide="ignore"):
            volw[:, j] = np.where(avail, np.minimum(CAP, VOL_TARGET / sigma), 0.0)
        for v, (L, skip) in VARIANTS.items():
            ok = have & (idc - L >= 0)
            a = np.where(ok, idc - skip, 0)
            b = np.where(ok, idc - L, 0)
            sret = np.where(ok, lc[a] - lc[b], np.nan)
            S[v][:, j] = np.where(np.isfinite(sret), np.sign(sret), 0.0)
    days = np.zeros(K)
    days[:-1] = (R[1:] - R[:-1]) / day
    return Panel(R, list(SYMBOLS), S, volw, live, ret, hs, days, cnt)


# ── P&L ─────────────────────────────────────────────────────────────────────────────

def swap_vec(level: str) -> np.ndarray:
    fx, g = SWAP[level]
    v = np.full(len(SYMBOLS), fx)
    v[GOLD] = g
    return v


def components(panel: Panel, S: np.ndarray, cols=None, swapc: np.ndarray | None = None, cost_mult: float = 1.0,
               off: np.ndarray | None = None):
    """Per-period, per-instrument gross P&L, trading cost, swap, weights and traded weight (arrays of shape (Kp, ncols))."""
    cols = list(range(len(SYMBOLS))) if cols is None else list(cols)
    swapc = swap_vec("S0") if swapc is None else swapc
    volw = panel.volw[:, cols]
    if off is not None:
        volw = np.where(off[:, cols], 0.0, volw)
    N = np.maximum(panel.live[:, cols].sum(1), 1).astype(float)
    w = S[:, cols] * volw / N[:, None]
    wp = np.vstack([np.zeros((1, w.shape[1])), w[:-1]])
    dw = np.abs(w - wp)
    cost = dw * (0.5 * cost_mult * panel.hs[:, cols] + COMM)
    gross = w * panel.ret[:, cols]
    swap = np.abs(w) * swapc[cols][None, :] * panel.days[:, None] / 365.0
    k = panel.Kp
    return gross[:k], cost[:k], swap[:k], w[:k], dw[:k]


def net_series(panel: Panel, S: np.ndarray, cols=None, swap: str = "S0", cost_mult: float = 1.0, off=None) -> np.ndarray:
    g, c, s, _, _ = components(panel, S, cols, swap_vec(swap), cost_mult, off)
    return (g - c - s).sum(1)


def sharpe(x: np.ndarray) -> float:
    x = np.asarray(x, float)
    if len(x) < 3:
        return float("nan")
    sd = x.std(ddof=1)
    return float(x.mean() / sd * np.sqrt(52)) if sd > 0 else 0.0


def bl_signs(panel: Panel) -> np.ndarray:
    return np.ones((panel.K, len(SYMBOLS)))


# ── nulls ───────────────────────────────────────────────────────────────────────────

def derangement(n: int, rng: np.random.Generator) -> np.ndarray:
    if n < 2:
        return np.arange(n)
    while True:
        p = rng.permutation(n)
        if not (p == np.arange(n)).any():
            return p


def block_perm_index(n: int, block: int, rng: np.random.Generator) -> np.ndarray:
    """Cut 0..n-1 into blocks of `block` (the last may be shorter), permute the blocks by a derangement, concatenate."""
    starts = list(range(0, n, block))
    bounds = [(a, min(a + block, n)) for a in starts]
    p = derangement(len(bounds), rng)
    return np.concatenate([np.arange(*bounds[j]) for j in p])


def apply_index(S: np.ndarray, panel: Panel, idx_fx: np.ndarray, idx_g: np.ndarray, per_col: dict | None = None) -> np.ndarray:
    """Re-order the sign matrix: FX columns by idx_fx over all weeks, gold by idx_g over its live weeks (calendar order)."""
    Sp = S.copy()
    LG = np.where(panel.live[:, GOLD])[0]
    for j in FX5:
        ix = per_col[j] if per_col else idx_fx
        Sp[:, j] = S[ix, j]
    if len(LG):
        Sp[LG, GOLD] = S[LG[idx_g], GOLD]
    return Sp


def null_replicate(panel: Panel, rng: np.random.Generator, mode: str = "perm", m: int | None = None) -> dict:
    """Index maps for one replicate. mode: 'perm' (primary, common derangement), 'indep' (derangement per instrument), 'shift' (circular, lag m)."""
    K, Kg = panel.K, int(panel.live[:, GOLD].sum())
    if mode == "shift":
        return {"fx": (np.arange(K) - m) % K, "g": (np.arange(Kg) - m) % max(Kg, 1), "per": None}
    if mode == "indep":
        return {"fx": None, "g": block_perm_index(Kg, BLOCK_NULL, rng) if Kg else np.arange(0),
                "per": {j: block_perm_index(K, BLOCK_NULL, rng) for j in FX5}}
    return {"fx": block_perm_index(K, BLOCK_NULL, rng), "g": block_perm_index(Kg, BLOCK_NULL, rng) if Kg else np.arange(0), "per": None}


def null_distribution(panel: Panel, n: int, seed: int, mode: str = "perm", lags=None) -> dict:
    """SR (S0, net of spread) of every variant under n sign-path replicates; the same replicate is used for all variants.
    Also the mean traded weight per week (turnover diagnostic)."""
    rng = np.random.default_rng(seed)
    reps = list(lags) if mode == "shift" else range(n)
    sr = np.zeros((len(reps), len(VNAMES)))
    to = np.zeros_like(sr)
    co = np.zeros_like(sr)
    for r, m in enumerate(reps):
        rp = null_replicate(panel, rng, mode, m if mode == "shift" else None)
        for vi, v in enumerate(VNAMES):
            Sp = apply_index(panel.S[v], panel, rp["fx"], rp["g"], rp["per"])
            g, c, s, w, dw = components(panel, Sp)
            sr[r, vi] = sharpe((g - c).sum(1))
            to[r, vi] = dw.sum(1).mean()
            co[r, vi] = c.sum(1).mean() * 52
    return {"sr": sr, "turnover": to, "cost": co}


def p_values(obs: np.ndarray, null_sr: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Individual and max-adjusted one-sided Monte Carlo p-values: (1 + #{null >= obs}) / (1 + M)."""
    M = null_sr.shape[0]
    mx = null_sr.max(1)
    p = np.array([(1 + (null_sr[:, i] >= obs[i]).sum()) / (1 + M) for i in range(len(obs))])
    pm = np.array([(1 + (mx >= obs[i]).sum()) / (1 + M) for i in range(len(obs))])
    return p, pm


def holm(p: np.ndarray, alpha: float = ALPHA_HOLM) -> tuple[np.ndarray, np.ndarray]:
    """Holm step-down. Returns (reject flags, adjusted p-values) in the input order."""
    K = len(p)
    order = np.argsort(p)
    adj = np.zeros(K)
    run = 0.0
    for rank, i in enumerate(order):
        run = max(run, min(1.0, (K - rank) * p[i]))
        adj[i] = run
    return adj <= alpha, adj


# ── bootstrap ───────────────────────────────────────────────────────────────────────

def boot_index(n: int, block: int, B: int, rng: np.random.Generator) -> np.ndarray:
    nb = int(np.ceil(n / block))
    starts = rng.integers(0, n, size=(B, nb))
    idx = (starts[:, :, None] + np.arange(block)[None, None, :]) % n
    return idx.reshape(B, nb * block)[:, :n]


def boot_sharpes(series: dict, blocks=BOOT_BLOCKS, B: int = N_BOOT, seed: int = SEED_BOOT) -> dict:
    """For each named weekly series: point SR and, per block length, the 2.5 / 97.5 percentiles over B circular-block
    resamples. All series share the resampled weeks (cross-sectional dependence kept). Returns name -> dict."""
    n = len(next(iter(series.values())))
    out = {k: {"sr": sharpe(v), "ci": {}} for k, v in series.items()}
    for b in blocks:
        rng = np.random.default_rng(seed + b)
        idx = boot_index(n, b, B, rng)
        for k, v in series.items():
            x = np.asarray(v)[idx]
            sd = x.std(axis=1, ddof=1)
            sr = np.where(sd > 0, x.mean(axis=1) / np.where(sd > 0, sd, 1) * np.sqrt(52), 0.0)
            out[k]["ci"][b] = (float(np.percentile(sr, 2.5)), float(np.percentile(sr, 97.5)))
    for k in out:
        lo_b = min(out[k]["ci"], key=lambda b: out[k]["ci"][b][0])
        out[k]["lo"], out[k]["hi"] = out[k]["ci"][lo_b][0], max(c[1] for c in out[k]["ci"].values())
        out[k]["block_of_lo"] = lo_b
    return out


# ── per-variant statistics and gates ────────────────────────────────────────────────

def era_of(year: int) -> str:
    for name, a, b in ERAS:
        if a <= year <= b:
            return name
    return "other"


def variant_stats(panel: Panel, v: str, S: np.ndarray | None = None) -> dict:
    """Everything the gates and the report need for one variant on a panel (no null)."""
    S = panel.S[v] if S is None else S
    yrs = panel.years
    out = {}
    for lvl in SWAP:
        out[f"net_{lvl}"] = net_series(panel, S, swap=lvl)
    out["net_S0_x2"] = net_series(panel, S, swap="S0", cost_mult=2.0)
    out["exgold_S1"] = net_series(panel, S, cols=FX5, swap="S1")
    out["exgold_S0"] = net_series(panel, S, cols=FX5, swap="S0")
    g, c, s, w, dw = components(panel, S)
    out["gross"] = g.sum(1)
    out["cost"] = c.sum(1)
    out["w"], out["dw"], out["g_i"], out["c_i"], out["s_i"] = w, dw, g, c, s
    return out


def gates(panel: Panel, st: dict, holm_reject: bool, pmax: float) -> dict:
    """Section 6 gates for one variant. `st` is variant_stats output."""
    yrs = panel.years
    n1 = st["net_S1"]
    sr1 = sharpe(n1)
    y_full = [y for y in range(2003, 2021)]
    y_pos = sum(1 for y in y_full if n1[yrs == y].sum() > 0) if len(n1) else 0
    tot_by_year = {y: n1[yrs == y].sum() for y in np.unique(yrs)}
    best3 = sorted(tot_by_year, key=lambda y: tot_by_year[y], reverse=True)[:3]
    keep = ~np.isin(yrs, best3)
    sr_wo3 = sharpe(n1[keep])
    era_pos = sum(1 for name, a, b in ERAS if sharpe(n1[(yrs >= a) & (yrs <= b)]) > 0)
    loyo = {int(y): sharpe(n1[yrs != y]) for y in np.unique(yrs)}
    G = {
        "G1": bool(holm_reject and pmax <= 0.05),
        "G2": bool(sr1 > 0),
        "G3a": bool(sr_wo3 > 0),
        "G3b": bool(y_pos >= G3B_MIN_POSITIVE_YEARS),
        "G3c": bool(era_pos >= 2),
        "G4": bool(sharpe(st["exgold_S1"]) > 0),
        "G5": bool(sr1 >= G5_MIN_SR),
    }
    G["eligible"] = all(G[k] for k in ("G1", "G2", "G3a", "G3b", "G3c", "G4", "G5"))
    G["detail"] = {"sr_S1": sr1, "years_positive_2003_2020": y_pos, "sr_without_best3_years": sr_wo3, "best3_years": [int(y) for y in best3],
                   "eras_positive": era_pos, "sr_exgold_S1": sharpe(st["exgold_S1"]), "loyo_min": min(loyo.values()), "loyo": loyo}
    return G


def select_candidate(eligible: dict) -> str | None:
    """Highest SR(S1) among eligible variants."""
    el = {v: x for v, x in eligible.items() if x["eligible"]}
    return max(el, key=lambda v: el[v]["detail"]["sr_S1"]) if el else None


# ── DEV run ─────────────────────────────────────────────────────────────────────────

def _per_year(panel: Panel, series: dict) -> pd.DataFrame:
    yrs = panel.years
    rows = []
    for y in np.unique(yrs):
        m = yrs == y
        row = {"year": int(y), "weeks": int(m.sum())}
        for k, x in series.items():
            row[f"{k}_ret"] = float(x[m].sum())
            row[f"{k}_sr"] = sharpe(x[m]) if m.sum() >= 10 else float("nan")
        rows.append(row)
    return pd.DataFrame(rows)


def run_dev(out_dir: Path = OUT_DEV, n_perm: int = N_PERM, n_boot: int = N_BOOT, insts: dict | None = None,
            start: str = DEV_START, end: str = DEV_END, quiet: bool = False) -> dict:
    s0, e0 = check_dev_window(start, end)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    insts = insts or load_dev_insts(end)
    panel = build_panel(insts, s0, e0, WEEKDAY)
    prereg_sha = sha256_file(PREREG) if PREREG.exists() else "missing"
    (out_dir / "prereg_sha256.txt").write_text(prereg_sha + "\n")
    stats = {v: variant_stats(panel, v) for v in VNAMES}
    sr0 = np.array([sharpe(stats[v]["net_S0"]) for v in VNAMES])
    nd = null_distribution(panel, n_perm, SEED_PERM, "perm")
    p, pm = p_values(sr0, nd["sr"])
    rej, padj = holm(p)
    G = {v: gates(panel, stats[v], bool(rej[i]), float(pm[i])) for i, v in enumerate(VNAMES)}
    cand = select_candidate(G)
    # benchmark and bootstrap
    S_bl = bl_signs(panel)
    bl = {l: net_series(panel, S_bl, swap=l) for l in ("S0", "S1")}
    series = {"BL_S0": bl["S0"], "BL_S1": bl["S1"]}
    for v in VNAMES:
        series[f"{v}_S0"], series[f"{v}_S1"], series[f"{v}_S2"] = stats[v]["net_S0"], stats[v]["net_S1"], stats[v]["net_S2"]
        series[f"{v}_exgold_S1"] = stats[v]["exgold_S1"]
        series[f"{v}_minus_BL_S0"] = stats[v]["net_S0"] - bl["S0"]
        for j, sym in enumerate(SYMBOLS):
            series[f"{v}_inst_{sym}_S0"] = stats[v]["g_i"][:, j] - stats[v]["c_i"][:, j]
    bs = boot_sharpes(series, B=n_boot)
    # secondary nulls (descriptive)
    Kg = int(panel.live[:, GOLD].sum())
    lags = range(78, max(Kg - 78, 79))
    sh = null_distribution(panel, len(lags), 0, "shift", lags)
    ind = null_distribution(panel, min(n_perm, 500), SEED_PERM + 1, "indep")
    p_sh, pm_sh = p_values(sr0, sh["sr"])
    p_in, pm_in = p_values(sr0, ind["sr"])
    z_sh = (sr0 - sh["sr"].mean(0)) / sh["sr"].std(0, ddof=1)
    # tables
    rows = []
    for i, v in enumerate(VNAMES):
        st, g = stats[v], G[v]
        gross_lev = np.abs(st["w"]).sum(1).mean()
        mean_gc = (st["gross"] - st["cost"]).mean()
        base = (np.abs(st["w"]).sum(1) * panel.days[: panel.Kp] / 365.0).mean()
        row = {"variant": v, "L": VARIANTS[v][0], "skip": VARIANTS[v][1],
               "SR_gross": sharpe(st["gross"]), "SR_S0": sr0[i], "SR_S0_ci_lo": bs[f"{v}_S0"]["lo"], "SR_S0_ci_hi": bs[f"{v}_S0"]["hi"],
               "SR_S1": sharpe(st["net_S1"]), "SR_S1_ci_lo": bs[f"{v}_S1"]["lo"], "SR_S1_ci_hi": bs[f"{v}_S1"]["hi"],
               "SR_S2": sharpe(st["net_S2"]), "SR_S0_costx2": sharpe(st["net_S0_x2"]),
               "SR_exgold_S0": sharpe(st["exgold_S0"]), "SR_exgold_S1": sharpe(st["exgold_S1"]),
               "exgold_S1_ci_lo": bs[f"{v}_exgold_S1"]["lo"], "exgold_S1_ci_hi": bs[f"{v}_exgold_S1"]["hi"],
               "SR_minus_BL_S0": sr0[i] - sharpe(bl["S0"]), "diff_ci_lo": bs[f"{v}_minus_BL_S0"]["lo"], "diff_ci_hi": bs[f"{v}_minus_BL_S0"]["hi"],
               "ann_ret_S0": float(st["net_S0"].mean() * 52), "ann_vol": float(st["net_S0"].std(ddof=1) * np.sqrt(52)),
               "gross_notional": float(gross_lev), "turnover_per_year": float(st["dw"].sum(1).mean() * 52),
               "cost_drag_per_year": float(st["cost"].mean() * 52), "breakeven_swap_pct": float(mean_gc / base * 100) if base > 0 else float("nan"),
               "p_indiv": p[i], "p_holm_adj": padj[i], "p_max": pm[i], "holm_reject": bool(rej[i]),
               "null_mean_SR": float(nd["sr"][:, i].mean()), "null_sd_SR": float(nd["sr"][:, i].std(ddof=1)),
               "null_turnover_ratio": float(nd["turnover"][:, i].mean() / st["dw"].sum(1).mean()),
               "null_cost_drag_per_year": float(nd["cost"][:, i].mean()),
               "p_shift_null": p_sh[i], "z_shift_null": float(z_sh[i]), "p_indep_null": p_in[i],
               **{k: g[k] for k in ("G1", "G2", "G3a", "G3b", "G3c", "G4", "G5", "eligible")},
               "years_positive_2003_2020": g["detail"]["years_positive_2003_2020"], "SR_without_best3": g["detail"]["sr_without_best3_years"],
               "eras_positive": g["detail"]["eras_positive"], "loyo_min": g["detail"]["loyo_min"]}
        rows.append(row)
    tab = pd.DataFrame(rows)
    tab.to_csv(out_dir / "variants.csv", index=False)
    # weekly pnl, per year, per instrument, null
    wk = pd.DataFrame({"R": panel.R[: panel.Kp]})
    for v in VNAMES:
        wk[f"{v}_S0"], wk[f"{v}_S1"] = stats[v]["net_S0"], stats[v]["net_S1"]
    wk["BL_S0"] = bl["S0"]
    wk.to_csv(out_dir / "weekly_pnl.csv", index=False)
    py = _per_year(panel, {**{f"{v}_S1": stats[v]["net_S1"] for v in VNAMES}, "BL_S0": bl["S0"]})
    py.to_csv(out_dir / "per_year.csv", index=False)
    pi_rows = []
    for v in VNAMES:
        for j, sym in enumerate(SYMBOLS):
            x = stats[v]["g_i"][:, j] - stats[v]["c_i"][:, j]
            live = panel.live[: panel.Kp, j]
            pi_rows.append({"variant": v, "symbol": sym, "weeks_live": int(live.sum()), "SR_S0_standalone": sharpe(x[live]) if live.sum() > 20 else float("nan"),
                            "ci_lo": bs[f"{v}_inst_{sym}_S0"]["lo"], "ci_hi": bs[f"{v}_inst_{sym}_S0"]["hi"], "sum_ret_S0": float(x.sum())})
    pd.DataFrame(pi_rows).to_csv(out_dir / "per_instrument.csv", index=False)
    pd.DataFrame(nd["sr"], columns=VNAMES).to_csv(out_dir / "null_distribution.csv", index=False)
    sens = _sensitivity(insts, panel, stats, s0, e0)
    sens.to_csv(out_dir / "sensitivity.csv", index=False)
    summary = {"prereg_sha256": prereg_sha, "code_sha256": sha256_file(Path(__file__)), "window": [str(s0.date()), str(e0.date())],
               "weeks": panel.Kp, "years": round(panel.Kp / 52.0, 2), "n_perm": n_perm, "n_boot": n_boot,
               "bl_SR_S0": sharpe(bl["S0"]), "bl_SR_S1": sharpe(bl["S1"]), "candidate": cand, "panel_counts": panel.counts,
               "max_null_sr_mean": float(nd["sr"].max(1).mean()), "max_null_sr_p95": float(np.percentile(nd["sr"].max(1), 95)),
               "holm_adj_p": dict(zip(VNAMES, [float(x) for x in padj])), "eligible": {v: bool(G[v]["eligible"]) for v in VNAMES},
               "gates": {v: {k: G[v][k] for k in ("G1", "G2", "G3a", "G3b", "G3c", "G4", "G5")} for v in VNAMES},
               "gate_detail": {v: {k: (x if k != "loyo" else {str(a): b for a, b in x.items()}) for k, x in G[v]["detail"].items()} for v in VNAMES}}
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, default=float))
    if cand:
        fc = {"variant": cand, "L": VARIANTS[cand][0], "skip": VARIANTS[cand][1], "weekday": WEEKDAY, "swap_S1": SWAP["S1"],
              "SR_S1_dev": G[cand]["detail"]["sr_S1"], "prereg_sha256": prereg_sha, "code_sha256": summary["code_sha256"]}
        CAND_PATH = out_dir / "frozen_candidate.json"
        CAND_PATH.write_text(json.dumps(fc, indent=2, default=float))
    if not quiet:
        print(format_report(tab, summary, sens))
    return {"table": tab, "summary": summary, "panel": panel, "stats": stats, "gates": G, "null": nd, "sens": sens}


def _sensitivity(insts, panel, stats, s0, e0) -> pd.DataFrame:
    """Descriptive only: SR (S0 and S1) of every variant under alternative weekdays, one-day delay, spread x2, start years,
    year exclusions and the 'without 2020-05 onward' cut. Nothing here changes eligibility."""
    rows = []

    def add(name, fn):
        for v in VNAMES:
            x0, x1 = fn(v, "S0"), fn(v, "S1")
            rows.append({"case": name, "variant": v, "SR_S0": sharpe(x0), "SR_S1": sharpe(x1), "weeks": len(x0)})

    yrs = panel.years
    Rk = panel.R[: panel.Kp]
    add("primary", lambda v, l: stats[v][f"net_{l}"] if l in ("S0", "S1") else None)
    add("spread_x2", lambda v, l: net_series(panel, panel.S[v], swap=l, cost_mult=2.0))
    for name, mask in (("from_2010", yrs >= 2010), ("from_2014", yrs >= 2014), ("without_2016_2017", ~np.isin(yrs, [2016, 2017])),
                       ("without_2020-05_onward", Rk < np.datetime64("2020-05-01"))):
        add(name, lambda v, l, m=mask: net_series(panel, panel.S[v], swap=l)[m])
    off = np.zeros((panel.K, len(SYMBOLS)), bool)
    off[panel.R.astype("datetime64[Y]").astype(int) + 1970 == 2019, 1] = True
    add("without_GBPUSD_2019", lambda v, l: net_series(panel, panel.S[v], swap=l, off=off))
    add("FX5_only", lambda v, l: net_series(panel, panel.S[v], cols=FX5, swap=l))
    for wd, nm in enumerate(["Mon", "Tue", "Wed", "Thu", "Fri"]):
        if wd == WEEKDAY:
            continue
        pw = build_panel(insts, s0, e0, wd)
        add(f"weekday_{nm}", lambda v, l, pw=pw: net_series(pw, pw.S[v], swap=l))
    pd1 = build_panel(insts, s0, e0, WEEKDAY, exec_delay_days=1.0)
    add("exec_delay_1day", lambda v, l: net_series(pd1, pd1.S[v], swap=l))
    return pd.DataFrame(rows)


def format_report(tab: pd.DataFrame, summary: dict, sens: pd.DataFrame) -> str:
    L = [f"Window {summary['window'][0]} to {summary['window'][1]}, {summary['weeks']} weekly periods ({summary['years']} years). prereg sha256 {summary['prereg_sha256'][:12]}",
         f"Benchmark BL (always long, vol-targeted): SR S0 {summary['bl_SR_S0']:+.2f}, S1 {summary['bl_SR_S1']:+.2f}",
         f"Null max-over-variants SR: mean {summary['max_null_sr_mean']:+.2f}, 95th pct {summary['max_null_sr_p95']:+.2f}", ""]
    cols = ["variant", "SR_gross", "SR_S0", "SR_S0_ci_lo", "SR_S0_ci_hi", "SR_S1", "SR_S2", "SR_exgold_S1", "SR_minus_BL_S0", "p_indiv", "p_holm_adj", "p_max",
            "null_mean_SR", "null_sd_SR", "null_turnover_ratio", "turnover_per_year", "gross_notional", "ann_vol", "breakeven_swap_pct", "eligible"]
    L.append(tab[cols].to_string(index=False, float_format=lambda x: f"{x:.3f}"))
    L.append("")
    gcols = ["variant", "G1", "G2", "G3a", "G3b", "G3c", "G4", "G5", "years_positive_2003_2020", "eras_positive", "SR_without_best3", "p_shift_null", "z_shift_null", "p_indep_null"]
    L.append(tab[gcols].to_string(index=False, float_format=lambda x: f"{x:.3f}"))
    L.append(f"\nCandidate: {summary['candidate']}")
    return "\n".join(L)


# ── FINAL (disabled unless FINAL_GO) ────────────────────────────────────────────────

def run_final(out_dir: Path = OUT_FINAL, insts: dict | None = None, go_path=None, cand_path=None, n_boot: int = N_BOOT) -> dict:
    """One run of the frozen candidate on 2021-06-01 .. 2025-04-30. Refuses without FINAL_GO carrying the candidate hash."""
    cand_sha = require_final_go(go_path, cand_path)
    fc = json.loads(Path(cand_path or CANDIDATE).read_text())
    v = fc["variant"]
    insts = insts or load_final_insts(FINAL_END, go_path, cand_path)
    panel = build_panel(insts, FINAL_START, FINAL_END, int(fc["weekday"]))
    S = panel.S[v]
    pooled = {l: net_series(panel, S, swap=l) for l in ("S0", "S1")}
    fx5 = {l: net_series(panel, S, cols=FX5, swap=l) for l in ("S0", "S1")}
    bl = net_series(panel, bl_signs(panel), swap="S0")
    series = {"fx5_S1": fx5["S1"], "pooled_S1": pooled["S1"], "diff_S0": pooled["S0"] - bl}
    bs = boot_sharpes(series, blocks=(13, 26), B=n_boot)
    # one-sided 97.5 % lower bound = 2.5th percentile of the resampled Sharpes, lowest over the two block lengths
    lb = bs["fx5_S1"]["lo"]
    pt = bs["fx5_S1"]["sr"]
    confirmed = lb > 0 and bs["pooled_S1"]["sr"] > 0 and (sharpe(pooled["S0"]) - sharpe(bl)) > 0
    verdict = "CONFIRMED" if confirmed else ("CONSISTENT BUT NOT CONFIRMED" if pt > 0 else "NOT REPLICATED")
    res = {"candidate": v, "candidate_sha256": cand_sha, "weeks": panel.Kp, "fx5_S1": bs["fx5_S1"], "pooled_S1": bs["pooled_S1"],
           "diff_vs_BL_S0": bs["diff_S0"], "SR_pooled_S0": sharpe(pooled["S0"]), "SR_fx5_S0": sharpe(fx5["S0"]), "SR_BL_S0": sharpe(bl), "verdict": verdict}
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    (Path(out_dir) / "final_result.json").write_text(json.dumps(res, indent=2, default=float))
    return res


# ── synthetic calibration (no project returns) ──────────────────────────────────────

def synth_insts(rng: np.random.Generator, strength: float, start="2000-03-30", end=DEV_END, _cache: dict = {}) -> dict:
    """Synthetic instruments on the real broker calendar: weekday D1 bars, exec price = D1 close. Daily return = drift_t + noise
    with a persistent AR(1) drift whose std is `strength` x the daily noise std (strength 0 = no predictability)."""
    key = (start, end)
    if key not in _cache:
        days = pd.bdate_range(start, pd.Timestamp(end) - pd.Timedelta(days=1))
        _cache[key] = (days, server_to_utc(pd.Series(days + pd.Timedelta(days=1))).dt.tz_convert("UTC").dt.tz_localize(None).to_numpy(dtype="datetime64[ns]"))
    days, T = _cache[key]
    n = len(days)
    out = {}
    common = rng.standard_normal(n)
    for sym in SYMBOLS:
        vol = (0.15 if sym == "XAUUSD" else 0.08) / np.sqrt(ANN_DAYS)
        noise = vol * (0.5 * common + np.sqrt(0.75) * rng.standard_normal(n))
        phi = 0.99
        eta = rng.standard_normal(n) * np.sqrt(1 - phi ** 2)
        m = np.zeros(n)
        for t in range(1, n):
            m[t] = phi * m[t - 1] + eta[t]
        r = noise + strength * vol * m
        close = 1.0 * np.exp(np.cumsum(r)) if sym != "XAUUSD" else 500.0 * np.exp(np.cumsum(r))
        out[sym] = Inst(sym, sym in USD_BASE, T, close, T, close, close, np.full(n, 1e-4))
    return out


def synth_panel(rng, strength, weekday=WEEKDAY):
    return build_panel(synth_insts(rng, strength), DEV_START, DEV_END, weekday)


def calibrate(out_dir: Path = OUT_DEV, n_panels: int = 40, n_perm: int = 199, strengths=(0.0, 0.05, 0.1, 0.2, 0.35), seed: int = 7) -> dict:
    rows = []
    for st in strengths:
        rng = np.random.default_rng(seed + int(st * 1000))
        acc = {"sr0_mean": [], "any_G1": [], "any_elig": [], "G3b": [], "G3c": [], "G4": [], "G5": [], "G2": [], "G3a": []}
        for _ in range(n_panels):
            panel = synth_panel(rng, st)
            stats = {v: variant_stats(panel, v) for v in VNAMES}
            sr0 = np.array([sharpe(stats[v]["net_S0"]) for v in VNAMES])
            nd = null_distribution(panel, n_perm, int(rng.integers(1 << 30)), "perm")
            p, pm = p_values(sr0, nd["sr"])
            rej, _ = holm(p)
            G = {v: gates(panel, stats[v], bool(rej[i]), float(pm[i])) for i, v in enumerate(VNAMES)}
            acc["sr0_mean"].append(float(sr0.mean()))
            acc["any_G1"].append(any(G[v]["G1"] for v in VNAMES))
            acc["any_elig"].append(any(G[v]["eligible"] for v in VNAMES))
            for g in ("G2", "G3a", "G3b", "G3c", "G4", "G5"):
                acc[g].append(float(np.mean([G[v][g] for v in VNAMES])))
        rows.append({"strength": st, "n_panels": n_panels, "n_perm": n_perm, **{k: float(np.mean(x)) for k, x in acc.items()}})
    res = {"rows": rows, "note": "synthetic iid-noise plus AR(1) drift panels on the real calendar; no project returns; thresholds are not tuned from this"}
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    (Path(out_dir) / "calibration.json").write_text(json.dumps(res, indent=2))
    return res


# ── CLI ─────────────────────────────────────────────────────────────────────────────

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("dev")
    d.add_argument("--start", default=DEV_START)
    d.add_argument("--end", default=DEV_END)
    d.add_argument("--out", default=str(OUT_DEV))
    d.add_argument("--n-perm", type=int, default=N_PERM)
    d.add_argument("--n-boot", type=int, default=N_BOOT)
    c = sub.add_parser("calibrate")
    c.add_argument("--out", default=str(OUT_DEV))
    c.add_argument("--n-panels", type=int, default=40)
    sub.add_parser("final")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "dev":
            run_dev(Path(a.out), a.n_perm, a.n_boot, start=a.start, end=a.end)
        elif a.cmd == "calibrate":
            print(json.dumps(calibrate(Path(a.out), a.n_panels), indent=2))
        else:
            print(json.dumps(run_final(), indent=2, default=float))
    except (ValueError, PermissionError) as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
