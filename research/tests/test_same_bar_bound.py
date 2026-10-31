"""research/same_bar_bound.py: how many stop-outs shared their exit bar with the take-profit (the only trades a
different same-bar tie rule could have changed). Synthetic bars and trades only."""

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from research import same_bar_bound as sb   # noqa: E402

T0 = pd.Timestamp("2025-06-02 10:00", tz="UTC")


def bars(rows):
    """rows: (minutes_from_T0, open, high, low, close, spread_price)"""
    return pd.DataFrame({"time": [T0 + pd.Timedelta(minutes=m) for m, *_ in rows],
                         "open": [r[1] for r in rows], "high": [r[2] for r in rows], "low": [r[3] for r in rows],
                         "close": [r[4] for r in rows], "spread": [r[5] for r in rows]})


def trade(direction, tp, exit_reason, r, minutes, exit_px, symbol="EURUSD", opened=0):
    """`minutes` / `opened` are the OPEN times of the exit / fill bars; the simulator stamps trades with the bar's close
    time (open + 5 min), which is what the trade table holds."""
    return {"symbol": symbol, "direction": direction, "tp": tp, "exit": exit_px, "exit_reason": exit_reason, "r": r,
            "open_T": T0 + pd.Timedelta(minutes=opened + 5), "close_T": T0 + pd.Timedelta(minutes=minutes + 5)}


M5 = bars([(0, 1.1000, 1.1005, 1.0995, 1.1000, 0.0001),     # fill bar
           (5, 1.1000, 1.1002, 1.0990, 1.0992, 0.0001),     # SL bar for a BUY with sl 1.0990, tp far away
           (10, 1.1000, 1.1060, 1.0985, 1.1050, 0.0001),    # wild bar: low <= SL and high >= TP both reached
           (15, 1.1000, 1.1031, 1.0999, 1.1030, 0.0001)])   # TP bar for BUY tp 1.1030


def test_a_stop_out_whose_exit_bar_also_reached_the_target_is_ambiguous():
    t = pd.DataFrame([trade("BUY", 1.1030, "SL", -1.0, 5, 1.0990),            # bar 5 high 1.1002 < tp: not ambiguous
                      trade("BUY", 1.1030, "SL", -1.0, 10, 1.0990),           # bar 10 high 1.1060 >= tp: ambiguous
                      trade("BUY", 1.1030, "TP", 3.0, 15, 1.1030)])
    out = sb.ambiguous_exits(t, {"EURUSD": M5})
    assert list(out["ambiguous"]) == [False, True, False]
    assert list(out["tp_reached_in_exit_bar"]) == [False, True, True]


def test_sell_side_uses_the_ask_for_the_target():
    """A SELL target is hit when low + spread <= tp (the sim's own rule)."""
    t = pd.DataFrame([trade("SELL", 1.0987, "SL", -1.0, 10, 1.1005)])          # low 1.0985 + 0.0001 <= 1.0987
    assert bool(sb.ambiguous_exits(t, {"EURUSD": M5}).loc[0, "ambiguous"])
    t2 = pd.DataFrame([trade("SELL", 1.0980, "SL", -1.0, 10, 1.1005)])         # low 1.0985 + 0.0001 > 1.0980
    assert not bool(sb.ambiguous_exits(t2, {"EURUSD": M5}).loc[0, "ambiguous"])


def test_fill_bar_exits_are_flagged_separately():
    t = pd.DataFrame([trade("BUY", 1.1030, "SL", -1.0, 10, 1.0990, opened=10)])
    out = sb.ambiguous_exits(t, {"EURUSD": M5})
    assert bool(out.loc[0, "fill_bar"]) and bool(out.loc[0, "ambiguous"])


def test_summary_bound_is_the_largest_possible_gain_per_trade():
    t = pd.DataFrame([trade("BUY", 1.1030, "SL", -1.0, 10, 1.0990),           # ambiguous: could become +rr
                      trade("BUY", 1.1030, "BE", 0.0, 10, 1.1002),            # ambiguous break-even exit
                      trade("BUY", 1.1030, "SL", -1.0, 5, 1.0990),
                      trade("BUY", 1.1030, "TP", 3.0, 15, 1.1030)])
    s = sb.summarize_bound(sb.ambiguous_exits(t, {"EURUSD": M5}), rr=3.0)
    assert s["trades"] == 4 and s["ambiguous"] == 2
    assert s["upper_bound_gain_r_per_trade"] == pytest.approx(((3.0 + 1.0) + (3.0 - 0.0)) / 4)
    assert s["alignment_tp_exits_checked"] == 1 and s["alignment_tp_exits_ok"] == 1


def test_misaligned_bars_are_reported_not_hidden():
    shifted = M5.copy()
    shifted["time"] = shifted["time"] + pd.Timedelta(minutes=10)               # every bar two steps late: minute 15 now holds the quiet minute-5 bar
    t = pd.DataFrame([trade("BUY", 1.1030, "TP", 3.0, 15, 1.1030)])
    s = sb.summarize_bound(sb.ambiguous_exits(t, {"EURUSD": shifted}), rr=3.0)
    assert s["alignment_tp_exits_ok"] == 0 and s["alignment_tp_exits_checked"] == 1


def test_exits_without_a_matching_bar_are_counted_as_unmatched():
    t = pd.DataFrame([trade("BUY", 1.1030, "SL", -1.0, 500, 1.0990)])
    out = sb.ambiguous_exits(t, {"EURUSD": M5})
    assert not bool(out.loc[0, "ambiguous"]) and bool(out.loc[0, "unmatched"])
    assert sb.summarize_bound(out, rr=3.0)["unmatched"] == 1
