"""No-look-ahead tests for the idea generators (same spirit as test_fidelity.py::test_no_lookahead):
candidates with T <= cut are identical with and without bars after the cut."""

import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from research.synth import synth_bars                                         # noqa: E402
from research.ideas import idea_a, idea_b, idea_c, idea_z                       # noqa: E402
from research.ideas.common import truncate_closed, CAND_COLS, ema, atr, adx     # noqa: E402

POINT = 1e-5


@pytest.fixture(scope="module")
def bars():
    # 200 days of synthetic weekday-only 24h bars: enough H4 history for EMA200 + ADX warm-up
    return synth_bars(n_days=200, seed=11, step_sd=0.00035)


# loosened parameters so the random walk produces enough rows to compare
LOOSE = {
    "A": (idea_a, idea_a.Params(excursion_cap=5.0)),
    "B": (idea_b, idea_b.Params(adx_thr=0.0, zone_depth=3.0, min_gap_hours=0.25)),
    "C": (idea_c, idea_c.Params(vol_filter=0.0)),
    "Z": (idea_z, idea_z.Params()),
}


def _cuts(bars, fracs=(0.55, 0.7, 0.83)):
    t = bars["M5"]["time"]
    return [t.iloc[int(len(t) * f)] for f in fracs]


def scramble_unclosed(bars, cut, seed=5):
    """Perturb every bar that is NOT fully closed at `cut` (open + tf > cut), keeping the closed past untouched."""
    from research.ideas.common import TF_MIN
    rng = np.random.default_rng(seed)
    out = {}
    for tf, df in bars.items():
        d = df.copy()
        m = (d["time"] + pd.Timedelta(minutes=TF_MIN[tf]) > cut).to_numpy()
        for col in ("open", "high", "low", "close"):
            d.loc[m, col] = d.loc[m, col].to_numpy() * (1 + rng.normal(0, 0.02, int(m.sum())))
        out[tf] = d
    return out


def _compare(mod, params, bars, cut, full=None):
    """Candidates with T <= cut must be identical (a) on truncated data and (b) when all not-yet-closed bars are scrambled.
    Both are checked right up to the cut, which is where a one-bar leak would show."""
    full = mod.generate("EURUSD", bars, POINT, params) if full is None else full
    f = full[full["T"] <= cut].reset_index(drop=True)
    cols = [c for c in full.columns if c != "bar_idx"]
    for variant in (truncate_closed(bars, cut), scramble_unclosed(bars, cut)):
        part = mod.generate("EURUSD", variant, POINT, params)
        p = part[part["T"] <= cut].reset_index(drop=True)
        pd.testing.assert_frame_equal(f[cols], p[cols], check_exact=False, rtol=1e-12)
    return len(f)


def _candidate_cuts(full, k=6, seed=0):
    """Cuts placed exactly at (and 5 min before) randomly chosen candidate times - the most sensitive places."""
    rng = np.random.default_rng(seed)
    ts = list(full["T"].iloc[rng.choice(len(full), size=min(k, len(full)), replace=False)])
    return ts + [t - pd.Timedelta(minutes=5) for t in ts[:3]]


@pytest.mark.parametrize("arm", list(LOOSE))
def test_no_lookahead_synthetic(bars, arm):
    mod, params = LOOSE[arm]
    full = mod.generate("EURUSD", bars, POINT, params)
    assert len(full) > 10, f"arm {arm}: synthetic window produced too few candidates to compare"
    for cut in _cuts(bars) + _candidate_cuts(full):
        _compare(mod, params, bars, cut, full)


@pytest.mark.parametrize("arm,symbol,mod", [("A", "EURUSD", idea_a), ("B", "USDJPY", idea_b),
                                            ("C", "XAUUSD", idea_c), ("Z", "GBPUSD", idea_z)])
def test_no_lookahead_real_data(arm, symbol, mod):
    """Real cached bars, default (pre-registered) parameters, several cuts incl. the dev/holdout split."""
    from research.data import load_specs, prepare_bars
    from research.ideas.common import SPLIT
    try:
        b = prepare_bars(symbol)
        point = load_specs()[symbol]["point"]
    except FileNotFoundError:
        pytest.skip("cached data not available")
    params = mod.Params()
    full = mod.generate(symbol, b, point, params)
    assert len(full) > 5, "no real-data candidates"
    t = b["M5"]["time"]
    cuts = [SPLIT, t.iloc[int(len(t) * 0.5)], t.iloc[int(len(t) * 0.8)] + pd.Timedelta(minutes=35)]
    cuts += _candidate_cuts(full, k=4, seed=3)
    for cut in cuts:
        for variant in (truncate_closed(b, cut), scramble_unclosed(b, cut)):
            part = mod.generate(symbol, variant, point, params)
            f = full[full["T"] <= cut].reset_index(drop=True)
            p = part[part["T"] <= cut].reset_index(drop=True)
            cols = [c for c in part.columns if c != "bar_idx"]
            pd.testing.assert_frame_equal(f[cols], p[cols], check_exact=False, rtol=1e-12)


@pytest.mark.parametrize("arm", list(LOOSE))
def test_schema_and_semantics(bars, arm):
    mod, params = LOOSE[arm]
    c = mod.generate("EURUSD", bars, POINT, params)
    assert list(c.columns) == CAND_COLS
    assert len(c) > 0
    assert (c["entry_type"] == "MARKET").all()
    # T is the close of an M5 bar
    assert (c["T"].dt.minute % 5 == 0).all() and (c["T"].dt.second == 0).all()
    buy = c["direction"] == "BUY"
    assert (c.loc[buy, "sl_raw"] < c.loc[buy, "price"]).all()
    assert (c.loc[~buy, "sl_raw"] > c.loc[~buy, "price"]).all()
    assert np.allclose(c.loc[buy, "price"], c.loc[buy, "ask"]) and np.allclose(c.loc[~buy, "price"], c.loc[~buy, "bid"])
    assert (c["expire_ns"] > c["T"].astype("int64")).all()
    assert (c["T"].dt.dayofweek < 5).all()
    # at most one candidate per (T, symbol)
    assert not c.duplicated(["T", "symbol"]).any()
    # entry is at the close of the bar whose close time is T
    m5 = bars["M5"].reset_index(drop=True)
    assert np.allclose(m5["close"].to_numpy()[c["bar_idx"].to_numpy()], c["bid"].to_numpy())


def test_indicators_are_causal():
    rng = np.random.default_rng(1)
    n = 600
    c = 1.1 + np.cumsum(rng.normal(0, 0.001, n))
    h, l = c + abs(rng.normal(0, .0005, n)), c - abs(rng.normal(0, .0005, n))
    full_e, part_e = ema(c, 50), ema(c[:400], 50)
    assert np.allclose(full_e[:400], part_e)
    assert np.allclose(atr(h, l, c, 14)[:400], atr(h[:400], l[:400], c[:400], 14))
    assert np.allclose(adx(h, l, c, 14)[:400], adx(h[:400], l[:400], c[:400], 14))
    a = adx(h, l, c, 14)
    assert np.nanmin(a) >= 0 and np.nanmax(a) <= 100
