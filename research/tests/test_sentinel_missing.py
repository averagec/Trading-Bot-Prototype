"""The 50-point placeholder spread: sentinel_mask, prepare_bars(sentinel_missing=...) and the flag plumbing. No MT5 needed."""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from research import data as D                                   # noqa: E402
from research.data import prepare_bars, sentinel_mask            # noqa: E402


def _month(start, n, spread, step_min=5):
    t = pd.date_range(start, periods=n, freq=f"{step_min}min", tz="UTC")
    c = 1.1 + np.arange(n) * 1e-6
    return pd.DataFrame({"time": t, "open": c, "high": c + 1e-5, "low": c - 1e-5, "close": c,
                         "volume": 10, "spread": spread})


def _two_months(tmp_path, symbol="EURUSD"):
    """Jan: clean, spread 4 (a few isolated 50s). Feb: dense placeholder - every 2nd bar is 50, the rest are 1."""
    jan = _month("2021-01-04", 1000, 4)
    jan.loc[jan.index % 250 == 0, "spread"] = 50                                  # 4 isolated 50s: a genuine rollover spike
    feb = _month("2021-02-01", 2000, 1)
    feb.loc[feb.index % 2 == 0, "spread"] = 50                                    # 50 % of the month
    df = pd.concat([jan, feb], ignore_index=True)
    for tf in D.TFS:
        D.save_bars(df, tmp_path / f"{symbol}_{tf}.parquet")
    return df


def test_mask_flags_dense_months_only_and_never_gold():
    df = pd.concat([_month("2021-01-04", 100, 4), _month("2021-02-01", 100, 1)], ignore_index=True)
    df.loc[0, "spread"] = 50                                                      # 1 % of January: not a placeholder month
    df.loc[df["time"] >= "2021-02-01", "spread"] = 50                             # all of February
    m = sentinel_mask(df, "EURUSD")
    assert not m[:100].any() and m[100:].all()
    assert sentinel_mask(df, "XAUUSD").sum() == 0
    assert len(sentinel_mask(df.iloc[:0], "EURUSD")) == 0


def test_constant_nominal_50_before_2018_is_never_flagged():
    old = _month("2002-03-04", 100, 50)                                           # 2000-2003 H1/H4: constant nominal 50
    assert not sentinel_mask(old, "EURUSD").any()
    new = _month("2018-03-05", 100, 50)
    assert sentinel_mask(new, "EURUSD").all()


def test_default_off_keeps_the_recorded_50s(tmp_path):
    raw = _two_months(tmp_path)
    out = prepare_bars("EURUSD", tmp_path, check_quality=False)["M5"]
    assert np.array_equal(out["spread_pts"].to_numpy(), raw["spread"].to_numpy(float))
    assert not out["spread_was_sentinel"].any()


def test_flag_replaces_sentinel_by_same_hour_clean_median_and_keeps_isolated_50s(tmp_path):
    raw = _two_months(tmp_path)
    out = prepare_bars("EURUSD", tmp_path, check_quality=False, sentinel_missing=True)["M5"]
    is50 = raw["spread"].to_numpy() == 50
    feb = (raw["time"] >= "2021-02-01").to_numpy()
    flagged = out["spread_was_sentinel"].to_numpy()
    assert np.array_equal(flagged, is50 & feb)                                    # only the dense month's 50s
    # clean bars (spread 1 in Feb, 4 in Jan, excluding Jan's isolated 50s): every flagged bar gets a median of those, not 50
    assert (out.loc[flagged, "spread_pts"] < 50).all() and (out.loc[flagged, "spread_pts"] >= 1).all()
    assert (out.loc[~flagged, "spread_pts"].to_numpy() == raw.loc[~flagged, "spread"].to_numpy(float)).all()
    assert (out.loc[is50 & ~feb, "spread_pts"] == 50).all()                       # January's isolated 50s are kept


