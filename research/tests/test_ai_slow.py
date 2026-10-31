"""Tests for the slow-horizon (H4 / D1) study: D1 from H1 incl. DST, H1-path labels, no-look-ahead of the slow features,
sentinel era handling, the permutation-null / stability / Holm machinery on planted signals, the go-token guard.
Synthetic data only; nothing touches MT5, the real cache or FINAL."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from research.ai import dataset as ds                                              # noqa: E402
from research.ai import slow as sl                                                 # noqa: E402
from research.ai import slow_study as ss                                           # noqa: E402
from research.ai import slow_power as sp                                           # noqa: E402
from research.ai.cv import half_year_blocks                                        # noqa: E402
from research.ai.labels import LabelSpec, barrier_scan, label_pair                 # noqa: E402
from research.ai.stats import auc_score, week_index                                # noqa: E402
from research.data import ny_is_dst, utc_to_server                                 # noqa: E402
from research.synth import synth_bars                                              # noqa: E402


def _spec(digits, point, ccy="USD"):
    return {"digits": digits, "point": point, "tick_size": point, "tick_value": 1.0, "contract_size": 100000.0,
            "volume_min": 0.01, "volume_max": 50.0, "volume_step": 0.01, "stops_level": 0,
            "currency_base": "X", "currency_profit": ccy, "spread_now": 0}


SPECS = {"EURUSD": _spec(5, 1e-5), "GBPUSD": _spec(5, 1e-5), "USDJPY": _spec(3, 1e-3, "JPY")}


# ── synthetic broker-clock H1 / H4 ────────────────────────────────────────────────────

def session_times(first_sunday: str, weeks: int) -> pd.DatetimeIndex:
    """H1 bar opens of consecutive weeks. The week opens Sunday 22:00 UTC in winter and 21:00 while US DST is in force
    (= server Monday 00:00 in UTC+2 / UTC+3) and runs 120 hours to the Friday close."""
    out = []
    for w in range(weeks):
        sun = pd.Timestamp(first_sunday, tz="UTC") + pd.Timedelta(weeks=w)
        probe = pd.Series([sun + pd.Timedelta(hours=21, minutes=30)])
        open_h = 21 if bool(ny_is_dst(probe)[0]) else 22
        t0 = sun + pd.Timedelta(hours=open_h)
        out.append(pd.date_range(t0, periods=120, freq="1h"))
    return pd.DatetimeIndex(np.concatenate([o.values for o in out])).tz_localize("UTC")


def make_h1(sym: str, weeks: int, seed: int, first_sunday="2021-01-03", invert=False) -> pd.DataFrame:
    t = session_times(first_sunday, weeks)
    rng = np.random.default_rng(seed)
    n = len(t)
    ret = rng.normal(0, 0.00055, n)
    close = 1.10 + np.cumsum(ret)
    open_ = np.concatenate([[1.10], close[:-1]])
    high = np.maximum(open_, close) + np.abs(rng.normal(0, 0.0003, n))
    low = np.minimum(open_, close) - np.abs(rng.normal(0, 0.0003, n))
    df = pd.DataFrame({"time": t, "open": open_, "high": high, "low": low, "close": close,
                       "volume": np.ones(n, dtype="uint64")})
    if invert:
        df = df.assign(open=120.0 / df["open"], high=120.0 / df["low"], low=120.0 / df["high"], close=120.0 / df["close"])
    sp_ = rng.integers(2, 15, n)
    sp_[rng.random(n) < 0.03] = 0
    df["spread"] = sp_.astype("int32")
    return ds.add_clean_spread(ds.impute_spread(df), sym)


def make_h4(h1: pd.DataFrame) -> pd.DataFrame:
    """Broker-grid H4: group H1 bars by the server-time 4h bucket (server 00:00, 04:00 ...)."""
    key = utc_to_server(h1["time"]).dt.floor("4h").to_numpy()
    g = h1.groupby(key, sort=True)
    return pd.DataFrame({"time": g["time"].first(), "open": g["open"].first(), "high": g["high"].max(),
                         "low": g["low"].min(), "close": g["close"].last(), "spread": g["spread"].last()}).reset_index(drop=True)


@pytest.fixture(scope="module")
def frames():
    h1 = {"EURUSD": make_h1("EURUSD", 170, 1), "GBPUSD": make_h1("GBPUSD", 170, 2),
          "USDJPY": make_h1("USDJPY", 170, 3, invert=True)}
    h4 = {s: make_h4(f) for s, f in h1.items()}
    return h1, h4


# ── D1 from H1 ────────────────────────────────────────────────────────────────────────

def test_d1_ny_close_day_across_both_dst_changes():
    h1 = make_h1("EURUSD", 70, 5, first_sunday="2024-01-07")          # spans 2024-03-10 (DST on) and 2024-11-03 (off)
    d1 = sl.build_d1(h1)
    assert (d1["n"] == 24).all() and len(d1) == 70 * 5
    assert ((d1["T"] - d1["time"]) == pd.Timedelta(hours=24)).all()
    winter = ~ny_is_dst(d1["T"])
    assert (d1.loc[winter, "T"].dt.hour == 22).all() and (d1.loc[~winter, "T"].dt.hour == 21).all()
    # both regimes occur, and the day containing each DST change still has exactly 24 bars
    assert winter.any() and (~winter).any()
    around = d1[(d1["T"] > "2024-03-09") & (d1["T"] < "2024-03-16")]
    assert (around["n"] == 24).all() and len(around) == 5
    # five D1 bars per calendar week: Monday..Friday server days, the Friday one closing at the weekly close
    assert (d1["T"].dt.dayofweek.value_counts().sort_index().to_numpy() == [70] * 5).all()


def test_d1_ohlc_is_the_aggregate_of_its_h1_bars():
    h1 = make_h1("EURUSD", 6, 6, first_sunday="2022-02-06")
    d1 = sl.build_d1(h1)
    first = d1.iloc[2]
    day = h1[(h1["time"] >= first["time"]) & (h1["time"] < first["T"])]
    assert len(day) == 24
    assert first["open"] == day["open"].iloc[0] and first["close"] == day["close"].iloc[-1]
    assert first["high"] == day["high"].max() and first["low"] == day["low"].min()


def test_d1_drops_stubs_and_incomplete_final_day_and_is_prefix_stable():
    h1 = make_h1("EURUSD", 30, 7)
    full = sl.build_d1(h1)
    cut = full["T"].iloc[60] + pd.Timedelta(hours=9)                    # inside a day
    part = sl.build_d1(h1[h1["time"] + pd.Timedelta(hours=1) <= cut])
    expected = full[full["T"] <= cut].reset_index(drop=True)
    pd.testing.assert_frame_equal(part, expected)                      # the day in progress is NOT a D1 bar
    assert part["T"].max() <= cut
    stub = pd.concat([h1.iloc[:5], h1.iloc[24 * 3 + 10:]], ignore_index=True)
    assert len(sl.build_d1(stub)) < len(sl.build_d1(h1))


# ── labels on an H1 path ─────────────────────────────────────────────────────────────

def test_h1_path_friday_flatten_and_barriers_hand_computed():
    t = pd.date_range("2024-01-04 12:00", periods=60, freq="1h", tz="UTC")          # Thursday noon onward
    n = len(t)
    px = np.full(n, 1.1)
    O = H = L = C = px.copy()
    S = np.zeros(n)
    tt = t.values.astype("datetime64[ns]")
    m = 5                                                     # last H1 bar of the decision opens 17:00 -> T = 18:00 Thu
    f = int(np.flatnonzero(t == pd.Timestamp("2024-01-05 19:00", tz="UTC"))[0])      # H1 bar ending 20:00 Friday
    C = C.copy()
    C[f] = 1.1012
    r = barrier_scan(tt, O, H, L, C, S, np.array([m]), np.array([0.0040]), 1, 3.0, 7 * 1440, 20.0, bar_min=60)
    assert r["valid"][0] and r["reason"][0] == 3 and r["exit_row"][0] == f and r["exit"][0] == pytest.approx(1.1012)
    H2 = H.copy()
    H2[20] = 1.1130                                           # target = 1.1 + 3 x 0.004 = 1.112 hit before the flatten
    r = barrier_scan(tt, O, H2, L, C, S, np.array([m]), np.array([0.0040]), 1, 3.0, 7 * 1440, 20.0, bar_min=60)
    assert r["reason"][0] == 0 and r["exit_row"][0] == 20
    late = int(f)                                             # decision whose T is 20:00 Friday is invalid
    r = barrier_scan(tt, O, H, L, C, S, np.array([late]), np.array([0.0040]), 1, 3.0, 7 * 1440, 20.0, bar_min=60)
    assert not r["valid"][0]


def test_h1_path_labels_agree_with_m5_path_labels_and_are_never_more_optimistic():
    bars = synth_bars(n_days=40, seed=11, start="2024-01-01", step_sd=0.0004)
    m5, h1 = bars["M5"].copy(), bars["H1"].copy()
    for d in (m5, h1):
        d["spread_pts"] = 2.0
        d["spread_clean_pts"] = 2.0
    ls = LabelSpec(k_atr=1.0, horizon_min=7 * 1440, spread_col="spread_clean_pts")
    dec = h1.iloc[40:].reset_index(drop=True)
    a = label_pair("EURUSD", dec, h1, SPECS, ls, tf_min=60, bar_min=60)
    b = label_pair("EURUSD", dec, m5, SPECS, ls, tf_min=60, bar_min=5)
    for tag in ("L", "S"):
        both = (a[f"valid_{tag}"] & b[f"valid_{tag}"]).to_numpy()
        assert both.sum() > 300
        ra, rb = a[f"r_{tag}"].to_numpy()[both], b[f"r_{tag}"].to_numpy()[both]
        same = (a[f"reason_{tag}"].to_numpy()[both] == b[f"reason_{tag}"].to_numpy()[both]).mean()
        assert same > 0.97
        assert (ra <= rb + 1e-9).all()                         # the stop-wins tie rule can only lower R on coarser bars


# ── features: no look-ahead, causal, mirror ──────────────────────────────────────────

def build(spec, h1, h4, first="2021-09-01"):
    return sl.build_from_frames(spec, h1, h4, SPECS, first_decision=first)


def truncate_closed(h1, h4, cut):
    return ({s: f[f["time"] + pd.Timedelta(hours=1) <= cut].reset_index(drop=True) for s, f in h1.items()},
            {s: f[f["time"] + pd.Timedelta(hours=4) <= cut].reset_index(drop=True) for s, f in h4.items()})


def compare_before_cut(full, alt, cut):
    cols = sl.SLOW_S + sl.SLOW_N
    a = full[full["T"] <= cut].set_index(["symbol", "T"])[cols].sort_index()
    b = alt[alt["T"] <= cut].set_index(["symbol", "T"])[cols].sort_index()
    both = a.index.intersection(b.index)
    assert len(both) > 400 and len(both) >= 0.99 * len(a)
    pd.testing.assert_frame_equal(a.loc[both], b.loc[both], check_exact=False, rtol=1e-10, atol=1e-12)
    return len(both)


@pytest.mark.parametrize("name", ["H4_k10", "D1_k15"])
def test_slow_features_truncation_gives_identical_features_before_cut(frames, name):
    h1, h4 = frames
    spec = sl.VARIANTS[name]
    full = build(spec, h1, h4)
    cut = h1["EURUSD"]["time"].iloc[int(len(h1["EURUSD"]) * 0.7)] + pd.Timedelta(minutes=37)
    th1, th4 = truncate_closed(h1, h4, cut)
    alt = build(spec, th1, th4)
    compare_before_cut(full, alt, cut)


@pytest.mark.parametrize("name", ["H4_k15", "D1_k10"])
def test_slow_features_scrambling_unclosed_bars_does_not_move_earlier_features(frames, name):
    h1, h4 = frames
    spec = sl.VARIANTS[name]
    full = build(spec, h1, h4)
    cut = h1["EURUSD"]["time"].iloc[int(len(h1["EURUSD"]) * 0.65)] + pd.Timedelta(minutes=20)
    rng = np.random.default_rng(3)
    s1, s4 = {}, {}
    for s in h1:
        for src, dst, mins in ((h1[s], s1, 60), (h4[s], s4, 240)):
            x = src.copy()
            bad = (x["time"] + pd.Timedelta(minutes=mins) > cut).to_numpy()
            for c in ("open", "high", "low", "close"):
                x.loc[bad, c] = x.loc[bad, c].to_numpy() * rng.uniform(0.6, 1.8, bad.sum())
            x.loc[bad, "spread"] = rng.integers(0, 400, bad.sum()).astype("int32")
            dst[s] = x
    for s in s1:
        s1[s] = ds.add_clean_spread(ds.impute_spread(s1[s].drop(columns=[c for c in s1[s].columns if c in
                                    ("spread_pts", "spread_was_zero", "spread_rec", "spread_sentinel", "spread_clean_pts")])), s)
    alt = build(spec, s1, s4)
    compare_before_cut(full, alt, cut)
    later = full[full["T"] > cut + pd.Timedelta(days=5)].set_index(["symbol", "T"])["ret_a"]
    later_alt = alt[alt["T"] > cut + pd.Timedelta(days=5)].set_index(["symbol", "T"])["ret_a"]
    idx = later.index.intersection(later_alt.index)
    assert len(idx) > 50 and not np.allclose(later.loc[idx], later_alt.loc[idx], equal_nan=True)   # the test can fail


def test_labels_start_after_decision_and_embargo_exceeds_longest_label(frames):
    h1, h4 = frames
    for name, spec in {**sl.VARIANTS, **sl.SENSITIVITY}.items():
        assert spec.embargo_days * 1440 > spec.horizon_min, name
    d = build(sl.VARIANTS["H4_k10"], h1, h4)
    ok = d["valid_L"]
    assert (d.loc[ok, "exit_T_L"] > d.loc[ok, "T"]).all()
    assert ((d.loc[ok, "exit_T_L"] - d.loc[ok, "T"]) <= pd.Timedelta(days=7.5)).all()
    f = d[d["T"].dt.dayofweek == 4]
    assert not (f.loc[f["T"].dt.hour >= 20, "valid_L"]).any()     # Friday decisions from 20:00 are invalid


def test_mirror_slow_is_an_involution_and_variants_are_the_registered_four():
    X = pd.DataFrame(np.random.default_rng(0).normal(size=(5, len(sl.SLOW_S + sl.SLOW_N))), columns=sl.SLOW_S + sl.SLOW_N)
    pd.testing.assert_frame_equal(sl.mirror_slow(sl.mirror_slow(X)), X)
    assert (sl.mirror_slow(X)["dist_hi_s"] == X["dist_lo_s"]).all() and (sl.mirror_slow(X)[sl.SLOW_N] == X[sl.SLOW_N]).all().all()
    assert sorted(sl.VARIANTS) == ["D1_k10", "D1_k15", "H4_k10", "H4_k15"]
    assert sorted({s.k_atr for s in sl.VARIANTS.values()}) == [1.0, 1.5]


def test_loader_guard_refuses_final_for_the_slow_build():
    with pytest.raises(ValueError):
        sl.build_variant(sl.VARIANTS["D1_k10"], end="2022-01-01")


# ── sentinel vs nominal constant spreads ─────────────────────────────────────────────

def test_constant_nominal_spread_before_2018_is_kept_but_the_2020_placeholder_is_missing():
    t1 = pd.date_range("2001-03-05", periods=24 * 40, freq="1h", tz="UTC")
    a = pd.DataFrame({"time": t1, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1, "spread": 50})
    out = ds.add_clean_spread(ds.impute_spread(a), "EURUSD")
    assert not out["spread_sentinel"].any() and (out["spread_clean_pts"] == 50).all() and out["spread_rec"].notna().all()
    t2 = pd.date_range("2020-06-01", periods=24 * 40, freq="1h", tz="UTC")
    sp_ = np.where(np.arange(len(t2)) % 3 == 0, 50, 2)
    b = pd.DataFrame({"time": t2, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1, "spread": sp_})
    out = ds.add_clean_spread(ds.impute_spread(b), "EURUSD")
    assert out["spread_sentinel"].sum() == (sp_ == 50).sum() and (out["spread_clean_pts"] == 2).all()


# ── study machinery on planted signals ───────────────────────────────────────────────

ALL = sl.SLOW_S + sl.SLOW_N
BLK = half_year_blocks("2008-01-01", "2016-01-01")


def make_slow_df(signal=0.0, seed=0, signal_year=None):
    rng = np.random.default_rng(seed)
    rows = []
    for s in ("EURUSD", "GBPUSD", "USDJPY"):
        t = pd.date_range("2004-01-05", "2015-12-28", freq="8h", tz="UTC")
        t = t[t.dayofweek < 5]
        d = pd.DataFrame({"symbol": s, "time": t - pd.Timedelta(hours=4), "T": t})
        for c in ALL:
            d[c] = rng.normal(0, 1, len(d))
        rows.append(d)
    df = pd.concat(rows, ignore_index=True).sort_values(["T", "symbol"]).reset_index(drop=True)
    n = len(df)
    sig = signal * df["ret_a"].to_numpy()
    if signal_year is not None:
        sig = np.where(df["T"].dt.year.to_numpy() == signal_year, sig, 0.0)
    z = -0.4 + 0.3 * df["atr_pct"].to_numpy()
    pL = 1 / (1 + np.exp(-(z + sig)))
    pS = 1 / (1 + np.exp(-(z - sig)))
    for tag, p in (("L", pL), ("S", pS)):
        win = rng.random(n) < p
        df[f"r_{tag}"] = np.where(win, 1.9, -1.1)
        df[f"reason_{tag}"] = np.where(win, 0, 1)
        df[f"valid_{tag}"] = True
        df[f"w_{tag}"] = 0.5
        df[f"exit_T_{tag}"] = df["T"] + pd.Timedelta(hours=40)
    df["cost_r_lab"] = 0.03
    df["week"] = week_index(df["T"])
    df["year"] = df["T"].dt.year
    df["era"] = sl.era_of(df["T"])
    return df


def fit_eval(df, spec_name="D1_k10"):
    spec = sl.VARIANTS[spec_name]
    st = ss.stack_variant(df)
    F = ss.fit_oof(st, spec, "LR", blks=BLK)
    B = ss.fit_oof(st, spec, "LR", cols=sl.SLOW_N, blks=BLK)
    return st, F, B, ss.evaluate(st, F, B, n_boot=300, auc_boot=40)


def test_planted_signal_passes_c1_stability_and_falsifier_and_null_gives_a_verdict():
    df = make_slow_df(0.9, seed=1)
    st, F, B, res = fit_eval(df)
    assert res["holm_week"]["lower_bound"] > 0 and res["holm_4week"]["lower_bound"] > 0
    assert res["stability"]["share_positive_years"] >= 0.7 and res["stability"]["min_leave_one_year_out_D1"] > 0
    assert not res["falsifier"]["falsified"] and res["falsifier"]["eras_at_or_above_0.52"] == 3
    spec = sl.VARIANTS["D1_k10"]
    base = pd.DataFrame({"rid": st["rid"], "pB": B["p"], "p0": F["p0"]})
    import tempfile
    with tempfile.TemporaryDirectory(dir=str(ROOT / "research" / ".pytest_tmp_ai")) as td:
        path = Path(td) / "perm.jsonl"
        ss.run_perm_variant(df, spec, base, 5, path, blks=BLK, min_shift=26)
        perm = ss.perm_summary(path, res["overall"]["D1_B1_minus_F"]["est"])
    assert perm["B"] == 5 and perm["obs"] > perm["null_max"]
    dec = ss.decide(res, perm, c1=True)
    assert dec["C1"] and dec["C2"] and dec["S_stability"] and dec["F_falsifier_survived"] and dec["learnable"]


def test_no_signal_is_not_learnable():
    df = make_slow_df(0.0, seed=2)
    st, F, B, res = fit_eval(df)
    assert res["holm_week"]["lower_bound"] <= 0
    assert not ss.decide(res, None)["learnable"]
    assert abs(res["overall"]["dir_auc"] - 0.5) < 0.03


def test_signal_in_one_year_only_fails_the_stability_rule():
    """Hand-built out-of-fold predictions: informative in 2012 only, noise elsewhere. The aggregate looks significant,
    the pre-registered stability rule must not."""
    df = make_slow_df(0.0, seed=3)
    st = ss.stack_variant(df)
    rng = np.random.default_rng(0)
    y = st["y"].to_numpy(float)
    yr = st["year"].to_numpy()
    oof = yr >= 2008
    pB = np.where(oof, 0.4, np.nan)
    pF = np.where(oof, np.clip(0.4 + rng.normal(0, 0.02, len(st)), 0.01, 0.99), np.nan)
    inform = oof & (yr == 2012)
    pF[inform] = np.clip(0.4 + 0.35 * (y[inform] - 0.4), 0.01, 0.99)
    F = {"p": pF, "p0": np.where(oof, 0.4, np.nan), "thr": np.where(oof, 0.6, np.nan)}
    res = ss.evaluate(st, F, {"p": pB}, n_boot=300, auc_boot=40)
    assert res["holm_week"]["lower_bound"] > 0                   # the aggregate looks significant ...
    assert res["stability"]["share_positive_years"] < 0.7       # ... but one year carries it
    assert res["stability"]["min_leave_one_year_out_D1"] < 0
    perm = {"obs": 1.0, "null_mean": 0.0, "null_sd": 0.1, "null_max": 0.2, "B": 100}
    dec = ss.decide(res, perm, c1=True)
    assert dec["C1"] and dec["C2"] and not dec["S_stability"] and not dec["learnable"]


def test_falsifier_counts_eras_when_the_signal_exists_in_one_era_only():
    df = make_slow_df(2.0, seed=4)
    mask = (df["T"].dt.year > 2009).to_numpy()
    for tag in ("L", "S"):                                       # kill the signal outside 2008-2009 by re-drawing outcomes
        rng = np.random.default_rng(9 if tag == "L" else 10)
        p = 1 / (1 + np.exp(-(-0.4 + 0.3 * df["atr_pct"].to_numpy())))
        win = rng.random(len(df)) < p
        df.loc[mask, f"r_{tag}"] = np.where(win, 1.9, -1.1)[mask]
    st, F, B, res = fit_eval(df)
    fa = res["falsifier"]
    assert fa["era_dir_auc"]["2003-2009"] > 0.9                     # the signal exists in the first era ...
    assert all(abs(fa["era_dir_auc"][e] - 0.5) < 0.04 for e in ("2010-2013", "2014-2021"))   # ... and nowhere else
    assert fa["falsified"] == (fa["eras_at_or_above_0.52"] < 2)   # the pre-registered counting rule


def test_holm_rejections_step_down_and_go_guard():
    r = ss.holm_rejections({"a": 0.001, "b": 0.02, "c": 0.04, "d": 0.5})
    assert r == {"a": True, "b": False, "c": False, "d": False}        # 0.001 <= 0.05/4, then 0.02 > 0.05/3 stops
    r = ss.holm_rejections({"a": 0.001, "b": 0.005, "c": 0.01, "d": 0.012})
    assert all(r.values())
    for tok in (None, "yes", ""):
        with pytest.raises(SystemExit):
            ss.require_go(tok)
    ss.require_go(ss.GO_TOKEN)


def test_by_group_stats_leave_one_out_removes_the_group():
    df = make_slow_df(1.0, seed=5)
    st, F, B, _ = fit_eval(df)
    g = ss.by_group_stats(st, F["p"], B["p"], st["year"].to_numpy())
    ev = np.isfinite(F["p"]) & np.isfinite(B["p"])
    y = st["y"].to_numpy(float)
    from research.ai.stats import logloss
    d = logloss(y, B["p"]) - logloss(y, F["p"])
    yr = st["year"].to_numpy()
    k = sorted(g["groups"])[2]
    assert g["groups"][k]["D1"] == pytest.approx(np.nanmean(d[ev & (yr == int(k))]))
    assert g["loo"][k]["D1"] == pytest.approx(np.nanmean(d[ev & (yr != int(k))]))


def test_power_planting_realises_the_target_directional_auc():
    df = make_slow_df(0.0, seed=6)
    for auc in (0.54, 0.60):
        g = sp.gamma_for_auc(auc)
        p = sp.plant(sp.noise_frame(df, 1), g, 7)
        yL, yS = p["r_L"] > 0, p["r_S"] > 0
        sel = (yL != yS).to_numpy()
        got = auc_score(yL[sel].to_numpy(), p["ret_a"].to_numpy()[sel])
        assert got == pytest.approx(auc, abs=0.02)
    p0 = sp.plant(sp.noise_frame(df, 1), 0.0, 7)
    sel = ((p0["r_L"] > 0) != (p0["r_S"] > 0)).to_numpy()
    assert abs(auc_score((p0["r_L"] > 0).to_numpy()[sel], p0["ret_a"].to_numpy()[sel]) - 0.5) < 0.02


def test_sentinel_mask_ignores_the_2001_constant_in_both_research_data_and_the_era_wrapper():
    """research.data.sentinel_mask is restricted to bars from 2018 (the nominal constant 50 of 2000-2003 is not the placeholder)."""
    from research.data import sentinel_mask
    t = pd.date_range("2001-03-05", periods=24 * 40, freq="1h", tz="UTC")
    a = pd.DataFrame({"time": t, "spread": 50})
    assert not sentinel_mask(a, "EURUSD").any()
    assert not ds.sentinel_mask_era(a, "EURUSD").any()


def test_decide_requires_the_four_week_block_interval_to_agree():
    res = {"holm_week": {"lower_bound": 1e-4}, "holm_4week": {"lower_bound": -1e-4},
           "stability": {"share_positive_years": 0.9, "min_leave_one_year_out_D1": 1e-4},
           "falsifier": {"falsified": False}, "overall": {"top": {}}}
    perm = {"obs": 1.0, "null_mean": 0.0, "null_sd": 0.1, "null_max": 0.2, "B": 100}
    assert not ss.decide(res, perm, c1=True)["C1"]
    res["holm_4week"]["lower_bound"] = 1e-4
    assert ss.decide(res, perm, c1=True)["C1"]
