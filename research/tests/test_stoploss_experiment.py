"""Minimum-stop-floor experiment (research/stoploss_experiment.py). Synthetic data only; no MT5, no cache files."""

import sys
from pathlib import Path
from unittest import mock

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from research import stoploss_experiment as sx      # noqa: E402
from research import nulltest as nt                  # noqa: E402
from research.metrics import summarize               # noqa: E402
from research.sim import Simulator, SimConfig, size_position   # noqa: E402

SPEC = {"EURUSD": dict(digits=5, point=1e-5, contract_size=100000, volume_min=0.01, volume_max=100,
                       volume_step=0.01, stops_level=0, currency_profit="USD"),
        "USDJPY": dict(digits=3, point=1e-3, contract_size=100000, volume_min=0.01, volume_max=100,
                       volume_step=0.01, stops_level=0, currency_profit="JPY"),
        "XAUUSD": dict(digits=2, point=0.01, contract_size=100, volume_min=0.01, volume_max=100,
                       volume_step=0.01, stops_level=0, currency_profit="USD")}
T0 = pd.Timestamp("2020-01-06 00:00", tz="UTC")           # a Monday
REAL_READ_PARQUET = pd.read_parquet


# ── helpers ────────────────────────────────────────────────────────────────────────────────

def walk_bars(days=14, seed=3, vol=0.0003):
    """Random-walk EURUSD M5 bars with a 1-point spread; returns {symbol: frame}."""
    rng = np.random.default_rng(seed)
    n = days * 288
    close = 1.1 + np.cumsum(rng.normal(0, vol, n))
    open_ = np.concatenate([[1.1], close[:-1]])
    hi = np.maximum(open_, close) + np.abs(rng.normal(0, vol / 2, n))
    lo = np.minimum(open_, close) - np.abs(rng.normal(0, vol / 2, n))
    df = pd.DataFrame({"time": [T0 + pd.Timedelta(minutes=5 * i) for i in range(n)], "open": open_, "high": hi,
                       "low": lo, "close": close, "spread_pts": 8.0})
    return {"EURUSD": df}


def candidates(m5, every=30, stop_pips=5.0, spread_pts=8.0):
    """BUY market candidates every `every` bars on weekdays (decision time = bar close), structure stop `stop_pips` away."""
    df = m5["EURUSD"]
    rows = []
    for k in range(40, len(df) - 5, every):
        t = df["time"].iloc[k]
        if t.dayofweek >= 5:
            continue
        px = float(df["close"].iloc[k])
        rows.append({"T": t + pd.Timedelta(minutes=5), "symbol": "EURUSD", "direction": "BUY", "price": px + spread_pts * 1e-5,
                     "sl_raw": px - stop_pips * 1e-4, "atr_factor": 1.0, "size_factor": 1.0, "entry_type": "MARKET",
                     "subtype": "MARKET", "ask": px + spread_pts * 1e-5, "bid": px, "spread_pts": spread_pts, "probability": 70,
                     "checks_passed": 4, "pattern": "x", "fvg_sig": "BUY", "sweep_sig": "NONE", "qt_phase": "London",
                     "trend_strong": True})
    return pd.DataFrame(rows)


@pytest.fixture
def tiny(monkeypatch, tmp_path):
    m5 = walk_bars()
    cands = candidates(m5)
    monkeypatch.setattr(sx, "load_specs", lambda: SPEC)
    monkeypatch.setattr(sx, "prepare_bars", lambda s, start=None, end=None: {"M5": m5["EURUSD"]})
    monkeypatch.setattr(sx.pd, "read_parquet", lambda p, *a, **k: cands)
    sx._STATE.clear()
    yield m5, cands
    sx._STATE.clear()


def task(seed, n_fl=2, end="2021-06-01", n_mirror=2):
    return {"seed": seed, "cand_path": "x.parquet", "symbols": ["EURUSD"], "start": "2014-01-01", "end": end, "n_fl": n_fl,
            "n_mirror": n_mirror}


# ── configuration ──────────────────────────────────────────────────────────────────────────

