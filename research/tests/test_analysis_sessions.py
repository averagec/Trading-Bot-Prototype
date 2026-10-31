"""Session / pair / weekend analysis helpers (research/analysis_sessions.py). Synthetic data only, hand-checkable numbers."""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from research import analysis_sessions as A      # noqa: E402
from research.sim import Simulator, SimConfig    # noqa: E402

SPECS = {
    "EURUSD": dict(point=1e-5, digits=5, contract_size=100000, volume_min=0.01, volume_max=100, volume_step=0.01, stops_level=0,
                   currency_profit="USD"),
    "USDJPY": dict(point=1e-3, digits=3, contract_size=100000, volume_min=0.01, volume_max=100, volume_step=0.01, stops_level=0,
                   currency_profit="JPY"),
    "XAUUSD": dict(point=1e-2, digits=2, contract_size=100, volume_min=0.01, volume_max=100, volume_step=0.01, stops_level=0,
                   currency_profit="USD"),
}


# ── guards ─────────────────────────────────────────────────────────────────────

def test_dev_window_guard_allows_dev_and_seen_and_refuses_final():
    A.assert_dev_window("2014-01-01", "2021-06-01")
    A.assert_dev_window("2014-01-01", "2019-01-01")
    A.assert_dev_window("2025-05-01", None)
    A.assert_dev_window("2025-06-19", "2026-10-01")
    for s, e in (("2014-01-01", None), (None, None), ("2014-01-01", "2021-06-02"), ("2021-06-01", "2025-04-01"),
                 ("2020-01-01", "2024-01-01"), ("2014-01-01", "2026-01-01")):
        with pytest.raises(ValueError):
            A.assert_dev_window(s, e)


def test_final_rows_guard():
    ok = pd.DataFrame({"placed_T": pd.to_datetime(["2021-05-31 23:55", "2025-05-01 00:00"], utc=True)})
    A.assert_no_final_rows(ok)
    bad = pd.DataFrame({"placed_T": pd.to_datetime(["2021-05-31 23:55", "2021-06-01 00:00"], utc=True)})
    with pytest.raises(ValueError):
        A.assert_no_final_rows(bad)
    A.assert_no_final_rows(pd.DataFrame({"placed_T": []}))


def test_run_one_refuses_a_final_window_before_loading_anything():
    with pytest.raises(ValueError):
        A.run_one({"scenario": "L", "symbols": ["EURUSD"], "flatten": 20.0, "seed": None, "start": "2014-01-01",
                   "end": "2022-01-01", "cand_path": "does_not_exist.parquet"})


def test_gate_and_scenario_tags_cannot_be_final_either():
    with pytest.raises(ValueError):
        A.run_gates("analyst_x", start="2014-01-01", end="2023-01-01")
    with pytest.raises(ValueError):
        A.run_scenarios("analyst_x", start="2014-01-01", end="2023-01-01")


# ── small pure helpers ─────────────────────────────────────────────────────────

def test_session_of_hour_covers_every_hour_once():
    names = [A.session_of_hour(h) for h in range(24)]
    assert names[:7] == ["Asia"] * 7 and names[7:9] == ["QT_manip"] * 2
    assert names[9:12] == ["London"] * 3 and names[12:18] == ["NewYork"] * 6
    assert names[18:22] == ["Late"] * 4 and names[22:] == ["Closed"] * 2


def test_pip_size_and_usd_sign():
    assert A.pip_size(SPECS["EURUSD"]) == pytest.approx(1e-4)
    assert A.pip_size(SPECS["USDJPY"]) == pytest.approx(1e-2)
    assert A.pip_size(SPECS["XAUUSD"]) == pytest.approx(1e-2)       # gold: one cent, the live definition
    assert A.usd_sign("EURUSD", "BUY") == -1 and A.usd_sign("EURUSD", "SELL") == 1
    assert A.usd_sign("USDJPY", "BUY") == 1 and A.usd_sign("USDCAD", "SELL") == -1
    assert A.usd_sign("XAUUSD", "BUY") == -1
    with pytest.raises(ValueError):
        A.usd_sign("EURGBP", "BUY")


