"""Hand-built price paths for the rule-change options added to SimConfig (all default OFF)."""

import sys
from dataclasses import fields
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from research.sim import Simulator, SimConfig   # noqa: E402

SPEC = {"EURUSD": dict(digits=5, point=1e-5, contract_size=100000, volume_min=0.01, volume_max=100,
                       volume_step=0.01, stops_level=0, currency_profit="USD")}

FRI = pd.Timestamp("2024-03-08 19:00", tz="UTC")      # a Friday
THU = pd.Timestamp("2024-03-07 19:00", tz="UTC")      # a Thursday
assert FRI.dayofweek == 4 and THU.dayofweek == 3


def bars(path, t0, spread_pts=0.0):
    rows = [(t0 + pd.Timedelta(minutes=5 * i), *b) for i, b in enumerate(path)]
    df = pd.DataFrame(rows, columns=["time", "open", "high", "low", "close"])
    df["spread_pts"] = spread_pts
    return {"EURUSD": df}


def cand(t0, at_bar, direction="BUY", price=1.0990, sl=1.0970, subtype="LIMIT", atr=1.0, size=1.0):
    T = t0 + pd.Timedelta(minutes=5 * (at_bar + 1))             # cycle time = close of bar `at_bar`
    return pd.DataFrame([{
        "T": T, "symbol": "EURUSD", "direction": direction, "price": price, "sl_raw": sl,
        "atr_factor": atr, "size_factor": size, "entry_type": "LIMIT", "subtype": subtype,
        "ask": price, "bid": price, "spread_pts": 0.0, "probability": 70, "checks_passed": 4,
        "pattern": "x", "fvg_sig": direction, "sweep_sig": "NONE", "qt_phase": "London", "trend_strong": True,
    }])


def flat(n, p=1.1000):
    return [(p, p + 0.0001, p - 0.0001, p)] * n


def run(path, t0, c, **kw):
    kw.setdefault("breakeven", False)
    cfg = SimConfig(commission_per_lot_side=0.0, monthly_target_pct=None, **kw)
    return Simulator(["EURUSD"], bars(path, t0), SPEC, c, cfg).run()


# bar i covers [t0+5i, t0+5i+5); T(i) = t0 + 5(i+1).  From 19:00: T=20:30 <-> i=17
def hold_path(n=45, drift=1.0995):
    """fills a BUY LIMIT 1.0990 at bar 3 (dip to 1.0985), then sits at `drift` (no SL/TP touched)."""
    return flat(3) + [(1.1000, 1.1000, 1.0985, drift)] + flat(n - 4, drift)


def test_new_fields_default_off():
    d = SimConfig()
    assert d.friday_flatten_utc is None and d.friday_no_entry_from_utc is None and d.no_entry_from_utc is None
    assert d.skip_stop_entries is False and d.fixed_risk_pct is None
    assert {f.name for f in fields(d)} >= {"friday_flatten_utc", "friday_no_entry_from_utc", "no_entry_from_utc",
                                           "skip_stop_entries", "fixed_risk_pct"}


def test_friday_flatten_closes_at_2030_bar_close():
    path = hold_path()
    res = run(path, FRI, cand(FRI, 1), friday_flatten_utc=20.5)
    t = res["trades"]
    assert len(t) == 1
    r = t.iloc[0]
    assert r["exit_reason"] == "FLAT"
    assert r["close_T"] == pd.Timestamp("2024-03-08 20:30", tz="UTC")
    assert r["exit"] == pytest.approx(1.0995)                    # bid = last close
    assert r["r"] == pytest.approx(0.25, abs=0.02)               # +5 pips on a 20-pip stop
    assert res["funnel"]["flattened"] == 1


def test_without_flatten_position_is_held():
    res = run(hold_path(), FRI, cand(FRI, 1))
    t = res["trades"]
    assert len(t) == 1 and t.iloc[0]["exit_reason"] == "END"


def test_flatten_does_nothing_on_thursday():
    res = run(hold_path(), THU, cand(THU, 1), friday_flatten_utc=20.5)
    t = res["trades"]
    assert len(t) == 1 and t.iloc[0]["exit_reason"] == "END"


