"""Part 1a/1b: data inventory and descriptive structure (DEV only, no strategy P/L).

    python -m research.ai.inventory            # writes research/output/ai/inventory/*.csv and inventory.json

Everything is vectorised; uncertainty is a week-block bootstrap on per-week sufficient statistics (pairs share calendar
weeks, so the pooled estimate resamples whole calendar weeks jointly).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from research.ai import dataset as ds
from research.ai.stats import block_bootstrap, group_sums, week_index
from research.data import utc_to_server

OUT = Path(__file__).resolve().parents[1] / "output" / "ai" / "inventory"
ERAS = [("2014-2016", "2014-01-01", "2017-01-01"), ("2017-2020", "2017-01-01", "2021-01-01"),
        ("2021(5m)", "2021-01-01", ds.DEV_END)]


# ── returns with gap handling ─────────────────────────────────────────────────────────

def lag_returns(times: np.ndarray, close: np.ndarray, q: int, step_min: float) -> np.ndarray:
    """log(c[t]/c[t-q]) as an array aligned to `times` (NaN for t<q or when the q bars are not exactly contiguous)."""
    c = np.log(np.asarray(close, float))
    out = np.full(len(c), np.nan)
    if len(c) <= q:
        return out
    dt = (times[q:] - times[:-q]).astype("timedelta64[m]").astype(float)
    r = c[q:] - c[:-q]
    out[q:] = np.where(dt == q * step_min, r, np.nan)
    return out


def _t64(df: pd.DataFrame) -> np.ndarray:
    return df["time"].values.astype("datetime64[ns]")


def d1_from_h1(h1: pd.DataFrame) -> pd.DataFrame:
    """Daily bars on the broker's NY-close day (server date), built from H1 bars: close = last H1 close of the day."""
    d = utc_to_server(h1["time"]).dt.floor("D")
    g = h1.assign(day=d.to_numpy()).groupby("day")
    out = pd.DataFrame({"open": g["open"].first(), "high": g["high"].max(), "low": g["low"].min(),
                        "close": g["close"].last(), "n": g["close"].size()}).reset_index()
    out = out[out["n"] >= 12].reset_index(drop=True)          # drop stubs
    out["time"] = pd.to_datetime(out["day"]).dt.tz_localize("UTC")
    return out


# ── inventory ─────────────────────────────────────────────────────────────────────────

def span_row(sym: str, tf: str, df: pd.DataFrame) -> dict:
    from research.data_quality import _classify_gap
    step = ds.TF_MIN[tf]
    t = _t64(df)
    gaps = np.diff(t).astype("timedelta64[m]").astype(float)
    weekend = gaps > 40 * 60
    odd = (gaps > 1.5 * step) & ~weekend
    unexplained = 0
    for k in np.flatnonzero(odd):
        a, b = pd.Timestamp(t[k], tz="UTC"), pd.Timestamp(t[k + 1], tz="UTC")
        if _classify_gap(a, b) == "unexplained":
            unexplained += 1
    row = {"symbol": sym, "tf": tf, "bars": len(df), "first": str(df["time"].iloc[0].date()),
           "last": str(df["time"].iloc[-1].date()), "weekend_gaps": int(weekend.sum()),
           "other_gaps": int(odd.sum()), "unexplained_gaps": unexplained,
           "missing_bars_in_gaps": int(np.sum(gaps[odd] / step - 1))}
    for name, a, b in ERAS:
        m = (df["time"] >= pd.Timestamp(a, tz="UTC")) & (df["time"] < pd.Timestamp(b, tz="UTC"))
        if m.any():
            sp = df.loc[m, "spread"].astype(float)
            nz = sp[sp > 0]
            row[f"zero_spread_pct_{name}"] = round(float((sp <= 0).mean() * 100), 1)
            row[f"median_spread_pts_{name}"] = float(nz.median()) if len(nz) else np.nan
    return row


def session_structure(sym: str, m5: pd.DataFrame) -> dict:
    t = _t64(m5)
    gaps = np.diff(t).astype("timedelta64[m]").astype(float)
    wk = np.flatnonzero(gaps > 40 * 60)
    open_h = pd.DatetimeIndex(t[wk + 1]).hour
    close_h = pd.DatetimeIndex(t[wk]).hour
    hrs = pd.DatetimeIndex(t).hour
    share = np.bincount(hrs, minlength=24) / len(hrs)
    return {"symbol": sym, "weeks": int(len(wk)),
            "weekly_open_utc_hour_counts": dict(pd.Series(open_h).value_counts().sort_index().items()),
            "weekly_close_utc_hour_counts": dict(pd.Series(close_h).value_counts().sort_index().items()),
            "bar_share_by_utc_hour_min": float(share.min()), "bar_share_by_utc_hour_max": float(share.max()),
            "bars_per_day_mean": float(len(m5) / (len(wk) * 5.0))}


