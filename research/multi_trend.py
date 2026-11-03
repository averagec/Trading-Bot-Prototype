"""Multi-asset time-series trend following on broker D1 data (vol-targeted, class-balanced, weekly rebalance).

Pre-registration: research/reports/multi_trend_prereg.md (read it first; every constant below is fixed there).
Data screen: research/reports/multi_trend_data_quality.md, research/multi_trend_data.py.
DEV ONLY. Every window is checked: an end after 2021-06-01 or a start on or after it is refused, and every file read is filtered at file
level (multi_trend_data.load_d1_raw). FINAL mode (2021-06-01 to 2025-04-30) refuses to run unless research/output/multi_trend/FINAL_GO
exists and carries the sha256 of the frozen candidate file; only the owner creates that file. No live-trading code and no MetaTrader5 import.

    python -m research.multi_trend calibrate     # synthetic planted-trend gate calibration and power (no project returns)
    python -m research.multi_trend dev           # the one pre-registered DEV run (NOT run by the author of the prereg)
    python -m research.multi_trend final         # refused unless FINAL_GO exists
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

from research import multi_trend_data as mtd

ROOT = Path(__file__).resolve().parent
OUT_DEV = ROOT / "output" / "multi_trend_dev"
OUT_FINAL = ROOT / "output" / "multi_trend_final"
FINAL_DIR = ROOT / "output" / "multi_trend"
FINAL_GO = FINAL_DIR / "FINAL_GO"
CANDIDATE = OUT_DEV / "frozen_candidate.json"
PREREG = ROOT / "reports" / "multi_trend_prereg.md"

# ── fixed constants (prereg sections 3 to 7) ─────────────────────────────────────────────────────
DEV_START = "2003-01-01"
DEV_END = "2021-06-01"
FINAL_START = "2021-06-01"
FINAL_END = "2025-05-01"            # exclusive: through 2025-04-30
CLASSES = mtd.CLASSES               # FX EQ EN MT AG
VARIANTS = {"M1": (30,), "M3": (91,), "M6": (182,), "M12": (365,), "B4": (30, 91, 182, 365)}   # calendar-day look-backs
VNAMES = list(VARIANTS)
VOL_WIN, VOL_MIN, VOL_TARGET, CAP, ANN_DAYS = 60, 40, 0.10, 3.0, 260
BASKET_WIN, BASKET_MIN, GCAP = 120, 80, 4.0
STALE_DAYS, EXEC_WAIT_D, LOOKBACK_TOL_D = 3, 4, 7
COMM = 0.25e-4                      # 0.25 bp per unit of weight traded
WEEKDAY = 2                         # Wednesday decision, execution at the next D1 open
SWAP = {"S0": {c: 0.0 for c in CLASSES},
        "S1": {"FX": 0.015, "EQ": 0.020, "EN": 0.025, "MT": 0.030, "AG": 0.030},
        "S2": {"FX": 0.035, "EQ": 0.040, "EN": 0.050, "MT": 0.050, "AG": 0.050}}      # charge per year on gross notional
BLOCK_NULL = 26
N_PERM, SEED_PERM = 2000, 20261104
N_BOOT, SEED_BOOT, BOOT_BLOCKS = 5000, 20261103, (13, 26, 52)
ALPHA = 0.05
ERAS = (("2003-2009", 2003, 2009), ("2010-2013", 2010, 2013), ("2014-2021", 2014, 2021))
G3B_MIN_POSITIVE_YEARS, G5_MIN_SR, G3C_MIN_ERAS = 12, 0.30, 2
CONV_PAIRS = {"EUR": ("EURUSD", False), "GBP": ("GBPUSD", False), "AUD": ("AUDUSD", False), "NZD": ("NZDUSD", False),
              "JPY": ("USDJPY", True), "CAD": ("USDCAD", True), "CHF": ("USDCHF", True)}   # (pair, inverted)


# ── window guards ──────────────────────────────────────────────────────────────────────────────

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


# ── instruments ────────────────────────────────────────────────────────────────────────────────

@dataclass
class Inst:
    sym: str
    cls: str
    quote: str                  # quote (profit) currency
    date: np.ndarray            # datetime64[D], D1 bar label (broker server day), one bar per weekday, ascending
    open: np.ndarray
    close: np.ndarray
    hs: np.ndarray              # full spread as a fraction of price, causal (value at bar t uses bars <= t)
    entry: np.datetime64        # first rebalance date at which the instrument may trade
    masked: tuple = ()          # calendar years in which it is flat (data-quality masks)


def inst_from_df(sym: str, cls: str, quote: str, df: pd.DataFrame, point: float, entry, masked=()) -> Inst:
    """`df` is the output of multi_trend_data.clean_d1."""
    hs = mtd.spread_fraction(df, cls, point)
    return Inst(sym, cls, quote, df["date"].to_numpy("datetime64[D]"), df["open"].to_numpy(float), df["close"].to_numpy(float), hs,
                np.datetime64(pd.Timestamp(entry).date(), "D"), tuple(int(y) for y in masked))


def load_dev_insts(end=DEV_END, universe: pd.DataFrame | None = None, data_dir: Path | None = None) -> dict:
    """The universe's D1 history through `end` (<= 2021-06-01; the loader is file-level filtered and itself guarded)."""
    kw = {} if data_dir is None else {"data_dir": Path(data_dir)}
    uni = mtd.load_universe() if universe is None else universe
    out = {}
    for _, u in uni.iterrows():
        spec = mtd.load_spec(u["symbol"], **kw)
        df, _ = mtd.clean_d1(mtd.load_d1_raw(u["symbol"], end, **kw))
        out[u["symbol"]] = inst_from_df(u["symbol"], u["class"], u["quote_ccy"], df, float(spec["point"]), u["entry"], u["masked_years"])
    return out


def load_conv_insts(end=DEV_END, data_dir: Path | None = None) -> dict:
    """Conversion pairs (cleaned D1) for the exact USD translation; a pair that is not in the cache is simply absent."""
    kw = {} if data_dir is None else {"data_dir": Path(data_dir)}
    out = {}
    for pair, _ in CONV_PAIRS.values():
        try:
            df, _ = mtd.clean_d1(mtd.load_d1_raw(pair, end, **kw))
        except FileNotFoundError:
            continue
        out[pair] = Inst(pair, "FX", pair[3:], df["date"].to_numpy("datetime64[D]"), df["open"].to_numpy(float), df["close"].to_numpy(float),
                         np.zeros(len(df)), np.datetime64("1900-01-01", "D"), ())
    return out


# ── the panel: everything that does not depend on the sign path ─────────────────────────────────

