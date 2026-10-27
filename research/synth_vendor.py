"""
Synthetic stand-ins for third-party data, for TESTING THE IMPORT PIPELINE ONLY (their statistics mean nothing).

    make_truth_m1()      a random-walk M1 series on a realistic FX calendar (broker week: Mon-Fri on the NY-close clock)
    build_mt5_cache()    write MT5-style parquet caches (M5/M15/H1/H4 with per-bar spread) derived from that truth
    write_vendor_file()  export M1 bars in Dukascopy / HistData / MetaTrader-CSV / generic style, in a chosen timezone,
                         with a small independent price noise (two feeds never agree to the tick)
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from research.data import utc_to_server, server_to_utc, save_bars, normalize_bars, cache_path
from research.data_import import resample_bars


def make_truth_m1(start: str = "2024-04-01", end: str = "2025-02-01", seed: int = 7, price0: float = 1.10,
                  sigma: float = 1.2e-4, drop_frac: float = 0.003, point: float = 1e-5) -> pd.DataFrame:
    """UTC M1 bars. Market open <=> broker weekday is Mon-Fri (opens Sun 21:00/22:00 UTC, closes Fri 21:00/22:00 UTC)."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, end, freq="1min", tz="UTC", inclusive="left")
    srv = utc_to_server(pd.Series(idx))
    open_ = (srv.dt.dayofweek < 5).to_numpy()
    idx = idx[open_]
    idx = idx[rng.random(len(idx)) > drop_frac]                       # minutes without a tick
    n = len(idx)
    hour = idx.hour.to_numpy()
    vol_profile = 0.6 + 0.8 * np.exp(-((hour - 13.5) / 5.0) ** 2)     # busier London/NY
    ret = rng.normal(0, sigma, n) * vol_profile
    close = price0 + np.cumsum(ret)
    open_p = np.r_[price0, close[:-1]]
    hi = np.maximum(open_p, close) + np.abs(rng.normal(0, sigma * 0.5, n))
    lo = np.minimum(open_p, close) - np.abs(rng.normal(0, sigma * 0.5, n))
    r = lambda x: np.round(x / point) * point
    return pd.DataFrame({"time": idx, "open": r(open_p), "high": r(hi), "low": r(lo), "close": r(close),
                         "volume": rng.integers(5, 120, n).astype("float64")})


def build_mt5_cache(data_dir: Path, truth: pd.DataFrame, symbol: str = "EURUSD", m5_from: Optional[str] = None,
                    h1_from: Optional[str] = None, h4_from: Optional[str] = None, point: float = 1e-5,
                    spread_pts: int = 12, zero_spread_frac: float = 0.0, seed: int = 1) -> None:
    """Write <symbol>_{M5,M15,H1,H4}.parquet + specs.json built from the truth. Windows mimic the real cache:
    M5/M15 shortest, H1 longer, H4 longest."""
    import json
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    start = {"M5": m5_from, "M15": m5_from, "H1": h1_from or m5_from, "H4": h4_from or h1_from or m5_from}
    for tf in ("M5", "M15", "H1", "H4"):
        bars = resample_bars(truth, tf)
        if start[tf]:
            bars = bars[bars["time"] >= pd.Timestamp(start[tf], tz="UTC")]
        bars = bars.copy()
        sp = spread_pts + rng.integers(-3, 4, len(bars)) + (bars["time"].dt.hour.to_numpy() >= 21) * 8
        if zero_spread_frac:
            sp = np.where(rng.random(len(bars)) < zero_spread_frac, 0, sp)
        bars["spread"] = sp
        save_bars(normalize_bars(bars), cache_path(symbol, tf, data_dir))
    (data_dir / "specs.json").write_text(json.dumps({symbol: {"digits": 5, "point": point}}))


def write_vendor_file(truth: pd.DataFrame, path: Path, style: str = "dukascopy", tz: str = "UTC",
                      noise: float = 1.5e-5, seed: int = 11, side_shift: float = 0.0, point: float = 1e-5,
                      scale: float = 1.0) -> Path:
    """
    Export `truth` as a vendor-like file. `tz` is how timestamps are WRITTEN: UTC | broker | fixed:-5 | EST | an IANA zone
    ('America/New_York' writes local wall-clock WITH daylight saving). `side_shift` adds a constant to every price
    (e.g. +0.00008 turns bid into a fake 'ask file'); `scale` multiplies prices.
    """
    rng = np.random.default_rng(seed)
    df = truth.copy()
    e = rng.normal(0, noise, len(df))
    for c in ("open", "high", "low", "close"):
        df[c] = np.round((df[c] * scale + e + side_shift) / point) * point
    df["high"] = df[["open", "high", "low", "close"]].max(axis=1)
    df["low"] = df[["open", "high", "low", "close"]].min(axis=1)
    t = df["time"]
    tzl = tz.strip().lower()
    if tzl in ("utc", "gmt"):
        local = t.dt.tz_convert(None)
    elif tzl in ("broker", "mt5"):
        local = utc_to_server(t)
    elif tzl == "est" or tzl.startswith("fixed:"):
        h = -5 if tzl == "est" else int(tzl.split(":")[1])
        local = t.dt.tz_convert(None) + pd.Timedelta(hours=h)
    else:
        local = t.dt.tz_convert(tz).dt.tz_localize(None)
    path = Path(path)
    if style == "dukascopy":
        out = pd.DataFrame({"Gmt time": local.dt.strftime("%d.%m.%Y %H:%M:%S.000"), "Open": df["open"], "High": df["high"],
                            "Low": df["low"], "Close": df["close"], "Volume": df["volume"]})
        out.to_csv(path, index=False)
    elif style == "histdata":
        out = pd.DataFrame({"t": local.dt.strftime("%Y%m%d %H%M%S"), "o": df["open"], "h": df["high"], "l": df["low"],
                            "c": df["close"], "v": 0})
        out.to_csv(path, index=False, header=False, sep=";")
    elif style == "mt":
        out = pd.DataFrame({"d": local.dt.strftime("%Y.%m.%d"), "t": local.dt.strftime("%H:%M"), "o": df["open"],
                            "h": df["high"], "l": df["low"], "c": df["close"], "v": df["volume"].astype(int)})
        out.to_csv(path, index=False, header=False)
    elif style == "generic":
        out = pd.DataFrame({"time": local.dt.strftime("%Y-%m-%d %H:%M:%S"), "open": df["open"], "high": df["high"],
                            "low": df["low"], "close": df["close"], "volume": df["volume"]})
        out.to_csv(path, index=False)
    else:
        raise ValueError(style)
    return path