def test_variants_change_only_the_floor_and_start_from_the_live_settings():
    base = sx.base_config()
    assert base.fixed_risk_pct == 0.25 and base.friday_flatten_utc == 20.0
    for name, (floor, _) in sx.VARIANTS.items():
        cfg = sx.variant_config(name)
        assert cfg.min_sl_pips == floor
        assert {k: v for k, v in vars(cfg).items() if k != "min_sl_pips"} == {k: v for k, v in vars(base).items() if k != "min_sl_pips"}
    assert sx.variant_config("C10").min_sl_pips == SimConfig().min_sl_pips == 10.0        # the control is the live floor
    assert sx.VARIANTS["F15"][0] == 15 and sx.VARIANTS["F20"][0] == 20 and sx.VARIANTS["F30"][0] == 30
    assert sx.TREATMENTS == ["F15", "F20", "F30", "S20"]


def test_frictionless_levels():
    cfg = sx.variant_config("F20")
    l1, l2 = sx.frictionless_config(cfg, "L1"), sx.frictionless_config(cfg, "L2")
    for c in (l1, l2):
        assert c.commission_per_lot_side == 0 and c.spread_mult == 0 and not c.use_spread_filter and c.min_sl_pips == 20
    assert l1.breakeven and l1.friday_flatten_utc == 20.0
    assert not l2.breakeven and l2.friday_flatten_utc is None
    with pytest.raises(ValueError):
        sx.frictionless_config(cfg, "L3")


# ── the candidate set does not depend on the floor ────────────────────────────────────────────

def test_structure_stop_does_not_depend_on_min_sl_pips(monkeypatch):
    from utils import risk_manager as rm_mod
    rng = np.random.default_rng(0)
    c = 1.1 + np.cumsum(rng.normal(0, 3e-4, 80))
    df = pd.DataFrame({"open": c, "high": c + 2e-4, "low": c - 2e-4, "close": c})
    before = rm_mod.RiskManager().find_structure_sl(df, "BUY", "EURUSD"), rm_mod.RiskManager().find_structure_sl(df, "SELL", "EURUSD")
    monkeypatch.setattr(rm_mod, "MIN_SL_PIPS", 30)
    after = rm_mod.RiskManager().find_structure_sl(df, "BUY", "EURUSD"), rm_mod.RiskManager().find_structure_sl(df, "SELL", "EURUSD")
    assert before == after and None not in before


def test_signals_module_never_reads_the_floor():
    src = (ROOT / "research" / "signals.py").read_text(encoding="utf-8")
    assert "MIN_SL" not in src and "min_sl" not in src


# ── spread-multiple floor == the sim's own widening ──────────────────────────────────────────────

def _cand_row(**kw):
    r = {"symbol": "EURUSD", "direction": "BUY", "price": 1.1000, "sl_raw": 1.0995, "spread_pts": 20.0}
    r.update(kw)
    return r


def test_spread_floor_widening_equals_the_simulators_widening():
    cands = pd.DataFrame([_cand_row(), _cand_row(direction="SELL", price=1.1000, sl_raw=1.1004)])
    w = sx.apply_spread_floor(cands, SPEC, 20.0, 10.0)           # floor = 20 x 2 pips = 40 pips
    for i, d in enumerate(["BUY", "SELL"]):
        sl_pips = abs(w.loc[i, "price"] - w.loc[i, "sl_raw"]) / 1e-4
        assert sl_pips == pytest.approx(40.0)
        a = size_position(SPEC["EURUSD"], "EURUSD", d, 1.1, w.loc[i, "sl_raw"], 100000, 1.0, 1.0,
                          SimConfig(min_sl_pips=10.0, fixed_risk_pct=0.25, risk_per_trade_pct=0.25))
        b = size_position(SPEC["EURUSD"], "EURUSD", d, 1.1, cands.loc[i, "sl_raw"], 100000, 1.0, 1.0,
                          SimConfig(min_sl_pips=40.0, fixed_risk_pct=0.25, risk_per_trade_pct=0.25))
        for k in ("sl", "tp", "volume", "sl_pips", "risk_amount"):
            assert a[k] == pytest.approx(b[k])


def test_spread_floor_never_narrows_and_leaves_scalar_bound_rows_alone():
    cands = pd.DataFrame([
        _cand_row(sl_raw=1.0900, spread_pts=20.0),               # 100-pip stop, floor 40 -> unchanged
        _cand_row(sl_raw=1.0995, spread_pts=4.0),                # floor 8 pips < 10 -> left to the sim's scalar floor
        _cand_row(sl_raw=1.0995, spread_pts=70.0)])              # floor 140 pips -> widened
    w = sx.apply_spread_floor(cands, SPEC, 20.0, 10.0)
    assert w.loc[0, "sl_raw"] == cands.loc[0, "sl_raw"] and w.loc[1, "sl_raw"] == cands.loc[1, "sl_raw"]
    assert w.loc[2, "price"] - w.loc[2, "sl_raw"] == pytest.approx(0.0140)
    assert sx.apply_spread_floor(cands, SPEC, None, 10.0) is cands


