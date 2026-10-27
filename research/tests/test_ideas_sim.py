"""Mechanics of the idea-specific pieces: time stop, Friday flatten, mirror (null), gross conversion, rule examples."""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from research.ideas.simx import TimedSimulator, edge_sim_config, to_gross, gross_sim_config   # noqa: E402
from research.ideas.common import friday_flat_ns, cap_expiry, make_row, SPLIT                # noqa: E402
from research.ideas.evaluate import mirror_market, holm                                       # noqa: E402
from research.ideas import idea_a                                                              # noqa: E402

SPEC = {"EURUSD": dict(digits=5, point=1e-5, contract_size=100000, volume_min=0.01, volume_max=100,
                       volume_step=0.01, stops_level=0, currency_profit="USD")}
T0 = pd.Timestamp("2024-03-05 10:00", tz="UTC")            # a Tuesday


def mk_bars(closes, spread_pts=0.0, wick=0.0001):
    rows = []
    prev = closes[0]
    for i, c in enumerate(closes):
        rows.append((T0 + pd.Timedelta(minutes=5 * i), prev, max(prev, c) + wick, min(prev, c) - wick, c))
        prev = c
    df = pd.DataFrame(rows, columns=["time", "open", "high", "low", "close"])
    df["spread_pts"] = spread_pts
    return {"EURUSD": df}


def mk_cand(at_bar, direction, expire_bar, price=1.1000, dist=0.0020, spread_pts=0.0):
    T = T0 + pd.Timedelta(minutes=5 * (at_bar + 1))
    bid = price if direction == "SELL" else price
    sl = price - dist if direction == "BUY" else price + dist
    return pd.DataFrame([{
        "T": T, "symbol": "EURUSD", "direction": direction, "price": price, "sl_raw": sl,
        "atr_factor": 1.0, "size_factor": 1.0, "entry_type": "MARKET", "subtype": "MARKET",
        "ask": price + spread_pts * 1e-5 if direction == "BUY" else price + spread_pts * 1e-5,
        "bid": price, "spread_pts": spread_pts, "probability": 100.0, "checks_passed": 1,
        "pattern": "t", "fvg_sig": "NONE", "sweep_sig": "NONE", "qt_phase": "NA", "trend_strong": False,
        "expire_ns": int((T0 + pd.Timedelta(minutes=5 * (expire_bar + 1))).value),
    }])


def run(closes, c, spread_pts=0.0, rr=2.0):
    cfg = edge_sim_config(rr, commission_per_lot_side=0.0, use_spread_filter=False)
    return TimedSimulator(["EURUSD"], mk_bars(closes, spread_pts), SPEC, c, cfg).run()


def test_time_stop_exits_at_bar_close_and_reports_r():
    # +10 pips on a 20-pip stop from bar 6 on; expire at the close of bar 8 -> exit 1.1010 => +0.5R
    closes = [1.1000] * 6 + [1.1010] * 8
    t = run(closes, mk_cand(2, "BUY", 8))["trades"]
    assert len(t) == 1 and t.iloc[0]["exit_reason"] == "TIME"
    assert t.iloc[0]["exit"] == pytest.approx(1.1010)
    assert t.iloc[0]["close_T"] == T0 + pd.Timedelta(minutes=5 * 9)
    assert t.iloc[0]["r"] == pytest.approx(0.5, abs=0.03)


def test_stop_loss_before_expiry_wins():
    closes = [1.1000] * 4 + [1.0970] * 8
    t = run(closes, mk_cand(2, "BUY", 9))["trades"]
    assert t.iloc[0]["exit_reason"] == "SL" and t.iloc[0]["r"] == pytest.approx(-1.0, abs=0.03)


def test_take_profit_before_expiry_is_2r():
    closes = [1.1000] * 4 + [1.1045] * 8
    t = run(closes, mk_cand(2, "BUY", 9), rr=2.0)["trades"]
    assert t.iloc[0]["exit_reason"] == "TP" and t.iloc[0]["r"] == pytest.approx(2.0, abs=0.03)


def test_sell_time_exit_pays_the_spread():
    # flat market, 2-pip spread: SELL enters at bid 1.1000, buys back at ask = 1.1002 => -2/20 = -0.1R
    t = run([1.1000] * 14, mk_cand(2, "SELL", 8), spread_pts=20.0)["trades"]
    assert t.iloc[0]["exit_reason"] == "TIME"
    assert t.iloc[0]["exit"] == pytest.approx(1.1002)
    assert t.iloc[0]["r"] == pytest.approx(-0.1, abs=0.03)


