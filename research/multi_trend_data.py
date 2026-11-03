"""Data layer and data-quality screen for the multi-asset trend study (research/reports/multi_trend_prereg.md).

DEV ONLY. Every read goes through `load_d1_raw`, which refuses an end after 2021-06-01 and filters the parquet at file
level, so bars at or after 2021-06-01 (FINAL and later) are never brought into memory, not even by the screen.
Nothing here reads a position rule's P/L: the screen looks at prices (jumps, holes, stale runs, spreads, clocks) only.
No MetaTrader5 import, no live-code import.

    python -m research.multi_trend_data screen      # writes research/output/multi_trend_dev/{dq_table.csv,universe.csv,dq_flags.json}
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data_multi"
OUT_DEV = ROOT / "output" / "multi_trend_dev"
DEV_START = "2003-01-01"
DEV_END = "2021-06-01"

# ── screen constants (data-quality report, fixed before any rule is run on returns) ──────────────────────────────
MIN_BARS_WARM = 270            # bars needed before an instrument may trade (365 calendar days = 261 weekdays, plus the 7-day tolerance and margin)
MIN_LIVE_YEARS = 8.0           # N: live DEV span (entry to 2021-06-01) required
HOLE_WEEKDAYS = 8              # >= 8 consecutive missing weekdays is a hole
COVERAGE_MIN = 0.80            # share of Mon-Fri weekdays with a bar, per calendar year
ZERO_RET_MAX = 0.25            # share of zero close-to-close returns per calendar year
SAME_RUN_MAX = 8               # longest run of identical closes in a calendar year
MASKED_SHARE_MAX = 0.30        # exclude when more than 30 % of the instrument's live years are masked
SPIKE_MULT, SPIKE_MIN = 10.0, 0.05      # reverting one-bar spike: |r| > 10 robust sigma and > 5 %
JUMP_MULT, JUMP_MIN = 12.0, 0.03        # reported jump: |r| > 12 robust sigma and > 3 %
ROLL_Z, ROLL_MIN_N, ROLL_BIN_SHARE, ROLL_P = 5.0, 20, 0.5, 1e-3   # roll-like: >= 20 days with |z| > 5, >= 50 % in one 5-day-of-month bin, binomial tail x6 < 0.001
DUP_CORR, DUP_MIN_WEEKS = 0.98, 100     # duplicate instruments: weekly return correlation
SPREAD_WIN, SPREAD_MIN = 60, 10
# full-spread floors in basis points of price (assumption table, prereg section 4)
SPREAD_FLOOR_BP = {"FX": 1.5, "EQ": 3.0, "EN": 8.0, "MT": 5.0, "AG": 15.0}
CLASSES = ("FX", "EQ", "EN", "MT", "AG")
G8 = {"USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD"}

# FX placeholder windows in which a recorded spread of exactly 50 points is a sentinel, not a quote (FX only)
SENTINEL_WINDOWS = ((pd.Timestamp("2019-01-01"), pd.Timestamp("2019-03-31")), (pd.Timestamp("2020-05-01"), pd.Timestamp("2021-09-30")))
SENTINEL_PTS = 50


# ── guards ───────────────────────────────────────────────────────────────────────────────────────────────────────

def check_end(end) -> pd.Timestamp:
    """Return `end` as a UTC timestamp; refuse a missing end and anything after DEV_END (FINAL must not be read)."""
    if end is None:
        raise ValueError("an explicit end <= 2021-06-01 is required (FINAL must not be read)")
    e = pd.Timestamp(end)
    e = e.tz_localize("UTC") if e.tzinfo is None else e.tz_convert("UTC")
    if e > pd.Timestamp(DEV_END, tz="UTC"):
        raise ValueError(f"end {e} is after DEV_END {DEV_END}: FINAL and later data are off limits")
    return e


def load_d1_raw(symbol: str, end=DEV_END, data_dir: Path = DATA_DIR) -> pd.DataFrame:
    """Raw D1 bars with label time (broker server day, UTC-labelled) strictly before `end`, read with a file-level filter."""
    e = check_end(end)
    p = Path(data_dir) / f"{symbol}_D1.parquet"
    df = pd.read_parquet(p, filters=[("time", "<", e)])
    return df.sort_values("time").reset_index(drop=True)


def load_spec(symbol: str, data_dir: Path = DATA_DIR) -> dict:
    return json.loads((Path(data_dir) / f"{symbol}_spec.json").read_text())


def available_symbols(data_dir: Path = DATA_DIR) -> list[str]:
    return sorted(p.name[: -len("_D1.parquet")] for p in Path(data_dir).glob("*_D1.parquet"))


def sha256_file(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


# ── asset-class rule (path first, then name lists; written before any return is looked at) ─────────────────────────

ENERGY_NAMES = ("OIL", "BRENT", "WTI", "NGAS", "NATGAS", "XNG", "XTI", "XBR", "GASOIL", "HEAT", "GASOLINE")
METAL_NAMES = ("XAU", "XAG", "XPT", "XPD", "XCU", "COPPER", "GOLD", "SILVER", "PLATINUM", "PALLADIUM", "XAL", "ALUM", "ZINC", "NICKEL")


def classify(symbol: str, path: str) -> tuple[str, str]:
    """(class, reason). Classes: FX EQ EN MT AG, or an excluded label: EX (exotic FX), DXY, CRYPTO, STOCK, OTHER.
    FX = a Forex-path pair whose two currencies are both in the G8 set (28 pairs); Exotics-path and other FX pairs are EX.
    Cash-path: the dollar index is DXY (excluded: a linear function of FX pairs), names containing an energy token are EN,
    metal tokens MT, everything else EQ (indices). Commodities/Agriculture/Metals paths follow the path with the name lists
    deciding energy versus metals versus agriculture."""
    top = path.split("\\")[0].lower() if path else ""
    s = symbol.upper()
    if top.startswith("crypto"):
        return "CRYPTO", "path"
    if top.startswith("equit") or top.startswith("stock"):
        return "STOCK", "path"
    if top.startswith("forex"):
        a, b = s[:3], s[3:6]
        return ("FX", "path+G8") if a in G8 and b in G8 and len(s) == 6 else ("EX", "forex path, currency outside G8")
    if top.startswith("exotic"):
        return "EX", "path"
    if s.startswith("DXY") or s.startswith("USDX") or s == "USDIDX":
        return "DXY", "name"
    if any(t in s for t in ENERGY_NAMES):
        return "EN", "name"
    if any(s.startswith(t) for t in METAL_NAMES):
        return "MT", "name"
    if top.startswith("agri"):
        return "AG", "path"
    if top.startswith("metal"):
        return "MT", "path"
    if top.startswith("commod"):
        return "AG", "path (commodities not matched as energy or metal)"
    if top.startswith("cash"):
        return "EQ", "path"
    return "OTHER", "unrecognised path"


# ── cleaning ─────────────────────────────────────────────────────────────────────────────────────────────────────

def robust_sigma(r: pd.Series, win: int = 250, minp: int = 100) -> pd.Series:
    """Trailing median-absolute-deviation sigma of r (causal: uses r[..t-1])."""
    med = r.rolling(win, min_periods=minp).median()
    mad = (r - med).abs().rolling(win, min_periods=minp).median()
    return (1.4826 * mad).shift(1)


def clean_d1(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Raw D1 -> one bar per weekday date. Weekend-labelled bars are merged into the next Monday bar (open of the first,
    extremes over both, close of the last). Bars with non-positive or NaN OHLC are dropped, and so is a bar that is a
    reverting one-bar spike (|r| > 10 robust sigma and > 5 %, next bar reverses at least 65 % of it): that test looks one
    bar ahead, which is a data-error correction (a vendor would repair it) and not a trading decision; the count is reported.
    Output columns: date (datetime64[D] as ns), open, high, low, close, tick_volume, spread."""
    cnt = {"rows": int(len(df)), "weekend_merged": 0, "nonpositive_dropped": 0, "ohlc_inconsistent": 0, "spike_dropped": 0, "dup_dates": 0}
    if len(df) == 0:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "tick_volume", "spread"]), cnt
    d = df.copy()
    d["date"] = d["time"].dt.tz_convert("UTC").dt.tz_localize(None).dt.normalize()
    dow = d["date"].dt.dayofweek
    cnt["weekend_merged"] = int((dow >= 5).sum())
    d["bdate"] = d["date"] + pd.to_timedelta(np.where(dow == 5, 2, np.where(dow == 6, 1, 0)), unit="D")
    cnt["dup_dates"] = int(d.duplicated("date").sum())
    g = d.groupby("bdate", sort=True)
    out = pd.DataFrame({"open": g["open"].first(), "high": g["high"].max(), "low": g["low"].min(), "close": g["close"].last(),
                        "tick_volume": g["tick_volume"].sum(), "spread": g["spread"].last()}).reset_index().rename(columns={"bdate": "date"})
    bad = ~((out[["open", "high", "low", "close"]] > 0).all(axis=1)) | out[["open", "high", "low", "close"]].isna().any(axis=1)
    cnt["nonpositive_dropped"] = int(bad.sum())
    out = out[~bad].reset_index(drop=True)
    incons = (out["high"] < out[["open", "close"]].max(axis=1) * (1 - 1e-9)) | (out["low"] > out[["open", "close"]].min(axis=1) * (1 + 1e-9))
    cnt["ohlc_inconsistent"] = int(incons.sum())
    for _ in range(2):          # two passes: a spike next to another spike
        r = np.log(out["close"]).diff()
        sg = robust_sigma(r)
        r1 = r.shift(-1)
        spike = (r.abs() > SPIKE_MULT * sg) & (r.abs() > SPIKE_MIN) & (np.sign(r1) == -np.sign(r)) & ((r + r1).abs() < 0.35 * r.abs())
        if not spike.any():
            break
        cnt["spike_dropped"] += int(spike.sum())
        out = out[~spike.fillna(False)].reset_index(drop=True)
    return out, cnt


