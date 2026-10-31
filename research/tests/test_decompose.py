"""research/decompose.py: the friction steps are exactly what they claim to be, and the run uses the current harness."""

import sys
from dataclasses import asdict
from pathlib import Path
from unittest import mock

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from research import decompose as dc          # noqa: E402
from research.sim import SimConfig             # noqa: E402

LIVE = SimConfig(fixed_risk_pct=0.25, friday_flatten_utc=20.0)


def changed(a, b):
    return {k for k, v in asdict(a).items() if v != asdict(b)[k]}


def test_cumulative_steps_remove_one_more_friction_each_time():
    steps = dc.build_steps(LIVE)
    names = list(steps["cumulative"])
    assert names[0].startswith("c0") and len(names) == 5
    cfgs = list(steps["cumulative"].values())
    assert cfgs[0] == LIVE
    assert changed(cfgs[0], cfgs[1]) == {"commission_per_lot_side"}
    assert changed(cfgs[1], cfgs[2]) == {"spread_mult", "use_spread_filter"}
    assert changed(cfgs[2], cfgs[3]) == {"breakeven"}
    assert changed(cfgs[3], cfgs[4]) == {"friday_flatten_utc"}
    assert cfgs[4].commission_per_lot_side == 0 and cfgs[4].spread_mult == 0 and cfgs[4].breakeven is False
    assert cfgs[4].friday_flatten_utc is None


def test_one_at_a_time_steps_change_exactly_one_friction_from_the_base():
    steps = dc.build_steps(LIVE)["one_at_a_time"]
    want = {"no commission": {"commission_per_lot_side"}, "no spread": {"spread_mult", "use_spread_filter"},
            "no breakeven": {"breakeven"}, "no Friday flatten": {"friday_flatten_utc"}}
    by_label = {k.split(": ", 1)[1]: v for k, v in steps.items()}
    assert set(by_label) == set(want)
    for label, cfg in by_label.items():
        assert changed(LIVE, cfg) == want[label]


def test_a_base_without_a_friday_flatten_gets_no_flatten_step():
    steps = dc.build_steps(SimConfig())
    assert len(steps["cumulative"]) == 4 and "no Friday flatten" not in " ".join(steps["one_at_a_time"])


def test_main_runs_every_step_through_the_current_harness_with_the_end_date(tmp_path, monkeypatch):
    calls = []

    def fake_run_seeds(args):
        calls.append(args)
        seeds = args[0]
        return [{"seed": s, "expectancy_r": -0.1, "win_rate_pct": 40.0, "trades": 100} for s in seeds], []

    class Pool:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def map(self, fn, jobs): return [fn(j) for j in jobs]

    cands = tmp_path / "c.parquet"
    pd.DataFrame({"x": [1]}).to_parquet(cands)
    monkeypatch.setattr(dc, "_run_seeds", fake_run_seeds)
    monkeypatch.setattr(dc, "ProcessPoolExecutor", Pool)
    monkeypatch.setattr(dc, "load_specs", lambda: {})
    monkeypatch.setattr(dc, "OUT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["decompose", "--cands", str(cands), "--start", "2014-01-01", "--end", "2021-06-01",
                                      "--seeds", "4", "--workers", "2", "--live-settings", "--tag", "t"])
    dc.main()
    assert calls and all(len(a) == 7 for a in calls)                      # the 7-tuple layout of nulltest._run_seeds
    assert all(a[4] == "2014-01-01" and a[6] == "2021-06-01" for a in calls)
    n_steps = len(dc.build_steps(LIVE)["cumulative"]) + len(dc.build_steps(LIVE)["one_at_a_time"])
    assert len({id(a[5]) for a in calls}) >= 1 and len(calls) == n_steps * 2
    out = (tmp_path / "t" / "decomposition.json").read_text()
    assert "c0" in out and "mean_expectancy_r" in out
