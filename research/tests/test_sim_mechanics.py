"""Hand-checkable scenarios for the simulator (fills, SL/TP, spread, breakeven, expiry, gates)."""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from research.sim import Simulator, SimConfig   # noqa: E402

SPEC = {"EURUSD": dict(digits=5, point=1e-5, contract_size=100000, volume_min=0.01, volume_max=100,
                       volume_step=0.01, stops_level=0, currency_profit="USD")}
T0 = pd.Timestamp("2024-03-05 10:00", tz="UTC")


def bars(path, spread_pts=0.0):
    """path: list of (open, high, low, close) M5 bars starting 10:00 UTC."""
    rows = [(T0 + pd.Timedelta(minutes=5 * i), *b) for i, b in enumerate(path)]
    df = pd.DataFrame(rows, columns=["time", "open", "high", "low", "close"])
    df["spread_pts"] = spread_pts
    return {"EURUSD": df}


def cand(at_bar, direction="BUY", price=1.0990, sl=1.0970, subtype="LIMIT", spread_pts=0.0):
    T = T0 + pd.Timedelta(minutes=5 * (at_bar + 1))            # cycle = close of bar `at_bar`
    return pd.DataFrame([{
        "T": T, "symbol": "EURUSD", "direction": direction, "price": price, "sl_raw": sl,
        "atr_factor": 1.0, "size_factor": 1.0, "entry_type": "LIMIT", "subtype": subtype,
        "ask": price, "bid": price, "spread_pts": spread_pts, "probability": 70, "checks_passed": 4,
        "pattern": "x", "fvg_sig": direction, "sweep_sig": "NONE", "qt_phase": "London", "trend_strong": True,
    }])


def flat(n, p=1.1000):
    return [(p, p + 0.0001, p - 0.0001, p)] * n


def run(path, c, _sp=0.0, **kw):
    kw.setdefault("breakeven", False)
    cfg = SimConfig(commission_per_lot_side=0.0, monthly_target_pct=None, **kw)
    return Simulator(["EURUSD"], bars(path, spread_pts=_sp), SPEC, c, cfg).run()


def test_buy_limit_hits_tp_is_plus_3r():
    path = flat(3) + [(1.1000, 1.1000, 1.0985, 1.0995)] + flat(2, 1.0995) + [(1.0995, 1.1052, 1.0995, 1.1050)] + flat(3)
    res = run(path, cand(1))
    t = res["trades"]
    assert len(t) == 1 and t.iloc[0]["exit_reason"] == "TP"
    assert t.iloc[0]["r"] == pytest.approx(3.0, abs=0.02)


def test_buy_limit_hits_sl_is_minus_1r():
    path = flat(3) + [(1.1000, 1.1000, 1.0985, 1.0992)] + [(1.0992, 1.0992, 1.0965, 1.0968)] + flat(3, 1.0968)
    res = run(path, cand(1))
    t = res["trades"]
    assert len(t) == 1 and t.iloc[0]["exit_reason"] == "SL"
    assert t.iloc[0]["r"] == pytest.approx(-1.0, abs=0.02)


def test_spread_blocks_limit_fill_that_mid_price_would_hit():
    # bid low 1.0988, spread 3 pips -> ask low 1.0991 > 1.0990 -> a BUY LIMIT must NOT fill
    path = flat(3) + [(1.1000, 1.1000, 1.0988, 1.0995)] + flat(6, 1.0995)
    wide = run(path, cand(1), _sp=30.0, use_spread_filter=False)
    assert wide["funnel"]["orders_placed"] == 1 and wide["funnel"].get("filled", 0) == 0
    # with zero spread the same path fills
    assert run(path + flat(3), cand(1), _sp=0.0)["funnel"].get("filled", 0) == 1


def test_same_bar_sl_and_tp_resolves_to_sl():
    path = flat(3) + [(1.1000, 1.1000, 1.0985, 1.0995)] + [(1.0995, 1.1060, 1.0960, 1.1000)] + flat(3)
    t = run(path, cand(1))["trades"]
    assert t.iloc[0]["exit_reason"] == "SL"


def test_fill_bar_ignores_tp_under_pessimistic_rule():
    # bar 3 touches the limit (fills) AND prints a high above TP -> TP must not count on the fill bar
    path = flat(3) + [(1.1000, 1.1060, 1.0985, 1.1000)] + flat(3)
    res = run(path, cand(1))
    assert res["funnel"].get("filled") == 1
    assert len(res["trades"]) == 1 and res["trades"].iloc[0]["exit_reason"] == "END"


def test_breakeven_moves_stop_then_exits_near_zero():
    # entry 1.0990, SL dist 20 pips -> +1R at 1.1010; bar closes there, then price returns through BE stop
    path = (flat(3) + [(1.1000, 1.1000, 1.0985, 1.0995)]
            + [(1.0995, 1.1012, 1.0995, 1.1011)]
            + [(1.1011, 1.1011, 1.0985, 1.0988)] + flat(3, 1.0988))
    t = run(path, cand(1), breakeven=True)["trades"]
    assert len(t) == 1
    assert t.iloc[0]["exit_reason"] == "BE" and t.iloc[0]["be_moved"]
    assert 0 < t.iloc[0]["r"] < 0.2                              # +2 pips on a 20-pip risk = +0.1R


def test_unfilled_pending_expires():
    path = flat(30)                                              # never reaches 1.0990
    res = run(path, cand(1))
    assert len(res["trades"]) == 0
    assert res["funnel"].get("expired_unfilled") == 1


def test_one_position_per_symbol_guard():
    c = pd.concat([cand(1), cand(2)], ignore_index=True)
    path = flat(3) + [(1.1000, 1.1000, 1.0985, 1.0995)] + flat(6, 1.0995)
    res = run(path, c)
    assert res["funnel"]["orders_placed"] == 1                   # second candidate blocked by the pending-order guard


def test_commission_is_charged_both_sides():
    path = flat(3) + [(1.1000, 1.1000, 1.0985, 1.0995)] + [(1.0995, 1.1052, 1.0995, 1.1050)] + flat(3) \
        + [(1.1050, 1.1052, 1.1048, 1.1050)]
    cfg = SimConfig(commission_per_lot_side=2.5, breakeven=False, monthly_target_pct=None)
    t = Simulator(["EURUSD"], bars(path), SPEC, cand(1), cfg).run()["trades"]
    assert len(t) >= 1
    assert t.iloc[0]["commission"] == pytest.approx(5.0 * t.iloc[0]["volume"])