def test_z_one_sided():
    assert A.z_one_sided(0.025) == pytest.approx(1.95996, abs=1e-4)
    assert A.z_one_sided(0.05 / 14) == pytest.approx(2.6901, abs=1e-3)


def _trade(**kw):
    base = dict(symbol="EURUSD", direction="BUY", placed_T="2020-03-04 14:00", open_T="2020-03-04 14:05", close_T="2020-03-04 16:00",
                entry=1.1000, sl0=1.0990, tp=1.1030, exit=1.1030, volume=1.0, risk_usd=100.0, gross_pnl=300.0, commission=5.0,
                net_pnl=295.0, r=2.95, exit_reason="TP", spread_pts=10.0, hold_min=115.0)
    base.update(kw)
    return base


def test_add_features_hand_numbers():
    t = pd.DataFrame([_trade(), _trade(exit_reason="END", symbol="USDJPY"),
                      _trade(symbol="USDJPY", entry=110.00, sl0=109.90, spread_pts=20.0, placed_T="2020-03-06 21:30", direction="SELL")])
    f = A.add_features(t, SPECS)
    assert len(f) == 2                                           # END dropped
    a = f.iloc[0]
    assert a["stop_pips"] == pytest.approx(10.0)                 # |1.1000 - 1.0990| / 1e-4
    assert a["spread_pips"] == pytest.approx(1.0)                # 10 points = 1 pip
    assert a["spread_r"] == pytest.approx(0.1)                   # 1e-4 / 1e-3
    assert a["comm_r"] == pytest.approx(0.05) and a["cost_r"] == pytest.approx(0.15)
    assert a["stop_over_spread"] == pytest.approx(10.0)
    assert a["session"] == "NewYork" and a["placed_hour"] == 14 and a["placed_dow"] == 2 and a["usd_sign"] == -1
    b = f.iloc[1]                                                # USDJPY: pip = 0.01
    assert b["stop_pips"] == pytest.approx(10.0) and b["spread_pips"] == pytest.approx(2.0)
    assert b["spread_r"] == pytest.approx(0.2) and b["session"] == "Late" and b["placed_dow"] == 4 and b["usd_sign"] == -1


# ── bootstrap ──────────────────────────────────────────────────────────────────

def _frames(n_days=300, per_day=4, effect=0.0, day_sd=0.0, noise=1.0, seeds=12, rng_seed=0):
    rng = np.random.default_rng(rng_seed)
    days = pd.date_range("2016-01-04", periods=n_days, freq="B")
    shock = rng.normal(0, day_sd, n_days)
    rows = []
    for i, d in enumerate(days):
        for _ in range(per_day):
            rows.append({"day": d, "r": effect + shock[i] + rng.normal(0, noise)})
    act = pd.DataFrame(rows)
    nl = []
    for sd in range(seeds):
        r2 = np.random.default_rng(100 + sd)
        x = act.copy()
        x["r"] = shock.repeat(per_day) + r2.normal(0, noise, len(act))      # same daily shock, no effect
        x["seed"] = sd
        nl.append(x)
    nulls = pd.concat(nl, ignore_index=True)
    for df in (act, nulls):
        df["net_pnl"], df["cost_r"], df["stop_pips"], df["stop_over_spread"] = df["r"], 0.1, 10.0, 10.0
    return act, nulls


def test_mean_boot_point_equals_plain_mean_and_null_is_mean_of_seed_means():
    act, nulls = _frames(effect=0.2)
    ctx = A.make_ctx([act, nulls], n_boot=200)
    pt, boot = A.mean_boot(act, np.ones(len(act), bool), "r", ctx)
    assert pt == pytest.approx(act["r"].mean())
    assert abs(boot.mean() - pt) < 0.05
    pn, _ = A.mean_boot(nulls, np.ones(len(nulls), bool), "r", ctx, null=True)
    assert pn == pytest.approx(nulls.groupby("seed")["r"].mean().mean())
    sub = (act["r"] > 0).to_numpy()
    ps, _ = A.mean_boot(act, sub, "r", ctx)
    assert ps == pytest.approx(act.loc[act["r"] > 0, "r"].mean())


