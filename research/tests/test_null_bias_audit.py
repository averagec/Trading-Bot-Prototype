"""research/null_bias_audit.py: where does a frictionless random-direction run lose R? Synthetic trade tables only."""

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from research import null_bias_audit as nb   # noqa: E402

T0 = pd.Timestamp("2020-01-06 10:00", tz="UTC")


def tr(subtype, reason, r, fill_bar, direction="BUY", entry=1.1, sl0=1.099, tp=1.103, exit_px=None):
    close = T0 if fill_bar else T0 + pd.Timedelta(minutes=30)
    return {"subtype": subtype, "exit_reason": reason, "r": r, "open_T": T0, "close_T": close, "direction": direction,
            "entry": entry, "sl0": sl0, "tp": tp,
            "exit": exit_px if exit_px is not None else (sl0 if reason == "SL" else tp)}


def test_groups_split_by_entry_type_fill_bar_and_exit_reason():
    t = pd.DataFrame([tr("LIMIT", "SL", -1.0, False), tr("LIMIT", "TP", 3.0, False),
                      tr("STOP", "SL", -1.0, True), tr("STOP", "SL", -1.0, True), tr("STOP", "TP", 3.0, False)])
    g = nb.bias_table(t).set_index(["subtype", "exit_on_fill_bar", "exit_reason"])
    assert g.loc[("STOP", True, "SL"), "trades"] == 2 and g.loc[("STOP", True, "SL"), "mean_r"] == pytest.approx(-1.0)
    assert g.loc[("LIMIT", False, "TP"), "mean_r"] == pytest.approx(3.0)
    assert g["trades"].sum() == 5
    assert g["share_of_total_r_pct"].sum() == pytest.approx(100.0)


def test_total_row_is_the_overall_mean():
    t = pd.DataFrame([tr("LIMIT", "SL", -1.0, False), tr("LIMIT", "TP", 3.0, False), tr("STOP", "SL", -1.0, True)])
    assert nb.overall(t)["mean_r"] == pytest.approx((-1 + 3 - 1) / 3)
    assert nb.overall(t)["trades"] == 3


def test_gap_exits_are_stops_filled_worse_than_their_level():
    t = pd.DataFrame([tr("LIMIT", "SL", -1.0, False),                                   # exact stop
                      tr("LIMIT", "SL", -1.8, False, exit_px=1.0975),                   # BUY stop gapped 25 points further
                      tr("LIMIT", "TP", 3.0, False),
                      tr("LIMIT", "TP", 3.0, False, exit_px=1.105)])                   # target gapped through but paid at the target
    gap = nb.gap_summary(t)
    assert gap["sl_exits"] == 2 and gap["sl_exits_worse_than_stop"] == 1
    assert gap["mean_r_of_gapped_stops"] == pytest.approx(-1.8)
    assert gap["tp_exits"] == 2 and gap["tp_exits_better_than_target"] == 1


def test_sell_side_gap_direction_is_mirrored():
    t = pd.DataFrame([tr("LIMIT", "SL", -1.5, False, direction="SELL", entry=1.1, sl0=1.101, tp=1.097, exit_px=1.1025)])
    assert nb.gap_summary(t)["sl_exits_worse_than_stop"] == 1


def test_end_trades_are_dropped():
    t = pd.DataFrame([tr("LIMIT", "SL", -1.0, False), tr("LIMIT", "END", 9.0, False)])
    assert nb.overall(t)["trades"] == 1
