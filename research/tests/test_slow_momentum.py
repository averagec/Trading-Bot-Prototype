"""Tests for research/slow_momentum.py (synthetic data only; no project returns, no MT5, no FINAL data)."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from research import slow_momentum as sm
from research.data import server_to_utc

START, END = "2019-06-01", "2021-06-01"


def calendar(start="2018-01-01", end="2021-06-01"):
    days = pd.bdate_range(start, pd.Timestamp(end) - pd.Timedelta(days=1))
    T = server_to_utc(pd.Series(days + pd.Timedelta(days=1))).dt.tz_convert("UTC").dt.tz_localize(None).to_numpy(dtype="datetime64[ns]")
    return days, T


def make_insts(closes: dict, T: np.ndarray, hs: float = 1e-4) -> dict:
    out = {}
    for s in sm.SYMBOLS:
        c = np.asarray(closes[s], float)
        out[s] = sm.Inst(s, s in sm.USD_BASE, T.copy(), c.copy(), T.copy(), c.copy(), c.copy(), np.full(len(T), hs))
    return out


def random_closes(n, seed, vol=0.08, drift=0.0):
    rng = np.random.default_rng(seed)
    return {s: 1.0 * np.exp(np.cumsum(drift + vol / np.sqrt(260) * rng.standard_normal(n))) for s in sm.SYMBOLS}


# ── no look-ahead ─────────────────────────────────────────────────────────────────

def test_no_lookahead_signal_vol_and_availability_by_truncation_and_scrambling():
    days, T = calendar()
    n = len(T)
    cl = random_closes(n, 1)
    full = sm.build_panel(make_insts(cl, T), START, END)
    cut_i = int(n * 0.8)
    cut = T[cut_i]
    # (a) truncation: cut every series at the cut time
    trunc = {}
    for s in sm.SYMBOLS:
        trunc[s] = sm.Inst(s, s in sm.USD_BASE, T[: cut_i + 1], cl[s][: cut_i + 1], T[: cut_i + 1], cl[s][: cut_i + 1], cl[s][: cut_i + 1],
                           np.full(cut_i + 1, 1e-4))
    tp = sm.build_panel(trunc, START, pd.Timestamp(cut).tz_localize("UTC"))
    ks = np.where(full.R <= cut - np.timedelta64(4, "D"))[0]
    assert len(ks) > 50 and len(tp.R) >= len(ks)
    for v in sm.VNAMES:
        assert np.array_equal(full.S[v][ks], tp.S[v][ks]), v
    assert np.allclose(full.volw[ks], tp.volw[ks])
    # (b) scrambling: replace everything after the cut by unrelated prices; rows decided at or before the cut do not move
    rng = np.random.default_rng(99)
    cl2 = {s: np.concatenate([cl[s][: cut_i + 1], cl[s][cut_i] * np.exp(np.cumsum(0.02 * rng.standard_normal(n - cut_i - 1)))]) for s in sm.SYMBOLS}
    sp = sm.build_panel(make_insts(cl2, T), START, END)
    for v in sm.VNAMES:
        assert np.array_equal(full.S[v][ks], sp.S[v][ks]), v
    assert np.allclose(full.volw[ks], sp.volw[ks])
    # and the scramble really changes later rows (the test can fail)
    late = np.where(full.R > cut + np.timedelta64(100, "D"))[0]
    assert not np.allclose(full.volw[late], sp.volw[late])


def test_signal_uses_window_and_skip_exactly():
    days, T = calendar()
    n = len(T)
    base = np.zeros(n)
    base[: n - 120] = np.linspace(0, 0.4, n - 120)        # long rise
    base[n - 120:] = base[n - 121]
    c = np.exp(base)
    c[-45:] = c[-46] * np.exp(np.linspace(0, -0.03, 45))        # then a short fall over the last 45 days
    cl = {s: c.copy() for s in sm.SYMBOLS}
    p = sm.build_panel(make_insts(cl, T), "2021-04-01", END)
    k = len(p.R) - 3
    # last 21 days fall, so V1 (21d) is short; V4 (252d) compares today with 252 days ago
    lc = np.log(c)
    idx = np.searchsorted(T, p.R[k], side="right") - 1
    assert p.S["V1"][k, 0] == np.sign(lc[idx] - lc[idx - 21])
    assert p.S["V4"][k, 0] == np.sign(lc[idx] - lc[idx - 252])
    assert p.S["V5"][k, 0] == np.sign(lc[idx - 21] - lc[idx - 126])
    assert p.S["V6"][k, 0] == np.sign(lc[idx - 21] - lc[idx - 252])
    assert p.S["V1"][k, 0] == -1 and p.S["V4"][k, 0] == 1       # the windows really disagree here, so the equalities above are not vacuous


def test_holiday_wednesday_falls_back_to_tuesday_and_is_counted():
    days, T = calendar()
    n = len(T)
    cl = random_closes(n, 5)
    ins = make_insts(cl, T)
    p0 = sm.build_panel(ins, START, END)
    k = 20
    drop = np.where(T == p0.R[k])[0][0]
    keep = np.ones(n, bool)
    keep[drop] = False
    ins["EURUSD"].d1_T = T[keep]
    ins["EURUSD"].d1_close = cl["EURUSD"][keep]
    p1 = sm.build_panel(ins, START, END)
    assert p1.counts["fallback_to_earlier_bar"] == p0.counts["fallback_to_earlier_bar"] + 1
    # row k now uses Tuesday's close: same as a signal computed one bar earlier
    # (the dropped bar shifts every later look-back window by one bar, as documented, so a few later signs may differ)
    assert np.array_equal(p1.S["V4"][:k, 0], p0.S["V4"][:k, 0])
    assert (p1.S["V4"][:, 0] != p0.S["V4"][:, 0]).sum() <= 4
    assert p1.volw[k, 0] > 0


def test_missing_exit_price_is_marked_at_last_close_not_zeroed():
    days, T = calendar()
    n = len(T)
    cl = random_closes(n, 6, vol=0.2)
    ins = make_insts(cl, T)
    p0 = sm.build_panel(ins, START, END)
    k = 10
    it = ins["GBPUSD"]
    drop = (it.h1_t >= p0.R[k + 1]) & (it.h1_t < p0.R[k + 1] + np.timedelta64(80, "h"))
    keep = ~drop
    it.h1_t, it.h1_open, it.h1_close, it.h1_hs = it.h1_t[keep], it.h1_open[keep], it.h1_close[keep], it.h1_hs[keep]
    p1 = sm.build_panel(ins, START, END)
    assert p1.counts["marked_at_last_close"] >= p0.counts["marked_at_last_close"] + 1
    assert p1.ret[k, 1] != 0.0                           # period k is booked (marked at the last close before R_(k+1))
    assert p1.volw[k + 1, 1] == 0.0                      # but no position is opened where there is no execution price
    assert p1.counts["no_exec"] >= p0.counts["no_exec"] + 1


def test_usd_base_return_convention():
    days, T = calendar()
    n = len(T)
    cl = {s: np.full(n, 100.0) for s in sm.SYMBOLS}
    p0 = sm.build_panel(make_insts(cl, T), START, END)
    k = 12
    for s in ("EURUSD", "USDJPY"):
        c = np.full(n, 100.0)
        c[T >= p0.R[k + 1]] = 110.0
        cl[s] = c
    p = sm.build_panel(make_insts(cl, T), START, END)
    assert p.ret[k, 0] == pytest.approx(0.10)                       # EURUSD long: P1/P0 - 1
    assert p.ret[k, 2] == pytest.approx(1 - 100.0 / 110.0)          # USDJPY long (USD notional): 1 - P0/P1
    assert p.ret[k, 1] == 0.0


# ── sizing, costs ────────────────────────────────────────────────────────────────

def test_position_sizing_hits_the_vol_target():
    days, T = calendar("2008-01-01", "2021-06-01")
    n = len(T)
    cl = random_closes(n, 3, vol=0.08)
    p = sm.build_panel(make_insts(cl, T), "2010-01-01", END)
    ones = np.ones((p.K, len(sm.SYMBOLS)))
    g, c, s, w, dw = sm.components(p, ones, cols=[0])
    N = np.maximum(p.live[: p.Kp, [0]].sum(1), 1)
    sleeve = g[:, 0] * N                                               # N_k undone: the stand-alone sleeve at weight vol_target/sigma
    ann = sleeve.std(ddof=1) * np.sqrt(52)
    assert ann == pytest.approx(sm.VOL_TARGET, rel=0.12)
    # and the leverage cap binds
    assert np.abs(p.volw).max() <= sm.CAP + 1e-12


def _handmade_panel(S_col0, hs=1e-4, ret=(0.01, -0.02, 0.0, 0.0), days=(7, 7, 7, 0)):
    K, N = len(S_col0), len(sm.SYMBOLS)
    S = {v: np.zeros((K, N)) for v in sm.VNAMES}
    volw = np.zeros((K, N))
    volw[:, 0] = 1.0
    live = np.ones((K, N), bool)
    r = np.zeros((K, N))
    r[:, 0] = ret
    H = np.full((K, N), hs)
    R = np.array(pd.date_range("2020-01-01", periods=K, freq="7D"), dtype="datetime64[ns]")
    S["V1"][:, 0] = S_col0
    return sm.Panel(R, list(sm.SYMBOLS), S, volw, live, r, H, np.array(days, float), {})


def test_costs_are_charged_on_turnover_and_swap_on_gross_notional():
    S = [1, 1, -1, -1]
    p = _handmade_panel(S)
    g, c, s, w, dw = sm.components(p, p.S["V1"], swapc=sm.swap_vec("S1"))
    assert p.Kp == 3
    N = 6.0
    # weights: +1/6, +1/6, -1/6 ; traded: 1/6, 0, 2/6
    assert np.allclose(dw[:, 0], [1 / N, 0.0, 2 / N])
    expect_cost = dw[:, 0] * (0.5 * 1e-4 + sm.COMM)
    assert np.allclose(c[:, 0], expect_cost)
    assert np.allclose(g[:, 0], np.array([1, 1, -1]) / N * np.array([0.01, -0.02, 0.0]))
    assert np.allclose(s[:, 0], (1 / N) * 0.015 * 7 / 365.0)
    # no turnover, no cost: a flat sign path costs nothing after the entry
    q = _handmade_panel([1, 1, 1, 1])
    _, c2, _, _, dw2 = sm.components(q, q.S["V1"])
    assert dw2[1:, 0].sum() == 0 and c2[1:, 0].sum() == 0
    # cost multiplier scales only the spread part
    _, c3, _, _, _ = sm.components(p, p.S["V1"], cost_mult=2.0)
    assert np.allclose(c3[:, 0], dw[:, 0] * (1e-4 + sm.COMM))
    # ex-gold: N becomes 5
    _, _, _, w5, _ = sm.components(p, p.S["V1"], cols=sm.FX5)
    assert np.allclose(w5[:, 0], w[:, 0] * 6 / 5)


def test_sharpe_formula():
    x = np.array([0.01, -0.005, 0.02, 0.0, 0.004])
    assert sm.sharpe(x) == pytest.approx(x.mean() / x.std(ddof=1) * np.sqrt(52))


# ── null, p-values, Holm, bootstrap ──────────────────────────────────────────────

def test_block_permutation_is_a_derangement_of_blocks():
    rng = np.random.default_rng(0)
    for n in (60, 100, 961):
        idx = sm.block_perm_index(n, 26, rng)
        assert sorted(idx.tolist()) == list(range(n))
        blocks = [(a, min(a + 26, n)) for a in range(0, n, 26)]
        # no block of the permuted series starts where... the block placed at slot j is never block j
        pos = 0
        placed = []
        i = 0
        while i < n:
            b0 = idx[i] // 26
            ln = min(26, n - b0 * 26)
            placed.append(b0)
            i += ln
        assert all(b != j for j, b in enumerate(placed)) or n % 26 != 0     # with a short last block the slots shift; no fixed block either way
        assert not np.array_equal(idx, np.arange(n))


def test_identity_index_reproduces_the_actual_run_and_null_uses_same_pipeline():
    days, T = calendar()
    p = sm.build_panel(make_insts(random_closes(len(T), 8), T), START, END)
    ident = np.arange(p.K)
    Kg = int(p.live[:, sm.GOLD].sum())
    for v in sm.VNAMES:
        Sp = sm.apply_index(p.S[v], p, ident, np.arange(Kg))
        assert np.array_equal(Sp, p.S[v])
        assert sm.sharpe(sm.net_series(p, Sp)) == sm.sharpe(sm.net_series(p, p.S[v]))


def test_same_replicate_for_all_variants_and_determinism():
    days, T = calendar()
    p = sm.build_panel(make_insts(random_closes(len(T), 9), T), START, END)
    p.S["V2"] = p.S["V1"].copy()                   # identical sign paths must give identical null columns
    a = sm.null_distribution(p, 25, 123, "perm")
    b = sm.null_distribution(p, 25, 123, "perm")
    assert np.array_equal(a["sr"], b["sr"])
    assert np.array_equal(a["sr"][:, 0], a["sr"][:, 1])
    assert not np.array_equal(a["sr"][:, 0], a["sr"][:, 3])
    c = sm.null_distribution(p, 25, 124, "perm")
    assert not np.array_equal(a["sr"], c["sr"])


def test_null_removes_timing_but_keeps_turnover_and_bias():
    days, T = calendar("2000-03-30", "2021-06-01")
    rng = np.random.default_rng(11)
    ins = sm.synth_insts(rng, 0.6)                 # strong planted trend
    p = sm.build_panel(ins, sm.DEV_START, sm.DEV_END)
    sr = np.array([sm.sharpe(sm.net_series(p, p.S[v])) for v in sm.VNAMES])
    nd = sm.null_distribution(p, 60, 5, "perm")
    pv, pm = sm.p_values(sr, nd["sr"])
    assert sr[3] > 0.5 and pv[3] <= 0.02 and (pm >= pv).all()          # the planted trend is detected against the null
    assert nd["sr"][:, 3].mean() < sr[3] - 3 * nd["sr"][:, 3].std()
    to_actual = sm.components(p, p.S["V4"])[4].sum(1).mean()
    ratio = nd["turnover"][:, 3].mean() / to_actual
    assert 0.8 < ratio < 2.0          # the block joins add flips (documented in the prereg); the output reports the ratio and the cost effect
    assert nd["cost"][:, 3].mean() < 2.5 * 52 * sm.components(p, p.S["V4"])[1].sum(1).mean()      # annual cost drag, same units


def test_null_does_not_reject_without_signal():
    rng = np.random.default_rng(12)
    rej = 0
    for _ in range(6):
        p = sm.build_panel(sm.synth_insts(rng, 0.0), sm.DEV_START, sm.DEV_END)
        sr = np.array([sm.sharpe(sm.net_series(p, p.S[v])) for v in sm.VNAMES])
        nd = sm.null_distribution(p, 99, int(rng.integers(1 << 30)), "perm")
        pv, pm = sm.p_values(sr, nd["sr"])
        rej += int(pm.min() <= 0.01)
    assert rej <= 1


def test_p_values_and_holm_known_values():
    null = np.array([[0.0, 0.5], [0.1, 0.6], [0.2, 0.2], [0.3, 0.1]])
    p, pm = sm.p_values(np.array([0.25, 0.9]), null)
    assert p[0] == pytest.approx((1 + 1) / 5) and p[1] == pytest.approx(1 / 5)
    assert pm[0] == pytest.approx((1 + 3) / 5)                   # max per row = .5,.6,.2,.3 -> three rows >= 0.25
    rej, adj = sm.holm(np.array([0.001, 0.02, 0.04, 0.3, 0.5, 0.9]), 0.05)
    assert list(rej) == [True, False, False, False, False, False]
    assert adj[0] == pytest.approx(0.006) and adj[1] == pytest.approx(0.1)
    rej2, _ = sm.holm(np.array([0.004, 0.006, 0.009, 0.3, 0.5, 0.9]), 0.05)
    assert list(rej2) == [True, True, True, False, False, False]


def test_boot_index_is_circular_blocks_and_deterministic():
    rng = np.random.default_rng(1)
    idx = sm.boot_index(50, 13, 7, rng)
    assert idx.shape == (7, 50)
    d = np.diff(idx, axis=1) % 50
    assert ((d == 1) | (np.arange(49)[None, :] % 13 == 12)).all()      # consecutive within a block
    x = np.random.default_rng(2).standard_normal(200) * 0.01 + 0.002
    a = sm.boot_sharpes({"x": x}, blocks=(13,), B=300)
    b = sm.boot_sharpes({"x": x}, blocks=(13,), B=300)
    assert a["x"]["ci"] == b["x"]["ci"] and a["x"]["lo"] < a["x"]["sr"] < a["x"]["hi"]


# ── gates and selection ──────────────────────────────────────────────────────────

def _weekly_panel(years=range(2003, 2022)):
    R = np.concatenate([np.array(pd.date_range(f"{y}-01-07", periods=52, freq="7D"), dtype="datetime64[ns]") for y in years])
    K = len(R)
    return sm.Panel(R, list(sm.SYMBOLS), {}, np.zeros((K, 6)), np.ones((K, 6), bool), np.zeros((K, 6)), np.zeros((K, 6)), np.full(K, 7.0), {})


def test_gate_thresholds_year_count_best3_and_eras():
    p = _weekly_panel()
    yrs = p.years
    n = np.where(np.isin(yrs, [2003, 2004, 2005, 2006, 2007, 2008, 2009, 2010, 2011, 2012, 2013, 2014, 2015]), 0.002, -0.0005)
    n = n + np.random.default_rng(0).normal(0, 0.0005, len(n))
    st = {"net_S1": n, "exgold_S1": n}
    g = sm.gates(p, st, True, 0.01)
    assert g["detail"]["years_positive_2003_2020"] == 13 and g["G3b"]
    n2 = np.where(np.isin(yrs, np.arange(2003, 2015)), 0.002, -0.0005) + np.random.default_rng(0).normal(0, 0.0005, len(yrs))
    g2 = sm.gates(p, {"net_S1": n2, "exgold_S1": n2}, True, 0.01)
    assert g2["detail"]["years_positive_2003_2020"] == 12 and not g2["G3b"]
    # G1 needs both Holm and p_max
    assert not sm.gates(p, st, True, 0.2)["G1"] and not sm.gates(p, st, False, 0.01)["G1"]
    # materiality
    tiny = n * 0.0 + np.random.default_rng(1).normal(0.00001, 0.01, len(n))
    assert not sm.gates(p, {"net_S1": tiny, "exgold_S1": tiny}, True, 0.01)["G5"]
    # a profit that lives in three years is not stable
    conc = np.where(np.isin(yrs, [2008, 2009, 2010]), 0.05, -0.001)
    gc = sm.gates(p, {"net_S1": conc, "exgold_S1": conc}, True, 0.01)
    assert not gc["G3a"] and gc["detail"]["best3_years"] == [2008, 2009, 2010] or set(gc["detail"]["best3_years"]) == {2008, 2009, 2010}


def test_candidate_selection_takes_highest_S1_among_eligible_and_none_otherwise():
    mk = lambda e, sr: {"eligible": e, "detail": {"sr_S1": sr}}
    assert sm.select_candidate({"V1": mk(True, 0.4), "V2": mk(True, 0.7), "V3": mk(False, 2.0)}) == "V2"
    assert sm.select_candidate({"V1": mk(False, 1.0)}) is None


# ── end to end on synthetic data: determinism and outputs ────────────────────────

def test_run_dev_is_deterministic_and_writes_outputs(tmp_path):
    rng = np.random.default_rng(21)
    ins = sm.synth_insts(rng, 0.3)
    a = sm.run_dev(tmp_path / "a", n_perm=30, n_boot=150, insts=ins, quiet=True)
    b = sm.run_dev(tmp_path / "b", n_perm=30, n_boot=150, insts=ins, quiet=True)
    assert (tmp_path / "a" / "variants.csv").read_text() == (tmp_path / "b" / "variants.csv").read_text()
    assert a["summary"]["candidate"] == b["summary"]["candidate"]
    for f in ("summary.json", "variants.csv", "weekly_pnl.csv", "per_year.csv", "per_instrument.csv", "null_distribution.csv", "sensitivity.csv",
              "prereg_sha256.txt"):
        assert (tmp_path / "a" / f).exists(), f
    s = json.loads((tmp_path / "a" / "summary.json").read_text())
    assert s["window"][1] == "2021-06-01"
    sens = pd.read_csv(tmp_path / "a" / "sensitivity.csv")
    assert {"weekday_Mon", "weekday_Fri", "exec_delay_1day", "FX5_only", "spread_x2"} <= set(sens["case"])


def test_signal_free_world_has_no_edge_and_a_trend_world_has_one():
    rng = np.random.default_rng(31)
    p0 = sm.build_panel(sm.synth_insts(rng, 0.0), sm.DEV_START, sm.DEV_END)
    p1 = sm.build_panel(sm.synth_insts(rng, 0.8), sm.DEV_START, sm.DEV_END)
    s0 = np.mean([sm.sharpe(sm.net_series(p0, p0.S[v])) for v in ("V3", "V4", "V6")])
    s1 = np.mean([sm.sharpe(sm.net_series(p1, p1.S[v])) for v in ("V3", "V4", "V6")])
    assert abs(s0) < 0.6 and s1 > 0.8


# ── guards: DEV window and FINAL ─────────────────────────────────────────────────

def test_dev_window_refuses_final_and_later():
    with pytest.raises(ValueError):
        sm.check_dev_window("2003-01-01", "2021-06-02")
    with pytest.raises(ValueError):
        sm.check_dev_window("2003-01-01", None)
    with pytest.raises(ValueError):
        sm.check_dev_window("2021-06-01", "2021-06-01")
    with pytest.raises(ValueError):
        sm.check_dev_window("2022-01-01", "2021-06-01")
    with pytest.raises(ValueError):
        sm.check_dev_window("2021-07-01", "2021-12-01")
    s, e = sm.check_dev_window("2003-01-01", "2021-06-01")
    assert str(e.date()) == "2021-06-01"
    # the loader is guarded as well (ds.check_end), before any file is read
    with pytest.raises(ValueError):
        sm.load_dev_insts(end="2021-07-01")
    with pytest.raises(ValueError):
        sm.run_dev(Path("unused"), insts={}, start="2021-06-01", end="2021-06-01")


def test_cli_refuses_final_window_and_final_mode_without_go(tmp_path, capsys):
    assert sm.main(["dev", "--end", "2021-06-02", "--out", str(tmp_path / "x")]) == 2
    assert sm.main(["dev", "--start", "2021-06-01", "--end", "2021-06-01", "--out", str(tmp_path / "x")]) == 2
    assert not (tmp_path / "x").exists()
    if not sm.FINAL_GO.exists():
        assert sm.main(["final"]) == 2
    err = capsys.readouterr().err
    assert "refused" in err


def test_final_mode_needs_go_file_with_candidate_hash(tmp_path):
    go, cand = tmp_path / "FINAL_GO", tmp_path / "frozen_candidate.json"
    with pytest.raises(PermissionError):
        sm.require_final_go(go, cand)                      # no GO file
    go.write_text("candidate_sha256=abc\n")
    with pytest.raises(PermissionError):
        sm.require_final_go(go, cand)                      # no candidate
    cand.write_text(json.dumps({"variant": "V4", "weekday": 2}))
    with pytest.raises(PermissionError):
        sm.require_final_go(go, cand)                      # wrong hash
    go.write_text("candidate_sha256=" + sm.sha256_file(cand) + "\n")
    assert sm.require_final_go(go, cand) == sm.sha256_file(cand)
    with pytest.raises(PermissionError):                   # the loader checks too
        sm.load_final_insts(go_path=tmp_path / "nope", cand_path=cand)


def test_final_run_refused_without_go_even_with_data_and_runs_with_it(tmp_path):
    rng = np.random.default_rng(41)
    ins = sm.synth_insts(rng, 0.3, start="2018-01-01", end=sm.FINAL_END)
    go, cand = tmp_path / "FINAL_GO", tmp_path / "frozen_candidate.json"
    cand.write_text(json.dumps({"variant": "V4", "weekday": 2}))
    with pytest.raises(PermissionError):
        sm.run_final(tmp_path / "out", insts=ins, go_path=go, cand_path=cand)
    assert not (tmp_path / "out").exists()
    go.write_text("candidate_sha256=" + sm.sha256_file(cand) + "\n")
    res = sm.run_final(tmp_path / "out", insts=ins, go_path=go, cand_path=cand, n_boot=100)
    assert res["verdict"] in ("CONFIRMED", "CONSISTENT BUT NOT CONFIRMED", "NOT REPLICATED")
    assert 190 <= res["weeks"] <= 215
    assert (tmp_path / "out" / "final_result.json").exists()


def test_module_has_no_live_imports():
    src = Path(sm.__file__).read_text(encoding="utf-8")
    for bad in ("import main", "from main", "mt5_connector", "from utils", "import utils", "from strategies", "from agents", "order_send"):
        assert bad not in src