def test_bootstrap_is_deterministic():
    act, nulls = _frames()
    c1, c2 = A.make_ctx([act, nulls], n_boot=100), A.make_ctx([act, nulls], n_boot=100)
    assert np.array_equal(c1.mult, c2.mult)
    r1 = A.bucket_row(act, nulls, np.ones(len(act), bool), np.ones(len(nulls), bool), c1)
    r2 = A.bucket_row(act, nulls, np.ones(len(act), bool), np.ones(len(nulls), bool), c2)
    assert r1 == r2


def test_bucket_row_finds_a_real_effect_and_not_a_fake_one():
    act, nulls = _frames(n_days=500, effect=0.35, rng_seed=1)
    ctx = A.make_ctx([act, nulls], n_boot=400)
    z = A.z_one_sided(0.05 / 14)
    row = A.bucket_row(act, nulls, np.ones(len(act), bool), np.ones(len(nulls), bool), ctx, z)
    assert row["skill"] == pytest.approx(0.35, abs=0.1)
    assert row["skill_lb_bonf"] > 0 and row["skill_lo"] < row["skill"] < row["skill_hi"]
    assert row["mde80"] == pytest.approx(2.8 * row["skill_se"])
    act0, nulls0 = _frames(n_days=500, effect=0.0, rng_seed=2)
    ctx0 = A.make_ctx([act0, nulls0], n_boot=400)
    row0 = A.bucket_row(act0, nulls0, np.ones(len(act0), bool), np.ones(len(nulls0), bool), ctx0, z)
    assert row0["skill_lb_bonf"] < 0 and row0["skill_lo"] < 0 < row0["skill_hi"]


def test_day_clustering_widens_the_interval():
    flat, _ = _frames(n_days=400, per_day=5, day_sd=0.0, noise=1.2, rng_seed=3)
    clus, _ = _frames(n_days=400, per_day=5, day_sd=1.0, noise=0.8, rng_seed=3)
    out = {}
    for name, df in (("flat", flat), ("clus", clus)):
        ctx = A.make_ctx([df], n_boot=600)
        _, b = A.mean_boot(df, np.ones(len(df), bool), "r", ctx)
        iid = df["r"].std(ddof=1) / np.sqrt(len(df))
        out[name] = np.std(b, ddof=1) / iid
    assert 0.8 < out["flat"] < 1.2            # no clustering: the day bootstrap matches the iid standard error
    assert out["clus"] > 1.5                  # a shared daily shock: the cluster SE is much larger than the iid one


def test_empty_bucket_and_missing_null_do_not_crash():
    act, nulls = _frames(n_days=20)
    ctx = A.make_ctx([act, nulls], n_boot=50)
    assert A.bucket_row(act, nulls, np.zeros(len(act), bool), np.zeros(len(nulls), bool), ctx) == {"n": 0}
    row = A.bucket_row(act, None, np.ones(len(act), bool), None, ctx)
    assert "skill" not in row and row["n"] == len(act)


def test_bucket_table_orders_buckets_and_counts():
    act, nulls = _frames(n_days=40)
    for df in (act, nulls):
        df["g"] = np.where(np.arange(len(df)) % 3 == 0, "a", "b")
    ctx = A.make_ctx([act, nulls], n_boot=50)
    t = A.bucket_table(act, nulls, "g", ["b", "a", "zz"], ctx)
    assert list(t["bucket"]) == ["b", "a", "zz"]
    assert t.loc[0, "n"] == (act["g"] == "b").sum() and t.loc[2, "n"] == 0


# ── weekend machinery ──────────────────────────────────────────────────────────

def _week_bars():
    """Two trading weeks of M5 bars: Mon 00:00 to Fri 21:55, then the next Monday (so Fri 21:55 -> Mon 00:00 is a 50 h gap)."""
    wk1 = pd.date_range("2020-03-02 00:00", "2020-03-06 21:55", freq="5min", tz="UTC")
    wk2 = pd.date_range("2020-03-09 00:00", "2020-03-09 12:00", freq="5min", tz="UTC")
    return pd.Series(wk1.append(wk2))


