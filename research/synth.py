"""Synthetic OHLC generator. FOR TESTS OF MECHANICS ONLY - its statistics say nothing about any strategy."""

from __future__ import annotations

import numpy as np
import pandas as pd

TF_RULE = {"M5": "5min", "M15": "15min", "H1": "1h", "H4": "4h"}


def synth_bars(n_days: int = 90, seed: int = 0, start: str = "2024-01-01", price0: float = 1.10,
               step_sd: float = 0.00018, spread_pts: float = 12.0, drift: float = 0.0) -> dict:
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=n_days * 288 * 7 // 5 + 288, freq="5min", tz="UTC")
    idx = idx[idx.dayofweek < 5][: n_days * 288 * 5 // 7 + 288]
    n = len(idx)
    ret = rng.normal(drift, step_sd, n)
    close = price0 + np.cumsum(ret)
    open_ = np.concatenate([[price0], close[:-1]])
    wiggle = np.abs(rng.normal(0, step_sd * 0.8, n))
    high = np.maximum(open_, close) + wiggle
    low = np.minimum(open_, close) - np.abs(rng.normal(0, step_sd * 0.8, n))
    m5 = pd.DataFrame({"time": idx, "open": open_, "high": high, "low": low, "close": close,
                       "volume": rng.integers(50, 500, n)})
    m5["spread"] = spread_pts
    out = {"M5": m5}
    for tf in ("M15", "H1", "H4"):
        g = (m5.set_index("time").resample(TF_RULE[tf])
             .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
             .dropna().reset_index())
        g["spread"] = spread_pts
        out[tf] = g
    for df in out.values():
        df["spread_pts"] = df["spread"].astype(float)
    return out


def truncate_closed(bars: dict, cut: pd.Timestamp) -> dict:
    """Keep only bars that were fully closed at `cut` (open + timeframe <= cut)."""
    mins = {"M5": 5, "M15": 15, "H1": 60, "H4": 240}
    return {tf: df[df["time"] + pd.Timedelta(minutes=mins[tf]) <= cut].reset_index(drop=True)
            for tf, df in bars.items()}
