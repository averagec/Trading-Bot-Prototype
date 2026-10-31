"""Quant method experiments (research/quant_methods.py). Synthetic data only: no MT5, no cache files."""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from research import quant_methods as qm            # noqa: E402
from research.sim import Simulator, SimConfig      # noqa: E402
from research.stoploss_experiment import COMP_SUMS  # noqa: E402

SPEC = {"EURUSD": dict(digits=5, point=1e-5, contract_size=100000, volume_min=0.01, volume_max=100,
                       volume_step=0.01, stops_level=0, currency_profit="USD")}
T0 = pd.Timestamp("2020-01-06 00:00", tz="UTC")           # a Monday


def h1_frame(n=60, seed=1):
    rng = np.random.default_rng(seed)
    close = 1.1 + np.cumsum(rng.normal(0, 0.0005, n))
    open_ = np.concatenate([[1.1], close[:-1]])
    hi = np.maximum(open_, close) + np.abs(rng.normal(0, 0.0003, n))
    lo = np.minimum(open_, close) - np.abs(rng.normal(0, 0.0003, n))
    return pd.DataFrame({"time": [T0 + pd.Timedelta(hours=i) for i in range(n)], "open": open_, "high": hi, "low": lo, "close": close})


def walk_m5(days=3, seed=3, vol=0.0002):
    rng = np.random.default_rng(seed)
    n = days * 288
    close = 1.1 + np.cumsum(rng.normal(0, vol, n))
    open_ = np.concatenate([[1.1], close[:-1]])
    hi = np.maximum(open_, close) + np.abs(rng.normal(0, vol / 2, n))
    lo = np.minimum(open_, close) - np.abs(rng.normal(0, vol / 2, n))
    return {"EURUSD": pd.DataFrame({"time": [T0 + pd.Timedelta(minutes=5 * i) for i in range(n)], "open": open_, "high": hi,
                                    "low": lo, "close": close, "spread_pts": 8.0})}


def one_candidate(m5, k=60, stop_pips=200.0, direction="BUY"):
    df = m5["EURUSD"]
    t = df["time"].iloc[k]
    px = float(df["close"].iloc[k])
    sgn = 1 if direction == "BUY" else -1
    return pd.DataFrame([{"T": t + pd.Timedelta(minutes=5), "symbol": "EURUSD", "direction": direction, "price": px + sgn * 8e-5,
                          "sl_raw": px - sgn * stop_pips * 1e-4, "atr_factor": 1.0, "size_factor": 1.0, "entry_type": "MARKET",
                          "subtype": "MARKET", "ask": px + 8e-5, "bid": px, "spread_pts": 8.0, "probability": 70,
                          "checks_passed": 4, "pattern": "x", "fvg_sig": "BUY", "sweep_sig": "NONE", "qt_phase": "London",
                          "trend_strong": True}])


# ── ATR ─────────────────────────────────────────────────────────────────────────────────────

def test_atr_hand_check_constant_range():
    n = 30
    h1 = pd.DataFrame({"time": [T0 + pd.Timedelta(hours=i) for i in range(n)], "open": 1.0, "high": 1.0010, "low": 1.0000, "close": 1.0005})
    close_ns, atr = qm.h1_atr_series(h1)
    assert np.isnan(atr[:13]).all()                     # needs a full 14-bar window
    assert atr[13:] == pytest.approx(0.0010)           # true range is 10 pips everywhere
    # the bar opening at hour 13 closes at hour 14: before that time the ATR is still the one of the previous bar (NaN here)
    T = np.array([(T0 + pd.Timedelta(hours=14)).value - 1, (T0 + pd.Timedelta(hours=14)).value, (T0 + pd.Timedelta(hours=14, minutes=30)).value])
    got = qm.atr_at(close_ns, atr, T)
    assert np.isnan(got[0]) and got[1] == pytest.approx(0.0010) and got[2] == pytest.approx(0.0010)


def test_atr_uses_previous_close_gap():
    h1 = pd.DataFrame({"time": [T0 + pd.Timedelta(hours=i) for i in range(20)], "open": 1.0, "high": 1.001, "low": 1.0, "close": 1.0005})
    h1.loc[10, ["open", "high", "low", "close"]] = [1.01, 1.0110, 1.0100, 1.0105]     # gap bar: TR = high - previous close = 106 pips
    _, atr = qm.h1_atr_series(h1)
    expected = (12 * 0.0010 + 2 * (1.0110 - 1.0005)) / 14          # bars 10 (gap up) and 11 (gap back down) both carry the 105-pip jump
    assert atr[13] == pytest.approx(expected)