def test_weekend_windows_use_the_simulator_cycle_time_convention():
    w = A.weekend_windows(_week_bars())
    assert len(w) == 1
    assert w.iloc[0]["gap_close_T"] == pd.Timestamp("2020-03-06 22:00", tz="UTC")       # last bar open 21:55 + 5 min
    assert w.iloc[0]["reopen_T"] == pd.Timestamp("2020-03-09 00:05", tz="UTC")          # first bar open 00:00 + 5 min


def test_crosses_closure_cases():
    w = A.weekend_windows(_week_bars())
    cases = [
        ("2020-03-06 10:00", "2020-03-09 00:05", True),    # held over the weekend, closed on the first bar after the reopen
        ("2020-03-06 10:00", "2020-03-09 03:00", True),
        ("2020-03-06 10:00", "2020-03-06 19:55", False),   # flattened before the close
        ("2020-03-06 22:00", "2020-03-09 00:05", True),    # opened on the last cycle before the closure
        ("2020-03-09 00:05", "2020-03-09 06:00", False),   # opened at the reopen: nothing to cross
        ("2020-03-03 10:00", "2020-03-04 10:00", False),   # midweek
        ("2020-03-06 10:00", "2020-03-06 22:00", False),   # closed at the gap's last cycle: before the reopen
    ]
    o = pd.Series([pd.Timestamp(c[0], tz="UTC") for c in cases])
    c = pd.Series([pd.Timestamp(c[1], tz="UTC") for c in cases])
    assert list(A.crosses_closure(o, c, w)) == [x[2] for x in cases]
    assert len(A.crosses_closure(pd.Series([], dtype="datetime64[ns, UTC]"), pd.Series([], dtype="datetime64[ns, UTC]"), w)) == 0


def test_crosses_closure_with_no_closure_in_the_data():
    t = pd.Series(pd.date_range("2020-03-02", periods=50, freq="5min", tz="UTC"))
    w = A.weekend_windows(t)
    assert len(w) == 0
    assert not A.crosses_closure(pd.Series([t.iloc[1]]), pd.Series([t.iloc[9]]), w).any()


def test_gap_table_size_direction_and_fill():
    t = _week_bars().reset_index(drop=True)
    n = len(t)
    close = np.full(n, 1.1000)
    df = pd.DataFrame({"time": t, "open": close, "high": close + 0.0002, "low": close - 0.0002, "close": close, "spread": 12.0})
    b = int(np.flatnonzero((t.diff() > pd.Timedelta(hours=40)).to_numpy())[0])
    df.loc[b:, ["open", "high", "low", "close"]] = df.loc[b:, ["open", "high", "low", "close"]] + 0.0020      # 20-pip gap up
    df.loc[b + 3, "low"] = 1.0990                                                                              # re-crosses 1.1000 on bar 4
    g = A.gap_table(df, SPECS["EURUSD"], horizons=(2, 5, 50))
    assert len(g) == 1
    assert g.iloc[0]["gap_pips"] == pytest.approx(20.0)
    assert g.iloc[0]["gap_hours"] == pytest.approx(50.0 + 5 / 60, abs=0.1)
    assert (not g.iloc[0]["fill_2"]) and g.iloc[0]["fill_5"] and g.iloc[0]["fill_50"]
    assert g.iloc[0]["first_spread_pts"] == 12.0


def test_hourly_spread_ignores_zero_sentinel_synthetic_and_weekend_bars():
    t = pd.date_range("2020-03-02 00:00", "2020-03-06 23:55", freq="5min", tz="UTC")
    df = pd.DataFrame({"time": t, "spread": 10.0})
    df["spread_was_sentinel"] = False
    df["spread_synthetic"] = False
    hour = df["time"].dt.hour
    df.loc[hour == 3, "spread"] = 30.0
    df.loc[(hour == 3) & (df["time"].dt.minute < 30), "spread"] = 0.0           # zeros are not real recordings
    df.loc[(hour == 5), "spread_was_sentinel"] = True                           # whole hour is placeholder: no data for it
    df.loc[(hour == 6) & (df["time"].dt.day == 4), "spread_synthetic"] = True
    sat = pd.DataFrame({"time": pd.date_range("2020-03-07 03:00", periods=12, freq="5min", tz="UTC"), "spread": 999.0,
                        "spread_was_sentinel": False, "spread_synthetic": False})
    h = A.hourly_spread(pd.concat([df, sat], ignore_index=True), SPECS["EURUSD"])
    assert h.loc[3, "median_pips"] == pytest.approx(3.0)            # 30 points = 3 pips, zeros and the Saturday bars excluded
    assert h.loc[3, "n"] == 6 * 5
    assert h.loc[10, "median_pips"] == pytest.approx(1.0)
    assert 5 not in h.index
    assert h.loc[6, "n"] == 12 * 4                                  # the synthetic day's bars are excluded


