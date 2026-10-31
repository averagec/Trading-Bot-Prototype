"""Tests for the walk-forward / permutation-null machinery with planted signals (synthetic data only).
The machinery must (a) find a planted directional signal, (b) find nothing when there is none, (c) have the
S-feature shift remove the planted signal, (d) never train on rows whose labels reach into the test block."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from research.ai import learnability as lb                                   # noqa: E402
from research.ai.build import stack_sides                                    # noqa: E402
from research.ai.cv import walk_forward, half_year_blocks                    # noqa: E402
from research.ai.features import S_FEATURES, N_FEATURES                      # noqa: E402
from research.ai.stats import week_index, auc_score, block_bootstrap, logloss  # noqa: E402


def make_df(signal: float, seed: int = 0, years=(2016, 2017, 2018)) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for s in ("EURUSD", "GBPUSD", "USDJPY"):
        t = pd.date_range(f"{years[0]}-01-02", f"{years[-1]}-12-28", freq="1h", tz="UTC")
        t = t[(t.dayofweek < 5)]
        n = len(t)
        d = pd.DataFrame({"symbol": s, "time": t - pd.Timedelta(hours=1), "T": t})
        for c in S_FEATURES + N_FEATURES:
            d[c] = rng.normal(0, 1, n)
        d["hrs_to_flat"] = rng.uniform(0, 48, n)
        rows.append(d)
    df = pd.concat(rows, ignore_index=True).sort_values(["time", "symbol"]).reset_index(drop=True)
    n = len(df)
    # long wins with p = sigmoid(-1 + signal*ret4 + 0.5*atr_pct); the mirrored short sees -ret4 -> p = sigmoid(-1 - signal*ret4 ...)
    z = -1.0 + 0.5 * df["atr_pct"].to_numpy()
    pL = 1 / (1 + np.exp(-(z + signal * df["ret4"].to_numpy())))
    pS = 1 / (1 + np.exp(-(z - signal * df["ret4"].to_numpy())))
    winL, winS = rng.random(n) < pL, rng.random(n) < pS
    for tag, win in (("L", winL), ("S", winS)):
        df[f"r_{tag}"] = np.where(win, 2.95, -1.05)
        df[f"rg_{tag}"] = df[f"r_{tag}"] + 0.05
        df[f"reason_{tag}"] = np.where(win, 0, 1)
        df[f"valid_{tag}"] = True
        df[f"w_{tag}"] = 0.2
        df[f"exit_T_{tag}"] = df["T"] + pd.Timedelta(hours=24)
    df["cost_r_lab"] = 0.05
    df["week"] = week_index(df["T"])
    df["year"] = df["T"].dt.year
    return df


BLOCKS = half_year_blocks("2017-01-01", "2019-01-01")


def run(df, model="LR"):
    st = stack_sides(df, S_FEATURES + N_FEATURES)
    F = lb.oof_predict(st, S_FEATURES + N_FEATURES, model, blocks=BLOCKS)
    B = lb.oof_predict(st, N_FEATURES, model, blocks=BLOCKS)
    return st, F, B, lb.metrics(st, F["p"], B["p"], F["p0"], F["thr"], n_boot=300, auc_boot=20)


def test_planted_signal_is_found():
    st, F, B, m = run(make_df(1.2))
    assert m["D1_B1_minus_F"]["lo"] > 0                      # log-loss improvement over the non-directional model
    assert m["dir_auc"] > 0.6 and m["dir_auc_ci"]["lo"] > 0.55
    assert m["top"]["gain_vs_random_side"]["lo"] > 0         # choosing the side by score beats a random side


def test_no_signal_finds_nothing():
    st, F, B, m = run(make_df(0.0, seed=1))
    assert m["D1_B1_minus_F"]["est"] < 1e-3                  # F cannot be better than B1 by more than noise
    assert abs(m["dir_auc"] - 0.5) < 0.02
    assert m["D1_B1_minus_F"]["lo"] <= 0.0005


def test_geometry_only_signal_is_attributed_to_the_non_directional_model():
    """atr_pct (an N feature) moves the win rate; B1 must capture it, so F adds (almost) nothing."""
    st, F, B, m = run(make_df(0.0, seed=2))
    assert m["D0_base_minus_B1"]["lo"] > 0                   # N features DO predict (atr_pct is planted)
    assert abs(m["D1_B1_minus_F"]["est"]) < 5e-4


def test_walk_forward_purges_by_label_end_and_never_trains_on_the_future():
    df = make_df(0.5)
    st = stack_sides(df, S_FEATURES + N_FEATURES)
    n_checked = 0
    for (a, b), tr, te in walk_forward(st["T"], st["exit_T"], BLOCKS, embargo_days=7):
        assert (st["exit_T"].iloc[tr] <= a - pd.Timedelta(days=7)).all()
        assert (st["T"].iloc[tr] < a).all()
        assert (st["T"].iloc[te] >= a).all() and (st["T"].iloc[te] < b).all()
        assert len(set(tr) & set(te)) == 0
        n_checked += 1
    assert n_checked == len(BLOCKS)


def test_purge_matters_a_label_straddling_the_boundary_is_excluded():
    T = pd.Series(pd.to_datetime(["2016-12-30 10:00", "2016-12-20 10:00", "2017-01-03 10:00"], utc=True))
    ex = pd.Series(pd.to_datetime(["2017-01-02 10:00", "2016-12-21 10:00", "2017-01-04 10:00"], utc=True))
    (a, b), tr, te = next(iter(walk_forward(T, ex, [(pd.Timestamp("2017-01-01", tz="UTC"),
                                                      pd.Timestamp("2017-07-01", tz="UTC"))])))
    assert tr.tolist() == [1]                     # row 0 ends after the embargo cut (2016-12-25) -> purged
    assert te.tolist() == [2]


def test_shift_directional_moves_only_s_features_by_whole_weeks():
    df = make_df(1.0)
    k = 40
    sh = lb.shift_directional(df, k)
    assert (sh["r_L"] == df["r_L"]).all() and (sh["atr_pct"] == df["atr_pct"]).all()
    # a row whose shifted source exists carries exactly that source row's S values
    T = pd.to_datetime(df["T"], utc=True)
    lo, hi = T.astype("int64").min(), T.astype("int64").max()
    span = int(np.ceil((hi - lo + 1) / lb.WEEK_NS)) * lb.WEEK_NS
    src = lo + ((T.astype("int64").to_numpy() - lo + k * lb.WEEK_NS) % span)
    key = pd.Series(np.arange(len(df)), index=pd.MultiIndex.from_arrays([df["symbol"], T.astype("int64")]))
    i = 5000
    j = key.get((df["symbol"].iloc[i], int(src[i])))
    if j is not None:
        assert sh["ret4"].iloc[i] == df["ret4"].iloc[j]
    assert (sh["ret4"].notna().mean() > 0.8)
    # same hour of week
    assert ((src - T.astype("int64").to_numpy()) % lb.WEEK_NS == 0).all()


def test_permutation_null_removes_the_planted_signal():
    df = make_df(1.2)
    _, _, _, real = run(df)
    sh = lb.shift_directional(df, 60)
    st = stack_sides(sh, S_FEATURES + N_FEATURES)
    F = lb.oof_predict(st, S_FEATURES + N_FEATURES, "LR", blocks=BLOCKS)
    B = lb.oof_predict(st, N_FEATURES, "LR", blocks=BLOCKS)
    m = lb.metrics(st, F["p"], B["p"], F["p0"], F["thr"], full=False)
    assert real["D1_B1_minus_F"]["est"] > 20 * max(m["D1_B1_minus_F"]["est"], 1e-5)
    assert abs(m["dir_auc"] - 0.5) < 0.03


def test_stats_helpers():
    y = np.array([0, 0, 1, 1])
    assert auc_score(y, np.array([0.1, 0.2, 0.3, 0.4])) == 1.0
    assert auc_score(y, np.array([0.5, 0.5, 0.5, 0.5])) == 0.5
    w = np.repeat(np.arange(10), 5)
    s = np.column_stack([np.ones(10) * 5, np.arange(10) * 5.0])
    b = block_bootstrap(s, lambda t: t[1] / t[0], 200, 0)
    assert b["est"] == pytest.approx(4.5) and b["lo"] < 4.5 < b["hi"]
    assert logloss(np.array([1.0]), np.array([0.5]))[0] == pytest.approx(np.log(2))
