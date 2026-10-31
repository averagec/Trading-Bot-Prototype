"""Slow-horizon (H4 and D1 decision bars) dataset: causal features + stateless net-R labels on an H1 path.

Pre-registration: research/reports/ai_slow_prereg.md. Reuses the label family of labels.py with three changes:
    * the decision timeframe is H4 or D1 (stop = max(10 pips, k x ATR14 of THAT timeframe), target 3R);
    * the barrier path is the H1 bars (M5 does not exist before 2013); a stop and a target inside one H1 bar -> stop;
    * the time barrier is 7 calendar days, which the Friday-20:00 flatten (max hold 4 d 20 h) always pre-empts.
D1 bars follow the broker's NY-close day: H1 bars are grouped by the SERVER date (UTC+2 in winter, UTC+3 while US DST
is in force, research.data.utc_to_server), so a D1 bar runs from the weekly/daily server midnight (21:00 or 22:00 UTC)
to the next; its close time T is the close of its last H1 bar. The week therefore has five full D1 bars (the Friday bar
closes at the weekly close and can never be a decision: decisions at or after Friday 20:00 are invalid).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from research.ai import dataset as ds
from research.ai.features import usd_factor
from research.ai.indicators import atr, true_range
from research.ai.labels import LabelSpec, _friday_flatten_open, decision_rows, label_pair
from research.ai.stats import week_index
from research.data import utc_to_server

FIRST_DECISION = "2003-01-01"           # 2000-2002 are indicator warm-up only
ERAS = (("2003-2009", 2003, 2009), ("2010-2013", 2010, 2013), ("2014-2021", 2014, 2021))
OOF_FIRST = "2006-01-01"                # first test block: at least three years of training before it
SLOW_S = ["ret_a", "ret_b", "ret_c", "ret_d", "ret_e", "dist_hi_s", "dist_lo_s", "dist_hi_l", "dist_lo_l",
          "trend_own", "trend_htf", "usd_f_s", "usd_f_l"]
SLOW_N = ["hour_sin", "hour_cos", "dow_sin", "dow_cos", "hrs_to_flat", "atr_pct", "vol_ratio", "htf_atr_pct",
          "spread_rel", "spread_miss", "stop_atr_ratio", "cost_r"]
_NEGATE = ["ret_a", "ret_b", "ret_c", "ret_d", "ret_e", "trend_own", "trend_htf", "usd_f_s", "usd_f_l"]
_SWAP = [("dist_hi_s", "dist_lo_s"), ("dist_hi_l", "dist_lo_l")]


def mirror_slow(X: pd.DataFrame) -> pd.DataFrame:
    M = X.copy()
    for c in _NEGATE:
        M[c] = -X[c]
    for a, b in _SWAP:
        M[a], M[b] = X[b], X[a]
    return M


@dataclass(frozen=True)
class SlowSpec:
    name: str
    tf: str                     # "H4" or "D1"
    k_atr: float
    ret_lags: tuple             # five return horizons in decision bars
    usd_lags: tuple             # two USD-factor horizons in decision bars
    pct_window: int             # window of the ATR percentile
    pct_min: int
    flatten: bool = True        # False = sensitivity S1 (weekend holds, 14 day barrier)

    @property
    def tf_min(self) -> int:
        return 240 if self.tf == "H4" else 1440

    @property
    def horizon_min(self) -> int:
        return 7 * 1440 if self.flatten else 14 * 1440

    @property
    def embargo_days(self) -> int:
        return 14 if self.flatten else 21

    def label_spec(self) -> LabelSpec:
        return LabelSpec(k_atr=self.k_atr, horizon_min=self.horizon_min,
                         flatten_hour_utc=20.0 if self.flatten else None, spread_col="spread_clean_pts")


def _h4(k, flatten=True):
    return SlowSpec(f"H4_k{int(round(k * 10)):02d}" + ("" if flatten else "_nf"), "H4", k, (1, 6, 18, 42, 126), (6, 30),
                    1000, 250, flatten)


def _d1(k, flatten=True):
    return SlowSpec(f"D1_k{int(round(k * 10)):02d}" + ("" if flatten else "_nf"), "D1", k, (1, 5, 10, 20, 60), (5, 20),
                    500, 120, flatten)


# The confirmatory family: K = 4. k = 1.0 and 1.5 ATR; see the pre-registration for the reasons.
VARIANTS = {s.name: s for s in (_h4(1.0), _h4(1.5), _d1(1.0), _d1(1.5))}
SENSITIVITY = {s.name: s for s in (_h4(1.0, False), _h4(1.5, False), _d1(1.0, False), _d1(1.5, False))}


# ── D1 from H1 ────────────────────────────────────────────────────────────────────────

def build_d1(h1: pd.DataFrame, min_bars: int = 12) -> pd.DataFrame:
    """D1 bars on the broker's NY-close day. Columns: time (open of the first H1 bar, UTC), T (close of the last H1 bar,
    UTC), open, high, low, close, n (H1 bars in the day). Stub days (< min_bars H1 bars) are dropped."""
    srv = utc_to_server(h1["time"])
    day = srv.dt.floor("D").to_numpy()
    g = h1.groupby(day, sort=True)
    d = pd.DataFrame({"time": g["time"].first(), "last_open": g["time"].last(), "open": g["open"].first(),
                      "high": g["high"].max(), "low": g["low"].min(), "close": g["close"].last(),
                      "n": g["time"].size()})
    d["T"] = d["last_open"] + pd.Timedelta(hours=1)
    day_start = pd.Series(g.size().index, index=d.index)
    # complete = the last H1 bar closes at or after the next server midnight (a calendar rule, so a day cut short by the
    # end of the data, or an early holiday close, is never treated as a closed D1 bar)
    end_srv = utc_to_server(d["T"]).reset_index(drop=True)
    complete = (end_srv.values >= (pd.to_datetime(day_start.values) + pd.Timedelta(days=1)).values)
    d = d[(d["n"] >= min_bars).to_numpy() & complete].reset_index(drop=True)
    return d.drop(columns="last_open")


def decision_frame(spec: SlowSpec, h1: pd.DataFrame, h4: pd.DataFrame) -> pd.DataFrame:
    """The decision-timeframe bars with a close-time column T."""
    if spec.tf == "H4":
        b = h4[["time", "open", "high", "low", "close"]].copy()
        b["T"] = b["time"] + pd.Timedelta(hours=4)
        return b.reset_index(drop=True)
    return build_d1(h1)


# ── features ──────────────────────────────────────────────────────────────────────────

def _roll_rank(x, window, min_periods):
    return pd.Series(x).rolling(window, min_periods=min_periods).rank(pct=True).to_numpy()


def _z(close, a_rel, k):
    lc = np.log(close)
    r = np.full(len(lc), np.nan)
    r[k:] = lc[k:] - lc[:-k]
    return r / (a_rel * np.sqrt(k))


def zreturns(base: pd.DataFrame, lags) -> dict:
    c = base["close"].to_numpy(float)
    a = atr(base["high"].to_numpy(), base["low"].to_numpy(), c, 14)
    return {k: _z(c, a / c, k) for k in lags}


def all_zreturns(bases: dict, spec: SlowSpec) -> dict:
    lags = sorted(set(spec.ret_lags) | set(spec.usd_lags))
    return {s: zreturns(b, lags) for s, b in bases.items()}


def slow_features(symbol: str, base: pd.DataFrame, h1: pd.DataFrame, d1: pd.DataFrame, usd: dict, spec: SlowSpec,
                  ls: LabelSpec, specs: dict) -> pd.DataFrame:
    """Feature frame aligned to the rows of `base`. `usd` = {lag: array aligned to base rows} (usd_factor output)."""
    c = base["close"].to_numpy(float)
    hi, lo = base["high"].to_numpy(float), base["low"].to_numpy(float)
    a = atr(hi, lo, c, 14)
    a_rel = a / c
    T = base["T"].values.astype("datetime64[ns]")
    f = pd.DataFrame({"time": base["time"].to_numpy(), "T": base["T"].to_numpy()})
    for name, k in zip(("ret_a", "ret_b", "ret_c", "ret_d", "ret_e"), spec.ret_lags):
        f[name] = _z(c, a_rel, k)
    for tag, w in (("s", 20), ("l", 120)):
        hh = pd.Series(hi).rolling(w, min_periods=w).max().to_numpy()
        ll = pd.Series(lo).rolling(w, min_periods=w).min().to_numpy()
        f[f"dist_hi_{tag}"] = (hh - c) / a
        f[f"dist_lo_{tag}"] = (c - ll) / a
    ema = pd.Series(c).ewm(span=50, adjust=False, min_periods=50).mean().to_numpy()
    f["trend_own"] = (c - ema) / a
    tr = true_range(hi, lo, c)
    tr[0] = hi[0] - lo[0]
    a100 = pd.Series(tr).rolling(100, min_periods=100).mean().to_numpy()
    f["vol_ratio"] = np.log(a / a100)
    f["atr_pct"] = _roll_rank(a_rel, spec.pct_window, spec.pct_min)
    if spec.tf == "H4":                                     # higher-timeframe context: D1 bars closed at T
        cd = d1["close"].to_numpy(float)
        ad = atr(d1["high"].to_numpy(float), d1["low"].to_numpy(float), cd, 14)
        emad = pd.Series(cd).ewm(span=50, adjust=False, min_periods=50).mean().to_numpy()
        tr_d = (cd - emad) / ad
        pct_d = _roll_rank(ad / cd, 250, 60)
        j = np.searchsorted(d1["T"].values.astype("datetime64[ns]"), T, side="right") - 1
        okj = j >= 0
        jj = np.where(okj, j, 0)
        f["trend_htf"] = np.where(okj, tr_d[jj], np.nan)
        f["htf_atr_pct"] = np.where(okj, pct_d[jj], np.nan)
    else:                                                   # D1: slower own-timeframe trend, shorter percentile window
        ema200 = pd.Series(c).ewm(span=200, adjust=False, min_periods=200).mean().to_numpy()
        f["trend_htf"] = (c - ema200) / a
        f["htf_atr_pct"] = _roll_rank(a_rel, 250, 60)
    f["usd_f_s"] = usd[spec.usd_lags[0]]
    f["usd_f_l"] = usd[spec.usd_lags[1]]
    # calendar (hour is a DST proxy for D1, so it is zeroed there)
    T_idx = pd.DatetimeIndex(base["T"])
    hour = T_idx.hour.to_numpy()
    f["hour_sin"] = np.sin(2 * np.pi * hour / 24) if spec.tf == "H4" else 0.0
    f["hour_cos"] = np.cos(2 * np.pi * hour / 24) if spec.tf == "H4" else 0.0
    dow = T_idx.dayofweek.to_numpy()
    f["dow_sin"] = np.sin(2 * np.pi * dow / 5)
    f["dow_cos"] = np.cos(2 * np.pi * dow / 5)
    valid, flat_open = _friday_flatten_open(T, 20.0, 60)
    hrs = (flat_open + np.timedelta64(60, "m") - T) / np.timedelta64(1, "h")
    f["hrs_to_flat"] = np.where(valid, np.minimum(hrs, 120.0), 0.0)
    # spread, causal: recorded values of the decision's last H1 bar relative to the trailing median of H1 spreads
    raw = h1["spread"].astype(float)
    era_ok = (h1["time"] >= pd.Timestamp(ds.SENTINEL_FROM, tz="UTC")) & (symbol != "XAUUSD")
    is50 = ((raw == ds.SENTINEL_PTS) & era_ok).astype(float)
    sent_c = (is50.rolling(240, min_periods=24).mean() >= ds.SENTINEL_MONTH_SHARE) & (is50 > 0)
    rec = raw.where((raw > 0) & ~sent_c)
    med = rec.rolling(240, min_periods=24).median()
    rel = np.log(rec / med).fillna(0.0).to_numpy()
    miss = rec.isna().astype(float).to_numpy()
    rec_f = rec.fillna(med).to_numpy(float)
    erow = decision_rows(base, h1["time"].values.astype("datetime64[ns]"), spec.tf_min, 60)
    ok = erow >= 0
    er = np.where(ok, erow, 0)
    spec_sym = specs[symbol]
    pip = ds.pip_size(spec_sym)
    stop = np.maximum(ls.floor_pips * pip, ls.k_atr * a)
    f["spread_rel"] = np.where(ok, rel[er], np.nan)
    f["spread_miss"] = np.where(ok, miss[er], np.nan)
    f["stop_atr_ratio"] = stop / a
    comm_price = ds.commission_pips(symbol, spec_sym, c, ls.comm_per_lot_side) * pip
    f["cost_r"] = np.where(ok, (rec_f[er] * spec_sym["point"] + comm_price) / stop, np.nan)
    return f


def era_of(T: pd.Series) -> np.ndarray:
    y = pd.to_datetime(T, utc=True).dt.year.to_numpy()
    out = np.full(len(y), "", dtype=object)
    for name, a, b in ERAS:
        out[(y >= a) & (y <= b)] = name
    return out


def build_from_frames(spec: SlowSpec, h1s: dict, h4s: dict, specs: dict, first_decision: str = FIRST_DECISION) -> pd.DataFrame:
    """One row per (pair, decision bar): features, labels (both sides), weights, week, year, era. Frames carry the
    columns of dataset.load_bars (time = bar OPEN, UTC, plus spread / spread_clean_pts)."""
    symbols = list(h1s)
    ls = spec.label_spec()
    d1s = {s: build_d1(h1s[s]) for s in symbols}
    bases = {s: decision_frame(spec, h1s[s], h4s[s]) for s in symbols}
    zr = all_zreturns(bases, spec)
    times = {s: bases[s]["T"].values for s in symbols}
    usd = {k: usd_factor(zr, times, k) for k in spec.usd_lags}
    parts = []
    for s in symbols:
        base = bases[s]
        feat = slow_features(s, base, h1s[s], d1s[s], {k: usd[k][s] for k in spec.usd_lags}, spec, ls, specs)
        lab = label_pair(s, base, h1s[s], specs, ls, tf_min=spec.tf_min, bar_min=60)
        d = pd.concat([lab.reset_index(drop=True), feat.drop(columns=["time", "T"]).reset_index(drop=True)], axis=1)
        d["close"] = base["close"].to_numpy()
        d["pip"] = ds.pip_size(specs[s])
        d.insert(0, "symbol", s)
        parts.append(d[d["time"] >= pd.Timestamp(first_decision, tz="UTC")])
    df = pd.concat(parts, ignore_index=True).sort_values(["T", "symbol"]).reset_index(drop=True)
    df["week"] = week_index(df["T"])
    df["year"] = df["T"].dt.year
    df["era"] = era_of(df["T"])
    return df


def build_variant(spec: SlowSpec, symbols=ds.FX5, end: str = ds.DEV_END, first_decision: str = FIRST_DECISION,
                  data_dir=None) -> pd.DataFrame:
    """Load the DEV frames (the loader refuses any end after 2021-06-01) and build the variant dataset."""
    kw = {} if data_dir is None else {"data_dir": data_dir}
    specs = ds.load_specs() if data_dir is None else ds.load_specs(data_dir)
    h1s = {s: ds.load_bars(s, "H1", end=end, **kw) for s in symbols}
    h4s = {s: ds.load_bars(s, "H4", end=end, **kw) for s in symbols}
    return build_from_frames(spec, h1s, h4s, specs, first_decision)
