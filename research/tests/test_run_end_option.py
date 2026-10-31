"""research.run / research.nulltest must pass --end all the way down to prepare_bars, so a DEV-only run physically
cannot read bars from the FINAL window. Everything here is mocked; no data files are read."""

import sys
from pathlib import Path
from unittest import mock

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from research import run as run_mod        # noqa: E402
from research import nulltest as null_mod  # noqa: E402


def _bars():
    t = pd.date_range("2020-01-01", periods=3, freq="5min", tz="UTC")
    return {"M5": pd.DataFrame({"time": t})}


def test_candidate_job_passes_end_to_prepare_bars():
    with mock.patch.object(run_mod, "prepare_bars", return_value=_bars()) as pb, \
            mock.patch.object(run_mod, "build_candidates_cached", return_value=pd.DataFrame()):
        run_mod._cand_job(("EURUSD", "2014-01-01", object(), False, 0.00001, "2021-06-01"))
    assert pb.call_args.kwargs == {"start": "2014-01-01", "end": "2021-06-01", "sentinel_missing": False}


def test_candidate_job_without_end_is_unchanged():
    with mock.patch.object(run_mod, "prepare_bars", return_value=_bars()) as pb, \
            mock.patch.object(run_mod, "build_candidates_cached", return_value=pd.DataFrame()):
        run_mod._cand_job(("EURUSD", "2014-01-01", object(), False, 0.00001))
    assert pb.call_args.kwargs == {"start": "2014-01-01", "end": None, "sentinel_missing": False}


def test_simulate_passes_end_to_prepare_bars():
    with mock.patch.object(run_mod, "prepare_bars", return_value=_bars()) as pb, \
            mock.patch.object(run_mod, "Simulator") as sim:
        run_mod.simulate(["EURUSD"], {}, pd.DataFrame(), object(), "2014-01-01", end="2021-06-01")
    assert pb.call_args.kwargs == {"start": "2014-01-01", "end": "2021-06-01", "sentinel_missing": False}
    sim.return_value.run.assert_called_once()


def test_get_candidates_threads_end_into_every_job():
    jobs = []
    with mock.patch.object(run_mod, "_cand_job", side_effect=lambda j: jobs.append(j) or pd.DataFrame()):
        run_mod.get_candidates(["EURUSD", "GBPUSD"], {"EURUSD": {"point": 1}, "GBPUSD": {"point": 1}}, object(),
                               "2014-01-01", False, 1, end="2021-06-01")
    assert [j[5] for j in jobs] == ["2021-06-01", "2021-06-01"]               # (symbol, start, sig, refresh, point, end, sentinel)
    assert [j[6] for j in jobs] == [False, False]


def test_null_seed_runs_pass_end_to_prepare_bars():
    cands = pd.DataFrame({"direction": ["BUY"], "price": [1.0], "sl_raw": [0.9], "ask": [1.0], "bid": [1.0],
                          "subtype": ["LIMIT"]})
    with mock.patch.object(null_mod.pd, "read_parquet", return_value=cands), \
            mock.patch.object(null_mod, "prepare_bars", return_value=_bars()) as pb, \
            mock.patch.object(null_mod, "Simulator") as sim, \
            mock.patch.object(null_mod, "summarize", return_value={}):
        sim.return_value.run.return_value = {"trades": pd.DataFrame(), "equity": None, "funnel": {}}
        null_mod._run_seeds(([0], "x.parquet", ["EURUSD"], {}, "2014-01-01", mock.Mock(initial_balance=1), "2021-06-01"))
    assert pb.call_args.kwargs == {"start": "2014-01-01", "end": "2021-06-01", "sentinel_missing": False}


def test_cli_exposes_the_end_option(monkeypatch):
    import argparse
    seen = {}
    real = argparse.ArgumentParser.parse_args

    def spy(self, args=None, namespace=None):
        seen["opts"] = {a.dest for a in self._actions}
        raise SystemExit(0)

    monkeypatch.setattr(argparse.ArgumentParser, "parse_args", spy)
    for main in (run_mod.main, null_mod.main):
        with pytest.raises(SystemExit):
            main()
        assert "end" in seen["opts"]
