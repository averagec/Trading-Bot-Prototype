"""
Historical data layer.

* fetch_history()  - READ-ONLY pull of OHLCV + per-bar spread + symbol specs from the
                     running MT5 terminal. Places no orders, uses no credentials
                     (mt5.initialize() with no args attaches to the terminal's logged-in account).
                     APPEND mode (default): every fetch is merged into the cached parquet and de-duplicated,
                     so history accumulates even though the terminal only serves its newest N bars.
* merge_bars()     - the de-duplicating merge used by fetch and by the third-party importer (data_import.py).
* load_history()   - load cached parquet as a UTC-indexed DataFrame.
* server_to_utc() / utc_to_server() - broker clock <-> UTC using the NY-close convention (UTC+2 winter / UTC+3 US-DST).
* prepare_bars()   - bars + usable `spread_pts` (zero/unrecorded spreads imputed; synthetic spreads flagged) and,
                     for every run, a one-time data-quality log line per symbol/timeframe (data_quality.py).
* coverage_report()- what the cache actually contains, plus clock sanity checks.

Cache schema (research/data/<SYMBOL>_<TF>.parquet):
    time[UTC ns], open, high, low, close, volume(uint64), spread(int32, points)
and, ONLY once third-party bars were merged in, two extra columns:
    src            "mt5" or "import:<name>"
    spread_synth   True where `spread` is a synthetic stand-in, not a broker-recorded value
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

ROOT      = Path(__file__).resolve().parent
DATA_DIR  = ROOT / "data"
TFS       = ["M5", "M15", "H1", "H4"]
TF_MINUTES = {"M1": 1, "M5": 5, "M15": 15, "H1": 60, "H4": 240}

BASE_COLS  = ["time", "open", "high", "low", "close", "volume", "spread"]
EXTRA_COLS = ["src", "spread_synth"]
MT5_SRC    = "mt5"


# ── Clock handling ───────────────────────────────────────────────────────────────

def ny_is_dst(utc_ts) -> np.ndarray:
    """Vectorised 'is US daylight saving in force at this UTC instant' (tz-aware UTC Series/Index -> bool ndarray)."""
    s = pd.Series(pd.to_datetime(utc_ts))
    ny_wall = s.dt.tz_convert("America/New_York").dt.tz_localize(None)
    offset = ny_wall - s.dt.tz_convert("UTC").dt.tz_localize(None)        # -4h in DST, -5h otherwise
    return (offset > pd.Timedelta(hours=-4, minutes=-30)).to_numpy()


def server_to_utc(server_ts: pd.Series) -> pd.Series:
    """
    MT5 returns bar times as *broker server time* encoded as if it were UTC.
    FTMO-style servers follow the NY-close convention: UTC+2 in winter, UTC+3 while
    US daylight saving is in force. Returns tz-aware UTC timestamps.
    """
    naive = pd.to_datetime(server_ts, unit="s") if np.issubdtype(server_ts.dtype, np.integer)         else pd.to_datetime(server_ts)
    cand3 = (naive - pd.Timedelta(hours=3)).dt.tz_localize("UTC")
    dst3  = pd.Series(ny_is_dst(cand3), index=cand3.index)
    cand2 = (naive - pd.Timedelta(hours=2)).dt.tz_localize("UTC")
    return cand3.where(dst3, cand2)


def utc_to_server(utc_ts) -> pd.Series:
    """
    Inverse of server_to_utc: tz-aware UTC -> naive 'broker wall clock' (the representation MT5 uses).
    Used to bucket bars on the broker's own H1/H4/D1 boundaries. The only ambiguous instants are the
    ~1h around the US DST switch on Sunday morning, when FX is closed (no bars exist there).
    """
    s = pd.Series(pd.to_datetime(utc_ts))
    if s.dt.tz is None:
        raise ValueError("utc_to_server needs tz-aware UTC timestamps")
    s = s.dt.tz_convert("UTC")
    off = pd.to_timedelta(np.where(ny_is_dst(s), 3, 2), unit="h")
    return s.dt.tz_localize(None) + pd.Series(off, index=s.index)


# ── Frame normalisation / merge ───────────────────────────────────────────────────

def rates_to_frame(rates) -> pd.DataFrame:
    """MT5 structured array (copy_rates_*) -> cache-schema DataFrame with UTC times. Pure function (no MT5 needed)."""
    df = pd.DataFrame(rates)
    df["time"] = server_to_utc(df["time"].astype("int64"))
    df = df.rename(columns={"tick_volume": "volume"})
    return normalize_bars(df[BASE_COLS])


def normalize_bars(df: pd.DataFrame) -> pd.DataFrame:
    """Canonical dtypes, sorted by time, one row per timestamp (last wins). Extra flag columns are kept if present."""
    out = df.copy()
    t = pd.to_datetime(out["time"])
    if t.dt.tz is None:
        raise ValueError("bar times must be tz-aware UTC")
    out["time"] = t.dt.tz_convert("UTC").astype("datetime64[ns, UTC]")
    for c in ("open", "high", "low", "close"):
        out[c] = out[c].astype("float64")
    out["volume"] = np.rint(out["volume"].astype("float64")).clip(lower=0).astype("uint64")
    out["spread"] = np.rint(out["spread"].astype("float64")).astype("int32")
    if "src" in out.columns:
        out["src"] = out["src"].astype(str)
    if "spread_synth" in out.columns:
        out["spread_synth"] = out["spread_synth"].astype(bool)
    cols = BASE_COLS + [c for c in EXTRA_COLS if c in out.columns]
    out = out[cols].drop_duplicates("time", keep="last").sort_values("time").reset_index(drop=True)
    return out


def _with_extras(df: pd.DataFrame, want_extras: bool, default_src: str) -> pd.DataFrame:
    if not want_extras:
        return df
    df = df.copy()
    if "src" not in df.columns:
        df["src"] = default_src
    if "spread_synth" not in df.columns:
        df["spread_synth"] = False
    return df


def merge_bars(old: Optional[pd.DataFrame], new: pd.DataFrame, prefer: str = "new",
               tf: str = "M5", only_outside: bool = False) -> tuple[pd.DataFrame, dict]:
    """
    Merge `new` bars into `old`, de-duplicating on bar time. Idempotent: merging the same bars twice changes nothing.

    prefer       "new" (a fresh broker fetch replaces the cached copy of an overlapping bar) or
                 "old" (third-party data never overwrites broker data).
    only_outside True -> keep only `new` bars strictly before old's first / after old's last bar, i.e. never
                 fill holes inside the existing span (used for third-party imports).
    Returns (merged, stats). stats: n_old, n_new_in, n_added, n_overlap, n_changed (overlap rows whose OHLC differ),
    hole_minutes (>0 when the two ranges do not touch, i.e. bars between them are missing from both).
    """
    if prefer not in ("new", "old"):
        raise ValueError("prefer must be 'new' or 'old'")
    new = normalize_bars(new)
    if old is None or len(old) == 0:
        stats = dict(n_old=0, n_new_in=len(new), n_added=len(new), n_overlap=0, n_changed=0, hole_minutes=0.0)
        return new, stats
    old = normalize_bars(old)
    step = pd.Timedelta(minutes=TF_MINUTES[tf])
    if only_outside:
        new = new[(new["time"] < old["time"].iloc[0]) | (new["time"] > old["time"].iloc[-1])]

    both = old.merge(new[["time", "open", "high", "low", "close"]], on="time", suffixes=("", "_n"))
    n_changed = int(((both[["open", "high", "low", "close"]].to_numpy()
                      - both[["open_n", "high_n", "low_n", "close_n"]].to_numpy()) != 0).any(axis=1).sum()) \
        if len(both) else 0
    n_overlap = len(both)

    # holes between the two ranges (no overlap -> bars between them are in neither frame)
    hole = 0.0
    if len(new):
        if new["time"].iloc[0] > old["time"].iloc[-1]:
            gap = new["time"].iloc[0] - old["time"].iloc[-1]
        elif new["time"].iloc[-1] < old["time"].iloc[0]:
            gap = old["time"].iloc[0] - new["time"].iloc[-1]
        else:
            gap = pd.Timedelta(0)
        if gap > step * 1.5:
            from research.data_quality import _classify_gap      # weekend / holiday / session breaks are not holes
            a_t, b_t = ((old["time"].iloc[-1], new["time"].iloc[0]) if new["time"].iloc[0] > old["time"].iloc[-1]
                        else (new["time"].iloc[-1], old["time"].iloc[0]))
            hole = float(gap / pd.Timedelta(minutes=1)) if _classify_gap(a_t, b_t) == "unexplained" else 0.0

    extras = any(c in df.columns for df in (old, new) for c in EXTRA_COLS)
    old_x = _with_extras(old, extras, MT5_SRC)
    new_x = _with_extras(new, extras, MT5_SRC)
    parts = [old_x, new_x] if prefer == "new" else [new_x, old_x]
    merged = pd.concat(parts, ignore_index=True)
    merged = merged.drop_duplicates("time", keep="last").sort_values("time").reset_index(drop=True)
    merged = normalize_bars(merged)
    stats = dict(n_old=len(old), n_new_in=len(new), n_added=len(merged) - len(old), n_overlap=n_overlap,
                 n_changed=n_changed, hole_minutes=hole)
    return merged, stats


def save_bars(df: pd.DataFrame, path: Path) -> None:
    """Atomic parquet write (tmp file + replace) so an interrupted run can never leave a truncated cache."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".parquet.tmp")
    df.to_parquet(tmp, index=False)
    os.replace(tmp, path)


