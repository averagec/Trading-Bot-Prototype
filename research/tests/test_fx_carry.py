"""Tests for research/fx_carry.py (synthetic data only: no MetaTrader5, no live code, no project returns).

Covers: DEV-only guards (prices and rates), the one-month rate lag and no-look-ahead by truncation and by scrambling the future,
a hand-computed interest accrual, return orientation, rank / continuous / combined weights, volatility scaling, P&L arithmetic,
the label-permutation and sign-flip nulls, Holm, the gates and the decision table, the analytic power, the freeze / FINAL guards,
and the synthetic planted-carry calibration.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from research import fx_carry as fc  # noqa: E402

IDX = {c: i for i, c in enumerate(fc.ALL10)}


def fake_rates(seed: int = 3, start="1999-01-01", end="2021-05-01") -> pd.DataFrame:
    """Deterministic, persistent, well-separated monthly rates (percent) for the ten currencies."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, end, freq="MS")
    base = {"USD": 2.0, "EUR": 1.5, "GBP": 3.0, "JPY": 0.2, "AUD": 4.5, "CAD": 2.5, "CHF": 0.5, "NZD": 5.0, "NOK": 3.5, "SEK": 2.0}
    cols = {}
    for c in fc.ALL10:
        walk = np.cumsum(rng.normal(0, 0.05, len(idx)))
        cols[c] = np.maximum(base[c] + walk - walk.mean(), 0.01)
    return pd.DataFrame(cols, index=idx)


@pytest.fixture(scope="module")
def rates():
    return fake_rates()


@pytest.fixture(scope="module")
def world(rates):
    return fc.synth_world(np.random.default_rng(5), rates, 0.0)


@pytest.fixture(scope="module")
def panel(world, rates):
    return fc.build_panel(world, rates)


# ── guards ───────────────────────────────────────────────────────────────────────────────

def test_check_end_refuses_final_and_missing():
    assert fc.check_end("2021-06-01") == pd.Timestamp("2021-06-01", tz="UTC")
    with pytest.raises(ValueError):
        fc.check_end("2021-06-02")
    with pytest.raises(ValueError):
        fc.check_end("2025-04-30")
    with pytest.raises(ValueError):
        fc.check_end(None)


def test_check_dev_window_refuses_final_start():
    fc.check_dev_window("2003-01-01", "2021-06-01")
    with pytest.raises(ValueError):
        fc.check_dev_window("2021-06-01", "2021-12-01")
    with pytest.raises(ValueError):
        fc.check_dev_window("2021-07-01", "2022-01-01")
    with pytest.raises(ValueError):
        fc.check_dev_window("2003-01-15", "2021-06-01")        # not a month start
    with pytest.raises(ValueError):
        fc.check_dev_window("2003-01-01", "2021-06-02")


def test_build_panel_refuses_final_window(world, rates):
    with pytest.raises(ValueError):
        fc.build_panel(world, rates, "2003-01-01", "2021-07-01")
    with pytest.raises(ValueError):
        fc.build_panel(world, rates, "2021-06-01", "2022-01-01")
    with pytest.raises(ValueError):
        fc.build_panel(world, rates, lag=0)


def _write_pair_files(d: Path, sym: str, start: str, end: str):
    days = pd.bdate_range(start, end, tz="UTC")
    n = len(days)
    px = 0.7 + 0.0001 * np.arange(n)
    df = pd.DataFrame({"time": days, "open": px, "high": px + 0.001, "low": px - 0.001, "close": px + 0.0002, "tick_volume": 1,
                       "spread": np.where(np.arange(n) % 3 == 0, 0, 12), "real_volume": 0})
    df.to_parquet(d / f"{sym}_D1.parquet", index=False)
    (d / f"{sym}_spec.json").write_text(json.dumps({"point": 1e-5, "digits": 5}))


def test_load_pair_file_level_end_guard(tmp_path):
    _write_pair_files(tmp_path, "AUDUSD", "2020-01-01", "2022-12-30")
    p = fc.load_pair("AUD", end="2021-06-01", data_dir=tmp_path)
    assert p.date.max() < np.datetime64("2021-06-01")
    assert p.date.max() == np.datetime64("2021-05-31")
    with pytest.raises(ValueError):
        fc.load_pair("AUD", end="2021-06-02", data_dir=tmp_path)
    with pytest.raises(ValueError):
        fc.load_pair("AUD", end="2022-12-30", data_dir=tmp_path)
    with pytest.raises(ValueError):
        fc.load_pair("AUD", end=None, data_dir=tmp_path)
    assert (p.hs >= fc.floor_frac("AUD") - 1e-15).all()           # zero spreads were replaced, the floor applies


def test_load_dev_world_refuses_missing_pair(tmp_path):
    _write_pair_files(tmp_path, "AUDUSD", "2020-01-01", "2021-05-31")
    with pytest.raises(FileNotFoundError):
        fc.load_dev_world(data_dir=tmp_path)


def test_load_rates_cuts_at_end_and_refuses_beyond():
    r = fc.load_rates("2021-06-01")
    assert r.index.max() == pd.Timestamp("2021-05-01")
    assert list(r.columns) == fc.ALL10
    with pytest.raises(ValueError):
        fc.load_rates("2021-07-01")
    r2 = fc.load_rates("2010-01-01")
    assert r2.index.max() == pd.Timestamp("2009-12-01")
    assert pd.isna(r.loc["2020-04-01", "USD"])                     # the one FRED gap in the DEV window (documented)


def test_load_rates_dot_is_nan(tmp_path):
    for c in fc.ALL10:
        (tmp_path / f"{fc.COUNTRY[c]}.csv").write_text("observation_date,X\n2010-01-01,1.5\n2010-02-01,.\n2010-03-01,2.0\n")
    r = fc.load_rates("2021-06-01", tmp_path)
    assert pd.isna(r.loc["2010-02-01", "EUR"]) and r.loc["2010-03-01", "EUR"] == 2.0
    assert fc.rates_ffilled(r).loc["2010-02-01", "EUR"] == 1.5      # carried forward


def test_rates_ffill_limit():
    idx = pd.date_range("2010-01-01", periods=8, freq="MS")
    s = pd.DataFrame({"USD": [1.0] + [np.nan] * 7}, index=idx)
    ff = fc.rates_ffilled(s)
    assert ff["USD"].iloc[:4].notna().all() and ff["USD"].iloc[4:].isna().all()


# ── calendar ─────────────────────────────────────────────────────────────────────────────

def test_decision_dates_are_last_weekdays():
    d = fc.decision_dates("2003-01-01", "2021-06-01")
    assert d[0] == np.datetime64("2002-12-31") and d[-1] == np.datetime64("2021-05-31")
    assert len(d) == 12 * 18 + 5 + 1
    dow = pd.DatetimeIndex(d).dayofweek
    assert (dow < 5).all()
    nxt = pd.DatetimeIndex(d) + pd.offsets.BDay(1)
    assert (nxt.month != pd.DatetimeIndex(d).month).all()            # the next weekday is in the next month
    assert np.datetime64("2011-04-29") in d                         # Friday month end
    assert np.datetime64("2010-06-30") in d


def test_panel_shapes_and_year(panel):
    assert panel.K == 222 and panel.Kp == 221
    assert panel.ED[0] == np.datetime64("2003-01-01") and panel.ED[-1] == np.datetime64("2021-06-01")
    assert panel.year[0] == 2003 and panel.year[-1] == 2021
    assert (panel.days > 27).all() and (panel.days < 36).all()
    assert panel.days.sum() == (panel.ED[-1] - panel.ED[0]).astype(int)       # the accrual calendar telescopes exactly
    assert panel.live.all()


# ── accrual, hand computed ───────────────────────────────────────────────────────────────

