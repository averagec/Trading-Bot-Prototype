"""Stage-0 estimator (research/nulltest.py) and verdict rule (research/stage0.py). Synthetic data only."""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from research import nulltest as nt     # noqa: E402
from research import stage0             # noqa: E402


def _trades(rows):
    return pd.DataFrame(rows, columns=["open_T", "symbol", "r", "exit_reason"])


# ── weekly_table ───────────────────────────────────────────────────────────────

def test_weekly_table_groups_by_iso_week_and_symbol_and_drops_end_trades():
    t = _trades([
        ("2021-03-01 10:00", "EURUSD", 1.0, "TP"),       # Monday of week A
        ("2021-03-05 23:30", "EURUSD", -1.0, "SL"),      # Friday of week A
        ("2021-03-07 22:00", "EURUSD", 0.5, "TP"),       # Sunday evening: still week A (ISO week starts Monday)
        ("2021-03-08 00:05", "EURUSD", 2.0, "TP"),       # Monday: week B
        ("2021-03-02 09:00", "GBPUSD", 3.0, "TP"),
        ("2021-03-03 09:00", "GBPUSD", 9.0, "END"),      # open at the end of the data: excluded, as summarize does
    ])
    t["open_T"] = pd.to_datetime(t["open_T"], utc=True)
    w = nt.weekly_table(t).set_index(["week", "symbol"])
    a, b = pd.Timestamp("2021-03-01"), pd.Timestamp("2021-03-08")
    assert w.loc[(a, "EURUSD"), "n"] == 3 and w.loc[(a, "EURUSD"), "sum_r"] == pytest.approx(0.5)
    assert w.loc[(b, "EURUSD"), "n"] == 1 and w.loc[(b, "EURUSD"), "sum_r"] == pytest.approx(2.0)
    assert w.loc[(a, "GBPUSD"), "n"] == 1 and w.loc[(a, "GBPUSD"), "sum_r"] == pytest.approx(3.0)
    assert w["n"].sum() == 5


def test_weekly_table_totals_equal_the_mean_of_the_non_end_trades():
    """summarize() computes expectancy over exit_reason != END; the weekly table must agree to the last trade."""
    rng = np.random.default_rng(0)
    n = 400
    t = pd.DataFrame({"open_T": pd.date_range("2020-01-01", periods=n, freq="9h", tz="UTC"),
                      "symbol": rng.choice(["EURUSD", "USDJPY"], n), "r": rng.normal(0, 1, n),
                      "exit_reason": rng.choice(["TP", "SL", "END"], n, p=[.45, .5, .05])})
    w = nt.weekly_table(t)
    done = t[t["exit_reason"] != "END"]
    assert w["n"].sum() == len(done) < n
    assert w["sum_r"].sum() / w["n"].sum() == pytest.approx(done["r"].mean())


def test_weekly_table_handles_empty_input():
    assert len(nt.weekly_table(pd.DataFrame())) == 0 and len(nt.weekly_table(None)) == 0


# ── paired_skill ───────────────────────────────────────────────────────────────

def _tables(rng, weeks=300, per_week=12, skill=0.0, shared_sd=0.0, seeds=40, symbols=("EURUSD", "XAUUSD")):
    """Actual and null weekly tables. A weekly shock `shared` (sd shared_sd) hits actual and null alike."""
    wk = pd.date_range("2018-01-01", periods=weeks, freq="7D")
    shared = rng.normal(0, shared_sd, weeks)

    def tbl(mu):
        rows = []
        for i, w in enumerate(wk):
            for s in symbols:
                r = rng.normal(mu + shared[i], 1.0, per_week)
                rows.append((w, s, per_week, r.sum()))
        return pd.DataFrame(rows, columns=["week", "symbol", "n", "sum_r"])

    return tbl(skill), {k: tbl(0.0) for k in range(seeds)}


def test_point_estimate_is_actual_mean_minus_mean_of_null_means():
    actual, nulls = _tables(np.random.default_rng(1), skill=0.10)
    e = nt.paired_skill(actual, nulls, n_boot=300)
    want = actual["sum_r"].sum() / actual["n"].sum() - np.mean([v["sum_r"].sum() / v["n"].sum() for v in nulls.values()])
    assert e["skill_r"] == pytest.approx(want)
    assert e["ub95"] == pytest.approx(e["skill_r"] + 1.96 * e["se_skill"])
    assert e["lb_bonf"] == pytest.approx(e["skill_r"] - 2.24 * e["se_skill"])
    assert 0.07 < e["skill_r"] < 0.13 and e["weeks"] == 300 and e["null_seeds"] == 40


def test_pairing_shrinks_the_error_when_actual_and_null_share_weekly_shocks():
    """A weekly price shock common to actual and null cancels in the paired estimate; unpaired it would add variance."""
    actual, nulls = _tables(np.random.default_rng(2), shared_sd=1.0)
    paired_se = nt.paired_skill(actual, nulls, n_boot=400)["se_skill"]
    # the SE of the actual run's mean alone, blocking by week: it contains the shared shock, which the pairing removes
    weekly = actual.groupby("week")[["n", "sum_r"]].sum()
    per_week_mean = (weekly["sum_r"] / weekly["n"]).to_numpy()
    actual_only_se = per_week_mean.std(ddof=1) / np.sqrt(len(per_week_mean))
    assert actual_only_se > 0.05                      # the shock really is in the actual numbers (about 1/sqrt(300))
    assert paired_se < 0.6 * actual_only_se           # and the paired estimate has most of it cancelled


