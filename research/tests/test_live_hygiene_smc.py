"""Live bot hygiene: detect_fvg_h1 must not crash on short H1 data (it used `print.info` with unbound names)."""

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from strategies.smc_strategies import detect_fvg_h1   # noqa: E402


def _bars(n):
    t = pd.date_range("2026-01-05", periods=n, freq="h", tz="UTC")
    return pd.DataFrame({"time": t, "open": 1.10, "high": 1.1010, "low": 1.0990, "close": 1.10, "volume": 1})


def test_short_h1_data_returns_none_signal_instead_of_crashing():
    out = detect_fvg_h1(_bars(5), "BUY")
    assert out["signal"] == "NONE" and out["score"] == 0 and "Insufficient" in out["reason"]


def test_ten_bars_is_enough_to_run_the_detector():
    """The 10-bar boundary is unchanged: below it 'Insufficient', at it the normal path."""
    assert "Insufficient" not in detect_fvg_h1(_bars(10), "BUY")["reason"]
