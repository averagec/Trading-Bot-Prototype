"""
Shared helpers for the idea generators: causal indicators, HTF alignment, bar loading/truncation,
candidate-row construction in the schema the Simulator expects, Friday flatten rule.

Convention (same as research/signals.py): cycle time T = close of an M5 bar (open + 5 min). A bar of timeframe tf may
be used at T only if open + tf <= T.  Everything here is causal.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from research.data import prepare_bars, load_specs

SPLIT = pd.Timestamp("2026-05-04", tz="UTC")          # fixed in the pre-registration
TF_MIN = {"M5": 5, "M15": 15, "H1": 60, "H4": 240}
M5 = np.timedelta64(5, "m")
FRIDAY_FLAT_HOUR = 20                                  # UTC
WARM_H4 = 250                                         # H4 bars before T needed for EMA200/ADX warm-up
WARM_H1 = 60
MIN_HORIZON_MIN = 30                                   # skip entries whose flatten time is closer than this

CAND_COLS = ["T", "symbol", "bar_idx", "direction", "probability", "checks_passed", "entry_type", "subtype", "price",
             "sl_raw", "atr_factor", "size_factor", "bid", "ask", "spread_pts", "trend_score", "trend_strong",
             "fvg_sig", "fvg_score", "qt_sig", "qt_score", "qt_phase", "sweep_sig", "sweep_score", "candle_sig",
             "candle_score", "pattern", "expire_ns"]


# ── causal indicators ─────────────────────────────────────────────────────────────

def ema(x: np.ndarray, n: int) -> np.ndarray:
    return pd.Series(np.asarray(x, float)).ewm(span=n, adjust=False).mean().to_numpy()


def _wilder(x: np.ndarray, n: int) -> np.ndarray:
    return pd.Series(np.asarray(x, float)).ewm(alpha=1.0 / n, adjust=False).mean().to_numpy()


def true_range(h, l, c) -> np.ndarray:
    h, l, c = (np.asarray(a, float) for a in (h, l, c))
    pc = np.r_[c[0], c[:-1]]
    return np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))


def atr(h, l, c, n: int = 14) -> np.ndarray:
    """Wilder ATR (recursive, causal)."""
    return _wilder(true_range(h, l, c), n)


def adx(h, l, c, n: int = 14) -> np.ndarray:
    """Wilder ADX (recursive, causal)."""
    h, l, c = (np.asarray(a, float) for a in (h, l, c))
    up = np.r_[0.0, h[1:] - h[:-1]]
    dn = np.r_[0.0, l[:-1] - l[1:]]
    pdm = np.where((up > dn) & (up > 0), up, 0.0)
    mdm = np.where((dn > up) & (dn > 0), dn, 0.0)
    a = _wilder(true_range(h, l, c), n)
    with np.errstate(divide="ignore", invalid="ignore"):
        pdi = 100 * _wilder(pdm, n) / a
        mdi = 100 * _wilder(mdm, n) / a
        dx = 100 * np.abs(pdi - mdi) / (pdi + mdi)
    dx = np.nan_to_num(dx, nan=0.0)
    return _wilder(dx, n)


# ── bars ──────────────────────────────────────────────────────────────────────────

def ns(df: pd.DataFrame) -> np.ndarray:
    return df["time"].values.astype("datetime64[ns]")


def truncate_closed(bars: dict, cut: pd.Timestamp) -> dict:
    """Keep only bars fully closed at `cut` (open + tf <= cut)."""
    return {tf: df[df["time"] + pd.Timedelta(minutes=TF_MIN[tf]) <= cut].reset_index(drop=True)
            for tf, df in bars.items()}


def load_symbol_bars(symbol: str, cut: Optional[pd.Timestamp] = None) -> dict:
    b = prepare_bars(symbol)
    return truncate_closed(b, cut) if cut is not None else b


def last_closed_idx(t_open: np.ndarray, tf: str, T: np.ndarray) -> np.ndarray:
    """Index of the last bar of timeframe tf with open + tf <= T (-1 if none)."""
    return np.searchsorted(t_open + np.timedelta64(TF_MIN[tf], "m"), T, side="right") - 1


def m5_index_closing_at(t5: np.ndarray, T: np.ndarray) -> np.ndarray:
    """Index j of the M5 bar whose close is T (open = T - 5 min); -1 if that bar is missing."""
    want = T - M5
    j = np.searchsorted(t5, want)
    j = np.minimum(j, len(t5) - 1)
    return np.where(t5[j] == want, j, -1)


# ── time rules ────────────────────────────────────────────────────────────────────

def friday_flat_ns(T: pd.Timestamp) -> int:
    """Next Friday 20:00 UTC at or after T, in ns."""
    day = T.normalize()
    ahead = (4 - day.dayofweek) % 7
    fri = day + pd.Timedelta(days=ahead, hours=FRIDAY_FLAT_HOUR)
    if fri < T:
        fri += pd.Timedelta(days=7)
    return int(fri.value)


def cap_expiry(T: pd.Timestamp, expire: pd.Timestamp) -> Optional[int]:
    """Apply the Friday-flat cap; None if the horizon would be shorter than MIN_HORIZON_MIN."""
    e = min(int(expire.value), friday_flat_ns(T))
    if e - T.value < MIN_HORIZON_MIN * 60 * 1_000_000_000:
        return None
    return e


# ── candidate rows ────────────────────────────────────────────────────────────────

def make_row(symbol: str, arm: str, k: int, t5: np.ndarray, close5: np.ndarray, spread5: np.ndarray, point: float,
             direction: str, sl_dist: Optional[float] = None, sl_level: Optional[float] = None,
             expire: Optional[pd.Timestamp] = None, checks: int = 1, tag: str = "") -> Optional[dict]:
    """
    Candidate row for a MARKET entry at the close of M5 bar k.  Provide either sl_dist (distance from entry) or an
    absolute sl_level.  Returns None if the rule's horizon is too short or the stop is on the wrong side.
    """
    T = pd.Timestamp(t5[k] + M5, tz="UTC")
    e = cap_expiry(T, expire)
    if e is None:
        return None
    bid = float(close5[k]); spr = float(spread5[k]); ask = bid + spr * point
    price = ask if direction == "BUY" else bid
    if sl_level is None:
        sl_level = price - sl_dist if direction == "BUY" else price + sl_dist
    if (direction == "BUY" and sl_level >= price) or (direction == "SELL" and sl_level <= price):
        return None
    return {"T": T, "symbol": symbol, "bar_idx": int(k), "direction": direction, "probability": 100.0,
            "checks_passed": checks, "entry_type": "MARKET", "subtype": "MARKET", "price": float(price),
            "sl_raw": float(sl_level), "atr_factor": 1.0, "size_factor": 1.0, "bid": bid, "ask": float(ask),
            "spread_pts": spr, "trend_score": 0, "trend_strong": False, "fvg_sig": "NONE", "fvg_score": 0,
            "qt_sig": "NONE", "qt_score": 0, "qt_phase": "NA", "sweep_sig": "NONE", "sweep_score": 0,
            "candle_sig": "NONE", "candle_score": 0, "pattern": f"{arm}:{tag}" if tag else arm,
            "expire_ns": int(e)}


def to_frame(rows: list) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=CAND_COLS)
    df = pd.DataFrame(rows)[CAND_COLS].sort_values(["T", "symbol"]).reset_index(drop=True)
    return df


def enforce_gap(rows: list, hours: float) -> list:
    """Keep a candidate only if >= `hours` after the previously KEPT candidate (rows in time order, one symbol)."""
    out, last = [], None
    for r in sorted(rows, key=lambda r: r["T"]):
        if last is None or (r["T"] - last) >= pd.Timedelta(hours=hours):
            out.append(r); last = r["T"]
    return out


def source_fingerprint(*paths: Path) -> str:
    h = hashlib.md5()
    for p in paths:
        h.update(Path(p).read_bytes())
    return h.hexdigest()[:10]