def test_accrual_hand_computed_two_months():
    idx = pd.date_range("1999-01-01", "2021-05-01", freq="MS")
    r = pd.DataFrame(1.0, index=idx, columns=fc.ALL10)
    r.loc["2003-01-01", ["AUD", "USD"]] = [4.80, 1.20]
    r.loc["2003-02-01", ["AUD", "USD"]] = [4.50, 1.30]
    rng = np.random.default_rng(1)
    pairs = fc.synth_world(rng, r, 1.0)
    pn = fc.build_panel(pairs, r)
    # period 0: exec 2003-01-01 to exec 2003-02-03 -> 31 January days + 2 February days; AUD basis 365, USD basis 360
    jan = 31 * (0.0480 / 365 - 0.0120 / 360)
    feb2 = 2 * (0.0450 / 365 - 0.0130 / 360)
    assert pn.ED[0] == np.datetime64("2003-01-01") and pn.ED[1] == np.datetime64("2003-02-03")
    assert pn.acc[0, IDX["AUD"] - 1] == pytest.approx(jan + feb2, rel=1e-12)
    # period 1: exec 2003-02-03 to exec 2003-03-03 -> 26 February days (3rd to 28th) + 2 March days (rates 1.0 / 1.0)
    assert pn.ED[2] == np.datetime64("2003-03-03")
    assert pn.acc[1, IDX["AUD"] - 1] == pytest.approx(26 * (0.0450 / 365 - 0.0130 / 360) + 2 * (0.01 / 365 - 0.01 / 360), rel=1e-12)
    # EUR is 1.00 against USD 1.20 / 1.30: negative accrual for a long EUR position (basis 360 both legs)
    assert pn.acc[0, IDX["EUR"] - 1] == pytest.approx(31 * (0.01 / 360 - 0.012 / 360) + 2 * (0.01 / 360 - 0.013 / 360), rel=1e-12)
    # a short position earns the opposite: accrual enters the P&L with the weight sign
    W = np.zeros((pn.K, 9))
    W[0, IDX["AUD"] - 1] = -0.5
    net, parts = fc.net_from_W(pn, W, 0.0, 1.0, parts=True)
    assert parts["acc"][0] == pytest.approx(-0.5 * (jan + feb2), rel=1e-12)


def test_accrual_uses_realised_month_not_lagged_rate():
    """The decision uses month m-1; the accrual is the rate of the month actually held. Changing the held month moves the accrual, not the weight."""
    r0 = fake_rates()
    r1 = r0.copy()
    r1.loc["2008-03-01", "AUD"] += 3.0                     # the month held after the end-February decision
    pairs = fc.synth_world(np.random.default_rng(2), r0, 1.0)
    a, b = fc.build_panel(pairs, r0), fc.build_panel(pairs, r1)
    j = int(np.where(a.dec == np.datetime64("2008-02-29"))[0][0])
    assert not np.allclose(a.acc[j], b.acc[j])
    np.testing.assert_array_equal(a.carry[: j + 1], b.carry[: j + 1])


# ── orientation ──────────────────────────────────────────────────────────────────────────

def test_pair_orientation_and_return_sign(rates):
    days = pd.bdate_range("2000-03-30", "2021-05-31")
    n = len(days)
    world = fc.synth_world(np.random.default_rng(4), rates, 1.0)
    jump = np.searchsorted(days.to_numpy("datetime64[D]"), np.datetime64("2005-06-14"))
    close = np.where(np.arange(n) >= jump, 110.0, 100.0)
    openp = np.concatenate([[100.0], close[:-1]])
    world["JPY"] = fc.make_pair("JPY", "USDJPY", True, days.to_numpy("datetime64[D]"), openp, close, np.full(n, 10.0), 1e-3)
    # a USD-quoted pair with the opposite jump: EUR from 1.00 to 1.10
    eclose = np.where(np.arange(n) >= jump, 1.10, 1.00)
    world["EUR"] = fc.make_pair("EUR", "EURUSD", False, days.to_numpy("datetime64[D]"), np.concatenate([[1.0], eclose[:-1]]), eclose, np.full(n, 10.0), 1e-5)
    pn = fc.build_panel(world, rates)
    j = int(np.where(pn.dec == np.datetime64("2005-05-31"))[0][0])                   # held June 2005, contains the jump
    assert pn.ret[j, IDX["JPY"] - 1] == pytest.approx(100.0 / 110.0 - 1.0)           # USDJPY up = JPY down vs USD
    assert pn.ret[j, IDX["EUR"] - 1] == pytest.approx(1.10 / 1.00 - 1.0)
    assert pn.ret[j - 1, IDX["JPY"] - 1] == 0.0 and pn.ret[j + 1, IDX["EUR"] - 1] == 0.0


def test_spread_cost_uses_only_previous_bars():
    days = np.arange(np.datetime64("2010-01-01"), np.datetime64("2010-04-01"))
    days = days[pd.DatetimeIndex(days).dayofweek < 5]
    n = len(days)
    p = np.full(n, 1.0)
    sp1 = np.full(n, 30.0)
    sp2 = sp1.copy()
    sp2[40] = 5000.0
    a = fc.make_pair("EUR", "EURUSD", False, days, p, p, sp1, 1e-5)
    b = fc.make_pair("EUR", "EURUSD", False, days, p, p, sp2, 1e-5)
    np.testing.assert_array_equal(a.hs[:41], b.hs[:41])                                  # bar 40's own spread does not touch its cost
    assert b.hs[41] > a.hs[41] or b.hs[41] == a.hs[41]
    assert a.hs[30] == pytest.approx(30 * 1e-5)                                          # above the 1 bp floor
    c = fc.make_pair("NOK", "USDNOK", True, days, np.full(n, 8.0), np.full(n, 8.0), np.full(n, 0.0), 1e-5)
    assert c.hs[30] == pytest.approx(4e-4)                                               # zero recorded -> floor of 4 bp for NOK


# ── signals ──────────────────────────────────────────────────────────────────────────────

def test_k_rule():
    assert [fc.k_of(n) for n in (6, 7, 8, 9, 10)] == [2, 2, 2, 3, 3]


def test_carry_rank_weights_long_high_short_low():
    carry = np.array([[2.0, 1.5, 3.0, 0.2, 4.5, 2.5, 0.5, 5.0, 3.5, 2.1]])
    live = np.ones((1, 10), bool)
    w = fc.raw_weights("carry_rank", carry, np.zeros((1, 10)), live)[0]
    assert w[IDX["NZD"]] == pytest.approx(1 / 3) and w[IDX["AUD"]] == pytest.approx(1 / 3) and w[IDX["NOK"]] == pytest.approx(1 / 3)
    assert w[IDX["JPY"]] == pytest.approx(-1 / 3) and w[IDX["CHF"]] == pytest.approx(-1 / 3) and w[IDX["EUR"]] == pytest.approx(-1 / 3)
    assert w.sum() == pytest.approx(0) and np.abs(w).sum() == pytest.approx(2)
    assert np.count_nonzero(w) == 6


def test_rank_ties_deterministic_and_small_universe():
    carry = np.zeros((1, 10))
    live = np.ones((1, 10), bool)
    w1 = fc.raw_weights("carry_rank", carry, carry, live)[0]
    w2 = fc.raw_weights("carry_rank", carry, carry, live)[0]
    np.testing.assert_array_equal(w1, w2)
    lv = live.copy()
    lv[0, [IDX["NOK"], IDX["SEK"]]] = False
    c2 = np.array([[2.0, 1.5, 3.0, 0.2, 4.5, 2.5, 0.5, 5.0, 3.5, 2.1]])
    w = fc.raw_weights("carry_rank", c2, np.zeros((1, 10)), lv)[0]
    assert np.count_nonzero(w) == 4 and w[IDX["NOK"]] == 0 and w[IDX["SEK"]] == 0       # n = 8 -> k = 2
    lv5 = np.zeros((1, 10), bool)
    lv5[0, :5] = True
    assert np.abs(fc.raw_weights("carry_rank", c2, np.zeros((1, 10)), lv5)).sum() == 0     # fewer than 6 live: flat


