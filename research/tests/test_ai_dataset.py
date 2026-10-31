"""Tests for research/ai/dataset.py: FINAL is unreachable, sentinel spreads, clean-spread fill (synthetic data)."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from research.ai import dataset as ds                       # noqa: E402


def frame(start, periods, spread, freq="1h"):
    t = pd.date_range(start, periods=periods, freq=freq, tz="UTC")
    n = len(t)
    return pd.DataFrame({"time": t, "open": 1.1, "high": 1.1, "low": 1.1, "close": 1.1,
                         "volume": np.ones(n, dtype="uint64"), "spread": np.asarray(spread, dtype="int32")})


def test_check_end_refuses_final_and_none():
    assert ds.check_end("2021-06-01") == pd.Timestamp("2021-06-01", tz="UTC")
    assert ds.check_end("2020-01-01") < pd.Timestamp(ds.DEV_END, tz="UTC")
    for bad in ("2021-06-02", "2025-01-01", None):
        with pytest.raises(ValueError):
            ds.check_end(bad)


def test_load_bars_never_returns_bars_at_or_after_dev_end(tmp_path):
    df = frame("2021-05-25", 24 * 20, 5)                     # crosses 2021-06-01
    df.to_parquet(tmp_path / "EURUSD_H1.parquet", index=False)
    out = ds.load_bars("EURUSD", "H1", data_dir=tmp_path)
    assert out["time"].max() < pd.Timestamp(ds.DEV_END, tz="UTC") and len(out) == 24 * 7
    with pytest.raises(ValueError):
        ds.load_bars("EURUSD", "H1", end="2022-01-01", data_dir=tmp_path)


def test_sentinel_only_flagged_in_dense_months():
    # month A: 60 % of bars at 50 points; month B: a single isolated 50 (a genuine wide spread) among 3-point bars
    a = frame("2020-06-01", 24 * 20, np.where(np.arange(24 * 20) % 5 < 3, 50, 1))
    b = frame("2020-07-01", 24 * 20, np.full(24 * 20, 3))
    b.loc[10, "spread"] = 50
    df = pd.concat([a, b], ignore_index=True)
    m = ds.sentinel_mask(df, "EURUSD")
    assert m[: len(a)].sum() == (a["spread"] == 50).sum()
    assert m[len(a):].sum() == 0
    assert ds.sentinel_mask(df, "XAUUSD").sum() == 0           # gold is never flagged


def test_clean_spread_replaces_sentinel_and_zero_with_same_hour_median():
    n = 24 * 40
    sp = np.full(n, 2)
    sp[::3] = 50
    sp[1::3] = 0
    df = frame("2020-06-01", n, sp)
    out = ds.add_clean_spread(df, "EURUSD")
    assert out["spread_rec"].isna().sum() == ((sp == 50) | (sp == 0)).sum()
    assert (out["spread_clean_pts"] == 2).all()
    assert out["spread_sentinel"].sum() == (sp == 50).sum()


def test_commission_pips_eurusd_and_usdjpy():
    eur = {"digits": 5, "point": 1e-5, "contract_size": 100000.0, "currency_profit": "USD"}
    jpy = {"digits": 3, "point": 1e-3, "contract_size": 100000.0, "currency_profit": "JPY"}
    assert ds.commission_pips("EURUSD", eur, 1.1) == pytest.approx(0.5)
    assert ds.commission_pips("USDJPY", jpy, 100.0) == pytest.approx(0.5 * 100.0 / 100.0)     # $10/pip at 100.0
    assert ds.commission_pips("USDJPY", jpy, 125.0) > ds.commission_pips("USDJPY", jpy, 100.0)
