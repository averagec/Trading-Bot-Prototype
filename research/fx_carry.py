"""FX carry (and carry with 12-1 momentum) on G10 currencies, monthly, DEV only. Study 2 of 2 of the new-information round.

Pre-registration: research/reports/carry_prereg.md (read it first; every constant below is fixed there). Data notes:
research/reports/carry_data_notes.md. DEV ONLY: every loader refuses an end after 2021-06-01 (file-level filter for prices,
row filter for rates), and every window refuses a start on or after 2021-06-01. There is no FINAL runner in this file: the
FINAL spec is written in the pre-registration and `require_final_go` is only the guard that a later runner must call.
No MetaTrader5 import, no live-trading code, no order path. Nothing here is run on real returns until the owner runs `dev`.

    python -m research.fx_carry power        # analytic power table (no data)
    python -m research.fx_carry calibrate    # gate calibration on planted-carry synthetic panels (real RATES as inputs, no project returns)
    python -m research.fx_carry freeze       # write the freeze manifest: sha256 of the pre-registration, this file, carry_screens.py, the nine pair parquets and the ten rate CSVs
    python -m research.fx_carry dev          # the one pre-registered DEV run; refuses unless every hashed file matches the freeze, and refuses to overwrite an existing summary.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from statistics import NormalDist

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data_multi"
RATES_DIR = DATA_DIR / "rates"
OUT_DEV = ROOT / "output" / "fx_carry_dev"
FINAL_DIR = ROOT / "output" / "fx_carry"
FINAL_GO = FINAL_DIR / "FINAL_GO"
CANDIDATE = OUT_DEV / "frozen_candidate.json"
PREREG = ROOT / "reports" / "carry_prereg.md"
FROZEN_SHA = ROOT / "reports" / "carry_prereg.sha256"

# ── fixed constants (pre-registration sections 3 to 7) ───────────────────────────────────
DEV_START = "2003-01-01"
DEV_END = "2021-06-01"
FINAL_START, FINAL_END = "2021-06-01", "2025-05-01"       # FINAL_END exclusive: through 2025-04-30 (not used in this file)
FOREIGN = ["EUR", "GBP", "JPY", "AUD", "CAD", "CHF", "NZD", "NOK", "SEK"]
ALL10 = ["USD"] + FOREIGN                                   # index 0 = USD (the numeraire; its position is the negative sum)
PAIR_OF = {"EUR": ("EURUSD", False), "GBP": ("GBPUSD", False), "JPY": ("USDJPY", True), "AUD": ("AUDUSD", False),
           "CAD": ("USDCAD", True), "CHF": ("USDCHF", True), "NZD": ("NZDUSD", False), "NOK": ("USDNOK", True), "SEK": ("USDSEK", True)}
COUNTRY = {"USD": "USA", "EUR": "EUR", "GBP": "GBR", "JPY": "JPN", "AUD": "AUS", "CAD": "CAN", "CHF": "CHE", "NZD": "NZL", "NOK": "NOR", "SEK": "SWE"}
BASIS = {"USD": 360, "EUR": 360, "GBP": 365, "JPY": 360, "AUD": 365, "CAD": 365, "CHF": 360, "NZD": 365, "NOK": 360, "SEK": 360}
EXOTIC = ["NOK", "SEK"]
LAG_MONTHS = 1                      # the decision at the end of calendar month m uses the rate average of month m-1
MAX_STALE = 3                       # a missing rate month is carried forward at most this many months
VOL_WIN, VOL_MIN, TARGET, CAP, ANN_DAYS = 120, 80, 0.10, 4.0, 260
MOM_L, MOM_SKIP = 252, 21
K_FRAC, MIN_N = 0.30, 6
COMM = 0.25e-4                      # 0.25 bp per unit of weight traded
FLOOR_BP = {"major": 1.0, "scand": 4.0}                     # floor of the FULL spread in bp of notional (assumption; section 5)
SPREAD_MED_WIN = 20
STALE_DAYS = 5
MARKUP = {"S0": 0.0, "S1": 0.015, "S2": 0.025}              # broker mark-up per year on gross notional (assumption; S1 raised from 0.010 by pre-run revision 1)
# Descriptive stress only: per-currency mark-up implied by today's swap snapshot, percent of notional per year at the 2021-05-31 price
# (research.carry_screens specs, supplied by the lead on 2026-11-03). Not history; cannot change eligibility.
MARKUP_SNAP = {"EUR": 1.43, "GBP": 1.29, "JPY": 2.71, "AUD": 2.17, "CAD": 1.80, "CHF": 2.35, "NZD": 1.19, "NOK": 0.96, "SEK": 1.60}
BLOCK = 12                          # months per permutation / sign block
N_PERM, SEED_PERM, SEED_FLIP, SEED_FULL = 5000, 20261104, 20261105, 20261106
N_BOOT, SEED_BOOT, BOOT_BLOCKS = 5000, 20261103, (3, 6, 12)
ALPHA = 0.05
ERAS = (("2003-2009", 2003, 2009), ("2010-2013", 2010, 2013), ("2014-2021", 2014, 2021))
G3B_MIN_YEARS, G5_MIN_SR = 13, 0.30
OFF_MONTHS: dict = {}               # ccy -> list of "YYYY-MM" decision months where that currency is not live (set by a pre-run note, data screen only)

VARIANTS = {"V1": "carry_rank", "V2": "carry_cont", "V3": "combo_rank"}      # the Holm family, K = 3
CONTROL = {"M": "mom_rank"}                                                  # control, outside the family, never a candidate
ALL_KINDS = {**VARIANTS, **CONTROL}


# ── window guards ────────────────────────────────────────────────────────────────────────

def _ts(x) -> pd.Timestamp:
    t = pd.Timestamp(x)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def check_end(end) -> pd.Timestamp:
    """UTC timestamp of `end`; refuse a missing end and any end after 2021-06-01 (FINAL and later must not be read)."""
    if end is None:
        raise ValueError("an explicit end <= 2021-06-01 is required (FINAL must not be read)")
    e = _ts(end)
    if e > _ts(DEV_END):
        raise ValueError(f"end {e.date()} is after {DEV_END}: FINAL and later data are off limits")
    return e


def check_dev_window(start, end) -> tuple[pd.Timestamp, pd.Timestamp]:
    e = check_end(end)
    s = _ts(start)
    if s >= _ts(DEV_END):
        raise ValueError(f"start {s.date()} is on or after {DEV_END}: that is the FINAL window")
    if s >= e:
        raise ValueError("start must be before end")
    if s.day != 1:
        raise ValueError("start must be the first day of a month")
    return s, e


def sha256_file(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def frozen_names() -> list[str]:
    """Files whose bytes are part of the freeze, relative to research/: this module, the screen module, the nine pair parquets and the ten rate CSVs.
    (Hashing the parquet bytes reads no value; the files also contain post-DEV bars, which the loaders never bring into memory.)"""
    return (["fx_carry.py", "carry_screens.py"] + [f"data_multi/{PAIR_OF[c][0]}_D1.parquet" for c in FOREIGN]
            + [f"data_multi/rates/{COUNTRY[c]}.csv" for c in ALL10])


def freeze(prereg: Path = PREREG, frozen: Path = FROZEN_SHA, root: Path = ROOT, names: list | None = None) -> str:
    """Write the freeze manifest: first line the pre-registration hash, then one `sha256  relative/path` line per frozen file. Returns the prereg hash."""
    names = frozen_names() if names is None else list(names)
    h = sha256_file(prereg)
    lines = [f"{h}  PREREG"] + [f"{sha256_file(Path(root) / n)}  {n}" for n in names]
    Path(frozen).write_text("\n".join(lines) + "\n")
    return h


def check_prereg_frozen(prereg: Path = PREREG, frozen: Path = FROZEN_SHA, root: Path = ROOT, names: list | None = None) -> str:
    """The DEV run needs the freeze manifest and every hashed file (pre-registration, code, data) unchanged since the freeze; the manifest must list
    exactly the expected files. A revision must be logged in the pre-registration and re-frozen."""
    names = frozen_names() if names is None else list(names)
    if not Path(prereg).exists():
        raise PermissionError(f"pre-registration missing: {prereg}")
    if not Path(frozen).exists():
        raise PermissionError("the pre-registration has not been frozen (run `python -m research.fx_carry freeze` once it is final)")
    rows = [ln.split(None, 1) for ln in Path(frozen).read_text().splitlines() if ln.strip()]
    if not rows or rows[0][1].strip() != "PREREG":
        raise PermissionError("freeze file malformed: first line must be the pre-registration hash")
    listed = {r[1].strip(): r[0] for r in rows[1:]}
    if set(listed) != set(names):
        raise PermissionError(f"freeze manifest lists the wrong files: missing {sorted(set(names) - set(listed))}, unexpected {sorted(set(listed) - set(names))}")
    h = sha256_file(prereg)
    if rows[0][0] != h:
        raise PermissionError("the pre-registration changed after the freeze: log the revision in its Log section and re-freeze")
    bad = []
    for n in names:
        f = Path(root) / n
        if not f.exists() or sha256_file(f) != listed[n]:
            bad.append(n)
    if bad:
        raise PermissionError(f"frozen file(s) changed or missing since the freeze: {bad}; log the revision and re-freeze")
    return h


def require_final_go(go_path: Path | None = None, cand_path: Path | None = None) -> str:
    """Guard a FINAL runner must call (none exists yet): the owner-created FINAL_GO must carry `candidate_sha256=<hash of the candidate file>`."""
    go, cand = Path(go_path or FINAL_GO), Path(cand_path or CANDIDATE)
    if not go.exists():
        raise PermissionError(f"FINAL mode is disabled: {go} does not exist (only the owner creates it)")
    if not cand.exists():
        raise PermissionError(f"no frozen candidate at {cand}: there is nothing to run on FINAL")
    want = sha256_file(cand)
    if f"candidate_sha256={want}" not in [ln.strip() for ln in go.read_text(encoding="utf-8").splitlines()]:
        raise PermissionError("FINAL_GO does not contain the sha256 of the frozen candidate file")
    return want


# ── data: rates ──────────────────────────────────────────────────────────────────────────

def load_rates(end=DEV_END, data_dir=None) -> pd.DataFrame:
    """Monthly-average 3-month interbank rates (percent p.a.), index = month start (naive), columns = ALL10 currencies.
    Months whose start is on or after `end` are never kept (end is refused beyond 2021-06-01). A '.' or blank is NaN."""
    e = check_end(end).tz_localize(None)
    d = Path(data_dir) if data_dir else RATES_DIR
    cols = {}
    for c in ALL10:
        df = pd.read_csv(d / f"{COUNTRY[c]}.csv")
        df.columns = ["date", "v"]
        df["date"] = pd.to_datetime(df["date"])
        df["v"] = pd.to_numeric(df["v"], errors="coerce")
        df = df[df["date"] < e]
        cols[c] = df.set_index("date")["v"]
    out = pd.DataFrame(cols).sort_index()
    return out


def rates_ffilled(rates: pd.DataFrame) -> pd.DataFrame:
    """Complete monthly index, a missing month carried forward at most MAX_STALE months (causal)."""
    full = pd.date_range(rates.index.min(), rates.index.max(), freq="MS")
    return rates.reindex(full).ffill(limit=MAX_STALE)


# ── data: prices ─────────────────────────────────────────────────────────────────────────

@dataclass
class Pair:
    ccy: str
    sym: str
    usd_base: bool
    date: np.ndarray        # datetime64[D], broker-server day of each D1 bar (weekend bars removed)
    openX: np.ndarray       # value of 1 unit of the currency in USD at the bar open
    closeX: np.ndarray      # ... at the bar close
    hs: np.ndarray          # full spread as a fraction of notional, trailing-median of clean recorded values, floored (known before the bar)
    counts: dict = field(default_factory=dict)


def floor_frac(ccy: str) -> float:
    return FLOOR_BP["scand" if ccy in EXOTIC else "major"] * 1e-4


def make_pair(ccy: str, sym: str, usd_base: bool, date, open_p, close_p, spread_pts=None, point=1e-5, counts=None) -> Pair:
    """Build a Pair from raw quote arrays. Quote P is USD per unit for USD-quoted symbols and units per USD for USD-base symbols."""
    date = np.asarray(date, "datetime64[D]")
    o, c = np.asarray(open_p, float), np.asarray(close_p, float)
    ox, cx = (1.0 / o, 1.0 / c) if usd_base else (o, c)
    fl = floor_frac(ccy)
    if spread_pts is None:
        hs = np.full(len(date), fl)
    else:
        sp = pd.Series(np.asarray(spread_pts, float))
        med = sp.shift(1).rolling(SPREAD_MED_WIN, min_periods=5).median().to_numpy()
        frac = med * point / o
        hs = np.where(np.isfinite(frac), np.maximum(frac, fl), fl)
    return Pair(ccy, sym, usd_base, date, ox, cx, hs, counts or {})


def load_pair(ccy: str, end=DEV_END, data_dir=None) -> Pair:
    """One G10-vs-USD D1 file through `end` (refused beyond 2021-06-01; the parquet read is filtered at file level, so
    later bars are never brought into memory). Weekend bars are dropped; zero and 50-point-placeholder spreads are
    replaced by the clean same-year median (research.ai.dataset.add_clean_spread); the cost spread is the trailing median."""
    from research.ai import dataset as ds
    e = check_end(end)
    d = Path(data_dir) if data_dir else DATA_DIR
    sym, usd_base = PAIR_OF[ccy]
    df = pd.read_parquet(d / f"{sym}_D1.parquet", filters=[("time", "<", e)])
    df = df.sort_values("time").drop_duplicates("time").reset_index(drop=True)
    wk = int((df["time"].dt.dayofweek >= 5).sum())
    df = df[df["time"].dt.dayofweek < 5].reset_index(drop=True)
    df = ds.add_clean_spread(df, sym)
    spec = json.loads((d / f"{sym}_spec.json").read_text())
    dates = df["time"].dt.tz_localize(None).dt.normalize().to_numpy(dtype="datetime64[D]")
    return make_pair(ccy, sym, usd_base, dates, df["open"], df["close"], df["spread_clean_pts"], spec["point"],
                     {"weekend_bars_dropped": wk, "bars": int(len(df))})


def load_dev_world(end=DEV_END, data_dir=None, rates_dir=None):
    """All nine USD pairs and the ten rate series, DEV only. A missing pair file refuses the run (no silent subset)."""
    d = Path(data_dir) if data_dir else DATA_DIR
    missing = [PAIR_OF[c][0] for c in FOREIGN if not (d / f"{PAIR_OF[c][0]}_D1.parquet").exists()]
    if missing:
        raise FileNotFoundError(f"D1 files missing for {missing}: the universe is fixed at the nine USD pairs; no substitution")
    return {c: load_pair(c, end, d) for c in FOREIGN}, load_rates(end, rates_dir)


# ── calendar, scores, panel ──────────────────────────────────────────────────────────────

def decision_dates(start, end) -> np.ndarray:
    """Last weekday of every calendar month whose following exec date lies in [start, end): the month before `start`'s month
    through the month containing end - 1 day. A calendar rule: known in advance, needs no data."""
    s, e = check_dev_window(start, end)
    s0, e0 = s.tz_localize(None), e.tz_localize(None)
    m0 = (s0 - pd.offsets.MonthBegin(1)).replace(day=1)
    m1 = (e0 - pd.Timedelta(days=1)).replace(day=1)
    out = [(m + pd.offsets.BMonthEnd(0)) for m in pd.date_range(m0, m1, freq="MS")]
    return np.array([x.to_datetime64() for x in out], dtype="datetime64[D]")


def k_of(n: int) -> int:
    return int(math.floor(K_FRAC * n + 0.5))


@dataclass
class Panel:
    dec: np.ndarray             # (K,) decision dates
    ED: np.ndarray              # (K,) nominal execution date = next business day after the decision
    live: np.ndarray            # (K,10) bool; USD always True
    carry: np.ndarray           # (K,10) lagged rate (percent), NaN where unavailable
    mom: np.ndarray             # (K,10) trailing 12-1 log return vs USD (USD = 0), NaN where unavailable
    Sig: np.ndarray             # (K,9,9) annualised covariance of the nine currency-vs-USD returns at the decision
    ret: np.ndarray             # (Kp,9) spot return of each currency vs USD over period j (exec j to exec j+1)
    acc: np.ndarray             # (Kp,9) interest accrual of holding currency c vs USD over the period, as a fraction of the position
    hs: np.ndarray              # (Kp,9) full spread fraction at execution
    days: np.ndarray            # (Kp,) calendar days of the period
    counts: dict = field(default_factory=dict)

    @property
    def K(self) -> int:
        return len(self.dec)

    @property
    def Kp(self) -> int:
        return len(self.dec) - 1

    @property
    def year(self) -> np.ndarray:
        return self.ED[: self.Kp].astype("datetime64[Y]").astype(int) + 1970


def build_panel(pairs: dict, rates: pd.DataFrame, start=DEV_START, end=DEV_END, lag: int = LAG_MONTHS, off_months: dict | None = None) -> Panel:
    """Everything that does not depend on the weights. Decisions use bars with date <= the decision date and rate months <= m - lag only;
    execution is the open of the first bar after the decision; the period ends at the next execution (the last one at the close of the last bar)."""
    if lag < 1:
        raise ValueError("the rate lag must be at least one month")
    s, e = check_dev_window(start, end)
    dec = decision_dates(start, end)
    K = len(dec)
    ED = (pd.DatetimeIndex(dec) + pd.offsets.BDay(1)).to_numpy(dtype="datetime64[D]")
    off_months = OFF_MONTHS if off_months is None else off_months
    dec_ym = pd.DatetimeIndex(dec).strftime("%Y-%m").to_numpy()
    live = np.zeros((K, 10), bool)
    live[:, 0] = True
    mom = np.full((K, 10), np.nan)
    mom[:, 0] = 0.0
    ret = np.zeros((K, 9))
    hs = np.zeros((K, 9))
    cnt = {"stale_pair": 0, "no_exec_bar": 0, "off_month": 0, "rate_unavailable": 0, "cov_unavailable": 0, "non_finite_return": 0}
    # carry score: rate of month (m - lag), carried forward at most MAX_STALE months
    rf = rates_ffilled(rates)
    mi = (dec.astype("datetime64[M]") - np.timedelta64(lag, "M")).astype("datetime64[ns]")
    pos = rf.index.get_indexer(pd.DatetimeIndex(mi))
    carry = np.where((pos >= 0)[:, None], rf.to_numpy(float)[np.clip(pos, 0, None)][:, [list(rf.columns).index(c) for c in ALL10]], np.nan)
    # covariance of daily log returns (rolling window ends at the decision bar)
    lx = pd.concat({c: pd.Series(np.log(pairs[c].closeX), index=pd.DatetimeIndex(pairs[c].date)) for c in FOREIGN}, axis=1)
    lx = lx.sort_index()
    lr = lx.diff()
    covarr = lr.rolling(VOL_WIN, min_periods=VOL_MIN).cov().to_numpy(float).reshape(len(lr), 9, 9) * ANN_DAYS
    idx_cov = np.searchsorted(lr.index.to_numpy("datetime64[D]"), dec, side="right") - 1
    Sig = np.where((idx_cov >= 0)[:, None, None], covarr[np.clip(idx_cov, 0, None)], np.nan)
    cov_ok = np.isfinite(np.einsum("kcc->kc", Sig))
    for j, c in enumerate(FOREIGN):
        p = pairs[c]
        n = len(p.date)
        idd = np.searchsorted(p.date, dec, side="right") - 1
        has = idd >= 0
        idc = np.clip(idd, 0, None)
        fresh = has & ((dec - p.date[idc]) <= np.timedelta64(STALE_DAYS, "D"))
        cnt["stale_pair"] += int((~fresh).sum())
        ie = np.searchsorted(p.date, dec, side="right")            # first bar strictly after the decision date
        okx = ie < n
        iec = np.clip(ie, 0, n - 1)
        o_ex = np.where(okx, p.openX[iec], np.nan)
        hs[:, j] = np.where(okx, p.hs[iec], floor_frac(c))
        exit_px = np.empty(K)
        exit_px[:-1] = o_ex[1:]
        exit_px[-1] = np.nan
        last_close = p.closeX[-1]
        exit_px[:-1] = np.where(np.isfinite(exit_px[:-1]), exit_px[:-1], last_close)    # only the end of data lacks a next open
        cnt["no_exec_bar"] += int((~okx[:-1]).sum())
        with np.errstate(invalid="ignore", divide="ignore"):
            r = exit_px / o_ex - 1.0
        r[-1] = 0.0
        bad = ~np.isfinite(r)
        cnt["non_finite_return"] += int(bad[:-1].sum())
        ret[:, j] = np.where(bad, 0.0, r)
        # momentum: log return from L bars ago to SKIP bars ago, in the pair's own bar count
        ok = has & (idc - MOM_L >= 0)
        a, b = np.where(ok, idc - MOM_SKIP, 0), np.where(ok, idc - MOM_L, 0)
        with np.errstate(invalid="ignore", divide="ignore"):
            mom[:, j + 1] = np.where(ok, np.log(p.closeX[a]) - np.log(p.closeX[b]), np.nan)
        off = np.isin(dec_ym, list(off_months.get(c, [])))
        cnt["off_month"] += int(off.sum())
        rate_ok = np.isfinite(carry[:, j + 1]) & np.isfinite(carry[:, 0])
        cnt["rate_unavailable"] += int((~rate_ok).sum())
        cnt["cov_unavailable"] += int((~cov_ok[:, j]).sum())
        live[:, j + 1] = fresh & rate_ok & cov_ok[:, j] & np.isfinite(mom[:, j + 1]) & ~off      # nothing here looks at the future
    live[:, 0] &= np.isfinite(carry[:, 0])
    # accrual: every calendar day in [ED_j, ED_(j+1)) earns the (unlagged) monthly rate of its own month, day-count by currency
    cal = pd.date_range(pd.Timestamp(ED[0]), pd.Timestamp(ED[-1]) - pd.Timedelta(days=1), freq="D")
    mday = cal.to_period("M").to_timestamp()
    rfa = rf.reindex(pd.date_range(rf.index.min(), max(rf.index.max(), mday.max()), freq="MS")).ffill(limit=MAX_STALE)
    rd = rfa.reindex(mday)[ALL10].to_numpy(float)
    if np.isnan(rd).any():
        raise ValueError("a rate month needed for the interest accrual is missing beyond the carry-forward limit")
    daily = rd / 100.0 / np.array([BASIS[c] for c in ALL10])[None, :]
    diff = daily[:, 1:] - daily[:, [0]]
    cum = np.vstack([np.zeros((1, 9)), np.cumsum(diff, axis=0)])
    cal_i = lambda d: (d - ED[0]).astype(int)                                       # noqa: E731
    acc = cum[cal_i(ED[1:])] - cum[cal_i(ED[:-1])]
    days = (ED[1:] - ED[:-1]).astype(int).astype(float)
    return Panel(dec, ED, live, carry, mom, Sig, ret[:-1], acc, hs[:-1], days, cnt)


# ── signals and weights ──────────────────────────────────────────────────────────────────

def _rank_weights(score: np.ndarray, live: np.ndarray, k: int | None = None, prev: np.ndarray | None = None) -> np.ndarray:
    """+1/k on the k highest, -1/k on the k lowest live scores, 0 elsewhere. Tie-break (arbitrary but fixed, pre-registered): among exactly equal
    scores the currency that was long last month ranks higher and the one that was short ranks lower (positions are kept), and any remaining tie
    is broken by the fixed currency order USD, EUR, GBP, JPY, AUD, CAD, CHF, NZD, NOK, SEK (lower index higher)."""
    idx = np.where(live)[0]
    n = len(idx)
    w = np.zeros(len(score))
    if n < MIN_N:
        return w
    kk = k_of(n) if k is None else k
    if kk < 1 or 2 * kk > n:
        return w
    ps = np.zeros(len(score)) if prev is None else np.sign(prev)
    order = idx[np.lexsort((idx, -ps[idx], -score[idx]))]
    w[order[:kk]] = 1.0 / kk
    w[order[-kk:]] = -1.0 / kk
    return w


def _z(x: np.ndarray, live: np.ndarray) -> np.ndarray:
    v = x[live]
    sd = v.std()
    return np.where(live, (x - v.mean()) / sd, 0.0) if sd > 0 else np.zeros_like(x)


def raw_weights(kind: str, carry: np.ndarray, mom: np.ndarray, live: np.ndarray, k_override: int | None = None) -> np.ndarray:
    """(K,10) unscaled currency weights (USD included, sum zero, gross 2) from information at each decision only."""
    K = carry.shape[0]
    W = np.zeros((K, 10))
    for j in range(K):
        lv = live[j]
        if lv.sum() < MIN_N:
            continue
        prev = W[j - 1] if j > 0 else None
        if kind == "carry_rank":
            W[j] = _rank_weights(carry[j], lv, k_override, prev)
        elif kind == "mom_rank":
            m = np.where(lv, mom[j] - mom[j][lv].mean(), 0.0)
            W[j] = _rank_weights(m, lv, k_override, prev)
        elif kind == "combo_rank":
            sc = _z(carry[j], lv) + _z(np.where(lv, mom[j], 0.0), lv)
            W[j] = _rank_weights(sc, lv, k_override, prev)
        elif kind == "carry_cont":
            x = np.where(lv, carry[j] - carry[j][lv].mean(), 0.0)
            g = np.abs(x).sum()
            W[j] = 2.0 * x / g if g > 0 else 0.0
        elif kind == "long_basket":                                    # benchmark: long every foreign currency vs USD, equal weights
            w = np.where(lv, 1.0, 0.0)
            w[0] = 0.0
            w[0] = -w.sum()
            W[j] = w * (2.0 / np.abs(w).sum())
        else:
            raise ValueError(kind)
    return W


def vol_scale(wf: np.ndarray, Sig: np.ndarray) -> np.ndarray:
    """Scale that gives an ex-ante annual volatility of TARGET (capped at CAP): wf (..., K, 9), Sig (K, 9, 9)."""
    S0 = np.nan_to_num(Sig)
    var = np.einsum("...kc,kcd,...kd->...k", wf, S0, wf)
    sd = np.sqrt(np.maximum(var, 0.0))
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(sd > 1e-12, np.minimum(CAP, TARGET / np.where(sd > 1e-12, sd, 1.0)), 0.0)


def scaled_weights(panel: Panel, w10: np.ndarray) -> np.ndarray:
    wf = w10[:, 1:]
    return wf * vol_scale(wf, panel.Sig)[:, None]


# ── P&L ──────────────────────────────────────────────────────────────────────────────────

def net_from_W(panel: Panel, W: np.ndarray, markup: float = 0.0, cost_mult: float = 1.0, parts: bool = False):
    """Monthly net excess return for scaled weights W (..., K, 9) (uses the first Kp rows): spot + accrual - spread/commission - mark-up.
    Mark-up is charged on gross notional for every calendar day held. Collateral interest is not credited (excess return)."""
    Kp = panel.Kp
    W = W[..., :Kp, :]
    spot = (W * panel.ret).sum(-1)
    acc = (W * panel.acc).sum(-1)
    Wp = np.concatenate([np.zeros_like(W[..., :1, :]), W[..., :-1, :]], axis=-2)
    cost = (np.abs(W - Wp) * (0.5 * cost_mult * panel.hs + COMM)).sum(-1)
    mk = (np.abs(W) * markup).sum(-1) * panel.days / 365.0          # markup: scalar or one value per currency (nine)
    net = spot + acc - cost - mk
    if parts:
        return net, {"spot": spot, "acc": acc, "cost": cost, "markup": mk, "turnover": np.abs(W - Wp).sum(-1), "gross": np.abs(W).sum(-1)}
    return net


def sharpe(x: np.ndarray) -> np.ndarray | float:
    """Annualised Sharpe of monthly excess returns along the last axis."""
    x = np.asarray(x, float)
    n = x.shape[-1]
    if n < 3:
        return float("nan") if x.ndim == 1 else np.full(x.shape[:-1], np.nan)
    sd = x.std(axis=-1, ddof=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        out = np.where(sd > 0, x.mean(axis=-1) / np.where(sd > 0, sd, 1.0) * np.sqrt(12), 0.0)
    return float(out) if x.ndim == 1 else out


# ── nulls ────────────────────────────────────────────────────────────────────────────────

def permute_weights(base: np.ndarray, live: np.ndarray, perms: np.ndarray, blk: np.ndarray) -> np.ndarray:
    """Relabel the (Kp,10) weights `base` with one permutation per block: perms (B, nb, 10), blk (Kp,) block index of each month.
    Months with all ten currencies live use the permutation as drawn. In a month with a smaller live set (a currency whose data
    start later, or a data exclusion) the induced permutation of the live currencies is used (the relative order of the drawn
    values restricted to the live slots, a uniform permutation of that set), so weights are only ever moved between live
    currencies and a non-live slot stays at zero; with all ten live it reduces to the drawn permutation."""
    B, Kp = perms.shape[0], base.shape[0]
    w = np.take_along_axis(np.broadcast_to(base, (B, Kp, 10)), perms[:, blk, :], axis=2)
    part = ~live.all(axis=1)
    if part.any():
        pats = {}
        for m in np.where(part)[0]:
            pats.setdefault(tuple(live[m]), []).append(m)
        for pat, ms in pats.items():
            ms = np.array(ms)
            L = np.where(np.array(pat))[0]
            w[:, ms, :] = 0.0
            if len(L) == 0:
                continue
            src = L[np.argsort(np.argsort(perms[:, :, L], axis=-1), axis=-1)]         # (B, nb, n): live source slot of each live target
            vals = np.take_along_axis(np.broadcast_to(base[ms], (B, len(ms), 10)), src[:, blk[ms], :], axis=2)
            w[:, ms[:, None], L[None, :]] = vals
    return w


def null_distribution(panel: Panel, w10s: dict, n: int, seed: int, mode: str = "perm", block: int = BLOCK, batch: int = 250) -> dict:
    """S0 Sharpe of every variant under n replicates; the same replicate is applied to all variants.
    mode 'perm': the currency labels of the weight vector (equivalently of the rate / momentum ranking) are permuted uniformly at
    random, one permutation per block of `block` months and independent across blocks; spot returns, accruals (attached to
    the true currency), spreads and sizing are recomputed. Uniform permutations are exchangeable, so E[weight on a currency] = 0
    (a derangement would not be: it leaks -w_c/9).
    mode 'flip': the whole weight vector is multiplied by a random sign, one per block (gross exposure and turnover kept)."""
    rng = np.random.default_rng(seed)
    Kp = panel.Kp
    nb = int(math.ceil(Kp / block))
    blk = np.arange(Kp) // block
    sr = {v: np.zeros(n) for v in w10s}
    to = {v: np.zeros(n) for v in w10s}
    for a in range(0, n, batch):
        B = min(batch, n - a)
        if mode == "perm":
            perms = np.argsort(rng.random((B, nb, 10)), axis=-1)
        elif mode == "flip":
            sg = rng.choice([-1.0, 1.0], size=(B, nb))[:, blk]
        else:
            raise ValueError(mode)
        for v, w10 in w10s.items():
            base = w10[:Kp]
            if mode == "perm":
                w = permute_weights(base, panel.live[:Kp], perms, blk)
            else:
                w = base[None] * sg[:, :, None]
            wf = w[..., 1:]
            W = wf * vol_scale(wf, panel.Sig[:Kp])[..., None]
            net, p = net_from_W(panel, W, 0.0, 1.0, parts=True)
            sr[v][a:a + B] = sharpe(net)
            to[v][a:a + B] = p["turnover"].mean(-1)
    return {"sr": sr, "turnover": to}


def p_one_sided(obs: float, null: np.ndarray) -> float:
    return float((1 + (null >= obs).sum()) / (1 + len(null)))


def g1_pvalue(obs: float, null_block: np.ndarray, null_full: np.ndarray) -> tuple[float, float, float]:
    """(p with one relabelling per 12-month block, p with one relabelling for the whole sample, the larger of the two = the p used by G1a)."""
    a, b = p_one_sided(obs, null_block), p_one_sided(obs, null_full)
    return a, b, max(a, b)


FINAL_CONDS = ("i_lower_bound_positive", "ii_perm_p", "iii_each_currency_dropped", "iv_usd_left_out", "v_owner_markup_check")


def final_outcome(s1_point: float, conds: dict) -> tuple[str, list]:
    """FINAL taxonomy (specification; no FINAL runner exists). Exhaustive and mutually exclusive: S1 point Sharpe <= 0 is NOT REPLICATED whatever else holds
    (a positive lower bound with a non-positive point estimate cannot occur); otherwise CONFIRMED iff all of (i) to (v) hold, else CONSISTENT BUT NOT CONFIRMED.
    The list of failed conditions is reported with the outcome."""
    failed = [k for k in FINAL_CONDS if not bool(conds[k])]
    if not s1_point > 0:
        return "NOT REPLICATED", failed
    return ("CONFIRMED" if not failed else "CONSISTENT BUT NOT CONFIRMED"), failed


def holm(p: np.ndarray, alpha: float = ALPHA) -> tuple[np.ndarray, np.ndarray]:
    K = len(p)
    order = np.argsort(p)
    adj = np.zeros(K)
    run = 0.0
    for rank, i in enumerate(order):
        run = max(run, min(1.0, (K - rank) * p[i]))
        adj[i] = run
    return adj <= alpha, adj


# ── bootstrap ────────────────────────────────────────────────────────────────────────────

def boot_index(n: int, block: int, B: int, rng: np.random.Generator) -> np.ndarray:
    nb = int(math.ceil(n / block))
    starts = rng.integers(0, n, size=(B, nb))
    idx = (starts[:, :, None] + np.arange(block)[None, None, :]) % n
    return idx.reshape(B, nb * block)[:, :n]


def boot_sharpes(series: dict, blocks=BOOT_BLOCKS, B: int = N_BOOT, seed: int = SEED_BOOT) -> dict:
    """Point Sharpe and circular-block-bootstrap 95 % interval of each monthly series; all series share the resampled months.
    Reporting only (no gate uses it). The quoted interval is the one with the lowest lower bound over the block lengths."""
    n = len(next(iter(series.values())))
    out = {k: {"sr": sharpe(v), "ci": {}} for k, v in series.items()}
    for b in blocks:
        rng = np.random.default_rng(seed + b)
        idx = boot_index(n, b, B, rng)
        for k, v in series.items():
            s = sharpe(np.asarray(v)[idx])
            out[k]["ci"][b] = (float(np.percentile(s, 2.5)), float(np.percentile(s, 97.5)))
    for k in out:
        lo_b = min(out[k]["ci"], key=lambda b: out[k]["ci"][b][0])
        out[k]["lo"], out[k]["hi"], out[k]["block_of_lo"] = out[k]["ci"][lo_b][0], max(c[1] for c in out[k]["ci"].values()), lo_b
    return out


# ── per-variant evaluation and gates ─────────────────────────────────────────────────────

def era_of(year: int) -> str:
    for name, a, b in ERAS:
        if a <= year <= b:
            return name
    return "other"


def evaluate(panel: Panel, kind: str, w10: np.ndarray | None = None) -> dict:
    """Net monthly series of one construction at all financing levels and all re-cut universes needed by the gates."""
    w10 = raw_weights(kind, panel.carry, panel.mom, panel.live) if w10 is None else w10
    W = scaled_weights(panel, w10)
    out = {"w10": w10, "W": W}
    for lvl, m in MARKUP.items():
        out[lvl] = net_from_W(panel, W, m)
    out["S0_cost2"] = net_from_W(panel, W, 0.0, 2.0)
    out["S0_cost4"] = net_from_W(panel, W, 0.0, 4.0)
    net0, parts = net_from_W(panel, W, 0.0, parts=True)
    out["parts"] = parts
    out["scale"] = vol_scale(w10[:, 1:], panel.Sig)
    loco = {}
    for j, c in enumerate(FOREIGN):
        lv = panel.live.copy()
        lv[:, j + 1] = False
        loco[c] = net_from_W(panel, scaled_weights(panel, raw_weights(kind, panel.carry, panel.mom, lv)), MARKUP["S1"])
    out["loco_S1"] = loco
    lv = panel.live.copy()
    for c in EXOTIC:
        lv[:, ALL10.index(c)] = False
    out["exotic_S1"] = net_from_W(panel, scaled_weights(panel, raw_weights(kind, panel.carry, panel.mom, lv)), MARKUP["S1"])
    return out


def gates(panel: Panel, ev: dict, holm_reject: bool, p_flip: float) -> dict:
    """Section 6 gates for one family variant. Everything on S1 unless stated; years are calendar years of the execution date."""
    yrs = panel.year
    n1 = ev["S1"]
    sr1 = sharpe(n1)
    ys = list(range(2003, 2021))
    ysum = {int(y): float(n1[yrs == y].sum()) for y in np.unique(yrs)}
    ypos = sum(1 for y in ys if ysum.get(y, 0.0) > 0)
    best3 = sorted(ysum, key=lambda y: ysum[y], reverse=True)[:3]
    sr_wo3 = sharpe(n1[~np.isin(yrs, best3)])
    sr_wo0809 = sharpe(n1[~np.isin(yrs, [2008, 2009])])
    era_pos = sum(1 for _, a, b in ERAS if sharpe(n1[(yrs >= a) & (yrs <= b)]) > 0)
    loco = {c: sharpe(x) for c, x in ev["loco_S1"].items()}
    sr_ex = sharpe(ev["exotic_S1"])
    G = {"G1a": bool(holm_reject), "G1b": bool(p_flip <= ALPHA), "G2": bool(sr1 > 0), "G3a": bool(sr_wo3 > 0), "G3b": bool(ypos >= G3B_MIN_YEARS),
         "G3c": bool(era_pos >= 2), "G3d": bool(sr_wo0809 > 0), "G4a": bool(min(loco.values()) > 0), "G4b": bool(sr_ex > 0), "G5": bool(sr1 >= G5_MIN_SR)}
    G["eligible"] = all(G[k] for k in ("G1a", "G1b", "G2", "G3a", "G3b", "G3c", "G3d", "G4a", "G4b", "G5"))
    G["detail"] = {"sr_S1": sr1, "years_positive_2003_2020": ypos, "sr_without_best3": sr_wo3, "best3_years": [int(y) for y in best3],
                   "sr_without_2008_09": sr_wo0809, "eras_positive": era_pos, "loco_S1": loco, "loco_min": min(loco.values()), "sr_without_NOK_SEK": sr_ex}
    return G


def outcome(sr0: dict, g: dict, p_indiv: dict, holm_rej: dict) -> str:
    """Decision table (section 6). sr0: family variant -> S0 Sharpe; g: variant -> gates; holm_rej: variant -> bool."""
    if any(x["eligible"] for x in g.values()):
        return "E"
    detectable = [v for v in sr0 if holm_rej[v] and sr0[v] > 0 and g[v]["G1b"]]
    if detectable:
        return "P"                                   # premium detectable at S0, not capturable at the S1 mark-up or not stable
    if any(sr0[v] > 0 for v in sr0):
        return "U"                                   # positive but not detectable at DEV power
    return "N"


def select_candidate(g: dict, sr1: dict) -> str | None:
    el = [v for v in g if g[v]["eligible"]]
    return max(el, key=lambda v: sr1[v]) if el else None


# ── analytic power ───────────────────────────────────────────────────────────────────────

def power_table(T_dev: float = 221 / 12.0, T_final: float = 47 / 12.0, k_family: int = 3) -> dict:
    """Plug-in normal approximation for an ANNUALISED Sharpe from monthly data: SE(SR) = sqrt((1 + SR^2/24) f / T) (the monthly Sharpe is SR/sqrt(12), so the
    textbook (1 + SR_m^2/2)/n becomes (1 + SR^2/24)/T), power = P(z > z_(1-alpha) - SR/SE), f = 1.0 and 1.3. Checked against the empirical sd of 221 iid months."""
    nd = NormalDist()

    def pw(sr, T, alpha, f):
        se = math.sqrt((1 + sr * sr / 24) * f / T)
        return 1 - nd.cdf(nd.inv_cdf(1 - alpha) - sr / se)

    def mde(T, alpha, f, target=0.8):
        lo, hi = 0.0, 6.0
        for _ in range(80):
            mid = (lo + hi) / 2
            lo, hi = (mid, hi) if pw(mid, T, alpha, f) < target else (lo, mid)
        return (lo + hi) / 2 if pw(6.0, T, alpha, f) >= target else float("nan")
    rows = []
    for label, T, a in (("DEV, single test", T_dev, 0.05), (f"DEV, first Holm step (K={k_family})", T_dev, 0.05 / k_family), ("FINAL, one candidate", T_final, 0.025)):
        rows.append({"window": label, "T_years": round(T, 2), "alpha_one_sided": a,
                     **{f"power_SR{s}": [round(pw(s, T, a, f), 2) for f in (1.0, 1.3)] for s in (0.3, 0.5, 0.75, 1.0)},
                     "SR_80pct_power": [round(mde(T, a, f), 2) for f in (1.0, 1.3)]})
    return {"rows": rows, "note": "f = variance inflation for autocorrelation and skew (1.0, 1.3); true Sharpe means the S0 Sharpe"}


# ── DEV run ──────────────────────────────────────────────────────────────────────────────

def _per_year(panel: Panel, series: dict) -> pd.DataFrame:
    yrs = panel.year
    rows = []
    for y in np.unique(yrs):
        m = yrs == y
        row = {"year": int(y), "months": int(m.sum())}
        for k, x in series.items():
            row[f"{k}_ret"] = float(x[m].sum())
        rows.append(row)
    return pd.DataFrame(rows)


def _skew_dd(x: np.ndarray) -> tuple[float, float, float]:
    sd = x.std(ddof=1)
    sk = float(((x - x.mean()) ** 3).mean() / sd ** 3) if sd > 0 else 0.0
    eq = np.cumsum(x)
    return sk, float(x.min()), float((np.maximum.accumulate(eq) - eq).max())


def run_dev(out_dir: Path = OUT_DEV, n_perm: int = N_PERM, n_boot: int = N_BOOT, world=None, start: str = DEV_START, end: str = DEV_END,
            require_frozen: bool = True, quiet: bool = False, prereg: Path = PREREG, frozen: Path = FROZEN_SHA,
            force_rerun_logged: bool = False, rerun_reason: str = "") -> dict:
    s0, e0 = check_dev_window(start, end)
    sha = check_prereg_frozen(prereg, frozen) if require_frozen else (sha256_file(prereg) if Path(prereg).exists() else "missing")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    old = out_dir / "summary.json"
    if old.exists():
        if not force_rerun_logged:
            raise PermissionError(f"{old} exists: the DEV run is write-once (a re-run needs --force-rerun-logged with --reason; the first result is kept and logged)")
        if not rerun_reason.strip():
            raise PermissionError("a logged re-run needs a non-empty reason")
        stamp = pd.Timestamp.now(tz="UTC").strftime("%Y%m%dT%H%M%SZ")
        keep = out_dir / f"summary_before_rerun_{stamp}.json"
        keep.write_bytes(old.read_bytes())
        with open(out_dir / "rerun_log.txt", "a", encoding="utf-8") as fh:
            fh.write(f"{stamp}\treason={rerun_reason.strip()}\told_summary_sha256={sha256_file(keep)}\tprereg_sha256={sha}\tcode_sha256={sha256_file(Path(__file__))}\n")
    pairs, rates = world if world is not None else load_dev_world(end)
    panel = build_panel(pairs, rates, start, end)
    (out_dir / "prereg_sha256.txt").write_text(sha + "\n")
    evs = {v: evaluate(panel, k) for v, k in ALL_KINDS.items()}
    w10s = {v: evs[v]["w10"] for v in evs}
    nd = null_distribution(panel, w10s, n_perm, SEED_PERM, "perm")
    nfull = null_distribution(panel, w10s, n_perm, SEED_FULL, "perm", block=panel.Kp)          # one relabelling for the whole sample
    nf = null_distribution(panel, w10s, n_perm, SEED_FLIP, "flip")
    sr0 = {v: sharpe(evs[v]["S0"]) for v in evs}
    pp = {v: g1_pvalue(sr0[v], nd["sr"][v], nfull["sr"][v]) for v in evs}
    p_perm12, p_permfull, p_perm = {v: pp[v][0] for v in evs}, {v: pp[v][1] for v in evs}, {v: pp[v][2] for v in evs}      # p_perm = max = the p used by G1a
    p_flip = {v: p_one_sided(sr0[v], nf["sr"][v]) for v in evs}
    p2 = build_panel(pairs, rates, s0, e0, lag=2)
    lag2 = {v: evaluate_lite(p2, k) for v, k in ALL_KINDS.items()}
    fam = list(VARIANTS)
    rej, padj = holm(np.array([p_perm[v] for v in fam]))
    holm_rej = dict(zip(fam, [bool(x) for x in rej]))
    G = {v: gates(panel, evs[v], holm_rej[v], p_flip[v]) for v in fam}
    sr1 = {v: G[v]["detail"]["sr_S1"] for v in fam}
    cand = select_candidate(G, sr1)
    cat = outcome({v: sr0[v] for v in fam}, G, p_perm, holm_rej)
    # benchmark, bootstrap
    ev_bl = evaluate(panel, "long_basket")
    series = {"BL_S0": ev_bl["S0"], "BL_S1": ev_bl["S1"]}
    for v in evs:
        for lvl in MARKUP:
            series[f"{v}_{lvl}"] = evs[v][lvl]
        series[f"{v}_minus_BL_S0"] = evs[v]["S0"] - ev_bl["S0"]
        series[f"{v}_spot"] = evs[v]["parts"]["spot"]
        series[f"{v}_accrual"] = evs[v]["parts"]["acc"]
    bs = boot_sharpes(series, B=n_boot)
    rows = []
    for v in evs:
        pr, kind = evs[v]["parts"], ALL_KINDS[v]
        sk, worst, dd = _skew_dd(evs[v]["S0"])
        row = {"variant": v, "kind": kind, "in_family": v in VARIANTS, "SR_S0": sr0[v], "SR_S0_lo": bs[f"{v}_S0"]["lo"], "SR_S0_hi": bs[f"{v}_S0"]["hi"],
               "SR_S1": sharpe(evs[v]["S1"]), "SR_S1_lo": bs[f"{v}_S1"]["lo"], "SR_S1_hi": bs[f"{v}_S1"]["hi"], "SR_S2": sharpe(evs[v]["S2"]),
               "SR_S0_cost2": sharpe(evs[v]["S0_cost2"]), "SR_S0_cost4": sharpe(evs[v]["S0_cost4"]),
               "SR_minus_BL_S0": sr0[v] - sharpe(ev_bl["S0"]), "SR_accrual_part": bs[f"{v}_accrual"]["sr"], "SR_spot_part": bs[f"{v}_spot"]["sr"],
               "ann_ret_S0": float(evs[v]["S0"].mean() * 12), "ann_vol": float(evs[v]["S0"].std(ddof=1) * np.sqrt(12)),
               "ann_accrual": float(pr["acc"].mean() * 12), "ann_spot": float(pr["spot"].mean() * 12), "ann_cost": float(pr["cost"].mean() * 12),
               "gross_notional": float(pr["gross"].mean()), "turnover_per_year": float(pr["turnover"].mean() * 12), "mean_scale": float(evs[v]["scale"][: panel.Kp].mean()),
               "breakeven_markup_pct": float(((pr["acc"] + pr["spot"] - pr["cost"]).mean() / (pr["gross"] * panel.days / 365.0).mean()) * 100),
               "skew": sk, "worst_month": worst, "max_drawdown": dd,
               "SR_S0_lag2": sharpe(lag2[v]["S0"]), "SR_S1_lag2": sharpe(lag2[v]["S1"]),
               "mean_net_USD_weight": float(-evs[v]["W"][: panel.Kp].sum(1).mean()), "share_months_net_long_USD": float((evs[v]["W"][: panel.Kp].sum(1) < 0).mean()),
               "p_perm12": p_perm12[v], "p_permfull": p_permfull[v], "p_perm": p_perm[v], "p_flip": p_flip[v], "null_mean_SR": float(nd["sr"][v].mean()), "null_sd_SR": float(nd["sr"][v].std(ddof=1)),
               "null_turnover_ratio": float(nd["turnover"][v].mean() / max(pr["turnover"].mean(), 1e-12)), "flip_null_sd_SR": float(nf["sr"][v].std(ddof=1))}
        if v in VARIANTS:
            row.update({"p_holm_adj": float(padj[fam.index(v)]), "holm_reject": holm_rej[v], **{k: G[v][k] for k in ("G1a", "G1b", "G2", "G3a", "G3b", "G3c", "G3d", "G4a", "G4b", "G5", "eligible")},
                        "years_positive_2003_2020": G[v]["detail"]["years_positive_2003_2020"], "SR_without_best3": G[v]["detail"]["sr_without_best3"],
                        "SR_without_2008_09": G[v]["detail"]["sr_without_2008_09"], "eras_positive": G[v]["detail"]["eras_positive"],
                        "loco_min_S1": G[v]["detail"]["loco_min"], "SR_without_NOK_SEK_S1": G[v]["detail"]["sr_without_NOK_SEK"]})
        rows.append(row)
    tab = pd.DataFrame(rows)
    tab.to_csv(out_dir / "variants.csv", index=False)
    mp = pd.DataFrame({"ED": panel.ED[: panel.Kp]})
    for v in evs:
        mp[f"{v}_S0"], mp[f"{v}_S1"] = evs[v]["S0"], evs[v]["S1"]
        mp[f"{v}_spot"], mp[f"{v}_accrual"] = evs[v]["parts"]["spot"], evs[v]["parts"]["acc"]
    mp["BL_S0"] = ev_bl["S0"]
    mp.to_csv(out_dir / "monthly_pnl.csv", index=False)
    _per_year(panel, {**{f"{v}_S1": evs[v]["S1"] for v in evs}, "BL_S0": ev_bl["S0"]}).to_csv(out_dir / "per_year.csv", index=False)
    pd.DataFrame([{"variant": v, "dropped": c, "SR_S1": sharpe(x)} for v in fam for c, x in evs[v]["loco_S1"].items()]).to_csv(out_dir / "per_currency_loco.csv", index=False)
    pd.DataFrame({v: nd["sr"][v] for v in evs}).to_csv(out_dir / "null_distribution_perm.csv", index=False)
    pd.DataFrame({v: nfull["sr"][v] for v in evs}).to_csv(out_dir / "null_distribution_perm_full.csv", index=False)
    pd.DataFrame({v: nf["sr"][v] for v in evs}).to_csv(out_dir / "null_distribution_flip.csv", index=False)
    sens = _sensitivity(pairs, rates, panel, evs, s0, e0, p2)
    sens.to_csv(out_dir / "sensitivity.csv", index=False)
    summary = {"prereg_sha256": sha, "code_sha256": sha256_file(Path(__file__)), "window": [str(s0.date()), str(e0.date())], "months": panel.Kp,
               "years": round(panel.Kp / 12.0, 2), "n_perm": n_perm, "n_boot": n_boot, "outcome": cat, "candidate": cand, "panel_counts": panel.counts,
               "live_share_by_ccy": dict(zip(ALL10, [float(x) for x in panel.live[: panel.Kp].mean(0)])),
               "bl_SR_S0": sharpe(ev_bl["S0"]), "bl_SR_S1": sharpe(ev_bl["S1"]), "holm_adj_p": dict(zip(fam, [float(x) for x in padj])),
               "p_perm_G1": p_perm, "p_perm12": p_perm12, "p_permfull": p_permfull, "p_flip": p_flip,
               "lag2_SR_S1": {v: sharpe(lag2[v]["S1"]) for v in fam}, "lag2_SR_S1_positive_for_all_family": bool(all(sharpe(lag2[v]["S1"]) > 0 for v in fam)),
               "eligible": {v: bool(G[v]["eligible"]) for v in fam},
               "gate_detail": {v: {k: x for k, x in G[v]["detail"].items()} for v in fam}}
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, default=float))
    if cand:
        fc = {"variant": cand, "kind": VARIANTS[cand], "lag_months": LAG_MONTHS, "block": BLOCK, "vol_target": TARGET, "cap": CAP, "markup_S1": MARKUP["S1"],
              "SR_S1_dev": sr1[cand], "prereg_sha256": sha, "code_sha256": summary["code_sha256"]}
        (out_dir / "frozen_candidate.json").write_text(json.dumps(fc, indent=2, default=float))
    if not quiet:
        print(format_report(tab, summary))
    return {"table": tab, "summary": summary, "panel": panel, "evs": evs, "gates": G, "null": nd, "flip": nf, "sens": sens}


def _sensitivity(pairs, rates, panel, evs, s0, e0, p2=None) -> pd.DataFrame:
    """Descriptive only. Nothing here changes eligibility."""
    rows = []

    def add(name, series_by_v):
        for v, x in series_by_v.items():
            rows.append({"case": name, "variant": v, "SR_S0": sharpe(x["S0"]), "SR_S1": sharpe(x["S1"]), "months": len(x["S0"])})
    add("primary", {v: evs[v] for v in evs})
    p2 = build_panel(pairs, rates, s0, e0, lag=2) if p2 is None else p2
    add("rate_lag_2_months", {v: evaluate_lite(p2, k) for v, k in ALL_KINDS.items()})
    for kk in (2, 4):
        add(f"top_bottom_{kk}", {v: evaluate_lite(panel, k, k_override=kk) for v, k in ALL_KINDS.items() if k.endswith("rank")})
    lv_xu = panel.live.copy()
    lv_xu[:, 0] = False                                                  # USD left out of the ranking: no net USD exposure
    add("exUSD_ranking", {v: evaluate_lite(panel, k, live=lv_xu) for v, k in ALL_KINDS.items()})
    yrs = panel.year
    for name, mask in (("from_2010", yrs >= 2010), ("from_2014", yrs >= 2014), ("without_2008_09", ~np.isin(yrs, [2008, 2009])), ("to_2019", yrs <= 2019)):
        add(name, {v: {"S0": evs[v]["S0"][mask], "S1": evs[v]["S1"][mask]} for v in evs})
    add("from_2007-02_all_ten_live", {v: {"S0": evs[v]["S0"][panel.ED[: panel.Kp] >= np.datetime64("2007-02-01")], "S1": evs[v]["S1"][panel.ED[: panel.Kp] >= np.datetime64("2007-02-01")]} for v in evs})
    add("spread_cost_x2", {v: {"S0": evs[v]["S0_cost2"], "S1": net_from_W(panel, evs[v]["W"], MARKUP["S1"], 2.0)} for v in evs})
    add("spread_cost_x4", {v: {"S0": evs[v]["S0_cost4"], "S1": net_from_W(panel, evs[v]["W"], MARKUP["S1"], 4.0)} for v in evs})
    add("markup_S2", {v: {"S0": evs[v]["S0"], "S1": evs[v]["S2"]} for v in evs})
    snap = np.array([MARKUP_SNAP[c] for c in FOREIGN]) / 100.0
    add("markup_snapshot_per_pair", {v: {"S0": evs[v]["S0"], "S1": net_from_W(panel, evs[v]["W"], snap)} for v in evs})
    return pd.DataFrame(rows)


def evaluate_lite(panel: Panel, kind: str, k_override: int | None = None, live: np.ndarray | None = None) -> dict:
    w10 = raw_weights(kind, panel.carry, panel.mom, panel.live if live is None else live, k_override)
    W = scaled_weights(panel, w10)
    return {"S0": net_from_W(panel, W, 0.0), "S1": net_from_W(panel, W, MARKUP["S1"])}


def format_report(tab: pd.DataFrame, summary: dict) -> str:
    L = [f"Window {summary['window'][0]} to {summary['window'][1]}, {summary['months']} monthly periods ({summary['years']} years). prereg sha256 {summary['prereg_sha256'][:12]}",
         f"Benchmark BL (long all foreign vs USD, vol-targeted): SR S0 {summary['bl_SR_S0']:+.2f}, S1 {summary['bl_SR_S1']:+.2f}", f"Outcome category: {summary['outcome']}   Candidate: {summary['candidate']}", ""]
    c1 = ["variant", "SR_S0", "SR_S0_lo", "SR_S0_hi", "SR_S1", "SR_S2", "SR_accrual_part", "SR_spot_part", "SR_minus_BL_S0", "ann_accrual", "ann_spot", "ann_cost", "ann_vol",
          "SR_S0_lag2", "SR_S1_lag2", "gross_notional", "turnover_per_year", "breakeven_markup_pct", "skew", "worst_month", "mean_net_USD_weight", "p_perm12", "p_permfull", "p_perm", "p_flip", "null_sd_SR"]
    L.append(tab[c1].to_string(index=False, float_format=lambda x: f"{x:.3f}"))
    fam = tab[tab["in_family"]]
    L.append("")
    L.append(fam[["variant", "p_holm_adj", "G1a", "G1b", "G2", "G3a", "G3b", "G3c", "G3d", "G4a", "G4b", "G5", "eligible", "years_positive_2003_2020", "SR_without_best3", "SR_without_2008_09", "loco_min_S1"]]
             .to_string(index=False, float_format=lambda x: f"{x:.3f}"))
    return "\n".join(L)


# ── synthetic world and calibration (no project returns) ─────────────────────────────────

SYNTH_VOL = {"EUR": 0.08, "GBP": 0.08, "JPY": 0.09, "AUD": 0.11, "CAD": 0.07, "CHF": 0.09, "NZD": 0.11, "NOK": 0.11, "SEK": 0.11}    # stylised, general knowledge
SYNTH_X0 = {"EUR": 1.0, "GBP": 1.5, "JPY": 0.01, "AUD": 0.7, "CAD": 0.8, "CHF": 0.8, "NZD": 0.6, "NOK": 0.15, "SEK": 0.13}               # USD per unit at the start
CRASH_RATE, CRASH_MEAN = 0.15, 0.10                                                                                           # jumps a year, mean size
SYNTH_RHO = {"JPY": 0.3, "CAD": 0.3}                                                                                                # loading on the common USD factor; others 0.6


def synth_world(rng: np.random.Generator, rates: pd.DataFrame, lam: float, start: str = "2000-03-30", end: str = DEV_END, spread_pts: float = 10.0,
                slow_sd: float = 0.0, crash: bool = False) -> dict:
    """Nine synthetic USD pairs on weekday D1 bars. The expected log spot change of currency c is -lam x (rate_c - rate_USD)/100 per year
    (day-weighted by month, ffilled, backfilled before the first rate): lam = 1 is UIP (no carry premium, true excess return 0), lam = 0 a
    random-walk spot (the carry premium equals the accrual), lam < 0 spot moves with the differential. Noise: one common factor plus idiosyncratic
    terms at stylised volatilities. Real RATES are inputs; no project price or return is used.
    slow_sd > 0 adds a zero-mean slow idiosyncratic drift to each currency (an Ornstein-Uhlenbeck expected return with stationary sd `slow_sd` per year and a
    half-life of 3 years), which creates variance ratios above 1 without any carry premium. crash=True adds common carry-crash jumps (0.15 a year, exponential size
    with mean 10 %, loading on each currency proportional to its rate differential over 3 %: high-yield currencies fall, low-yield rise), compensated so that
    the expected excess return stays that of `lam` (zero for lam = 1)."""
    days = pd.bdate_range(start, pd.Timestamp(end) - pd.Timedelta(days=1))
    n = len(days)
    rf = rates_ffilled(rates).reindex(pd.date_range(days[0].replace(day=1), days[-1], freq="MS")).ffill().bfill()
    rd = rf.reindex(days.to_period("M").to_timestamp())[ALL10].to_numpy(float)
    f = rng.standard_normal(n)
    crash_size = (rng.random(n) < CRASH_RATE / ANN_DAYS) * rng.exponential(CRASH_MEAN, n) if crash else None
    out = {}
    for j, c in enumerate(FOREIGN):
        diff = (rd[:, j + 1] - rd[:, 0]) / 100.0
        rho = SYNTH_RHO.get(c, 0.6)
        vol = SYNTH_VOL[c] / math.sqrt(ANN_DAYS)
        r = -lam * diff / ANN_DAYS + vol * (rho * f + math.sqrt(1 - rho * rho) * rng.standard_normal(n))
        if slow_sd > 0:
            phi = 0.5 ** (1.0 / 780.0)
            eps = rng.standard_normal(n) * slow_sd * math.sqrt(1 - phi * phi)
            x = np.empty(n)
            x[0] = slow_sd * rng.standard_normal()
            for tt in range(1, n):
                x[tt] = phi * x[tt - 1] + eps[tt]
            r = r + x / ANN_DAYS
        if crash:
            load = diff / 0.03
            r = r - crash_size * load + (CRASH_RATE * CRASH_MEAN / ANN_DAYS) * load
        X = np.exp(np.cumsum(r)) * SYNTH_X0[c]
        P = 1.0 / X if PAIR_OF[c][1] else X
        close = P
        openp = np.concatenate([[P[0]], P[:-1]])
        out[c] = make_pair(c, PAIR_OF[c][0], PAIR_OF[c][1], days.to_numpy("datetime64[D]"), openp, close, np.full(n, spread_pts), 1e-5)
    return out


CAL_WORLDS = (("uip_slow_drift_2pct", {"slow_sd": 0.02}), ("uip_slow_drift_4pct", {"slow_sd": 0.04}), ("uip_carry_crash", {"crash": True}))


def _cal_panel_stats(panel: Panel, rng: np.random.Generator, n_perm: int, acc: dict) -> None:
    evs = {v: evaluate(panel, k) for v, k in ALL_KINDS.items()}
    w10s = {v: evs[v]["w10"] for v in evs}
    sd = int(rng.integers(1 << 30))
    nd = null_distribution(panel, w10s, n_perm, sd, "perm")
    nfull = null_distribution(panel, w10s, n_perm, sd + 2, "perm", block=panel.Kp)
    nf = null_distribution(panel, w10s, n_perm, sd + 1, "flip")
    sr0 = {v: sharpe(evs[v]["S0"]) for v in evs}
    pp = {v: g1_pvalue(sr0[v], nd["sr"][v], nfull["sr"][v]) for v in evs}
    p_perm = {v: pp[v][2] for v in evs}
    p_flip = {v: p_one_sided(sr0[v], nf["sr"][v]) for v in evs}
    fam = list(VARIANTS)
    rej, _ = holm(np.array([p_perm[v] for v in fam]))
    hr = dict(zip(fam, [bool(x) for x in rej]))
    G = {v: gates(panel, evs[v], hr[v], p_flip[v]) for v in fam}
    cat = outcome({v: sr0[v] for v in fam}, G, p_perm, hr)

    def put(k, x):
        acc.setdefault(k, []).append(float(x))
    for v in evs:
        put(f"SR0_{v}", sr0[v])
        put(f"SR1_{v}", sharpe(evs[v]["S1"]))
    put("SR0_family_mean", np.mean([sr0[v] for v in fam]))
    put("M_p_perm_below_005", p_perm["M"] <= 0.05)
    put("V1_p_block12_below_005", pp["V1"][0] <= 0.05)
    put("V1_p_full_below_005", pp["V1"][1] <= 0.05)
    put("V1_p_G1_below_005", pp["V1"][2] <= 0.05)
    put("V1_p_flip_below_005", p_flip["V1"] <= 0.05)
    for g in ("G1a", "G1b", "G2", "G3a", "G3b", "G3c", "G3d", "G4a", "G4b", "G5"):
        put(f"any_{g}", any(G[v][g] for v in fam))
    put("any_eligible", any(G[v]["eligible"] for v in fam))
    for c in "EPUN":
        put(f"outcome_{c}", cat == c)


def calibrate(out_dir: Path = OUT_DEV, n_panels: int = 100, n_perm: int = 499, lams=(1.0, 0.5, 0.0, -0.5, -1.0, -2.0), seed: int = 11, rates: pd.DataFrame | None = None,
              worlds=CAL_WORLDS) -> dict:
    """Pass probability of every gate, of eligibility and of each outcome category on planted-carry panels (rows by lambda: iid Gaussian noise) and on
    zero-premium (UIP) worlds with slow idiosyncratic drift and with common carry-crash jumps (rows by world), with the real calendar, the real RATES and
    synthetic spot returns. G1a uses the larger of the block-12 and the whole-sample relabelling p-values. Thresholds are NOT tuned from this."""
    rates = load_rates(DEV_END) if rates is None else rates
    rows = []
    for lam in lams:
        rng = np.random.default_rng(seed + int(round(lam * 100)) + 1000)
        acc: dict = {}
        for _ in range(n_panels):
            _cal_panel_stats(build_panel(synth_world(rng, rates, lam), rates, DEV_START, DEV_END), rng, n_perm, acc)
        rows.append({"world": "iid_gaussian", "lambda": lam, "n_panels": n_panels, "n_perm": n_perm, **{k: float(np.mean(x)) for k, x in acc.items()}})
    for k, (name, kw) in enumerate(worlds):
        rng = np.random.default_rng(seed + 5000 + 17 * k)
        acc = {}
        for _ in range(n_panels):
            _cal_panel_stats(build_panel(synth_world(rng, rates, 1.0, **kw), rates, DEV_START, DEV_END), rng, n_perm, acc)
        rows.append({"world": name, "lambda": 1.0, "n_panels": n_panels, "n_perm": n_perm, **{k2: float(np.mean(x)) for k2, x in acc.items()}})
    res = {"rows": rows, "prereg_sha256": sha256_file(PREREG) if PREREG.exists() else "missing", "code_sha256": sha256_file(Path(__file__)),
           "note": "synthetic panels: real calendar, real RATES, synthetic spot returns (stylised vols, one common factor); lambda=1 is UIP (true excess return 0). "
                   "Rows 'iid_gaussian' vary the planted carry; rows uip_* have zero true premium with persistent or crash-exposed currency returns. "
                   "Thresholds are not tuned from this. SR0_* are mean realised S0 Sharpes of the panels."}
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    (Path(out_dir) / "calibration.json").write_text(json.dumps(res, indent=2))
    return res


# ── CLI ──────────────────────────────────────────────────────────────────────────────────

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("power")
    sub.add_parser("freeze")
    c = sub.add_parser("calibrate")
    c.add_argument("--out", default=str(OUT_DEV))
    c.add_argument("--n-panels", type=int, default=100)
    c.add_argument("--n-perm", type=int, default=499)
    d = sub.add_parser("dev")
    d.add_argument("--out", default=str(OUT_DEV))
    d.add_argument("--n-perm", type=int, default=N_PERM)
    d.add_argument("--n-boot", type=int, default=N_BOOT)
    d.add_argument("--force-rerun-logged", action="store_true", help="allow a second run; the first summary is kept and the re-run is appended to rerun_log.txt")
    d.add_argument("--reason", default="", help="required with --force-rerun-logged")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "power":
            res = power_table()
            OUT_DEV.mkdir(parents=True, exist_ok=True)
            (OUT_DEV / "power_table.json").write_text(json.dumps(res, indent=2))
            print(json.dumps(res, indent=2))
        elif a.cmd == "freeze":
            print(freeze())
        elif a.cmd == "calibrate":
            print(json.dumps(calibrate(Path(a.out), a.n_panels, a.n_perm), indent=2))
        else:
            run_dev(Path(a.out), a.n_perm, a.n_boot, force_rerun_logged=a.force_rerun_logged, rerun_reason=a.reason)
    except (ValueError, PermissionError, FileNotFoundError) as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