@dataclass
class Panel:
    R: np.ndarray                       # (K,) decision dates (datetime64[D]), the chosen weekday of every week
    syms: list
    cls: list                           # class label per column
    cls_idx: np.ndarray                 # (N,) index into CLASSES
    cohort: np.ndarray                  # (N,) entry year clipped to 2003..2013 (null permutation group)
    S: dict                             # variant -> (K,N) signal in [-1,1] (0 where unavailable)
    volw: np.ndarray                    # (K,N) min(CAP, target/sigma) where tradable, else 0
    avail: np.ndarray                   # (K,N) bool, tradable at k
    gC: np.ndarray                      # (K,ncls) class scale min(GCAP, target / ex-ante vol of the class's always-long basket)
    ret: np.ndarray                     # (K,N) local-currency return from execution k to execution k+1 (last row 0)
    conv: np.ndarray                    # (K,N) quote-currency USD value ratio Q(k+1)/Q(k) (1 where no conversion pair)
    hs: np.ndarray                      # (K,N) full spread fraction at the decision bar
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

    def cols_of(self, classes) -> list:
        return [j for j, c in enumerate(self.cls) if c in classes]


def decision_dates(start, end, weekday: int = WEEKDAY) -> np.ndarray:
    names = ["MON", "TUE", "WED", "THU", "FRI"]
    s, e = pd.Timestamp(_utc(start).date()), pd.Timestamp(_utc(end).date())
    d = pd.date_range(s, e, freq=f"W-{names[weekday]}")
    return d[(d >= s) & (d < e)].to_numpy("datetime64[D]")


def _asof(D: np.ndarray, t: np.ndarray):
    """Index of the last bar with date <= t (and a validity flag)."""
    i = np.searchsorted(D, t, side="right") - 1
    return np.where(i >= 0, i, 0), i >= 0


def _quote_rate(quote: str, conv_insts: dict, Rx: np.ndarray):
    """USD value of one unit of `quote` at the first D1 open after Rx (or None when there is no conversion pair)."""
    if quote == "USD":
        return np.ones(len(Rx))
    if quote not in CONV_PAIRS or CONV_PAIRS[quote][0] not in conv_insts:
        return None
    pair, inv = CONV_PAIRS[quote]
    it = conv_insts[pair]
    pos = np.searchsorted(it.date, Rx, side="right")
    ok = pos < len(it.date)
    posc = np.where(ok, pos, 0)
    ok &= (it.date[posc] - Rx) <= np.timedelta64(EXEC_WAIT_D, "D")
    px = np.where(ok, it.open[posc], np.nan)
    return 1.0 / px if inv else px


def build_panel(insts: dict, start, end, weekday: int = WEEKDAY, exec_delay_days: int = 0, conv_insts: dict | None = None) -> Panel:
    """Wednesday (or `weekday`) decision from bars with label date <= the decision date (closed bars only); execution at the open of the
    first bar dated after the decision date (within 4 days); the position is held to the next execution."""
    R = decision_dates(start, end, weekday)
    K = len(R)
    syms = sorted(insts, key=lambda s: (CLASSES.index(insts[s].cls), s))
    N = len(syms)
    day = np.timedelta64(1, "D")
    S = {v: np.zeros((K, N)) for v in VNAMES}
    volw, avail = np.zeros((K, N)), np.zeros((K, N), bool)
    ret, conv, hs = np.zeros((K, N)), np.ones((K, N)), np.zeros((K, N))
    cnt = {"stale_signal": 0, "no_signal_bar": 0, "no_exec": 0, "marked_at_last_close": 0, "no_conversion_pair": 0, "converted": 0}
    Ry = R.astype("datetime64[Y]").astype(int) + 1970
    Rx = R + np.timedelta64(int(exec_delay_days), "D")
    zs = {}                                                 # per class: list of date-indexed daily vol-scaled returns
    conv_insts = conv_insts or {}
    for j, sym in enumerate(syms):
        it = insts[sym]
        D, lc = it.date, np.log(it.close)
        lv = (R >= it.entry) & ~np.isin(Ry, list(it.masked))
        idc, have = _asof(D, R)
        stale = have & ((R - D[idc]) > np.timedelta64(STALE_DAYS, "D"))
        r1 = np.concatenate([[np.nan], np.diff(lc)])
        sd = pd.Series(r1).rolling(VOL_WIN, min_periods=VOL_MIN).std(ddof=1).to_numpy() * np.sqrt(ANN_DAYS)
        sigma = sd[idc]
        # execution: first bar dated after Rx, within 4 days
        pos = np.searchsorted(D, Rx, side="right")
        okx = pos < len(D)
        posc = np.where(okx, pos, 0)
        okx &= (D[posc] - Rx) <= np.timedelta64(EXEC_WAIT_D, "D")
        p_open = np.where(okx, it.open[posc], np.nan)
        hs[:, j] = it.hs[idc]
        p_exit = np.full(K, np.nan)
        p_exit[:-1] = p_open[1:]
        miss = np.zeros(K, bool)
        miss[:-1] = ~np.isfinite(p_open[1:])
        if miss.any():
            lb = np.clip(np.searchsorted(D, Rx[1:], side="right") - 1, 0, len(D) - 1)
            p_exit[:-1] = np.where(miss[:-1], it.close[lb], p_exit[:-1])
        cnt["marked_at_last_close"] += int((miss[:-1] & okx[:-1] & lv[:-1]).sum())
        with np.errstate(invalid="ignore", divide="ignore"):
            rr = p_exit / p_open - 1.0
        rr[-1] = 0.0
        ret[:, j] = np.where(np.isfinite(rr), rr, 0.0)
        q = _quote_rate(it.quote, conv_insts, Rx)
        if q is None:
            cnt["no_conversion_pair"] += 1
        else:
            cnt["converted"] += 1
            c = np.ones(K)
            with np.errstate(invalid="ignore", divide="ignore"):
                c[:-1] = q[1:] / q[:-1]
            conv[:, j] = np.where(np.isfinite(c), c, 1.0)
        ok_sigma = np.isfinite(sigma) & (sigma > 0)
        av = lv & have & ~stale & okx & ok_sigma
        cnt["stale_signal"] += int((lv & have & stale).sum())
        cnt["no_signal_bar"] += int((lv & ~have).sum())
        cnt["no_exec"] += int((lv & ~okx).sum())
        with np.errstate(invalid="ignore", divide="ignore"):
            volw[:, j] = np.where(av, np.minimum(CAP, VOL_TARGET / sigma), 0.0)
        avail[:, j] = av
        # signals: sign of the log return over `L` calendar days to the signal bar
        signs = {}
        for L in sorted({l for ls in VARIANTS.values() for l in ls}):
            idb, hb = _asof(D, R - L * day)
            okb = hb & have & ((R - L * day - D[idb]) <= np.timedelta64(LOOKBACK_TOL_D, "D")) & (idb < idc)
            sr = np.where(okb, lc[idc] - lc[idb], np.nan)
            signs[L] = np.where(np.isfinite(sr), np.sign(sr), np.nan)
        for v, Ls in VARIANTS.items():
            m = np.mean([signs[L] for L in Ls], axis=0)
            S[v][:, j] = np.where(np.isfinite(m), m, 0.0)
        # daily vol-scaled return for the class basket (long, no signal; known only up to each bar)
        sd_prev = pd.Series(sd).shift(1).to_numpy()
        with np.errstate(invalid="ignore", divide="ignore"):
            z = r1 * np.minimum(CAP, VOL_TARGET / sd_prev)
        dts = D.astype("datetime64[D]")
        okz = (dts >= it.entry) & ~np.isin(dts.astype("datetime64[Y]").astype(int) + 1970, list(it.masked))
        zs.setdefault(it.cls, []).append(pd.Series(np.where(okz, z, np.nan), index=pd.DatetimeIndex(dts)))
    # class scale: target / ex-ante vol of the equal-weight always-long basket of vol-scaled instruments, history only
    gC = np.full((K, len(CLASSES)), np.nan)
    for c, lst in zs.items():
        B = pd.concat(lst, axis=1).mean(axis=1, skipna=True)
        bv = B.rolling(BASKET_WIN, min_periods=BASKET_MIN).std(ddof=1).to_numpy() * np.sqrt(ANN_DAYS)
        Bd = B.index.to_numpy("datetime64[D]")
        ib, hb = _asof(Bd, R)
        ok = hb & ((R - Bd[ib]) <= np.timedelta64(STALE_DAYS, "D")) & np.isfinite(bv[ib]) & (bv[ib] > 0)
        with np.errstate(invalid="ignore", divide="ignore"):
            gC[:, CLASSES.index(c)] = np.where(ok, np.minimum(GCAP, VOL_TARGET / bv[ib]), np.nan)
    cls_idx = np.array([CLASSES.index(insts[s].cls) for s in syms])
    # a class without a valid scale at k is not tradable at k
    avail &= np.isfinite(gC[:, cls_idx])
    volw = np.where(avail, volw, 0.0)
    days = np.zeros(K)
    days[:-1] = (R[1:] - R[:-1]) / day
    cohort = np.array([int(np.clip(np.datetime64(insts[s].entry, "Y").astype(int) + 1970, 2003, 2013)) for s in syms])
    return Panel(R, syms, [insts[s].cls for s in syms], cls_idx, cohort, S, volw, avail, gC, ret, conv, hs, days, cnt)


