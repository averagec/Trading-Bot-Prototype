"""Fidelity tests: the research code must reproduce the live logic and must not look ahead."""

import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from research.signals import (SignalConfig, qt_asof, build_candidates, compose_confluence,   # noqa: E402
                              detect_sweep_m15_fixed)
from research.sim import SimConfig, size_position, profit_usd                                  # noqa: E402
from research.synth import synth_bars, truncate_closed                                         # noqa: E402
import strategies.smc_strategies as live                                                       # noqa: E402

SPECS = {
    "EURUSD": dict(digits=5, point=1e-5, contract_size=100000, volume_min=0.01, volume_max=100, volume_step=0.01,
                   stops_level=0, currency_profit="USD"),
    "USDJPY": dict(digits=3, point=1e-3, contract_size=100000, volume_min=0.01, volume_max=100, volume_step=0.01,
                   stops_level=0, currency_profit="JPY"),
    "XAUUSD": dict(digits=2, point=0.01, contract_size=100, volume_min=0.01, volume_max=50, volume_step=0.01,
                   stops_level=0, currency_profit="USD"),
}


@pytest.fixture(scope="module")
def bars():
    return synth_bars(n_days=70, seed=3)


# ── 1. quarterly-theory port == live function under a frozen clock ────────────────
@pytest.mark.parametrize("hour", range(24))
def test_qt_port_matches_live(bars, hour):
    h1 = bars["H1"].iloc[-300:]
    frozen = datetime(2024, 3, 5, hour, 30, tzinfo=timezone.utc)

    class FakeDT:
        @classmethod
        def now(cls, tz=None):
            return frozen

    with mock.patch.object(live, "datetime", FakeDT):
        expected = live.detect_quarterly_theory_h1(h1)
    assert qt_asof(h1, hour) == expected


# ── 2. confluence composition == live run_confluence under a frozen clock ─────────
def test_confluence_matches_live(bars):
    m5, m15, h1, h4 = (bars[t].iloc[-500:].reset_index(drop=True) for t in ("M5", "M15", "H1", "H4"))
    frozen = datetime(2024, 3, 5, 14, 5, tzinfo=timezone.utc)

    class FakeDT:
        @classmethod
        def now(cls, tz=None):
            return frozen

    with mock.patch.object(live, "datetime", FakeDT):
        expected = live.run_confluence(m15, h1, h4, m5)
    if expected["direction"] == "NONE":
        pytest.skip("synthetic window has no H4 bias")
    d = expected["direction"]
    mine = compose_confluence(
        live.detect_trend_h4(h4), live.detect_fvg_h1(h1, d), qt_asof(h1, 14),
        live.detect_sweep_m15(m15, d, expected["h1_level"]), live.detect_entry_candle_m5(m5, d))
    assert mine["probability"] == expected["probability"]
    assert mine["checks_passed"] == expected["checks_passed"]
    assert mine["checklist"] == expected["checklist"]


# ── 3. live sweep is dead; the fixed one is not ───────────────────────────────────
def test_live_sweep_cannot_fire_but_fixed_can():
    rng = np.random.default_rng(0)
    live_hits = fixed_hits = 0
    for _ in range(400):
        n = 60
        c = 1.1 + np.cumsum(rng.normal(0, 0.0004, n))
        df = pd.DataFrame({"open": np.r_[c[0], c[:-1]], "close": c})
        df["high"] = df[["open", "close"]].max(axis=1) + abs(rng.normal(0, 0.0003, n))
        df["low"] = df[["open", "close"]].min(axis=1) - abs(rng.normal(0, 0.0003, n))
        for d in ("BUY", "SELL"):
            live_hits += live.detect_sweep_m15(df, d)["signal"] != "NONE"
            fixed_hits += detect_sweep_m15_fixed(df, d)["signal"] != "NONE"
    assert live_hits == 0
    assert fixed_hits > 0