def test_weekly_open_spread_ratio():
    t = _week_bars()
    df = pd.DataFrame({"time": t, "spread": 10.0, "spread_was_sentinel": False, "spread_synthetic": False})
    b = int(np.flatnonzero((t.diff() > pd.Timedelta(hours=40)).to_numpy())[0])
    df.loc[b, "spread"] = 100.0
    w = A.weekly_open_spread(df, SPECS["EURUSD"], n_bars=3)
    assert w.loc[0, "median_pips"] == pytest.approx(10.0)           # 100 points = 10 pips
    assert w.loc[0, "ratio_to_overall_median"] == pytest.approx(10.0) and w.loc[1, "ratio_to_overall_median"] == pytest.approx(1.0)


# ── clustering ─────────────────────────────────────────────────────────────────

def test_eigen_neff_limits():
    assert A.eigen_neff(np.eye(6)) == pytest.approx(6.0)
    assert A.eigen_neff(np.ones((6, 6))) == pytest.approx(1.0)
    c = np.full((4, 4), 0.5) + 0.5 * np.eye(4)
    assert 1.0 < A.eigen_neff(c) < 4.0
    assert A.eigen_neff(c) == pytest.approx((4.0 ** 2) / (2.5 ** 2 + 3 * 0.5 ** 2))


def test_icc_recovers_known_variance_ratio_and_is_zero_for_iid():
    rng = np.random.default_rng(0)
    days = np.repeat(np.arange(600), 5)
    shock = rng.normal(0, 1.0, 600).repeat(5)
    r = shock + rng.normal(0, 1.0, 3000)                            # true ICC = 1 / (1 + 1) = 0.5
    out = A.icc_oneway(days, r)
    assert out["icc"] == pytest.approx(0.5, abs=0.06)
    assert out["deff"] == pytest.approx(1 + 4 * out["icc"]) and out["n_eff"] == pytest.approx(3000 / out["deff"])
    iid = A.icc_oneway(days, rng.normal(0, 1, 3000))
    assert abs(iid["icc"]) < 0.06


def test_icc_degenerate_input():
    assert np.isnan(A.icc_oneway(np.arange(5), np.arange(5.0))["icc"])


def test_daily_r_matrix_zero_fills_and_sums_per_day_and_symbol():
    tr = pd.DataFrame({"day": pd.to_datetime(["2020-03-02", "2020-03-02", "2020-03-04"]), "symbol": ["EURUSD", "EURUSD", "USDJPY"],
                       "r": [1.0, -3.0, 2.0]})
    m = A.daily_r_matrix(tr, ["EURUSD", "USDJPY"], pd.bdate_range("2020-03-02", "2020-03-06"))
    assert m.shape == (5, 2)
    assert m.loc["2020-03-02", "EURUSD"] == -2.0 and m.loc["2020-03-04", "USDJPY"] == 2.0 and m.loc["2020-03-03"].sum() == 0