def test_spread_floor_uses_gold_and_jpy_pip_conventions():
    assert sx.pip_size(SPEC["XAUUSD"]) == pytest.approx(0.01) and sx.pip_size(SPEC["USDJPY"]) == pytest.approx(0.01)
    assert sx.pip_size(SPEC["EURUSD"]) == pytest.approx(1e-4)
    gold = pd.DataFrame([_cand_row(symbol="XAUUSD", price=1500.0, sl_raw=1497.0, spread_pts=30.0)])   # spread 0.30, stop 3.00
    w = sx.apply_spread_floor(gold, SPEC, 20.0, 10.0)            # floor 6.00 dollars
    assert 1500.0 - w.loc[0, "sl_raw"] == pytest.approx(6.0)


# ── window guard ───────────────────────────────────────────────────────────────────────────────

def test_assert_dev_window_rejects_bars_or_candidates_at_or_after_the_end():
    ok = pd.DataFrame({"time": pd.date_range("2021-05-31 23:50", periods=2, freq="5min", tz="UTC")})
    sx.assert_dev_window(pd.DataFrame({"T": [pd.Timestamp("2021-05-31", tz="UTC")]}), {"EURUSD": ok}, "2021-06-01")
    bad = pd.DataFrame({"time": pd.date_range("2021-05-31 23:55", periods=3, freq="5min", tz="UTC")})
    with pytest.raises(AssertionError):
        sx.assert_dev_window(pd.DataFrame(), {"EURUSD": bad}, "2021-06-01")
    with pytest.raises(AssertionError):
        sx.assert_dev_window(pd.DataFrame({"T": [pd.Timestamp("2021-06-01", tz="UTC")]}), {"EURUSD": ok}, "2021-06-01")


def test_dev_end_date_reaches_prepare_bars(monkeypatch):
    bars = {"M5": pd.DataFrame({"time": pd.date_range("2020-01-01", periods=3, freq="5min", tz="UTC")})}
    monkeypatch.setattr(sx, "load_specs", lambda: {})
    monkeypatch.setattr(sx.pd, "read_parquet", lambda *a, **k: pd.DataFrame())
    sx._STATE.clear()
    with mock.patch.object(sx, "prepare_bars", return_value=bars) as pb:
        sx._load("x.parquet", ["EURUSD", "GBPUSD"], "2014-01-01", "2021-06-01")
    sx._STATE.clear()
    assert pb.call_count == 2
    assert all(c.kwargs == {"start": "2014-01-01", "end": "2021-06-01"} for c in pb.call_args_list)


def test_run_refuses_a_final_window_end_and_a_reused_tag(tmp_path, monkeypatch):
    with pytest.raises(SystemExit):
        sx.main(["run", "--tag", "t", "--end", "2021-06-02"])
    with pytest.raises(SystemExit):
        sx.main(["run", "--tag", "t", "--end", ""])
    monkeypatch.setattr(sx, "STOP_OUT", tmp_path)
    (tmp_path / "taken").mkdir()
    with pytest.raises(SystemExit):
        sx.main(["run", "--tag", "taken"])


def test_cli_end_default_is_the_dev_end():
    import argparse
    seen = {}

    def spy(self, args=None, namespace=None):
        seen["defaults"] = {a.dest: a.default for sp in self._subparsers._group_actions for a in sp.choices["run"]._actions}
        raise SystemExit(0)

    with mock.patch.object(argparse.ArgumentParser, "parse_args", spy), pytest.raises(SystemExit):
        sx.main([])
    assert seen["defaults"]["end"] == "2021-06-01" and seen["defaults"]["seeds"] == 60


# ── simulations with the real Simulator on a synthetic path ──────────────────────────────────────