# ── predictability: autocorrelation and variance ratios ───────────────────────────────

def _acf_stats(times, close, lag, step_min):
    """per-week sufficient statistics (keys, W x 6) for corr(r_t, r_{t-lag}) with contiguous bars only."""
    r1 = lag_returns(times, close, 1, step_min)
    chain = lag_returns(times, close, lag, step_min)            # NaN unless t-lag..t contiguous
    x = r1[lag:]
    y = r1[:-lag]
    ok = np.isfinite(chain[lag:])
    x = np.where(ok, x, np.nan)
    from research.ai.stats import corr_stats
    cs = corr_stats(x, y)
    keys = week_index(times[lag:])
    return group_sums(keys, cs)


def _pooled(parts):
    """combine [(keys, W x k)] from several pairs into calendar-week stats."""
    keys = np.concatenate([p[0] for p in parts])
    mat = np.vstack([p[1] for p in parts])
    return group_sums(keys, mat)


def acf_table(frames: dict, n_boot: int = 1000) -> pd.DataFrame:
    """frames: {sym: {'M5':df,'M15':df,'H1':df,'H4':df,'D1':df}} -> rho(lag) per pair x TF, plus pooled over the 5 FX pairs."""
    from research.ai.stats import pearson_from_sums
    rows = []
    step = {"M5": 5, "M15": 15, "H1": 60, "H4": 240, "D1": 1440}
    for tf in ("M5", "M15", "H1", "H4", "D1"):
        for lag in (1, 2, 3):
            parts = {}
            for sym, fr in frames.items():
                df = fr[tf]
                if tf == "D1":                                    # consecutive trading days, up to a 4-day holiday gap
                    t = _t64(df)
                    c = np.log(df["close"].to_numpy())
                    r1 = np.full(len(c), np.nan)
                    r1[1:] = np.where(np.diff(t).astype("timedelta64[D]").astype(float) <= 4, np.diff(c), np.nan)
                    x, y = r1[lag:], r1[:-lag]
                    from research.ai.stats import corr_stats
                    parts[sym] = group_sums(week_index(t[lag:]), corr_stats(x, y))
                else:
                    parts[sym] = _acf_stats(_t64(df), df["close"].to_numpy(), lag, step[tf])
            for sym, (k, s) in parts.items():
                b = block_bootstrap(s, pearson_from_sums, n_boot, seed=lag)
                rows.append({"scope": sym, "tf": tf, "lag": lag, **b, "n": int(s[:, 0].sum())})
            k, s = _pooled(list(parts.values()))
            b = block_bootstrap(s, pearson_from_sums, n_boot, seed=lag)
            rows.append({"scope": "POOLED5", "tf": tf, "lag": lag, **b, "n": int(s[:, 0].sum())})
    return pd.DataFrame(rows)


VR_M5 = [3, 6, 12, 24, 48, 96, 288]          # 15m, 30m, 1h, 2h, 4h, 8h, 24h in M5 units
VR_H1 = [4, 12, 24, 48]                       # 4h, 12h, 1d, 2d in H1 units (a 5-day window always contains a weekend gap)
CLIP_K = 8.0                                  # robust variant: 1-bar returns clipped at 8 x 1.4826 x median|r|


def _clipped_logprice(times, close, step_min, k=CLIP_K):
    """Log price rebuilt from clipped 1-bar returns (gap returns -> 0). Robust to isolated bad ticks / rollover spikes."""
    r1 = lag_returns(times, close, 1, step_min)
    scale = 1.4826 * np.nanmedian(np.abs(r1))
    r = np.clip(np.nan_to_num(r1, nan=0.0), -k * scale, k * scale)
    return np.cumsum(r)


def _vr_stats(times, close, q, step_min, clip=False):
    if clip:
        lp = _clipped_logprice(times, close, step_min)
        valid1 = np.isfinite(lag_returns(times, close, 1, step_min))
        r1 = np.where(valid1, np.concatenate([[np.nan], np.diff(lp)]), np.nan)
        validq = np.isfinite(lag_returns(times, close, q, step_min))
        rq = np.full(len(lp), np.nan)
        rq[q:] = lp[q:] - lp[:-q]
        rq = np.where(validq, rq, np.nan)
    else:
        r1 = lag_returns(times, close, 1, step_min)
        rq = lag_returns(times, close, q, step_min)
    ok = np.isfinite(rq) & np.isfinite(r1)                     # same support for both
    cols = np.column_stack([np.where(ok, rq * rq, 0.0), ok.astype(float), np.where(ok, r1 * r1, 0.0)])
    return group_sums(week_index(times), cols)