def test_buy_time_exit_pays_the_spread_on_entry():
    # BUY enters at ask 1.1002, exits at bid 1.1000 => -2 pips
    t = run([1.1000] * 14, mk_cand(2, "BUY", 8, spread_pts=20.0), spread_pts=20.0)["trades"]
    assert t.iloc[0]["entry"] == pytest.approx(1.1002) and t.iloc[0]["exit"] == pytest.approx(1.1000)
    assert t.iloc[0]["r"] == pytest.approx(-0.1, abs=0.03)


def test_no_trade_state_leaks_without_expiry_column():
    c = mk_cand(2, "BUY", 8).drop(columns=["expire_ns"])
    res = run([1.1000] * 14, c)
    assert res["trades"].iloc[0]["exit_reason"] == "END"          # nothing else closes it


def test_correlation_filter_restored_after_run():
    import research.sim as s
    orig = s.correlation_filter
    run([1.1000] * 14, mk_cand(2, "BUY", 8))
    assert s.correlation_filter is orig


# ── Friday flatten ────────────────────────────────────────────────────────────────

def test_friday_flat():
    wed = pd.Timestamp("2026-05-06 10:00", tz="UTC")
    assert pd.Timestamp(friday_flat_ns(wed), tz="UTC") == pd.Timestamp("2026-05-08 20:00", tz="UTC")
    fri_am = pd.Timestamp("2026-05-08 07:00", tz="UTC")
    assert pd.Timestamp(friday_flat_ns(fri_am), tz="UTC") == pd.Timestamp("2026-05-08 20:00", tz="UTC")
    fri_late = pd.Timestamp("2026-05-08 20:00", tz="UTC")
    assert pd.Timestamp(friday_flat_ns(fri_late), tz="UTC") == fri_late
    # 48 h hold from Thursday 10:00 is capped by Friday 20:00; a Friday 19:45 entry is skipped (< 30 min horizon)
    thu = pd.Timestamp("2026-05-07 10:00", tz="UTC")
    assert pd.Timestamp(cap_expiry(thu, thu + pd.Timedelta(hours=48)), tz="UTC") == pd.Timestamp("2026-05-08 20:00", tz="UTC")
    assert cap_expiry(pd.Timestamp("2026-05-08 19:45", tz="UTC"), pd.Timestamp("2026-05-10 19:45", tz="UTC")) is None


def test_split_is_a_monday_and_fixed():
    assert SPLIT == pd.Timestamp("2026-05-04", tz="UTC") and SPLIT.dayofweek == 0


# ── null (mirror) and gross conversions ───────────────────────────────────────────

def _cands():
    rows = [mk_cand(2, "BUY", 8, spread_pts=20.0).iloc[0].to_dict(), mk_cand(3, "SELL", 8, spread_pts=20.0).iloc[0].to_dict()]
    rows[0]["price"] = rows[0]["ask"]
    rows[1]["T"] = rows[1]["T"] + pd.Timedelta(days=1)
    rows[1]["sl_raw"] = rows[1]["price"] + 0.0030
    return pd.DataFrame(rows)


def test_mirror_swaps_direction_and_keeps_stop_distance():
    c = _cands()
    m = mirror_market(c, np.array([True, True]))
    assert list(m["direction"]) == ["SELL", "BUY"]
    d0, d1 = (c["price"] - c["sl_raw"]).abs(), (m["price"] - m["sl_raw"]).abs()
    assert np.allclose(d0, d1)
    assert m.loc[0, "price"] == pytest.approx(c.loc[0, "bid"]) and m.loc[1, "price"] == pytest.approx(c.loc[1, "ask"])
    assert m.loc[0, "sl_raw"] > m.loc[0, "price"] and m.loc[1, "sl_raw"] < m.loc[1, "price"]
    same = mirror_market(c, np.array([False, False]))
    pd.testing.assert_frame_equal(same, c)
    twice = mirror_market(m, np.array([True, True]))
    assert np.allclose(twice["price"], c["price"]) and np.allclose(twice["sl_raw"], c["sl_raw"])


def test_gross_has_no_spread_and_same_stop_distance():
    c = _cands()
    g = to_gross(c)
    assert (g["ask"] == g["bid"]).all() and (g["spread_pts"] == 0).all()
    assert np.allclose((c["price"] - c["sl_raw"]).abs(), (g["price"] - g["sl_raw"]).abs())
    cfg = gross_sim_config(edge_sim_config(2.0))
    assert cfg.spread_mult == 0 and cfg.commission_per_lot_side == 0


