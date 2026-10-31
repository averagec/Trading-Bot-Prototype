"""Risk-profile simulation (research/quant_risk.py). Synthetic data only: hand-built days with known outcomes."""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from research import quant_risk as qr                 # noqa: E402
from research.montecarlo import ftmo_mc, FtmoRules    # noqa: E402

R1 = qr.Rules(block_len=1)


def src_const(ret_pct, low_pct, n=40, k=1, f0=1.0, risk=0.25):
    """Every day: k identical trades of `ret_pct` (% of balance) and an intraday low of `low_pct`."""
    return qr.DaySource(low=np.full(n, float(low_pct)), rets=np.full((n, k), float(ret_pct)), risk=np.full((n, k), risk),
                        mask=np.ones((n, k), bool), seg=np.zeros(n, int), mean_r=-0.1, f0=f0)


def run(src, prof, H, **kw):
    return qr.profile_mc(src, prof, H, n_paths=50, seed=1, rules=kw.pop("rules", R1), **kw)


def test_one_day_path_is_linear_in_the_multiplier():
    s = src_const(-1.0, -1.2, k=1)
    full = run(s, qr.Profile("a", 1.0), 1)
    half = run(s, qr.Profile("b", 0.5), 1)
    assert full["final_return_pct"]["mean"] == pytest.approx(-1.0)
    assert half["final_return_pct"]["mean"] == pytest.approx(-0.5)


def test_daily_breach_depends_on_the_multiplier():
    s = src_const(-2.0, -6.0)                              # a -6 % floating low at f0
    assert run(s, qr.Profile("f", 1.0), 1)["p_fail_daily"] == 1.0
    assert run(s, qr.Profile("h", 0.5), 1)["p_fail_daily"] == 0.0        # -3 % at half risk: inside the 5 % limit
    assert run(s, qr.Profile("h", 0.5), 1)["final_return_pct"]["mean"] == pytest.approx(-1.0)


def test_static_max_loss_breach_day():
    s = src_const(-2.5, -2.6, n=30)
    r = run(s, qr.Profile("a", 1.0), 10)
    assert r["p_fail_max_loss"] == 1.0 and r["p_fail_daily"] == 0.0
    # day 5 starts at 0.975^4 = 0.9037; low -2.6 % -> 0.8802 <= 0.90 : breach on day 5, after 4 trades
    assert r["months_to_breach_median"] == pytest.approx(5 / 21.0)
    assert r["trades_before_first_event_median"] == 4


def test_halt_rule_stops_trading_and_keeps_the_balance():
    s = src_const(-2.5, -0.5, n=30)
    r = run(s, qr.Profile("h", 1.0, halt_loss=0.06), 10)
    assert r["p_halted_own_rule"] == 1.0 and r["p_any_limit_breach"] == 0.0
    assert r["trades_per_path_mean"] == 3                  # 0.975^3 -> loss 7.3 % >= 6 % after the third trade
    assert r["final_return_pct"]["mean"] == pytest.approx((0.975 ** 3 - 1) * 100)
    assert r["months_to_halt_median"] == pytest.approx(3 / 21.0)


def test_streak_pause_counts_trades():
    s = src_const(-0.1, -0.1, n=40)
    r = run(s, qr.Profile("s", 1.0, streak_n=2, streak_skip_days=1), 6)
    # days 0,1 trade (2 losses -> pause), day 2 skipped, days 3,4 trade, day 5 skipped -> 4 trades
    assert r["trades_per_path_mean"] == 4


def test_streak_resets_on_a_win():
    rets = np.array([[-1.0], [1.0], [-1.0], [-1.0], [1.0], [-1.0]])
    s = qr.DaySource(low=np.full(6, -1.0), rets=rets, risk=np.full((6, 1), .25), mask=np.ones((6, 1), bool), seg=np.zeros(6, int), mean_r=0.0, f0=1.0)
    r = run(s, qr.Profile("s", 1.0, streak_n=3), 6, rules=qr.Rules(block_len=6))     # one block = the whole fixed sequence
    assert r["trades_per_path_mean"] == 6                  # never three losses in a row
    r3 = run(s, qr.Profile("s", 1.0, streak_n=2, streak_skip_days=1), 6, rules=qr.Rules(block_len=6))
    assert r3["trades_per_path_mean"] == 5                 # days 2 and 3 are two losses in a row: day 4 (a win) is skipped