def cache_path(symbol: str, tf: str, data_dir: Path = DATA_DIR) -> Path:
    return Path(data_dir) / f"{symbol}_{tf}.parquet"


def append_bars(symbol: str, tf: str, new: pd.DataFrame, data_dir: Path = DATA_DIR,
                prefer: str = "new", only_outside: bool = False, dry_run: bool = False) -> tuple[pd.DataFrame, dict]:
    """Merge `new` into the cached parquet for symbol/tf (creating it if absent) and save. Returns (merged, stats)."""
    p = cache_path(symbol, tf, data_dir)
    old = pd.read_parquet(p) if p.exists() else None
    merged, stats = merge_bars(old, new, prefer=prefer, tf=tf, only_outside=only_outside)
    if not dry_run:
        save_bars(merged, p)
    return merged, stats


# ── Fetch (MT5, read-only) ───────────────────────────────────────────────────────

def _spec_from_info(info) -> dict:
    return {
        "digits":        int(info.digits),
        "point":         float(info.point),
        "tick_size":     float(info.trade_tick_size),
        "tick_value":    float(info.trade_tick_value),
        "contract_size": float(info.trade_contract_size),
        "volume_min":    float(info.volume_min),
        "volume_max":    float(info.volume_max),
        "volume_step":   float(info.volume_step),
        "stops_level":   int(info.trade_stops_level),
        "currency_base":   str(info.currency_base),
        "currency_profit": str(info.currency_profit),
        "spread_now":    int(info.spread),
    }