def test_flatten_sell_pays_the_ask():
    # SELL LIMIT 1.1010 fills when bar high >= 1.1010; spread 4 pts*1e-5 -> exit at close + spread
    path = flat(3) + [(1.1000, 1.1015, 1.0999, 1.1005)] + flat(40, 1.1005)
    c = cand(FRI, 1, direction="SELL", price=1.1010, sl=1.1030)
    cfg = SimConfig(commission_per_lot_side=0.0, monthly_target_pct=None, breakeven=False, friday_flatten_utc=20.5,
                    use_spread_filter=False)
    res = Simulator(["EURUSD"], bars(path, FRI, spread_pts=40.0), SPEC, c, cfg).run()
    t = res["trades"].iloc[0]
    assert t["exit_reason"] == "FLAT"
    assert t["exit"] == pytest.approx(1.1005 + 0.0004)


def test_flatten_blocks_new_entry_after_cutoff_and_cancels_pendings():
    # order placed at T=20:20 (bar 15) is still pending at the 20:30 flatten -> cancelled, and the dip at 20:40 must not fill it
    path = flat(40)
    path[19] = (1.1000, 1.1000, 1.0985, 1.0995)                   # 20:35-20:40 dips through the limit
    res = run(path, FRI, cand(FRI, 15), friday_flatten_utc=20.5)  # T = 20:20
    assert res["funnel"].get("cancelled_time", 0) == 1
    assert len(res["trades"]) == 0
    # a fresh candidate at/after 20:30 is blocked outright
    res2 = run(hold_path() + flat(10), FRI, cand(FRI, 17), friday_flatten_utc=20.5)   # T = 20:30
    assert res2["funnel"].get("orders_placed", 0) == 0 and res2["funnel"]["blocked_time"] == 1


def test_friday_no_entry_from_only_affects_fridays():
    fri = run(hold_path(), FRI, cand(FRI, 12), friday_no_entry_from_utc=19.5)    # T = 20:05 > 19:30
    assert fri["funnel"].get("orders_placed", 0) == 0 and fri["funnel"]["blocked_time"] == 1
    thu = run(hold_path(), THU, cand(THU, 12), friday_no_entry_from_utc=19.5)
    assert thu["funnel"].get("orders_placed", 0) == 1


def test_no_entry_from_hour_blocks_placement_and_cancels_pending_fills():
    # placement blocked
    res = run(hold_path(), THU, cand(THU, 12), no_entry_from_utc=20.0)           # T = 20:05
    assert res["funnel"].get("orders_placed", 0) == 0
    # order placed at T=19:50 (bar 10) whose limit is touched in the bar ending 20:00 -> the 20:00 cycle is blocked: cancelled, no trade
    path = flat(40)
    path[11] = (1.1000, 1.1000, 1.0985, 1.0995)                                  # bar 19:55-20:00 -> T = 20:00
    res = run(path, THU, cand(THU, 10), no_entry_from_utc=20.0)
    assert len(res["trades"]) == 0 and res["funnel"].get("cancelled_time", 0) == 1
    # same path without the rule fills
    assert len(run(path + flat(3, 1.0995), THU, cand(THU, 10))["trades"]) == 1


def test_skip_stop_entries():
    path = flat(3) + [(1.1000, 1.1030, 1.0999, 1.1020)] + flat(10, 1.1020)
    c = cand(FRI, 1, direction="BUY", price=1.1010, sl=1.0990, subtype="STOP")
    assert len(run(path, FRI, c)["trades"]) == 1
    res = run(path, FRI, c, skip_stop_entries=True)
    assert len(res["trades"]) == 0 and res["funnel"]["skipped_stop"] == 1
    lim = run(hold_path(), FRI, cand(FRI, 1), skip_stop_entries=True)             # LIMIT still allowed
    assert len(lim["trades"]) == 1


def test_fixed_risk_pct_ignores_atr_and_regime_scaling():
    # default sizing: 0.5 % * atr 0.5 * size 0.5 = 0.125 %  ->  $125 on 100k ;  fixed 0.25 % -> $250
    c = cand(FRI, 1, atr=0.5, size=0.5)
    base = run(hold_path(), FRI, c)["trades"].iloc[0]
    fixed = run(hold_path(), FRI, c, fixed_risk_pct=0.25)["trades"].iloc[0]
    assert base["risk_pct"] == pytest.approx(0.125) and base["risk_usd"] == pytest.approx(125.0, rel=0.02)
    assert fixed["risk_pct"] == pytest.approx(0.25) and fixed["risk_usd"] == pytest.approx(250.0, rel=0.02)
    # scaling factors of 1.0 and fixed == 0.5 reproduces the default exactly
    c1 = cand(FRI, 1)
    a = run(hold_path(), FRI, c1)["trades"].iloc[0]
    b = run(hold_path(), FRI, c1, fixed_risk_pct=0.5)["trades"].iloc[0]
    assert a["volume"] == b["volume"] and a["r"] == pytest.approx(b["r"])