# ── P&L ──────────────────────────────────────────────────────────────────────────────────────

def swap_vec(panel: Panel, level: str) -> np.ndarray:
    return np.array([SWAP[level][c] for c in panel.cls])


def scale_matrix(panel: Panel, cols=None, scheme: str = "class") -> np.ndarray:
    """(K, ncols) weight per unit signal. scheme 'class' (primary): w = sig * min(CAP,target/sigma_i) / n_c * g_c / C, where n_c is the
    number of tradable instruments in the class at k, g_c the class scale and C the number of tradable classes. scheme 'equal'
    (descriptive): w = sig * min(CAP,target/sigma_i) / N_k. Both depend on history only."""
    cols = list(range(len(panel.syms))) if cols is None else list(cols)
    av = panel.avail[:, cols]
    vw = panel.volw[:, cols]
    if scheme == "equal":
        n = np.maximum(av.sum(1), 1).astype(float)
        return np.where(av, vw / n[:, None], 0.0)
    ci = panel.cls_idx[cols]
    n_c = np.zeros((panel.K, len(CLASSES)))
    for c in range(len(CLASSES)):
        sel = ci == c
        if sel.any():
            n_c[:, c] = av[:, sel].sum(1)
    C = np.maximum((n_c > 0).sum(1), 1).astype(float)
    g = np.nan_to_num(panel.gC[:, ci], nan=0.0)
    return np.where(av, vw * g / np.maximum(n_c[:, ci], 1.0) / C[:, None], 0.0)


def components(panel: Panel, S: np.ndarray, cols=None, level: str = "S0", cost_mult: float = 1.0, scheme: str = "class",
               usd: bool = True, off: np.ndarray | None = None, scale: np.ndarray | None = None):
    """Per-period, per-instrument gross P&L, trading cost, financing, weights and traded weight, arrays of shape (Kp, ncols)."""
    cols = list(range(len(panel.syms))) if cols is None else list(cols)
    sc = scale_matrix(panel, cols, scheme) if scale is None else scale
    if off is not None:
        sc = np.where(off[:, cols], 0.0, sc)
    w = S[:, cols] * sc
    wp = np.vstack([np.zeros((1, w.shape[1])), w[:-1]])
    dw = np.abs(w - wp)
    cost = dw * (0.5 * cost_mult * panel.hs[:, cols] + COMM)
    r = panel.ret[:, cols] * (panel.conv[:, cols] if usd else 1.0)
    gross = w * r
    swap = np.abs(w) * swap_vec(panel, level)[cols][None, :] * panel.days[:, None] / 365.0
    k = panel.Kp
    return gross[:k], cost[:k], swap[:k], w[:k], dw[:k]


def net_series(panel: Panel, S: np.ndarray, cols=None, level: str = "S0", cost_mult: float = 1.0, scheme: str = "class", usd: bool = True,
               off=None, scale=None) -> np.ndarray:
    g, c, s, _, _ = components(panel, S, cols, level, cost_mult, scheme, usd, off, scale)
    return (g - c - s).sum(1)


def sharpe(x: np.ndarray) -> float:
    x = np.asarray(x, float)
    if len(x) < 3:
        return float("nan")
    sd = x.std(ddof=1)
    return float(x.mean() / sd * np.sqrt(52)) if sd > 0 else 0.0


def bl_signs(panel: Panel) -> np.ndarray:
    return np.ones((panel.K, len(panel.syms)))


# ── nulls ──────────────────────────────────────────────────────────────────────────────────────

def derangement(n: int, rng: np.random.Generator) -> np.ndarray:
    if n < 2:
        return np.arange(n)
    while True:
        p = rng.permutation(n)
        if not (p == np.arange(n)).any():
            return p


def block_perm_index(n: int, block: int, rng: np.random.Generator) -> np.ndarray:
    """Cut 0..n-1 into blocks of `block` (the last may be shorter), permute the blocks by a derangement, concatenate."""
    bounds = [(a, min(a + block, n)) for a in range(0, n, block)]
    p = derangement(len(bounds), rng)
    return np.concatenate([np.arange(*bounds[j]) for j in p])


def cohort_weeks(panel: Panel) -> dict:
    """cohort -> (column indices, week indices from the cohort's first tradable week to the end)."""
    out = {}
    for c in sorted(set(panel.cohort.tolist())):
        cols = np.where(panel.cohort == c)[0]
        wk = np.where(panel.avail[:, cols].any(1))[0]
        if len(wk):
            out[c] = (cols, np.arange(wk[0], panel.K))
    return out


def null_replicate(panel: Panel, rng: np.random.Generator, mode: str = "perm", lag: int | None = None, block: int = BLOCK_NULL) -> list:
    """A list of (cols, target weeks, source weeks) maps. mode 'perm' (primary): one block derangement per entry cohort, shared by the
    cohort's instruments (cross-sectional alignment inside a cohort is kept); 'indep': a derangement per instrument; 'shift': circular
    shift by `lag` weeks inside each cohort's span."""
    maps = []
    for c, (cols, wk) in cohort_weeks(panel).items():
        n = len(wk)
        if mode == "shift":
            maps.append((cols, wk, wk[(np.arange(n) - lag) % n]))
        elif mode == "indep":
            for j in cols:
                maps.append((np.array([j]), wk, wk[block_perm_index(n, block, rng)]))
        else:
            maps.append((cols, wk, wk[block_perm_index(n, block, rng)]))
    return maps


