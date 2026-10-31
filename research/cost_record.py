"""
Spread and cost record (descriptive; research/reports/cost_decomposition_prereg.md).

  python -m research.cost_record --trades research/output/stage0_R2/actual_trades.csv

1. Recorded spread by pair, period and session: median of the NON-ZERO recorded spreads (points and price units) and the share of bars that
   recorded a zero spread (the simulator imputes those, so every cost in a mostly-zero period is an estimate).
2. Cost per R from a trade table: commission over risk, and the spread over the stop distance, by pair (cost columns only; no P/L is read).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

PERIODS = {"2014-2016": ("2014-01-01", "2017-01-01"), "2017-2020": ("2017-01-01", "2021-01-01"),
           "2021-2024": ("2021-01-01", "2025-01-01"), "2025-2026": ("2025-01-01", "2027-01-01")}
SESSIONS = [("Asia 00-07", 0, 7), ("London 07-13", 7, 13), ("NY 13-21", 13, 21), ("Rollover 21-24", 21, 24)]


def spread_profile(bars: pd.DataFrame, point: float, periods: dict = None) -> pd.DataFrame:
    """bars: time (UTC) and the broker-RECORDED spread in points. One row per period x session."""
    periods = periods or PERIODS
    t = pd.to_datetime(bars["time"], utc=True)
    sp = bars["spread"].astype(float)
    rows = []
    for pname, (a, b) in periods.items():
        in_p = (t >= pd.Timestamp(a, tz="UTC")) & (t < pd.Timestamp(b, tz="UTC"))
        for sname, h0, h1 in SESSIONS:
            m = in_p & (t.dt.hour >= h0) & (t.dt.hour < h1)
            s = sp[m]
            nz = s[s > 0]
            rows.append({"period": pname, "session": sname, "bars": int(m.sum()),
                         "zero_share_pct": float((s <= 0).mean() * 100) if len(s) else float("nan"),
                         "median_nonzero_pts": float(nz.median()) if len(nz) else float("nan"),
                         "median_nonzero_price": float(nz.median() * point) if len(nz) else float("nan")})
    return pd.DataFrame(rows)


def cost_per_r(trades: pd.DataFrame, specs: dict) -> pd.DataFrame:
    """Per pair: median stop distance and spread at the decision (price units), their ratio, and the mean commission in R.
    END trades are excluded. Uses columns entry, sl0, spread_pts, commission, risk_usd only."""
    t = trades[trades["exit_reason"] != "END"].copy()
    rows = []
    for sym, g in t.groupby("symbol"):
        stop = (g["entry"] - g["sl0"]).abs()
        spread = g["spread_pts"].astype(float) * specs[sym]["point"]
        rows.append({"symbol": sym, "trades": int(len(g)), "median_stop_price": float(stop.median()),
                     "median_spread_price": float(spread.median()),
                     "spread_to_stop_pct": float(spread.median() / stop.median() * 100),
                     "commission_r_mean": float((g["commission"] / g["risk_usd"]).mean())})
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trades", default="research/output/stage0_R2/actual_trades.csv")
    ap.add_argument("--out", default="research/output/cost_record")
    args = ap.parse_args()

    from config import PAIRS
    from research.data import load_history, load_specs
    specs = load_specs()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    frames = []
    for s in PAIRS:
        df = load_history(s, "M5")          # all years: recorded spreads only (cost record), no trade result is read
        prof = spread_profile(df.rename(columns={"spread": "spread"})[["time", "spread"]], specs[s]["point"])
        prof.insert(0, "symbol", s)
        frames.append(prof)
    prof = pd.concat(frames, ignore_index=True)
    prof.to_csv(out / "spread_profile.csv", index=False)
    pd.set_option("display.width", 200, "display.max_columns", 20)
    for s in PAIRS:
        print(f"\n=== {s}: recorded spread (points; price units in the CSV) ===")
        print(prof[prof.symbol == s].pivot(index="session", columns="period",
                                           values="median_nonzero_pts").reindex([x[0] for x in SESSIONS])[list(PERIODS)].round(1).to_string())
        print("zero-spread share, % of bars:")
        print(prof[prof.symbol == s].pivot(index="session", columns="period",
                                           values="zero_share_pct").reindex([x[0] for x in SESSIONS])[list(PERIODS)].round(1).to_string())
    trades = pd.read_csv(args.trades)
    cpr = cost_per_r(trades, specs)
    cpr.to_csv(out / "cost_per_r.csv", index=False)
    print("\n=== cost per R from", args.trades, "===")
    print(cpr.round(5).to_string(index=False))


if __name__ == "__main__":
    main()
