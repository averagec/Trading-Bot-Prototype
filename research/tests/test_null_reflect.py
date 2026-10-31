"""research/nulltest.py `reflect`: the symmetric random-direction null (whole trade reflected about the decision-time price)."""

import sys
from pathlib import Path
from unittest import mock

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from research import nulltest as nt   # noqa: E402


def cands():
    # decision-time mid = 1.1000 (bid 1.0999, ask 1.1001). Row 0: BUY LIMIT 20 pips below; row 1: BUY STOP 5 pips above;
    # row 2: SELL LIMIT 12 pips above; row 3: SELL STOP 7 pips below.
    return pd.DataFrame({
        "direction": ["BUY", "BUY", "SELL", "SELL"],
        "price":     [1.0980, 1.1005, 1.1012, 1.0993],
        "sl_raw":    [1.0968, 1.0995, 1.1024, 1.1000],
        "ask":       [1.1001] * 4, "bid": [1.0999] * 4,
        "subtype":   ["LIMIT", "STOP", "LIMIT", "STOP"],
    })


def test_reflection_keeps_every_distance_and_swaps_the_side():
    c = cands()
    r = nt.reflect(c, np.ones(4, bool))
    mid = (c["ask"] + c["bid"]) / 2
    assert list(r["direction"]) == ["SELL", "SELL", "BUY", "BUY"]
    assert np.allclose((r["price"] - mid).abs(), (c["price"] - mid).abs())                   # as far from the market as before
    assert np.allclose((r["sl_raw"] - r["price"]).abs(), (c["sl_raw"] - c["price"]).abs())   # same stop distance
    assert (np.sign(r["price"] - mid) == -np.sign(c["price"] - mid)).all()                   # on the other side of the market


def test_a_far_limit_stays_a_limit_instead_of_becoming_a_far_stop():
    """This is the point of the reflection: nulltest.mirror turns a far BUY LIMIT into a far SELL STOP (a different kind of trade)."""
    c = cands()
    flip = np.ones(4, bool)
    assert list(nt.reflect(c, flip)["subtype"]) == ["LIMIT", "STOP", "LIMIT", "STOP"]
    assert nt.mirror(c, flip)["subtype"].iloc[0] == "STOP"                                   # the old geometry, for contrast


def test_unflipped_rows_are_untouched_and_the_input_is_not_modified():
    c = cands()
    before = c.copy()
    r = nt.reflect(c, np.array([False, True, False, True]))
    pd.testing.assert_frame_equal(c, before)
    assert list(r["direction"]) == ["BUY", "SELL", "SELL", "BUY"]
    assert (r.loc[[0, 2], ["price", "sl_raw", "subtype"]] == c.loc[[0, 2], ["price", "sl_raw", "subtype"]]).all().all()


def test_reflecting_twice_restores_the_original_prices():
    c = cands()
    twice = nt.reflect(nt.reflect(c, np.ones(4, bool)), np.ones(4, bool))
    assert twice["price"].to_numpy() == pytest.approx(c["price"].to_numpy())
    assert twice["sl_raw"].to_numpy() == pytest.approx(c["sl_raw"].to_numpy())
    assert list(twice["direction"]) == list(c["direction"])


def test_seed_workers_use_the_geometry_they_are_told_to():
    cands_df = cands()
    used = []

    def spy(name):
        def f(c, flip):
            used.append(name)
            return c
        return f

    with mock.patch.object(nt.pd, "read_parquet", return_value=cands_df), \
            mock.patch.object(nt, "prepare_bars", return_value={"M5": pd.DataFrame()}), \
            mock.patch.dict(nt.NULL_GEOMETRIES, {"mirror": spy("mirror"), "reflect": spy("reflect")}), \
            mock.patch.object(nt, "Simulator") as sim, mock.patch.object(nt, "summarize", return_value={}):
        sim.return_value.run.return_value = {"trades": pd.DataFrame(), "equity": None, "funnel": {}}
        nt._run_seeds(([0], "x", ["EURUSD"], {}, None, mock.Mock(initial_balance=1), None))                # default
        nt._run_seeds(([0], "x", ["EURUSD"], {}, None, mock.Mock(initial_balance=1), None, "reflect"))
    assert used == ["mirror", "reflect"]


def test_sim_config_builder_only_changes_what_each_flag_says():
    from dataclasses import asdict
    plain = nt.build_sim_config(live_settings=False, clean_conventions=False)
    live = nt.build_sim_config(live_settings=True, clean_conventions=False)
    clean = nt.build_sim_config(live_settings=True, clean_conventions=True)
    changed = lambda a, b: {k for k, v in asdict(a).items() if v != asdict(b)[k]}   # noqa: E731
    assert changed(plain, live) == {"fixed_risk_pct", "friday_flatten_utc"}
    assert changed(live, clean) == {"intrabar", "tp_gap_fill"}
    assert clean.intrabar == "neutral" and clean.tp_gap_fill is True
    assert live.intrabar == "pessimistic" and live.tp_gap_fill is False and plain.fixed_risk_pct is None


def test_cli_exposes_the_geometry_and_convention_options(monkeypatch):
    import argparse
    seen = {}

    def spy(self, args=None, namespace=None):
        seen["opts"] = {a.dest for a in self._actions}
        raise SystemExit(0)

    monkeypatch.setattr(argparse.ArgumentParser, "parse_args", spy)
    with pytest.raises(SystemExit):
        nt.main()
    assert {"null_geometry", "clean_conventions"} <= seen["opts"]