def test_continuous_weights_zero_sum_gross_two():
    carry = np.array([[2.0, 1.5, 3.0, 0.2, 4.5, 2.5, 0.5, 5.0, 3.5, 2.1]])
    w = fc.raw_weights("carry_cont", carry, np.zeros((1, 10)), np.ones((1, 10), bool))[0]
    assert w.sum() == pytest.approx(0, abs=1e-12) and np.abs(w).sum() == pytest.approx(2)
    assert w[IDX["NZD"]] > w[IDX["AUD"]] > w[IDX["NOK"]] > 0 > w[IDX["CHF"]] > w[IDX["JPY"]]


def test_momentum_and_combo_weights():
    carry = np.array([[2.0, 1.5, 3.0, 0.2, 4.5, 2.5, 0.5, 5.0, 3.5, 2.1]])
    mom = np.array([[0.0, 0.05, -0.02, 0.10, -0.08, 0.0, 0.01, -0.10, 0.07, 0.03]])
    live = np.ones((1, 10), bool)
    wm = fc.raw_weights("mom_rank", carry, mom, live)[0]
    assert wm[IDX["JPY"]] > 0 and wm[IDX["NOK"]] > 0 and wm[IDX["EUR"]] > 0
    assert wm[IDX["NZD"]] < 0 and wm[IDX["AUD"]] < 0 and wm[IDX["GBP"]] < 0
    wc = fc.raw_weights("combo_rank", carry, mom, live)[0]
    assert wc.sum() == pytest.approx(0) and np.count_nonzero(wc) == 6
    wcar = fc.raw_weights("carry_rank", carry, mom, live)[0]
    assert not np.array_equal(wc, wcar) or not np.array_equal(wc, wm)


def test_momentum_skips_last_month(panel, world):
    j = 120
    d = panel.dec[j]
    for c in ("EUR", "JPY"):
        p = world[c]
        i = np.searchsorted(p.date, d, side="right") - 1
        want = np.log(p.closeX[i - fc.MOM_SKIP]) - np.log(p.closeX[i - fc.MOM_L])
        assert panel.mom[j, IDX[c]] == pytest.approx(want)
    assert panel.mom[j, 0] == 0.0


def test_vol_scale_hits_target_and_cap(panel):
    w10 = fc.raw_weights("carry_rank", panel.carry, panel.mom, panel.live)
    wf = w10[:, 1:]
    sc = fc.vol_scale(wf, panel.Sig)
    for j in (10, 100, 200):
        var = wf[j] @ panel.Sig[j] @ wf[j]
        if sc[j] < fc.CAP:
            assert sc[j] * np.sqrt(var) == pytest.approx(fc.TARGET)
        else:
            assert sc[j] == fc.CAP
    tiny = np.zeros((1, 9, 9)) + np.eye(9)[None] * 1e-8
    assert fc.vol_scale(np.ones((1, 9)) / 9, tiny)[0] == fc.CAP
    assert fc.vol_scale(np.zeros((1, 9)), tiny)[0] == 0.0


def test_realised_vol_close_to_target(panel):
    ev = fc.evaluate(panel, "carry_rank")
    vol = ev["S0"].std(ddof=1) * np.sqrt(12)
    assert 0.06 < vol < 0.15


# ── P&L arithmetic ───────────────────────────────────────────────────────────────────────

def _mini_panel():
    Kp = 3
    ret = np.zeros((Kp, 9))
    acc = np.zeros((Kp, 9))
    hs = np.full((Kp, 9), 2e-4)
    ret[:, 0] = [0.01, -0.02, 0.03]
    acc[:, 0] = [0.002, 0.002, 0.002]
    ret[:, 1] = [0.005, 0.0, -0.01]
    acc[:, 1] = [-0.001, -0.001, -0.001]
    ED = np.array(["2010-02-01", "2010-03-01", "2010-04-01", "2010-05-03"], dtype="datetime64[D]")
    return fc.Panel(dec=ED - 1, ED=ED, live=np.ones((4, 10), bool), carry=np.zeros((4, 10)), mom=np.zeros((4, 10)), Sig=np.zeros((4, 9, 9)),
                    ret=ret, acc=acc, hs=hs, days=np.array([28.0, 31.0, 33.0]))


def test_net_from_W_matches_hand_calculation():
    pn = _mini_panel()
    W = np.zeros((4, 9))
    W[:, 0] = [0.5, 0.5, -0.25, 9.9]          # the 4th row (no period) must be ignored
    W[:, 1] = [-0.5, 0.0, 0.5, 9.9]
    net, p = fc.net_from_W(pn, W, markup=0.02, cost_mult=2.0, parts=True)
    half = 0.5 * 2.0 * 2e-4 + fc.COMM
    gross = [0.5 * (0.01 + 0.002) - 0.5 * (0.005 - 0.001), 0.5 * (-0.02 + 0.002) + 0.0, -0.25 * (0.03 + 0.002) + 0.5 * (-0.01 - 0.001)]
    trade = [0.5 + 0.5, 0.0 + 0.5, 0.75 + 0.5]
    mk = [(0.5 + 0.5) * 0.02 * 28 / 365, (0.5 + 0.0) * 0.02 * 31 / 365, (0.25 + 0.5) * 0.02 * 33 / 365]
    for i in range(3):
        assert net[i] == pytest.approx(gross[i] - trade[i] * half - mk[i], rel=1e-12)
    assert p["turnover"].tolist() == pytest.approx(trade)


def test_sharpe_annualisation():
    x = np.array([0.01, 0.02, 0.0, 0.03, -0.01, 0.02])
    assert fc.sharpe(x) == pytest.approx(x.mean() / x.std(ddof=1) * np.sqrt(12))
    assert fc.sharpe(np.zeros(10)) == 0.0
    assert fc.sharpe(np.vstack([x, 2 * x])).shape == (2,)


# ── no look-ahead ────────────────────────────────────────────────────────────────────────

def _truncate(world, rates, T: np.datetime64, lag: int):
    wt = {}
    for c, p in world.items():
        m = p.date <= T
        wt[c] = fc.Pair(p.ccy, p.sym, p.usd_base, p.date[m], p.openX[m], p.closeX[m], p.hs[m], {})
    last_rate_month = (T.astype("datetime64[M]") - np.timedelta64(lag, "M")).astype("datetime64[ns]")
    return wt, rates[rates.index <= pd.Timestamp(last_rate_month)]


@pytest.mark.parametrize("T", ["2012-06-29", "2009-02-27", "2017-12-29"])
def test_no_lookahead_by_truncation_including_rate_lag(world, rates, panel, T):
    T = np.datetime64(T)
    wt, rt = _truncate(world, rates, T, fc.LAG_MONTHS)
    end = pd.Timestamp((T.astype("datetime64[M]") + np.timedelta64(1, "M")).astype("datetime64[D]"))
    pt = fc.build_panel(wt, rt, "2003-01-01", end)
    K = pt.K
    assert pt.dec[-1] == T
    np.testing.assert_array_equal(pt.live, panel.live[:K])
    np.testing.assert_allclose(pt.carry, panel.carry[:K])
    np.testing.assert_allclose(pt.mom, panel.mom[:K])
    np.testing.assert_allclose(pt.Sig, panel.Sig[:K], rtol=1e-10, equal_nan=True)
    for kind in fc.ALL_KINDS.values():
        w_all = fc.raw_weights(kind, panel.carry, panel.mom, panel.live)
        w_tr = fc.raw_weights(kind, pt.carry, pt.mom, pt.live)
        np.testing.assert_allclose(w_tr, w_all[:K])
        np.testing.assert_allclose(fc.scaled_weights(pt, w_tr), fc.scaled_weights(panel, w_all)[:K], rtol=1e-10)
    np.testing.assert_allclose(pt.ret[: K - 2], panel.ret[: K - 2])           # the last period's exit price is the end of the data
    np.testing.assert_allclose(pt.hs, panel.hs[: K - 1])
    np.testing.assert_allclose(pt.acc[: K - 3], panel.acc[: K - 3])      # the last two periods touch rate months not yet published


