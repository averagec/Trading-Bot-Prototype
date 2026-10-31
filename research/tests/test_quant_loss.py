"""Loss anatomy (research/quant_loss.py). Synthetic data only: hand-built paths with known excursions."""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from research import quant_loss as ql   # noqa: E402

SPECS = {"EURUSD": dict(digits=5, point=1e-5, contract_size=100000, volume_min=0.01, volume_max=100, volume_step=0.01,
                        stops_level=0, currency_profit="USD")}
T0 = pd.Timestamp("2020-01-06 00:00", tz="UTC")


def bars(rows):
    """rows: list of (high, low) per M5 bar, bar i opens at T0 + 5 i minutes (closes 5 minutes later)."""
    n = len(rows)
    return {"EURUSD": pd.DataFrame({"time": [T0 + pd.Timedelta(minutes=5 * i) for i in range(n)], "open": 1.1,
                                    "high": [h for h, _ in rows], "low": [l for _, l in rows], "close": 1.1, "spread_pts": 10.0})}


def trade(direction, entry, sl0, open_bar_close, close_bar_close, reason, **kw):
    base = {"symbol": "EURUSD", "direction": direction, "entry": entry, "sl0": sl0, "exit_reason": reason,
            "open_T": T0 + pd.Timedelta(minutes=5 * open_bar_close), "close_T": T0 + pd.Timedelta(minutes=5 * close_bar_close),
            "hold_min": 5 * (close_bar_close - open_bar_close), "r": -1.0, "net_pnl": -100.0, "risk_usd": 100.0, "gross_pnl": -95.0,
            "commission": 5.0, "spread_pts": 10.0, "risk_pct": 0.25, "subtype": "LIMIT", "placed_T": T0}
    base.update(kw)
    return base


def pad(rows):
    return rows + [(1.1000, 1.1000)] * 20


def test_buy_excursions_hand_checked():
    # entry 1.1000 stop 1.0990 (dist 0.0010 = 1R). Trade is open from the close of bar 9 (= 50 min) to the close of bar 12.
    rows = [(1.1, 1.1)] * 10 + [(1.1004, 1.0998), (1.1012, 1.0999), (1.1006, 1.0989)]
    t = pd.DataFrame([trade("BUY", 1.1000, 1.0990, 10, 13, "SL")])
    e = ql.excursions(t, bars(pad(rows)), SPECS)
    r = e.iloc[0]
    assert r["mfe_incl"] == pytest.approx(1.2)
    assert r["mfe_pre"] == pytest.approx(1.2)            # the 1.2R spike was on bar 11, before the exit bar
    assert r["mae"] == pytest.approx(1.1)
    assert r["ft_0.5"] == 1.0 and r["ft_1.0"] == 1.0 and r["ft_2.0"] == 0.0 and r["ft_3.0"] == 0.0


def test_exit_bar_spike_counts_only_in_mfe_incl_and_ties_go_to_the_stop():
    rows = [(1.1, 1.1)] * 10 + [(1.1003, 1.0996), (1.1020, 1.0989)]     # last bar touches both +2R and the stop
    t = pd.DataFrame([trade("BUY", 1.1000, 1.0990, 10, 12, "SL")])
    e = ql.excursions(t, bars(pad(rows)), SPECS).iloc[0]
    assert e["mfe_pre"] == pytest.approx(0.3) and e["mfe_incl"] == pytest.approx(2.0)
    assert e["ft_1.0"] == 0.0 and e["ft_2.0"] == 0.0                    # same-bar tie: the stop wins


def test_sell_uses_ask_side_spread():
    # SELL entry (bid) 1.1000, stop 1.1010. spread 10 points = 1 pip = 0.0001.
    rows = [(1.1, 1.1)] * 10 + [(1.1005, 1.0990)]
    t = pd.DataFrame([trade("SELL", 1.1000, 1.1010, 10, 11, "FLAT")])
    e = ql.excursions(t, bars(pad(rows)), SPECS).iloc[0]
    assert e["mfe_incl"] == pytest.approx((1.1000 - (1.0990 + 0.0001)) / 0.0010)    # 0.9
    assert e["mae"] == pytest.approx(((1.1005 + 0.0001) - 1.1000) / 0.0010)          # 0.6


def test_fill_bar_stop_has_no_later_bar():
    t = pd.DataFrame([trade("BUY", 1.1000, 1.0990, 10, 10, "SL", hold_min=0)])
    e = ql.excursions(t, bars(pad([(1.1, 1.1)] * 11)), SPECS).iloc[0]
    assert e["mae"] == 1.0 and e["mfe_pre"] == 0.0 and e["mfe_incl"] == 0.0


def test_excursion_tables_shares():
    rows = [(1.1, 1.1)] * 10 + [(1.1012, 1.0999), (1.1006, 1.0989)]
    t = pd.DataFrame([trade("BUY", 1.1000, 1.0990, 10, 12, "SL"), trade("BUY", 1.1000, 1.0990, 10, 12, "SL", r=-1.0)])
    e = ql.excursions(t, bars(pad(rows)), SPECS)
    res = ql.excursion_tables(t, e)
    assert res["reach"].loc["SL", "mfe_pre>=1.0R_pct"] == 100.0
    assert res["be_gap"]["sl_exits_mfe_pre_ge_1R"] == 2
    ft = res["first_touch"]
    assert ft.loc[1.0, "p_target_before_stop_pct"] == 100.0 and ft.loc[3.0, "p_target_before_stop_pct"] == 0.0
    assert ft.loc[1.0, "driftless_zero_cost_pct"] == 50.0 and ft.loc[3.0, "driftless_zero_cost_pct"] == 25.0


