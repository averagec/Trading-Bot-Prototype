"""Shared fixtures for the data-depth tests (synthetic data only - nothing here touches MT5 or the real cache)."""

import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from research.synth_vendor import make_truth_m1, build_mt5_cache, write_vendor_file   # noqa: E402

# MT5 windows mimic the real cache: M5/M15 shortest, H1 longer, H4 longest. The window spans the Nov-2024 US DST change.
M5_FROM, H1_FROM, H4_FROM = "2024-09-01", "2024-08-01", "2024-07-01"


@pytest.fixture(scope="session")
def truth_m1():
    return make_truth_m1("2024-04-01", "2025-02-01", seed=7)


@pytest.fixture(scope="session")
def cache_template(tmp_path_factory, truth_m1):
    d = tmp_path_factory.mktemp("mt5cache") / "data"
    build_mt5_cache(d, truth_m1, "EURUSD", m5_from=M5_FROM, h1_from=H1_FROM, h4_from=H4_FROM)
    return d


@pytest.fixture
def cache(cache_template, tmp_path):
    """A private, mutable copy of the synthetic MT5 cache."""
    dst = tmp_path / "data"
    shutil.copytree(cache_template, dst)
    return dst


@pytest.fixture(scope="session")
def vendor_dir(tmp_path_factory, truth_m1):
    d = tmp_path_factory.mktemp("vendor")
    write_vendor_file(truth_m1, d / "duka_utc.csv", "dukascopy", "UTC")
    write_vendor_file(truth_m1, d / "hist_est.csv", "histdata", "EST")
    write_vendor_file(truth_m1, d / "mt_broker.csv", "mt", "broker")
    write_vendor_file(truth_m1, d / "gen_ny.csv", "generic", "America/New_York")     # local wall clock WITH DST
    return d


@pytest.fixture(scope="module")
def live_main(tmp_path_factory):
    """Import main.py from a scratch cwd so its FileHandler('forex_bot.log') does not touch the live log."""
    import importlib
    import os
    cwd = os.getcwd()
    os.chdir(tmp_path_factory.mktemp("livecwd_shared"))
    try:
        sys.modules.pop("main", None)
        mod = importlib.import_module("main")
    finally:
        os.chdir(cwd)
    return mod