def vr_table(frames: dict, n_boot: int = 1000, clip: bool = False) -> pd.DataFrame:
    """Variance ratio VR(q) = Var(q-bar return) / (q Var(1-bar return)) on a common support; 1 = random walk.
    clip=True: robust variant on returns clipped at CLIP_K robust sigmas."""
    rows = []
    for base, qs, step in (("M5", VR_M5, 5), ("H1", VR_H1, 60)):
        for q in qs:
            parts = {s: _vr_stats(_t64(f[base]), f[base]["close"].to_numpy(), q, step, clip) for s, f in frames.items()}

            def fn(t, q=q):
                return (t[0] / t[1]) / (q * t[2] / t[1])
            for sym, (k, s) in parts.items():
                rows.append({"scope": sym, "base": base, "q": q, "horizon_min": q * step, "clipped": clip,
                             **block_bootstrap(s, fn, n_boot, seed=q)})
            k, s = _pooled(list(parts.values()))
            rows.append({"scope": "POOLED5", "base": base, "q": q, "horizon_min": q * step, "clipped": clip,
                         **block_bootstrap(s, fn, n_boot, seed=q)})
    return pd.DataFrame(rows)


def spread_regime_table(m5_by_sym: dict) -> pd.DataFrame:
    """Per pair and year: share of zero / 50-point-sentinel / >=30-point recorded M5 spreads and quantiles."""
    rows = []
    for sym, df in m5_by_sym.items():
        yr = df["time"].dt.year
        for y, g in df.groupby(yr):
            sp = g["spread"].to_numpy()
            rows.append({"symbol": sym, "year": int(y), "bars": len(g), "zero_pct": round(float((sp == 0).mean() * 100), 1),
                         "p10": float(np.percentile(sp, 10)), "p50": float(np.percentile(sp, 50)),
                         "p90": float(np.percentile(sp, 90)), "eq50_pct": round(float((sp == 50).mean() * 100), 1),
                         "sentinel_flagged_pct": round(float(g["spread_sentinel"].mean() * 100), 1)})
    return pd.DataFrame(rows)


# ── volatility clustering and seasonality ─────────────────────────────────────────────

def vol_cluster_table(frames: dict, n_boot: int = 500) -> pd.DataFrame:
    """ACF of hour-of-week-deseasonalised |H1 return| at several lags (volatility clustering), with CIs."""
    from research.ai.stats import corr_stats, pearson_from_sums
    rows = []
    for lag in (1, 2, 6, 24, 48):
        parts = {}
        for sym, fr in frames.items():
            h1 = fr["H1"]
            t = _t64(h1)
            r = np.abs(lag_returns(t, h1["close"].to_numpy(), 1, 60))
            how = (pd.DatetimeIndex(t).dayofweek * 24 + pd.DatetimeIndex(t).hour).to_numpy()
            seas = pd.Series(r).groupby(how).transform("mean").to_numpy()
            z = r / seas
            chain = lag_returns(t, h1["close"].to_numpy(), lag, 60)
            ok = np.isfinite(chain[lag:])
            x = np.where(ok, z[lag:], np.nan)
            parts[sym] = group_sums(week_index(t[lag:]), corr_stats(x, z[:-lag]))
        for sym, (k, s) in parts.items():
            rows.append({"scope": sym, "lag_h": lag, **block_bootstrap(s, pearson_from_sums, n_boot, seed=lag)})
        k, s = _pooled(list(parts.values()))
        rows.append({"scope": "POOLED5", "lag_h": lag, **block_bootstrap(s, pearson_from_sums, n_boot, seed=lag)})
    return pd.DataFrame(rows)


def true_range(df: pd.DataFrame) -> np.ndarray:
    h, l, c = df["high"].to_numpy(), df["low"].to_numpy(), df["close"].to_numpy()
    pc = np.concatenate([[np.nan], c[:-1]])
    return np.fmax(h - l, np.fmax(np.abs(h - pc), np.abs(l - pc)))