def test_scrambling_the_future_changes_nothing_before_it(world, rates, panel):
    T = np.datetime64("2012-06-29")
    rng = np.random.default_rng(9)
    ws = {}
    for c, p in world.items():
        m = p.date > T
        ox, cx = p.openX.copy(), p.closeX.copy()
        ox[m], cx[m] = rng.uniform(0.3, 3.0, m.sum()), rng.uniform(0.3, 3.0, m.sum())
        ws[c] = fc.Pair(p.ccy, p.sym, p.usd_base, p.date, ox, cx, p.hs, {})
    r2 = rates.copy()
    r2[r2.index >= pd.Timestamp("2012-06-01")] = 77.0                   # month m and later: unknown at the June decision
    p2 = fc.build_panel(ws, r2)
    j = int(np.where(panel.dec == T)[0][0])
    np.testing.assert_allclose(p2.carry[: j + 1], panel.carry[: j + 1])
    np.testing.assert_allclose(p2.Sig[: j + 1], panel.Sig[: j + 1], rtol=1e-10)
    for kind in fc.ALL_KINDS.values():
        np.testing.assert_allclose(fc.raw_weights(kind, p2.carry, p2.mom, p2.live)[: j + 1], fc.raw_weights(kind, panel.carry, panel.mom, panel.live)[: j + 1])
    assert not np.allclose(p2.carry[j + 1:], panel.carry[j + 1:])       # the scramble does reach the later decisions (the test can fail)


def test_rate_lag_positive_control(world, rates, panel):
    j = int(np.where(panel.dec == np.datetime64("2012-06-29"))[0][0])
    top = rates.loc["2012-05-01"].drop("USD").sort_values().index[-1]
    low = rates.loc["2012-05-01"].drop("USD").sort_values().index[0]
    # month m = June 2012 (the decision month): no weight at or before the June decision may change
    r_m = rates.copy()
    r_m.loc["2012-06-01", low] = 99.0
    p_m = fc.build_panel(world, r_m)
    w0 = fc.raw_weights("carry_rank", panel.carry, panel.mom, panel.live)
    w1 = fc.raw_weights("carry_rank", p_m.carry, p_m.mom, p_m.live)
    np.testing.assert_allclose(w1[: j + 1], w0[: j + 1])
    assert not np.allclose(w1[j + 1], w0[j + 1]) or not np.allclose(p_m.carry[j + 1], panel.carry[j + 1])
    # month m-1 = May 2012: the June decision must change (positive control: the signal really reads the lagged month)
    r_l = rates.copy()
    r_l.loc["2012-05-01", low] = 99.0
    p_l = fc.build_panel(world, r_l)
    w2 = fc.raw_weights("carry_rank", p_l.carry, p_l.mom, p_l.live)
    assert w2[j, IDX[low]] > 0 and w0[j, IDX[low]] <= 0
    np.testing.assert_allclose(w2[:j], w0[:j])
    assert top in fc.FOREIGN


def test_rate_gap_uses_last_known_value_only(world, rates):
    r = rates.copy()
    r.loc["2012-04-01", "AUD"] = np.nan
    p = fc.build_panel(world, r)
    j = int(np.where(p.dec == np.datetime64("2012-05-31"))[0][0])          # lag 1 reads April: carried forward from March
    assert p.carry[j, IDX["AUD"]] == pytest.approx(rates.loc["2012-03-01", "AUD"])


# ── nulls, Holm, gates ───────────────────────────────────────────────────────────────────

def _w10s(panel):
    ev = {v: fc.evaluate(panel, k) for v, k in fc.ALL_KINDS.items()}
    return ev, {v: ev[v]["w10"] for v in ev}


def test_permutation_null_is_centred_and_blocked(rates):
    pairs = fc.synth_world(np.random.default_rng(21), rates, 1.0)          # UIP: no carry premium
    pn = fc.build_panel(pairs, rates)
    ev, w10s = _w10s(pn)
    nd = fc.null_distribution(pn, w10s, 400, 3, "perm")
    for v in w10s:
        assert abs(nd["sr"][v].mean()) < 0.06
        assert 0.15 < nd["sr"][v].std() < 0.40
    again = fc.null_distribution(pn, w10s, 400, 3, "perm")
    np.testing.assert_array_equal(nd["sr"]["V1"], again["sr"]["V1"])        # deterministic given the seed
    # batching does not change the draws' distribution of lengths
    assert len(fc.null_distribution(pn, w10s, 37, 3, "perm", batch=10)["sr"]["V1"]) == 37


def test_permutation_keeps_gross_weights_inside_blocks(panel):
    w10 = fc.raw_weights("carry_rank", panel.carry, panel.mom, panel.live)[: panel.Kp]
    rng = np.random.default_rng(0)
    nb = int(np.ceil(panel.Kp / fc.BLOCK))
    blk = np.arange(panel.Kp) // fc.BLOCK
    perms = np.argsort(rng.random((1, nb, 10)), axis=-1)[:, blk, :]
    w = np.take_along_axis(np.broadcast_to(w10, (1, panel.Kp, 10)), perms, axis=2)[0]
    np.testing.assert_allclose(np.abs(w).sum(1), np.abs(w10).sum(1))
    np.testing.assert_allclose(w.sum(1), 0, atol=1e-12)
    # inside one block the same relabelling is applied every month
    b0 = w[: fc.BLOCK]
    assert (np.sign(b0)[:, :] == np.sign(w10[: fc.BLOCK][:, perms[0, 0]])).all()


def test_permutation_null_detects_planted_carry_and_not_uip(rates):
    strong = fc.synth_world(np.random.default_rng(31), rates, -1.0)
    pn = fc.build_panel(strong, rates)
    ev, w10s = _w10s(pn)
    nd = fc.null_distribution(pn, w10s, 499, 5, "perm")
    assert fc.sharpe(ev["V1"]["S0"]) > 0.7
    assert fc.p_one_sided(fc.sharpe(ev["V1"]["S0"]), nd["sr"]["V1"]) <= 0.02
    fp = 0
    for s in range(8):
        w = fc.synth_world(np.random.default_rng(100 + s), rates, 1.0)
        pw = fc.build_panel(w, rates)
        e, ws = _w10s(pw)
        n = fc.null_distribution(pw, ws, 199, s, "perm")
        fp += fc.p_one_sided(fc.sharpe(e["V1"]["S0"]), n["sr"]["V1"]) <= 0.05
    assert fp <= 2                                                              # about 5 % expected: allow noise in 8 panels


def test_sign_flip_null_symmetric_and_preserves_exposure(panel):
    ev, w10s = _w10s(panel)
    nf = fc.null_distribution(panel, w10s, 400, 8, "flip")
    for v in w10s:
        assert abs(nf["sr"][v].mean()) < 0.08
    # turnover under the flip null is at least the real turnover (sign flips at block edges add trades): costs are not understated
    assert nf["turnover"]["V1"].mean() >= ev["V1"]["parts"]["turnover"].mean() - 1e-12


def test_holm_known_values():
    rej, adj = fc.holm(np.array([0.01, 0.04, 0.03]))
    assert adj.tolist() == pytest.approx([0.03, 0.06, 0.06])
    assert rej.tolist() == [True, False, False]
    rej2, adj2 = fc.holm(np.array([0.001, 0.2, 0.9]))
    assert rej2.tolist() == [True, False, False] and adj2[0] == pytest.approx(0.003)
    assert fc.p_one_sided(2.0, np.arange(100) / 100.0) == pytest.approx(1 / 101)


