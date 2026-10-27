"""Append/de-duplicate fetch mode, broker clock conversion, merge semantics, prepare_bars flags. No real MT5 needed."""

import sys
import types
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from research import data as D                                                              # noqa: E402
from research.data import (merge_bars, normalize_bars, server_to_utc, utc_to_server, rates_to_frame,   # noqa: E402
                           fetch_history, prepare_bars, load_history)


def _bars(start, n, step_min=5, price0=1.1, seed=0, spread=10):
    rng = np.random.default_rng(seed)
    t = pd.date_range(start, periods=n, freq=f"{step_min}min", tz="UTC")
    c = price0 + np.cumsum(rng.normal(0, 1e-4, n))
    o = np.r_[price0, c[:-1]]
    return pd.DataFrame({"time": t, "open": o, "high": np.maximum(o, c) + 1e-5, "low": np.minimum(o, c) - 1e-5,
                         "close": c, "volume": rng.integers(1, 100, n), "spread": spread})


# ── clock ──────────────────────────────────────────────────────────────────────────

def _legacy_server_to_utc(server_ts):
    """The original (slow, per-element) implementation, kept as an oracle for the vectorised one."""
    naive = pd.to_datetime(server_ts, unit="s") if np.issubdtype(server_ts.dtype, np.integer) else pd.to_datetime(server_ts)
    cand3 = (naive - pd.Timedelta(hours=3)).dt.tz_localize("UTC")
    dst3 = cand3.dt.tz_convert("America/New_York").map(lambda t: bool(t.dst()))
    cand2 = (naive - pd.Timedelta(hours=2)).dt.tz_localize("UTC")
    return cand3.where(dst3, cand2)


def test_server_to_utc_known_values_and_oracle():
    s = pd.Series(pd.to_datetime(["2025-01-15 12:00", "2025-07-15 12:00", "2025-03-07 23:55", "2025-03-10 00:05",
                                  "2025-10-31 12:00", "2025-11-03 12:00"]))
    got = server_to_utc(s)
    assert got.iloc[0] == pd.Timestamp("2025-01-15 10:00", tz="UTC")       # winter: UTC+2
    assert got.iloc[1] == pd.Timestamp("2025-07-15 09:00", tz="UTC")       # summer: UTC+3
    assert got.iloc[4] == pd.Timestamp("2025-10-31 09:00", tz="UTC")       # US DST still on until 2 Nov
    assert got.iloc[5] == pd.Timestamp("2025-11-03 10:00", tz="UTC")
    wide = pd.Series(pd.date_range("2023-01-01", "2026-12-31", freq="37min"))
    assert server_to_utc(wide).equals(_legacy_server_to_utc(wide))


def test_utc_to_server_inverts_server_to_utc_outside_dst_switch_hour():
    utc = pd.Series(pd.date_range("2024-01-01", "2025-12-31", freq="11min", tz="UTC"))
    back = server_to_utc(utc_to_server(utc))
    bad = utc[back != utc]
    # only the 1h around the US fall-back (Sunday ~06:00-07:00 UTC) is ambiguous; FX is closed then
    assert (bad.dt.dayofweek == 6).all() and bad.dt.hour.isin([5, 6]).all()
    assert len(bad) < 30


def test_rates_to_frame_matches_legacy_conversion():
    utc = pd.date_range("2025-06-02 00:00", periods=50, freq="5min", tz="UTC")
    server_secs = (utc_to_server(pd.Series(utc)) - pd.Timestamp("1970-01-01")).dt.total_seconds().astype("int64")
    rates = np.zeros(50, dtype=[("time", "i8"), ("open", "f8"), ("high", "f8"), ("low", "f8"), ("close", "f8"),
                                ("tick_volume", "u8"), ("spread", "i4"), ("real_volume", "u8")])
    rates["time"], rates["open"], rates["high"], rates["low"], rates["close"] = server_secs, 1.1, 1.2, 1.0, 1.15
    rates["tick_volume"], rates["spread"] = 77, 3
    df = rates_to_frame(rates)
    assert list(df.columns) == ["time", "open", "high", "low", "close", "volume", "spread"]
    assert (df["time"].to_numpy() == utc.to_numpy()).all()                  # summer: server = UTC+3
    assert str(df["volume"].dtype) == "uint64" and str(df["spread"].dtype) == "int32"


# ── merge semantics ────────────────────────────────────────────────────────────────

def test_merge_accumulates_deduplicates_and_is_idempotent():
    full = _bars("2025-01-06", 172)
    a, b = full.iloc[:100].reset_index(drop=True), full.iloc[72:].reset_index(drop=True)   # overlap 28 bars
    m, st = merge_bars(a, b)
    assert len(m) == 172 and st["n_added"] == 72 and st["n_overlap"] == 28
    assert m["time"].is_monotonic_increasing and not m["time"].duplicated().any()
    m2, st2 = merge_bars(m, b)
    assert m2.equals(m) and st2["n_added"] == 0
    m3, _ = merge_bars(m, a)
    assert m3.equals(m)