def seasonality_tables(frames: dict, specs: dict, floor_pips: float = 10.0, k_atr: float = 1.5):
    """By UTC hour and era: mean H1 range (pips), median recorded non-zero M5 spread (pips) and the implied cost per R,
    cost_R = (spread + round-trip commission) / stop, stop = max(floor, k_atr x ATR14(H1)). Returns (hour_df, era_df)."""
    rows = []
    for sym, fr in frames.items():
        spec = specs[sym]
        pip = ds.pip_size(spec)
        h1 = fr["H1"]
        m5 = fr["M5"]
        tr = true_range(h1)
        atr = pd.Series(tr).rolling(14).mean().shift(0).to_numpy()          # ATR including the bar just closed
        stop = np.maximum(floor_pips * pip, k_atr * atr)
        # spread in effect at the H1 decision: last M5 bar of the hour => use the H1 bar's own recorded/imputed spread
        sp_pips = h1["spread_clean_pts"].to_numpy() * spec["point"] / pip
        sp_sim = h1["spread_pts"].to_numpy() * spec["point"] / pip          # simulator-faithful (sentinel kept)
        comm_p = ds.commission_pips(sym, spec, h1["close"].to_numpy())
        cost_r = (sp_pips + comm_p) / (stop / pip)
        cost_r_sim = (sp_sim + comm_p) / (stop / pip)
        hour = h1["time"].dt.hour.to_numpy()
        era = np.full(len(h1), "", dtype=object)
        for name, a, b in ERAS:
            m = ((h1["time"] >= pd.Timestamp(a, tz="UTC")) & (h1["time"] < pd.Timestamp(b, tz="UTC"))).to_numpy()
            era[m] = name
        d = pd.DataFrame({"symbol": sym, "hour": hour, "era": era, "range_pips": (h1["high"] - h1["low"]).to_numpy() / pip,
                          "abs_ret_pips": np.abs(h1["close"].to_numpy() - h1["open"].to_numpy()) / pip,
                          "spread_pips": sp_pips, "recorded_zero": h1["spread_was_zero"].to_numpy(),
                          "cost_r": cost_r, "cost_r_sim": cost_r_sim, "stop_pips": stop / pip, "pinned": stop <= floor_pips * pip * 1.0000001,
                          "dow": h1["time"].dt.dayofweek.to_numpy()})
        rows.append(d[d["era"] != ""])
    allr = pd.concat(rows, ignore_index=True)
    allr = allr[np.isfinite(allr["cost_r"])]
    by_hour = allr.groupby(["symbol", "era", "hour"]).agg(
        range_pips=("range_pips", "mean"), abs_ret_pips=("abs_ret_pips", "mean"),
        spread_pips_med=("spread_pips", "median"), cost_r_med=("cost_r", "median"),
        stop_pips_med=("stop_pips", "median"), pinned_share=("pinned", "mean")).reset_index()
    by_dow = allr.groupby(["symbol", "dow"]).agg(range_pips=("range_pips", "mean"),
                                                 cost_r_med=("cost_r", "median")).reset_index()
    return by_hour, by_dow, allr


def run(out_dir: Path = OUT, n_boot: int = 1000) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    specs = ds.load_specs()
    syms = ds.FX5 + ["XAUUSD"]
    spans, sess = [], []
    frames = {}
    for s in syms:
        fr = {}
        for tf in ("M5", "M15", "H1", "H4"):
            full = ds.load_bars(s, tf, start=None)                     # whole history before DEV_END
            spans.append(span_row(s, tf, full))
            fr[tf] = ds.load_bars(s, tf, start=ds.DEV_START)
        fr["D1"] = d1_from_h1(fr["H1"])
        sess.append(session_structure(s, fr["M5"]))
        frames[s] = fr
    pd.DataFrame(spans).to_csv(out_dir / "spans.csv", index=False)
    (out_dir / "sessions.json").write_text(json.dumps(sess, indent=1, default=str))
    fx = {s: frames[s] for s in ds.FX5}
    acf = acf_table(fx, n_boot)
    acf.to_csv(out_dir / "acf.csv", index=False)
    vr = pd.concat([vr_table(fx, n_boot), vr_table(fx, n_boot, clip=True)], ignore_index=True)
    vr.to_csv(out_dir / "vr.csv", index=False)
    spread_regime_table({s: frames[s]["M5"] for s in syms}).to_csv(out_dir / "spread_regimes.csv", index=False)
    vc = vol_cluster_table(fx, 500)
    vc.to_csv(out_dir / "vol_cluster.csv", index=False)
    by_hour, by_dow, allr = seasonality_tables(fx, specs)
    by_hour.to_csv(out_dir / "by_hour.csv", index=False)
    by_dow.to_csv(out_dir / "by_dow.csv", index=False)
    summ = allr.groupby(["symbol", "era"]).agg(spread_pips_med=("spread_pips", "median"),
                                               cost_r_med=("cost_r", "median"), cost_r_mean=("cost_r", "mean"), cost_r_sim_mean=("cost_r_sim", "mean"),
                                               stop_pips_med=("stop_pips", "median"),
                                               pinned_share=("pinned", "mean"),
                                               recorded_zero=("recorded_zero", "mean")).reset_index()
    summ.to_csv(out_dir / "cost_per_r_summary.csv", index=False)
    return {"acf": acf, "vr": vr, "vol_cluster": vc, "by_hour": by_hour, "cost": summ,
            "spans": pd.DataFrame(spans)}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--boot", type=int, default=1000)
    a = ap.parse_args()
    res = run(n_boot=a.boot)
    pd.set_option("display.width", 200, "display.max_columns", 30, "display.max_rows", 400)
    for k, v in res.items():
        print(f"\n== {k} ==")
        print(v.round(4).to_string(index=False) if k != "by_hour" else v.head(3).to_string(index=False))
