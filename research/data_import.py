"""
Third-party M1/M5 bar importer with a hard validation gate.

    python -m research.data_tools import --symbol EURUSD --file path\\EURUSD_M1.csv --tz UTC --side bid          # dry run
    python -m research.data_tools import --symbol EURUSD --file ... --tz UTC --side bid --commit                 # merge

Pipeline (import_external):
  1. read_external()   parse Dukascopy / HistData / MetaTrader-CSV / generic CSV|parquet; explicit timezone -> UTC.
  2. bid/ask handling  MT5 bars are BID prices. 'ask'/'mid' input is converted to bid with a spread; a separate
                       ask file (ask_path) gives bid+ask -> a derived spread. Anything not a broker-recorded spread
                       is flagged `spread_synth=True`.
  3. quality_report()  on the source itself (duplicates, grid alignment, Saturday bars, OHLC validity ...).
  4. resample_bars()   M1/M5 -> M5, M15, H1, H4 on the BROKER's own bucket boundaries (H4 bars start 00/04/08..
                       broker time, i.e. their UTC hour moves with US DST).
  5. validate_bars()   each timeframe against the overlapping real MT5 bars in the cache: bar alignment, price
                       agreement (in units of that timeframe's median range), bias (bid/ask), return correlation,
                       time-shift search (+-12h) and a separate summer/winter (DST) comparison.
  6. merge             ONLY if nothing failed. Imported bars never overwrite MT5 bars and never fill holes inside the
                       MT5 span (only bars strictly before the first / after the last MT5 bar are added). Output rows
                       carry src="import:<name>" and spread_synth=True.
Nothing is downloaded here; you supply the file.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import logging
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional, Sequence

import numpy as np
import pandas as pd

from research.data import (ny_is_dst, DATA_DIR, TF_MINUTES, MT5_SRC, normalize_bars, utc_to_server, server_to_utc,
                           load_specs, cache_path, append_bars, _read_json)
from research.data_quality import quality_report

logger = logging.getLogger(__name__)

IMPORT_TFS = ("M5", "M15", "H1", "H4")


class ImportRefused(Exception):
    """Raised when validation fails; .report holds the full ImportReport (nothing was written)."""

    def __init__(self, message: str, report: "ImportReport"):
        super().__init__(message)
        self.report = report


# ══ 1. reading + timezone ════════════════════════════════════════════════════════════

_GMT_SUFFIX = re.compile(r"\s*GMT([+-])(\d{2})(\d{2})\s*$", re.I)
_FIXED = re.compile(r"^(?:fixed:|utc|gmt)?\s*([+-])\s*(\d{1,2})(?::?(\d{2}))?$", re.I)


def parse_fixed_offset(tz: str) -> Optional[pd.Timedelta]:
    """'+2', 'fixed:-5', 'UTC+02:00', '-0500', 'EST' (HistData: US Eastern WITHOUT DST) -> Timedelta, else None."""
    t = tz.strip()
    if t.upper() == "EST":
        return pd.Timedelta(hours=-5)
    m = _FIXED.match(t)
    if not m:
        return None
    sign = -1 if m.group(1) == "-" else 1
    return sign * pd.Timedelta(hours=int(m.group(2)), minutes=int(m.group(3) or 0))


def localize_to_utc(naive: pd.Series, tz: str) -> tuple[pd.Series, int]:
    """
    naive timestamps in `tz` -> tz-aware UTC. Returns (utc_series, n_dropped).
      'UTC' | 'GMT'                 already UTC
      'broker' | 'mt5'              MT5 server time (NY-close rule: UTC+2 winter / UTC+3 US-DST) - identical to fetch
      '+2' | 'fixed:-5' | 'EST'     constant offset from UTC, no DST (HistData is 'EST' = fixed -5)
      'America/New_York' etc.       IANA zone WITH daylight saving; ambiguous/non-existent local times are dropped
    There is deliberately no default: pass the zone your vendor documents.
    """
    t = tz.strip()
    if t.upper() in ("UTC", "GMT", "Z"):
        return naive.dt.tz_localize("UTC"), 0
    if t.lower() in ("broker", "mt5", "server", "mt5_server"):
        return server_to_utc(naive), 0
    off = parse_fixed_offset(t)
    if off is not None:
        return (naive - off).dt.tz_localize("UTC"), 0
    try:
        loc = naive.dt.tz_localize(t, ambiguous="NaT", nonexistent="NaT")
    except Exception as e:
        raise ValueError(f"unknown timezone {tz!r}: use UTC, broker, a fixed offset like +2 / fixed:-5 / EST, "
                         f"or an IANA name like America/New_York") from e
    dropped = int(loc.isna().sum())
    return loc.dt.tz_convert("UTC"), dropped


def _open_text(path: Path):
    if path.suffix == ".gz":
        return io.TextIOWrapper(gzip.open(path, "rb"), encoding="utf-8-sig", errors="replace")
    return open(path, "r", encoding="utf-8-sig", errors="replace")


def sniff_format(path: Path) -> str:
    """dukascopy | histdata | mt | generic | parquet"""
    path = Path(path)
    if path.suffix == ".parquet":
        return "parquet"
    with _open_text(path) as f:
        lines = [ln.strip() for ln in (f.readline() for _ in range(3)) if ln.strip()]
    if not lines:
        raise ValueError(f"{path} is empty")
    first = lines[0].lower()
    if "gmt time" in first or "local time" in first:
        return "dukascopy"
    if re.match(r"^\d{8} \d{6};", lines[0]) or first.startswith("datetime stamp"):
        return "histdata"
    if re.match(r"^\d{4}\.\d{2}\.\d{2},\d{2}:\d{2},", lines[0]):
        return "mt"
    return "generic"


_ALIASES = {
    "time": ("time", "datetime", "date_time", "timestamp", "date", "gmt time", "local time", "utc", "t"),
    "open": ("open", "o", "bar open bid quote"),
    "high": ("high", "h", "bar high bid quote"),
    "low": ("low", "l", "bar low bid quote"),
    "close": ("close", "c", "bar close bid quote"),
    "volume": ("volume", "vol", "tick_volume", "tickvol", "tick volume", "v"),
    "spread": ("spread", "spread_pts", "spreadpoints"),
}


def _map_columns(df: pd.DataFrame) -> pd.DataFrame:
    low = {str(c).strip().lower(): c for c in df.columns}
    out = {}
    for canon, names in _ALIASES.items():
        for n in names:
            if n in low:
                out[canon] = df[low[n]]
                break
    missing = [k for k in ("time", "open", "high", "low", "close") if k not in out]
    if missing:
        raise ValueError(f"cannot find columns {missing} in {list(df.columns)}")
    return pd.DataFrame(out)


def _parse_times(raw: pd.Series, tz: str, fmt_hint: Optional[str] = None) -> tuple[pd.Series, int]:
    """Any of: epoch s/ms (UTC unless tz='broker'), 'dd.mm.yyyy HH:MM:SS(.fff) [GMT+hhmm]', ISO strings."""
    if np.issubdtype(raw.dtype, np.number):
        unit = "ms" if float(raw.iloc[0]) > 1e11 else "s"
        naive = pd.to_datetime(raw, unit=unit)
        return localize_to_utc(naive, "broker" if tz.lower() in ("broker", "mt5") else "UTC")
    s = raw.astype(str).str.strip()
    gmt = s.str.extract(_GMT_SUFFIX)
    has_suffix = gmt[0].notna()
    if has_suffix.any():
        if not has_suffix.all():
            raise ValueError("mixed rows with and without a 'GMT+hhmm' suffix")
        sign = np.where(gmt[0] == "-", -1, 1)
        off = pd.to_timedelta(sign * (gmt[1].astype(int) * 60 + gmt[2].astype(int)), unit="min")
        body = s.str.replace(_GMT_SUFFIX, "", regex=True)
        naive = _to_naive(body, fmt_hint)
        return (naive - off).dt.tz_localize("UTC"), 0
    return localize_to_utc(_to_naive(s, fmt_hint), tz)


def _to_naive(s: pd.Series, fmt_hint: Optional[str]) -> pd.Series:
    if fmt_hint:
        return pd.to_datetime(s, format=fmt_hint)
    if s.str.match(r"^\d{2}\.\d{2}\.\d{4}").all():
        return pd.to_datetime(s, format="%d.%m.%Y %H:%M:%S.%f" if s.str.contains(r"\.\d{3}$").any()
                              else "%d.%m.%Y %H:%M:%S")
    if s.str.match(r"^\d{8} \d{6}$").all():
        return pd.to_datetime(s, format="%Y%m%d %H%M%S")
    return pd.to_datetime(s, format="mixed")


def read_external(path, fmt: str = "auto", tz: str = "UTC", time_format: Optional[str] = None) -> pd.DataFrame:
    """
    Parse a vendor file into DataFrame[time (UTC), open, high, low, close, volume(float)[, spread]].
    `tz` is the timezone of the file's timestamps (see localize_to_utc) and has NO default in the CLI.
    Formats: dukascopy ('Gmt time,Open,High,Low,Close,Volume' or 'Local time' with a GMT+hhmm suffix),
    histdata (no header, 'YYYYMMDD HHMMSS;o;h;l;c;v', documented as EST without DST => tz='EST'),
    mt (MetaTrader CSV export 'YYYY.MM.DD,HH:MM,o,h,l,c,v'), generic (header with time/open/high/low/close[/volume/spread]),
    parquet (same column names). Duplicate timestamps keep the last row.
    """
    path = Path(path)
    fmt = sniff_format(path) if fmt == "auto" else fmt
    if fmt == "parquet":
        raw = pd.read_parquet(path)
        df = _map_columns(raw)
    elif fmt == "dukascopy":
        raw = pd.read_csv(path)
        hdr0 = str(raw.columns[0]).strip().lower()
        if hdr0 == "gmt time" and tz.strip().upper() not in ("UTC", "GMT", "Z"):
            raise ValueError("the file header says 'Gmt time' but tz was given as "
                             f"{tz!r}; use tz=UTC (or fmt=generic if the header is mislabelled)")
        df = _map_columns(raw)
    elif fmt == "histdata":
        raw = pd.read_csv(path, sep=";", header=None, names=["time", "open", "high", "low", "close", "volume"],
                          skiprows=1 if sniff_header_line(path) else 0)
        df = raw
    elif fmt == "mt":
        raw = pd.read_csv(path, header=None, names=["d", "t", "open", "high", "low", "close", "volume"])
        raw["time"] = raw["d"].astype(str) + " " + raw["t"].astype(str)
        df = raw[["time", "open", "high", "low", "close", "volume"]]
        time_format = time_format or "%Y.%m.%d %H:%M"
    elif fmt == "generic":
        raw = pd.read_csv(path)
        df = _map_columns(raw)
    else:
        raise ValueError(f"unknown fmt {fmt!r}")

    df = df.copy()
    utc, dropped = _parse_times(df["time"], tz, time_format)
    n_total = len(df)
    df["time"] = utc
    df = df[df["time"].notna()]
    if dropped and dropped > 0.005 * n_total:
        raise ValueError(f"{dropped} of {n_total} timestamps are ambiguous/non-existent local times in {tz!r}; "
                         f"use a fixed offset or UTC export instead of a DST zone")
    for c in ("open", "high", "low", "close"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0.0) if "volume" in df.columns else 0.0
    keep = ["time", "open", "high", "low", "close", "volume"] + (["spread"] if "spread" in df.columns else [])
    df = df[keep].dropna(subset=["open", "high", "low", "close"])
    df = df.drop_duplicates("time", keep="last").sort_values("time").reset_index(drop=True)
    df.attrs["dropped_ambiguous_times"] = dropped
    df.attrs["format"] = fmt
    return df


def sniff_header_line(path: Path) -> bool:
    with _open_text(Path(path)) as f:
        first = f.readline().strip().lower()
    return first.startswith("datetime stamp") or not re.match(r"^\d{8} \d{6};", first)


def infer_tf(times: pd.Series) -> str:
    d = times.diff().dropna()
    d = d[d > pd.Timedelta(0)]
    if d.empty:
        raise ValueError("cannot infer timeframe from <2 bars")
    mins = d.median() / pd.Timedelta(minutes=1)
    for name, m in TF_MINUTES.items():
        if abs(mins - m) < 1e-9:
            return name
    raise ValueError(f"median bar spacing {mins} min matches no supported timeframe {list(TF_MINUTES)}")


# ══ 2. bid / ask / spread ════════════════════════════════════════════════════════════

def pair_bid_ask(bid: pd.DataFrame, ask: pd.DataFrame, point: float) -> pd.DataFrame:
    """Bid OHLC (what MT5 bars are) + a derived spread in points = round((ask_close - bid_close)/point), from two vendor
    files for the same period. Only bars present in both are kept. The spread is the VENDOR's, so it is still flagged
    synthetic (not what the MT5 broker charges)."""
    j = bid.merge(ask[["time", "close"]].rename(columns={"close": "ask_close"}), on="time", how="inner")
    if len(j) < 0.9 * min(len(bid), len(ask)):
        raise ValueError(f"bid and ask files share only {len(j)} of {min(len(bid), len(ask))} bars - not the same period/timezone?")
    j["spread"] = np.maximum(0, np.rint((j["ask_close"] - j["close"]) / point))
    return j.drop(columns=["ask_close"])


def to_bid(df: pd.DataFrame, side: str, spread_price) -> pd.DataFrame:
    """Convert ask/mid OHLC to bid by subtracting the (full / half) spread, given in PRICE units (scalar or per-row)."""
    side = side.lower()
    if side == "bid":
        return df
    if side not in ("ask", "mid"):
        raise ValueError("side must be bid | ask | mid")
    if spread_price is None:
        raise ValueError(f"price side {side!r} needs a spread to convert to bid")
    k = 1.0 if side == "ask" else 0.5
    out = df.copy()
    for c in ("open", "high", "low", "close"):
        out[c] = out[c] - k * np.asarray(spread_price, dtype=float)
    return out


def hourly_spread_profile(mt5_m5: pd.DataFrame) -> pd.Series:
    """Median non-zero broker-recorded spread (points) by UTC hour from real MT5 bars; imports are excluded."""
    real = mt5_m5[~mt5_m5["spread_synth"].astype(bool)] if "spread_synth" in mt5_m5.columns else mt5_m5
    nz = real["spread"].where(real["spread"] > 0)
    prof = nz.groupby(real["time"].dt.hour).median()
    return prof.reindex(range(24)).fillna(nz.median())


def synthesize_spread(times: pd.Series, profile: Optional[pd.Series], scale: float = 1.0,
                      fixed_pts: Optional[float] = None) -> np.ndarray:
    """Synthetic spread (points) for imported bars: `fixed_pts`, or the MT5 hour-of-day profile x scale. Never 0."""
    if fixed_pts is not None:
        return np.full(len(times), max(1.0, float(fixed_pts)))
    if profile is None or profile.isna().all():
        raise ValueError("no MT5 spread history to build a synthetic spread from - pass spread_fixed_pts")
    sp = profile.reindex(times.dt.hour).to_numpy(float) * scale
    return np.maximum(1.0, np.rint(sp))


# ══ 3. resampling on broker boundaries ═══════════════════════════════════════════════

def resample_bars(df: pd.DataFrame, tf: str, drop_partial_edges: bool = True) -> pd.DataFrame:
    """
    M1/M5 (UTC) -> `tf` bars bucketed on BROKER time (utc_to_server), exactly how MT5 builds H1/H4: an H4 bar covers
    server 00:00-04:00, 04:00-08:00 ... which is 21:00/01:00/05:00 UTC in US summer and 22:00/02:00/06:00 in winter.
    Output times are the UTC bucket starts. Volume is summed; the flags/spread columns, if present, use last / max.
    With drop_partial_edges the first and last bucket are dropped when the data does not cover them fully (file edges).
    """
    src_tf = infer_tf(df["time"])
    step = TF_MINUTES[tf]
    src_step = TF_MINUTES[src_tf]
    if src_step > step:
        raise ValueError(f"cannot build {tf} from {src_tf}")
    server = utc_to_server(df["time"])
    g = df.assign(_b=server.dt.floor(f"{step}min").to_numpy())
    named = dict(open=("open", "first"), high=("high", "max"), low=("low", "min"), close=("close", "last"),
                 volume=("volume", "sum"), t_first=("time", "first"), t_last=("time", "last"))
    if "spread" in df.columns:
        named["spread"] = ("spread", "last")
    r = g.groupby("_b", sort=True).agg(**named).reset_index()
    out = pd.DataFrame({"time": server_to_utc(r["_b"])})
    for c in ("open", "high", "low", "close", "volume") + (("spread",) if "spread" in r.columns else ()):
        out[c] = r[c]
    if drop_partial_edges and len(out) and step > src_step:
        keep = np.ones(len(out), bool)
        if r["t_first"].iloc[0] > out["time"].iloc[0]:
            keep[0] = False
        if r["t_last"].iloc[-1] + pd.Timedelta(minutes=src_step) < out["time"].iloc[-1] + pd.Timedelta(minutes=step):
            keep[-1] = False
        out = out[keep]
    return out.reset_index(drop=True)


# ══ 4. validation against MT5 ════════════════════════════════════════════════════════

@dataclass
class Tolerances:
    """Pass/fail limits. Price differences are expressed in units of the MT5 median bar range (high-low) of that
    timeframe, so one setting works for EURUSD and XAUUSD. DEFAULTS ARE REASONED, NOT CALIBRATED against a real vendor
    file (none was available when written): look at the printed metrics on your first real file, and tighten or
    justify any change - do not loosen limits just to make a file pass."""
    min_overlap_bars: dict = field(default_factory=lambda: {"M5": 5000, "M15": 1500, "H1": 500, "H4": 150})
    max_missing_frac: float = 0.05         # MT5 bars (in the shared window) absent from the import
    max_extra_frac: float = 0.05           # import bars (in the shared window) absent from MT5
    med_close_range_frac: float = 0.10     # median |close_imp - close_mt5| / median range
    p99_close_range_frac: float = 1.00     # 99th percentile of the same
    med_hl_range_frac: float = 0.15        # median |high/low difference| / median range
    max_bias_range_frac: float = 0.10      # |median signed close diff| / median range  (bid/ask/mid mix-up)
    warn_bias_range_frac: float = 0.04
    min_return_corr: float = 0.97          # corr of consecutive close-to-close changes
    max_scale_dev: float = 0.002           # |median(close ratio) - 1|
    max_lag_hours: int = 12                # time-shift search window
    lag_gain: float = 0.05                 # a shifted copy must beat zero-shift correlation by this to count as a shift
    min_season_frac: float = 0.15          # each of summer/winter needs this share of min_overlap_bars to be judged


@dataclass
class Check:
    name: str
    status: str          # pass | warn | fail | skip
    value: object = None
    limit: object = None
    msg: str = ""


@dataclass
class TfValidation:
    tf: str
    ok: bool
    overlap_bars: int
    enough_overlap: bool
    checks: list = field(default_factory=list)
    metrics: dict = field(default_factory=dict)

    def failures(self):
        return [c for c in self.checks if c.status == "fail"]


def _return_series(close: pd.Series, step: pd.Timedelta) -> pd.Series:
    d = close.diff()
    consecutive = close.index.to_series().diff() == step
    return d.where(consecutive.to_numpy())


def _corr(a: pd.Series, b: pd.Series) -> tuple[float, int]:
    j = pd.concat([a, b], axis=1, join="inner").dropna()
    if len(j) < 200 or j.iloc[:, 0].std() == 0 or j.iloc[:, 1].std() == 0:
        return float("nan"), len(j)
    return float(np.corrcoef(j.iloc[:, 0], j.iloc[:, 1])[0, 1]), len(j)


def validate_bars(imp: pd.DataFrame, ref: pd.DataFrame, tf: str, tol: Optional[Tolerances] = None) -> TfValidation:
    """Compare imported `tf` bars with real MT5 `tf` bars (both UTC cache schema) over their shared window."""
    tol = tol or Tolerances()
    step = pd.Timedelta(minutes=TF_MINUTES[tf])
    imp = imp.drop_duplicates("time").set_index("time").sort_index()
    ref = ref.drop_duplicates("time").set_index("time").sort_index()
    v = TfValidation(tf=tf, ok=True, overlap_bars=0, enough_overlap=False)
    if imp.empty or ref.empty:
        v.checks.append(Check("overlap", "skip", 0, tol.min_overlap_bars[tf], "no bars on one side"))
        return v
    lo, hi = max(imp.index[0], ref.index[0]), min(imp.index[-1], ref.index[-1])
    if lo > hi:
        v.checks.append(Check("overlap", "skip", 0, tol.min_overlap_bars[tf], "import and MT5 windows do not intersect"))
        return v
    ri, rr = imp.loc[lo:hi], ref.loc[lo:hi]
    common = ri.index.intersection(rr.index)
    v.overlap_bars = int(len(common))
    need = tol.min_overlap_bars[tf]
    v.enough_overlap = len(common) >= need
    if not v.enough_overlap:
        v.checks.append(Check("overlap", "skip", len(common), need,
                              f"only {len(common)} shared {tf} bars (< {need}); cannot validate this timeframe"))
        return v

    def add(name, ok, value, limit, msg, warn=False):
        v.checks.append(Check(name, "pass" if ok else ("warn" if warn else "fail"), value, limit, msg))

    # (a) bar alignment / holes
    miss = 1 - len(common) / max(len(rr), 1)
    extra = 1 - len(common) / max(len(ri), 1)
    add("bars_missing_in_import", miss <= tol.max_missing_frac, round(miss, 4), tol.max_missing_frac,
        f"{miss:.1%} of MT5 {tf} bars in the shared window are absent from the import")
    add("bars_extra_in_import", extra <= tol.max_extra_frac, round(extra, 4), tol.max_extra_frac,
        f"{extra:.1%} of imported {tf} bars do not exist at MT5 (weekend/holiday fills or a time shift)")

    a, b = ri.loc[common], rr.loc[common]
    R = float((b["high"] - b["low"]).median())
    if not R > 0:
        v.checks.append(Check("range", "fail", R, ">0", "MT5 median bar range is zero")); v.ok = False
        return v
    dc = (a["close"] - b["close"]).to_numpy()
    med_abs, p99 = float(np.median(np.abs(dc))) / R, float(np.percentile(np.abs(dc), 99)) / R
    bias = float(np.median(dc)) / R
    hl = float(np.median(np.r_[np.abs(a["high"] - b["high"]).to_numpy(), np.abs(a["low"] - b["low"]).to_numpy()])) / R
    ratio = float(np.median(a["close"].to_numpy() / b["close"].to_numpy()))
    v.metrics.update(median_range=R, median_abs_close_diff_over_range=med_abs, p99_abs_close_diff_over_range=p99,
                     bias_over_range=bias, median_hl_diff_over_range=hl, close_ratio=ratio, common_bars=len(common))
    add("scale", abs(ratio - 1) <= tol.max_scale_dev, round(ratio, 6), f"1 +- {tol.max_scale_dev}",
        f"median close ratio import/MT5 = {ratio:.5f} (different instrument, inverted pair, or price scaling)")
    add("median_close_diff", med_abs <= tol.med_close_range_frac, round(med_abs, 4), tol.med_close_range_frac,
        f"median |close diff| = {med_abs:.3f} x median {tf} range")
    add("p99_close_diff", p99 <= tol.p99_close_range_frac, round(p99, 4), tol.p99_close_range_frac,
        f"99th pct |close diff| = {p99:.3f} x median {tf} range")
    add("median_hl_diff", hl <= tol.med_hl_range_frac, round(hl, 4), tol.med_hl_range_frac,
        f"median |high/low diff| = {hl:.3f} x median range")
    if abs(bias) > tol.max_bias_range_frac:
        add("bias", False, round(bias, 4), tol.max_bias_range_frac,
            f"signed close bias {bias:+.3f} x range: import sits {'above' if bias > 0 else 'below'} MT5 "
            f"(ask/mid file labelled as bid, or a different feed)")
    else:
        add("bias", abs(bias) <= tol.warn_bias_range_frac, round(bias, 4), tol.warn_bias_range_frac,
            f"signed close bias {bias:+.3f} x range", warn=True)

    # (b) co-movement and time-shift search
    rret, iret = _return_series(rr["close"], step), _return_series(ri["close"], step)
    c0, n0 = _corr(rret, iret)
    add("return_corr", (not np.isnan(c0)) and c0 >= tol.min_return_corr, round(c0, 4), tol.min_return_corr,
        f"corr of consecutive close changes = {c0:.4f} on {n0} pairs")
    best_k, best_c = 0, c0
    if not np.isnan(c0) and c0 < 0.999:
        for k in range(-tol.max_lag_hours, tol.max_lag_hours + 1):
            if k == 0:
                continue
            sh = iret.copy()
            sh.index = sh.index + pd.Timedelta(hours=k)
            ck, _ = _corr(rret, sh)
            if not np.isnan(ck) and ck > best_c:
                best_k, best_c = k, ck
    v.metrics.update(corr_zero_shift=c0, best_shift_hours=best_k, corr_best_shift=best_c)
    shifted = best_k != 0 and (np.isnan(c0) or best_c - c0 > tol.lag_gain)
    add("time_shift", not shifted, best_k, 0,
        (f"the import matches MT5 best when its timestamps are moved by {best_k:+d}h (corr {best_c:.3f} vs {c0:.3f}): "
         f"the file's timezone is mis-specified") if shifted else "no time shift detected")

    # (c) DST: summer vs winter must agree (a wrong DST rule fails one season only)
    ny_dst = ny_is_dst(a.index)
    need_season = max(200, int(tol.min_season_frac * need))
    for label, mask in (("summer_dst", ny_dst), ("winter", ~ny_dst)):
        n = int(mask.sum())
        if n < need_season:
            v.checks.append(Check(f"season_{label}", "skip", n, need_season, f"only {n} bars in {label}; not judged"))
            continue
        d = np.abs(dc[mask]) / R
        ok_s = np.median(d) <= tol.med_close_range_frac
        sub_idx = a.index[mask]
        cs, _ = _corr(rret.reindex(sub_idx), iret.reindex(sub_idx))
        ok_c = (not np.isnan(cs)) and cs >= tol.min_return_corr
        add(f"season_{label}", bool(ok_s and ok_c), {"median_diff": round(float(np.median(d)), 4),
                                                       "corr": None if np.isnan(cs) else round(cs, 4)},
            f"<= {tol.med_close_range_frac} / >= {tol.min_return_corr}",
            f"{label}: median |diff| {np.median(d):.3f} x range, corr {cs:.4f} on {n} bars"
            + ("" if (ok_s and ok_c) else "  <- DST handling differs between seasons"))

    v.ok = not v.failures()
    return v


# ══ 5. orchestration ═════════════════════════════════════════════════════════════════

@dataclass
class ImportReport:
    symbol: str
    name: str
    source_file: str
    source_tf: str
    tz: str
    price_side: str
    committed: bool = False
    ok: bool = False
    reasons: list = field(default_factory=list)       # why it was refused
    notes: list = field(default_factory=list)
    source_quality: dict = field(default_factory=dict)
    tf: dict = field(default_factory=dict)            # tf -> {validation, merge stats}
    spread_mode: str = ""

    def text(self) -> str:
        L = [f"IMPORT {self.symbol} <- {self.source_file}  [{self.source_tf}, tz={self.tz}, side={self.price_side}]",
             f"  verdict: {'ACCEPTED' if self.ok else 'REFUSED'}{' (committed)' if self.committed else ' (dry run)' if self.ok else ''}"]
        for r in self.reasons:
            L.append(f"  REFUSED because: {r}")
        for n in self.notes:
            L.append(f"  note: {n}")
        for tf, d in self.tf.items():
            val: TfValidation = d.get("validation")
            if val is None:
                continue
            L.append(f"  [{tf}] overlap={val.overlap_bars} bars  ok={val.ok}  enough={val.enough_overlap}")
            for c in val.checks:
                L.append(f"      {c.status.upper():5s} {c.name}: {c.msg}")
            if d.get("merge"):
                L.append(f"      merge: {d['merge']}")
        return "\n".join(L)


def _file_sha256(path: Path, limit_mb: int = 2048) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(8 << 20):
            h.update(chunk)
    return h.hexdigest()


def build_timeframes(src: pd.DataFrame, tfs: Sequence[str] = IMPORT_TFS) -> dict:
    src_tf = infer_tf(src["time"])
    out = {}
    for tf in tfs:
        if TF_MINUTES[tf] < TF_MINUTES[src_tf]:
            continue
        out[tf] = src.copy() if tf == src_tf else resample_bars(src, tf)
    return out


def import_external(symbol: str, path, *, tz: str, price_side: str = "bid", fmt: str = "auto",
                    ask_path=None, name: Optional[str] = None, data_dir: Path = DATA_DIR,
                    tfs: Sequence[str] = IMPORT_TFS, tol: Optional[Tolerances] = None,
                    spread_scale: float = 1.0, spread_fixed_pts: Optional[float] = None,
                    time_format: Optional[str] = None, commit: bool = False, raise_on_refuse: bool = True) -> ImportReport:
    """
    Read, convert, validate and (only if commit=True and everything passed) merge a vendor file into the cache.
    Dry run by default. Returns the ImportReport; with commit=True a failed validation raises ImportRefused.
    """
    path = Path(path)
    data_dir = Path(data_dir)
    tol = tol or Tolerances()
    name = name or path.stem
    src = read_external(path, fmt=fmt, tz=tz, time_format=time_format)
    src_tf = infer_tf(src["time"])
    rep = ImportReport(symbol=symbol, name=name, source_file=str(path), source_tf=src_tf, tz=tz, price_side=price_side)
    if src_tf not in ("M1", "M5"):
        rep.reasons.append(f"source timeframe {src_tf} unsupported (provide M1 or M5)")
        return _finish(rep, commit, raise_on_refuse)
    specs = load_specs(data_dir) if (data_dir / "specs.json").exists() else {}
    point = specs.get(symbol, {}).get("point")

    # reference MT5 M5 (real rows only) for the spread profile
    m5_path = cache_path(symbol, "M5", data_dir)
    mt5_m5 = pd.read_parquet(m5_path) if m5_path.exists() else None
    profile = hourly_spread_profile(mt5_m5) if mt5_m5 is not None else None

    # bid / ask / spread
    if ask_path is not None:
        if price_side != "bid":
            raise ValueError("when ask_path is given, the main file must be the BID file (price_side='bid')")
        if point is None:
            raise ValueError(f"no point size for {symbol} in specs.json (needed to derive spread from bid/ask)")
        ask = read_external(ask_path, fmt=fmt, tz=tz, time_format=time_format)
        src = pair_bid_ask(src, ask, point)
        rep.spread_mode = "derived from vendor bid/ask closes (flagged synthetic)"
    else:
        if price_side != "bid":
            if point is None:
                raise ValueError("price_side != bid needs the symbol's point size in specs.json")
            sp_pts = synthesize_spread(src["time"], profile, spread_scale, spread_fixed_pts)
            src = to_bid(src, price_side, sp_pts * point)
            rep.notes.append(f"{price_side} prices converted to bid with a synthetic spread (MT5 hourly median); "
                             f"conversion error is of the order of the spread")
        if "spread" in src.columns:
            src = src.drop(columns=["spread"])
            rep.notes.append("spread column in the vendor file ignored (not the MT5 broker's spread)")
        rep.spread_mode = (f"fixed {spread_fixed_pts} pts" if spread_fixed_pts is not None
                           else f"MT5 hour-of-day median non-zero spread x {spread_scale}")

    # source-level quality
    q = quality_report(src.drop(columns=["spread"], errors="ignore"), tf=src_tf, symbol=symbol)
    rep.source_quality = {"ok": q["ok"], "metrics": {k: (str(v) if isinstance(v, pd.Timestamp) else v)
                                                     for k, v in q["metrics"].items()},
                          "issues": q["issues"]}
    for sev, msg in q["issues"]:
        if sev == "fail":
            rep.reasons.append(f"source data quality: {msg}")
        elif sev == "warn":
            rep.notes.append(f"source data quality: {msg}")
    if src.attrs.get("dropped_ambiguous_times"):
        rep.notes.append(f"{src.attrs['dropped_ambiguous_times']} ambiguous local times dropped (DST fall-back hour)")

    # timeframes + validation
    frames = build_timeframes(src, tfs)
    n_validated = 0
    for tf, fr in frames.items():
        ref_path = cache_path(symbol, tf, data_dir)
        ref = pd.read_parquet(ref_path) if ref_path.exists() else None
        if ref is not None and "src" in ref.columns:
            ref = ref[ref["src"] == MT5_SRC]
        d: dict = {"bars": len(fr)}
        if ref is None or ref.empty:
            d["validation"] = TfValidation(tf=tf, ok=True, overlap_bars=0, enough_overlap=False,
                                           checks=[Check("overlap", "skip", 0, tol.min_overlap_bars[tf], "no MT5 cache for this timeframe")])
        else:
            d["validation"] = validate_bars(fr, ref, tf, tol)
        val = d["validation"]
        if val.enough_overlap and val.ok:
            n_validated += 1
        for c in val.failures():
            rep.reasons.append(f"{tf} validation: {c.name} - {c.msg}")
        rep.tf[tf] = d
    if n_validated == 0 and not any(r.startswith(tuple(IMPORT_TFS)) for r in rep.reasons):
        rep.reasons.append("no timeframe had enough overlap with MT5 bars to validate against (need e.g. >= "
                           f"{tol.min_overlap_bars['M5']} shared M5 bars): include a few weeks inside the MT5 window "
                           f"in the file")
    unvalidated = [tf for tf, d in rep.tf.items() if not d["validation"].enough_overlap]
    if unvalidated and n_validated:
        rep.notes.append(f"{unvalidated} had too little MT5 overlap to validate separately; they inherit trust from "
                         f"the validated timeframes (same source, same clock)")
    rep.ok = not rep.reasons
    if rep.ok:
        rep.notes.append("validation covers only the span that overlaps MT5 data; older years are assumed to share the "
                         "same vendor clock/quality (spot-check them against the H1/H4 cache where it reaches back)")

    # merge
    if rep.ok:
        for tf, fr in frames.items():
            fr = fr.copy()
            spread = synthesize_spread(fr["time"], profile, spread_scale, spread_fixed_pts) \
                if "spread" not in fr.columns else fr["spread"].to_numpy(float)
            fr["spread"] = np.maximum(1, np.rint(spread))
            fr["spread_synth"] = True
            fr["src"] = f"import:{name}"
            _, st = append_bars(symbol, tf, fr, data_dir, prefer="old", only_outside=True, dry_run=not commit)
            rep.tf[tf]["merge"] = {k: (round(v, 1) if isinstance(v, float) else v) for k, v in st.items()}
            if st["hole_minutes"]:
                rep.notes.append(f"{tf}: {st['hole_minutes'] / 1440:.1f} days between the import and the MT5 span "
                                 f"are missing from both (file does not reach the MT5 window?)")
        rep.committed = bool(commit)
        if commit:
            log_p = data_dir / "import_log.json"
            log = _read_json(log_p) if log_p.exists() else {}
            log.setdefault("imports", []).append({
                "when_utc": datetime.now(timezone.utc).isoformat(), "symbol": symbol, "name": name,
                "file": str(path), "sha256": _file_sha256(path), "source_tf": src_tf, "tz": tz,
                "price_side": price_side, "spread_mode": rep.spread_mode,
                "timeframes": {tf: {"bars_in": d["bars"], **d.get("merge", {}),
                                    "validated": rep.tf[tf]["validation"].enough_overlap} for tf, d in rep.tf.items()},
            })
            log_p.write_text(json.dumps(log, indent=2, default=str))
    return _finish(rep, commit, raise_on_refuse)


def _finish(rep: ImportReport, commit: bool, raise_on_refuse: bool) -> ImportReport:
    if not rep.ok and not rep.reasons:
        rep.reasons.append("unspecified")
    if rep.reasons:
        rep.ok = False
        if commit and raise_on_refuse:
            raise ImportRefused("import refused:\n  - " + "\n  - ".join(rep.reasons), rep)
    return rep