def test_merge_prefer_new_replaces_overlap_and_counts_changes():
    a = _bars("2025-01-06", 50)
    b = a.iloc[30:].copy().reset_index(drop=True)
    b.loc[:4, "close"] += 0.001                                                # broker revised 5 bars
    m, st = merge_bars(a, b, prefer="new")
    assert st["n_changed"] == 5 and st["n_overlap"] == 20
    assert np.isclose(m.set_index("time").loc[b["time"].iloc[0], "close"], b["close"].iloc[0])
    m_old, _ = merge_bars(a, b, prefer="old")
    assert np.isclose(m_old.set_index("time").loc[b["time"].iloc[0], "close"], a["close"].iloc[30])


def test_merge_only_outside_never_fills_holes_inside_existing_span():
    old = _bars("2025-01-07", 200)
    old = old.drop(old.index[80:90]).reset_index(drop=True)                    # a hole inside the MT5 span
    ext = _bars("2025-01-06", 700)                                             # covers before, hole and after
    m, st = merge_bars(old, ext, prefer="old", only_outside=True)
    assert st["n_overlap"] == 0
    hole_times = _bars("2025-01-07", 200)["time"].iloc[80:90]
    assert not m["time"].isin(hole_times).any()
    assert m["time"].iloc[0] == ext["time"].iloc[0] and m["time"].iloc[-1] == ext["time"].iloc[-1]


def test_hole_detection_ignores_weekends_but_flags_midweek_gaps():
    mon = _bars("2025-01-06 00:00", 100)                                       # Mon
    fri_end = _bars("2025-01-10 20:00", 24)                                    # ends Fri 21:55
    sun_start = _bars("2025-01-12 22:00", 50)                                  # Sun 22:00 UTC
    _, st_weekend = merge_bars(fri_end, sun_start)
    assert st_weekend["hole_minutes"] == 0.0
    _, st_hole = merge_bars(mon, _bars("2025-01-08 12:00", 50))                # Mon 08:15 -> Wed 12:00 missing
    assert st_hole["hole_minutes"] > 24 * 60


def test_extra_columns_only_appear_when_needed():
    a, b = _bars("2025-01-06", 10), _bars("2025-01-06 02:00", 10)
    m, _ = merge_bars(a, b)
    assert list(m.columns) == D.BASE_COLS
    b2 = b.assign(src="import:x", spread_synth=True)
    m2, _ = merge_bars(a, b2, only_outside=True)
    assert {"src", "spread_synth"} <= set(m2.columns)
    assert set(m2["src"]) == {"mt5", "import:x"} and m2["spread_synth"].sum() == 10
    assert (m2.loc[m2["time"] <= a["time"].iloc[-1], "src"] == "mt5").all()


# ── append fetch against a stub MT5 ────────────────────────────────────────────────

class _StubMT5(types.SimpleNamespace):
    pass


def _make_stub(monkeypatch, series: pd.DataFrame, maxbars: int):
    """Stub `MetaTrader5` serving the newest `count` bars of `series` up to `state['upto']` (exclusive)."""
    state = {"upto": len(series), "requests": []}
    secs = ((utc_to_server(series["time"]) - pd.Timestamp("1970-01-01")).dt.total_seconds().astype("int64")).to_numpy()
    dt = np.dtype([("time", "i8"), ("open", "f8"), ("high", "f8"), ("low", "f8"), ("close", "f8"),
                   ("tick_volume", "u8"), ("spread", "i4"), ("real_volume", "u8")])
    arr = np.zeros(len(series), dtype=dt)
    arr["time"] = secs
    for c in ("open", "high", "low", "close"):
        arr[c] = series[c].to_numpy()
    arr["tick_volume"], arr["spread"] = series["volume"].to_numpy(), series["spread"].to_numpy()

    def copy_rates_from_pos(sym, tf, pos, count):
        state["requests"].append(count)
        if count >= maxbars:
            return None
        hi = state["upto"] - pos + 1
        return arr[max(0, hi - count):hi]

    info = types.SimpleNamespace(digits=5, point=1e-5, trade_tick_size=1e-5, trade_tick_value=1.0, trade_contract_size=100000.0,
                                 volume_min=0.01, volume_max=100.0, volume_step=0.01, trade_stops_level=0,
                                 currency_base="EUR", currency_profit="USD", spread=10)
    mod = _StubMT5(
        TIMEFRAME_M5=5, TIMEFRAME_M15=15, TIMEFRAME_H1=16385, TIMEFRAME_H4=16388,
        initialize=lambda *a, **k: True, shutdown=lambda: None, last_error=lambda: (1, "ok"),
        terminal_info=lambda: types.SimpleNamespace(maxbars=maxbars),
        account_info=lambda: types.SimpleNamespace(currency="USD"),
        symbol_info_tick=lambda s: None, symbol_select=lambda s, b: True, symbol_info=lambda s: info,
        copy_rates_from_pos=copy_rates_from_pos)
    monkeypatch.setitem(sys.modules, "MetaTrader5", mod)
    return state