def test_atr_no_lookahead_truncation():
    h1 = h1_frame(80)
    c_full, a_full = qm.h1_atr_series(h1)
    cut = 50
    c_cut, a_cut = qm.h1_atr_series(h1.iloc[:cut])
    assert np.allclose(a_full[:cut], a_cut, equal_nan=True)
    # candidate decided before bar `cut` closes sees the same ATR with or without the future
    T = np.array([(T0 + pd.Timedelta(hours=cut - 1, minutes=5)).value])
    assert qm.atr_at(c_full, a_full, T)[0] == pytest.approx(qm.atr_at(c_cut, a_cut, T)[0])
    # scrambling the future bars cannot change it either
    h2 = h1.copy()
    h2.loc[cut:, ["high", "low", "close"]] = h2.loc[cut:, ["high", "low", "close"]].to_numpy()[::-1] * 1.01
    c2, a2 = qm.h1_atr_series(h2)
    assert qm.atr_at(c2, a2, T)[0] == pytest.approx(qm.atr_at(c_full, a_full, T)[0])


def test_add_atr_and_floor_widen_both_sides():
    h1 = h1_frame(60)
    close_ns, atr = qm.h1_atr_series(h1)
    T1 = T0 + pd.Timedelta(hours=40)
    c = pd.DataFrame({"T": [T1, T1], "symbol": ["EURUSD", "EURUSD"], "direction": ["BUY", "SELL"],
                      "price": [1.1000, 1.1000], "sl_raw": [1.0999, 1.1001]})
    c = qm.add_atr(c, {"EURUSD": h1})
    a = c["atr_h1"].iloc[0]
    assert a > 0 and c["atr_h1"].iloc[1] == a
    w = qm.apply_atr_floor(c, 2.0)
    assert w["sl_raw"].iloc[0] == pytest.approx(1.1000 - 2 * a)
    assert w["sl_raw"].iloc[1] == pytest.approx(1.1000 + 2 * a)
    # an already wider stop is untouched; None and NaN ATR leave the frame unchanged
    wide = c.copy(); wide["sl_raw"] = [1.0000, 1.2000]
    assert qm.apply_atr_floor(wide, 2.0)["sl_raw"].tolist() == [1.0, 1.2]
    assert qm.apply_atr_floor(c, None)["sl_raw"].tolist() == c["sl_raw"].tolist()
    nan = c.copy(); nan["atr_h1"] = np.nan
    assert qm.apply_atr_floor(nan, 3.0)["sl_raw"].tolist() == c["sl_raw"].tolist()


# ── time stop ───────────────────────────────────────────────────────────────────────────────

def test_time_stop_closes_at_the_right_bar_and_none_changes_nothing():
    m5 = walk_m5()
    c = one_candidate(m5, k=60, stop_pips=500.0)                # 500-pip stop and 1,500-pip target: never hit in 3 days
    cfg = SimConfig(fixed_risk_pct=0.25)
    base = Simulator(["EURUSD"], m5, SPEC, c, cfg).run()["trades"]
    same = qm.TimeStopSim(["EURUSD"], m5, SPEC, c, cfg, time_stop_min=None).run()["trades"]
    assert base["exit_reason"].tolist() == same["exit_reason"].tolist() == ["END"]
    assert base["r"].tolist() == pytest.approx(same["r"].tolist())
    res = qm.TimeStopSim(["EURUSD"], m5, SPEC, c, cfg, time_stop_min=120).run()["trades"]
    assert res["exit_reason"].tolist() == ["TIME"]
    assert res["hold_min"].iloc[0] == pytest.approx(120.0)      # first step with T - open_T >= 120 minutes
    # exit at the close of that bar (BUY at bid = close)
    j = m5["EURUSD"].index[m5["EURUSD"]["time"] + pd.Timedelta(minutes=5) == res["close_T"].iloc[0]][0]
    assert res["exit"].iloc[0] == pytest.approx(m5["EURUSD"]["close"].iloc[j])


def test_time_stop_sl_wins_before_time():
    m5 = walk_m5()
    c = one_candidate(m5, k=60, stop_pips=0.5)                  # an immediately-hit stop (floor 10 pips applies)
    res = qm.TimeStopSim(["EURUSD"], m5, SPEC, c, SimConfig(fixed_risk_pct=0.25), time_stop_min=24 * 60).run()["trades"]
    assert res["exit_reason"].iloc[0] in ("SL", "BE", "TP", "TIME", "END")


# ── arms ────────────────────────────────────────────────────────────────────────────────────