# ── spreads (costs only) ─────────────────────────────────────────────────────────────────────────────────────────

def clean_spread_pts(df: pd.DataFrame, cls: str) -> pd.Series:
    """Recorded D1 spread in points with zero and (FX only, in the placeholder windows) the 50-point sentinel set to NaN."""
    sp = df["spread"].astype(float).copy()
    sp[sp <= 0] = np.nan
    if cls == "FX":
        d = df["date"]
        sent = pd.Series(False, index=df.index)
        for a, b in SENTINEL_WINDOWS:
            sent |= (d >= a) & (d <= b)
        sp[sent & (sp == SENTINEL_PTS)] = np.nan
    return sp


def spread_fraction(df: pd.DataFrame, cls: str, point: float) -> np.ndarray:
    """Causal full-spread fraction of price at each bar: max(trailing median of the clean recorded spreads, class floor).
    Uses bars up to and including t only."""
    sp = clean_spread_pts(df, cls)
    med = sp.rolling(SPREAD_WIN, min_periods=SPREAD_MIN).median()
    frac = (med * point / df["close"]).to_numpy(float)
    floor = SPREAD_FLOOR_BP.get(cls, 5.0) * 1e-4
    return np.where(np.isfinite(frac), np.maximum(frac, floor), floor)


# ── screens ──────────────────────────────────────────────────────────────────────────────────────────────────────