def apply_maps(S: np.ndarray, maps: list) -> np.ndarray:
    Sp = S.copy()
    for cols, tgt, src in maps:
        Sp[np.ix_(tgt, cols)] = S[np.ix_(src, cols)]
    return Sp


def null_distribution(panel: Panel, n: int, seed: int, mode: str = "perm", lags=None, block: int = BLOCK_NULL) -> dict:
    """S0 Sharpe (net of spread) of every variant under n sign-path replicates; the same replicate for all variants. Also the mean
    traded weight per week and the mean gross notional (diagnostics of the null's turnover and exposure against the actual)."""
    rng = np.random.default_rng(seed)
    reps = list(lags) if mode == "shift" else range(n)
    sc = scale_matrix(panel)
    sr = np.zeros((len(reps), len(VNAMES)))
    to, gn = np.zeros_like(sr), np.zeros_like(sr)
    for r, m in enumerate(reps):
        maps = null_replicate(panel, rng, mode, m if mode == "shift" else None, block)
        for vi, v in enumerate(VNAMES):
            Sp = apply_maps(panel.S[v], maps)
            g, c, s, w, dw = components(panel, Sp, scale=sc)
            sr[r, vi] = sharpe((g - c).sum(1))
            to[r, vi] = dw.sum(1).mean()
            gn[r, vi] = np.abs(w).sum(1).mean()
    return {"sr": sr, "turnover": to, "gross": gn}


def p_values(obs: np.ndarray, null_sr: np.ndarray) -> np.ndarray:
    """Individual one-sided Monte Carlo p-values (1 + #{null >= obs}) / (1 + M)."""
    M = null_sr.shape[0]
    return np.array([(1 + (null_sr[:, i] >= obs[i]).sum()) / (1 + M) for i in range(len(obs))])


