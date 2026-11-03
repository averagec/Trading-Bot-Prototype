"""Tests for research/multi_trend.py and research/multi_trend_data.py (synthetic data only: no project returns, no MT5, no FINAL data)."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from research import multi_trend as mt
from research import multi_trend_data as mtd

START, END = "2018-06-01", "2021-06-01"


# ── helpers ───────────────────────────────────────────────────────────────────────────────────

def bdays(start="2016-01-01", end="2021-06-01"):
    return pd.bdate_range(start, pd.Timestamp(end) - pd.Timedelta(days=1)).to_numpy("datetime64[D]")


def make_inst(sym, cls, close, D, entry="2016-06-01", quote="USD", masked=(), hs=1e-4, opn=None):
    close = np.asarray(close, float)
    o = np.concatenate([[close[0]], close[:-1]]) if opn is None else np.asarray(opn, float)
    return mt.Inst(sym, cls, quote, D.copy(), o, close, np.full(len(D), hs), np.datetime64(entry, "D"), tuple(masked))


def rw(n, seed, vol=0.10, drift=0.0, start=100.0):
    rng = np.random.default_rng(seed)
    return start * np.exp(np.cumsum(drift + vol / np.sqrt(260) * rng.standard_normal(n)))


def small_universe(D, seed=1, classes=(("FX", 3), ("EQ", 2), ("MT", 2)), vol=None):
    out = {}
    k = 0
    for cls, n in classes:
        for i in range(n):
            k += 1
            out[f"{cls}{i}"] = make_inst(f"{cls}{i}", cls, rw(len(D), seed * 100 + k, vol=(vol or 0.08 + 0.04 * (k % 3))), D)
    return out


def synth_uni(spec=(("FX", 6, "2003-01-01"), ("EQ", 4, "2003-01-01"), ("MT", 2, "2003-01-01"), ("EN", 2, "2006-01-04"), ("AG", 2, "2009-01-07"))):
    rows = []
    for cls, n, ent in spec:
        for i in range(n):
            rows.append({"symbol": f"{cls}{i}", "class": cls, "entry": ent, "masked_years": [], "quote_ccy": "USD"})
    return pd.DataFrame(rows)


@pytest.fixture(scope="module")
def dev_insts():
    return mt.synth_insts(np.random.default_rng(3), synth_uni(), 0.03)


@pytest.fixture(scope="module")
def dev_panel(dev_insts):
    return mt.build_panel(dev_insts, mt.DEV_START, mt.DEV_END)


# ── DEV/FINAL guards and the file-level filter ─────────────────────────────────────────────────

def test_check_end_refuses_missing_and_post_final():
    with pytest.raises(ValueError):
        mtd.check_end(None)
    with pytest.raises(ValueError):
        mtd.check_end("2021-06-02")
    with pytest.raises(ValueError):
        mtd.check_end("2025-04-30")
    assert mtd.check_end("2021-06-01") == pd.Timestamp("2021-06-01", tz="UTC")


def _write_d1(tmp_path, sym="EURUSD", start="2019-01-01", end="2022-12-30"):
    days = pd.bdate_range(start, end)
    n = len(days)
    c = rw(n, 5, 0.08, start=1.1)
    df = pd.DataFrame({"time": pd.DatetimeIndex(days).tz_localize("UTC"), "open": c, "high": c * 1.001, "low": c * 0.999, "close": c,
                       "tick_volume": np.full(n, 100, dtype="uint64"), "spread": np.full(n, 5, dtype="int32"), "real_volume": np.zeros(n, dtype="uint64")})
    df.to_parquet(tmp_path / f"{sym}_D1.parquet", index=False)
    (tmp_path / f"{sym}_spec.json").write_text(json.dumps({"path": "Forex\\" + sym, "point": 1e-5, "digits": 5, "contract_size": 100000.0,
                                                           "currency_profit": "USD"}))
    return df


def test_loader_never_returns_bars_at_or_after_dev_end_and_filters_at_file_level(tmp_path, monkeypatch):
    _write_d1(tmp_path)
    got = mtd.load_d1_raw("EURUSD", "2021-06-01", tmp_path)
    assert got["time"].max() < pd.Timestamp("2021-06-01", tz="UTC") and len(got) > 500
    with pytest.raises(ValueError):
        mtd.load_d1_raw("EURUSD", "2022-01-01", tmp_path)
    with pytest.raises(ValueError):
        mtd.load_d1_raw("EURUSD", None, tmp_path)
    seen = {}
    real = pd.read_parquet

    def spy(*a, **kw):
        seen["filters"] = kw.get("filters")
        return real(*a, **kw)
    monkeypatch.setattr(pd, "read_parquet", spy)
    mtd.load_d1_raw("EURUSD", "2021-06-01", tmp_path)
    assert seen["filters"] and seen["filters"][0][0] == "time" and seen["filters"][0][1] == "<"


def test_dev_window_refuses_final_and_later():
    mt.check_dev_window("2003-01-01", "2021-06-01")
    with pytest.raises(ValueError):
        mt.check_dev_window("2003-01-01", "2021-06-02")
    with pytest.raises(ValueError):
        mt.check_dev_window("2003-01-01", None)
    with pytest.raises(ValueError):
        mt.check_dev_window("2021-06-01", "2021-12-01")
    with pytest.raises(ValueError):
        mt.run_dev(Path("unused"), 2, 2, insts={}, start="2003-01-01", end="2022-01-01")


def test_final_mode_needs_go_file_with_candidate_hash(tmp_path):
    cand, go = tmp_path / "frozen_candidate.json", tmp_path / "FINAL_GO"
    with pytest.raises(PermissionError):
        mt.require_final_go(go, cand)
    go.write_text("candidate_sha256=abc\n")
    with pytest.raises(PermissionError):
        mt.require_final_go(go, cand)
    cand.write_text('{"variant": "B4"}')
    with pytest.raises(PermissionError):
        mt.require_final_go(go, cand)                       # wrong hash
    go.write_text(f"candidate_sha256={mt.sha256_file(cand)}\n")
    assert mt.require_final_go(go, cand) == mt.sha256_file(cand)


def test_final_loader_and_run_refused_without_go(tmp_path):
    _write_d1(tmp_path)
    uni = pd.DataFrame([{"symbol": "EURUSD", "class": "FX", "entry": "2019-06-01", "masked_years": [], "quote_ccy": "USD"}])
    with pytest.raises(PermissionError):
        mt.load_final_insts(go_path=tmp_path / "nogo", cand_path=tmp_path / "nocand", universe=uni, data_dir=tmp_path)
    with pytest.raises(PermissionError):
        mt.run_final(insts={}, go_path=tmp_path / "nogo", cand_path=tmp_path / "nocand")


def test_cli_refuses_final_window_and_final_mode_without_go(capsys):
    assert mt.main(["dev", "--end", "2022-01-01", "--out", str(Path("research/output/scratch/_unused_cli"))]) == 2
    assert "refused" in capsys.readouterr().err
    if not mt.FINAL_GO.exists():
        assert mt.main(["final"]) == 2
        assert "refused" in capsys.readouterr().err


# ── cleaning and class rule ─────────────────────────────────────────────────────────────────────

def raw_frame(dates, close, spread=5):
    n = len(dates)
    c = np.asarray(close, float)
    return pd.DataFrame({"time": pd.DatetimeIndex(dates).tz_localize("UTC"), "open": c, "high": c * 1.001, "low": c * 0.999, "close": c,
                         "tick_volume": np.full(n, 10, dtype="uint64"), "spread": np.full(n, spread, dtype="int32"), "real_volume": np.zeros(n, dtype="uint64")})


def test_clean_d1_merges_weekend_bars_drops_bad_rows_and_spikes():
    days = pd.bdate_range("2019-01-01", "2020-12-31")
    c = rw(len(days), 2, 0.1, start=100.0)
    df = raw_frame(days, c)
    # a Sunday bar before a Monday, a non-positive bar, and a reverting spike
    mon = df.index[df["time"].dt.dayofweek == 0][10]
    mon_ts = df.loc[mon, "time"].tz_localize(None).normalize()
    sun = df.loc[[mon]].copy()
    sun["time"] = sun["time"] - pd.Timedelta(days=1)
    sun["open"], sun["close"], sun["high"], sun["low"] = 99.0, 99.5, 99.9, 98.0
    df = pd.concat([df, sun]).sort_values("time").reset_index(drop=True)
    zero_i = df.index[300]
    df.loc[zero_i, ["open", "high", "low", "close"]] = 0.0
    sp_i = df.index[400]
    df.loc[sp_i, "close"] = df.loc[sp_i - 1, "close"] * 1.40
    df.loc[sp_i, "high"] = df.loc[sp_i, "close"] * 1.001
    out, cnt = mtd.clean_d1(df)
    assert cnt["weekend_merged"] == 1 and cnt["nonpositive_dropped"] == 1 and cnt["spike_dropped"] == 1
    assert (out["date"].dt.dayofweek < 5).all() and out["date"].is_monotonic_increasing and out["date"].is_unique
    m = out[out["date"] == mon_ts].iloc[0]
    assert m["open"] == 99.0 and m["low"] <= 98.0               # open of the Sunday bar, extremes over both


def test_classify_rule():
    assert mtd.classify("EURUSD", "Forex\\EURUSD")[0] == "FX"
    assert mtd.classify("USDTRY", "Forex\\USDTRY")[0] == "EX" and mtd.classify("USDMXN", "Exotics\\USDMXN")[0] == "EX"
    assert mtd.classify("DXY.cash", "Cash III CFD\\DXY.cash")[0] == "DXY"
    assert mtd.classify("US500.cash", "Cash CFD\\US500.cash")[0] == "EQ"
    assert mtd.classify("UKOIL.cash", "Cash CFD\\UKOIL.cash")[0] == "EN" and mtd.classify("USOIL.cash", "Cash CFD\\USOIL.cash")[0] == "EN"
    assert mtd.classify("XAUUSD", "Metals CFD\\XAUUSD")[0] == "MT"
    assert mtd.classify("COCOA.c", "Agriculture\\COCOA.c")[0] == "AG"
    assert mtd.classify("BTCUSD", "Crypto\\BTCUSD")[0] == "CRYPTO" and mtd.classify("AAPL", "Equities\\AAPL")[0] == "STOCK"


# ── screen ──────────────────────────────────────────────────────────────────────────────────────

def test_screen_masks_holes_and_stale_years_and_applies_exclusion_rules():
    days = pd.bdate_range("2002-01-01", "2021-05-31")
    c = rw(len(days), 4, 0.1)
    keep = np.ones(len(days), bool)
    hole = (days >= "2008-03-03") & (days <= "2008-04-30")
    keep &= ~hole                                             # a two-month hole in 2008
    c = c.copy()
    st = (days >= "2012-01-01") & (days <= "2012-12-31")
    c[st] = c[st][0]                                          # a stale year
    df = raw_frame(days[keep], c[keep])
    spec = {"path": "Forex\\EURGBP", "point": 1e-5, "digits": 5, "contract_size": 1e5, "currency_profit": "GBP"}
    r = mtd.screen_symbol("EURGBP", spec, df)
    assert 2008 in r["masked_years"] and 2012 in r["masked_years"] and 2015 not in r["masked_years"]
    assert not r["excluded"] and r["entry"].startswith("2003-01")
    # too short a live span
    short = raw_frame(days[days >= "2016-01-01"], c[days >= "2016-01-01"])
    assert any("live span" in x for x in mtd.screen_symbol("EURGBP", spec, short)["reasons"])
    # a series with many masked live years is excluded
    c2 = rw(len(days), 6, 0.1)
    for y in range(2004, 2012):
        m = (days >= f"{y}-01-01") & (days <= f"{y}-12-31")
        c2[m] = c2[m][0]
    r2 = mtd.screen_symbol("EURGBP", spec, raw_frame(days, c2))
    assert r2["excluded"] and any("masked" in x for x in r2["reasons"])


def test_roll_like_detector_flags_recurring_gaps_not_random_outliers():
    days = pd.bdate_range("2005-01-03", "2020-12-31")
    rng = np.random.default_rng(8)
    r = 0.008 * rng.standard_normal(len(days))
    rolled = r.copy()
    mid = (days.month % 3 == 0) & (days.day >= 14) & (days.day <= 16)
    first = pd.Series(mid).groupby([days.year, days.month]).cumsum().to_numpy() == 1
    rolled[mid & first] += 0.08                                # a 10-sigma gap every quarter around the 15th
    rand = r.copy()
    idx = rng.choice(len(days), 60, replace=False)
    rand[idx[:30]] += 0.08
    rand[idx[30:]] -= 0.08
    f_roll = pd.DataFrame({"date": days, "close": 100 * np.exp(np.cumsum(rolled))})
    f_rand = pd.DataFrame({"date": days, "close": 100 * np.exp(np.cumsum(rand))})
    assert mtd.roll_like(f_roll)["roll_like"] and not mtd.roll_like(f_rand)["roll_like"]


def test_duplicate_instruments_are_detected(tmp_path):
    days = pd.bdate_range("2002-01-01", "2021-05-28")
    c = rw(len(days), 12, 0.1)
    for s, cc in (("AAA", c), ("BBB", c * 1.0001), ("CCC", rw(len(days), 13, 0.1))):
        raw_frame(days, cc).to_parquet(tmp_path / f"{s}_D1.parquet", index=False)
        (tmp_path / f"{s}_spec.json").write_text(json.dumps({"path": "Cash CFD\\" + s, "point": 0.01, "digits": 2, "contract_size": 1.0, "currency_profit": "USD"}))
    tab = mtd.screen_all(tmp_path)
    t = tab.set_index("symbol")
    assert t.loc["BBB", "duplicate_of"] == "AAA" and t.loc["BBB", "excluded"]
    assert t.loc["CCC", "duplicate_of"] == "" and not t.loc["CCC", "excluded"] and not t.loc["AAA", "excluded"]


def test_spread_fraction_uses_floor_sentinel_and_causal_median():
    days = pd.bdate_range("2020-03-02", "2020-12-31")
    sp = np.full(len(days), 12)
    df = raw_frame(days, np.full(len(days), 1.0), 12)
    df["spread"] = sp.astype("int32")
    df["date"] = df["time"].dt.tz_localize(None)
    f = mtd.spread_fraction(df, "FX", 1e-5)
    assert f[0] == pytest.approx(mtd.SPREAD_FLOOR_BP["FX"] * 1e-4)             # no history yet: floor
    assert f[-1] == pytest.approx(max(12 * 1e-5, mtd.SPREAD_FLOOR_BP["FX"] * 1e-4))
    # the 50-point sentinel inside the FX placeholder window is ignored; outside it, it is a real quote
    d2r = pd.bdate_range("2020-06-01", "2020-12-31"); d2 = raw_frame(d2r, np.full(len(d2r), 1.0), 50)
    assert mtd.clean_spread_pts(d2.assign(date=d2["time"].dt.tz_localize(None)), "FX").isna().all()
    d3r = pd.bdate_range("2015-06-01", "2015-12-31"); d3 = raw_frame(d3r, np.full(len(d3r), 1.0), 50)
    assert mtd.clean_spread_pts(d3.assign(date=d3["time"].dt.tz_localize(None)), "FX").notna().all()
    # causality: changing a later spread cannot change an earlier value
    df2 = df.copy()
    df2.loc[df2.index[-1], "spread"] = 900
    assert np.array_equal(mtd.spread_fraction(df, "FX", 1e-5)[:-1], mtd.spread_fraction(df2, "FX", 1e-5)[:-1])


# ── no look-ahead ─────────────────────────────────────────────────────────────────────────────

def test_no_lookahead_signals_vol_scale_and_availability_by_truncation_and_scrambling():
    D = bdays("2016-01-01", "2021-06-01")
    n = len(D)
    full_i = small_universe(D, seed=1)
    full = mt.build_panel(full_i, START, END)
    cut_i = int(n * 0.8)
    cut = D[cut_i]

    def cutof(it, k):
        return mt.Inst(it.sym, it.cls, it.quote, it.date[:k], it.open[:k], it.close[:k], it.hs[:k], it.entry, it.masked)

    trunc = {s: cutof(it, cut_i + 1) for s, it in full_i.items()}
    tp = mt.build_panel(trunc, START, END)
    ks = np.where(full.R <= cut - np.timedelta64(4, "D"))[0]
    assert len(ks) > 80
    for v in mt.VNAMES:
        assert np.array_equal(full.S[v][ks], tp.S[v][ks]), v
    assert np.allclose(full.volw[ks], tp.volw[ks]) and np.array_equal(full.avail[ks], tp.avail[ks])
    assert np.allclose(full.gC[ks], tp.gC[ks], equal_nan=True) and np.allclose(full.hs[ks], tp.hs[ks])
    # scrambling: replace everything after the cut by unrelated prices; rows decided before the cut do not move
    rng = np.random.default_rng(99)
    scr = {}
    for s, it in full_i.items():
        c2 = np.concatenate([it.close[: cut_i + 1], it.close[cut_i] * np.exp(np.cumsum(0.03 * rng.standard_normal(n - cut_i - 1)))])
        scr[s] = make_inst(s, it.cls, c2, D)
    sp = mt.build_panel(scr, START, END)
    for v in mt.VNAMES:
        assert np.array_equal(full.S[v][ks], sp.S[v][ks]), v
    assert np.allclose(full.volw[ks], sp.volw[ks]) and np.allclose(full.gC[ks], sp.gC[ks], equal_nan=True)
    # ... and the scramble really moves later rows
    late = np.where(full.R > cut + np.timedelta64(60, "D"))[0]
    assert any(not np.array_equal(full.S[v][late], sp.S[v][late]) for v in mt.VNAMES)


def test_signal_uses_calendar_window_and_blend_is_mean_of_signs():
    D = bdays("2018-01-01", "2021-06-01")
    n = len(D)
    # anchors at the exact calendar look-backs from the decision date: short horizons up, long horizons down
    Rk = mt.decision_dates("2020-01-01", END)[-10]
    day = np.timedelta64(1, "D")
    ik = np.searchsorted(D, Rk, side="right") - 1
    i30, i91, i182, i365 = (np.searchsorted(D, Rk - L * day, side="right") - 1 for L in (30, 91, 182, 365))
    xs = [0, i365, i182, i91, i30, ik, n - 1]
    c = np.interp(np.arange(n), xs, [200, 200, 180, 120, 130, 140, 140])
    insts = {"FX0": make_inst("FX0", "FX", c, D, entry="2018-06-01")}
    p = mt.build_panel(insts, "2020-01-01", END)
    k = int(np.where(p.R == Rk)[0][0])
    assert p.S["M1"][k, 0] == 1 and p.S["M3"][k, 0] == 1 and p.S["M6"][k, 0] == -1 and p.S["M12"][k, 0] == -1
    assert p.S["B4"][k, 0] == 0.0
    # the look-back is in calendar days: change one price just outside the 30-day window and nothing moves
    i_out = np.searchsorted(D, Rk - np.timedelta64(40, "D"))
    c2 = c.copy()
    c2[i_out] *= 1.5
    p2 = mt.build_panel({"FX0": make_inst("FX0", "FX", c2, D, entry="2018-06-01")}, "2020-01-01", END)
    assert p2.S["M1"][k, 0] == p.S["M1"][k, 0]


def test_holiday_decision_day_falls_back_and_stale_signal_is_flat():
    D = bdays("2018-01-01", "2021-06-01")
    c = rw(len(D), 7, 0.1)
    wed = np.datetime64("2020-03-04")
    assert pd.Timestamp(wed).dayofweek == 2
    keep = D != wed
    p_hol = mt.build_panel({"FX0": make_inst("FX0", "FX", c[keep], D[keep], entry="2018-06-01")}, "2020-01-01", END)
    k = int(np.where(p_hol.R == wed)[0][0])
    assert p_hol.avail[k, 0]                                   # Tuesday's bar is used
    gap = (D < np.datetime64("2020-03-02")) | (D > np.datetime64("2020-03-10"))
    p_gap = mt.build_panel({"FX0": make_inst("FX0", "FX", c[gap], D[gap], entry="2018-06-01")}, "2020-01-01", END)
    assert not p_gap.avail[k, 0] and p_gap.counts["stale_signal"] >= 1


# ── execution, returns, conversion ───────────────────────────────────────────────────────────────

def test_execution_is_next_bar_open_and_return_is_open_to_open():
    D = bdays("2018-01-01", "2021-06-01")
    c = rw(len(D), 9, 0.1)
    o = c * 1.003
    it = make_inst("FX0", "FX", c, D, entry="2018-06-01", opn=o)
    p = mt.build_panel({"FX0": it}, "2020-01-01", END)
    k = 10
    thu = p.R[k] + np.timedelta64(1, "D")
    thu2 = p.R[k + 1] + np.timedelta64(1, "D")
    i0, i1 = np.searchsorted(D, thu), np.searchsorted(D, thu2)
    assert p.ret[k, 0] == pytest.approx(o[i1] / o[i0] - 1)
    # a missing exit bar is marked at the last close, never zeroed
    keep = ~np.isin(D, np.arange(thu2, thu2 + np.timedelta64(6, "D")))
    p2 = mt.build_panel({"FX0": make_inst("FX0", "FX", c[keep], D[keep], entry="2018-06-01", opn=o[keep])}, "2020-01-01", END)
    assert p2.ret[k, 0] != 0.0 and p2.counts["marked_at_last_close"] >= 1


def test_usd_conversion_identity_and_local_bound():
    D = bdays("2018-01-01", "2021-06-01")
    n = len(D)
    usdjpy = 110 * np.exp(np.cumsum(0.0045 * np.random.default_rng(1).standard_normal(n)))
    jp = make_inst("USDJPY", "FX", usdjpy, D, entry="2018-06-01", quote="JPY")
    conv = {"USDJPY": mt.Inst("USDJPY", "FX", "JPY", D, jp.open, jp.close, jp.hs, np.datetime64("1900-01-01"), ())}
    p = mt.build_panel({"USDJPY": jp}, "2020-01-01", END, conv_insts=conv)
    k = 20
    i0 = np.searchsorted(D, p.R[k] + np.timedelta64(1, "D"))
    i1 = np.searchsorted(D, p.R[k + 1] + np.timedelta64(1, "D"))
    P0, P1 = jp.open[i0], jp.open[i1]
    assert p.ret[k, 0] * p.conv[k, 0] == pytest.approx(1 - P0 / P1)           # long USD notional earns (P1-P0)/P1 (R3-1 convention)
    # EUR-quoted index: USD return = local return x Q1/Q0 with Q = EURUSD
    eurusd = 1.1 * np.exp(np.cumsum(0.004 * np.random.default_rng(2).standard_normal(n)))
    ger = make_inst("GER", "EQ", rw(n, 3, 0.2), D, entry="2018-06-01", quote="EUR")
    cv = {"EURUSD": mt.Inst("EURUSD", "FX", "USD", D, np.concatenate([[eurusd[0]], eurusd[:-1]]), eurusd, ger.hs, np.datetime64("1900-01-01"), ())}
    q = mt.build_panel({"GER": ger}, "2020-01-01", END, conv_insts=cv)
    q_loc = mt.build_panel({"GER": ger}, "2020-01-01", END)
    assert q_loc.counts["no_conversion_pair"] == 1 and q.counts["converted"] == 1
    j0 = np.searchsorted(D, q.R[k] + np.timedelta64(1, "D"))
    j1 = np.searchsorted(D, q.R[k + 1] + np.timedelta64(1, "D"))
    e0, e1 = cv["EURUSD"].open[j0], cv["EURUSD"].open[j1]
    assert q.conv[k, 0] == pytest.approx(e1 / e0)
    # the cross-term bound: |usd - local| = |r x (conv - 1)|
    diff = np.abs(q.ret[:, 0] * q.conv[:, 0] - q.ret[:, 0])
    assert np.allclose(diff, np.abs(q.ret[:, 0] * (q.conv[:, 0] - 1)))
    assert diff.max() < 0.01                                  # second order: weekly r ~ 3 %, weekly fx move ~ 1 %


# ── sizing, class balance, costs, financing ─────────────────────────────────────────────────────

def test_position_sizing_hits_the_vol_target_for_a_single_instrument():
    D = bdays("2016-01-01", "2021-06-01")
    c = rw(len(D), 11, 0.20)
    p = mt.build_panel({"EQ0": make_inst("EQ0", "EQ", c, D)}, START, END)
    k = np.where(p.avail[:, 0])[0][40]
    it_sd = np.std(np.diff(np.log(c))[: np.searchsorted(D, p.R[k], side="right") - 1][-60:], ddof=1) * np.sqrt(260)
    assert p.volw[k, 0] == pytest.approx(min(mt.CAP, 0.10 / it_sd))
    # one instrument in one class: class scale makes the always-long basket 10 % vol, so weight x vol is about 10 % / 10 % = 1
    assert 0.5 < p.gC[k, mt.CLASSES.index("EQ")] * p.volw[k, 0] * it_sd / 0.10 < 2.0


def test_class_scale_gives_equal_class_risk_and_class_balanced_weights():
    D = bdays("2016-01-01", "2021-06-01")
    n = len(D)
    rng = np.random.default_rng(5)
    common = 0.2 / np.sqrt(260) * rng.standard_normal(n)
    ins = {}
    for i in range(4):                                         # EQ: four perfectly correlated instruments
        ins[f"EQ{i}"] = make_inst(f"EQ{i}", "EQ", 100 * np.exp(np.cumsum(common)), D)
    for i in range(4):                                         # MT: four independent instruments of the same vol
        ins[f"MT{i}"] = make_inst(f"MT{i}", "MT", 100 * np.exp(np.cumsum(0.2 / np.sqrt(260) * rng.standard_normal(n))), D)
    p = mt.build_panel(ins, START, END)
    k = int(np.where(p.avail.all(1))[0][-5])
    gE, gM = p.gC[k, mt.CLASSES.index("EQ")], p.gC[k, mt.CLASSES.index("MT")]
    assert gE == pytest.approx(1.0, rel=0.25) and gM == pytest.approx(2.0, rel=0.35)      # sqrt(4) for independent names
    sc = mt.scale_matrix(p)
    eq_cols, mt_cols = p.cols_of(["EQ"]), p.cols_of(["MT"])
    # each class carries its scale / C of risk-weighted notional; within a class the weights are equal
    assert (sc[k, eq_cols] / p.volw[k, eq_cols]).sum() == pytest.approx(gE / 2)
    assert (sc[k, mt_cols] / p.volw[k, mt_cols]).sum() == pytest.approx(gM / 2)
    assert np.allclose(sc[k, eq_cols] / p.volw[k, eq_cols], sc[k, eq_cols][0] / p.volw[k, eq_cols][0])
    # removing a class re-balances the rest (C changes)
    sc_mt = mt.scale_matrix(p, mt_cols)
    assert (sc_mt[k] / p.volw[k, mt_cols]).sum() == pytest.approx(gM)


def test_costs_on_turnover_and_financing_on_gross_notional_by_class():
    D = bdays("2016-01-01", "2021-06-01")
    ins = {"FX0": make_inst("FX0", "FX", rw(len(D), 1, 0.08), D, hs=2e-4), "MT0": make_inst("MT0", "MT", rw(len(D), 2, 0.15), D, hs=6e-4)}
    p = mt.build_panel(ins, START, END)
    S = np.ones((p.K, 2))
    g, c, s, w, dw = mt.components(p, S, level="S1")
    k = 30
    assert dw[k, 0] == pytest.approx(abs(w[k, 0] - w[k - 1, 0]))
    assert c[k, 0] == pytest.approx(dw[k, 0] * (0.5 * p.hs[k, 0] + mt.COMM))
    assert s[k, 0] == pytest.approx(abs(w[k, 0]) * mt.SWAP["S1"]["FX"] * p.days[k] / 365)
    assert s[k, 1] == pytest.approx(abs(w[k, 1]) * mt.SWAP["S1"]["MT"] * p.days[k] / 365)
    _, c2, _, _, _ = mt.components(p, S, cost_mult=2.0)
    _, c1, _, _, _ = mt.components(p, S)
    assert c2[k, 0] - c1[k, 0] == pytest.approx(dw[k, 0] * 0.5 * p.hs[k, 0])
    n0 = mt.net_series(p, S, level="S0")
    assert (mt.net_series(p, S, level="S2") <= mt.net_series(p, S, level="S1") + 1e-15).all() and (mt.net_series(p, S, level="S1") <= n0 + 1e-15).all()


def test_sharpe_formula():
    x = np.array([0.01, -0.005, 0.02, 0.0, 0.003] * 20)
    assert mt.sharpe(x) == pytest.approx(x.mean() / x.std(ddof=1) * np.sqrt(52))


# ── null machinery ────────────────────────────────────────────────────────────────────────────────

def test_block_permutation_is_a_derangement_of_blocks():
    rng = np.random.default_rng(0)
    for n in (60, 61, 130):
        idx = mt.block_perm_index(n, 26, rng)
        assert sorted(idx.tolist()) == list(range(n))
        bounds = [(a, min(a + 26, n)) for a in range(0, n, 26)]
        for a, b in bounds:                                    # no block stays in its own place
            assert not np.array_equal(idx[a:b], np.arange(a, b)) or len(bounds) < 2
        assert not (np.arange(n) == idx).all()
    assert (mt.derangement(7, rng) != np.arange(7)).all()


def test_cohort_maps_stay_inside_cohort_and_identity_reproduces_the_actual_run(dev_panel):
    p = dev_panel
    cw = mt.cohort_weeks(p)
    assert set(cw) == set(p.cohort.tolist()) and len(cw) >= 3
    rng = np.random.default_rng(1)
    maps = mt.null_replicate(p, rng)
    for cols, tgt, src in maps:
        assert set(src.tolist()) == set(tgt.tolist()) and tgt.min() >= 0
    ident = [(cols, wk, wk) for cols, wk in cw.values()]
    for v in mt.VNAMES:
        assert np.array_equal(mt.apply_maps(p.S[v], ident), p.S[v])
    sc = mt.scale_matrix(p)
    g, c, s, w, dw = mt.components(p, mt.apply_maps(p.S["M6"], ident), scale=sc)
    assert np.allclose((g - c).sum(1), mt.net_series(p, p.S["M6"]))


def test_same_replicate_for_all_variants_and_determinism(dev_panel):
    a = mt.null_distribution(dev_panel, 6, 123)
    b = mt.null_distribution(dev_panel, 6, 123)
    c = mt.null_distribution(dev_panel, 6, 124)
    assert np.array_equal(a["sr"], b["sr"]) and not np.array_equal(a["sr"], c["sr"])
    assert a["sr"].shape == (6, len(mt.VNAMES))
    # one shared replicate per draw: recompute draw 0 by hand and compare every variant
    rng = np.random.default_rng(123)
    maps = mt.null_replicate(dev_panel, rng, "perm")
    sc = mt.scale_matrix(dev_panel)
    for vi, v in enumerate(mt.VNAMES):
        g, c, s, w, dw = mt.components(dev_panel, mt.apply_maps(dev_panel.S[v], maps), scale=sc)
        assert a["sr"][0, vi] == pytest.approx(mt.sharpe((g - c).sum(1)))


def test_null_removes_timing_but_keeps_exposure_turnover_scale(dev_panel):
    p = dev_panel
    v = "M12"
    nd = mt.null_distribution(p, 12, 5)
    st = mt.variant_stats(p, v)
    vi = mt.VNAMES.index(v)
    assert 0.6 < nd["turnover"][:, vi].mean() / st["dw"].sum(1).mean() < 1.8
    assert 0.6 < nd["gross"][:, vi].mean() / np.abs(st["w"]).sum(1).mean() < 1.4


def test_null_does_not_reject_without_signal_and_rejects_a_strong_trend():
    uni = synth_uni((("FX", 6, "2003-01-01"), ("EQ", 3, "2003-01-01"), ("MT", 2, "2003-01-01")))
    rejs = []
    for seed in range(6):
        p0 = mt.build_panel(mt.synth_insts(np.random.default_rng(40 + seed), uni, 0.0), mt.DEV_START, mt.DEV_END)
        ev = mt.evaluate(p0, 99, seed)
        rejs.append(bool((ev["wy"] <= 0.05).any()))
    assert sum(rejs) <= 1                                      # nominal 5 % per panel; allow one in six
    p1 = mt.build_panel(mt.synth_insts(np.random.default_rng(7), uni, 0.12), mt.DEV_START, mt.DEV_END)
    ev1 = mt.evaluate(p1, 99, 1)
    assert ev1["sr0"].max() > 1.0 and (ev1["wy"] <= 0.05).any()


def test_wy_stepdown_matches_bruteforce_and_holm_known_values():
    rng = np.random.default_rng(0)
    null = rng.standard_normal((400, 4)) * np.array([1, 2, 1, 1]) + np.array([0, 1, 0, 0])
    obs = np.array([2.5, 1 + 2 * 1.0, 0.2, -1.0])
    adj, z, single = mt.wy_stepdown(obs, null)
    mu, sd = null.mean(0), null.std(0, ddof=1)
    zn, zo = (null - mu) / sd, (obs - mu) / sd
    order = np.argsort(-zo)
    run, exp = 0.0, np.zeros(4)
    for j, i in enumerate(order):
        mx = zn[:, order[j:]].max(1)
        run = max(run, (1 + (mx >= zo[i]).sum()) / 401)
        exp[i] = run
    assert np.allclose(adj, exp) and (adj >= mt.p_values(obs, null) - 1e-12).all() and (single >= adj - 1e-12).all()
    rej, a = mt.holm(np.array([0.001, 0.04, 0.03, 0.2]))
    assert np.allclose(a, [0.004, 0.09, 0.09, 0.2]) and rej.tolist() == [True, False, False, False]
    p = mt.p_values(np.array([5.0]), np.zeros((1999, 1)))
    assert p[0] == pytest.approx(1 / 2000)                     # resolution of the null


def test_boot_index_is_circular_blocks_and_deterministic():
    idx = mt.boot_index(50, 13, 7, np.random.default_rng(0))
    assert idx.shape == (7, 50)
    d = np.diff(idx[:, :13], axis=1) % 50
    assert (d == 1).all()
    assert np.array_equal(idx, mt.boot_index(50, 13, 7, np.random.default_rng(0)))
    out = mt.boot_sharpes({"a": np.random.default_rng(1).standard_normal(300) * 0.01 + 0.001}, B=300)
    assert set(out["a"]["ci"]) == {13, 26, 52} and out["a"]["lo"] < out["a"]["hi"] and out["a"]["block_of_lo"] in (13, 26, 52)


# ── gates ────────────────────────────────────────────────────────────────────────────────────────

class FakePanel:
    def __init__(self, yrs):
        self.years = yrs


def test_gate_thresholds_year_count_best3_eras_and_class_robustness():
    years = np.repeat(np.arange(2003, 2021), 52)
    rng = np.random.default_rng(3)
    base = rng.standard_normal(len(years)) * 0.01
    pos = base.copy()
    for y in range(2003, 2021):
        m = years == y
        pos[m] = base[m] - base[m].mean() + (0.0006 if y not in (2005, 2006, 2007, 2008, 2009, 2010) else -0.0006)
    st = {"net_S1": pos, "loo_S1": {"FX": pos * 1.0, "EQ": pos * 1.0}}
    fp = FakePanel(years)
    G = mt.gates(fp, st, wy_p=0.03)
    assert G["detail"]["years_positive_2003_2020"] == 12 and G["G3b"]                      # exactly the threshold
    assert mt.gates(fp, st, 0.06)["G1"] is False
    st2 = {**st, "loo_S1": {"FX": pos * 1.0, "EQ": -pos}}
    assert mt.gates(fp, st2, 0.03)["G4"] is False
    carried = -0.0002 + 0.001 * rng.standard_normal(len(years))
    carried[np.isin(years, [2005, 2008, 2012])] += 0.004          # three big years carry a losing strategy
    G3 = mt.gates(fp, {"net_S1": carried, "loo_S1": {"FX": carried}}, 0.01)
    assert G3["G2"] and not G3["G3a"] and sorted(G3["detail"]["best3_years"]) == [2005, 2008, 2012] and not G3["eligible"]
    assert mt.select_candidate({"a": {"eligible": True, "detail": {"sr_S1": 0.4}}, "b": {"eligible": True, "detail": {"sr_S1": 0.6}},
                                "c": {"eligible": False, "detail": {"sr_S1": 0.9}}}) == "b"
    assert mt.select_candidate({"a": {"eligible": False, "detail": {"sr_S1": 0.4}}}) is None


# ── synthetic calibration and the full DEV run on synthetic data ─────────────────────────────────

def test_signal_free_world_has_no_edge_and_a_trend_world_has_one():
    uni = synth_uni((("FX", 6, "2003-01-01"), ("EQ", 3, "2003-01-01"), ("MT", 2, "2003-01-01"), ("EN", 1, "2003-01-01")))
    s0 = [mt.sharpe(mt.net_series(mt.build_panel(mt.synth_insts(np.random.default_rng(60 + i), uni, 0.0), mt.DEV_START, mt.DEV_END),
                                  mt.build_panel(mt.synth_insts(np.random.default_rng(60 + i), uni, 0.0), mt.DEV_START, mt.DEV_END).S["B4"], level="S0")) for i in range(3)]
    assert abs(np.mean(s0)) < 0.5
    pt = mt.build_panel(mt.synth_insts(np.random.default_rng(1), uni, 0.1), mt.DEV_START, mt.DEV_END)
    assert mt.sharpe(mt.net_series(pt, pt.S["B4"], level="S0")) > 0.8


def test_calibrate_runs_on_a_tiny_design_and_reports_gate_rates(tmp_path):
    uni = synth_uni((("FX", 4, "2003-01-01"), ("EQ", 2, "2003-01-01"), ("MT", 2, "2003-01-01")))
    res = mt.calibrate(tmp_path, uni, n_panels=2, n_perm=19, strengths=(0.0, 0.1))
    assert (tmp_path / "calibration.json").exists() and len(res["rows"]) == 2
    r0, r1 = res["rows"]
    assert r1["sr0_best"] > r0["sr0_best"] and 0.0 <= r0["any_elig"] <= 1.0
    pw = res["analytic_power"]["alpha_0.05_f_1.0"]
    assert pw["se_at_zero"] == pytest.approx(1 / np.sqrt(res["T_years"])) and 0.3 < pw["critical_sr"] < 0.45
    assert pw["power_0.5"] < pw["power_1.0"] and 0.55 < pw["sr_for_80pct_power"] < 0.75


def test_analytic_power_matches_known_normal_values():
    out = mt.analytic_power(18.4, 0.05, 1.0)
    assert out["critical_sr"] == pytest.approx(1.6449 / np.sqrt(18.4), rel=1e-3)
    assert 0.0 < out["power_0.3"] < out["power_0.5"] < out["power_0.75"] < out["power_1.0"] < 1.0


def test_run_dev_is_deterministic_stamps_prereg_hash_and_writes_outputs(tmp_path, monkeypatch):
    fake = tmp_path / "prereg.md"
    fake.write_text("prereg text")
    monkeypatch.setattr(mt, "PREREG", fake)
    insts = mt.synth_insts(np.random.default_rng(4), synth_uni((("FX", 4, "2003-01-01"), ("EQ", 2, "2003-01-01"), ("MT", 2, "2003-01-01"))), 0.05)
    r1 = mt.run_dev(tmp_path / "a", n_perm=19, n_boot=50, insts=insts, quiet=True)
    r2 = mt.run_dev(tmp_path / "b", n_perm=19, n_boot=50, insts=insts, quiet=True)
    assert r1["table"].drop(columns=[]).equals(r2["table"])
    assert (tmp_path / "a" / "prereg_sha256.txt").read_text().strip() == mt.sha256_file(fake)
    for f in ("summary.json", "variants.csv", "weekly_pnl.csv", "per_year.csv", "per_instrument.csv", "null_distribution.csv", "sensitivity.csv"):
        assert (tmp_path / "a" / f).exists()
    assert len(r1["table"]) == len(mt.VNAMES) and r1["summary"]["prereg_sha256"] == mt.sha256_file(fake)
    assert set(r1["sens"]["case"]) >= {"primary", "spread_x2", "equal_risk_weighting", "local_currency_returns", "weekday_Mon", "exec_delay_1day"}


def test_modules_have_no_live_or_terminal_imports():
    for f in (mt.__file__, mtd.__file__):
        lines = [ln.strip() for ln in Path(f).read_text(encoding="utf-8").splitlines() if ln.strip().startswith(("import ", "from "))]
        for ln in lines:
            for bad in ("MetaTrader5", "mt5_connector", "main", "utils", "strategies", "agents", "config"):
                assert not ln.split()[1].split(".")[0] == bad, (f, ln)


def test_final_run_needs_go_and_runs_once_the_candidate_is_hashed(tmp_path):
    uni = synth_uni((("FX", 4, "2003-01-01"), ("EQ", 2, "2003-01-01"), ("MT", 2, "2003-01-01")))
    insts = mt.synth_insts(np.random.default_rng(2), uni, 0.1, end="2025-05-01")
    cand, go = tmp_path / "frozen_candidate.json", tmp_path / "FINAL_GO"
    cand.write_text(json.dumps({"variant": "B4", "weekday": 2}))
    with pytest.raises(PermissionError):
        mt.run_final(tmp_path / "o", insts=insts, go_path=go, cand_path=cand, n_boot=30, n_perm=19)
    go.write_text(f"candidate_sha256={mt.sha256_file(cand)}\n")
    res = mt.run_final(tmp_path / "o", insts=insts, go_path=go, cand_path=cand, n_boot=30, n_perm=19)
    assert res["verdict"] in ("CONFIRMED", "CONSISTENT BUT NOT CONFIRMED", "NOT REPLICATED") and 190 < res["weeks"] < 215
    assert (tmp_path / "o" / "final_result.json").exists() and 0 < res["p_timing_null"] <= 1


def test_mask_years_rule_is_shared_by_dev_screen_and_final_loader():
    days = pd.bdate_range("2010-01-01", "2013-12-31")
    c = rw(len(days), 3, 0.1)
    c[(days >= "2012-01-01") & (days <= "2012-12-31")] = c[days >= "2012-01-01"][0]
    df, _ = mtd.clean_d1(raw_frame(days, c))
    assert mtd.mask_years(df) == {2012}


def test_fetch_status_and_report_refresh_write_only_the_generated_blocks(tmp_path):
    log = tmp_path / "fetch.log"
    log.write_text("AAA          Forex            100 bars  2000-01-01 -> 2026-01-01\nBBB          FAILED rc=-9\n")
    _write_d1(tmp_path, "AAA")
    st = mtd.fetch_status(log, tmp_path)
    assert "NOT complete" in st and "BBB" in st and "Fetched: AAA" in st
    log.write_text(log.read_text() + "done: 1 symbols, 1 failed: []\n")
    assert "**complete**" in mtd.fetch_status(log, tmp_path)
    rep = tmp_path / "r.md"
    rep.write_text("intro\n<!--STATUS-->\nold\n<!--/STATUS-->\nprose stays\n<!--TABLES-->\nold\n<!--/TABLES-->\n")
    (tmp_path / "dq_report_tables.md").write_text("TABLE-X")
    mtd.refresh_report(rep, tmp_path, log)
    out = rep.read_text()
    assert "TABLE-X" in out and "prose stays" in out and "old" not in out and out.startswith("intro")


def test_roll_detector_does_not_flag_a_few_extreme_days_that_happen_to_share_a_bin():
    days = pd.bdate_range("2003-01-01", "2020-12-31")
    rng = np.random.default_rng(21)
    r = 0.004 * rng.standard_normal(len(days))
    in_bin = np.where((days.day >= 6) & (days.day <= 10))[0]
    out_bin = np.where((days.day >= 16))[0]
    r[rng.choice(in_bin, 9, replace=False)] += 0.04            # 9 of 14 extreme days in one 5-day bin (the AUDNZD case)
    r[rng.choice(out_bin, 5, replace=False)] -= 0.04
    f = pd.DataFrame({"date": days, "close": 100 * np.exp(np.cumsum(r))})
    out = mtd.roll_like(f)
    assert out["n_extreme"] < mtd.ROLL_MIN_N and not out["roll_like"]
