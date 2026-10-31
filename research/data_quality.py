"""
Data-quality report for a bar series (cache frame: time[UTC], open, high, low, close, volume, spread[, flags]).

    from research.data_quality import quality_report, format_quality, quality_table
    rep = quality_report(df, tf="M5", symbol="EURUSD")      # dict: metrics + issues [(severity, message)]
    print(format_quality(rep))

Checks: duplicates / ordering / alignment to the timeframe grid, gaps (classified weekend / holiday / unexplained),
weekend alignment (Saturday bars, bars after the Friday close, bars before the Sunday open, weekly-open hours),
zero- and synthetic-spread share (overall, by month, last 30 days), OHLC validity, flat/frozen runs, and
outliers/spikes (reverting single-bar spikes = bad ticks, huge wicks, non-reverting jumps with no time gap).

Thresholds are conservative defaults; every verdict is printed with its number so you can judge it. The report
never modifies data. Severity: "fail" = do not trust a backtest on this series until explained; "warn" = look at
it; "info" = context.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Optional

import numpy as np
import pandas as pd

TF_MIN = {"M1": 1, "M5": 5, "M15": 15, "H1": 60, "H4": 240, "D1": 1440}

# dates (month, day) where FX liquidity is thin/closed and gaps are normal
HOLIDAY_MD = {(12, 24), (12, 25), (12, 26), (12, 31), (1, 1), (1, 2)}


def _easter_holidays(years) -> set:
    """Good Friday and Easter Monday (month, day, year) - FX/metals are shut or thin around them."""
    from dateutil.easter import easter
    out = set()
    for y in years:
        e = pd.Timestamp(easter(int(y)))
        out |= {(e - pd.Timedelta(days=2)).date(), (e + pd.Timedelta(days=1)).date()}
    return out


def _classify_gap(t0: pd.Timestamp, t1: pd.Timestamp) -> str:
    """t0 = last bar before the gap, t1 = first bar after it (UTC).
    weekend | holiday (Christmas/New Year/Easter) | session (daily break or US-holiday early close: starts 17:00-23:00 UTC,
    ends 21:00-24:00 UTC, < 5h; normal for metals/CFDs) | unexplained (mid-session silence)."""
    span_h = (t1 - t0) / pd.Timedelta(hours=1)
    if t0.dayofweek == 4 and t1.dayofweek in (6, 0) and 30 <= span_h <= 72:
        return "weekend"
    days = pd.date_range(t0.normalize(), t1.normalize(), freq="D")
    if {(t.month, t.day) for t in days} & HOLIDAY_MD or {t.date() for t in days} & _easter_holidays({t0.year, t1.year}):
        return "holiday"
    t0m, t1m = t0.hour * 60 + t0.minute, t1.hour * 60 + t1.minute
    if span_h < 5 and 17 * 60 <= t0m < 23 * 60 and 21 * 60 <= t1m <= 24 * 60 - 1:
        return "session"
    return "unexplained"


def quality_report(df: pd.DataFrame, tf: str = "M5", symbol: str = "", point: Optional[float] = None,
                   spike_k: float = 20.0, top: int = 5) -> dict:
    """Return {'symbol','tf','metrics':{...},'issues':[(sev,msg)],'details':{...}}. `point` = price of 1 spread point (optional)."""
    if tf not in TF_MIN:
        raise ValueError(f"unknown timeframe {tf}")
    step_min = TF_MIN[tf]
    step = pd.Timedelta(minutes=step_min)
    m: dict = {"bars": int(len(df))}
    issues: list = []
    details: dict = {}
    if len(df) == 0:
        return {"symbol": symbol, "tf": tf, "metrics": m, "issues": [("fail", "no bars")], "details": details}

    t = pd.to_datetime(df["time"])
    if t.dt.tz is None:
        issues.append(("fail", "timestamps are not tz-aware (expected UTC)"))
        t = t.dt.tz_localize("UTC")
    t = t.dt.tz_convert("UTC")
    m["first"], m["last"] = t.iloc[0], t.iloc[-1]
    m["span_days"] = round((t.iloc[-1] - t.iloc[0]) / pd.Timedelta(days=1), 1)

    # ── ordering / duplicates / grid alignment ───────────────────────────────────
    m["duplicates"] = int(t.duplicated().sum())
    m["unsorted"] = int((t.diff() < pd.Timedelta(0)).sum())
    if step_min >= 240:      # H4/D1 bars sit on BROKER-clock boundaries (00:00, 04:00 ... server time), not UTC ones
        from research.data import utc_to_server
        grid_t = utc_to_server(t)
    else:
        grid_t = t.dt.tz_localize(None)
    epoch_min = (grid_t.astype("int64") // 60_000_000_000)
    m["misaligned"] = int((epoch_min % step_min != 0).sum())
    if m["duplicates"]:
        issues.append(("fail", f"{m['duplicates']} duplicate timestamps"))
    if m["unsorted"]:
        issues.append(("fail", f"{m['unsorted']} out-of-order timestamps"))
    if m["misaligned"]:
        issues.append(("fail", f"{m['misaligned']} bars are not on the {tf} grid (wrong timeframe or time shift; H4/D1 are checked on the broker clock)"))

    # ── gaps ──────────────────────────────────────────────────────────────────────
    tt = t.drop_duplicates().sort_values().reset_index(drop=True)
    d = tt.diff()
    gi = np.flatnonzero((d > step * 1.5).to_numpy())
    kinds = {"weekend": 0, "holiday": 0, "session": 0, "unexplained": 0}
    missing = {"weekend": 0, "holiday": 0, "session": 0, "unexplained": 0}
    unexplained = []
    for i in gi:
        k = _classify_gap(tt.iloc[i - 1], tt.iloc[i])
        n_miss = int(round(d.iloc[i] / step)) - 1
        kinds[k] += 1
        missing[k] += n_miss
        if k == "unexplained":
            unexplained.append((tt.iloc[i - 1], tt.iloc[i], n_miss))
    m["gaps_weekend"], m["gaps_holiday"], m["gaps_session"], m["gaps_unexplained"] = (
        kinds["weekend"], kinds["holiday"], kinds["session"], kinds["unexplained"])
    m["missing_bars_unexplained"] = missing["unexplained"]
    expected = max(1, int(round((tt.iloc[-1] - tt.iloc[0]) / step)))
    m["missing_unexplained_pct_of_span"] = round(100 * missing["unexplained"] / expected, 3)
    unexplained.sort(key=lambda x: -x[2])
    details["largest_unexplained_gaps"] = [(a.isoformat(), b.isoformat(), n) for a, b, n in unexplained[:top]]
    # a gap of a few bars in a quiet market happens; hours of silence mid-week do not
    big = [g for g in unexplained if g[2] * step_min >= 60]
    m["gaps_unexplained_ge_1h"] = len(big)
    if big:
        issues.append(("warn", f"{len(big)} unexplained gaps >= 1h (largest {max(g[2] for g in big)} bars, "
                               f"{missing['unexplained']} bars missing in total)"))
    elif m["missing_unexplained_pct_of_span"] > 0.5:
        issues.append(("warn", f"{m['missing_unexplained_pct_of_span']}% of expected bars missing in short gaps"))

    # ── weekend alignment ─────────────────────────────────────────────────────────
    if step_min < 1440:
        dow, hr = t.dt.dayofweek, t.dt.hour + t.dt.minute / 60
        sat = int((dow == 5).sum())
        fri_late = int(((dow == 4) & (hr >= 22)).sum())
        sun_early = int(((dow == 6) & (hr < 21)).sum())
        m["weekend_bars_saturday"], m["bars_after_friday_close"], m["bars_before_sunday_open"] = sat, fri_late, sun_early
        wk_idx = np.flatnonzero((tt.diff() > pd.Timedelta(hours=40)).to_numpy())
        open_hours = sorted({int(tt.iloc[i].hour) for i in wk_idx})
        m["weekly_open_utc_hours"] = open_hours
        if sat:
            issues.append(("fail", f"{sat} bars on Saturday UTC (clock shifted, or a vendor fills the weekend)"))
        if fri_late + sun_early:
            sev = "fail" if (fri_late + sun_early) > 0.002 * len(df) else "warn"
            issues.append((sev, f"{fri_late} bars after Fri 22:00 UTC and {sun_early} before Sun 21:00 UTC"))
        if open_hours and not set(open_hours) <= {21, 22, 23}:
            issues.append(("fail", f"weekly open at UTC hours {open_hours}; expected 21-22 (broker clock wrong?)"))
        elif len(open_hours) > 2:
            issues.append(("warn", f"weekly open at UTC hours {open_hours} (expected 21 and 22 only)"))

    # ── OHLC validity ─────────────────────────────────────────────────────────────
    o, h, l, c = (df[x].to_numpy(float) for x in ("open", "high", "low", "close"))
    nan = int(np.isnan(np.c_[o, h, l, c]).any(axis=1).sum())
    bad_hl = int(((h < np.maximum(o, c) - 1e-12) | (l > np.minimum(o, c) + 1e-12) | (h < l)).sum())
    nonpos = int((np.c_[o, h, l, c] <= 0).any(axis=1).sum())
    m["nan_rows"], m["ohlc_inconsistent"], m["nonpositive_price"] = nan, bad_hl, nonpos
    for name, v in (("rows with NaN", nan), ("bars with high/low inconsistent with open/close", bad_hl),
                    ("bars with non-positive price", nonpos)):
        if v:
            issues.append(("fail", f"{v} {name}"))

    # ── flat / frozen runs ────────────────────────────────────────────────────────
    flat = (h == l)
    m["flat_bars_pct"] = round(float(flat.mean() * 100), 2)
    run = 0
    runs = 0
    for v in flat:
        run = run + 1 if v else 0
        if run == 12:
            runs += 1
    m["flat_runs_ge_12"] = runs
    if runs:
        issues.append(("warn", f"{runs} runs of >= 12 consecutive zero-range bars (frozen feed?)"))

    # ── outliers / spikes ─────────────────────────────────────────────────────────
    dc = np.abs(np.diff(c, prepend=c[0]))
    win = max(50, 288 * 5 // max(step_min, 1)) if step_min < 60 else 100
    base = pd.Series(dc).rolling(win, min_periods=30).median().shift(1).to_numpy()
    base = np.where(np.isfinite(base) & (base > 0), base, np.nan)
    with np.errstate(invalid="ignore", divide="ignore"):
        big_move = dc > spike_k * base
        nxt = np.r_[c[2:], [np.nan, np.nan]][: len(c)]
        prv2 = np.r_[[np.nan], c[:-1]]
        # reverting spike: price jumps then returns to (near) where it was on the next bar
        reverted = np.abs(np.r_[c[1:], [np.nan]] - prv2) < 0.3 * dc
        n_rev = int((big_move & reverted).sum())
        rng_ = h - l
        rbase = pd.Series(rng_).rolling(win, min_periods=30).median().shift(1).to_numpy()
        rbase = np.where(np.isfinite(rbase) & (rbase > 0), rbase, np.nan)
        huge_wick = (rng_ > 25 * rbase) & (np.abs(c - o) < 0.3 * rng_)
        n_wick = int(huge_wick.sum())
        consecutive = (t.diff() <= step * 1.01).to_numpy()
        gap_open = np.abs(o - np.r_[[np.nan], c[:-1]])
        n_jump = int(((gap_open > spike_k * base) & consecutive).sum())
    m["spikes_reverting"], m["spikes_wick"], m["jumps_no_time_gap"] = n_rev, n_wick, n_jump
    sp_idx = np.flatnonzero(big_move & reverted | huge_wick)
    details["spike_examples"] = [t.iloc[i].isoformat() for i in sp_idx[:top]]
    if n_rev:
        issues.append(("warn", f"{n_rev} reverting single-bar spikes (> {spike_k:g}x typical move, then back) - bad ticks OR genuine "
                               f"news/intervention spikes (check other pairs at the same time); e.g. {details['spike_examples'][:3]}"))
    if n_wick:
        issues.append(("warn", f"{n_wick} bars with a range > 25x typical and a tiny body (wick spikes)"))
    if n_jump:
        issues.append(("warn", f"{n_jump} open-vs-previous-close jumps > {spike_k:g}x typical move with no time gap"))

    # ── spread ────────────────────────────────────────────────────────────────────
    if "spread" in df.columns:
        sp = df["spread"].to_numpy(float)
        synth = df["spread_synth"].to_numpy(bool) if "spread_synth" in df.columns else np.zeros(len(df), bool)
        real = ~synth
        zero_real = (sp == 0) & real
        m["spread_zero_pct"] = round(float(zero_real.sum() / max(real.sum(), 1) * 100), 1)
        m["spread_synthetic_pct"] = round(float(synth.mean() * 100), 1)
        nzv = sp[(sp > 0) & real]
        m["spread_median_nonzero_pts"] = float(np.median(nzv)) if len(nzv) else float("nan")
        m["spread_p99_pts"] = float(np.percentile(nzv, 99)) if len(nzv) else float("nan")
        m["spread_max_pts"] = float(sp[real].max()) if real.any() else float("nan")
        by_month = (pd.Series(zero_real[real].astype(float), index=t[real].dt.strftime("%Y-%m").to_numpy())
                    .groupby(level=0).mean() * 100).round(1)
        details["zero_spread_pct_by_month"] = by_month.to_dict()
        recent = real & (t >= t.iloc[-1] - pd.Timedelta(days=30)).to_numpy()
        m["spread_zero_pct_last30d"] = round(float(((sp == 0) & recent).sum() / max(recent.sum(), 1) * 100), 1)
        if m["spread_zero_pct"] > 20:
            issues.append(("warn", f"{m['spread_zero_pct']}% of broker-recorded spreads are 0 (last 30d: "
                                   f"{m['spread_zero_pct_last30d']}%) - prepare_bars imputes the same-hour non-zero "
                                   f"median; costs from imputed spreads are an estimate"))
        if m["spread_synthetic_pct"] > 0:
            issues.append(("info", f"{m['spread_synthetic_pct']}% of bars carry a SYNTHETIC spread (imported data)"))
        if point and len(nzv):
            m["spread_median_nonzero_price"] = m["spread_median_nonzero_pts"] * point
    if "src" in df.columns:
        m["imported_pct"] = round(float((df["src"] != "mt5").mean() * 100), 1)
        if m["imported_pct"] > 0:
            issues.append(("info", f"{m['imported_pct']}% of bars come from third-party imports (column 'src')"))

    ok = not any(s == "fail" for s, _ in issues)
    return {"symbol": symbol, "tf": tf, "ok": ok, "metrics": m, "issues": issues, "details": details}


def summary_line(rep: dict) -> str:
    m = rep["metrics"]
    verdict = "OK" if rep.get("ok") else "FAIL"
    n_warn = sum(1 for s, _ in rep["issues"] if s == "warn")
    return (f"{rep['symbol']} {rep['tf']}: {verdict} ({n_warn} warn) bars={m.get('bars')} "
            f"{str(m.get('first', ''))[:10]}..{str(m.get('last', ''))[:10]} dup={m.get('duplicates')} "
            f"gaps_unexpl={m.get('gaps_unexplained')} sat={m.get('weekend_bars_saturday')} "
            f"zero_spread={m.get('spread_zero_pct')}% spikes={m.get('spikes_reverting')}")


def format_quality(rep: dict) -> str:
    m = rep["metrics"]
    lines = [f"== {rep['symbol']} {rep['tf']}  {'OK' if rep.get('ok') else 'FAIL'} ==", summary_line(rep)]
    for k in ("span_days", "gaps_weekend", "gaps_holiday", "gaps_session", "gaps_unexplained", "gaps_unexplained_ge_1h",
              "missing_bars_unexplained", "weekly_open_utc_hours", "spread_zero_pct", "spread_zero_pct_last30d",
              "spread_median_nonzero_pts", "spread_p99_pts", "flat_bars_pct", "spikes_reverting", "spikes_wick",
              "jumps_no_time_gap"):
        if k in m:
            lines.append(f"  {k}: {m[k]}")
    for sev, msg in rep["issues"]:
        lines.append(f"  [{sev}] {msg}")
    return "\n".join(lines)


def window_bars(df: pd.DataFrame, since: Optional[str] = None, until: Optional[str] = None) -> pd.DataFrame:
    """Bars from `since` (inclusive) to `until` (exclusive); UTC dates, either may be None."""
    if since is None and until is None:
        return df
    t = pd.to_datetime(df["time"])
    tz = "UTC" if t.dt.tz is not None else None
    keep = pd.Series(True, index=df.index)
    if since is not None:
        keep &= t >= pd.Timestamp(since, tz=tz)
    if until is not None:
        keep &= t < pd.Timestamp(until, tz=tz)
    return df[keep]


def quality_table(symbols: Iterable[str], tfs: Iterable[str] = ("M5",), data_dir: Optional[Path] = None,
                  since: Optional[str] = None, until: Optional[str] = None) -> pd.DataFrame:
    """One row per symbol/timeframe from the parquet cache - handy for a printout at the top of a run.
    `since` / `until` (UTC dates, e.g. "2021-06-01") restrict the report to bars from `since` (inclusive) up to `until` (exclusive)."""
    from research.data import DATA_DIR
    data_dir = Path(data_dir or DATA_DIR)
    rows = []
    for s in symbols:
        for tf in tfs:
            p = data_dir / f"{s}_{tf}.parquet"
            if not p.exists():
                rows.append({"symbol": s, "tf": tf, "ok": False, "bars": 0})
                continue
            rep = quality_report(window_bars(pd.read_parquet(p), since, until), tf=tf, symbol=s)
            m = rep["metrics"]
            rows.append({"symbol": s, "tf": tf, "ok": rep["ok"], "bars": m["bars"],
                         "first": str(m["first"])[:10], "last": str(m["last"])[:10],
                         "dups": m["duplicates"], "gaps_unexpl": m["gaps_unexplained"],
                         "gaps_ge1h": m["gaps_unexplained_ge_1h"], "sat_bars": m.get("weekend_bars_saturday"),
                         "zero_spread%": m.get("spread_zero_pct"), "spikes": m["spikes_reverting"] + m["spikes_wick"],
                         "n_warn": sum(1 for sv, _ in rep["issues"] if sv == "warn"),
                         "n_fail": sum(1 for sv, _ in rep["issues"] if sv == "fail")})
    return pd.DataFrame(rows)
