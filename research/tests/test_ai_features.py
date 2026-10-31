"""No-look-ahead and sanity tests for research/ai/features.py (synthetic data only).

Truncation: cut every timeframe at a time (keep bars closed by the cut); features of rows decided at or before the cut
must be identical to the full-data features. Scramble: replace every bar that had NOT closed at the cut by garbage in
all timeframes; features decided at or before the cut must not move.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from research.ai import dataset as ds                                                   # noqa: E402
from research.ai.features import (all_pair_features, mirror, S_FEATURES, N_FEATURES, usd_factor,  # noqa: E402
                                  pair_zreturns)
from research.ai.labels import LabelSpec                                                 # noqa: E402
from research.synth import synth_bars                                                    # noqa: E402

LS = LabelSpec()


def _spec(digits, point, contract=100000.0, ccy="USD"):
    return {"digits": digits, "point": point, "tick_size": point, "tick_value": 1.0, "contract_size": contract,
            "volume_min": 0.01, "volume_max": 50.0, "volume_step": 0.01, "stops_level": 0,
            "currency_base": "X", "currency_profit": ccy, "spread_now": 0}


SPECS = {"EURUSD": _spec(5, 1e-5), "GBPUSD": _spec(5, 1e-5), "USDJPY": _spec(3, 1e-3, ccy="JPY")}


def invert(df, scale):
    out = df.copy()
    o, h, l, c = (df[k].to_numpy() for k in ("open", "high", "low", "close"))
    out["open"], out["high"], out["low"], out["close"] = scale / o, scale / l, scale / h, scale / c
    return out


@pytest.fixture(scope="module")
def pairs():
    base = synth_bars(n_days=170, seed=1, price0=1.1, step_sd=0.00015)
    rng = np.random.default_rng(0)
    out = {}
    for sym, f in (("EURUSD", lambda d: d.copy()), ("GBPUSD", lambda d: d.copy()),
                   ("USDJPY", lambda d: invert(d, 120.0))):
        fr = {}
        for tf in ("H1", "H4"):
            d = f(base[tf]).reset_index(drop=True)
            sp = rng.integers(1, 20, len(d)).astype(float)
            sp[rng.random(len(d)) < 0.05] = 0.0                         # some unrecorded zeros
            d["spread"] = sp
            fr[tf] = d
        out[sym] = fr
    return out


def compute(pairs):
    return all_pair_features({s: p["H1"] for s, p in pairs.items()}, {s: p["H4"] for s, p in pairs.items()}, LS, SPECS)


def closed(df, cut, minutes):
    return df[df["time"] + pd.Timedelta(minutes=minutes) <= cut].reset_index(drop=True)


@pytest.fixture(scope="module")
def full(pairs):
    return compute(pairs)


def test_all_features_present_and_finite_after_warmup(full):
    f = full["EURUSD"].iloc[1500:]
    for c in S_FEATURES + N_FEATURES:
        assert c in f.columns
        assert np.isfinite(f[c].to_numpy(float)).mean() > 0.99, c


@pytest.mark.parametrize("frac", [0.5, 0.8])
def test_truncation_gives_identical_features_before_cut(pairs, full, frac):
    h1 = pairs["EURUSD"]["H1"]
    cut = h1["time"].iloc[int(len(h1) * frac)] + pd.Timedelta(minutes=17)         # cut inside a bar on purpose
    part = compute({s: {"H1": closed(p["H1"], cut, 60), "H4": closed(p["H4"], cut, 240)} for s, p in pairs.items()})
    for s in pairs:
        n = len(part[s])
        assert n > 1000
        assert (full[s]["time"].iloc[:n].to_numpy() == part[s]["time"].to_numpy()).all()
        pd.testing.assert_frame_equal(full[s].iloc[:n].reset_index(drop=True), part[s], check_exact=False, rtol=1e-10,
                                      atol=1e-12)


def test_scrambling_unclosed_bars_does_not_move_earlier_features(pairs, full):
    h1 = pairs["EURUSD"]["H1"]
    cut = h1["time"].iloc[int(len(h1) * 0.7)] + pd.Timedelta(minutes=30)
    rng = np.random.default_rng(9)
    scr = {}
    for s, p in pairs.items():
        d = {}
        for tf, mins in (("H1", 60), ("H4", 240)):
            x = p[tf].copy()
            bad = (x["time"] + pd.Timedelta(minutes=mins) > cut).to_numpy()
            for c in ("open", "high", "low", "close"):
                x.loc[bad, c] = x.loc[bad, c].to_numpy() * rng.uniform(0.5, 1.9, bad.sum())
            x.loc[bad, "spread"] = rng.integers(0, 500, bad.sum())
            d[tf] = x
        scr[s] = d
    alt = compute(scr)
    for s in pairs:
        known = (full[s]["time"] + pd.Timedelta(minutes=60) <= cut).to_numpy()
        assert known.sum() > 1000
        pd.testing.assert_frame_equal(full[s][known].reset_index(drop=True), alt[s][known].reset_index(drop=True),
                                      check_exact=False, rtol=1e-10, atol=1e-12)
        # the scramble really changed something afterwards (the test can fail)
        assert not np.allclose(full[s].loc[~known, "ret1"].to_numpy(), alt[s].loc[~known, "ret1"].to_numpy(),
                               equal_nan=True)


def test_h4_bar_still_forming_is_not_used(pairs):
    """A decision at a time inside an H4 bar must use the previous closed H4 bar, never the forming one."""
    s = "EURUSD"
    f = compute(pairs)[s]
    h4 = pairs[s]["H4"]
    h1 = pairs[s]["H1"]
    i = 1800
    T = h1["time"].iloc[i] + pd.Timedelta(hours=1)
    last_closed = h4[h4["time"] + pd.Timedelta(hours=4) <= T].iloc[-1]
    h4_mod = h4.copy()
    h4_mod.loc[h4_mod["time"] > last_closed["time"], ["open", "high", "low", "close"]] *= 1.5
    pairs2 = {k: {"H1": v["H1"], "H4": h4_mod if k == s else v["H4"]} for k, v in pairs.items()}
    g = compute(pairs2)[s]
    for c in ("h4_trend", "h4_ret6", "h4_atr_pct"):
        assert g[c].iloc[i] == pytest.approx(f[c].iloc[i], rel=1e-12)


def test_mirror_is_an_involution_and_swaps_hi_lo(full):
    X = full["EURUSD"][S_FEATURES + N_FEATURES].iloc[1800:1900]
    M = mirror(X)
    pd.testing.assert_frame_equal(mirror(M), X)
    assert (M["dist_hi20"] == X["dist_lo20"]).all() and (M["ret4"] == -X["ret4"]).all()
    assert (M[N_FEATURES] == X[N_FEATURES]).all().all()


def test_price_inversion_negates_return_features(pairs):
    """Reflecting the price path (p -> k/p) negates the return features and swaps the distances (approximately)."""
    base = {"EURUSD": pairs["EURUSD"], "GBPUSD": pairs["GBPUSD"], "USDJPY": pairs["USDJPY"]}
    flipped = {s: {"H1": invert(p["H1"], 1.0 * (120.0 if s == "USDJPY" else 1.0) ** 0 * 2.0), "H4": invert(p["H4"], 2.0)}
               for s, p in base.items()}
    f0 = compute(base)["EURUSD"]
    f1 = compute(flipped)["EURUSD"]
    sl = slice(1500, 2300)
    a, b = f0["ret4"].to_numpy()[sl], f1["ret4"].to_numpy()[sl]
    assert np.corrcoef(a, -b)[0, 1] > 0.99
    a, b = f0["dist_hi20"].to_numpy()[sl], f1["dist_lo20"].to_numpy()[sl]
    assert np.corrcoef(a, b)[0, 1] > 0.98


def test_usd_factor_orientation():
    """GBPUSD and EURUSD move together; USDJPY is their inverse: the USD factor seen by GBPUSD equals EURUSD's own move."""
    base = synth_bars(n_days=60, seed=2, price0=1.1, step_sd=0.00015)["H1"]
    fr = {"EURUSD": base, "GBPUSD": base.copy(), "USDJPY": invert(base, 120.0)}
    zr = {s: pair_zreturns(d) for s, d in fr.items()}
    times = {s: d["time"].values for s, d in fr.items()}
    f = usd_factor(zr, times, 4)
    ok = np.isfinite(f["GBPUSD"]) & np.isfinite(zr["EURUSD"][4])
    assert ok.sum() > 500
    assert np.corrcoef(f["GBPUSD"][ok], zr["EURUSD"][4][ok])[0, 1] > 0.98


def test_calendar_features_are_deterministic_functions_of_time(full):
    f = full["EURUSD"]
    T = f["time"] + pd.Timedelta(hours=1)
    assert np.allclose(f["hour_sin"], np.sin(2 * np.pi * T.dt.hour / 24))
    fri_late = (T.dt.dayofweek == 4) & (T.dt.hour >= 20)
    assert (f.loc[fri_late, "hrs_to_flat"] == 0).all()
    assert (f["hrs_to_flat"] <= 48).all()
