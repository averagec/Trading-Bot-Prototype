"""Purged, embargoed walk-forward splits on label end times.

Test block = a calendar half-year of decision times T. Training rows are those whose LABEL ENDED (exit_T) at least
`embargo_days` before the test block starts, for every symbol at once (the pairs share one USD factor, so a per-pair
split would leak). Training is expanding. Nothing after the test block is ever used for training.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def half_year_blocks(first: str = "2016-01-01", end: str = "2021-06-01") -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    edges = list(pd.date_range(first, end, freq="6MS", tz="UTC"))
    if pd.Timestamp(end, tz="UTC") > edges[-1]:
        edges.append(pd.Timestamp(end, tz="UTC"))
    return list(zip(edges[:-1], edges[1:]))


def walk_forward(T: pd.Series, exit_T: pd.Series, blocks, embargo_days: int = 7):
    """Yield (block, train_positions, test_positions) as integer position arrays."""
    T = pd.to_datetime(T, utc=True).dt.tz_convert("UTC").dt.tz_localize(None).to_numpy("datetime64[ns]")
    ex = pd.to_datetime(exit_T, utc=True).dt.tz_convert("UTC").dt.tz_localize(None).to_numpy("datetime64[ns]")
    for a, b in blocks:
        a64 = a.tz_convert("UTC").tz_localize(None).to_datetime64()
        b64 = b.tz_convert("UTC").tz_localize(None).to_datetime64()
        cut = a64 - np.timedelta64(embargo_days, "D")
        train = np.flatnonzero(ex <= cut)
        test = np.flatnonzero((T >= a64) & (T < b64))
        if len(train) and len(test):
            yield (a, b), train, test
