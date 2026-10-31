"""research/cost_record.py: spread profile by period and session, and cost per R from a trade table. Synthetic data only."""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from research import cost_record as cr   # noqa: E402


def bars(rows):
    return pd.DataFrame({"time": pd.to_datetime([r[0] for r in rows], utc=True), "spread": [r[1] for r in rows]})


def test_median_excludes_zero_spreads_and_zero_share_counts_them():
    b = bars([("2015-03-02 08:00", 10), ("2015-03-02 08:05", 0), ("2015-03-02 08:10", 20), ("2015-03-02 08:15", 0)])
    t = cr.spread_profile(b, point=0.00001, periods={"p": ("2014-01-01", "2017-01-01")})
    row = t[(t["period"] == "p") & (t["session"] == "London 07-13")].iloc[0]
    assert row["median_nonzero_pts"] == pytest.approx(15.0)       # median of 10 and 20, the zeros are not data
    assert row["zero_share_pct"] == pytest.approx(50.0)
    assert row["median_nonzero_price"] == pytest.approx(15.0 * 0.00001)
    assert row["bars"] == 4


def test_sessions_and_periods_split_the_bars():
    b = bars([("2015-03-02 03:00", 8), ("2015-03-02 08:00", 12), ("2015-03-02 15:00", 14), ("2015-03-02 21:30", 40),
              ("2019-03-04 08:00", 6), ("2030-01-01 08:00", 99)])
    t = cr.spread_profile(b, point=1.0, periods={"early": ("2014-01-01", "2017-01-01"), "mid": ("2017-01-01", "2021-01-01")})
    got = {(r["period"], r["session"]): r["median_nonzero_pts"] for _, r in t.iterrows()}
    assert got[("early", "Asia 00-07")] == 8 and got[("early", "London 07-13")] == 12
    assert got[("early", "NY 13-21")] == 14 and got[("early", "Rollover 21-24")] == 40
    assert got[("mid", "London 07-13")] == 6
    assert t["bars"].sum() == 5                                    # the 2030 bar is in no period


def test_cost_per_r_uses_commission_over_risk_and_spread_over_stop():
    trades = pd.DataFrame({"symbol": ["EURUSD"] * 4, "entry": [1.1000] * 4, "sl0": [1.0980, 1.0990, 1.1020, 1.1010],
                           "spread_pts": [10.0, 10.0, 20.0, 10.0], "commission": [5.0, 5.0, 5.0, 5.0],
                           "risk_usd": [250.0, 250.0, 100.0, 100.0], "exit_reason": ["SL", "TP", "SL", "END"]})
    t = cr.cost_per_r(trades, {"EURUSD": {"point": 0.00001}})
    row = t.iloc[0]
    assert row["trades"] == 3                                      # END trades are not completed trades
    assert row["median_stop_price"] == pytest.approx(0.0020)       # stops: 0.0020, 0.0010, 0.0020
    assert row["median_spread_price"] == pytest.approx(10 * 0.00001)
    assert row["spread_to_stop_pct"] == pytest.approx(0.0001 / 0.0020 * 100)
    assert row["commission_r_mean"] == pytest.approx(np.mean([5 / 250, 5 / 250, 5 / 100]))
