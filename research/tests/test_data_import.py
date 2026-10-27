"""
Third-party importer: parsing + timezone handling, broker-boundary resampling, validation against the (synthetic) MT5
window, refusal on disagreement, and the guarantees of the merge (MT5 never overwritten, flagged columns, idempotent).
All data is synthetic and generated here - nothing is downloaded and the real cache is not modified.
"""

import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from research.data import DATA_DIR, prepare_bars, normalize_bars                       # noqa: E402
from research.data_import import (read_external, sniff_format, localize_to_utc, parse_fixed_offset, resample_bars,   # noqa: E402
                                  import_external, ImportRefused, Tolerances, validate_bars, pair_bid_ask, to_bid,
                                  infer_tf)
from research.data_quality import quality_report                                         # noqa: E402
from research.synth_vendor import write_vendor_file                                       # noqa: E402


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _cache_hashes(d: Path) -> dict:
    return {p.name: _sha(p) for p in sorted(d.glob("*.parquet"))}


# ── parsing / timezones ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("fname,fmt,tz", [("duka_utc.csv", "dukascopy", "UTC"), ("hist_est.csv", "histdata", "EST"),
                                          ("mt_broker.csv", "mt", "broker"), ("gen_ny.csv", "generic", "America/New_York")])
def test_every_format_and_timezone_recovers_the_true_utc_clock(vendor_dir, truth_m1, fname, fmt, tz):
    p = vendor_dir / fname
    assert sniff_format(p) == fmt
    df = read_external(p, tz=tz)
    assert df["time"].dt.tz is not None and str(df["time"].dt.tz) == "UTC"
    assert len(df) == len(truth_m1) and (df["time"].to_numpy() == truth_m1["time"].to_numpy()).all()
    assert np.abs(df["close"].to_numpy() - truth_m1["close"].to_numpy()).max() < 1e-4
    assert infer_tf(df["time"]) == "M1"


def test_wrong_timezone_declaration_moves_the_clock(vendor_dir, truth_m1):
    df = read_external(vendor_dir / "hist_est.csv", tz="UTC")                   # EST file read as UTC: 5 h too early
    assert (truth_m1["time"].to_numpy() - df["time"].to_numpy() == np.timedelta64(5, "h")).all()


def test_fixed_offsets_and_named_zones():
    assert parse_fixed_offset("+2") == pd.Timedelta(hours=2)
    assert parse_fixed_offset("fixed:-5") == pd.Timedelta(hours=-5)
    assert parse_fixed_offset("UTC+05:30") == pd.Timedelta(hours=5, minutes=30)
    assert parse_fixed_offset("EST") == pd.Timedelta(hours=-5)
    assert parse_fixed_offset("America/New_York") is None
    s = pd.Series(pd.to_datetime(["2025-01-15 12:00", "2025-07-15 12:00"]))
    ny, dropped = localize_to_utc(s, "America/New_York")
    assert dropped == 0 and list(ny.dt.hour) == [17, 16]                         # DST-aware
    fixed, _ = localize_to_utc(s, "-5")
    assert list(fixed.dt.hour) == [17, 17]                                       # fixed offset ignores DST
    brk, _ = localize_to_utc(s, "broker")
    assert list(brk.dt.hour) == [10, 9]                                          # UTC+2 winter / UTC+3 summer
    with pytest.raises(ValueError):
        localize_to_utc(s, "Mars/Olympus_Mons")


def test_dukascopy_local_time_with_gmt_suffix_is_converted_per_row(tmp_path):
    p = tmp_path / "local.csv"
    p.write_text("Local time,Open,High,Low,Close,Volume\n"
                 "15.01.2025 12:00:00.000 GMT+0100,1.1,1.2,1.0,1.15,10\n"
                 "15.07.2025 12:00:00.000 GMT+0200,1.1,1.2,1.0,1.15,10\n")
    df = read_external(p)
    assert list(df["time"]) == [pd.Timestamp("2025-01-15 11:00", tz="UTC"), pd.Timestamp("2025-07-15 10:00", tz="UTC")]


def test_gmt_labelled_header_refuses_contradicting_tz(tmp_path):
    p = tmp_path / "g.csv"
    p.write_text("Gmt time,Open,High,Low,Close,Volume\n15.01.2025 12:00:00.000,1.1,1.2,1.0,1.15,10\n")
    with pytest.raises(ValueError, match="Gmt time"):
        read_external(p, tz="EST")