def test_day_stop_skips_the_rest_of_the_day():
    s = src_const(-0.6, -2.0, n=20, k=3)
    r = run(s, qr.Profile("d", 1.0, day_stop=0.01), 4)
    assert r["trades_per_path_mean"] == 8                  # 2 trades/day: after -1.2 % the third is skipped
    r2 = run(s, qr.Profile("d", 1.0), 4)
    assert r2["trades_per_path_mean"] == 12


def test_ladder_halves_risk_below_the_threshold():
    s = src_const(-1.0, -0.5, n=20)
    r = run(s, qr.Profile("l", 1.0, ladder=((0.03, 0.5),)), 6)
    bal = 1.0
    for _ in range(6):
        m = 0.5 if (1 - bal) >= 0.03 - 1e-12 else 1.0
        bal *= 1 - 0.01 * m
    assert r["final_return_pct"]["mean"] == pytest.approx((bal - 1) * 100)
    assert (bal - 1) * 100 > (0.99 ** 6 - 1) * 100


def test_pass_needs_the_target_and_min_days():
    s = src_const(+4.0, -0.2, n=30)
    r = qr.profile_mc(s, qr.Profile("w", 1.0), 10, n_paths=50, seed=1, rules=qr.Rules(block_len=1, min_days=4))
    assert r["p_pass"] == 1.0                              # 1.04^3 = 1.125 >= 1.10 after day 3, but 4 trading days are required
    r2 = qr.profile_mc(s, qr.Profile("w", 1.0), 3, n_paths=50, seed=1, rules=qr.Rules(block_len=1, min_days=4))
    assert r2["p_pass"] == 0.0


def test_matches_ftmo_mc_when_no_rule_is_active():
    rng = np.random.default_rng(3)
    n = 600
    pnl = rng.normal(-0.02, 0.9, n)
    low = np.minimum(pnl, 0) - np.abs(rng.normal(0, 0.4, n))
    days = pd.DataFrame({"pnl_pct": pnl, "low_pct": low, "traded": True})
    ref = ftmo_mc(days, FtmoRules(profit_target_pct=10.0, max_days=90, block_len=5), n_paths=20_000, seed=21)
    s = qr.DaySource(low=low, rets=pnl[:, None], risk=np.full((n, 1), .25), mask=np.ones((n, 1), bool), seg=np.zeros(n, int), mean_r=0.0, f0=1.0)
    mine = qr.profile_mc(s, qr.Profile("x", 1.0), 90, n_paths=20_000, seed=21, rules=qr.Rules(block_len=5))
    assert mine["p_fail_daily"] == pytest.approx(ref["p_fail_daily_loss"], abs=0.02)
    assert mine["p_fail_max_loss"] == pytest.approx(ref["p_fail_max_loss"], abs=0.02)
    assert mine["p_pass"] == pytest.approx(ref["p_pass"], abs=0.02)


def test_blocks_never_straddle_runs():
    seg = np.array([0] * 7 + [1] * 6)
    st = qr.block_starts(seg, 5)
    assert all(seg[s] == seg[s + 4] for s in st) and 3 not in st and 6 not in st and len(st) == 3 + 2


def test_determinism_and_seed_dependence():
    s = src_const(-0.5, -1.0, n=60, k=2)
    a = qr.profile_mc(s, qr.Profile("a", 1.0), 40, n_paths=200, seed=5)
    b = qr.profile_mc(s, qr.Profile("a", 1.0), 40, n_paths=200, seed=5)
    assert a == b


# ── sources and scenarios ───────────────────────────────────────────────────────────────────