def test_arm_registry_is_consistent():
    base = qm.base_config()
    assert qm.METHOD_ARMS["B0"].cfg(base) == base
    assert qm.GATE_ARMS["B0"].cfg(base) == base
    for n in qm.FAMILY_TREATMENTS:
        assert n in qm.METHOD_ARMS
    assert len(qm.FAMILY_TREATMENTS) == 10                      # + the composite = K 11, z 2.61 (prereg section 3)
    cfgs = {n: a.cfg(base) for n, a in qm.METHOD_ARMS.items()}
    assert cfgs["M2_c5"].max_spread_to_stop_pct == 5.0 and cfgs["M3_h21"].no_entry_from_utc == 21.0
    assert cfgs["M4_o1"].max_open_trades == 1 and cfgs["M5_rr4"].rr_ratio == 4.0
    assert cfgs["M1_k15"] == base and qm.METHOD_ARMS["M1_k15"].atr_k == 1.5     # ATR floors act on the candidates, not the config
    assert qm.METHOD_ARMS["M6_t24"].time_stop_min == 1440.0
    assert qm.base_config().fixed_risk_pct == 0.25 and qm.base_config().friday_flatten_utc == 20.0


def test_gate_correlation_switch_restores_filter():
    import research.sim as sim
    orig = sim.correlation_filter
    m5 = walk_m5()
    qm.GateSim(["EURUSD"], m5, SPEC, one_candidate(m5), SimConfig(fixed_risk_pct=0.25), disable_correlation=True).run()
    assert sim.correlation_filter is orig


def test_dev_end_is_refused(tmp_path):
    class A:  # a FINAL-window end must exit before anything is read
        end = "2022-01-01"; tag = "quant_x"; seeds = 1; workers = 1; cands = "x"; start = "2014-01-01"; set = "methods"; sentinel_missing = False
    with pytest.raises(SystemExit):
        qm.run_cmd(A)


# ── analysis on synthetic outputs ───────────────────────────────────────────────────────────

def _fake_outputs(tmp, shift=None, seeds=6, weeks=40):
    shift = shift or {}
    rng = np.random.default_rng(5)
    rows, wk, comp = [], [], []
    wlist = pd.date_range("2016-01-04", periods=weeks, freq="7D")
    for kind, sds in (("A", [-1]), ("N", list(range(seeds)))):
        for v in qm.METHOD_ARMS:
            for sd in sds:
                mu = -0.13 + shift.get(v, 0.0)
                n = rng.integers(2, 6, size=weeks)
                s = n * mu + rng.normal(0, 0.3, size=weeks) * np.sqrt(n)
                for w, nn, ss in zip(wlist, n, s):
                    wk.append({"kind": kind, "variant": v, "seed": sd, "week": w, "symbol": "EURUSD", "n": int(nn), "sum_r": float(ss)})
                tot_n = int(n.sum())
                rows.append({"kind": kind, "variant": v, "seed": sd, "trades": tot_n, "expectancy_r": float(s.sum() / tot_n), "win_rate_pct": 40.0,
                             "avg_hold_min": 200.0, "max_dd_pct": -30.0, "return_pct": -20.0, "worst_r": -3.0, "worst_low_pct": -2.0,
                             "calendar_days": 365 * 2, "days_low_le_3": 0, "days_low_le_5": 0, "profit_factor": 0.8, "worst_day_pnl_pct": -2.0,
                             "f_filled": tot_n})
                comp.append({"kind": kind, "variant": v, "seed": sd, "symbol": "EURUSD", "subtype": "LIMIT", "exit_reason": "SL",
                             **{c: 1.0 for c in COMP_SUMS}, "n": tot_n, "sum_r": float(s.sum())})
    pd.DataFrame(rows).to_csv(tmp / "rows.csv", index=False)
    pd.DataFrame(wk).to_parquet(tmp / "weekly.parquet", index=False)
    pd.DataFrame(comp).to_parquet(tmp / "comp.parquet", index=False)


def test_analyse_detects_a_planted_cost_effect(tmp_path):
    _fake_outputs(tmp_path, shift={"M1_k20": 0.08, "M2_c3": -0.08})
    res = qm.analyse(tmp_path, "methods", n_boot=300)
    assert res["arms"]["M1_k20"]["D_null"]["est"] > 0.03
    assert res["arms"]["M1_k20"]["cost_effect"] == "REDUCES"
    assert res["arms"]["M2_c3"]["cost_effect"] == "INCREASES"
    assert res["arms"]["M3_h20"]["cost_effect"] == "NONE"
    assert res["arms"]["M1_k20"]["years_same_sign_null"] >= 1
    assert res["arms"]["B0"]["null"]["trades_per_month"] > 0


def test_select_arms_and_composite():
    sub = qm.select_arms("methods", ["M2_c5"])
    assert set(sub) == {"B0", "M2_c5"}
    c = qm.select_arms("methods", ["M1_k15", "M2_c7"], ["M1_k15", "M2_c7"])["C1"]
    assert c.atr_k == 1.5 and dict(c.sim_over) == {"max_spread_to_stop_pct": 7.0} and c.family == "C"
    assert c.cfg(qm.base_config()).max_spread_to_stop_pct == 7.0
    assert set(qm.select_arms("methods")) == set(qm.METHOD_ARMS)
