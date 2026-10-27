"""
Validate the replay against the REAL live bot: for every signal row the live bot wrote to the Excel dashboard
(2026-06 .. 2026-07), replay the same cycle on historical bars and compare decisions.

Caveats: the live code changed during that window, the live bot fetched bars a few seconds after the close,
and the dashboard only records cycles that survived the regime filter.

    python -m research.compare_live
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import openpyxl
import pandas as pd

from research.data import prepare_bars, load_specs
from research.signals import SignalConfig, build_candidates

XLSX = Path(__file__).resolve().parent.parent / "forex_bot_dashboard.xlsx"


def load_live_signals() -> pd.DataFrame:
    wb = openpyxl.load_workbook(XLSX, read_only=True, data_only=True)
    rows = list(wb["Signals"].iter_rows(values_only=True))
    hdr = rows[1]
    df = pd.DataFrame(rows[2:], columns=hdr).dropna(subset=["Timestamp"])
    df["ts"] = pd.to_datetime(df["Timestamp"], utc=True)
    df["T"] = df["ts"].dt.floor("5min")                    # last closed M5 boundary at logging time
    return df.rename(columns={"Symbol": "symbol", "Direction": "live_dir", "Probability %": "live_prob"})


def side(label) -> str:
    s = str(label)
    return "BUY" if "Bull" in s else ("SELL" if "Bear" in s else "NONE")


def main():
    live = load_live_signals()
    live = live.drop_duplicates(["symbol", "T"])
    specs = load_specs()
    cfg = SignalConfig()
    parts = []
    for sym, g in live.groupby("symbol"):
        bars = prepare_bars(sym)
        times = g["T"].dt.tz_convert("UTC").values.astype("datetime64[ns]")
        parts.append(build_candidates(sym, bars, cfg, specs[sym]["point"], diag_times=times))
    mine = pd.concat([p for p in parts if len(p)], ignore_index=True)
    m = live.merge(mine, on=["symbol", "T"], how="left")
    m["status"] = m["status"].fillna("not_evaluated")
    print(f"live rows (unique symbol/cycle): {len(live)} | replayed: {(m.status != 'not_evaluated').sum()}")
    print("replay status counts:\n", m["status"].value_counts().to_string())

    # effective replay output as the dashboard would have recorded it
    eff_dir = np.where(m["status"].isin(["below_gate", "candidate"]), m["direction"], "NONE")
    eff_prob = np.where(m["status"].isin(["below_gate", "candidate"]), m["probability"], 0.0)
    m["my_dir"], m["my_prob"] = eff_dir, eff_prob.astype(float)

    v = m[m["status"] != "regime_skip"].copy()
    v = v[v["status"] != "not_evaluated"]
    v["dir_ok"] = v["live_dir"] == v["my_dir"]
    v["prob_ok"] = (v["live_prob"].astype(float) - v["my_prob"]).abs() < 0.51
    v["trend_ok"] = v["Trend"].astype(str) == v["trend_label"].astype(str)
    both = v[(v["live_dir"] != "NONE") | (v["my_dir"] != "NONE")]

    def rate(x):
        return f"{x.mean() * 100:5.1f}%  (n={len(x)})"

    print("\n── agreement with the live bot ──")
    print("direction equal        :", rate(v["dir_ok"]))
    print("direction equal (either side non-NONE):", rate(both["dir_ok"]))
    print("probability equal      :", rate(v["prob_ok"]))
    print("H4 trend label equal   :", rate(v.loc[v["trend_label"].notna(), "trend_ok"]))
    v["period"] = v["T"].dt.strftime("%m-%d")
    byd = v.groupby("period").agg(n=("dir_ok", "size"), dir_ok=("dir_ok", "mean"), prob_ok=("prob_ok", "mean"))
    print("\nby day (live code changed during this window):\n", (byd.assign(dir_ok=byd.dir_ok.round(3),
                                                                           prob_ok=byd.prob_ok.round(3))).to_string())

    # live rows the replay says should have been regime-skipped (never logged live)
    print("\nlive-logged rows my replay would have regime-skipped:",
          int((m["status"] == "regime_skip").sum()), "of", len(m))

    # same-decision test on the thing that actually trades: candidate vs live 'would trade'
    live_sig = (v["live_dir"] != "NONE") & (v["live_prob"].astype(float) >= 50)
    my_sig = v["status"] == "candidate"
    print("\nconfusion  (live prob>=50 & dir)  vs  (replay candidate):")
    print(pd.crosstab(live_sig, my_sig, rownames=["live>=50"], colnames=["replay_candidate"]).to_string())

    out = Path(__file__).resolve().parent / "output" / "compare_live.csv"
    out.parent.mkdir(exist_ok=True)
    v.drop(columns=["LLM Reasoning"], errors="ignore").to_csv(out, index=False)
    print("\nwrote", out)


if __name__ == "__main__":
    main()
