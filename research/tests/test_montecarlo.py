import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from research.montecarlo import trade_mc, ftmo_mc, FtmoRules, edge_summary   # noqa: E402


def _returns(mean_r, n=400, risk=0.5, win=0.25, seed=0):
    rng = np.random.default_rng(seed)
    w = rng.random(n) < win
    return np.where(w, 3 * risk, -risk)


def test_zero_edge_is_near_coin_flip():
    r = np.array([1.0, -1.0] * 200)                       # symmetric, zero mean (compounding drag makes it slightly < 0.5)
    res = trade_mc(r, n_paths=5000)
    assert 0.30 < res["p_profit"] < 0.60


def test_positive_edge_beats_negative_edge():
    good = trade_mc(_returns(0, win=0.35), n_paths=3000)
    bad = trade_mc(_returns(0, win=0.15), n_paths=3000)
    assert good["p_profit"] > 0.9 > 0.1 > bad["p_profit"]


def test_shuffle_keeps_total_return_but_changes_path():
    r = _returns(0, win=0.3)
    res = trade_mc(r, n_paths=500, mode="shuffle")
    expect = (np.prod(1 + r / 100) - 1) * 100
    assert res["final_return_pct"]["p05"] == pytest.approx(expect, rel=1e-6)
    assert res["final_return_pct"]["p95"] == pytest.approx(expect, rel=1e-6)


def test_ftmo_deterministic_and_monotonic():
    rng = np.random.default_rng(1)

    def days(mu):
        pnl = rng.normal(mu, 0.8, 250)
        return pd.DataFrame({"pnl_pct": pnl, "low_pct": np.minimum(pnl, 0) - abs(rng.normal(0, .3, 250)),
                             "traded": True})
    a = ftmo_mc(days(0.25), FtmoRules(), n_paths=4000, seed=3)
    b = ftmo_mc(days(0.25), FtmoRules(), n_paths=4000, seed=3)
    weak = ftmo_mc(days(-0.05), FtmoRules(), n_paths=4000, seed=3)
    assert a["p_pass"] > weak["p_pass"]
    tot = a["p_pass"] + a["p_fail_daily_loss"] + a["p_fail_max_loss"] + a["p_unresolved_at_horizon"]
    assert tot == pytest.approx(1.0)
    assert a["rules"] == b["rules"]


def test_daily_loss_rule_triggers():
    days = pd.DataFrame({"pnl_pct": [0.1] * 60, "low_pct": [-6.0] * 60, "traded": True})
    res = ftmo_mc(days, FtmoRules(max_daily_loss_pct=5.0), n_paths=500)
    assert res["p_fail_daily_loss"] == pytest.approx(1.0)