def _fake_ev(panel, n1, loco=None, exotic=None):
    return {"S1": n1, "loco_S1": loco or {c: n1 for c in fc.FOREIGN}, "exotic_S1": n1 if exotic is None else exotic}


def test_gate_logic_each_gate_can_fail(panel):
    yrs = panel.year
    rng = np.random.default_rng(0)
    base = 0.004 + rng.normal(0, 0.01, panel.Kp)
    g = fc.gates(panel, _fake_ev(panel, base), True, 0.01)
    assert g["G2"] and g["G5"] and g["G3b"] and g["G3c"] and g["G3d"] and g["G1a"] and g["G1b"] and g["G4a"] and g["G4b"]
    assert g["G3a"] and g["eligible"]
    # all-negative: nothing passes
    neg = fc.gates(panel, _fake_ev(panel, -np.abs(base)), True, 0.01)
    assert not (neg["G2"] or neg["G3b"] or neg["G5"] or neg["G3c"])
    # years positive threshold: exactly 12 positive years of 18 fails, 13 passes
    for npos, want in ((12, False), (13, True)):
        x = np.where(np.isin(yrs, list(range(2003, 2003 + npos))), 0.01, -0.01) / 12.0
        gg = fc.gates(panel, _fake_ev(panel, x), True, 0.01)
        assert gg["G3b"] is want
    # best-three-years removal: profit concentrated in three years
    x = np.full(panel.Kp, -0.001)
    x[np.isin(yrs, [2005, 2006, 2007])] = 0.02
    gg = fc.gates(panel, _fake_ev(panel, x), True, 0.01)
    assert gg["G2"] and not gg["G3a"] and not gg["eligible"] and set(gg["detail"]["best3_years"]) == {2005, 2006, 2007}
    # leave-one-currency-out and exotics
    loco = {c: base for c in fc.FOREIGN}
    loco["JPY"] = -np.abs(base)
    assert not fc.gates(panel, _fake_ev(panel, base, loco), True, 0.01)["G4a"]
    assert not fc.gates(panel, _fake_ev(panel, base, None, -np.abs(base)), True, 0.01)["G4b"]
    # G1 components
    assert not fc.gates(panel, _fake_ev(panel, base), False, 0.01)["G1a"]
    assert not fc.gates(panel, _fake_ev(panel, base), True, 0.2)["G1b"]
    # without 2008-09
    x = np.full(panel.Kp, 0.002)
    x[np.isin(yrs, [2008, 2009])] = -0.05
    assert fc.gates(panel, _fake_ev(panel, x), True, 0.01)["G3d"] is True
    y = np.full(panel.Kp, -0.002)
    y[np.isin(yrs, [2008, 2009])] = 0.05
    assert fc.gates(panel, _fake_ev(panel, y), True, 0.01)["G3d"] is False


def test_materiality_gate_threshold(panel):
    rng = np.random.default_rng(1)
    z = rng.normal(0, 1, panel.Kp)
    z = (z - z.mean()) / z.std(ddof=1)
    lo = z * 0.02 + 0.02 * 0.20 / np.sqrt(12)             # monthly Sharpe 0.20/sqrt(12) -> annual 0.20
    hi = z * 0.02 + 0.02 * 0.40 / np.sqrt(12)
    assert fc.sharpe(lo) == pytest.approx(0.20, abs=1e-9) and fc.sharpe(hi) == pytest.approx(0.40, abs=1e-9)
    assert not fc.gates(panel, _fake_ev(panel, lo), True, 0.01)["G5"]
    assert fc.gates(panel, _fake_ev(panel, hi), True, 0.01)["G5"]


def test_decision_table_and_selection():
    ok = {"eligible": True, "G1b": True, "detail": {}}
    no = {"eligible": False, "G1b": True, "detail": {}}
    nob = {"eligible": False, "G1b": False, "detail": {}}
    assert fc.outcome({"V1": 0.6, "V2": 0.5, "V3": 0.4}, {"V1": ok, "V2": no, "V3": no}, {}, {"V1": True, "V2": False, "V3": False}) == "E"
    assert fc.outcome({"V1": 0.6, "V2": 0.5, "V3": 0.4}, {"V1": no, "V2": no, "V3": no}, {}, {"V1": True, "V2": False, "V3": False}) == "P"
    assert fc.outcome({"V1": 0.3, "V2": 0.2, "V3": 0.1}, {"V1": no, "V2": no, "V3": no}, {}, {"V1": False, "V2": False, "V3": False}) == "U"
    assert fc.outcome({"V1": 0.6, "V2": 0.5, "V3": 0.4}, {"V1": nob, "V2": nob, "V3": nob}, {}, {"V1": True, "V2": True, "V3": True}) == "U"
    assert fc.outcome({"V1": -0.1, "V2": -0.2, "V3": 0.0}, {"V1": no, "V2": no, "V3": no}, {}, {"V1": False, "V2": False, "V3": False}) == "N"
    g = {"V1": {"eligible": True}, "V2": {"eligible": True}, "V3": {"eligible": False}}
    assert fc.select_candidate(g, {"V1": 0.4, "V2": 0.5, "V3": 0.9}) == "V2"
    assert fc.select_candidate({"V1": {"eligible": False}}, {"V1": 1.0}) is None


def test_control_is_not_in_the_family():
    assert "M" not in fc.VARIANTS and "M" in fc.CONTROL and len(fc.VARIANTS) == 3


# ── bootstrap, power ─────────────────────────────────────────────────────────────────────

def test_bootstrap_interval_brackets_point_and_shares_months():
    rng = np.random.default_rng(0)
    x = rng.normal(0.004, 0.02, 220)
    bs = fc.boot_sharpes({"a": x, "b": 2 * x}, B=800, seed=1)
    assert bs["a"]["lo"] < bs["a"]["sr"] < bs["a"]["hi"]
    assert bs["a"]["sr"] == pytest.approx(bs["b"]["sr"])
    assert bs["a"]["lo"] == pytest.approx(bs["b"]["lo"])                        # same resampled months for every series
    idx = fc.boot_index(50, 12, 5, np.random.default_rng(0))
    assert idx.shape == (5, 50) and idx.min() >= 0 and idx.max() < 50


def test_power_table_values():
    t = fc.power_table()["rows"]
    single, holm1, final = t
    assert single["power_SR0.5"][0] == pytest.approx(0.69, abs=0.02)             # annualised-Sharpe variance (1 + SR^2/24), not (1 + SR^2/2)
    assert single["power_SR0.5"][1] < single["power_SR0.5"][0]
    assert holm1["power_SR0.5"][0] < single["power_SR0.5"][0]
    assert 0.52 < single["SR_80pct_power"][0] < 0.66 and 0.64 < holm1["SR_80pct_power"][0] < 0.78 and holm1["SR_80pct_power"][0] > single["SR_80pct_power"][0]
    assert final["power_SR1.0"][0] == pytest.approx(0.49, abs=0.02) and 1.3 < final["SR_80pct_power"][0] < 1.7      # FINAL needs a true S0 Sharpe of about 1.5


# ── freeze and FINAL guards ──────────────────────────────────────────────────────────────

def test_freeze_guard(tmp_path):
    pr, fz = tmp_path / "prereg.md", tmp_path / "prereg.sha256"
    with pytest.raises(PermissionError):
        fc.check_prereg_frozen(pr, fz, tmp_path, [])
    pr.write_text("v1")
    with pytest.raises(PermissionError):
        fc.check_prereg_frozen(pr, fz, tmp_path, [])
    h = fc.freeze(pr, fz, tmp_path, [])
    assert fc.check_prereg_frozen(pr, fz, tmp_path, []) == h
    pr.write_text("v2")
    with pytest.raises(PermissionError):
        fc.check_prereg_frozen(pr, fz, tmp_path, [])