def test_day_agreement_counts_pairs_on_different_symbols_only():
    df = pd.DataFrame({"symbol": ["EURUSD", "GBPUSD", "USDJPY", "EURUSD", "GBPUSD"],
                       "usd_sign": [-1, -1, 1, 1, 1],
                       "day": pd.to_datetime(["2020-03-02"] * 3 + ["2020-03-03"] * 2)})
    days = pd.DatetimeIndex(sorted(df["day"].unique()))
    ctx = A.BootCtx(days=days, mult=np.ones((2, 1)))
    ag, tot = A.day_agreement(df, 2, A._didx(df, ctx))
    # day 1: EUR-GBP agree, EUR-JPY and GBP-JPY disagree; day 2: EUR-GBP agree
    assert list(tot) == [3, 1] and list(ag) == [1, 1]
    same_sym = df.iloc[[0, 3]].assign(day=pd.Timestamp("2020-03-02"))
    ag2, tot2 = A.day_agreement(same_sym.reset_index(drop=True), 2, np.zeros(2, int))
    assert tot2.sum() == 0                                           # two EURUSD trades are not a cross-pair bet


# ── funnel by hour ─────────────────────────────────────────────────────────────

def test_funnel_by_hour_adds_up_to_the_funnel():
    spec = {"EURUSD": SPECS["EURUSD"]}
    t0 = pd.Timestamp("2024-03-05 10:00", tz="UTC")
    path = [(1.1000, 1.1001, 1.0999, 1.1000)] * 3 + [(1.1000, 1.1000, 1.0985, 1.0995)] + [(1.0995, 1.0995, 1.0995, 1.0995)] * 60
    rows = [(t0 + pd.Timedelta(minutes=5 * i), *b) for i, b in enumerate(path)]
    m5 = pd.DataFrame(rows, columns=["time", "open", "high", "low", "close"])
    m5["spread_pts"] = 0.0
    c = pd.DataFrame([{"T": t0 + pd.Timedelta(minutes=10), "symbol": "EURUSD", "direction": "BUY", "price": 1.0990, "sl_raw": 1.0970,
                       "atr_factor": 1.0, "size_factor": 1.0, "entry_type": "LIMIT", "subtype": "LIMIT", "ask": 1.0990, "bid": 1.0990,
                       "spread_pts": 0.0, "probability": 70, "checks_passed": 4, "pattern": "x", "fvg_sig": "BUY", "sweep_sig": "NONE",
                       "qt_phase": "London", "trend_strong": True},
                      {"T": t0 + pd.Timedelta(minutes=50), "symbol": "EURUSD", "direction": "BUY", "price": 1.0990, "sl_raw": 1.0970,
                       "atr_factor": 1.0, "size_factor": 1.0, "entry_type": "LIMIT", "subtype": "LIMIT", "ask": 1.0990, "bid": 1.0990,
                       "spread_pts": 0.0, "probability": 70, "checks_passed": 4, "pattern": "x", "fvg_sig": "BUY", "sweep_sig": "NONE",
                       "qt_phase": "London", "trend_strong": True}])
    cfg = SimConfig(commission_per_lot_side=0.0, monthly_target_pct=None, breakeven=False)
    plain = Simulator(["EURUSD"], {"EURUSD": m5}, spec, c, cfg).run()
    res = A.FunnelSim(["EURUSD"], {"EURUSD": m5}, spec, c, cfg).run()
    assert res["funnel"] == plain["funnel"]                          # the subclass does not change behaviour
    assert len(res["trades"]) == len(plain["trades"])
    by_key = {}
    for k, v in res["funnel_by_hour"].items():
        by_key[k.split("|", 1)[1]] = by_key.get(k.split("|", 1)[1], 0) + v
    assert by_key == {k: v for k, v in res["funnel"].items() if v}   # every count is booked under exactly one hour
    assert res["funnel_by_hour"].get("10|orders_placed") == 1 and res["funnel_by_hour"].get("10|candidates_reached") == 2


def test_scenario_defs():
    d = A.scenario_defs(40, 20)
    assert d["L"]["flatten"] == 20.0 and d["N"]["flatten"] is None and d["L"]["symbols"] == d["N"]["symbols"]
    drops = [k for k in d if k.startswith("S_no")]
    assert len(drops) == 6 and all(len(d[k]["symbols"]) == 5 for k in drops)
    assert d["S_top3"]["symbols"] == ["EURUSD", "GBPUSD", "USDJPY"]
    assert all(d[k]["seeds"] == 20 for k in d if k.startswith("S_")) and d["L"]["seeds"] == 40