# Bars requested per timeframe. The terminal rejects counts >= its "Max bars in chart" (default 100000), so M5 is
# capped at ~16 months unless that MT5 option is raised. Higher timeframes only need warm-up depth before M5 starts.
BARS_WANTED = {"M5": 99_999, "M15": 40_000, "H1": 12_000, "H4": 5_000}
TAIL_OVERLAP_BARS = 600          # re-fetched bars before the cached end in "tail" mode (detects broker revisions)


def _read_json(p: Path) -> dict:
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def fetch_history(symbols: Iterable[str], tfs: Iterable[str] = TFS,
                  bars_wanted: Optional[dict] = None, out_dir: Path = DATA_DIR,
                  append: bool = True, mode: str = "full", max_depth: bool = False) -> dict:
    """
    Pull history from the terminal: closed bars only, position 1 onward (same as the live bot).

    append     True (default): merge into the existing cache and de-duplicate, so the cache only ever grows.
               False: legacy behaviour - overwrite the cache with whatever the terminal serves now.
    mode       "full": request `bars_wanted` bars (re-covers everything the terminal holds; also back-fills if the
               terminal's Max-bars limit was raised).  "tail": request only the bars since the cached end plus
               TAIL_OVERLAP_BARS of overlap (cheap; for scheduled incremental runs). tail needs append=True.
    max_depth  ignore `bars_wanted` and ask for (terminal Max-bars - 1) bars of every timeframe, i.e. as deep as
               the terminal allows. Use after raising 'Max bars in chart'.
    The broker-time -> UTC conversion is the same server_to_utc() as ever, applied per fetch to the raw timestamps,
    so appended bars are on exactly the same clock as the cached ones.
    """
    if mode not in ("full", "tail"):
        raise ValueError("mode must be 'full' or 'tail'")
    if mode == "tail" and not append:
        raise ValueError("tail mode requires append=True")
    bars_wanted = {**BARS_WANTED, **(bars_wanted or {})}
    import MetaTrader5 as mt5

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if not mt5.initialize():
        raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()} "
                           f"(open the MT5 terminal and log in first)")
    tf_const = {"M5": mt5.TIMEFRAME_M5, "M15": mt5.TIMEFRAME_M15,
                "H1": mt5.TIMEFRAME_H1, "H4": mt5.TIMEFRAME_H4}
    meta: dict = _read_json(out_dir / "meta.json") if append else {}
    meta.setdefault("symbols", {})
    meta["fetched_at_utc"] = datetime.now(timezone.utc).isoformat()
    meta["append_mode"] = bool(append)
    try:
        ti = mt5.terminal_info()
        terminal_max = int(getattr(ti, "maxbars", 0) or 0) if ti is not None else 0
        meta["terminal_maxbars"] = terminal_max or None
        if max_depth:
            if terminal_max <= 1:
                raise RuntimeError("max_depth requested but terminal Max-bars is unknown")
            bars_wanted = {tf: terminal_max - 1 for tf in bars_wanted}
        acct = mt5.account_info()
        meta["account_currency"] = acct.currency if acct else None
        tick_ref = mt5.symbol_info_tick("EURUSD")
        if tick_ref is not None:
            diff_h = (tick_ref.time - datetime.now(timezone.utc).timestamp()) / 3600
            meta["live_server_offset_hours_estimate"] = round(diff_h, 2)

        specs: dict = _read_json(out_dir / "specs.json") if append else {}
        for sym in symbols:
            if not mt5.symbol_select(sym, True):
                logger.warning("%s: symbol_select failed (%s)", sym, mt5.last_error())
            info = mt5.symbol_info(sym)
            if info is None:
                logger.error("%s: no symbol info - skipped", sym)
                continue
            specs[sym] = _spec_from_info(info)
            meta["symbols"].setdefault(sym, {})

            for tf in tfs:
                n = int(bars_wanted[tf])
                p = cache_path(sym, tf, out_dir)
                if mode == "tail" and p.exists():
                    last = pd.read_parquet(p, columns=["time"])["time"].iloc[-1]
                    gap_min = max(0.0, (pd.Timestamp.now(tz="UTC") - last) / pd.Timedelta(minutes=1))
                    n = min(n, int(gap_min // TF_MINUTES[tf]) + TAIL_OVERLAP_BARS)
                # position 1 => exclude the still-forming bar, exactly like the live bot
                rates = mt5.copy_rates_from_pos(sym, tf_const[tf], 1, n)
                if rates is None or len(rates) == 0:
                    logger.error("%s %s: no data (%s)", sym, tf, mt5.last_error())
                    continue
                fresh = rates_to_frame(rates)
                if append:
                    df, st = append_bars(sym, tf, fresh, out_dir, prefer="new")
                    if st["n_changed"]:
                        logger.warning("%s %s: %d overlapping bars differ from the cached copy (broker revised "
                                       "history); newer fetch kept", sym, tf, st["n_changed"])
                    if st["hole_minutes"]:
                        logger.warning("%s %s: the fetched window does not touch the cache - about %.1f days of "
                                       "bars are missing between them (the terminal cap slid past the cache end; "
                                       "fetch more often or raise Max bars)", sym, tf, st["hole_minutes"] / 1440)
                    logger.info("%s %s: +%d new bars, %d overlap (%d changed) -> %d total  %s -> %s", sym, tf,
                                st["n_added"], st["n_overlap"], st["n_changed"], len(df),
                                df["time"].iloc[0].date(), df["time"].iloc[-1].date())
                else:
                    df, st = fresh, {}
                    save_bars(df, p)
                    logger.info("%s %s: %d bars  %s -> %s", sym, tf, len(df),
                                df["time"].iloc[0].date(), df["time"].iloc[-1].date())
                meta["symbols"][sym][tf] = {
                    "bars": int(len(df)),
                    "first": df["time"].iloc[0].isoformat(),
                    "last":  df["time"].iloc[-1].isoformat(),
                    "added_last_fetch": int(st.get("n_added", len(df))),
                }
        (out_dir / "specs.json").write_text(json.dumps(specs, indent=2))
        (out_dir / "meta.json").write_text(json.dumps(meta, indent=2))
    finally:
        mt5.shutdown()
    return meta


# ── Load ──────────────────────────────────────────────────────────────────────────

def load_history(symbol: str, tf: str, data_dir: Path = DATA_DIR,
                 start: Optional[str] = None, end: Optional[str] = None) -> pd.DataFrame:
    p = Path(data_dir) / f"{symbol}_{tf}.parquet"
    if not p.exists():
        raise FileNotFoundError(f"{p} missing - run `python -m research.fetch_data` first")
    df = pd.read_parquet(p)
    if start:
        df = df[df["time"] >= pd.Timestamp(start, tz="UTC")]
    if end:
        df = df[df["time"] < pd.Timestamp(end, tz="UTC")]
    return df.reset_index(drop=True)


def load_specs(data_dir: Path = DATA_DIR) -> dict:
    return json.loads((Path(data_dir) / "specs.json").read_text())


_QUALITY_SEEN: set = set()


def log_quality_once(df: pd.DataFrame, symbol: str, tf: str) -> None:
    """Run the data-quality report once per (symbol, tf, span) per process and log the verdict. Never raises."""
    key = (symbol, tf, len(df), str(df["time"].iloc[-1]) if len(df) else "")
    if key in _QUALITY_SEEN:
        return
    _QUALITY_SEEN.add(key)
    try:
        from research.data_quality import quality_report, summary_line
        rep = quality_report(df, tf=tf, symbol=symbol)
        line = summary_line(rep)
        (logger.warning if any(s == "fail" for s, _ in rep["issues"]) else logger.info)("data quality | %s", line)
        for sev, msg in rep["issues"]:
            if sev in ("fail", "warn"):
                logger.log(logging.WARNING if sev == "fail" else logging.INFO, "data quality | %s %s: [%s] %s",
                           symbol, tf, sev, msg)
    except Exception as e:                                     # diagnostics must never break a run
        logger.debug("data quality check skipped for %s %s: %s", symbol, tf, e)


def prepare_bars(symbol: str, data_dir: Path = DATA_DIR,
                 start: Optional[str] = None, end: Optional[str] = None,
                 check_quality: bool = True) -> dict:
    """
    All four timeframes for a symbol, with a usable `spread_pts` column.
    History bars sometimes carry spread 0; those are replaced by the median non-zero spread
    for the same UTC hour (so the simulator never assumes a free spread).
    Synthetic spreads (third-party imports, `spread_synth` True) are kept as-is, flagged in `spread_synthetic`,
    and are EXCLUDED from the hourly medians so they cannot contaminate the broker-recorded statistic.
    Note: `start` trims M5 only; the higher timeframes keep their earlier history so indicators are warm.
    """
    out = {}
    for tf in TFS:
        df = load_history(symbol, tf, data_dir,
                          start=start if tf == "M5" else None, end=end)
        if check_quality and len(df):
            log_quality_once(df, symbol, tf)
        sp = df["spread"].astype(float)
        synth = df["spread_synth"].astype(bool) if "spread_synth" in df.columns \
            else pd.Series(False, index=df.index)
        nz = sp.where((sp > 0) & ~synth)
        by_hour = nz.groupby(df["time"].dt.hour).transform("median")
        df["spread_pts"] = sp.where(sp > 0, by_hour).fillna(nz.median()).fillna(0.0)
        df["spread_was_zero"] = sp <= 0
        df["spread_synthetic"] = synth.to_numpy()
        out[tf] = df
    return out


# ── Diagnostics ───────────────────────────────────────────────────────────────────

def coverage_report(symbols: Iterable[str], data_dir: Path = DATA_DIR) -> pd.DataFrame:
    """One row per symbol/timeframe: bars, span, gaps, zero-spread share, weekly-open hour check."""
    rows = []
    for sym in symbols:
        for tf in TFS:
            p = Path(data_dir) / f"{sym}_{tf}.parquet"
            if not p.exists():
                rows.append({"symbol": sym, "tf": tf, "bars": 0})
                continue
            df = pd.read_parquet(p)
            step = pd.Timedelta(minutes=TF_MINUTES[tf])
            gaps = df["time"].diff()
            weekend = (gaps > pd.Timedelta(hours=40)).sum()
            odd_gaps = ((gaps > step * 1.5) & (gaps <= pd.Timedelta(hours=40))).sum()
            # first bar after each weekend gap should open ~Sun 21:00/22:00 UTC if the clock is right
            wk = df.loc[gaps > pd.Timedelta(hours=40), "time"]
            open_hours = sorted(set(wk.dt.hour.tolist()))
            years = (df["time"].iloc[-1] - df["time"].iloc[0]).days / 365.25
            row = {
                "symbol": sym, "tf": tf, "bars": len(df),
                "first": df["time"].iloc[0].date(), "last": df["time"].iloc[-1].date(),
                "years": round(years, 2),
                "weekend_gaps": int(weekend), "other_gaps": int(odd_gaps),
                "zero_spread_pct": round(float((df["spread"] == 0).mean() * 100), 1),
                "weekly_open_utc_hours": open_hours,
            }
            if "src" in df.columns:
                row["imported_bars"] = int((df["src"] != MT5_SRC).sum())
            rows.append(row)
    return pd.DataFrame(rows)