def test_append_fetch_accumulates_beyond_terminal_cap(monkeypatch, tmp_path):
    series = _bars("2025-01-06", 1500)
    st = _make_stub(monkeypatch, series, maxbars=800)
    st["upto"] = 1000
    fetch_history(["EURUSD"], tfs=["M5"], bars_wanted={"M5": 799}, out_dir=tmp_path)
    first = load_history("EURUSD", "M5", tmp_path)
    assert len(first) == 799 and first["time"].iloc[0] == series["time"].iloc[1000 - 799]
    st["upto"] = 1300                                                           # the cap window slides 300 bars on
    fetch_history(["EURUSD"], tfs=["M5"], bars_wanted={"M5": 799}, out_dir=tmp_path)
    merged = load_history("EURUSD", "M5", tmp_path)
    assert len(merged) == 799 + 300                                             # grew past the cap instead of being replaced
    assert not merged["time"].duplicated().any() and merged["time"].is_monotonic_increasing
    # overwrite (legacy) mode loses the older bars
    fetch_history(["EURUSD"], tfs=["M5"], bars_wanted={"M5": 799}, out_dir=tmp_path, append=False)
    assert len(load_history("EURUSD", "M5", tmp_path)) == 799
    meta = D._read_json(tmp_path / "meta.json")
    assert meta["terminal_maxbars"] == 800 and "EURUSD" in meta["symbols"]


def test_append_fetch_reports_hole_when_window_slides_past_cache(monkeypatch, tmp_path, caplog):
    series = _bars("2025-01-06", 3000)
    st = _make_stub(monkeypatch, series, maxbars=500)
    st["upto"] = 600
    fetch_history(["EURUSD"], tfs=["M5"], bars_wanted={"M5": 400}, out_dir=tmp_path)
    st["upto"] = 2000                                                           # 1400 bars later: cache end is out of reach
    with caplog.at_level("WARNING"):
        fetch_history(["EURUSD"], tfs=["M5"], bars_wanted={"M5": 400}, out_dir=tmp_path)
    assert any("missing between them" in r.message for r in caplog.records)


def test_tail_mode_requests_few_bars_and_max_depth_uses_terminal_cap(monkeypatch, tmp_path):
    now = pd.Timestamp.now(tz="UTC").floor("5min")
    t = pd.date_range(end=now - pd.Timedelta(minutes=5), periods=3000, freq="5min", tz="UTC")
    series = _bars("2025-01-06", 3000).assign(time=t)
    st = _make_stub(monkeypatch, series, maxbars=5000)
    st["upto"] = 2900                                                           # cache ends ~8h ago
    fetch_history(["EURUSD"], tfs=["M5"], bars_wanted={"M5": 2000}, out_dir=tmp_path)
    st["requests"].clear(); st["upto"] = 3000
    fetch_history(["EURUSD"], tfs=["M5"], bars_wanted={"M5": 2000}, out_dir=tmp_path, mode="tail")
    assert st["requests"][0] < 1000                                             # not the full 2000
    assert len(load_history("EURUSD", "M5", tmp_path)) == 2000 + 100
    st["requests"].clear()
    fetch_history(["EURUSD"], tfs=["M5"], out_dir=tmp_path, max_depth=True)
    assert st["requests"] == [4999]
    with pytest.raises(ValueError):
        fetch_history(["EURUSD"], tfs=["M5"], out_dir=tmp_path, mode="tail", append=False)


# ── prepare_bars ───────────────────────────────────────────────────────────────────

def _legacy_spread_pts(df):
    sp = df["spread"].astype(float)
    nz = sp.where(sp > 0)
    by_hour = nz.groupby(df["time"].dt.hour).transform("median")
    return sp.where(sp > 0, by_hour).fillna(nz.median()).fillna(0.0)


def test_prepare_bars_unchanged_for_plain_mt5_cache(cache):
    out = prepare_bars("EURUSD", cache, check_quality=False)
    raw = pd.read_parquet(cache / "EURUSD_M5.parquet")
    assert np.allclose(out["M5"]["spread_pts"].to_numpy(), _legacy_spread_pts(raw).to_numpy())
    assert not out["M5"]["spread_synthetic"].any()


def test_prepare_bars_keeps_synthetic_spread_and_excludes_it_from_imputation(tmp_path):
    a = _bars("2025-01-06", 600, spread=0)                                      # recorded zeros -> imputed
    a.loc[a.index % 3 == 0, "spread"] = 4                                       # real non-zero median = 4
    imp = _bars("2025-01-03", 100, spread=99).assign(src="import:x", spread_synth=True)
    m, _ = merge_bars(a, imp, only_outside=True)
    for tf in D.TFS:
        D.save_bars(m, tmp_path / f"EURUSD_{tf}.parquet")
    out = prepare_bars("EURUSD", tmp_path, check_quality=False)["M5"]
    synth = out["spread_synthetic"]
    assert synth.sum() == 100 and (out.loc[synth, "spread_pts"] == 99).all()
    real_zero = (~synth) & out["spread_was_zero"]
    assert (out.loc[real_zero, "spread_pts"] == 4).all()                        # the 99s did not pollute the median
