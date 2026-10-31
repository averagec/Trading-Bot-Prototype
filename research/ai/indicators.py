"""Causal indicator helpers (every value at row i uses rows <= i only)."""
from __future__ import annotations

import numpy as np
import pandas as pd


def true_range(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> np.ndarray:
    """True range; the first bar uses high-low only."""
    h, l, c = (np.asarray(a, float) for a in (high, low, close))
    pc = np.concatenate([[np.nan], c[:-1]])
    return np.fmax(h - l, np.fmax(np.abs(h - pc), np.abs(l - pc)))


def atr(high, low, close, n: int = 14) -> np.ndarray:
    """Simple-mean ATR over the last n bars INCLUDING the bar just closed (NaN until n bars exist)."""
    tr = true_range(high, low, close)
    tr[0] = np.asarray(high, float)[0] - np.asarray(low, float)[0]
    return pd.Series(tr).rolling(n, min_periods=n).mean().to_numpy()