def test_random_direction_null_is_centred_below_zero_for_noise():
    """On a driftless random walk with costs, random-direction trades must lose about the cost, never win."""
    rng = np.random.default_rng(2)
    n = 6000
    closes = 1.1 + np.cumsum(rng.normal(0, 0.00015, n))
    bars = mk_bars(list(closes), spread_pts=10.0, wick=0.00008)
    rows = []
    for k in range(10, n - 200, 150):
        d = "BUY" if rng.random() < 0.5 else "SELL"
        bid = closes[k]
        price = bid + 1e-4 if d == "BUY" else bid
        rows.append({**mk_cand(k, d, k + 120, price=price, dist=0.0015, spread_pts=10.0).iloc[0].to_dict(),
                     "bid": bid, "ask": bid + 1e-4})
    c = pd.DataFrame(rows)
    cfg = edge_sim_config(2.0, commission_per_lot_side=0.0)
    out = []
    for sd in range(20):
        flip = np.random.default_rng(sd).random(len(c)) < 0.5
        t = TimedSimulator(["EURUSD"], bars, SPEC, mirror_market(c, flip), cfg).run()["trades"]
        out.append(t["r"].mean())
    assert np.mean(out) < 0.05


# ── rule example for arm A (hand-built session) ───────────────────────────────────

def test_arm_a_hand_built_sweep_and_reverse():
    # London-local winter day (UTC == local): Asian range 1.1000..1.1020 (bars 00:00-06:55), sweep to 1.1030 at 07:10,
    # then a close back below the Asian high and below the previous 6 bars' lows -> SELL; stop = 1.1030 + 2 pips.
    idx = pd.date_range("2026-01-13 00:00", "2026-01-13 12:00", freq="5min", tz="UTC")
    n = len(idx)
    px = np.full(n, 1.1010)
    hi = px + 0.0002
    lo = px - 0.0002
    hi[0], lo[1] = 1.1020, 1.1000                   # define Asian high / low
    k_sweep = list(idx).index(pd.Timestamp("2026-01-13 07:10", tz="UTC"))
    hi[k_sweep] = 1.1030
    px[k_sweep] = 1.1028
    # following bars stay high so the previous-6-bar low is ~1.1008; then a sharp close at 1.1005 (< AH and < those lows)
    k_trig = k_sweep + 3
    for j in range(k_sweep + 1, k_trig):
        px[j], hi[j], lo[j] = 1.1020, 1.1022, 1.1012
    lo[k_sweep - 6:k_trig] = np.maximum(lo[k_sweep - 6:k_trig], 1.1008)
    px[k_trig], hi[k_trig], lo[k_trig] = 1.1005, 1.1015, 1.1003
    m5 = pd.DataFrame({"time": idx, "open": np.r_[px[0], px[:-1]], "high": hi, "low": lo, "close": px,
                       "volume": 1, "spread_pts": 10.0})
    m5.loc[m5["time"] < pd.Timestamp("2026-01-13 07:00", tz="UTC"), "high"] = np.minimum(m5["high"], 1.1020)
    bars = {"M5": m5}
    # the generator needs >= 500 bars: pad the front with earlier flat days
    pad_idx = pd.date_range("2026-01-09 00:00", "2026-01-12 23:55", freq="5min", tz="UTC")
    pad_idx = pad_idx[pad_idx.dayofweek < 5]
    pad = pd.DataFrame({"time": pad_idx, "open": 1.1010, "high": 1.1012, "low": 1.1008, "close": 1.1010, "volume": 1,
                        "spread_pts": 10.0})
    bars = {"M5": pd.concat([pad, m5], ignore_index=True)}
    c = idea_a.generate("EURUSD", bars, 1e-5, idea_a.Params())
    c = c[c["T"].dt.date == pd.Timestamp("2026-01-13").date()]
    assert len(c) == 1
    r = c.iloc[0]
    assert r["direction"] == "SELL"
    assert r["T"] == m5["time"].iloc[k_trig] + pd.Timedelta(minutes=5)
    assert r["sl_raw"] == pytest.approx(1.1030 + 0.0002)
    assert r["expire_ns"] == int(pd.Timestamp("2026-01-13 16:00", tz="UTC").value)


def test_holm():
    adj = holm({"a": 0.01, "b": 0.04, "c": 0.03, "d": 0.5})
    assert adj["a"] == pytest.approx(0.04) and adj["c"] == pytest.approx(0.09) and adj["b"] == pytest.approx(0.09)
    assert adj["d"] == pytest.approx(0.5)