def test_epoch_parquet_and_dedup(tmp_path):
    t = pd.date_range("2025-01-06", periods=5, freq="1min", tz="UTC")
    df = pd.DataFrame({"timestamp": (t.astype("int64") // 10**9).tolist() + [int(t[-1].timestamp())],
                       "open": 1.1, "high": 1.2, "low": 1.0, "close": 1.15, "volume": 3})
    p = tmp_path / "x.parquet"; df.to_parquet(p)
    out = read_external(p)
    assert len(out) == 5 and out["time"].iloc[0] == t[0]


# ── bid / ask ──────────────────────────────────────────────────────────────────────

def test_ask_and_mid_to_bid_conversion_and_pairing():
    ask = pd.DataFrame({"time": pd.date_range("2025-01-06", periods=3, freq="1min", tz="UTC"),
                        "open": 1.1002, "high": 1.1004, "low": 1.1000, "close": 1.1002, "volume": 1.0})
    bid = ask.assign(open=1.1000, high=1.1002, low=1.0998, close=1.1000)
    assert np.allclose(to_bid(ask, "ask", 0.0002)["close"], 1.1000)
    assert np.allclose(to_bid(ask.assign(close=1.1001), "mid", 0.0002)["close"], 1.1000)
    with pytest.raises(ValueError):
        to_bid(ask, "ask", None)
    paired = pair_bid_ask(bid, ask, 1e-5)
    assert (paired["spread"] == 20).all() and np.allclose(paired["close"], 1.1000)
    with pytest.raises(ValueError):
        pair_bid_ask(bid, ask.assign(time=ask["time"] + pd.Timedelta(days=3)), 1e-5)


# ── resampling on broker boundaries ────────────────────────────────────────────────

def test_resample_matches_truth_and_uses_broker_h4_boundaries(truth_m1):
    h4 = resample_bars(truth_m1, "H4")
    summer = h4[(h4["time"] >= "2024-07-01") & (h4["time"] < "2024-09-01")]
    winter = h4[(h4["time"] >= "2024-12-01") & (h4["time"] < "2025-01-15")]
    assert set(summer["time"].dt.hour) == {1, 5, 9, 13, 17, 21}                  # server 00,04,...: UTC+3
    assert set(winter["time"].dt.hour) == {2, 6, 10, 14, 18, 22}                 # UTC+2
    m5 = resample_bars(truth_m1, "M5")
    chunk = truth_m1[(truth_m1["time"] >= "2024-10-02 10:00") & (truth_m1["time"] < "2024-10-02 10:05")]
    row = m5[m5["time"] == pd.Timestamp("2024-10-02 10:00", tz="UTC")].iloc[0]
    assert row["open"] == chunk["open"].iloc[0] and row["close"] == chunk["close"].iloc[-1]
    assert row["high"] == chunk["high"].max() and row["low"] == chunk["low"].min()
    assert row["volume"] == chunk["volume"].sum()


def test_resample_drops_partial_edge_buckets(truth_m1):
    cut = truth_m1[(truth_m1["time"] >= "2024-10-02 10:17") & (truth_m1["time"] < "2024-10-02 13:42")]
    h1 = resample_bars(cut, "H1")
    assert h1["time"].iloc[0] == pd.Timestamp("2024-10-02 11:00", tz="UTC")
    assert h1["time"].iloc[-1] == pd.Timestamp("2024-10-02 12:00", tz="UTC")
    kept = resample_bars(cut, "H1", drop_partial_edges=False)
    assert len(kept) == len(h1) + 2


@pytest.mark.skipif(not (DATA_DIR / "EURUSD_M5.parquet").exists(), reason="real cache not present")
@pytest.mark.parametrize("tf", ["M15", "H1", "H4"])
def test_resampler_reproduces_real_mt5_higher_timeframes_exactly(tf):
    """Strongest check available: MT5's own H1/H4 bars == our resample of MT5's own M5 bars (broker-clock buckets)."""
    m5 = pd.read_parquet(DATA_DIR / "EURUSD_M5.parquet")
    ref = pd.read_parquet(DATA_DIR / f"EURUSD_{tf}.parquet").set_index("time")
    mine = resample_bars(m5, tf).set_index("time")
    common = mine.index.intersection(ref.index)
    assert len(common) > 1000 and len(mine.index.difference(ref.index)) == 0
    diff = (mine.loc[common, ["open", "high", "low", "close"]] - ref.loc[common, ["open", "high", "low", "close"]]).abs().max().max()
    assert diff < 1e-9


# ── validation: accept good data ───────────────────────────────────────────────────

@pytest.mark.parametrize("fname,tz", [("duka_utc.csv", "UTC"), ("hist_est.csv", "EST"), ("mt_broker.csv", "broker"),
                                      ("gen_ny.csv", "America/New_York")])
def test_correctly_declared_files_are_accepted_across_dst(cache, vendor_dir, fname, tz):
    rep = import_external("EURUSD", vendor_dir / fname, tz=tz, data_dir=cache, commit=False)
    assert rep.ok, rep.text()
    m5 = rep.tf["M5"]["validation"]
    names = {c.name: c for c in m5.checks}
    assert m5.enough_overlap and names["time_shift"].status == "pass"
    assert names["season_summer_dst"].status == "pass" and names["season_winter"].status == "pass"
    assert rep.tf["H4"]["validation"].enough_overlap and rep.tf["H4"]["validation"].ok     # older H4 window validated too
    assert not rep.committed


def test_dry_run_writes_nothing(cache, vendor_dir):
    before = _cache_hashes(cache)
    import_external("EURUSD", vendor_dir / "duka_utc.csv", tz="UTC", data_dir=cache, commit=False)
    assert _cache_hashes(cache) == before and not (cache / "import_log.json").exists()


# ── validation: refuse bad data ────────────────────────────────────────────────────

def _assert_refused(cache, path, tz, expect, **kw):
    before = _cache_hashes(cache)
    rep = import_external("EURUSD", path, tz=tz, data_dir=cache, commit=False, **kw)
    assert not rep.ok, rep.text()
    assert any(expect in r for r in rep.reasons), rep.reasons
    with pytest.raises(ImportRefused) as ei:                                      # committing a refused file must raise...
        import_external("EURUSD", path, tz=tz, data_dir=cache, commit=True, **kw)
    assert not ei.value.report.ok
    assert _cache_hashes(cache) == before                                         # ...and write nothing
    return rep


def test_refuses_wrong_timezone_and_names_the_shift(cache, vendor_dir):
    rep = _assert_refused(cache, vendor_dir / "hist_est.csv", "UTC", "time_shift")
    text = " ".join(rep.reasons)
    assert "+5h" in text                                                          # tells you how far off the clock is


def test_refuses_dst_mishandling(cache, vendor_dir):
    # file carries New York local time WITH daylight saving, but is declared as fixed EST (no DST): wrong in summer only
    rep = _assert_refused(cache, vendor_dir / "gen_ny.csv", "EST", "validation")
    assert any("season_summer_dst" in r or "time_shift" in r or "median_close_diff" in r for r in rep.reasons)


def test_refuses_ask_prices_labelled_as_bid(cache, truth_m1, tmp_path):
    p = write_vendor_file(truth_m1, tmp_path / "ask.csv", "dukascopy", "UTC", side_shift=9e-5)    # ~0.9 pip above bid
    _assert_refused(cache, p, "UTC", "bias")


def test_ask_file_is_accepted_when_declared_and_converted(cache, truth_m1, tmp_path):
    p = write_vendor_file(truth_m1, tmp_path / "ask.csv", "dukascopy", "UTC", side_shift=12e-5)
    rep = import_external("EURUSD", p, tz="UTC", price_side="ask", spread_fixed_pts=12, data_dir=cache, commit=True)
    assert rep.ok and rep.committed, rep.text()
    assert any("converted to bid" in n for n in rep.notes)


def test_refuses_wrong_price_scale(cache, truth_m1, tmp_path):
    p = write_vendor_file(truth_m1, tmp_path / "scaled.csv", "dukascopy", "UTC", scale=1.01)
    _assert_refused(cache, p, "UTC", "scale")


def test_refuses_noisy_feed_that_disagrees_with_mt5(cache, truth_m1, tmp_path):
    p = write_vendor_file(truth_m1, tmp_path / "noisy.csv", "dukascopy", "UTC", noise=2.5e-4)
    rep = import_external("EURUSD", p, tz="UTC", data_dir=cache)
    assert not rep.ok and any("return_corr" in r or "median_close_diff" in r for r in rep.reasons), rep.text()


def test_refuses_file_with_no_overlap_with_mt5(cache, truth_m1, tmp_path):
    old = truth_m1[truth_m1["time"] < "2024-08-20"]
    p = write_vendor_file(old, tmp_path / "old.csv", "dukascopy", "UTC")
    # the H1/H4 caches reach back to Aug/Jul 2024, so those overlap; the M5/M15 do not. Strip them to prove the rule:
    for tf in ("H1", "H4"):
        (cache / f"EURUSD_{tf}.parquet").unlink()
    rep = import_external("EURUSD", p, tz="UTC", data_dir=cache)
    assert not rep.ok and any("overlap" in r for r in rep.reasons), rep.text()


def test_refuses_source_with_weekend_fill(cache, truth_m1, tmp_path):
    sat = truth_m1.iloc[2000:2300].copy()
    sat["time"] = pd.Timestamp("2024-06-08 06:00", tz="UTC") + pd.to_timedelta(np.arange(300), unit="min")   # a Saturday
    bad = pd.concat([truth_m1, sat]).sort_values("time")
    p = write_vendor_file(bad, tmp_path / "weekendfill.csv", "dukascopy", "UTC")
    rep = import_external("EURUSD", p, tz="UTC", data_dir=cache)
    assert not rep.ok and any("Saturday" in r for r in rep.reasons), rep.text()


def test_validate_bars_flags_missing_bars(cache, truth_m1):
    ref = pd.read_parquet(cache / "EURUSD_M5.parquet")
    imp = resample_bars(truth_m1, "M5")
    imp = imp[imp["time"] >= "2024-09-01"]
    assert validate_bars(imp, ref, "M5").ok
    holed = imp.drop(imp.index[::7])                                              # 14% of bars missing
    v = validate_bars(holed, ref, "M5")
    assert not v.ok and any(c.name == "bars_missing_in_import" for c in v.failures())


# ── merge guarantees ───────────────────────────────────────────────────────────────

def test_commit_extends_history_without_touching_mt5_rows(cache, vendor_dir):
    before = {tf: pd.read_parquet(cache / f"EURUSD_{tf}.parquet") for tf in ("M5", "M15", "H1", "H4")}
    rep = import_external("EURUSD", vendor_dir / "duka_utc.csv", tz="UTC", data_dir=cache, commit=True, name="duka-test")
    assert rep.ok and rep.committed, rep.text()
    for tf, old in before.items():
        new = pd.read_parquet(cache / f"EURUSD_{tf}.parquet")
        assert new["time"].iloc[0] < old["time"].iloc[0]                         # history got deeper
        assert new["time"].is_monotonic_increasing and not new["time"].duplicated().any()
        mt5 = new[new["src"] == "mt5"].drop(columns=["src", "spread_synth"]).reset_index(drop=True)
        pd.testing.assert_frame_equal(mt5, old.reset_index(drop=True))            # every MT5 row byte-identical
        imp = new[new["src"] != "mt5"]
        assert len(imp) > 0 and imp["spread_synth"].all() and (imp["src"] == "import:duka-test").all()
        assert (imp["time"] < old["time"].iloc[0]).all()                         # only OUTSIDE the MT5 span
        assert (imp["spread"] >= 1).all()                                        # never a free spread
    log = json.loads((cache / "import_log.json").read_text())["imports"][-1]
    assert log["symbol"] == "EURUSD" and len(log["sha256"]) == 64 and log["tz"] == "UTC"


def test_commit_is_idempotent_and_result_passes_quality(cache, vendor_dir):
    import_external("EURUSD", vendor_dir / "duka_utc.csv", tz="UTC", data_dir=cache, commit=True)
    h1 = _cache_hashes(cache)
    rep = import_external("EURUSD", vendor_dir / "duka_utc.csv", tz="UTC", data_dir=cache, commit=True)
    assert rep.ok and all(d["merge"]["n_added"] == 0 for d in rep.tf.values())
    assert _cache_hashes(cache) == h1
    for tf in ("M5", "H4"):
        q = quality_report(pd.read_parquet(cache / f"EURUSD_{tf}.parquet"), tf=tf, symbol="EURUSD")
        assert q["ok"], q["issues"]
        assert q["metrics"]["gaps_unexplained"] == 0 or q["metrics"]["gaps_unexplained_ge_1h"] == 0   # no seam hole
        assert q["metrics"]["spread_synthetic_pct"] > 0 and q["metrics"]["imported_pct"] > 0


def test_prepare_bars_runs_on_merged_cache_and_flags_synthetic(cache, vendor_dir):
    import_external("EURUSD", vendor_dir / "duka_utc.csv", tz="UTC", data_dir=cache, commit=True)
    out = prepare_bars("EURUSD", cache, check_quality=False)
    m5 = out["M5"]
    assert m5["spread_synthetic"].sum() > 10000 and (m5["spread_pts"] > 0).all()
    assert m5["time"].iloc[0] < pd.Timestamp("2024-09-01", tz="UTC")
    trimmed = prepare_bars("EURUSD", cache, start="2024-09-01", check_quality=False)["M5"]
    assert not trimmed["spread_synthetic"].any()                                 # `start` still selects the pure-MT5 window


def test_bid_ask_pair_import_flags_vendor_spread_as_synthetic(cache, truth_m1, tmp_path):
    bid = write_vendor_file(truth_m1, tmp_path / "bid.csv", "dukascopy", "UTC", noise=0.0)
    ask = write_vendor_file(truth_m1, tmp_path / "ask.csv", "dukascopy", "UTC", noise=0.0, side_shift=8e-5)
    rep = import_external("EURUSD", bid, ask_path=ask, tz="UTC", data_dir=cache, commit=True)
    assert rep.ok and "derived" in rep.spread_mode
    m5 = pd.read_parquet(cache / "EURUSD_M5.parquet")
    imp = m5[m5["src"] != "mt5"]
    assert imp["spread_synth"].all() and (imp["spread"] == 8).all()