def test_freeze_covers_code_and_data_files(tmp_path):
    """The freeze manifest hashes the prereg AND the code AND the data; changing any of them, deleting a manifest line or adding a file is refused."""
    (tmp_path / "data").mkdir()
    pr, fz = tmp_path / "prereg.md", tmp_path / "prereg.sha256"
    pr.write_text("prereg")
    (tmp_path / "code.py").write_text("x = 1\n")
    (tmp_path / "data" / "a.parquet").write_bytes(b"AAAA")
    (tmp_path / "data" / "b.csv").write_text("1,2\n")
    names = ["code.py", "data/a.parquet", "data/b.csv"]
    h = fc.freeze(pr, fz, tmp_path, names)
    lines = fz.read_text().splitlines()
    assert len(lines) == 4 and lines[0].endswith("PREREG") and lines[0].startswith(h)
    assert fc.check_prereg_frozen(pr, fz, tmp_path, names) == h
    (tmp_path / "code.py").write_text("x = 2\n")                                  # code edited after the freeze
    with pytest.raises(PermissionError, match="code.py"):
        fc.check_prereg_frozen(pr, fz, tmp_path, names)
    (tmp_path / "code.py").write_text("x = 1\n")
    assert fc.check_prereg_frozen(pr, fz, tmp_path, names) == h
    (tmp_path / "data" / "a.parquet").write_bytes(b"AAAB")                         # data file replaced
    with pytest.raises(PermissionError, match="a.parquet"):
        fc.check_prereg_frozen(pr, fz, tmp_path, names)
    (tmp_path / "data" / "a.parquet").write_bytes(b"AAAA")
    (tmp_path / "data" / "b.csv").unlink()                                          # data file deleted
    with pytest.raises(PermissionError):
        fc.check_prereg_frozen(pr, fz, tmp_path, names)
    (tmp_path / "data" / "b.csv").write_text("1,2\n")
    fz.write_text("\n".join(lines[:-1]) + "\n")                                    # a manifest line removed: cannot silently drop a file from the freeze
    with pytest.raises(PermissionError, match="wrong files"):
        fc.check_prereg_frozen(pr, fz, tmp_path, names)


def test_default_manifest_names_the_study_files():
    names = fc.frozen_names()
    assert len(names) == 2 + 9 + 10 and len(set(names)) == len(names)
    assert "fx_carry.py" in names and "carry_screens.py" in names
    for c in fc.FOREIGN:
        assert f"data_multi/{fc.PAIR_OF[c][0]}_D1.parquet" in names
    for c in fc.ALL10:
        assert f"data_multi/rates/{fc.COUNTRY[c]}.csv" in names
    assert all((fc.ROOT / n).exists() for n in names)                              # every frozen file exists in the repository


def test_dev_run_is_write_once(tmp_path, world, rates):
    pr = tmp_path / "p.md"
    pr.write_text("prereg stand-in")
    out = tmp_path / "out"
    fc.run_dev(out, 49, 50, world=(world, rates), require_frozen=False, quiet=True, prereg=pr)
    first = (out / "summary.json").read_bytes()
    with pytest.raises(PermissionError, match="write-once"):
        fc.run_dev(out, 49, 50, world=(world, rates), require_frozen=False, quiet=True, prereg=pr)
    assert (out / "summary.json").read_bytes() == first                              # untouched by the refused run
    with pytest.raises(PermissionError, match="reason"):
        fc.run_dev(out, 49, 50, world=(world, rates), require_frozen=False, quiet=True, prereg=pr, force_rerun_logged=True, rerun_reason="  ")
    assert not (out / "rerun_log.txt").exists()
    fc.run_dev(out, 49, 50, world=(world, rates), require_frozen=False, quiet=True, prereg=pr, force_rerun_logged=True, rerun_reason="bug fix X, test added")
    log = (out / "rerun_log.txt").read_text()
    assert "bug fix X, test added" in log and log.count("\n") == 1
    kept = list(out.glob("summary_before_rerun_*.json"))
    assert len(kept) == 1 and kept[0].read_bytes() == first                          # the first result is kept on record
    assert fc.sha256_file(kept[0]) in log


def test_run_dev_refuses_unfrozen(tmp_path, world, rates):
    pr = tmp_path / "p.md"
    pr.write_text("x")
    with pytest.raises(PermissionError):
        fc.run_dev(tmp_path / "o", 19, 50, world=(world, rates), prereg=pr, frozen=tmp_path / "none.sha")
    with pytest.raises(ValueError):
        fc.run_dev(tmp_path / "o", 19, 50, world=(world, rates), end="2021-07-01", require_frozen=False)
    with pytest.raises(ValueError):
        fc.run_dev(tmp_path / "o", 19, 50, world=(world, rates), start="2021-06-01", end="2021-06-01", require_frozen=False)


def test_final_go_guard(tmp_path):
    cand, go = tmp_path / "cand.json", tmp_path / "FINAL_GO"
    with pytest.raises(PermissionError):
        fc.require_final_go(go, cand)
    go.write_text("candidate_sha256=abc\n")
    with pytest.raises(PermissionError):
        fc.require_final_go(go, cand)
    cand.write_text('{"variant": "V1"}')
    with pytest.raises(PermissionError):
        fc.require_final_go(go, cand)
    go.write_text(f"candidate_sha256={fc.sha256_file(cand)}\n")
    assert fc.require_final_go(go, cand) == fc.sha256_file(cand)
    assert not fc.FINAL_GO.exists()                                              # no FINAL_GO exists in the repository


# ── end-to-end and calibration on synthetic panels ───────────────────────────────────────

def test_run_dev_end_to_end_synthetic(tmp_path, world, rates):
    pr = tmp_path / "p.md"
    pr.write_text("prereg stand-in")
    res = fc.run_dev(tmp_path / "out", 99, 100, world=(world, rates), require_frozen=False, quiet=True, prereg=pr)
    out = tmp_path / "out"
    for f in ("summary.json", "variants.csv", "monthly_pnl.csv", "per_year.csv", "per_currency_loco.csv", "null_distribution_perm.csv",
              "null_distribution_flip.csv", "sensitivity.csv", "prereg_sha256.txt"):
        assert (out / f).exists(), f
    s = json.loads((out / "summary.json").read_text())
    assert s["months"] == 221 and s["outcome"] in "EPUN" and s["prereg_sha256"] == fc.sha256_file(pr)
    assert (out / "prereg_sha256.txt").read_text().strip() == fc.sha256_file(pr)
    tab = res["table"]
    assert set(tab["variant"]) == {"V1", "V2", "V3", "M"} and not tab.loc[tab["variant"] == "M", "in_family"].item()
    sens = res["sens"]
    assert {"primary", "rate_lag_2_months", "top_bottom_2", "top_bottom_4", "exUSD_ranking", "without_2008_09", "spread_cost_x4", "markup_S2", "markup_snapshot_per_pair", "from_2007-02_all_ten_live"} <= set(sens["case"])


def test_calibration_smoke(tmp_path, rates):
    res = fc.calibrate(tmp_path, n_panels=2, n_perm=49, lams=(1.0, -1.0), seed=3, rates=rates)
    assert len(res["rows"]) == 2 + len(fc.CAL_WORLDS) and (tmp_path / "calibration.json").exists()
    assert [r["world"] for r in res["rows"][2:]] == [w for w, _ in fc.CAL_WORLDS]
    for r in res["rows"]:
        for k in ("V1_p_block12_below_005", "V1_p_full_below_005", "V1_p_G1_below_005", "V1_p_flip_below_005"):
            assert 0.0 <= r[k] <= 1.0
        assert r["V1_p_G1_below_005"] <= min(r["V1_p_block12_below_005"], r["V1_p_full_below_005"]) + 1e-12      # max of two p-values rejects less often than either
    r0, r1 = res["rows"][:2]
    assert r1["SR0_V1"] > r0["SR0_V1"]                                           # planted carry raises the realised Sharpe
    for k in ("any_eligible", "outcome_E", "outcome_P", "outcome_U", "outcome_N", "M_p_perm_below_005"):
        assert 0.0 <= r0[k] <= 1.0
    assert r0["outcome_E"] + r0["outcome_P"] + r0["outcome_U"] + r0["outcome_N"] == pytest.approx(1.0)