def test_wider_floor_widens_the_stop_shrinks_volume_and_commission_in_r():
    m5 = walk_bars()
    c = candidates(m5, stop_pips=4.0)
    tr = {}
    for name in ("C10", "F20", "F30"):
        tr[name] = Simulator(["EURUSD"], m5, SPEC, c, sx.variant_config(name)).run()["trades"]
    st = {k: sx.enrich(v, SPEC) for k, v in tr.items()}
    for name, f in (("C10", 10), ("F20", 20), ("F30", 30)):
        assert np.allclose(st[name]["stop_pips"], f, atol=0.1)
    # same first order, lots scale with 1/stop (risk is fixed), commission in R scales with 1/stop
    first = {k: v.sort_values("placed_T").iloc[0] for k, v in tr.items()}
    assert first["C10"]["volume"] / first["F20"]["volume"] == pytest.approx(2.0, rel=0.05)
    assert first["C10"]["volume"] / first["F30"]["volume"] == pytest.approx(3.0, rel=0.05)
    cm = {k: v["comm_r"].mean() for k, v in st.items()}
    assert cm["C10"] / cm["F20"] == pytest.approx(2.0, rel=0.1) and cm["C10"] / cm["F30"] == pytest.approx(3.0, rel=0.1)


def test_floor_without_effect_when_structure_stop_is_already_wider():
    m5 = walk_bars()
    c = candidates(m5, stop_pips=35.0)
    a = Simulator(["EURUSD"], m5, SPEC, c, sx.variant_config("C10")).run()["trades"]
    b = Simulator(["EURUSD"], m5, SPEC, c, sx.variant_config("F30")).run()["trades"]
    assert len(a) == len(b) and (a["r"].to_numpy() == pytest.approx(b["r"].to_numpy()))


def test_sim_row_matches_summarize():
    m5 = walk_bars()
    cfg = sx.variant_config("C10")
    res = Simulator(["EURUSD"], m5, SPEC, candidates(m5), cfg).run()
    row, s = sx.sim_row(res, cfg.initial_balance), summarize(res["trades"], res["equity"], cfg.initial_balance)
    assert row["trades"] == s["trades"] > 5
    for mine, theirs in (("expectancy_r", "expectancy_r"), ("win_rate_pct", "win_rate_pct"), ("profit_factor", "profit_factor"),
                         ("return_pct", "return_pct"), ("max_dd_pct", "max_dd_equity_pct"), ("avg_hold_min", "avg_hold_min"),
                         ("commission_in_r", "commission_in_r")):
        assert row[mine] == pytest.approx(s[theirs]), mine


def test_worker_null_rows_equal_nulltest_run_seeds_rows_for_both_geometries(tiny):
    m5, cands = tiny
    out = sx._work(task(1))
    for kind, geometry in (("N", "reflect"), ("NM", "mirror")):
        mine = next(r for r in out["rows"] if r["variant"] == "C10" and r["kind"] == kind)
        with mock.patch.object(nt.pd, "read_parquet", return_value=cands),                 mock.patch.object(nt, "prepare_bars", return_value={"M5": m5["EURUSD"]}):
            rows, weekly = nt._run_seeds(([1], "x", ["EURUSD"], SPEC, "2014-01-01", sx.variant_config("C10"), "2021-06-01", geometry))
        for k in ("trades", "expectancy_r", "profit_factor", "return_pct", "win_rate_pct", "buy_share"):
            assert mine[k] == pytest.approx(rows[0][k]), (kind, k)
        w = next(x for x in out["weekly"] if x["variant"].iloc[0] == "C10" and x["kind"].iloc[0] == kind)
        pd.testing.assert_frame_equal(w[["week", "symbol", "n", "sum_r"]].reset_index(drop=True),
                                      weekly[0][["week", "symbol", "n", "sum_r"]].reset_index(drop=True))