# ── 4. position sizing port == live RiskManager.calculate_position ────────────────
@pytest.mark.parametrize("symbol,entry,sl,direction", [
    ("EURUSD", 1.10000, 1.09800, "BUY"),
    ("EURUSD", 1.10000, 1.09970, "BUY"),      # tight stop -> expanded to 10 pips
    ("EURUSD", 1.10000, 1.10300, "SELL"),
    ("USDJPY", 150.000, 149.600, "BUY"),
    ("XAUUSD", 2000.00, 2004.00, "SELL"),
])
@pytest.mark.parametrize("atr_f,size_f", [(1.0, 1.0), (0.5, 0.5), (0.75, 1.0)])
def test_sizing_matches_live(symbol, entry, sl, direction, atr_f, size_f):
    import utils.risk_manager as rm
    spec = SPECS[symbol]

    class Shim:
        def get_symbol_info(self, s):
            return {k: spec[k] for k in ("digits", "point", "volume_min", "volume_max", "volume_step", "stops_level")}

        def calculate_profit(self, symbol, direction, volume, entry, exit_price):
            return profit_usd(spec, symbol, direction, volume, entry, exit_price)

    # scaled-risk path: the live bot now defaults to FIXED_RISK_PCT, so switch it off here
    with mock.patch.object(rm, "connector", Shim()), mock.patch.object(rm, "FIXED_RISK_PCT", None):
        expected = rm.RiskManager().calculate_position(symbol, direction, entry, sl, 100_000.0,
                                                       df=None, size_factor=atr_f * size_f)
    mine = size_position(spec, symbol, direction, entry, sl, 100_000.0, atr_f, size_f, SimConfig())
    assert mine["volume"] == pytest.approx(expected["volume"])
    assert mine["sl"] == pytest.approx(expected["sl"])
    assert mine["tp"] == pytest.approx(expected["tp"])
    assert mine["risk_amount"] == pytest.approx(expected["risk_amount"], abs=0.01)


@pytest.mark.parametrize("symbol,entry,sl,direction", [
    ("EURUSD", 1.10000, 1.09800, "BUY"),
    ("USDJPY", 150.000, 149.600, "BUY"),
    ("XAUUSD", 2000.00, 2004.00, "SELL"),
])
@pytest.mark.parametrize("size_f", [1.0, 0.5, 0.25])
def test_fixed_risk_sizing_matches_live_and_ignores_scaling(symbol, entry, sl, direction, size_f):
    """Live FIXED_RISK_PCT == sim fixed_risk_pct: the regime/ATR factors no longer change the size."""
    import utils.risk_manager as rm
    from dataclasses import replace
    spec = SPECS[symbol]

    class Shim:
        def get_symbol_info(self, s):
            return {k: spec[k] for k in ("digits", "point", "volume_min", "volume_max", "volume_step", "stops_level")}

        def calculate_profit(self, symbol, direction, volume, entry, exit_price):
            return profit_usd(spec, symbol, direction, volume, entry, exit_price)

    with mock.patch.object(rm, "connector", Shim()), mock.patch.object(rm, "FIXED_RISK_PCT", 0.25):
        live_pos = rm.RiskManager().calculate_position(symbol, direction, entry, sl, 100_000.0,
                                                       df=None, size_factor=size_f)
    full = rm.RiskManager
    with mock.patch.object(rm, "connector", Shim()), mock.patch.object(rm, "FIXED_RISK_PCT", 0.25):
        live_full = full().calculate_position(symbol, direction, entry, sl, 100_000.0, df=None, size_factor=1.0)
    assert live_pos["risk_pct"] == 0.25
    assert live_pos["volume"] == live_full["volume"]                       # size_factor has no effect
    mine = size_position(spec, symbol, direction, entry, sl, 100_000.0, 1.0, 1.0,
                         replace(SimConfig(), risk_per_trade_pct=0.25))
    assert mine["volume"] == pytest.approx(live_pos["volume"])
    assert mine["risk_amount"] == pytest.approx(live_pos["risk_amount"], abs=0.01)


# ── 5. no look-ahead: candidates up to a cut are identical with or without the future ─
def test_no_lookahead(bars):
    cfg = SignalConfig(window=220, min_checks=3, min_score=30.0)       # looser gates => more rows to compare
    cut = bars["M5"]["time"].iloc[int(len(bars["M5"]) * 0.8)]
    full = build_candidates("EURUSD", bars, cfg, point=1e-5)
    part = build_candidates("EURUSD", truncate_closed(bars, cut), cfg, point=1e-5)
    if len(part) == 0:
        pytest.skip("no candidates in synthetic window")
    f = full[full["T"] <= cut].reset_index(drop=True)
    cols = [c for c in part.columns if c != "bar_idx"]
    pd.testing.assert_frame_equal(f[cols], part[cols], check_exact=False, rtol=1e-12)
