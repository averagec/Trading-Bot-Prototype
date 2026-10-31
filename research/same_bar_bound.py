"""
Same-bar bound: how much could a different same-bar tie rule change the result?

The simulator lets the stop win when one M5 bar reaches both the stop and the target (research/sim.py `_bar_exit`). A different
rule could only change trades that were stopped out (SL or breakeven) on a bar that also reached the take-profit. This counts
those trades from the stored trade table and the M5 bars, and turns the count into an upper bound on the expectancy change
(if every one of them had hit the target instead). It reads no new data and changes nothing.

    python -m research.same_bar_bound --trades research/output/audit_A/base_faithful/trades.csv --start 2025-05-23

Alignment check: every TP exit must show the target inside the bar at its `close_T`; the summary reports how many do.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

BAR = pd.Timedelta(minutes=5)


def ambiguous_exits(trades: pd.DataFrame, bars_by_symbol: dict) -> pd.DataFrame:
    """One row per trade: was the take-profit also reached in the bar the trade exited on?

    bars_by_symbol[symbol] has columns time (UTC), open, high, low, close, spread (price units).
    A BUY target is reached when high >= tp, a SELL target when low + spread <= tp (the simulator's own rule)."""
    out = trades.copy()
    out["unmatched"] = False
    out["tp_reached_in_exit_bar"] = False
    out["tp_reached_in_prev_bar"] = False
    out["tp_reached_in_next_bar"] = False
    out["fill_bar"] = pd.to_datetime(out["close_T"], utc=True) == pd.to_datetime(out["open_T"], utc=True)
    indexed = {s: b.assign(time=pd.to_datetime(b["time"], utc=True)).drop_duplicates("time").set_index("time")
               for s, b in bars_by_symbol.items()}
    for i, t in out.iterrows():
        bars = indexed.get(t["symbol"])
        ts = pd.Timestamp(t["close_T"])
        ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
        ts = ts - BAR                     # the simulator stamps a step with the bar's CLOSE time (open + 5 min, sim.py:231)
        if bars is None or ts not in bars.index:
            out.at[i, "unmatched"] = True
            continue
        def hit(bar):
            return bool(bar["high"] >= t["tp"]) if t["direction"] == "BUY" else bool(bar["low"] + bar["spread"] <= t["tp"])

        pos = bars.index.get_loc(ts)
        out.at[i, "tp_reached_in_exit_bar"] = hit(bars.iloc[pos])
        if pos > 0:
            out.at[i, "tp_reached_in_prev_bar"] = hit(bars.iloc[pos - 1])
        if pos + 1 < len(bars):
            out.at[i, "tp_reached_in_next_bar"] = hit(bars.iloc[pos + 1])
    out["ambiguous"] = out["exit_reason"].isin(["SL", "BE"]) & out["tp_reached_in_exit_bar"] & ~out["unmatched"]
    return out


def summarize_bound(df: pd.DataFrame, rr: float) -> dict:
    """Counts and the upper bound on the expectancy gain per trade if every ambiguous stop-out had been a target hit
    (gain per trade = rr - its actual R, ignoring the small cost difference, so it slightly overstates)."""
    d = df[df["exit_reason"] != "END"]
    amb = d[d["ambiguous"]]
    tp = d[(d["exit_reason"] == "TP") & ~d["unmatched"]]
    n = len(d)
    return {
        "trades": int(n),
        "ambiguous": int(len(amb)),
        "ambiguous_share_pct": float(len(amb) / n * 100) if n else float("nan"),
        "ambiguous_on_fill_bar": int(amb["fill_bar"].sum()),
        "ambiguous_by_exit": amb["exit_reason"].value_counts().to_dict(),
        "upper_bound_gain_r_per_trade": float((rr - amb["r"]).sum() / n) if n else float("nan"),
        "unmatched": int(d["unmatched"].sum()),
        "alignment_tp_exits_checked": int(len(tp)),
        "alignment_tp_exits_ok": int(tp["tp_reached_in_exit_bar"].sum()),
        "alignment_failures_target_in_previous_bar": int(tp[~tp["tp_reached_in_exit_bar"]]["tp_reached_in_prev_bar"].sum()),
        "alignment_failures_target_in_next_bar": int(tp[~tp["tp_reached_in_exit_bar"]]["tp_reached_in_next_bar"].sum()),
        "alignment_failures_by_direction": tp[~tp["tp_reached_in_exit_bar"]]["direction"].value_counts().to_dict(),
        "alignment_failures_by_symbol": tp[~tp["tp_reached_in_exit_bar"]]["symbol"].value_counts().to_dict(),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trades", default="research/output/audit_A/base_faithful/trades.csv")
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    ap.add_argument("--rr", type=float, default=3.0)
    ap.add_argument("--out", default=None, help="write the summary JSON here")
    args = ap.parse_args()

    from research.data import prepare_bars, load_specs
    trades = pd.read_csv(args.trades, parse_dates=["open_T", "close_T"])
    specs = load_specs()
    bars = {}
    for s in sorted(trades["symbol"].unique()):
        m5 = prepare_bars(s, start=args.start, end=args.end, check_quality=False)["M5"]
        bars[s] = pd.DataFrame({"time": m5["time"], "open": m5["open"], "high": m5["high"], "low": m5["low"],
                                "close": m5["close"], "spread": m5["spread_pts"] * specs[s]["point"]})
    summary = summarize_bound(ambiguous_exits(trades, bars), args.rr)
    text = json.dumps(summary, indent=1, default=str)
    print(text)
    if args.out:
        Path(args.out).write_text(text)


if __name__ == "__main__":
    main()