def test_worker_seed_layout_and_pairing(tiny):
    out = sx._work(task(0, n_fl=2, n_mirror=2))
    kinds = {(r["kind"], r["variant"]) for r in out["rows"]}
    assert {k for k, _ in kinds} == {"N", "NM", "L1", "L2"} and len(kinds) == 4 * len(sx.VARIANTS)
    assert {r["kind"] for r in sx._work(task(1, n_fl=1, n_mirror=2))["rows"]} == {"N", "NM"}
    assert {r["kind"] for r in sx._work(task(5, n_fl=2, n_mirror=2))["rows"]} == {"N"}       # beyond the extra seeds: reflect only
    act = sx._work(task(None))
    assert {r["kind"] for r in act["rows"]} == {"A"} and all(r["seed"] == -1 for r in act["rows"])
    assert {m["variant"] for m in act["matched"]} == set(sx.TREATMENTS)
    # one flip per seed: N (reflect) and NM (mirror) flip the same candidates, and every floor sees the same flips
    seen = []
    real = sx.Simulator

    def spy(symbols, m5_, specs_, c, cfg):
        seen.append((cfg.min_sl_pips, cfg.commission_per_lot_side, c.copy()))
        return real(symbols, m5_, specs_, c, cfg)

    with mock.patch.object(sx, "Simulator", spy):
        sx._work(task(1, n_fl=2, n_mirror=5))
    n_calls, nm_calls = seen[:len(sx.VARIANTS)], seen[len(sx.VARIANTS):2 * len(sx.VARIANTS)]
    assert len({tuple(c["direction"]) for _, _, c in n_calls + nm_calls}) == 1
    assert "SELL" in n_calls[0][2]["direction"].values and "BUY" in n_calls[0][2]["direction"].values
    orig = tiny[1]
    flipped = (n_calls[0][2]["direction"] != orig["direction"]).to_numpy()
    mid = ((orig["ask"] + orig["bid"]) / 2).to_numpy()
    assert flipped.any()
    # reflect: the whole trade is reflected about the mid; mirror: entry price kept
    assert np.allclose(n_calls[0][2]["price"].to_numpy()[flipped], (2 * mid - orig["price"].to_numpy())[flipped])
    assert np.allclose(nm_calls[0][2]["price"].to_numpy(), orig["price"].to_numpy())
    # the frictionless kinds use reflect, with the costs off
    fl = [x for x in seen if x[1] == 0.0]
    assert len(fl) == 2 * len(sx.VARIANTS)
    assert np.allclose(fl[0][2]["price"].to_numpy()[flipped], n_calls[0][2]["price"].to_numpy()[flipped])


def test_worker_rejects_a_candidate_after_the_end(tiny):
    with pytest.raises(AssertionError):
        sx._work(task(0, end="2020-01-10"))        # bars and candidates run to 2020-01-19


# ── measurements on hand-built trades ──────────────────────────────────────────────────────────────

def _trades(rows):
    cols = ["symbol", "subtype", "exit_reason", "r", "entry", "sl0", "spread_pts", "commission", "risk_usd", "gross_pnl",
            "hold_min", "placed_T"]
    return pd.DataFrame(rows, columns=cols)


def test_composition_counts_pinned_fill_bar_stops_and_excludes_end_trades():
    t = _trades([
        ("EURUSD", "LIMIT", "SL", -1.0, 1.1000, 1.0990, 10, 5.0, 100.0, -100.0, 0.0, T0),            # 10 pips: pinned, fill-bar stop
        ("EURUSD", "LIMIT", "TP", 3.0, 1.1000, 1.0985, 10, 5.0, 100.0, 305.0, 60.0, T0),             # 15 pips: not pinned
        ("EURUSD", "STOP", "END", 9.0, 1.1000, 1.0990, 10, 5.0, 100.0, 900.0, 60.0, T0)])            # open at the end: ignored
    t["r"] = (t["gross_pnl"] - t["commission"]) / t["risk_usd"]
    c = sx.composition(t, SPEC, 10.0, None).set_index(["subtype", "exit_reason"])
    assert c["n"].sum() == 2 and ("STOP", "END") not in c.index
    assert c.loc[("LIMIT", "SL"), "n_pinned"] == 1 and c.loc[("LIMIT", "TP"), "n_pinned"] == 0
    assert c.loc[("LIMIT", "SL"), "n_fillbar_stop"] == 1 and c.loc[("LIMIT", "TP"), "n_fillbar_stop"] == 0
    assert c.loc[("LIMIT", "SL"), "sum_comm_r"] == pytest.approx(0.05)
    assert c.loc[("LIMIT", "SL"), "sum_spread_r"] == pytest.approx(10 * 1e-5 / 0.0010)       # entry spread / stop
    assert c["sum_r"].sum() == pytest.approx(c["sum_gross_r"].sum() - c["sum_comm_r"].sum())


def test_composition_pinned_flag_follows_the_per_trade_spread_floor():
    t = _trades([("EURUSD", "LIMIT", "SL", -1.0, 1.1000, 1.0960, 20, 5.0, 100.0, -100.0, 5.0, T0)])    # 40 pips = 20 x 2 pips
    assert sx.composition(t, SPEC, 10.0, 20.0)["n_pinned"].iloc[0] == 1
    assert sx.composition(t, SPEC, 10.0, None)["n_pinned"].iloc[0] == 0


