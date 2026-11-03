"""Data screens for the FX carry study (rates + D1 prices). Structure only: no returns of any rule are computed.

    python -m research.carry_screens rates     # gaps, first/last, jumps, negatives, flat runs of each OECD series
    python -m research.carry_screens prices    # bar counts, gaps, zero/sentinel spreads of the D1 pair files present
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
RATES = ROOT / "data_multi" / "rates"
MULTI = ROOT / "data_multi"
OUT = ROOT / "output" / "fx_carry_dev"
CTRY = ["USA", "EUR", "GBR", "JPN", "AUS", "CAN", "CHE", "NZL", "NOR", "SWE"]


def screen_rates() -> str:
    L = []
    for c in CTRY:
        df = pd.read_csv(RATES / f"{c}.csv")
        df.columns = ["date", "v"]
        df["date"] = pd.to_datetime(df["date"])
        raw_na = int(df["v"].isna().sum() + (df["v"].astype(str).str.strip() == ".").sum())
        df["v"] = pd.to_numeric(df["v"], errors="coerce")
        d = df.dropna().set_index("date")["v"]
        full = pd.date_range(d.index.min(), d.index.max(), freq="MS")
        miss = full.difference(d.index)
        dd = d.diff().abs()
        flat = (d.diff() == 0).astype(int)
        runs = flat.groupby((flat == 0).cumsum()).cumsum().max()
        win = d["2002-01-01":"2021-05-31"]
        wm = pd.date_range("2002-01-01", "2021-05-01", freq="MS").difference(win.index)
        L.append(f"{c}: first {d.index.min().date()} last {d.index.max().date()} n={len(d)} nan_or_dot={raw_na} missing_months_inside={len(miss)} {[str(m.date()) for m in miss[:6]]}")
        L.append(f"     min {d.min():.2f} max {d.max():.2f} negatives={(d<0).sum()} (first {d[d<0].index.min().date() if (d<0).any() else '-'}) "
                 f"max_abs_monthly_change={dd.max():.2f} on {dd.idxmax().date()} longest_flat_run_months={int(runs)} missing_in_2002-01..2021-05={len(wm)}")
        L.append(f"     2002-01..2021-05: mean {win.mean():.2f}  2003-01 {d.get('2003-01-01', np.nan):.2f}  2008-09 {d.get('2008-09-01', np.nan):.2f}  2021-05 {d.get('2021-05-01', np.nan):.2f}")
    return "\n".join(L)


def screen_prices() -> str:
    from research.data import sentinel_mask
    L = []
    for f in sorted(MULTI.glob("*_D1.parquet")):
        sym = f.name.split("_")[0]
        df = pd.read_parquet(f, filters=[("time", "<", pd.Timestamp("2021-06-01", tz="UTC"))]).sort_values("time").reset_index(drop=True)
        if df.empty:
            L.append(f"{sym}: no bars before the DEV end (history starts later)")
            continue
        t = df["time"]
        gap = t.diff().dt.days
        sp = df["spread"].astype(float)
        yr = t.dt.year
        z = (sp <= 0).groupby(yr).mean()
        sent = sp.eq(50).groupby(yr).mean()
        wk = (t.dt.dayofweek >= 5).sum()
        L.append(f"{sym}: bars {len(df)} {t.iloc[0].date()} -> {t.iloc[-1].date()} weekend_bars={wk} max_gap_days={int(gap.max())} gaps>4d={(gap>4).sum()} "
                 f"dup_times={int(t.duplicated().sum())} nonpos_prices={(df[['open','high','low','close']]<=0).any(axis=1).sum()} "
                 f"hl_viol={((df.high<df[['open','close']].max(axis=1))|(df.low>df[['open','close']].min(axis=1))).sum()}")
        L.append("     zero-spread share by year: " + " ".join(f"{y}:{v:.2f}" for y, v in z.items() if v > 0))
        L.append("     spread==50 share by year: " + " ".join(f"{y}:{v:.2f}" for y, v in sent.items() if v > 0.02))
        L.append("     median spread (pts) by year: " + " ".join(f"{y}:{v:.0f}" for y, v in sp.where(sp > 0).groupby(yr).median().items() if np.isfinite(v)))
    return "\n".join(L)


def screen_specs() -> str:
    """Broker swap snapshot (as of the fetch day, NOT history) for the nine USD pairs: implied mark-up = -(long + short)/2 per lot per day.
    Converted to percent of notional per year with the 2021-05-31 close (DEV-window price, an approximation only: no later price is read)."""
    import json
    from research.fx_carry import FOREIGN, PAIR_OF
    L = ["pair: swap_long swap_short mode | implied mark-up pts/lot/day | implied differential pts/lot/day | mark-up % p.a. of notional (approx, 2021-05-31 price)"]
    for c in FOREIGN:
        sym, usd_base = PAIR_OF[c]
        sp = MULTI / f"{sym}_spec.json"
        f = MULTI / f"{sym}_D1.parquet"
        if not sp.exists() or not f.exists():
            L.append(f"{sym}: MISSING")
            continue
        s = json.loads(sp.read_text())
        mk = -(s["swap_long"] + s["swap_short"]) / 2.0
        df = pd.read_parquet(f, filters=[("time", "<", pd.Timestamp("2021-06-01", tz="UTC"))])
        px = float(df.sort_values("time")["close"].iloc[-1])
        if s["swap_mode"] != 1:
            L.append(f"{sym}: swap_mode {s['swap_mode']} (not points): not converted; long {s['swap_long']} short {s['swap_short']}")
            continue
        usd_per_pt = s["point"] * s["contract_size"] / (px if usd_base else 1.0)         # USD value of one point per lot (quote ccy -> USD)
        notional_usd = s["contract_size"] * (1.0 if usd_base else px)
        pct = mk * usd_per_pt * 365 / notional_usd * 100
        L.append(f"{sym}: {s['swap_long']} {s['swap_short']} {s['swap_mode']} | {mk:.2f} | {(s['swap_long'] - s['swap_short']) / 2:.2f} | {pct:.2f}  (price {px:.5g})")
    return "\n".join(L)


def screen_rates_window() -> str:
    """DEV-window (2002-04 .. 2021-05) structure of the rate panel: flat runs, jumps, yearly cross-sectional dispersion. No returns."""
    L = []
    panel = {}
    for c in CTRY:
        df = pd.read_csv(RATES / f"{c}.csv")
        df.columns = ["date", "v"]
        df["date"] = pd.to_datetime(df["date"])
        df["v"] = pd.to_numeric(df["v"], errors="coerce")
        panel[c] = df.set_index("date")["v"]
    P = pd.DataFrame(panel).sort_index()
    W = P["2002-01-01":"2021-05-01"]
    for c in CTRY:
        x = W[c].dropna()
        flat = (x.diff() == 0)
        grp = (~flat).cumsum()
        runs = flat.groupby(grp).sum()
        long = [(str(x.index[grp == g][0].date()), int(n) + 1) for g, n in runs.items() if n >= 3]
        d = x.diff().abs()
        big = d[d > 0.75]
        L.append(f"{c}: months in window {len(x)} flat runs>=4 months (start, length) {long} abs monthly change >0.75: {[(str(i.date()), round(float(v),2)) for i, v in big.items()]}")
    W2 = W.copy()
    spread = (W2.max(axis=1) - W2.min(axis=1))
    mean = W2.mean(axis=1)
    L.append("calendar year: cross-sectional max-min and mean of the 10 rates (percent)")
    for y, g in spread.groupby(spread.index.year):
        L.append(f"  {y}: range {g.mean():.2f}  mean {mean[mean.index.year == y].mean():.2f}  months with all-10 data {int(W2[W2.index.year == y].notna().all(axis=1).sum())}")
    return "\n".join(L)


if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else "rates"
    txt = {"rates": screen_rates, "prices": screen_prices, "window": screen_rates_window, "specs": screen_specs}[what]()
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"screen_{what}.txt").write_text(txt + "\n")
    print(txt)