def test_sentinel_does_not_pollute_the_median_and_zero_still_imputes(tmp_path):
    raw = _two_months(tmp_path)
    raw.loc[raw.index[5], "spread"] = 0                                           # a recorded zero in January
    for tf in D.TFS:
        D.save_bars(raw, tmp_path / f"EURUSD_{tf}.parquet")
    out = prepare_bars("EURUSD", tmp_path, check_quality=False, sentinel_missing=True)["M5"]
    assert out["spread_was_zero"].iloc[5] and 0 < out["spread_pts"].iloc[5] < 50
    # oracle: the replacement is the same-UTC-hour median of the bars that are neither zero nor sentinel
    sent = out["spread_was_sentinel"]
    clean = raw["spread"].astype(float).where((raw["spread"] > 0) & ~sent)
    expect = clean.groupby(raw["time"].dt.hour).transform("median")
    assert sent.any() and np.allclose(out.loc[sent, "spread_pts"], expect[sent])
    with_sentinel = raw["spread"].astype(float).where(raw["spread"] > 0).groupby(raw["time"].dt.hour).transform("median")
    assert not np.allclose(with_sentinel[sent], expect[sent])                     # so the exclusion is actually tested


def test_gold_is_untouched_by_the_flag(tmp_path):
    raw = _two_months(tmp_path, "XAUUSD")
    out = prepare_bars("XAUUSD", tmp_path, check_quality=False, sentinel_missing=True)["M5"]
    assert not out["spread_was_sentinel"].any()
    assert np.array_equal(out["spread_pts"].to_numpy(), raw["spread"].to_numpy(float))


def test_flag_reaches_candidates_and_skips_the_candidate_cache(monkeypatch):
    import research.run as R
    seen = {}

    def fake_prepare(symbol, start=None, end=None, sentinel_missing=False, **kw):
        seen["flag"] = sentinel_missing
        return {"M5": pd.DataFrame()}

    monkeypatch.setattr(R, "prepare_bars", fake_prepare)
    monkeypatch.setattr(R, "build_candidates", lambda *a, **k: pd.DataFrame({"x": [1]}))
    monkeypatch.setattr(R, "build_candidates_cached", lambda *a, **k: pytest.fail("cache must not be used with the flag"))
    out = R.get_candidates(["EURUSD"], {"EURUSD": {"point": 1e-5}}, R.SignalConfig(), None, False, 1, end=None,
                           sentinel_missing=True)
    assert seen["flag"] is True and len(out) == 1


def test_flag_off_still_uses_the_cache(monkeypatch):
    import research.run as R
    monkeypatch.setattr(R, "prepare_bars", lambda *a, **k: {"M5": pd.DataFrame()})
    monkeypatch.setattr(R, "build_candidates", lambda *a, **k: pytest.fail("uncached builder must not run"))
    monkeypatch.setattr(R, "build_candidates_cached", lambda *a, **k: pd.DataFrame({"x": [1]}))
    assert len(R.get_candidates(["EURUSD"], {"EURUSD": {"point": 1e-5}}, R.SignalConfig(), None, False, 1)) == 1


def test_null_worker_passes_the_flag_to_prepare_bars(monkeypatch, tmp_path):
    import research.nulltest as N
    seen = {}

    class Stop(Exception):
        pass

    def fake_prepare(symbol, start=None, end=None, sentinel_missing=False, **kw):
        seen["flag"] = sentinel_missing
        raise Stop

    cand = tmp_path / "c.parquet"
    pd.DataFrame({"x": [1]}).to_parquet(cand)
    monkeypatch.setattr(N, "prepare_bars", fake_prepare)
    with pytest.raises(Stop):
        N._run_seeds(([0], cand, ["EURUSD"], {}, None, None, None, "reflect", True))
    assert seen["flag"] is True
    with pytest.raises(Stop):
        N._run_seeds(([0], cand, ["EURUSD"], {}, None, None, None, "reflect"))
    assert seen["flag"] is False                                                  # old 8-element callers are unchanged