def test_matched_split_separates_common_and_unique_orders():
    t1, t2, t3 = T0, T0 + pd.Timedelta(hours=1), T0 + pd.Timedelta(hours=2)
    def tr(rows):
        return pd.DataFrame(rows, columns=["symbol", "placed_T", "r", "exit_reason"])
    c = tr([("EURUSD", t1, -1.0, "SL"), ("EURUSD", t2, 3.0, "TP"), ("EURUSD", t3, 0.2, "END")])
    v = tr([("EURUSD", t1, 0.1, "BE"), ("EURUSD", t2, 3.0, "TP"), ("GBPUSD", t3, -1.0, "SL")])
    s, trn = sx.matched_split(v, c)
    assert (s["n_common"], s["n_only_v"], s["n_only_c"]) == (2, 1, 0)                     # the END trade is not an order
    assert s["sum_v_common"] == pytest.approx(3.1) and s["sum_c_common"] == pytest.approx(2.0) and s["sum_only_v"] == pytest.approx(-1.0)
    row = trn.set_index(["exit_reason_c", "exit_reason_v"]).loc[("SL", "BE")]
    assert row["n"] == 1 and row["sum_dr"] == pytest.approx(1.1)


def test_mix_decomposition_is_an_exact_identity():
    rows = []
    rng = np.random.default_rng(0)
    for v, shift in (("C10", 0.0), ("F20", 0.05)):
        for reason, share, m in (("SL", 0.6, -1.0), ("TP", 0.2, 3.0), ("BE", 0.2, 0.1)):
            n = 100 * share * 10
            rows.append({"kind": "N", "variant": v, "seed": 0, "symbol": "EURUSD", "subtype": "LIMIT", "exit_reason": reason, "n": n,
                         "sum_r": n * (m + shift), "sum_r2": 0, "sum_comm_r": n * 0.05 / (1 + (v == "F20")), "sum_gross_r": 0,
                         "sum_spread_r": 0, "sum_stop_pips": 0, "n_pinned": 0, "sum_hold": 0, "n_fillbar_stop": 0})
    comp = pd.DataFrame(rows)
    comp["sum_gross_r"] = comp["sum_r"] + comp["sum_comm_r"]
    d = sx.mix_decomposition(comp, "F20")
    assert d["mix_effect"] + d["payoff_effect"] == pytest.approx(d["delta_mean_r"])
    assert d["delta_commission_r"] + d["delta_gross_r"] == pytest.approx(d["delta_mean_r"])
    assert d["delta_mean_r"] == pytest.approx(0.05)


# ── estimator ──────────────────────────────────────────────────────────────────────────────────────

def _weekly(rng, weeks=120, seeds=8, skill=0.1):
    wk = pd.date_range("2018-01-01", periods=weeks, freq="7D")

    def tbl(mu):
        return pd.DataFrame([(w, s, 10, rng.normal(mu * 10, 3.0)) for w in wk for s in ("EURUSD", "GBPUSD")],
                            columns=["week", "symbol", "n", "sum_r"])
    return tbl(skill), {k: tbl(0.0) for k in range(seeds)}


def test_generic_contrast_reproduces_paired_skill():
    actual, nulls = _weekly(np.random.default_rng(2))
    want = nt.paired_skill(actual, nulls, n_boot=400)
    parts = [actual.assign(kind="A", variant="C10", seed=-1)] + [g.assign(kind="N", variant="C10", seed=k) for k, g in nulls.items()]
    weekly = pd.concat(parts, ignore_index=True)
    weeks = pd.Index(sorted(weekly["week"].unique()))
    got = sx.contrast(sx.Boot(len(weeks), 400), sx.build_sets(weekly, weeks), [(("C10", "A"), 1), (("C10", "N"), -1)])
    assert got["est"] == pytest.approx(want["skill_r"]) and got["se"] == pytest.approx(want["se_skill"])


def test_contrast_of_identical_runs_is_exactly_zero_with_zero_se():
    actual, nulls = _weekly(np.random.default_rng(4))
    weekly = pd.concat([actual.assign(kind="N", variant=v, seed=0) for v in ("C10", "F20")], ignore_index=True)
    weeks = pd.Index(sorted(weekly["week"].unique()))
    got = sx.contrast(sx.Boot(len(weeks), 200), sx.build_sets(weekly, weeks), [(("F20", "N"), 1), (("C10", "N"), -1)])
    assert got["est"] == 0 and got["se"] == 0