def test_tp_gap_fill_default_off_and_fills_at_gapped_open():
    assert SimConfig().tp_gap_fill is False
    # BUY LIMIT 1.0990 / SL 1.0970 / TP 1.1050 ; Friday 20:20 bar close 1.0995, next bar OPENS at 1.1100 (gap through TP)
    path = hold_path(n=6) + [(1.1100, 1.1105, 1.1095, 1.1100)] + flat(3, 1.1100)
    capped = run(path, FRI, cand(FRI, 1))["trades"].iloc[0]
    gap = run(path, FRI, cand(FRI, 1), tp_gap_fill=True)["trades"].iloc[0]
    assert capped["exit_reason"] == "TP" and capped["exit"] == pytest.approx(1.1050)
    assert capped["r"] == pytest.approx(3.0, abs=0.02)
    assert gap["exit_reason"] == "TP" and gap["exit"] == pytest.approx(1.1100)
    assert gap["r"] == pytest.approx(5.5, abs=0.02)                   # (1.1100-1.0990)/0.0020
    # SELL: ask at the gapped open (open + spread) is the fill when it is below TP
    path = flat(3) + [(1.1000, 1.1015, 1.0999, 1.1005)] + flat(2, 1.1005) + [(1.0900, 1.0905, 1.0895, 1.0900)] + flat(3, 1.0900)
    c = cand(FRI, 1, direction="SELL", price=1.1010, sl=1.1030)
    cfg = dict(commission_per_lot_side=0.0, monthly_target_pct=None, breakeven=False, use_spread_filter=False)
    out = {}
    for flag in (False, True):
        r = Simulator(["EURUSD"], bars(path, FRI, spread_pts=20.0), SPEC, c, SimConfig(tp_gap_fill=flag, **cfg)).run()
        out[flag] = r["trades"].iloc[0]
    assert out[False]["exit"] == pytest.approx(1.0950) and out[True]["exit"] == pytest.approx(1.0900 + 0.0002)


def test_tp_gap_fill_does_not_change_non_gap_exit():
    path = flat(3) + [(1.1000, 1.1000, 1.0985, 1.0995)] + flat(2, 1.0995) + [(1.0995, 1.1052, 1.0995, 1.1050)] + flat(3)
    a = run(path, FRI, cand(FRI, 1))["trades"].iloc[0]
    b = run(path, FRI, cand(FRI, 1), tp_gap_fill=True)["trades"].iloc[0]
    assert a["exit"] == b["exit"] == pytest.approx(1.1050)


def test_reopen_spread_zero_only_affects_first_bar_after_weekend():
    assert SimConfig().reopen_spread_zero is False
    # SELL LIMIT 1.1010 fills Friday; SL 1.1030. Sunday-reopen bar: open 1.1004, high 1.1010, spread 40 pips (400 pts)
    fri = [(FRI + pd.Timedelta(minutes=5 * i), *b) for i, b in enumerate(
        flat(3) + [(1.1000, 1.1015, 1.0999, 1.1005)] + flat(2, 1.1005))]
    sun0 = pd.Timestamp("2024-03-10 22:00", tz="UTC")
    sun = [(sun0, 1.1004, 1.1010, 1.1000, 1.1005), (sun0 + pd.Timedelta(minutes=5), 1.1005, 1.1010, 1.1000, 1.1005)]
    df = pd.DataFrame(fri + sun, columns=["time", "open", "high", "low", "close"])
    df["spread_pts"] = 0.0
    df.loc[df.index[-2], "spread_pts"] = 400.0
    c = cand(FRI, 1, direction="SELL", price=1.1010, sl=1.1030)
    res = {}
    for flag in (False, True):
        cfg = SimConfig(commission_per_lot_side=0.0, monthly_target_pct=None, breakeven=False, use_spread_filter=False,
                        reopen_spread_zero=flag)
        res[flag] = Simulator(["EURUSD"], {"EURUSD": df.copy()}, SPEC, c, cfg).run()["trades"]
    assert res[False].iloc[0]["exit_reason"] == "SL" and res[False].iloc[0]["exit"] == pytest.approx(1.1004 + 0.0040)
    assert res[True].iloc[0]["exit_reason"] == "END"                 # not stopped when the reopen spread is ignored