def _tiny_run():
    # eq on a Friday (2020-01-03) and the following Monday (2020-01-06); a trade closing on Saturday 2020-01-04 goes to Monday
    idx = pd.to_datetime(["2020-01-03 08:00", "2020-01-03 16:00", "2020-01-06 08:00", "2020-01-06 16:00"], utc=True)
    eq = pd.DataFrame({"T": idx, "balance": [100.0, 99.0, 99.0, 99.5], "equity": [100.0, 98.5, 98.0, 99.5]})
    tr = pd.DataFrame({"open_T": [idx[0], idx[1], idx[1]],
                       "close_T": [idx[1], pd.Timestamp("2020-01-04 10:00", tz="UTC"), idx[3]],
                       "exit_reason": ["SL", "SL", "TP"], "ret_pct": [-1.0, -0.25, 0.5], "risk_pct": [0.25, 0.25, 0.25], "r": [-4.0, -1.0, 2.0]})
    return tr, eq


def test_day_arrays_weekend_close_goes_to_monday():
    tr, eq = _tiny_run()
    a = qr.day_arrays(tr, eq)
    assert a["n_days"] == 2
    assert a["mask"][0].sum() == 1 and a["mask"][1].sum() == 2
    assert a["rets"][1].tolist()[:2] == [-0.25, 0.5]


def test_shift_mean_r_moves_the_mean_exactly():
    tr, eq = _tiny_run()
    s = qr.build_source([qr.day_arrays(tr, eq)])
    assert s.mean_r == pytest.approx((-4 - 1 + 2) / 3)
    s2 = qr.shift_mean_r(s, 0.0)
    # every trade's return moves by delta x risk %, delta = 0 - mean_r
    delta = -s.mean_r
    assert s2.rets[s2.mask].tolist() == pytest.approx((s.rets[s.mask] + delta * 0.25).tolist())
    assert s2.mean_r == 0.0


def test_scale_source_is_linear():
    tr, eq = _tiny_run()
    s = qr.build_source([qr.day_arrays(tr, eq)])
    h = qr.scale_source(s, 0.5)
    assert h.rets.tolist() == (s.rets * 0.5).tolist() and h.f0 == pytest.approx(s.f0 * 0.5)


def test_no_edge_stays_negative_whatever_the_risk_rules():
    """With mean R = 0 and symmetric returns no rule creates a positive mean: the mean final return of every profile is <= ~0."""
    rng = np.random.default_rng(9)
    n = 800
    ret = rng.permutation(np.tile([-0.25, 0.25], n)).reshape(n, 2)           # exactly zero mean
    s = qr.DaySource(low=np.minimum(ret.sum(axis=1), 0) - 0.1, rets=ret, risk=np.full((n, 2), .25), mask=np.ones((n, 2), bool),
                     seg=np.zeros(n, int), mean_r=0.0, f0=0.25)
    plain = qr.profile_mc(s, qr.Profile("a", 0.25), 252, n_paths=4000, seed=2)
    se = (plain["final_return_pct"]["p95"] - plain["final_return_pct"]["p05"]) / 3.29 / np.sqrt(4000)
    # the plain stream's own mean is the (small, positive) edge effect of block sampling a finite source; rules must not add to it beyond noise
    for p in (qr.Profile("b", 0.25, ladder=((0.03, 0.5),), halt_loss=0.08, day_stop=0.01, streak_n=4),
              qr.Profile("c", 0.25, ladder=((0.01, 0.25),), halt_loss=0.04)):
        r = qr.profile_mc(s, p, 252, n_paths=4000, seed=2)
        assert r["final_return_pct"]["mean"] < plain["final_return_pct"]["mean"] + 4 * se


def test_trades_taken_median_counts_all_paths():
    s = src_const(-0.1, -0.1, n=40)
    r = run(s, qr.Profile("m", 1.0), 10)
    assert r["trades_taken_median"] == 10 and r["trades_taken_p10"] == 10
    h = run(src_const(-2.5, -0.5, n=30), qr.Profile("h", 1.0, halt_loss=0.06), 10)
    assert h["trades_taken_median"] == 3                   # halted after three trades: the rest of the horizon takes none
