"""DEV-only data access for the AI study.

Hard guard: nothing at or after DEV_END (2021-06-01) is ever loaded; the parquet read is filtered at the file level,
so FINAL bars are not even brought into memory. Spread imputation mirrors research.data.prepare_bars (same-UTC-hour
non-zero median over the loaded window); it is used ONLY for costs in labels, never as a feature.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from research.data import DATA_DIR, SENTINEL_MONTH_SHARE, SENTINEL_PTS, sentinel_mask  # noqa: F401  (re-exported)

DEV_START = "2014-01-01"
DEV_END = "2021-06-01"
FX5 = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD"]
TF_MIN = {"M5": 5, "M15": 15, "H1": 60, "H4": 240}
# +1: pair return = foreign-currency strength vs USD (EURUSD up = EUR up); -1: USD is the base (USDJPY up = USD up)
USD_SIGN = {"EURUSD": 1.0, "GBPUSD": 1.0, "AUDUSD": 1.0, "USDJPY": -1.0, "USDCAD": -1.0}


def check_end(end) -> pd.Timestamp:
    """Return `end` as a UTC timestamp; refuse anything after DEV_END (FINAL must not be read)."""
    if end is None:
        raise ValueError("an explicit end <= 2021-06-01 is required (FINAL must not be read)")
    e = pd.Timestamp(end)
    e = e.tz_localize("UTC") if e.tzinfo is None else e.tz_convert("UTC")
    if e > pd.Timestamp(DEV_END, tz="UTC"):
        raise ValueError(f"end {e} is after DEV_END {DEV_END}: FINAL and later data are off limits")
    return e


def impute_spread(df: pd.DataFrame) -> pd.DataFrame:
    """Add spread_pts (zero recorded spreads replaced by the same-UTC-hour non-zero median) and spread_was_zero."""
    sp = df["spread"].astype(float)
    nz = sp.where(sp > 0)
    by_hour = nz.groupby(df["time"].dt.hour).transform("median")
    out = df.copy()
    out["spread_pts"] = sp.where(sp > 0, by_hour).fillna(nz.median()).fillna(0.0)
    out["spread_was_zero"] = (sp <= 0).to_numpy()
    return out


SENTINEL_FROM = "2018-01-01"      # before this, a constant 50 is a NOMINAL spread (EURUSD 2000-2003), not the placeholder


def sentinel_mask_era(df: pd.DataFrame, symbol: str) -> np.ndarray:
    """research.data.sentinel_mask restricted to bars from SENTINEL_FROM. The month-share rule alone would flag the
    constant 50-point spread of 2000-2003 (every bar equals 50) as missing; those are nominal recorded values and are kept."""
    m = np.asarray(sentinel_mask(df, symbol), bool)
    return m & (df["time"] >= pd.Timestamp(SENTINEL_FROM, tz="UTC")).to_numpy()


def add_clean_spread(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Add `spread_rec` (recorded spread, NaN where zero or sentinel: usable as a causal feature input) and
    `spread_clean_pts` (the same with gaps filled by the median of clean recorded spreads of the same calendar year and
    UTC hour, falling back to the hour median over the window). The fill uses same-year data, so it is for COSTS IN
    LABELS only, never for a feature."""
    out = df.copy()
    sp = out["spread"].astype(float)
    sent = sentinel_mask_era(out, symbol)
    bad = (sp <= 0) | sent
    rec = sp.where(~bad)
    out["spread_rec"] = rec
    out["spread_sentinel"] = sent
    yr = out["time"].dt.year
    hr = out["time"].dt.hour
    med_yh = rec.groupby([yr, hr]).transform("median")
    med_h = rec.groupby(hr).transform("median")
    out["spread_clean_pts"] = rec.fillna(med_yh).fillna(med_h).fillna(rec.median()).fillna(0.0)
    return out


def load_bars(symbol: str, tf: str, start: str | None = None, end: str = DEV_END,
              data_dir: Path = DATA_DIR) -> pd.DataFrame:
    """Bars with time (bar OPEN, UTC) in [start, end). `end` is capped at DEV_END.
    Adds spread_pts (simulator-faithful imputation), spread_rec / spread_clean_pts / spread_sentinel (see above)."""
    e = check_end(end)
    p = Path(data_dir) / f"{symbol}_{tf}.parquet"
    filt = [("time", "<", e)]
    if start:
        filt.append(("time", ">=", pd.Timestamp(start, tz="UTC")))
    df = pd.read_parquet(p, filters=filt)
    df = df.sort_values("time").reset_index(drop=True)
    return add_clean_spread(impute_spread(df), symbol)


def load_specs(data_dir: Path = DATA_DIR) -> dict:
    return json.loads((Path(data_dir) / "specs.json").read_text())


def pip_size(spec: dict) -> float:
    return spec["point"] * (10 if spec["digits"] in (3, 5) else 1)


def commission_pips(symbol: str, spec: dict, price: float, comm_per_lot_side: float = 2.5) -> float:
    """Round-trip commission expressed in pips at `price` (USD-quoted: $10 per pip per lot for 5-digit majors)."""
    pip_value = pip_size(spec) * spec["contract_size"]
    if symbol.startswith("USD") and spec["currency_profit"] != "USD":
        pip_value = pip_value / price
    return 2 * comm_per_lot_side / pip_value
