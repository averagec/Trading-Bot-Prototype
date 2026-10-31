"""Tests for research/ai/predictability.py on planted synthetic data."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from research.ai import predictability as pr                       # noqa: E402
from research.ai.features import S_FEATURES, N_FEATURES            # noqa: E402
from research.ai.stats import week_index                           # noqa: E402


def make(beta: float, seed=0, n=30000):
    rng = np.random.default_rng(seed)
    T = pd.date_range("2016-01-04", periods=n, freq="1h", tz="UTC")
    df = pd.DataFrame({"T": T})
    for c in S_FEATURES + N_FEATURES:
        df[c] = rng.normal(0, 1, n)
    df["week"] = week_index(T)
    df["atr_pips"] = 10.0
    df["spread_pips"] = 0.7
    sig = beta * df["ret4"].to_numpy()
    for h in pr.HORIZONS:
        df[f"fwd{h}_pips"] = (sig + rng.normal(0, 1, n)) * 10.0 * np.sqrt(h)
        df[f"rng{h}_pips"] = 10.0 * np.exp(0.5 * df["atr_pct"].to_numpy() + rng.normal(0, 0.2, n))
    return df


def test_ic_table_recovers_planted_ic_and_only_that():
    ic = pr.ic_table(make(0.5), n_boot=200)
    d = ic[(ic["family"] == "direction") & (ic["h"] == 4)].set_index("feature")
    assert d.loc["ret4", "z"] > 10 and d.loc["ret4", "lo"] > 0.1
    others = d.drop(index="ret4")
    assert (others["z"].abs() < 4.0).all()
    r = ic[(ic["family"] == "range") & (ic["h"] == 4)].set_index("feature")
    assert r.loc["atr_pct", "z"] > 10                      # planted range dependence on atr_pct


def test_no_signal_gives_no_ic_and_oof_auc_half():
    df = make(0.0, seed=3)
    ic = pr.ic_table(df, n_boot=200)
    assert (ic[ic["family"] == "direction"]["z"].abs() < 4.5).all()
    blocks = pr.half_year_blocks("2017-01-01", "2018-06-01")
    sc = pr.oof_direction(df, 4, S_FEATURES, "LR", blocks)
    ok = np.isfinite(sc)
    from research.ai.stats import auc_score
    assert ok.sum() > 5000
    assert abs(auc_score((df["fwd4_pips"].to_numpy()[ok] > 0).astype(float), sc[ok]) - 0.5) < 0.02


def test_oof_auc_finds_planted_signal_and_survive_spread_reports_costs():
    df = make(0.5, seed=4)
    blocks = pr.half_year_blocks("2017-01-01", "2018-06-01")
    sc = pr.oof_direction(df, 4, S_FEATURES, "LR", blocks)
    ok = np.isfinite(sc)
    from research.ai.stats import auc_score
    assert auc_score((df["fwd4_pips"].to_numpy()[ok] > 0).astype(float), sc[ok]) > 0.6
    r = pr.survive_spread(df, 4, sc, n_boot=200)
    assert r["gross_pips"]["lo"] > 0 and r["net_pips"]["est"] < r["gross_pips"]["est"]
    assert abs(r["mean_cost_pips"] - (0.7 + pr.COMM_PIPS)) < 1e-9