def _yearly(df: pd.DataFrame) -> pd.DataFrame:
    """Per calendar year: bars, weekday coverage, zero-return share, longest same-close run, weekend flag."""
    rows = []
    r = np.log(df["close"]).diff()
    same = (df["close"] == df["close"].shift(1)).astype(int)
    run = same.groupby((same == 0).cumsum()).cumsum()
    yrs = df["date"].dt.year
    for y in sorted(yrs.unique()):
        m = (yrs == y).to_numpy()
        n = int(m.sum())
        lo, hi = df["date"][m].min(), df["date"][m].max()
        wd = int(np.busday_count(lo.date(), (hi + pd.Timedelta(days=1)).date()))
        rows.append({"year": int(y), "bars": n, "weekday_coverage": n / max(wd, 1), "zero_ret_share": float((r[m] == 0).mean()) if n > 1 else 0.0,
                     "longest_same_run": int(run[m].max()) if n else 0})
    return pd.DataFrame(rows)


def holes(df: pd.DataFrame, min_missing: int = HOLE_WEEKDAYS) -> list[tuple[str, str, int]]:
    """Gaps of at least `min_missing` consecutive missing weekdays between successive bars: (last bar, next bar, missing)."""
    d = df["date"].to_numpy("datetime64[D]")
    if len(d) < 2:
        return []
    miss = np.busday_count(d[:-1], d[1:]) - 1
    ix = np.where(miss >= min_missing)[0]
    return [(str(d[i]), str(d[i + 1]), int(miss[i])) for i in ix]


