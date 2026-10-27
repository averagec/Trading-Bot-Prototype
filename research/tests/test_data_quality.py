"""Data-quality report: detects planted defects, stays quiet on clean data, and handles broker-aligned H4."""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from research.data_quality import quality_report, format_quality, summary_line, quality_table, _classify_gap   # noqa: E402
from research.data import DATA_DIR, normalize_bars                                                        # noqa: E402
from research.data_import import resample_bars                                                            # noqa: E402


@pytest.fixture(scope="module")
def clean_m5(truth_m1):
    m5 = resample_bars(truth_m1, "M5")
    m5 = m5[m5["time"] >= "2024-09-01"].copy()
    m5["spread"] = 12
    return normalize_bars(m5).reset_index(drop=True)


def _sev(rep, word):
    return [m for s, m in rep["issues"] if word in m]


def test_clean_series_has_no_failures(clean_m5):
    rep = quality_report(clean_m5, "M5", "EURUSD")
    assert rep["ok"], format_quality(rep)
    m = rep["metrics"]
    assert m["duplicates"] == 0 and m["misaligned"] == 0 and m["weekend_bars_saturday"] == 0
    assert m["weekly_open_utc_hours"] and set(m["weekly_open_utc_hours"]) <= {21, 22}
    assert m["gaps_weekend"] >= 20 and m["spread_zero_pct"] == 0.0
    assert "EURUSD M5" in summary_line(rep)


def test_planted_defects_are_found(clean_m5):
    df = clean_m5.copy()
    dup = df.iloc[[100]].copy()                                                 # duplicate timestamp
    sat = df.iloc[[500]].copy(); sat["time"] = pd.Timestamp("2024-09-07 12:00", tz="UTC")   # Saturday bar
    off = df.iloc[[900]].copy(); off["time"] = off["time"] + pd.Timedelta(minutes=2)       # off-grid bar
    bad = pd.concat([df, dup, sat, off], ignore_index=True).sort_values("time", kind="stable").reset_index(drop=True)
    # a 3h mid-week silence
    t0 = pd.Timestamp("2024-10-16 08:00", tz="UTC")
    bad = bad[~((bad["time"] >= t0) & (bad["time"] < t0 + pd.Timedelta(hours=3)))].reset_index(drop=True)
    # reverting single-bar spike (bad tick)
    i = int(bad.index[bad["time"] == pd.Timestamp("2024-11-12 10:00", tz="UTC")][0])
    bad.loc[i, ["close", "high"]] = bad.loc[i - 1, "close"] + 0.02
    # frozen feed: 20 flat bars
    j = int(bad.index[bad["time"] == pd.Timestamp("2024-12-10 10:00", tz="UTC")][0])
    bad.loc[j:j + 19, ["open", "high", "low", "close"]] = bad.loc[j, "open"]
    # recorded zero spreads
    bad.loc[::2, "spread"] = 0
    rep = quality_report(bad, "M5", "EURUSD")
    m = rep["metrics"]
    assert not rep["ok"]
    assert m["duplicates"] == 1 and _sev(rep, "duplicate")
    assert m["weekend_bars_saturday"] == 1 and _sev(rep, "Saturday")
    assert m["misaligned"] >= 1 and _sev(rep, "grid")
    assert m["gaps_unexplained_ge_1h"] >= 1 and _sev(rep, "unexplained gaps")
    assert m["spikes_reverting"] >= 1
    assert m["flat_runs_ge_12"] == 1
    assert m["spread_zero_pct"] > 45 and _sev(rep, "spreads are 0")
    assert any("2024-10-16" in g[0] for g in rep["details"]["largest_unexplained_gaps"])


def test_wrong_clock_shows_up_as_weekend_misalignment(clean_m5):
    shifted = clean_m5.copy()
    shifted["time"] = shifted["time"] + pd.Timedelta(hours=5)                   # e.g. an EST file read as UTC
    rep = quality_report(shifted, "M5", "EURUSD")
    assert not rep["ok"]
    assert rep["metrics"]["weekend_bars_saturday"] > 0 or _sev(rep, "weekly open")


def test_h4_is_checked_on_the_broker_clock(truth_m1):
    h4 = normalize_bars(resample_bars(truth_m1, "H4").assign(spread=10))
    assert set(h4["time"].dt.hour) == {1, 5, 9, 13, 17, 21} | {2, 6, 10, 14, 18, 22}   # DST moves the UTC hour
    rep = quality_report(h4, "H4", "EURUSD")
    assert rep["metrics"]["misaligned"] == 0, format_quality(rep)


def test_synthetic_spread_is_excluded_from_zero_share_and_reported(clean_m5):
    df = clean_m5.copy()
    df["src"] = "mt5"; df["spread_synth"] = False
    df.loc[df.index < 1000, ["src", "spread_synth", "spread"]] = ["import:x", True, 0]
    rep = quality_report(df, "M5", "EURUSD")
    assert rep["metrics"]["spread_zero_pct"] == 0.0                              # synthetic rows do not count as "recorded zeros"
    assert rep["metrics"]["spread_synthetic_pct"] > 1 and rep["metrics"]["imported_pct"] > 1


@pytest.mark.parametrize("a,b,kind", [
    ("2025-01-10 21:55", "2025-01-12 22:00", "weekend"),
    ("2025-12-24 20:00", "2025-12-26 07:00", "holiday"),
    ("2026-04-02 20:45", "2026-04-05 22:05", "holiday"),                          # Good Friday
    ("2025-11-27 19:25", "2025-11-27 23:05", "session"),                          # US-holiday early close
    ("2025-03-04 10:00", "2025-03-04 13:00", "unexplained"),
])
def test_gap_classifier(a, b, kind):
    assert _classify_gap(pd.Timestamp(a, tz="UTC"), pd.Timestamp(b, tz="UTC")) == kind


@pytest.mark.skipif(not (DATA_DIR / "EURUSD_M5.parquet").exists(), reason="real cache not present")
def test_real_cache_report_runs_and_is_consistent():
    """The cache also holds old H1/H4 history (before ~2020: Saturday bars, shifted broker clock, zero spreads) that the
    quality report rightly fails. The backtest loads it only as warm-up and signal windows (<= 500 bars, ~83 days of
    H4) cannot reach it, so check from 2020-06-01, which covers that warm-up."""
    since = "2020-06-01"
    tbl = quality_table(["EURUSD", "XAUUSD"], ["M5", "H4"], since=since)
    assert len(tbl) == 4 and (tbl["dups"] == 0).all() and (tbl["sat_bars"].fillna(0) == 0).all()
    assert (tbl["first"] >= since).all()


def test_quality_table_since_restricts_the_window(clean_m5, tmp_path):
    clean_m5.to_parquet(tmp_path / "TEST_M5.parquet")
    everything = quality_table(["TEST"], ["M5"], data_dir=tmp_path)
    cut = quality_table(["TEST"], ["M5"], data_dir=tmp_path, since="2025-01-01")
    assert everything["first"].iloc[0] < "2025-01-01" <= cut["first"].iloc[0]
    assert 0 < cut["bars"].iloc[0] < everything["bars"].iloc[0]