def test_planted_carry_is_in_the_accrual_not_the_noise(rates):
    """With lam = 0 (random-walk spot) the carry portfolio's mean excess return is its mean accrual; with lam = 1 (UIP) spot offsets it."""
    a = fc.build_panel(fc.synth_world(np.random.default_rng(7), rates, 0.0), rates)
    b = fc.build_panel(fc.synth_world(np.random.default_rng(7), rates, 1.0), rates)
    ea, eb = fc.evaluate(a, "carry_rank"), fc.evaluate(b, "carry_rank")
    assert ea["parts"]["acc"].mean() > 0 and ea["parts"]["acc"].mean() == pytest.approx(eb["parts"]["acc"].mean(), rel=0.3)
    # same noise draw in both worlds: the whole difference in mean excess return is the spot drift that offsets the accrual
    assert ea["S0"].mean() - eb["S0"].mean() == pytest.approx(ea["parts"]["acc"].mean(), rel=0.25)


# ── late-starting currency (USDNOK starts 2006-02) and per-pair mark-up ──────────────────────

@pytest.fixture(scope="module")
def late_nok_panel(world, rates):
    w = dict(world)
    p = world["NOK"]
    m = p.date >= np.datetime64("2006-02-06")
    w["NOK"] = fc.Pair(p.ccy, p.sym, p.usd_base, p.date[m], p.openX[m], p.closeX[m], p.hs[m], {})
    return fc.build_panel(w, rates)


def test_late_nok_is_not_live_until_it_has_history(late_nok_panel):
    pn = late_nok_panel
    j_nok = IDX["NOK"]
    first_live = int(np.argmax(pn.live[:, j_nok]))
    assert pn.dec[first_live] >= np.datetime64("2007-01-01") and pn.dec[first_live] <= np.datetime64("2007-04-30")   # needs 252 bars of momentum
    assert not pn.live[:first_live, j_nok].any() and pn.live[first_live:, j_nok].all()
    assert pn.live[:, [i for i in range(10) if i != j_nok]].all()
    for kind in fc.ALL_KINDS.values():
        w = fc.raw_weights(kind, pn.carry, pn.mom, pn.live)
        assert (w[:first_live, j_nok] == 0).all()
        assert np.allclose(w.sum(1), 0, atol=1e-12) and np.allclose(np.abs(w).sum(1), 2)             # still dollar-neutral, gross 2 on nine currencies
        if kind.endswith("rank"):
            assert (np.count_nonzero(w[:first_live], axis=1) == 6).all()                                # k = 3 of n = 9


def test_permutation_moves_weights_only_between_live_slots(late_nok_panel):
    pn = late_nok_panel
    w10 = fc.raw_weights("carry_rank", pn.carry, pn.mom, pn.live)[: pn.Kp]
    rng = np.random.default_rng(0)
    nb = int(np.ceil(pn.Kp / fc.BLOCK))
    blk = np.arange(pn.Kp) // fc.BLOCK
    perms = np.argsort(rng.random((200, nb, 10)), axis=-1)
    w = fc.permute_weights(w10, pn.live[: pn.Kp], perms, blk)
    dead = ~pn.live[: pn.Kp]
    assert (w[:, dead] == 0).all()                                               # nothing lands on a currency that has no data
    np.testing.assert_allclose(np.abs(w).sum(-1), np.broadcast_to(np.abs(w10).sum(1), (200, pn.Kp)))
    np.testing.assert_allclose(np.sort(w, axis=-1), np.broadcast_to(np.sort(w10, axis=-1), w.shape), atol=1e-12)   # same multiset of weights each month
    # uniform over the live currencies: the long-weight of month 0 lands on each of the nine live slots about equally often
    land = (w[:, 0, :] > 0).sum(0)
    assert land[IDX["NOK"]] == 0 and land[1:].min() >= 0 and np.count_nonzero(land) == 9
    assert land[[i for i in range(10) if i != IDX["NOK"]]].std() < 25
    # with all ten live the function is the plain gather
    live_all = np.ones((pn.Kp, 10), bool)
    g = fc.permute_weights(w10, live_all, perms, blk)
    np.testing.assert_array_equal(g, np.take_along_axis(np.broadcast_to(w10, g.shape), perms[:, blk, :], axis=2))


def test_null_runs_with_a_late_currency(late_nok_panel):
    pn = late_nok_panel
    w10s = {v: fc.raw_weights(k, pn.carry, pn.mom, pn.live) for v, k in fc.ALL_KINDS.items()}
    nd = fc.null_distribution(pn, w10s, 100, 4, "perm")
    assert np.isfinite(nd["sr"]["V1"]).all() and abs(nd["sr"]["V1"].mean()) < 0.12


def test_markup_vector_equals_scalar_when_uniform_and_weights_by_currency(panel):
    w10 = fc.raw_weights("carry_rank", panel.carry, panel.mom, panel.live)
    W = fc.scaled_weights(panel, w10)
    a = fc.net_from_W(panel, W, 0.02)
    b = fc.net_from_W(panel, W, np.full(9, 0.02))
    np.testing.assert_allclose(a, b)
    v = np.zeros(9)
    v[IDX["JPY"] - 1] = 0.05
    got = fc.net_from_W(panel, W, 0.0) - fc.net_from_W(panel, W, v)
    want = np.abs(W[: panel.Kp, IDX["JPY"] - 1]) * 0.05 * panel.days / 365.0
    np.testing.assert_allclose(got, want)
    assert set(fc.MARKUP_SNAP) == set(fc.FOREIGN)


# ── revision 2: FINAL taxonomy, tie-break, whole-sample null, new calibration worlds, lag-2 line, power formula ──

def test_final_outcome_is_exhaustive_and_exclusive():
    import itertools
    seen = {}
    for s1 in (-0.3, 0.0, 0.2):
        for combo in itertools.product([False, True], repeat=5):
            conds = dict(zip(fc.FINAL_CONDS, combo))
            out, failed = fc.final_outcome(s1, conds)
            assert out in ("CONFIRMED", "CONSISTENT BUT NOT CONFIRMED", "NOT REPLICATED")             # exactly one label, never undefined
            assert failed == [k for k, v in conds.items() if not v]                                  # the failed conditions are listed
            seen[(s1, combo)] = out
    assert len(seen) == 3 * 32
    assert sum(v == "CONFIRMED" for v in seen.values()) == 1                                          # only (S1 > 0 and all five)
    assert seen[(0.2, (True,) * 5)] == "CONFIRMED"
    assert all(v == "NOT REPLICATED" for (s1, _), v in seen.items() if s1 <= 0)
    assert all(v == "CONSISTENT BUT NOT CONFIRMED" for (s1, c), v in seen.items() if s1 > 0 and not all(c))
    # the hole the review found: lower bound above 0 but dropping a currency flips the sign
    assert fc.final_outcome(0.4, {**dict.fromkeys(fc.FINAL_CONDS, True), "iii_each_currency_dropped": False})[0] == "CONSISTENT BUT NOT CONFIRMED"
    assert len(fc.FINAL_CONDS) == 5