def test_subsetting_removes_gold():
    actual, _ = _weekly(np.random.default_rng(4))
    weekly = actual.assign(kind="N", variant="C10", seed=0)
    weeks = pd.Index(sorted(weekly["week"].unique()))
    full = sx.build_sets(weekly, weeks)[("C10", "N")][0].sum()
    no_gbp = sx.build_sets(weekly, weeks, exclude_symbols=("GBPUSD",))[("C10", "N")][0].sum()
    assert no_gbp == full / 2


# ── decision rule ────────────────────────────────────────────────────────────────────────────────────

def test_cost_and_real_effect_classification():
    assert sx.cost_effect(0.05, 0.005) == "REDUCES"
    assert sx.cost_effect(0.05, 0.03) == "NONE"            # 0.05 - 2.24 * 0.03 < 0
    assert sx.cost_effect(0.01, 0.001) == "NONE"           # below the size threshold
    assert sx.cost_effect(-0.05, 0.005) == "INCREASES"
    assert sx.real_effect(0.06, 0.01) == "HELPS" and sx.real_effect(-0.06, 0.01) == "HURTS"
    assert sx.real_effect(0.03, 0.025) == "NEUTRAL (noise)" and sx.real_effect(0.0, 0.01) == "NEUTRAL"


def test_verdict_and_nomination():
    assert sx.verdict("REDUCES", "NEUTRAL") == "COST-ONLY" and sx.verdict("REDUCES", "NEUTRAL (noise)") == "COST-ONLY"
    assert sx.verdict("REDUCES", "HELPS") == "HELPS" and sx.verdict("REDUCES", "HURTS") == "HURTS"
    assert sx.verdict("NONE", "NEUTRAL") == "NEUTRAL"
    assert sx.nominate("HELPS", 0.9, 5) and not sx.nominate("HELPS", 0.7, 6) and not sx.nominate("HELPS", 1.0, 4)
    assert not sx.nominate("NEUTRAL", 1.0, 8)


# ── end to end on synthetic data ──────────────────────────────────────────────────────────────────────

def test_run_and_analyse_end_to_end(tiny, tmp_path, monkeypatch):
    parts = [sx._work(task(sd)) for sd in [None, 0, 1, 2, 3]]
    prov = {"n_seeds": 4, "n_fl_seeds": 2, "n_mirror_seeds": 2}
    monkeypatch.setattr(sx.pd, "read_parquet", REAL_READ_PARQUET)      # the fixture patched the global pandas function
    sx.save_outputs(parts, tmp_path, prov)
    res = sx.analyse(tmp_path, n_boot=60, small_boot=30)
    assert set(res["variants"]) == set(sx.VARIANTS)
    f20 = res["variants"]["F20"]
    for k in ("D_null", "D_act", "D_skill", "skill", "cost_effect", "real_effect", "verdict", "null_decomposition",
              "matched_null", "by_symbol", "by_year", "no_gold"):
        assert k in f20, k
    d = f20["null_decomposition"]
    assert d["mix_effect"] + d["payoff_effect"] == pytest.approx(d["delta_mean_r"])
    # wider floors raise the share of stops that sit at the floor only for pinned trades: the 5-pip structure stops are all pinned
    assert res["variants"]["C10"]["actual_pooled"]["pinned_pct"] == pytest.approx(100.0)
    assert res["variants"]["F30"]["actual_pooled"]["stop_pips"] == pytest.approx(30.0, abs=0.2)
    assert f20["D_null"]["est"] == pytest.approx(f20["null"]["mean_r"] - res["variants"]["C10"]["null"]["mean_r"], abs=0.02)
    assert f20["skill"]["est"] == pytest.approx(f20["actual"]["expectancy_r"] - f20["null"]["mean_r"], abs=0.02)
    assert res["reproduction"]["ok"] in (True, False) and res["reproduction"]["geometry"] == "mirror"
    for k in ("null_mirror", "D_null_mirror", "skill_vs_mirror", "null_decomposition_mirror"):
        assert k in f20, k
    assert res["variants"]["C10"]["null_mirror"]["n_seeds"] == 2