def test_no_lookahead_excursions_ignore_bars_after_the_exit():
    rows = [(1.1, 1.1)] * 10 + [(1.1004, 1.0996), (1.1006, 1.0989)]
    t = pd.DataFrame([trade("BUY", 1.1000, 1.0990, 10, 12, "SL")])
    a = ql.excursions(t, bars(pad(rows)), SPECS).iloc[0]
    wild = rows + [(1.5, 0.5)] * 5                                         # absurd bars AFTER the exit
    b = ql.excursions(t, bars(wild), SPECS, scan_bars=1).iloc[0]            # scan horizon 1 bar keeps the stateless part inside the trade
    assert a["mfe_pre"] == b["mfe_pre"] and a["mfe_incl"] == b["mfe_incl"] and a["mae"] == b["mae"]


# ── costs, buckets ──────────────────────────────────────────────────────────────────────────

def test_cost_table_identity():
    t = pd.DataFrame([trade("BUY", 1.1000, 1.0990, 10, 12, "SL", r=-1.05, net_pnl=-105.0, gross_pnl=-100.0, commission=5.0, risk_usd=100.0, spread_pts=10.0),
                      trade("BUY", 1.1000, 1.0990, 10, 12, "TP", r=2.95, net_pnl=295.0, gross_pnl=300.0, commission=5.0, risk_usd=100.0, spread_pts=10.0)])
    c = ql.cost_table(t, SPECS).iloc[0]
    assert c["comm_r"] == pytest.approx(0.05) and c["spread_r"] == pytest.approx(0.10)          # 10 points = 0.0001 over a 0.0010 stop
    assert c["mean_r"] == pytest.approx(0.95)
    assert c["pre_cost_r"] - c["cost_r"] == pytest.approx(c["mean_r"])                             # pre-cost - spread - commission = net


def test_bucket_table_and_exit_shares():
    t = pd.DataFrame([trade("BUY", 1.1, 1.099, 1, 2, "SL", stop_pips=5), trade("BUY", 1.1, 1.099, 1, 2, "TP", stop_pips=5, r=3.0, net_pnl=300.0),
                      trade("BUY", 1.1, 1.099, 1, 2, "BE", stop_pips=15, r=0.1, net_pnl=10.0), trade("BUY", 1.1, 1.099, 1, 2, "SL", stop_pips=15)])
    b = ql.bucket_table(t, "stop_pips", [0, 10, 20])
    assert b.loc[pd.Interval(0, 10), "n"] == 2 and b.loc[pd.Interval(0, 10), "sl_pct"] == 50.0
    assert b.loc[pd.Interval(10, 20), "be_pct"] == 50.0 and b.loc[pd.Interval(10, 20), "loss_pct"] == 50.0


# ── streaks, drawdown, exposure ─────────────────────────────────────────────────────────────

def test_streak_lengths():
    assert ql.streak_lengths(np.array([1, 1, 0, 1, 0, 0, 1, 1, 1], bool)) == [2, 1, 3]
    assert ql.streak_lengths(np.zeros(5, bool)) == []


def test_streak_table_against_iid():
    n = 400
    pnl = np.where(np.arange(n) % 2 == 0, -1.0, 1.0)                    # strictly alternating: max streak 1, far below iid
    t = pd.DataFrame({"exit_reason": "SL", "close_T": [T0 + pd.Timedelta(hours=i) for i in range(n)], "net_pnl": pnl})
    s = ql.streak_table(t, n_sim=200)
    assert s["observed_max"] == 1 and s["loss_rate"] == pytest.approx(0.5) and s["iid_max_mean"] > 5
    assert s["p_iid_max_ge_observed"] == 1.0
    assert s["streaks_ge"][5]["observed"] == 0 and s["streaks_ge"][5]["iid_mean"] > 0.5


def test_drawdown_anatomy_hand_check():
    idx = pd.date_range("2020-01-01", periods=10, freq="D", tz="UTC")
    eq = pd.DataFrame({"T": idx, "balance": [100, 105, 110, 99, 88, 90, 100, 111, 120, 110.0],
                       "equity": [100, 105, 110, 99, 88, 90, 100, 111, 120, 110.0]})
    tr = pd.DataFrame({"close_T": [idx[3], idx[4]], "exit_reason": ["SL", "SL"], "r": [-1.0, -1.0], "symbol": ["EURUSD", "EURUSD"]})
    d = ql.drawdown_anatomy(eq, tr)
    assert d["max_dd_equity_pct"] == pytest.approx((88 / 110 - 1) * 100)
    ep = d["episodes"].iloc[0]
    assert ep["depth_pct"] == pytest.approx(-20.0) and ep["trough"] == idx[4] and ep["recovered"] == idx[7]
    assert d["worst_episode_trades"]["n"] == 2 and d["worst_episode_trades"]["sum_r"] == -2.0


def test_exposure_counts_overlaps():
    t = pd.DataFrame({"exit_reason": "SL", "risk_pct": [0.25, 0.25, 0.25, 0.25],
                      "open_T": [T0, T0 + pd.Timedelta(hours=1), T0 + pd.Timedelta(hours=1), T0 + pd.Timedelta(hours=1)],
                      "close_T": [T0 + pd.Timedelta(hours=3), T0 + pd.Timedelta(hours=2), T0 + pd.Timedelta(hours=4),
                                  T0 + pd.Timedelta(hours=1)]})                     # the 4th is a fill-bar exit: it must not count
    x = ql.exposure_table(t)
    assert x["max_open"] == 3 and x["max_open_risk_pct"] == pytest.approx(0.75)
    sh = x["time_share_by_open_count_pct"]
    assert sh[3] == pytest.approx(25.0) and sh[2] == pytest.approx(25.0) and sh[1] == pytest.approx(50.0)   # 0-1h: 1 open, 1-2h: 3, 2-3h: 2, 3-4h: 1