def test_tie_break_keeps_previous_positions_then_currency_order():
    live = np.ones(10, bool)
    flat = np.zeros(10)
    w0 = fc._rank_weights(flat, live)                                           # all tied, no history: fixed currency order
    assert (np.where(w0 > 0)[0] == [0, 1, 2]).all() and (np.where(w0 < 0)[0] == [7, 8, 9]).all()
    assert (fc._rank_weights(flat, live, None, w0) == w0).all()                # next month, still tied: positions kept
    # a boundary tie: currencies 2 and 3 tie for the third long slot (0 and 1 are clearly top)
    sc = np.array([9.0, 8.0, 5.0, 5.0, 1.0, 0.5, 0.4, 0.3, 0.2, 0.1])
    no_hist = fc._rank_weights(sc, live)
    kept3 = fc._rank_weights(sc, live, None, np.eye(10)[3])                      # currency 3 was long last month
    kept2 = fc._rank_weights(sc, live, None, np.eye(10)[2])
    assert (np.where(no_hist > 0)[0] == [0, 1, 2]).all()                         # no history: lower index wins
    assert (np.where(kept3 > 0)[0] == [0, 1, 3]).all()                           # the previous holder keeps the slot
    assert (np.where(kept2 > 0)[0] == [0, 1, 2]).all()
    # a short-side tie: currencies 6 and 7 tie for the third short slot (8 and 9 are clearly bottom)
    sc2 = np.array([9.0, 8.0, 7.0, 6.0, 5.0, 4.0, 1.0, 1.0, 0.5, 0.2])
    none = fc._rank_weights(sc2, live)
    s_prev6 = fc._rank_weights(sc2, live, None, -np.eye(10)[6])
    s_prev7 = fc._rank_weights(sc2, live, None, -np.eye(10)[7])
    assert (np.where(none < 0)[0] == [7, 8, 9]).all()                            # no history: the higher index is shorted
    assert (np.where(s_prev6 < 0)[0] == [6, 8, 9]).all() and (np.where(s_prev7 < 0)[0] == [7, 8, 9]).all()     # the previous short stays short
    assert np.count_nonzero(s_prev6 > 0) == 3
    # rows of raw_weights use last month's output: constant rates give constant positions month after month
    carry = np.zeros((6, 10))
    w = fc.raw_weights("carry_rank", carry, carry, np.ones((6, 10), bool))
    assert (w == w[0]).all()
    # the rule is deterministic
    assert (fc.raw_weights("carry_rank", carry, carry, np.ones((6, 10), bool)) == w).all()


def test_whole_sample_relabelling_is_one_permutation(panel):
    w10 = fc.raw_weights("carry_rank", panel.carry, panel.mom, panel.live)[: panel.Kp]
    rng = np.random.default_rng(1)
    perms = np.argsort(rng.random((50, 1, 10)), axis=-1)
    blk = np.zeros(panel.Kp, int)
    w = fc.permute_weights(w10, panel.live[: panel.Kp], perms, blk)
    for b in range(5):
        np.testing.assert_array_equal(w[b], w10[:, perms[b, 0]])                  # the same relabelling in every month of the sample
    nd = fc.null_distribution(panel, {"V1": fc.raw_weights("carry_rank", panel.carry, panel.mom, panel.live)}, 120, 5, "perm", block=panel.Kp)
    assert np.isfinite(nd["sr"]["V1"]).all() and abs(nd["sr"]["V1"].mean()) < 0.15


def test_g1_p_value_is_the_larger_of_the_two():
    null_a = np.linspace(-1, 1, 99)
    null_b = np.linspace(-0.2, 0.2, 99)                                            # narrower null: smaller p for a large observation
    a, b, g = fc.g1_pvalue(0.5, null_a, null_b)
    assert g == max(a, b) and b < a
    a2, b2, g2 = fc.g1_pvalue(0.1, null_b, null_a)                                  # roles swapped: now the second null is the wide one
    assert g2 == max(a2, b2) and a2 < b2


def test_new_calibration_worlds_have_the_intended_properties(rates):
    def vr(world, h=750):
        v = []
        for c in ("EUR", "AUD", "JPY", "NOK"):
            x = np.log(world[c].closeX)
            d = np.diff(x)
            v.append((np.var(x[h:] - x[:-h]) / h) / np.var(d))
        return np.mean(v)
    base = [vr(fc.synth_world(np.random.default_rng(s), rates, 1.0)) for s in range(6)]
    slow = [vr(fc.synth_world(np.random.default_rng(s), rates, 1.0, slow_sd=0.04)) for s in range(6)]
    assert np.mean(slow) > np.mean(base) + 0.4                                      # slow drift raises the long-horizon variance ratio
    crash = fc.synth_world(np.random.default_rng(3), rates, 1.0, crash=True)
    calm = fc.synth_world(np.random.default_rng(3), rates, 1.0)
    rc = np.diff(np.log(crash["AUD"].closeX))
    r0 = np.diff(np.log(calm["AUD"].closeX))
    assert rc.min() < r0.min() - 0.02 or rc.max() > r0.max() + 0.02                # visible jumps; AUD is a high-yield currency in the sample
    pn = fc.build_panel(crash, rates)                                               # the panel machinery runs on it
    assert pn.live.all()


def test_crash_world_is_compensated_so_the_expected_drift_is_unchanged(rates, monkeypatch):
    monkeypatch.setattr(fc, "SYNTH_VOL", {c: 1e-9 for c in fc.FOREIGN})            # remove the noise: only the planted drift and the jumps remain
    calm = fc.synth_world(np.random.default_rng(0), rates, 1.0, start="2008-01-01")
    mean0 = np.diff(np.log(calm["AUD"].closeX)).mean()
    diffs = []
    for s in range(60):
        w = fc.synth_world(np.random.default_rng(s), rates, 1.0, start="2008-01-01", crash=True)
        diffs.append(np.diff(np.log(w["AUD"].closeX)).mean() - mean0)
    jump_mean = fc.CRASH_RATE * fc.CRASH_MEAN / fc.ANN_DAYS                          # per day, times the loading (about 1 for AUD)
    assert abs(np.mean(diffs)) < 0.4 * jump_mean                                     # an uncompensated world would sit near -1 x jump_mean x loading


def test_run_dev_reports_lag2_usd_exposure_and_both_nulls(tmp_path, world, rates):
    pr = tmp_path / "p.md"
    pr.write_text("x")
    res = fc.run_dev(tmp_path / "o", 49, 50, world=(world, rates), require_frozen=False, quiet=True, prereg=pr)
    tab = res["table"]
    for c in ("SR_S0_lag2", "SR_S1_lag2", "mean_net_USD_weight", "share_months_net_long_USD", "p_perm12", "p_permfull", "p_perm"):
        assert c in tab.columns
    np.testing.assert_allclose(tab["p_perm"], np.maximum(tab["p_perm12"], tab["p_permfull"]))        # G1a uses the larger p
    s = json.loads((tmp_path / "o" / "summary.json").read_text())
    assert "lag2_SR_S1" in s and "lag2_SR_S1_positive_for_all_family" in s and set(s["lag2_SR_S1"]) == {"V1", "V2", "V3"}
    assert (tmp_path / "o" / "null_distribution_perm_full.csv").exists()
    row = res["sens"][res["sens"]["case"] == "rate_lag_2_months"].set_index("variant")
    assert tab.set_index("variant").loc["V1", "SR_S1_lag2"] == pytest.approx(row.loc["V1", "SR_S1"])


def test_analytic_power_se_matches_simulation():
    rng = np.random.default_rng(0)
    n = 221
    for sr in (0.5, 1.0):
        m = sr / np.sqrt(12)
        x = rng.normal(m * 0.04, 0.04, size=(4000, n))
        est = x.mean(1) / x.std(1, ddof=1) * np.sqrt(12)
        se_formula = np.sqrt((1 + est.mean() ** 2 / 24) / (n / 12))
        assert est.std() == pytest.approx(se_formula, rel=0.06)
