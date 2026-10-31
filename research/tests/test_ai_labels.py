"""Tests for research/ai/labels.py: hand-computed barriers, the simulator cross-check, censoring, uniqueness weights.
Synthetic data only; nothing touches MT5 or the real cache."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from research.ai import dataset as ds                                   # noqa: E402
from research.ai.labels import (barrier_scan, commission_r, average_uniqueness, label_pair, LabelSpec,   # noqa: E402
                                _friday_flatten_open)
from research.synth import synth_bars                                    # noqa: E402

SPECS = {"EURUSD": {"digits": 5, "point": 1e-05, "tick_size": 1e-05, "tick_value": 1.0, "contract_size": 100000.0,
                    "volume_min": 0.01, "volume_max": 50.0, "volume_step": 0.01, "stops_level": 0,
                    "currency_base": "EUR", "currency_profit": "USD", "spread_now": 0}}
PIP = 1e-4


def flat_path(n=600, start="2024-01-02 00:00", px=1.1000):
    """Monday 2024-01-02 00:00 onward, flat M5 bars at px with zero range."""
    t = pd.date_range(start, periods=n, freq="5min", tz="UTC")
    a = np.full(n, px)
    return t, a.copy(), a.copy(), a.copy(), a.copy(), np.zeros(n)


def scan(t, O, H, L, C, S, m, stop, side, **kw):
    kw = {"rr": 3.0, "horizon_min": 2880, "flatten_hour": 20.0, **kw}
    return barrier_scan(t.values.astype("datetime64[ns]"), O, H, L, C, S, np.array([m]), np.array([stop]), side, **kw)


def test_take_profit_long_hand_computed():
    t, O, H, L, C, S = flat_path()
    H[20] = 1.1000 + 0.0061            # 61 pips above entry, stop is 20 pips -> TP at +60
    r = scan(t, O, H, L, C, S, m=10, stop=20 * PIP, side=1)
    assert r["valid"][0] and r["reason"][0] == 0
    assert r["exit_row"][0] == 20
    assert r["exit"][0] == pytest.approx(1.1060)
    gross = (r["exit"][0] - r["entry"][0]) / (20 * PIP)
    assert gross == pytest.approx(3.0)


def test_stop_wins_tie_in_same_bar():
    t, O, H, L, C, S = flat_path()
    H[15] = 1.1070
    L[15] = 1.0970                     # both barriers touched in one M5 bar
    r = scan(t, O, H, L, C, S, m=10, stop=20 * PIP, side=1)
    assert r["reason"][0] == 1 and r["exit_row"][0] == 15
    assert r["exit"][0] == pytest.approx(1.0980)


def test_stop_gap_through_fills_at_open_long_and_short_pays_spread():
    t, O, H, L, C, S = flat_path()
    O[12], H[12], L[12], C[12] = 1.0950, 1.0950, 1.0950, 1.0950          # gaps 50 pips below a 20 pip stop
    r = scan(t, O, H, L, C, S, m=10, stop=20 * PIP, side=1)
    assert r["reason"][0] == 1 and r["exit"][0] == pytest.approx(1.0950)
    # short with a 2-pip spread: stop is hit on high + spread, exit at the ask
    t, O, H, L, C, S = flat_path()
    S[:] = 2 * PIP
    H[14] = 1.0990
    O[14] = 1.0985
    H[14] = 1.0999 + 0.0002                                              # high 1.1001 + spread 0.0002 >= 1.1020? no
    r = scan(t, O, H, L, C, S, m=10, stop=20 * PIP, side=-1)
    assert r["reason"][0] in (2, 3)                                      # nothing hit: ends on a barrier
    # now actually hit the short stop: entry is the bid 1.1000, stop 1.1020, hit when high + 0.0002 >= 1.1020
    H[16] = 1.1019
    r = scan(t, O, H, L, C, S, m=10, stop=20 * PIP, side=-1)
    assert r["reason"][0] == 1 and r["exit_row"][0] == 16 and r["exit"][0] == pytest.approx(1.1020)


def test_long_entry_pays_the_spread_short_does_not():
    t, O, H, L, C, S = flat_path()
    S[:] = 1.5 * PIP
    rl = scan(t, O, H, L, C, S, m=10, stop=20 * PIP, side=1)
    rs = scan(t, O, H, L, C, S, m=10, stop=20 * PIP, side=-1)
    assert rl["entry"][0] == pytest.approx(1.1000 + 1.5 * PIP)
    assert rs["entry"][0] == pytest.approx(1.1000)


def test_time_barrier_exits_at_close_of_bar_ending_at_T_plus_horizon():
    t, O, H, L, C, S = flat_path(n=800)
    C[10 + 12 * 4] = 1.1010            # bar index m + 48: ends at T + 4 hours
    r = scan(t, O, H, L, C, S, m=10, stop=20 * PIP, side=1, horizon_min=240)
    assert r["reason"][0] == 2
    assert r["exit_row"][0] == 10 + 48
    assert r["exit"][0] == pytest.approx(1.1010)


def test_friday_flatten_closes_at_19_55_bar_and_blocks_late_decisions():
    # Friday 2024-01-05, M5 bars from Friday 00:00
    t, O, H, L, C, S = flat_path(n=288 + 100, start="2024-01-05 00:00")
    m = 12 * 18                                    # bar opening 18:00, decision T = 18:05
    f = np.flatnonzero(t == pd.Timestamp("2024-01-05 19:55", tz="UTC"))[0]
    C[f] = 1.1008
    r = scan(t, O, H, L, C, S, m=m, stop=20 * PIP, side=1)
    assert r["reason"][0] == 3 and r["exit_row"][0] == f and r["exit"][0] == pytest.approx(1.1008)
    # decision at 20:00 or later on Friday is invalid
    m_late = int(np.flatnonzero(t == pd.Timestamp("2024-01-05 19:55", tz="UTC"))[0])      # T = 20:00
    r2 = scan(t, O, H, L, C, S, m=m_late, stop=20 * PIP, side=1)
    assert not r2["valid"][0]


def test_flatten_uses_stop_before_flatten_on_the_flatten_bar():
    t, O, H, L, C, S = flat_path(n=288 + 100, start="2024-01-05 00:00")
    f = np.flatnonzero(t == pd.Timestamp("2024-01-05 19:55", tz="UTC"))[0]
    L[f] = 1.0970                                  # stop touched on the flatten bar itself
    r = scan(t, O, H, L, C, S, m=12 * 18, stop=20 * PIP, side=1)
    assert r["reason"][0] == 1 and r["exit_row"][0] == f


def test_censored_at_end_of_data_is_invalid_not_filled():
    t, O, H, L, C, S = flat_path(n=60)             # 5 hours of data, horizon 48 h, no barrier touched
    r = scan(t, O, H, L, C, S, m=10, stop=20 * PIP, side=1)
    assert not r["valid"][0]


def test_label_uses_only_path_after_decision():
    """Truncating the data after the exit bar must not change the label; changing bars after it must not either."""
    t, O, H, L, C, S = flat_path(n=900)
    H[30] = 1.1070
    full = scan(t, O, H, L, C, S, m=10, stop=20 * PIP, side=1)
    cut = 31
    part = scan(t[:cut], O[:cut], H[:cut], L[:cut], C[:cut], S[:cut], m=10, stop=20 * PIP, side=1)
    assert part["valid"][0] and part["exit_row"][0] == full["exit_row"][0]
    H2, L2 = H.copy(), L.copy()
    H2[31:], L2[31:] = 9.0, 0.1
    alt = scan(t, O, H2, L2, C, S, m=10, stop=20 * PIP, side=1)
    assert alt["exit_row"][0] == full["exit_row"][0] and alt["exit"][0] == full["exit"][0]


def test_commission_in_r_is_half_pip_over_stop_for_eurusd():
    c = commission_r("EURUSD", SPECS["EURUSD"], np.array([1.1]), np.array([20 * PIP]), 2.5)
    assert c[0] == pytest.approx(0.5 / 20)


def test_friday_flatten_open_helper():
    T = np.array(["2024-01-05T19:00", "2024-01-05T20:00", "2024-01-06T01:00", "2024-01-03T10:00"], dtype="datetime64[ns]")
    valid, fo = _friday_flatten_open(T, 20.0)
    assert valid.tolist() == [True, False, False, True]
    assert fo[0] == np.datetime64("2024-01-05T19:55") and fo[3] == np.datetime64("2024-01-05T19:55")


def test_average_uniqueness_known_cases():
    # no overlap
    assert average_uniqueness(np.array([0, 5]), np.array([2, 7]), 10) == pytest.approx([1.0, 1.0])
    # two identical spans -> each half unique
    assert average_uniqueness(np.array([0, 0]), np.array([3, 3]), 5) == pytest.approx([0.5, 0.5])
    # partial overlap: A=[0,3], B=[2,5]: A alive alone at 0,1 (1) and shared at 2,3 (0.5) -> (1+1+.5+.5)/4
    u = average_uniqueness(np.array([0, 2]), np.array([3, 5]), 6)
    assert u == pytest.approx([0.75, 0.75])


# ── cross-check against the real simulator ────────────────────────────────────────────

def _sim_r(m5, specs, side, m, stop, ts):
    from research.sim import Simulator, SimConfig
    px_bid = float(m5["close"].iat[m])
    sp = float(m5["spread_pts"].iat[m]) * specs["EURUSD"]["point"]
    ask = px_bid + sp
    price = ask if side > 0 else px_bid
    cand = pd.DataFrame([{
        "symbol": "EURUSD", "T": ts, "bar_idx": m, "direction": "BUY" if side > 0 else "SELL", "probability": 80.0,
        "raw_score": 80, "checks_passed": 5, "entry_type": "MARKET", "subtype": "MARKET", "price": price,
        "sl_raw": price - side * stop, "atr_factor": 1.0, "size_factor": 1.0, "bid": px_bid, "ask": ask,
        "spread_pts": float(m5["spread_pts"].iat[m]), "trend_strong": True, "fvg_sig": "BUY", "sweep_sig": "NONE",
        "qt_phase": "London", "pattern": "None"}])
    cfg = SimConfig(breakeven=False, friday_flatten_utc=20.0, fixed_risk_pct=0.25, monthly_target_pct=None,
                    daily_dd_pct=99.0, use_spread_filter=False, rr_ratio=3.0)
    res = Simulator(["EURUSD"], {"EURUSD": m5}, specs, cand, cfg).run()
    tr = res["trades"]
    if not len(tr) or tr["exit_reason"].iat[0] == "END":
        return None, None
    return float(tr["r"].iat[0]), tr["exit_reason"].iat[0]


def test_matches_simulator_on_random_synthetic_trades():
    bars = synth_bars(n_days=14, seed=3, start="2024-01-01", step_sd=0.0004)
    m5 = bars["M5"].copy()
    rng = np.random.default_rng(5)
    m5["spread_pts"] = rng.integers(1, 5, len(m5)).astype(float)
    t = m5["time"].values.astype("datetime64[ns]")
    O, H, L, C = (m5[c].to_numpy(float) for c in ("open", "high", "low", "close"))
    S = m5["spread_pts"].to_numpy(float) * 1e-5
    picks = rng.choice(np.arange(300, len(m5) - 400), 40, replace=False)
    compared = 0
    for m in picks:
        for side in (1, -1):
            stop = float(rng.uniform(12, 30)) * PIP
            lab = barrier_scan(t, O, H, L, C, S, np.array([m]), np.array([stop]), side, 3.0, 7 * 24 * 60, 20.0)
            if not lab["valid"][0]:
                continue
            ts = pd.Timestamp(t[m] + np.timedelta64(5, "m"), tz="UTC")
            r_sim, why = _sim_r(m5, SPECS, side, int(m), stop, ts)
            if r_sim is None:
                continue
            comm = commission_r("EURUSD", SPECS["EURUSD"], np.array([C[m]]), np.array([stop]), 2.5)[0]
            r_lab = side * (lab["exit"][0] - lab["entry"][0]) / stop - comm
            assert why == {0: "TP", 1: "SL", 3: "FLAT"}[int(lab["reason"][0])]
            assert r_lab == pytest.approx(r_sim, abs=0.03), (m, side, why)
            compared += 1
    assert compared >= 30


def test_label_pair_runs_and_flags_floor():
    bars = synth_bars(n_days=14, seed=4, start="2024-01-01", step_sd=0.0003)
    m5 = bars["M5"].copy()
    m5["spread_pts"] = 2.0
    m5["spread_clean_pts"] = 2.0
    h1 = bars["H1"].copy()
    h1["spread_pts"] = 2.0
    out = label_pair("EURUSD", h1, m5, SPECS, LabelSpec(horizon_min=1440), tf_min=60)
    assert len(out) == len(h1)
    assert out["valid_L"].sum() > 100
    assert (out.loc[out["valid_L"], "w_L"] > 0).all() and (out.loc[out["valid_L"], "w_L"] <= 1).all()
    assert out["stop_pips"].dropna().min() >= 10 - 1e-9
    # net R is gross minus commission
    v = out["valid_L"]
    assert ((out.loc[v, "rg_L"] - out.loc[v, "r_L"]) > 0).all()