def test_subsetting_filters_the_same_tables():
    actual, nulls = _tables(np.random.default_rng(3), skill=0.0)
    full = nt.paired_skill(actual, nulls, n_boot=200)
    no_gold = nt.paired_skill(actual, nulls, n_boot=200, exclude_symbols=("XAUUSD",))
    only_gold = nt.paired_skill(actual, nulls, n_boot=200, only_symbols=["XAUUSD"])
    assert no_gold["trades"] + only_gold["trades"] == full["trades"]
    y = nt.paired_skill(actual, nulls, n_boot=200, only_years=[2018])
    assert 0 < y["trades"] < full["trades"]
    cut = nt.paired_skill(actual, nulls, n_boot=200, exclude_symbol_years=(("EURUSD", 2018),))
    assert cut["trades"] < full["trades"]


def test_a_true_skill_is_recovered_and_zero_skill_is_not_flagged():
    zero = nt.paired_skill(*_tables(np.random.default_rng(4), skill=0.0), n_boot=400)
    real = nt.paired_skill(*_tables(np.random.default_rng(4), skill=0.15), n_boot=400)
    assert zero["lb_bonf"] < 0 < zero["ub95"]
    assert real["lb_bonf"] > 0 and real["skill_r"] == pytest.approx(0.15, abs=0.04)


def test_degenerate_inputs_return_nan_instead_of_raising():
    e = nt.paired_skill(pd.DataFrame(columns=["week", "symbol", "n", "sum_r"]), {}, n_boot=10)
    assert np.isnan(e["skill_r"]) and np.isnan(e["ub95"])


def test_bootstrap_is_reproducible():
    a, n = _tables(np.random.default_rng(5))
    assert nt.paired_skill(a, n, n_boot=200)["se_skill"] == nt.paired_skill(a, n, n_boot=200)["se_skill"]


# ── the verdict rule ───────────────────────────────────────────────────────────

def _est(skill, se):
    e = {"trades": 4000, "skill_r": skill, "se_skill": se, "ub95": skill + 1.96 * se, "lb_bonf": skill - 2.24 * se}
    return {v: dict(e) for v in stage0.VARIANTS}


def test_kill_needs_every_upper_bound_below_the_threshold():
    assert stage0.decide({"R2": _est(-0.02, 0.015), "R4": _est(-0.03, 0.015)})["verdict"] == "KILL"
    mixed = {"R2": _est(-0.02, 0.015), "R4": _est(-0.03, 0.015)}
    mixed["R4"]["no_gold"] = dict(mixed["R4"]["no_gold"], ub95=0.06)          # one variant above the threshold
    assert stage0.decide(mixed)["verdict"] == "INCONCLUSIVE"


def test_proceed_needs_a_lower_bound_above_zero_not_just_a_high_upper_bound():
    noisy = stage0.decide({"R2": _est(0.0, 0.03), "R4": _est(0.0, 0.03)})          # UB 0.059 >= 0.05, LB < 0
    assert noisy["verdict"] == "INCONCLUSIVE"
    real = stage0.decide({"R2": _est(0.08, 0.02), "R4": _est(0.0, 0.03)})          # LB(R2) = 0.035 > 0
    assert real["verdict"] == "PROCEED" and "R2" in real["reason"]


def test_proceed_takes_precedence_and_nan_is_never_a_kill():
    assert stage0.decide({"R2": _est(0.08, 0.005), "R4": _est(-0.05, 0.01)})["verdict"] == "PROCEED"
    nan = _est(-0.05, 0.01)
    nan["clean"]["ub95"] = float("nan")
    assert stage0.decide({"R2": _est(-0.05, 0.01), "R4": nan})["verdict"] == "INCONCLUSIVE"


def test_estimates_that_went_through_json_as_null_are_inconclusive_not_a_crash():
    """nulltest writes NaN as null via _clean; stage0.main() reads that JSON back, so None must behave like NaN."""
    import json
    from research.run import _clean
    broken = _est(-0.05, 0.01)
    broken["all"]["ub95"] = float("nan")
    broken["all"]["lb_bonf"] = float("nan")
    roundtrip = json.loads(json.dumps(_clean({"R2": _est(-0.05, 0.01), "R4": broken})))
    assert roundtrip["R4"]["all"]["ub95"] is None
    assert stage0.decide(roundtrip)["verdict"] == "INCONCLUSIVE"


def test_missing_or_incomplete_input_is_inconclusive():
    assert stage0.decide({})["verdict"] == "INCONCLUSIVE"
    missing_variant = _est(-0.05, 0.01)
    del missing_variant["clean_no_gold"]
    assert stage0.decide({"R2": _est(-0.05, 0.01), "R4": missing_variant})["verdict"] == "INCONCLUSIVE"
    assert stage0.decide({"R2": {"all": None}, "R4": _est(-0.05, 0.01)})["verdict"] == "INCONCLUSIVE"


def test_a_kill_needs_both_kill_set_runs():
    """One run alone must never kill: the pre-registration's kill set is R2 and R4 together."""
    assert stage0.decide({"R2": _est(-0.05, 0.01)})["verdict"] == "INCONCLUSIVE"


def test_decide_uses_the_module_constants():
    just_below = _est(0.0, 0.0)
    for v in stage0.VARIANTS:
        just_below[v]["ub95"] = nt.KILL_UB_R - 1e-9
        just_below[v]["lb_bonf"] = -0.01
    assert stage0.decide({"R2": just_below, "R4": just_below})["verdict"] == "KILL"
    at = {v: dict(just_below[v], ub95=nt.KILL_UB_R) for v in stage0.VARIANTS}
    assert stage0.decide({"R2": at, "R4": just_below})["verdict"] == "INCONCLUSIVE"