def jumps(df: pd.DataFrame) -> pd.DataFrame:
    r = np.log(df["close"]).diff()
    sg = robust_sigma(r)
    z = r / sg
    m = (r.abs() > JUMP_MULT * sg) & (r.abs() > JUMP_MIN)
    return pd.DataFrame({"date": df["date"][m], "ret": r[m], "z": z[m]})


def roll_like(df: pd.DataFrame) -> dict:
    """Recurring-gap detector: days with |z| > ROLL_Z; roll-like when there are at least ROLL_MIN_N of them and at least
    ROLL_BIN_SHARE of them fall into one of the six day-of-month bins 1-5, 6-10, ..., 26-31, and that concentration is
    significant (binomial tail times 6 below ROLL_P). A monthly/quarterly roll calendar concentrates in one bin; real shocks and
    random outliers spread over the month. (First version used 12 days and no tail test: 14 random extreme days of AUDNZD, a spot
    pair, were flagged; revised before any run, see the preregistration log.)"""
    r = np.log(df["close"]).diff()
    z = (r / robust_sigma(r)).abs()
    d = df["date"][z > ROLL_Z]
    n = int(len(d))
    if n == 0:
        return {"n_extreme": 0, "bin_share": 0.0, "roll_like": False}
    bins = np.minimum((d.dt.day.to_numpy() - 1) // 5, 5)
    k = int(np.bincount(bins, minlength=6).max())
    share = float(k / n)
    # tail probability of a fullest bin of this size if the extreme days were spread uniformly over the six bins (Bonferroni over bins)
    from math import comb
    p = 1.0 / 6.0
    tail = min(1.0, 6.0 * sum(comb(n, j) * p ** j * (1 - p) ** (n - j) for j in range(k, n + 1)))
    return {"n_extreme": n, "bin_share": share, "bin_tail_p": tail, "roll_like": bool(n >= ROLL_MIN_N and share >= ROLL_BIN_SHARE and tail < ROLL_P)}


def weekly_returns(df: pd.DataFrame) -> pd.Series:
    """Friday-as-of weekly log returns indexed by week-ending Friday (for the duplicate screen)."""
    s = pd.Series(np.log(df["close"].to_numpy()), index=pd.DatetimeIndex(df["date"]))
    return s.resample("W-FRI").last().dropna().diff().dropna()


def dst_gap_ratio(df: pd.DataFrame) -> float:
    """Median absolute overnight gap (open vs previous close, in units of daily robust sigma) in the weeks where the US has switched to or
    from DST and Europe has not (mid-March to end-March, end-October to early November), over the same statistic elsewhere.
    Descriptive clock check; about 1 means the D1 boundary shift does not show in the overnight gap."""
    r = np.log(df["close"]).diff()
    sg = robust_sigma(r)
    gap = (np.log(df["open"]) - np.log(df["close"]).shift(1)) / sg
    d = df["date"]
    md = d.dt.month * 100 + d.dt.day
    mism = ((md >= 308) & (md <= 331)) | ((md >= 1025) & (md <= 1106))
    ok = gap.notna() & np.isfinite(gap)
    a, b = gap[ok & mism], gap[ok & ~mism]
    if len(a) < 20 or len(b) < 100:
        return float("nan")
    return float(a.abs().median() / b.abs().median()) if b.abs().median() > 0 else float("nan")


def mask_years(df: pd.DataFrame) -> set:
    """Calendar years in which an instrument is flat for data reasons: weekday coverage below 80 %, more than 25 % zero returns, a run of
    8 identical closes, or a hole of 8 or more missing weekdays (both the year the hole starts and the year it ends). The first and last
    calendar years are partial; coverage is measured inside the bars' own span."""
    masked = set()
    for _, y in _yearly(df).iterrows():
        if y["weekday_coverage"] < COVERAGE_MIN or y["zero_ret_share"] > ZERO_RET_MAX or y["longest_same_run"] >= SAME_RUN_MAX:
            masked.add(int(y["year"]))
    for a, b, _ in holes(df):
        masked.add(int(a[:4]))
        masked.add(int(b[:4]))
    return masked


def screen_symbol(sym: str, spec: dict, raw: pd.DataFrame) -> dict:
    """All data-quality facts for one instrument (DEV window only) and its masked years."""
    cls, why = classify(sym, spec.get("path", ""))
    df, cnt = clean_d1(raw)
    out = {"symbol": sym, "path": spec.get("path", ""), "class": cls, "class_reason": why, "quote_ccy": spec.get("currency_profit", ""),
           "digits": spec.get("digits"), "point": spec.get("point"), "contract_size": spec.get("contract_size"), **cnt}
    if len(df) < 50:
        out.update({"bars": int(len(df)), "first": "", "last": "", "excluded": True, "masked_years": [],
                    "reasons": [f"fewer than 50 D1 bars before {DEV_END} (terminal history starts {str(spec.get('first', ''))[:10]})"]})
        return out
    out["bars"], out["first"], out["last"] = int(len(df)), str(df["date"].iloc[0].date()), str(df["date"].iloc[-1].date())
    ym = _yearly(df)
    hl = holes(df)
    jp = jumps(df)
    rl = roll_like(df)
    out.update({"holes": hl, "n_holes": len(hl), "n_jumps": int(len(jp)), "jump_dates": [f"{d.date()}({z:+.0f}z)" for d, z in zip(jp["date"], jp["z"])][:12],
                "roll_n_extreme": rl["n_extreme"], "roll_bin_share": rl["bin_share"], "roll_bin_tail_p": rl.get("bin_tail_p", 1.0), "roll_like": rl["roll_like"], "dst_gap_ratio": dst_gap_ratio(df)})
    masked = mask_years(df)
    # warm-up gate: the first Wednesday with at least MIN_BARS_WARM bars behind it
    wed = df["date"][df["date"].dt.dayofweek == 2]
    cum = pd.Series(np.arange(1, len(df) + 1), index=df["date"])
    entry = None
    for w in wed:
        if cum.loc[:w].iloc[-1] >= MIN_BARS_WARM:
            entry = w
            break
    entry = max(entry, pd.Timestamp(DEV_START)) if entry is not None else None
    out["entry"] = str(entry.date()) if entry is not None else ""
    live_years = [] if entry is None else list(range(entry.year, 2021 + 1))
    span = ((pd.Timestamp(DEV_END) - entry).days / 365.25) if entry is not None else 0.0
    out["live_span_years"] = round(span, 2)
    m_live = [y for y in live_years if y in masked]
    out["masked_years"] = sorted(masked)
    out["masked_share_live"] = (len(m_live) / max(len(live_years), 1)) if live_years else 1.0
    # spread record facts (descriptive)
    sp = clean_spread_pts(df, cls if cls in CLASSES else "EQ")
    frac = spread_fraction(df, cls if cls in CLASSES else "EQ", float(spec.get("point") or 0.0))
    out["spread_zero_share"] = float((df["spread"] <= 0).mean())
    out["spread_clean_median_pts"] = float(np.nanmedian(sp)) if sp.notna().any() else float("nan")
    out["half_spread_bp_median"] = float(np.median(frac) * 0.5e4)
    out["swap_long"], out["swap_short"], out["swap_mode"] = spec.get("swap_long"), spec.get("swap_short"), spec.get("swap_mode")
    reasons = []
    if cls not in CLASSES:
        reasons.append(f"class {cls}")
    if entry is None or span < MIN_LIVE_YEARS:
        reasons.append(f"live span {span:.1f}y < {MIN_LIVE_YEARS:.0f}y")
    if rl["roll_like"]:
        reasons.append("roll-like recurring gaps")
    if live_years and out["masked_share_live"] > MASKED_SHARE_MAX:
        reasons.append(f"masked {out['masked_share_live']:.0%} of live years")
    out["reasons"] = reasons
    out["excluded"] = bool(reasons)
    return out


def screen_all(data_dir: Path = DATA_DIR, end=DEV_END, symbols: list[str] | None = None) -> pd.DataFrame:
    rows, weekly = [], {}
    for s in (symbols or available_symbols(data_dir)):
        try:
            spec, raw = load_spec(s, data_dir), load_d1_raw(s, end, data_dir)
        except FileNotFoundError:
            continue
        r = screen_symbol(s, spec, raw)
        rows.append(r)
        if not r["excluded"] or r.get("class") in CLASSES:
            weekly[s] = weekly_returns(clean_d1(raw)[0]) if len(raw) > 60 else pd.Series(dtype=float)
    tab = pd.DataFrame(rows)
    tab["duplicate_of"] = ""
    if len(tab):
        cand = [s for s in tab["symbol"] if tab.set_index("symbol").loc[s, "class"] in CLASSES and s in weekly and len(weekly[s]) > DUP_MIN_WEEKS]
        # earliest first bar wins, ties alphabetical
        order = sorted(cand, key=lambda s: (tab.set_index("symbol").loc[s, "first"], s))
        keep = []
        for s in order:
            dup = None
            for k in keep:
                a, b = weekly[s].align(weekly[k], join="inner")
                if len(a) >= DUP_MIN_WEEKS and np.corrcoef(a, b)[0, 1] >= DUP_CORR:
                    dup = k
                    break
            if dup:
                tab.loc[tab["symbol"] == s, "duplicate_of"] = dup
            else:
                keep.append(s)
        for i, r in tab.iterrows():
            if r["duplicate_of"]:
                tab.at[i, "reasons"] = list(r["reasons"]) + [f"duplicate of {r['duplicate_of']}"]
                tab.at[i, "excluded"] = True
    return tab


def write_screen(out_dir: Path = OUT_DEV, data_dir: Path = DATA_DIR) -> pd.DataFrame:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    tab = screen_all(data_dir)
    flat = tab.copy()
    for c in ("reasons", "masked_years", "holes", "jump_dates"):
        if c in flat:
            flat[c] = flat[c].apply(lambda x: "; ".join(map(str, x)) if isinstance(x, (list, tuple)) else x)
    flat.to_csv(out_dir / "dq_table.csv", index=False)
    uni = tab[~tab["excluded"]][["symbol", "class", "entry", "masked_years", "quote_ccy"]].copy()
    uni["masked_years"] = uni["masked_years"].apply(lambda x: ",".join(map(str, x)))
    uni.to_csv(out_dir / "universe.csv", index=False)
    (out_dir / "dq_flags.json").write_text(json.dumps(tab.to_dict("records"), indent=1, default=str))
    return tab


def report_tables(tab: pd.DataFrame, fetched_log: Path | None = None) -> str:
    """Markdown tables for the data-quality report, generated from the screen (so a refreshed fetch regenerates them exactly)."""
    L = []
    inc = tab[~tab["excluded"]]
    L.append(f"Screened {len(tab)} fetched symbols; {len(inc)} enter the universe.\n")
    L.append("| class | fetched | in universe | excluded |\n|---|---|---|---|")
    for c in list(CLASSES) + ["EX", "DXY", "OTHER"]:
        sub = tab[tab["class"] == c]
        if len(sub):
            L.append(f"| {c} | {len(sub)} | {int((~sub['excluded']).sum())} | {int(sub['excluded'].sum())} |")
    scr = tab[tab["bars"] >= 50]
    if len(scr) and "dst_gap_ratio" in scr:
        L.append("\n**Per class (screened symbols with DEV bars):** median DST-week overnight-gap ratio (about 1 means no clock effect), instruments with at least one hole, "
                 "with roll-like pattern, total weekend bars merged, total spikes dropped, median share of zero recorded spreads.\n")
        L.append("| class | symbols | median DST ratio | with holes | roll-like | weekend bars merged | spikes dropped | median zero-spread share |\n|---|---|---|---|---|---|---|---|")
        for c, sub in scr.groupby("class"):
            L.append(f"| {c} | {len(sub)} | {sub['dst_gap_ratio'].median():.2f} | {int((sub['n_holes'] > 0).sum())} | {int(sub['roll_like'].sum())} | "
                     f"{int(sub['weekend_merged'].sum())} | {int(sub['spike_dropped'].sum())} | {sub['spread_zero_share'].median():.2f} |")
    L.append("\n**In the universe** (entry = first Wednesday with at least 270 bars, not before 2003-01-01; masked years are flat):\n")
    L.append("| symbol | class | first bar | entry | live years | masked years | holes | jumps | clean spread median (pts) | half-spread (bp) | quote |\n|---|---|---|---|---|---|---|---|---|---|---|")
    for _, r in inc.sort_values(["class", "symbol"]).iterrows():
        L.append(f"| {r['symbol']} | {r['class']} | {r['first']} | {r['entry']} | {r['live_span_years']} | {','.join(map(str, r['masked_years']))} | {r['n_holes']:.0f} | {r['n_jumps']:.0f} | "
                 f"{r['spread_clean_median_pts']:.1f} | {r['half_spread_bp_median']:.1f} | {r['quote_ccy']} |")
    exc = tab[tab["excluded"]]
    L.append("\n**Excluded** (rule that fired):\n")
    L.append("| symbol | path | first bar | bars | reasons |\n|---|---|---|---|---|")
    for _, r in exc.sort_values(["class", "symbol"]).iterrows():
        L.append(f"| {r['symbol']} | {r['path']} | {r.get('first', '')} | {r['bars']} | {'; '.join(r['reasons'])} |")
    return "\n".join(L)


_SCR = ROOT / "output" / "scratch"
FETCH_LOG = _SCR / "fetch_multi_retry.log" if (_SCR / "fetch_multi_retry.log").exists() else _SCR / "fetch_multi.log"
REPORT = ROOT / "reports" / "multi_trend_data_quality.md"


def fetch_status(log_path: Path = FETCH_LOG, data_dir: Path = DATA_DIR) -> str:
    """Markdown status of the fetch from its log: done or not, symbols fetched, symbols that failed (timeouts) and are not on disk."""
    ok, failed, done = [], [], False
    if Path(log_path).exists():
        for ln in Path(log_path).read_text(encoding="utf-8", errors="replace").splitlines():
            parts = ln.split()
            if ln.startswith("done:"):
                done = True
            elif len(parts) >= 2 and parts[1] == "FAILED":
                failed.append(parts[0])
            elif len(parts) >= 3:
                ok.append(parts[0])
    on_disk = set(available_symbols(data_dir))
    missing = [s for s in failed if s not in on_disk]
    last = (ok + failed)[-1] if (ok or failed) else "-"
    state = "**complete** (the last log line starts with `done:`)" if done else f"**NOT complete**: the log has no `done:` line; the last symbol processed in alphabetical order was `{last}`; symbols after it have not been attempted yet"
    nl = chr(10) * 2
    return (f"Fetch state: {state}. D1 files on disk: {len(on_disk)}. Log lines: {len(ok)} fetched, {len(failed)} FAILED (timeout, `rc=-9`)." + nl +
            f"Fetched: {', '.join(ok) or '-'}." + nl +
            f"Missing so far (timed out, not on disk; the owner's session retries them): {', '.join(missing) or '-'}." + nl +
            ("The universe below is the screen of everything on disk and is final unless the owner adds symbols (its sha256 is in `universe_sha256.txt`). " if done else
             "The universe below is whatever is on disk; it is **not frozen** until the fetch is complete and the owner has retried the failures. ") +
            "Refresh everything with `python -m research.multi_trend_data screen` followed by `python -m research.multi_trend_data report`.")


def refresh_report(report: Path = REPORT, out_dir: Path = OUT_DEV, log_path: Path = FETCH_LOG) -> None:
    """Rewrite the generated blocks (<!--STATUS-->..<!--/STATUS--> and <!--TABLES-->..<!--/TABLES-->) of the data-quality report."""
    import re
    txt = Path(report).read_text(encoding="utf-8")
    blocks = {"STATUS": fetch_status(log_path), "TABLES": (Path(out_dir) / "dq_report_tables.md").read_text(encoding="utf-8")}
    for k, v in blocks.items():
        nl = chr(10)
        txt, n = re.subn(rf"<!--{k}-->.*?<!--/{k}-->", lambda m, k=k, v=v: f"<!--{k}-->{nl}{v}{nl}<!--/{k}-->", txt, flags=re.S)
        if n != 1:
            raise ValueError(f"marker {k} not found exactly once in {report}")
    Path(report).write_text(txt, encoding="utf-8")


def load_universe(path: Path | None = None) -> pd.DataFrame:
    u = pd.read_csv(Path(path or OUT_DEV / "universe.csv"), dtype={"masked_years": str})
    u["masked_years"] = u["masked_years"].fillna("").apply(lambda s: [int(x) for x in str(s).split(",") if x])
    return u


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["screen", "report"])
    ap.add_argument("--out", default=str(OUT_DEV))
    a = ap.parse_args(argv)
    if a.cmd == "report":
        refresh_report(out_dir=Path(a.out))
        print(f"refreshed {REPORT}")
        return 0
    tab = write_screen(Path(a.out))
    keep = tab[~tab["excluded"]]
    (Path(a.out) / "dq_report_tables.md").write_text(report_tables(tab), encoding="utf-8")
    (Path(a.out) / "universe_sha256.txt").write_text(sha256_file(Path(a.out) / "universe.csv") + "\n")
    print(f"{len(tab)} symbols screened, {len(keep)} in the universe")
    print(keep.groupby("class").size().to_string())
    return 0


if __name__ == "__main__":
    sys.exit(main())
