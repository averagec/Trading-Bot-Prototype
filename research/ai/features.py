"""Causal feature set for H1 decision bars (OHLC + recorded spread only).

Decision at time T = close of H1 bar i. Every feature at row i is a function of H1 rows <= i, of H4 bars with
open + 4h <= T, and of the other pairs' H1 rows with the same property. `tests/test_ai_features.py` checks this by
truncation (cut the data at a time; features before the cut are identical) and by scrambling bars that had not closed.

Feature groups (the split is the heart of the evaluation, see ai_edge_approach.md):
    N  non-directional ("state"): identical for a long and a short at the same bar. Time of day / week, hours to the
       Friday flatten, volatility level and percentile, H4 volatility percentile, spread level relative to its own
       recent past, and the label-geometry terms (stop in ATR units, first-order cost over stop).
    S  directional: odd under mirroring (returns, H4 trend, USD factor) or swapped under mirroring (distance to the
       rolling high / low). `mirror(X)` maps the features of a long into the features of the equivalent short.
Nothing here uses the live SMC signals.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from research.ai import dataset as ds
from research.ai.indicators import atr, true_range
from research.ai.labels import LabelSpec, _friday_flatten_open

RET_K = (1, 4, 12, 24, 120)
S_FEATURES = ["ret1", "ret4", "ret12", "ret24", "ret120", "dist_hi20", "dist_lo20", "dist_hi120", "dist_lo120",
              "h4_trend", "h4_ret6", "usd_f4", "usd_f24"]
N_FEATURES = ["hour_sin", "hour_cos", "dow_sin", "dow_cos", "hrs_to_flat", "atr_pct", "vol_ratio", "h4_atr_pct",
              "spread_rel", "spread_miss", "stop_atr_ratio", "cost_r"]
# columns negated by mirror(); the hi/lo pairs are swapped
_NEGATE = ["ret1", "ret4", "ret12", "ret24", "ret120", "h4_trend", "h4_ret6", "usd_f4", "usd_f24"]
_SWAP = [("dist_hi20", "dist_lo20"), ("dist_hi120", "dist_lo120")]
WARMUP_ROWS = 1000          # longest trailing window (percentile ranks); earlier decision rows are dropped by callers


def mirror(X: pd.DataFrame) -> pd.DataFrame:
    """Features of the short that mirrors each long row (price path reflected)."""
    M = X.copy()
    for c in _NEGATE:
        M[c] = -X[c]
    for a, b in _SWAP:
        M[a], M[b] = X[b], X[a]
    return M


def _zret(close: np.ndarray, a_rel: np.ndarray, k: int) -> np.ndarray:
    lc = np.log(close)
    r = np.full(len(lc), np.nan)
    r[k:] = lc[k:] - lc[:-k]
    return r / (a_rel * np.sqrt(k))


def pair_zreturns(h1: pd.DataFrame) -> dict:
    """z-scored H1 returns (log return / (ATR14/close x sqrt(k))) for k in RET_K, as arrays aligned to h1 rows."""
    c = h1["close"].to_numpy(float)
    a = atr(h1["high"].to_numpy(), h1["low"].to_numpy(), c, 14)
    a_rel = a / c
    return {k: _zret(c, a_rel, k) for k in RET_K}


def usd_factor(zr: dict, times: dict, k: int) -> dict:
    """For each pair p: sign_p x mean_{q != p}(sign_q x z_k,q) on a common hourly index (needs >= 2 other pairs)."""
    idx = None
    for s, t in times.items():
        idx = pd.DatetimeIndex(t) if idx is None else idx.union(pd.DatetimeIndex(t))
    frame = pd.DataFrame({s: pd.Series(zr[s][k] * ds.USD_SIGN[s], index=pd.DatetimeIndex(times[s])).reindex(idx)
                          for s in times})
    out = {}
    tot = frame.sum(axis=1, skipna=True)
    cnt = frame.notna().sum(axis=1)
    for s in times:
        own = frame[s].fillna(0.0)
        n_oth = cnt - frame[s].notna().astype(int)
        f = (tot - own) / n_oth.where(n_oth >= 2)
        out[s] = (ds.USD_SIGN[s] * f).reindex(pd.DatetimeIndex(times[s])).to_numpy()
    return out


def _roll_rank(x: np.ndarray, window: int, min_periods: int) -> np.ndarray:
    return pd.Series(x).rolling(window, min_periods=min_periods).rank(pct=True).to_numpy()


def pair_features(symbol: str, h1: pd.DataFrame, h4: pd.DataFrame, usd_f: dict, ls: LabelSpec = LabelSpec(),
                  specs: dict | None = None) -> pd.DataFrame:
    """Feature frame aligned to h1 rows (time = bar open). `usd_f` = {4: array, 24: array} from usd_factor()."""
    c = h1["close"].to_numpy(float)
    hi, lo = h1["high"].to_numpy(float), h1["low"].to_numpy(float)
    a = atr(hi, lo, c, 14)
    a_rel = a / c
    f = pd.DataFrame({"time": h1["time"].to_numpy()})
    z = pair_zreturns(h1)
    for k in RET_K:
        f[f"ret{k}"] = z[k]
    for w in (20, 120):
        hh = pd.Series(hi).rolling(w, min_periods=w).max().to_numpy()
        ll = pd.Series(lo).rolling(w, min_periods=w).min().to_numpy()
        f[f"dist_hi{w}"] = (hh - c) / a
        f[f"dist_lo{w}"] = (c - ll) / a
    # volatility state
    tr = true_range(hi, lo, c)
    tr[0] = hi[0] - lo[0]
    a100 = pd.Series(tr).rolling(100, min_periods=100).mean().to_numpy()
    f["vol_ratio"] = np.log(a / a100)
    f["atr_pct"] = _roll_rank(a_rel, WARMUP_ROWS, 250)
    # H4 state, only from H4 bars closed at T
    T = h1["time"].values.astype("datetime64[ns]") + np.timedelta64(60, "m")
    c4 = h4["close"].to_numpy(float)
    a4 = atr(h4["high"].to_numpy(float), h4["low"].to_numpy(float), c4, 14)
    a4_rel = a4 / c4
    ema = pd.Series(c4).ewm(span=50, adjust=False, min_periods=50).mean().to_numpy()
    h4_trend = (c4 - ema) / a4
    h4_ret6 = _zret(c4, a4_rel, 6)
    h4_pct = _roll_rank(a4_rel, 250, 60)
    t4_close = h4["time"].values.astype("datetime64[ns]") + np.timedelta64(240, "m")
    i4 = np.searchsorted(t4_close, T, side="right") - 1
    ok4 = i4 >= 0
    i4c = np.where(ok4, i4, 0)
    f["h4_trend"] = np.where(ok4, h4_trend[i4c], np.nan)
    f["h4_ret6"] = np.where(ok4, h4_ret6[i4c], np.nan)
    f["h4_atr_pct"] = np.where(ok4, h4_pct[i4c], np.nan)
    f["usd_f4"] = usd_f[4]
    f["usd_f24"] = usd_f[24]
    # calendar
    hour = (pd.DatetimeIndex(h1["time"]).hour + 1).to_numpy() % 24          # hour of T
    dow = pd.DatetimeIndex(h1["time"] + pd.Timedelta(hours=1)).dayofweek.to_numpy()
    f["hour_sin"] = np.sin(2 * np.pi * hour / 24)
    f["hour_cos"] = np.cos(2 * np.pi * hour / 24)
    f["dow_sin"] = np.sin(2 * np.pi * dow / 5)
    f["dow_cos"] = np.cos(2 * np.pi * dow / 5)
    valid, flat_open = _friday_flatten_open(T, ls.flatten_hour_utc)
    hrs = (flat_open + np.timedelta64(5, "m") - T) / np.timedelta64(1, "h")
    f["hrs_to_flat"] = np.where(valid, np.minimum(hrs, 48.0), 0.0)
    # spread level relative to its own trailing past (recorded values only; zeros and the 50-point sentinel are NaN)
    raw = h1["spread"].astype(float)
    is50 = ((raw == ds.SENTINEL_PTS) & (symbol != "XAUUSD")).astype(float)
    sentinel_c = (is50.rolling(240, min_periods=24).mean() >= ds.SENTINEL_MONTH_SHARE) & (is50 > 0)   # trailing, causal
    rec = raw.where((raw > 0) & ~sentinel_c)
    med = rec.rolling(240, min_periods=24).median()
    rel = np.log(rec / med)
    f["spread_miss"] = rec.isna().astype(float).to_numpy()
    f["spread_rel"] = rel.fillna(0.0).to_numpy()
    # label geometry known at T
    spec = (specs or ds.load_specs())[symbol]
    pip = ds.pip_size(spec)
    stop = np.maximum(ls.floor_pips * pip, ls.k_atr * a)
    f["stop_atr_ratio"] = stop / a
    sp = rec.fillna(med).to_numpy(float) * spec["point"]           # causal spread level used for the cost term
    f["cost_r"] = (sp + ds.commission_pips(symbol, spec, c, ls.comm_per_lot_side) * pip) / stop
    return f


def all_pair_features(h1s: dict, h4s: dict, ls: LabelSpec = LabelSpec(), specs: dict | None = None) -> dict:
    """{symbol: feature frame aligned to that symbol's H1 rows}; the USD factor pools the other pairs' H1 z-returns."""
    specs = specs or ds.load_specs()
    zr = {s: pair_zreturns(h1s[s]) for s in h1s}
    times = {s: h1s[s]["time"].values for s in h1s}
    usd = {k: usd_factor(zr, times, k) for k in (4, 24)}
    return {s: pair_features(s, h1s[s], h4s[s], {4: usd[4][s], 24: usd[24][s]}, ls, specs) for s in h1s}


def forward_stats(h1: pd.DataFrame, pip: float, hs=(1, 4, 24)) -> pd.DataFrame:
    """OUTCOME columns for the cheap-label feasibility study (never features): forward close-to-close move and forward
    high-low range, in pips, over the next h H1 bars from the decision close (decision i: close[i] -> close[i+h]; the
    range spans bars i+1 .. i+h). NaN if bars i..i+h are not contiguous (weekend / holes). These look forward on
    purpose; keep them out of any feature list."""
    t = h1["time"].values.astype("datetime64[ns]")
    c = h1["close"].to_numpy(float)
    hi, lo = h1["high"].to_numpy(float), h1["low"].to_numpy(float)
    n = len(c)
    out = {}
    for h in hs:
        fwd = np.full(n, np.nan)
        rng = np.full(n, np.nan)
        if n > h + 1:
            ok = np.zeros(n, bool)
            ok[: n - h] = (t[h:] - t[: n - h]) == np.timedelta64(h * 60, "m")
            fwd[: n - h] = c[h:] - c[: n - h]
            hmax = pd.Series(hi).rolling(h, min_periods=h).max().to_numpy()          # max over bars j-h+1 .. j
            lmin = pd.Series(lo).rolling(h, min_periods=h).min().to_numpy()
            rng[: n - h] = hmax[h:] - lmin[h:]                                       # j = i+h  -> bars i+1 .. i+h
            fwd[~ok] = np.nan
            rng[~ok] = np.nan
        out[f"fwd{h}_pips"] = fwd / pip
        out[f"rng{h}_pips"] = rng / pip
    return pd.DataFrame(out)
