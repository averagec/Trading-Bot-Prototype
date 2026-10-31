"""
Null bias audit: a random-direction run with every friction removed should average about 0 R (a bracket on a driftless
price has no gross edge). The stage-0 decomposition found -0.11 to -0.14 R instead. This splits those trades by where
the loss sits: entry type after the flip (LIMIT/STOP), exit reason, and whether the trade exited on its fill bar, and
counts exits filled worse than their level (gaps). It reads no skill: directions are random.

    python -m research.null_bias_audit --cands research/output/stage0_R2/base_candidates.parquet \
        --start 2014-01-01 --end 2021-06-01 --seeds 8
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd


def _done(trades: pd.DataFrame) -> pd.DataFrame:
    return trades[trades["exit_reason"] != "END"]


def overall(trades: pd.DataFrame) -> dict:
    d = _done(trades)
    return {"trades": int(len(d)), "mean_r": float(d["r"].mean()) if len(d) else float("nan")}


def bias_table(trades: pd.DataFrame) -> pd.DataFrame:
    """Mean R and count by (entry type, exit on the fill bar?, exit reason); `share_of_total_r_pct` is each group's share of the total R."""
    d = _done(trades).copy()
    d["exit_on_fill_bar"] = pd.to_datetime(d["close_T"], utc=True) == pd.to_datetime(d["open_T"], utc=True)
    g = d.groupby(["subtype", "exit_on_fill_bar", "exit_reason"]).agg(trades=("r", "size"), mean_r=("r", "mean"), sum_r=("r", "sum"))
    total = d["r"].sum()
    g["share_of_total_r_pct"] = g["sum_r"] / total * 100 if total else np.nan
    return g.reset_index()


def gap_summary(trades: pd.DataFrame) -> dict:
    """Stops filled worse than the stop level and targets filled better than the target (price gaps through a level)."""
    d = _done(trades)
    buy = d["direction"] == "BUY"
    sl = d[d["exit_reason"].isin(["SL"])]
    sl_buy = sl["direction"] == "BUY"
    worse = np.where(sl_buy, sl["exit"] < sl["sl0"] - 1e-9, sl["exit"] > sl["sl0"] + 1e-9)
    tp = d[d["exit_reason"] == "TP"]
    tp_buy = tp["direction"] == "BUY"
    better = np.where(tp_buy, tp["exit"] > tp["tp"] + 1e-9, tp["exit"] < tp["tp"] - 1e-9)
    return {"sl_exits": int(len(sl)), "sl_exits_worse_than_stop": int(worse.sum()),
            "mean_r_of_gapped_stops": float(sl["r"][worse].mean()) if worse.any() else float("nan"),
            "mean_r_of_other_stops": float(sl["r"][~worse].mean()) if (~worse).any() else float("nan"),
            "tp_exits": int(len(tp)), "tp_exits_better_than_target": int(better.sum())}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cands", default="research/output/stage0_R2/base_candidates.parquet")
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    ap.add_argument("--seeds", type=int, default=8)
    ap.add_argument("--tag", default="null_bias_audit")
    ap.add_argument("--trades", default=None, help="only break down this trade table (e.g. the REAL stage-0 trades) and exit")
    ap.add_argument("--null-geometry", default="mirror", help="mirror (original null) or reflect (whole trade reflected about the decision price)")
    args = ap.parse_args()

    if args.trades:
        tr = pd.read_csv(args.trades, parse_dates=["open_T", "close_T"])
        print(f"=== {args.trades}: {overall(tr)} ===\n{gap_summary(tr)}")
        print(bias_table(tr).round(4).to_string(index=False))
        return

    from config import PAIRS
    from research.data import load_specs, prepare_bars
    from research.nulltest import NULL_GEOMETRIES
    from research.run import OUT, _clean
    flipper = NULL_GEOMETRIES[args.null_geometry]
    from research.sim import SimConfig, Simulator

    symbols, specs = list(PAIRS), load_specs()
    cands = pd.read_parquet(args.cands)
    m5 = {s: prepare_bars(s, start=args.start, end=args.end, check_quality=False)["M5"] for s in symbols}
    base = SimConfig(fixed_risk_pct=0.25, friday_flatten_utc=20.0)
    frictionless = replace(base, commission_per_lot_side=0.0, spread_mult=0.0, use_spread_filter=False, breakeven=False)
    configs = {"frictionless, no breakeven, pessimistic intrabar (flatten on)": frictionless,
               "same, neutral intrabar": replace(frictionless, intrabar="neutral"),
               "same, no Friday flatten": replace(frictionless, friday_flatten_utc=None),
               "pessimistic intrabar, targets paid at gapped prices (tp_gap_fill)": replace(frictionless, tp_gap_fill=True),
               "neutral intrabar + tp_gap_fill (cleanest)": replace(frictionless, intrabar="neutral", tp_gap_fill=True)}
    out = {"settings": vars(args), "configs": {}}
    for name, cfg in configs.items():
        frames = []
        for sd in range(args.seeds):
            flip = np.random.default_rng(sd).random(len(cands)) < 0.5
            frames.append(Simulator(symbols, m5, specs, flipper(cands, flip), cfg).run()["trades"])
        tr = pd.concat(frames, ignore_index=True)
        tab = bias_table(tr)
        out["configs"][name] = {"overall": overall(tr), "gaps": gap_summary(tr), "groups": tab.to_dict("records")}
        print(f"\n=== {name}: {overall(tr)} ===\n{gap_summary(tr)}")
        print(tab.round(4).to_string(index=False), flush=True)
    d = OUT / args.tag
    d.mkdir(parents=True, exist_ok=True)
    (d / "null_bias_audit.json").write_text(json.dumps(_clean(out), indent=1, default=str))


if __name__ == "__main__":
    main()