def wy_stepdown(obs: np.ndarray, null_sr: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Westfall-Young step-down maxT adjusted p-values on null-standardised statistics z_v = (SR_v - null mean) / null sd, using
    the joint null replicates (so the dependence between variants is respected). Returns (adjusted p, z_obs, single-step max p)."""
    M, V = null_sr.shape
    mu, sd = null_sr.mean(0), null_sr.std(0, ddof=1)
    sd = np.where(sd > 0, sd, 1.0)
    zn = (null_sr - mu) / sd
    zo = (obs - mu) / sd
    order = np.argsort(-zo)
    adj = np.zeros(V)
    run = 0.0
    for j, i in enumerate(order):
        mx = zn[:, order[j:]].max(1)
        run = max(run, (1 + (mx >= zo[i]).sum()) / (1 + M))
        adj[i] = run
    mx_all = zn.max(1)
    single = np.array([(1 + (mx_all >= zo[i]).sum()) / (1 + M) for i in range(V)])
    return adj, zo, single


def holm(p: np.ndarray, alpha: float = ALPHA) -> tuple[np.ndarray, np.ndarray]:
    """Holm step-down. Returns (reject flags, adjusted p-values) in the input order."""
    K = len(p)
    order = np.argsort(p)
    adj = np.zeros(K)
    run = 0.0
    for rank, i in enumerate(order):
        run = max(run, min(1.0, (K - rank) * p[i]))
        adj[i] = run
    return adj <= alpha, adj


# ── bootstrap ──────────────────────────────────────────────────────────────────────────────────

def boot_index(n: int, block: int, B: int, rng: np.random.Generator) -> np.ndarray:
    nb = int(np.ceil(n / block))
    starts = rng.integers(0, n, size=(B, nb))
    idx = (starts[:, :, None] + np.arange(block)[None, None, :]) % n
    return idx.reshape(B, nb * block)[:, :n]


def boot_sharpes(series: dict, blocks=BOOT_BLOCKS, B: int = N_BOOT, seed: int = SEED_BOOT) -> dict:
    """Per named weekly series: point SR and the 2.5 / 97.5 percentiles over B circular-block resamples for each block length; all series
    share the resampled weeks. The quoted interval is the one with the lowest lower bound."""
    n = len(next(iter(series.values())))
    out = {k: {"sr": sharpe(v), "ci": {}} for k, v in series.items()}
    for b in blocks:
        idx = boot_index(n, b, B, np.random.default_rng(seed + b))
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


# ── per-variant statistics and gates ───────────────────────────────────────────────────────────

def era_of(year: int) -> str:
    for name, a, b in ERAS:
        if a <= year <= b:
            return name
    return "other"


def present_classes(panel: Panel) -> list:
    return [c for c in CLASSES if c in set(panel.cls)]


def variant_stats(panel: Panel, v: str, S: np.ndarray | None = None) -> dict:
    """Everything the gates and the report need for one variant on a panel (no null)."""
    S = panel.S[v] if S is None else S
    out = {}
    for lvl in SWAP:
        out[f"net_{lvl}"] = net_series(panel, S, level=lvl)
    out["net_S0_x2"] = net_series(panel, S, level="S0", cost_mult=2.0)
    out["net_S1_x2"] = net_series(panel, S, level="S1", cost_mult=2.0)
    out["net_S1_local"] = net_series(panel, S, level="S1", usd=False)
    out["net_S1_equal"] = net_series(panel, S, level="S1", scheme="equal")
    out["loo_S1"], out["only_S1"] = {}, {}
    for c in present_classes(panel):
        rest = [j for j in range(len(panel.syms)) if panel.cls[j] != c]
        if rest:                                   # a one-class universe has no leave-one-class-out portfolio (G4 is then vacuous)
            out["loo_S1"][c] = net_series(panel, S, cols=rest, level="S1")
        out["only_S1"][c] = net_series(panel, S, cols=panel.cols_of([c]), level="S1")
    g, c_, s, w, dw = components(panel, S)
    out["gross"], out["cost"] = g.sum(1), c_.sum(1)
    out["w"], out["dw"], out["g_i"], out["c_i"], out["s_i"] = w, dw, g, c_, s
    return out


def gates(panel: Panel, st: dict, wy_p: float) -> dict:
    """Section 6 gates for one variant. `st` is variant_stats output."""
    yrs = panel.years
    n1 = st["net_S1"]
    sr1 = sharpe(n1)
    y_full = [y for y in range(2003, 2021)]
    y_pos = sum(1 for y in y_full if n1[yrs == y].sum() > 0)
    tot_by_year = {int(y): n1[yrs == y].sum() for y in np.unique(yrs)}
    best3 = sorted(tot_by_year, key=lambda y: tot_by_year[y], reverse=True)[:3]
    sr_wo3 = sharpe(n1[~np.isin(yrs, best3)])
    era_pos = sum(1 for _, a, b in ERAS if sharpe(n1[(yrs >= a) & (yrs <= b)]) > 0)
    loo = {c: sharpe(x) for c, x in st["loo_S1"].items()}
    G = {"G1": bool(wy_p <= ALPHA), "G2": bool(sr1 > 0), "G3a": bool(sr_wo3 > 0), "G3b": bool(y_pos >= G3B_MIN_POSITIVE_YEARS),
         "G3c": bool(era_pos >= G3C_MIN_ERAS), "G4": bool(all(x > 0 for x in loo.values())), "G5": bool(sr1 >= G5_MIN_SR)}
    G["eligible"] = all(G[k] for k in ("G1", "G2", "G3a", "G3b", "G3c", "G4", "G5"))
    G["detail"] = {"sr_S1": sr1, "years_positive_2003_2020": y_pos, "sr_without_best3_years": sr_wo3, "best3_years": [int(y) for y in best3],
                   "eras_positive": era_pos, "sr_leave_class_out_S1": loo, "min_leave_class_out": min(loo.values()) if loo else float("nan")}
    return G


def select_candidate(eligible: dict) -> str | None:
    """Highest SR(S1) among eligible variants."""
    el = {v: x for v, x in eligible.items() if x["eligible"]}
    return max(el, key=lambda v: el[v]["detail"]["sr_S1"]) if el else None


def evaluate(panel: Panel, n_perm: int, seed: int) -> dict:
    """Stats, null, adjusted p-values and gates for all variants on one panel (used by the DEV run and the calibration)."""
    stats = {v: variant_stats(panel, v) for v in VNAMES}
    sr0 = np.array([sharpe(stats[v]["net_S0"]) for v in VNAMES])
    nd = null_distribution(panel, n_perm, seed, "perm")
    p = p_values(sr0, nd["sr"])
    rej, padj_h = holm(p)
    wy, zo, single = wy_stepdown(sr0, nd["sr"])
    G = {v: gates(panel, stats[v], float(wy[i])) for i, v in enumerate(VNAMES)}
    return {"stats": stats, "sr0": sr0, "null": nd, "p": p, "holm_adj": padj_h, "wy": wy, "z": zo, "p_single_max": single, "gates": G,
            "candidate": select_candidate(G)}


# ── DEV run ────────────────────────────────────────────────────────────────────────────────────

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


def run_dev(out_dir: Path = OUT_DEV, n_perm: int = N_PERM, n_boot: int = N_BOOT, insts: dict | None = None, conv_insts: dict | None = None,
            start: str = DEV_START, end: str = DEV_END, quiet: bool = False) -> dict:
    s0, e0 = check_dev_window(start, end)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    given = insts is not None
    insts = insts if given else load_dev_insts(end)
    conv_insts = conv_insts if conv_insts is not None else ({} if given else load_conv_insts(end))
    panel = build_panel(insts, s0, e0, WEEKDAY, conv_insts=conv_insts)
    prereg_sha = sha256_file(PREREG) if PREREG.exists() else "missing"
    (out_dir / "prereg_sha256.txt").write_text(prereg_sha + "\n")
    ev = evaluate(panel, n_perm, SEED_PERM)
    stats, G, nd = ev["stats"], ev["gates"], ev["null"]
    S_bl = bl_signs(panel)
    bl = {l: net_series(panel, S_bl, level=l) for l in ("S0", "S1")}
    series = {"BL_S0": bl["S0"], "BL_S1": bl["S1"]}
    for v in VNAMES:
        for l in SWAP:
            series[f"{v}_{l}"] = stats[v][f"net_{l}"]
        series[f"{v}_minus_BL_S0"] = stats[v]["net_S0"] - bl["S0"]
        for c, x in stats[v]["loo_S1"].items():
            series[f"{v}_without_{c}_S1"] = x
        for c, x in stats[v]["only_S1"].items():
            series[f"{v}_only_{c}_S1"] = x
    bs = boot_sharpes(series, B=n_boot)
    lags = range(78, max(panel.K - 78, 79), 4)
    sh = null_distribution(panel, len(lags), 0, "shift", lags)
    ind = null_distribution(panel, min(n_perm, 500), SEED_PERM + 1, "indep")
    p_sh, p_in = p_values(ev["sr0"], sh["sr"]), p_values(ev["sr0"], ind["sr"])
    rows = []
    for i, v in enumerate(VNAMES):
        st, g = stats[v], G[v]
        base = (np.abs(st["w"]).sum(1) * panel.days[: panel.Kp] / 365.0).mean()
        rows.append({
            "variant": v, "SR_gross": sharpe(st["gross"]), "SR_S0": ev["sr0"][i], "SR_S0_ci_lo": bs[f"{v}_S0"]["lo"], "SR_S0_ci_hi": bs[f"{v}_S0"]["hi"],
            "SR_S1": sharpe(st["net_S1"]), "SR_S1_ci_lo": bs[f"{v}_S1"]["lo"], "SR_S1_ci_hi": bs[f"{v}_S1"]["hi"], "SR_S2": sharpe(st["net_S2"]),
            "SR_S1_costx2": sharpe(st["net_S1_x2"]), "SR_S1_local_ccy": sharpe(st["net_S1_local"]), "SR_S1_equal_risk": sharpe(st["net_S1_equal"]),
            "SR_minus_BL_S0": ev["sr0"][i] - sharpe(bl["S0"]), "diff_ci_lo": bs[f"{v}_minus_BL_S0"]["lo"], "diff_ci_hi": bs[f"{v}_minus_BL_S0"]["hi"],
            **{f"SR_S1_without_{c}": sharpe(x) for c, x in st["loo_S1"].items()}, **{f"SR_S1_only_{c}": sharpe(x) for c, x in st["only_S1"].items()},
            "ann_ret_S0": float(st["net_S0"].mean() * 52), "ann_vol": float(st["net_S0"].std(ddof=1) * np.sqrt(52)),
            "gross_notional": float(np.abs(st["w"]).sum(1).mean()), "turnover_per_year": float(st["dw"].sum(1).mean() * 52),
            "cost_drag_per_year": float(st["cost"].mean() * 52),
            "breakeven_charge_pct": float((st["gross"] - st["cost"]).mean() / base * 100) if base > 0 else float("nan"),
            "p_indiv": ev["p"][i], "p_holm_adj": ev["holm_adj"][i], "p_wy_stepdown": ev["wy"][i], "p_single_step_max": ev["p_single_max"][i],
            "null_mean_SR": float(nd["sr"][:, i].mean()), "null_sd_SR": float(nd["sr"][:, i].std(ddof=1)),
            "null_turnover_ratio": float(nd["turnover"][:, i].mean() / st["dw"].sum(1).mean()),
            "null_gross_ratio": float(nd["gross"][:, i].mean() / np.abs(st["w"]).sum(1).mean()),
            "p_shift_null": p_sh[i], "p_indep_null": p_in[i],
            **{k: g[k] for k in ("G1", "G2", "G3a", "G3b", "G3c", "G4", "G5", "eligible")},
            "years_positive_2003_2020": g["detail"]["years_positive_2003_2020"], "SR_without_best3": g["detail"]["sr_without_best3_years"],
            "eras_positive": g["detail"]["eras_positive"]})
    tab = pd.DataFrame(rows)
    tab.to_csv(out_dir / "variants.csv", index=False)
    wk = pd.DataFrame({"R": panel.R[: panel.Kp]})
    for v in VNAMES:
        wk[f"{v}_S0"], wk[f"{v}_S1"] = stats[v]["net_S0"], stats[v]["net_S1"]
    wk["BL_S0"] = bl["S0"]
    wk.to_csv(out_dir / "weekly_pnl.csv", index=False)
    _per_year(panel, {**{f"{v}_S1": stats[v]["net_S1"] for v in VNAMES}, "BL_S0": bl["S0"]}).to_csv(out_dir / "per_year.csv", index=False)
    pi = []
    for v in VNAMES:
        for j, sym in enumerate(panel.syms):
            x = stats[v]["g_i"][:, j] - stats[v]["c_i"][:, j]
            live = panel.avail[: panel.Kp, j]
            pi.append({"variant": v, "symbol": sym, "class": panel.cls[j], "weeks_live": int(live.sum()), "sum_ret_S0": float(x.sum()),
                       "SR_S0_standalone": sharpe(x[live]) if live.sum() > 20 else float("nan")})
    pd.DataFrame(pi).to_csv(out_dir / "per_instrument.csv", index=False)
    pd.DataFrame(nd["sr"], columns=VNAMES).to_csv(out_dir / "null_distribution.csv", index=False)
    sens = _sensitivity(insts, conv_insts, panel, stats, s0, e0)
    sens.to_csv(out_dir / "sensitivity.csv", index=False)
    summary = {"prereg_sha256": prereg_sha, "code_sha256": sha256_file(Path(__file__)), "data_code_sha256": sha256_file(Path(mtd.__file__)),
               "window": [str(s0.date()), str(e0.date())], "weeks": panel.Kp, "years": round(panel.Kp / 52.0, 2), "n_perm": n_perm, "n_boot": n_boot,
               "instruments": {c: int(sum(1 for x in panel.cls if x == c)) for c in CLASSES}, "bl_SR_S0": sharpe(bl["S0"]), "bl_SR_S1": sharpe(bl["S1"]),
               "candidate": ev["candidate"], "panel_counts": panel.counts,
               "wy_adj_p": dict(zip(VNAMES, [float(x) for x in ev["wy"]])), "holm_adj_p": dict(zip(VNAMES, [float(x) for x in ev["holm_adj"]])),
               "eligible": {v: bool(G[v]["eligible"]) for v in VNAMES},
               "gates": {v: {k: G[v][k] for k in ("G1", "G2", "G3a", "G3b", "G3c", "G4", "G5")} for v in VNAMES},
               "gate_detail": {v: G[v]["detail"] for v in VNAMES}}
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, default=float))
    if ev["candidate"]:
        v = ev["candidate"]
        fc = {"variant": v, "lookbacks_calendar_days": list(VARIANTS[v]), "weekday": WEEKDAY, "scheme": "class", "swap_S1": SWAP["S1"],
              "SR_S1_dev": G[v]["detail"]["sr_S1"], "prereg_sha256": prereg_sha, "code_sha256": summary["code_sha256"]}
        (out_dir / "frozen_candidate.json").write_text(json.dumps(fc, indent=2, default=float))
    if not quiet:
        print(format_report(tab, summary))
    return {"table": tab, "summary": summary, "panel": panel, "eval": ev, "sens": sens}


def _sensitivity(insts, conv_insts, panel, stats, s0, e0) -> pd.DataFrame:
    """Descriptive only: SR (S0 and S1) of every variant under alternative weekdays, one-day delay, spread x2, start years, year exclusions,
    equal-risk weighting and local-currency returns. Nothing here changes eligibility."""
    rows = []

    def add(name, fn):
        for v in VNAMES:
            x0, x1 = fn(v, "S0"), fn(v, "S1")
            rows.append({"case": name, "variant": v, "SR_S0": sharpe(x0), "SR_S1": sharpe(x1), "weeks": len(x0)})

    yrs = panel.years
    add("primary", lambda v, l: stats[v][f"net_{l}"])
    add("spread_x2", lambda v, l: net_series(panel, panel.S[v], level=l, cost_mult=2.0))
    add("equal_risk_weighting", lambda v, l: net_series(panel, panel.S[v], level=l, scheme="equal"))
    add("local_currency_returns", lambda v, l: net_series(panel, panel.S[v], level=l, usd=False))
    for name, mask in (("from_2010", yrs >= 2010), ("from_2014", yrs >= 2014), ("without_2008", yrs != 2008), ("without_2020", yrs != 2020)):
        add(name, lambda v, l, m=mask: net_series(panel, panel.S[v], level=l)[m])
    for wd, nm in enumerate(["Mon", "Tue", "Wed", "Thu", "Fri"]):
        if wd == WEEKDAY:
            continue
        pw = build_panel(insts, s0, e0, wd, conv_insts=conv_insts)
        add(f"weekday_{nm}", lambda v, l, pw=pw: net_series(pw, pw.S[v], level=l))
    pd1 = build_panel(insts, s0, e0, WEEKDAY, exec_delay_days=1, conv_insts=conv_insts)
    add("exec_delay_1day", lambda v, l: net_series(pd1, pd1.S[v], level=l))
    return pd.DataFrame(rows)


def format_report(tab: pd.DataFrame, summary: dict) -> str:
    L = [f"Window {summary['window'][0]} to {summary['window'][1]}, {summary['weeks']} weekly periods ({summary['years']} years). prereg sha256 {summary['prereg_sha256'][:12]}",
         f"Instruments by class: {summary['instruments']}",
         f"Benchmark BL (always long, vol-targeted, class-balanced): SR S0 {summary['bl_SR_S0']:+.2f}, S1 {summary['bl_SR_S1']:+.2f}", ""]
    cols = ["variant", "SR_gross", "SR_S0", "SR_S0_ci_lo", "SR_S0_ci_hi", "SR_S1", "SR_S2", "SR_minus_BL_S0", "p_indiv", "p_holm_adj", "p_wy_stepdown",
            "null_mean_SR", "null_sd_SR", "null_turnover_ratio", "turnover_per_year", "gross_notional", "ann_vol", "breakeven_charge_pct", "eligible"]
    L.append(tab[cols].to_string(index=False, float_format=lambda x: f"{x:.3f}"))
    L.append("")
    gcols = ["variant", "G1", "G2", "G3a", "G3b", "G3c", "G4", "G5", "years_positive_2003_2020", "eras_positive", "SR_without_best3", "p_shift_null", "p_indep_null"]
    L.append(tab[gcols].to_string(index=False, float_format=lambda x: f"{x:.3f}"))
    L.append(f"\nCandidate: {summary['candidate']}")
    return "\n".join(L)


# ── FINAL (disabled unless FINAL_GO; specified in the prereg, not run) ─────────────────────────

def load_final_insts(end=FINAL_END, go_path=None, cand_path=None, universe: pd.DataFrame | None = None, data_dir: Path | None = None) -> dict:
    """FINAL loader: only after require_final_go; end capped at 2025-05-01. Reads the whole history up to `end` (look-back needs DEV bars)."""
    require_final_go(go_path, cand_path)
    if _utc(end) > _utc(FINAL_END):
        raise ValueError("end after 2025-05-01 is not part of FINAL")
    root = Path(data_dir) if data_dir else mtd.DATA_DIR
    uni = mtd.load_universe() if universe is None else universe
    out = {}
    for _, u in uni.iterrows():
        spec = mtd.load_spec(u["symbol"], root)
        raw = pd.read_parquet(root / f"{u['symbol']}_D1.parquet", filters=[("time", "<", _utc(end))]).sort_values("time").reset_index(drop=True)
        df, _ = mtd.clean_d1(raw)
        extra = {y for y in mtd.mask_years(df) if y >= 2021}          # the same data rules, applied to the FINAL years
        out[u["symbol"]] = inst_from_df(u["symbol"], u["class"], u["quote_ccy"], df, float(spec["point"]), u["entry"], sorted(set(u["masked_years"]) | extra))
    return out


def run_final(out_dir: Path = OUT_FINAL, insts: dict | None = None, conv_insts: dict | None = None, go_path=None, cand_path=None,
              n_boot: int = N_BOOT, n_perm: int = N_PERM) -> dict:
    """One run of the frozen candidate on 2021-06-01 .. 2025-04-30 (prereg section 9). Refuses without FINAL_GO carrying the candidate hash."""
    cand_sha = require_final_go(go_path, cand_path)
    fc = json.loads(Path(cand_path or CANDIDATE).read_text())
    v = fc["variant"]
    insts = insts if insts is not None else load_final_insts(FINAL_END, go_path, cand_path)
    conv_insts = conv_insts or {}
    panel = build_panel(insts, FINAL_START, FINAL_END, int(fc["weekday"]), conv_insts=conv_insts)
    S = panel.S[v]
    s1 = net_series(panel, S, level="S1")
    s0 = net_series(panel, S, level="S0")
    bl = net_series(panel, bl_signs(panel), level="S0")
    ex = [j for j in range(len(panel.syms)) if panel.cls[j] not in ("EQ", "MT")]
    s1_ex = net_series(panel, S, cols=ex, level="S1")
    bs = boot_sharpes({"S1": s1, "S1_ex_EQ_MT": s1_ex, "diff_S0": s0 - bl}, blocks=(13, 26), B=n_boot)
    nd = null_distribution(panel, n_perm, SEED_PERM + 2, "perm", block=13)
    vi = VNAMES.index(v)
    p_null = float((1 + (nd["sr"][:, vi] >= sharpe(s0)).sum()) / (1 + n_perm))
    confirmed = p_null <= 0.025 and bs["S1"]["sr"] > 0 and sharpe(s0) - sharpe(bl) > 0
    verdict = "CONFIRMED" if confirmed else ("CONSISTENT BUT NOT CONFIRMED" if bs["S1"]["sr"] > 0 and p_null <= 0.10 else "NOT REPLICATED")
    res = {"candidate": v, "candidate_sha256": cand_sha, "weeks": panel.Kp, "S1": bs["S1"], "S1_ex_EQ_MT": bs["S1_ex_EQ_MT"], "diff_vs_BL_S0": bs["diff_S0"],
           "p_timing_null": p_null, "SR_S0": sharpe(s0), "SR_BL_S0": sharpe(bl), "verdict": verdict}
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    (Path(out_dir) / "final_result.json").write_text(json.dumps(res, indent=2, default=float))
    return res


# ── synthetic calibration (no project returns) ─────────────────────────────────────────────────

SYN_VOL = {"FX": 0.08, "EQ": 0.17, "EN": 0.30, "MT": 0.18, "AG": 0.22}
SYN_CLASS_RHO = {"FX": 0.15, "EQ": 0.60, "EN": 0.80, "MT": 0.50, "AG": 0.20}


def _ar1(eta: np.ndarray, phi: float) -> np.ndarray:
    """m_t = phi m_{t-1} + eta_t with m_0 = eta_0."""
    try:
        from scipy.signal import lfilter
        return lfilter([1.0], [1.0, -phi], eta)
    except ImportError:                                   # pragma: no cover
        m, acc = np.empty(len(eta)), 0.0
        for t in range(len(eta)):
            acc = phi * acc + eta[t]
            m[t] = acc
        return m


def synth_insts(rng: np.random.Generator, uni: pd.DataFrame, strength: float, end=DEV_END, phi: float = 0.99, glob_rho: float = 0.1) -> dict:
    """Synthetic instruments with the universe's classes and entry dates on the real weekday calendar. Daily return = global factor +
    class factor + idiosyncratic noise (class correlation SYN_CLASS_RHO) + a persistent AR(1) drift with std `strength` x daily vol
    (strength 0 = no predictability). Execution price = D1 open = previous close. No project data is read."""
    days = pd.bdate_range("1999-01-01", pd.Timestamp(end) - pd.Timedelta(days=1))
    n = len(days)
    dd = days.to_numpy("datetime64[D]")
    gl = rng.standard_normal(n)
    cf = {c: rng.standard_normal(n) for c in CLASSES}
    out = {}
    for _, u in uni.iterrows():
        c = u["class"]
        vol = SYN_VOL[c] / np.sqrt(ANN_DAYS)
        rho = SYN_CLASS_RHO[c]
        noise = vol * (np.sqrt(glob_rho) * gl + np.sqrt(rho) * cf[c] + np.sqrt(max(1 - glob_rho - rho, 0.05)) * rng.standard_normal(n))
        eta = rng.standard_normal(n) * np.sqrt(1 - phi ** 2)
        eta[0] = 0.0
        m = _ar1(eta, phi)
        r = noise + strength * vol * m
        close = (100.0 if c != "FX" else 1.0) * np.exp(np.cumsum(r))
        first = np.datetime64(pd.Timestamp(u["entry"]).date(), "D") - np.timedelta64(420, "D")
        keep = dd >= first
        opn = np.concatenate([[close[0]], close[:-1]])
        hs = np.full(n, mtd.SPREAD_FLOOR_BP[c] * 1e-4)
        out[u["symbol"]] = Inst(u["symbol"], c, "USD", dd[keep], opn[keep], close[keep], hs[keep], np.datetime64(pd.Timestamp(u["entry"]).date(), "D"),
                                tuple(u["masked_years"]) if "masked_years" in u else ())
    return out


def analytic_power(T_years: float, alpha: float, f: float = 1.0) -> dict:
    """Plug-in normal power of a Sharpe test: SE = sqrt((1 + SR^2/2) f / T)."""
    from math import erf, sqrt

    def Phi(x):
        return 0.5 * (1 + erf(x / sqrt(2)))

    def zq(p):                                   # inverse normal by bisection
        lo, hi = -10.0, 10.0
        for _ in range(80):
            mid = (lo + hi) / 2
            lo, hi = (mid, hi) if Phi(mid) < p else (lo, mid)
        return (lo + hi) / 2

    za = zq(1 - alpha)
    se0 = sqrt(f / T_years)
    out = {"se_at_zero": se0, "critical_sr": za * se0}
    for sr in (0.3, 0.5, 0.75, 1.0):
        se = sqrt((1 + sr * sr / 2) * f / T_years)
        out[f"power_{sr}"] = 1 - Phi((za * se0 - sr) / se)
    lo, hi = 0.0, 5.0
    for _ in range(60):
        mid = (lo + hi) / 2
        se = sqrt((1 + mid * mid / 2) * f / T_years)
        pw = 1 - Phi((za * se0 - mid) / se)
        lo, hi = (mid, hi) if pw < 0.8 else (lo, mid)
    out["sr_for_80pct_power"] = (lo + hi) / 2
    return out


PROVISIONAL_DESIGNS = {      # (class, n instruments, entry date); used only while the fetch is incomplete, never for the DEV run
    "fx_only28": (("FX", 28, "2003-01-01"),),
    "broad45": (("FX", 28, "2003-01-01"), ("EQ", 8, "2003-01-01"), ("EN", 2, "2003-01-01"), ("MT", 4, "2003-01-01"), ("AG", 3, "2009-01-07")),
}


def provisional_universe(name: str) -> pd.DataFrame:
    rows = []
    for cls, n, ent in PROVISIONAL_DESIGNS[name]:
        rows += [{"symbol": f"{cls}{i}", "class": cls, "entry": ent, "masked_years": [], "quote_ccy": "USD"} for i in range(n)]
    return pd.DataFrame(rows)


def financing_diagnostics(uni: pd.DataFrame, strength: float = 0.05, n: int = 8, seed: int = 900, v: str = "B4") -> dict:
    """Synthetic only: how large the S1 / S2 financing charge is in Sharpe units for a book of this shape (the charge is c x gross notional,
    the Sharpe denominator is the signed book's vol, which diversification pushes far below the gross notional)."""
    rows = []
    for i in range(n):
        p = build_panel(synth_insts(np.random.default_rng(seed + i), uni, strength), DEV_START, DEV_END)
        S = p.S[v]
        n0, n1, n2 = (net_series(p, S, level=l) for l in ("S0", "S1", "S2"))
        g, c, s, w, dw = components(p, S, level="S0")
        rows.append([sharpe(n0), sharpe(n1), sharpe(n2), n0.std(ddof=1) * np.sqrt(52), np.abs(w).sum(1).mean(),
                     components(p, S, level="S1")[2].sum(1).mean() * 52, components(p, S, level="S2")[2].sum(1).mean() * 52,
                     dw.sum(1).mean() * 52, c.sum(1).mean() * 52])
    a = np.mean(rows, 0)
    keys = ["SR_S0", "SR_S1", "SR_S2", "ann_vol", "gross_notional", "financing_S1_per_year", "financing_S2_per_year", "turnover_per_year", "spread_cost_per_year"]
    return {"variant": v, "strength": strength, "n_panels": n, **dict(zip(keys, [float(x) for x in a])), "note": "synthetic panels; no project returns"}


def calibrate(out_dir: Path = OUT_DEV, uni: pd.DataFrame | None = None, n_panels: int = 24, n_perm: int = 199,
              strengths=(0.0, 0.015, 0.025, 0.035, 0.05, 0.065, 0.085), seed: int = 11, name: str = "calibration") -> dict:
    """Gate behaviour on synthetic planted-trend panels with the real universe shape and calendar: pass probability of G1 and of eligibility
    against the realised S0 Sharpe of the best variant. Thresholds are NOT tuned from this."""
    uni = mtd.load_universe() if uni is None else uni
    rows = []
    for st in strengths:
        rng = np.random.default_rng(seed + int(st * 1000))
        acc = {"sr0_best": [], "sr0_B4": [], "sr0_BL": [], "any_G1": [], "any_elig": [], "B4_G1": [], "B4_elig": [], **{g: [] for g in ("G2", "G3a", "G3b", "G3c", "G4", "G5")}}
        for _ in range(n_panels):
            insts = synth_insts(rng, uni, st)
            panel = build_panel(insts, DEV_START, DEV_END, WEEKDAY)
            ev = evaluate(panel, n_perm, int(rng.integers(1 << 30)))
            G = ev["gates"]
            acc["sr0_best"].append(float(ev["sr0"].max()))
            acc["sr0_B4"].append(float(ev["sr0"][VNAMES.index("B4")]))
            acc["sr0_BL"].append(sharpe(net_series(panel, bl_signs(panel), level="S0")))
            acc["any_G1"].append(any(G[v]["G1"] for v in VNAMES))
            acc["any_elig"].append(any(G[v]["eligible"] for v in VNAMES))
            acc["B4_G1"].append(G["B4"]["G1"])
            acc["B4_elig"].append(G["B4"]["eligible"])
            for g in ("G2", "G3a", "G3b", "G3c", "G4", "G5"):
                acc[g].append(float(np.mean([G[v][g] for v in VNAMES])))
        rows.append({"strength": st, "n_panels": n_panels, "n_perm": n_perm, **{k: float(np.mean(x)) for k, x in acc.items()},
                     "sr0_best_sd": float(np.std(acc["sr0_best"], ddof=1))})
    T = len(decision_dates(DEV_START, DEV_END)) / 52.0
    power = {f"alpha_{a}_f_{f}": analytic_power(T, a, f) for a in (0.05, 0.01, 0.025) for f in (1.0, 1.3)}
    res = {"rows": rows, "financing_diagnostics": financing_diagnostics(uni), "analytic_power": power, "T_years": T, "universe_counts": uni.groupby("class").size().to_dict(),
           "note": "synthetic factor-structured panels with AR(1) planted drift on the real weekday calendar; no project returns; thresholds are not tuned from this"}
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    (Path(out_dir) / f"{name}.json").write_text(json.dumps(res, indent=2))
    return res


# ── CLI ────────────────────────────────────────────────────────────────────────────────────────

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
    c.add_argument("--n-panels", type=int, default=24)
    c.add_argument("--n-perm", type=int, default=199)
    c.add_argument("--design", default="universe", help="'universe' (research/output/multi_trend_dev/universe.csv) or a provisional design name")
    sub.add_parser("final")
    f = sub.add_parser("diagnostics", help="synthetic financing-charge diagnostics for a provisional design")
    f.add_argument("--design", required=True)
    a = ap.parse_args(argv)
    try:
        if a.cmd == "dev":
            run_dev(Path(a.out), a.n_perm, a.n_boot, start=a.start, end=a.end)
        elif a.cmd == "calibrate":
            if a.design == "universe":
                res = calibrate(Path(a.out), n_panels=a.n_panels, n_perm=a.n_perm)
            else:
                res = calibrate(Path(a.out), provisional_universe(a.design), n_panels=a.n_panels, n_perm=a.n_perm, name=f"calibration_provisional_{a.design}")
            print(json.dumps(res, indent=2))
        elif a.cmd == "diagnostics":
            res = financing_diagnostics(provisional_universe(a.design))
            OUT_DEV.mkdir(parents=True, exist_ok=True)
            (OUT_DEV / f"financing_diagnostics_provisional_{a.design}.json").write_text(json.dumps(res, indent=2))
            print(json.dumps(res, indent=2))
        else:
            print(json.dumps(run_final(), indent=2, default=float))
    except (ValueError, PermissionError) as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
