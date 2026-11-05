"""ATR-floor confirmation (research/atr_floor_confirm.py). Synthetic data only: no MT5, no cache files, no FINAL data."""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from research import atr_floor_confirm as afc          # noqa: E402
from research import quant_methods as qm               # noqa: E402
from research.nulltest import weekly_table             # noqa: E402
from research.sim import Simulator, SimConfig, size_position   # noqa: E402

SPEC = {"EURUSD": dict(digits=5, point=1e-5, contract_size=100000, volume_min=0.01, volume_max=100,
                       volume_step=0.01, stops_level=0, currency_profit="USD")}
GOLD = dict(digits=2, point=0.01, contract_size=100, volume_min=0.01, volume_max=100, volume_step=0.01, stops_level=0, currency_profit="USD")
T0 = pd.Timestamp("2020-01-06 00:00", tz="UTC")           # a Monday


def h1_frame(n=80, seed=1):
    rng = np.random.default_rng(seed)
    close = 1.1 + np.cumsum(rng.normal(0, 0.0005, n))
    open_ = np.concatenate([[1.1], close[:-1]])
    hi = np.maximum(open_, close) + np.abs(rng.normal(0, 0.0003, n))
    lo = np.minimum(open_, close) - np.abs(rng.normal(0, 0.0003, n))
    return pd.DataFrame({"time": [T0 + pd.Timedelta(hours=i) for i in range(n)], "open": open_, "high": hi, "low": lo, "close": close})


def walk_m5(days=3, seed=3, vol=0.0002):
    rng = np.random.default_rng(seed)
    n = days * 288
    close = 1.1 + np.cumsum(rng.normal(0, vol, n))
    open_ = np.concatenate([[1.1], close[:-1]])
    hi = np.maximum(open_, close) + np.abs(rng.normal(0, vol / 2, n))
    lo = np.minimum(open_, close) - np.abs(rng.normal(0, vol / 2, n))
    return {"EURUSD": pd.DataFrame({"time": [T0 + pd.Timedelta(minutes=5 * i) for i in range(n)], "open": open_, "high": hi, "low": lo,
                                    "close": close, "spread_pts": 8.0})}


def one_candidate(m5, k=100, stop_pips=5.0, direction="BUY"):
    df = m5["EURUSD"]
    t = df["time"].iloc[k]
    px = float(df["close"].iloc[k])
    sgn = 1 if direction == "BUY" else -1
    return pd.DataFrame([{"T": t + pd.Timedelta(minutes=5), "symbol": "EURUSD", "direction": direction, "price": px + sgn * 8e-5,
                          "sl_raw": px - sgn * stop_pips * 1e-4, "atr_factor": 1.0, "size_factor": 1.0, "entry_type": "MARKET",
                          "subtype": "MARKET", "ask": px + 8e-5, "bid": px, "spread_pts": 8.0, "probability": 70,
                          "checks_passed": 4, "pattern": "x", "fvg_sig": "BUY", "sweep_sig": "NONE", "qt_phase": "London",
                          "trend_strong": True}])


# ── ATR: independent implementation and no look-ahead ──────────────────────────────────────

def loop_atr(h1: pd.DataFrame, n: int = 14) -> list:
    """Plain-loop ATR written independently of quant_methods: simple mean of the last n true ranges; value for the bar closing at open + 1 h."""
    hi, lo, cl = h1["high"].tolist(), h1["low"].tolist(), h1["close"].tolist()
    tr = [hi[0] - lo[0]] + [max(hi[i] - lo[i], abs(hi[i] - cl[i - 1]), abs(lo[i] - cl[i - 1])) for i in range(1, len(hi))]
    return [float("nan") if i < n - 1 else sum(tr[i - n + 1:i + 1]) / n for i in range(len(tr))]


def test_atr_matches_independent_loop_and_candidate_uses_last_closed_bar():
    h1 = h1_frame()
    ref = loop_atr(h1)
    _, atr = qm.h1_atr_series(h1)
    assert np.allclose(atr, ref, equal_nan=True)
    T = T0 + pd.Timedelta(hours=40, minutes=5)                     # bars 0..39 have closed (bar 39 closes at hour 40)
    c = qm.add_atr(pd.DataFrame({"T": [T], "symbol": ["EURUSD"], "price": [1.1], "sl_raw": [1.0999], "direction": ["BUY"]}), {"EURUSD": h1})
    assert c["atr_h1"].iloc[0] == pytest.approx(ref[39])
    T_exact = T0 + pd.Timedelta(hours=40)                           # the bar closing exactly at T is available at T
    c2 = qm.add_atr(pd.DataFrame({"T": [T_exact], "symbol": ["EURUSD"], "price": [1.1], "sl_raw": [1.0999], "direction": ["BUY"]}), {"EURUSD": h1})
    assert c2["atr_h1"].iloc[0] == pytest.approx(ref[39])


def test_candidate_atr_unchanged_when_future_is_removed_or_scrambled():
    h1 = h1_frame()
    cut = 50
    Ts = [T0 + pd.Timedelta(hours=h, minutes=m) for h in range(15, cut) for m in (0, 5, 55)]
    base = pd.DataFrame({"T": Ts, "symbol": "EURUSD", "price": 1.1, "sl_raw": 1.0999, "direction": "BUY"})
    full = qm.add_atr(base, {"EURUSD": h1})["atr_h1"].to_numpy()
    trunc = qm.add_atr(base, {"EURUSD": h1.iloc[:cut]})["atr_h1"].to_numpy()
    assert np.allclose(full, trunc, equal_nan=True)
    h2 = h1.copy()
    h2.loc[cut:, ["high", "low", "close"]] = h2.loc[cut:, ["high", "low", "close"]].to_numpy()[::-1] * 1.02
    assert np.allclose(full, qm.add_atr(base, {"EURUSD": h2})["atr_h1"].to_numpy(), equal_nan=True)


def test_atr_rel_is_causal():
    h1 = h1_frame(300, seed=5)
    T = np.array([(T0 + pd.Timedelta(hours=200, minutes=5)).value])
    full = afc.atr_rel_at(h1, T)[0]
    h2 = h1.copy()
    h2.loc[201:, ["high", "low", "close"]] *= 1.5
    assert afc.atr_rel_at(h2, T)[0] == pytest.approx(full)
    assert np.isnan(afc.atr_rel_at(h1, np.array([(T0 + pd.Timedelta(hours=20)).value]))[0])      # 100-bar median needs history


# ── the floor: never loosens, binds for gold, only sl_raw changes ──────────────────────────

def test_floor_never_loosens_a_stop_and_keeps_the_side():
    rng = np.random.default_rng(0)
    n = 400
    direction = rng.choice(["BUY", "SELL"], n)
    price = 1.1 + rng.normal(0, 0.01, n)
    dist = np.abs(rng.normal(0.0020, 0.0015, n)) + 1e-5
    sl = np.where(direction == "BUY", price - dist, price + dist)
    c = pd.DataFrame({"direction": direction, "price": price, "sl_raw": sl, "atr_h1": np.abs(rng.normal(0.0012, 0.0006, n)) + 1e-4})
    c.loc[::25, "atr_h1"] = np.nan                                            # warm-up candidates
    for k in (1.0, 1.5, 2.0):
        w = afc.apply_atr_floor(c, k)
        after = np.abs(w["price"] - w["sl_raw"]).to_numpy()
        assert (after >= dist - 1e-12).all()                                  # never narrower
        buy = (c["direction"] == "BUY").to_numpy()
        assert (w["sl_raw"][buy] < w["price"][buy]).all() and (w["sl_raw"][~buy] > w["price"][~buy]).all()
        ok = c["atr_h1"].notna().to_numpy()
        assert (after[ok] >= k * c["atr_h1"].to_numpy()[ok] - 1e-12).all()    # at least the floor
        untouched = ~ok | (dist >= k * c["atr_h1"].fillna(0).to_numpy())
        assert np.allclose(after[untouched], dist[untouched])                 # wide structure stops and NaN-ATR rows keep their stop
        assert (w.drop(columns="sl_raw") == c.drop(columns="sl_raw")).all().all() or w.drop(columns="sl_raw").equals(c.drop(columns="sl_raw"))


def test_floor_binds_for_gold_where_the_ten_pip_floor_never_does():
    cfg = SimConfig(fixed_risk_pct=0.25)
    entry, sl = 1800.00, 1799.50                                              # 0.50 dollar structure stop
    base = size_position(GOLD, "XAUUSD", "BUY", entry, sl, 100_000.0, 1.0, 1.0, cfg)
    assert base["sl_pips"] == pytest.approx(50.0)                             # 10-pip floor (0.10 dollar) does not bind
    c = pd.DataFrame({"direction": ["BUY"], "price": [entry], "sl_raw": [sl], "atr_h1": [4.0]})
    w = afc.apply_atr_floor(c, 1.0)
    wide = size_position(GOLD, "XAUUSD", "BUY", entry, float(w["sl_raw"].iloc[0]), 100_000.0, 1.0, 1.0, cfg)
    assert wide["sl_pips"] == pytest.approx(400.0)                            # 4 dollars
    assert wide["volume"] < base["volume"]


# ── sizing: the risk fraction is unchanged, the position shrinks, target moves with the stop ─

def test_sizing_risk_fraction_unchanged_and_target_is_three_times_the_widened_stop():
    h1 = h1_frame(80)
    m5 = walk_m5()
    cand = qm.add_atr(one_candidate(m5, k=250, stop_pips=5.0), {"EURUSD": h1})
    atr = float(cand["atr_h1"].iloc[0])
    assert 0.0004 < atr < 0.004
    cfg = SimConfig(fixed_risk_pct=0.25)
    res = {}
    for name, k in (("B0", None), ("K", 3.0)):
        cs = afc.apply_atr_floor(cand, k)
        res[name] = Simulator(["EURUSD"], m5, SPEC, cs, cfg).run()["trades"].iloc[0]
    b, f = res["B0"], res["K"]
    for t in (b, f):
        assert 0.90 * 250.0 <= t["risk_usd"] <= 250.0 * 1.01                  # 0.25 % of 100,000 up to lot rounding
    assert f["volume"] < b["volume"]
    assert f["volume"] * abs(f["entry"] - f["sl0"]) == pytest.approx(b["volume"] * abs(b["entry"] - b["sl0"]), rel=0.1)   # same money at risk
    assert abs(f["entry"] - f["sl0"]) >= 3.0 * atr - 1e-5 and abs(f["entry"] - f["sl0"]) < 3.0 * atr + 3e-4
    assert abs(f["tp"] - f["entry"]) == pytest.approx(3.0 * abs(f["entry"] - f["sl0"]), abs=4e-5)   # prices rounded to 5 digits


def test_run_arm_deterministic_and_only_the_stop_differs_between_arms():
    h1 = h1_frame(80)
    m5 = walk_m5()
    cand = qm.add_atr(one_candidate(m5, k=250, stop_pips=5.0), {"EURUSD": h1})
    a1 = afc.run_arm(afc.ARMS["K10"], cand, SPEC, m5, ["EURUSD"])
    a2 = afc.run_arm(afc.ARMS["K10"], cand, SPEC, m5, ["EURUSD"])
    pd.testing.assert_frame_equal(a1[0]["trades"], a2[0]["trades"])
    assert a1[1] == a2[1] == 1.0                                              # the single candidate was widened (5 pips < 1 ATR)
    ctl = afc.run_arm(afc.ARMS["B0"], cand, SPEC, m5, ["EURUSD"])
    assert ctl[1] == 0.0
    assert ctl[0]["trades"]["symbol"].tolist() == a1[0]["trades"]["symbol"].tolist()
    assert ctl[0]["trades"]["placed_T"].tolist() == a1[0]["trades"]["placed_T"].tolist()    # same order, only the stop (and the lots) differ


def test_arms_are_the_preregistered_ones():
    assert afc.K == 1.0
    assert afc.ARMS["B0"].atr_k is None and afc.ARMS["K10"].atr_k == 1.0
    base = afc.base_config()
    assert base.fixed_risk_pct == 0.25 and base.friday_flatten_utc == 20.0 and base.intrabar == "pessimistic"
    assert afc.ARMS["K10N"].cfg().intrabar == "neutral" and afc.ARMS["B0N"].cfg().intrabar == "neutral"
    s = afc.ARMS["K10S"].cfg()
    assert s.spread_mult == 1.5 and s.commission_per_lot_side == 3.125
    assert afc.N_SEEDS == 40 and afc.N_SENS_SEEDS == 10 and afc.N_BOOT == 5000
    for pair in afc.PAIRS_ARMS.values():
        assert afc.ARMS[pair[0]].atr_k is None and afc.ARMS[pair[1]].atr_k == 1.0
        assert afc.ARMS[pair[0]].over == afc.ARMS[pair[1]].over               # control and treated differ only by the floor


# ── FINAL lock and DEV window refusals ─────────────────────────────────────────────────────

def test_dev_window_refusals():
    assert afc.dev_window("2014-01-01", "2021-06-01").name == "DEV"
    assert afc.dev_window("2014-01-01", "2021-05-01").end == "2021-05-01"
    for start, end in (("2014-01-01", "2021-06-02"), ("2014-01-01", "2025-05-01"), ("2014-01-01", None), ("2021-06-01", "2022-01-01"),
                       ("2022-03-01", "2021-12-01"), ("2021-06-01", "2021-06-01"), ("2021-07-01", "2021-06-01")):
        with pytest.raises(SystemExit):
            afc.dev_window(start, end)


def test_cli_dev_refuses_before_touching_any_data(monkeypatch, tmp_path):
    def boom(*a, **k):
        raise AssertionError("data was touched")
    monkeypatch.setattr(afc, "prepare_bars", boom)
    monkeypatch.setattr(afc, "run_experiment", boom)
    monkeypatch.setattr(afc, "OUT", tmp_path)
    for argv in (["dev", "--tag", "atr_floor_x", "--end", "2021-06-02"], ["dev", "--tag", "atr_floor_x", "--start", "2021-06-01", "--end", "2021-06-01"],
                 ["dev", "--tag", "atr_floor_x", "--start", "2021-06-15", "--end", "2022-01-01"], ["dev", "--tag", "atr_floor_x", "--end", ""]):
        with pytest.raises(SystemExit):
            afc.main(argv)
    assert not list(tmp_path.iterdir())


def test_final_is_disabled_without_final_go(monkeypatch, tmp_path):
    def boom(*a, **k):
        raise AssertionError("FINAL data path was reached")
    monkeypatch.setattr(afc, "FINAL_GO", tmp_path / "FINAL_GO")
    monkeypatch.setattr(afc.final_gate, "__defaults__", (tmp_path / "FINAL_GO", afc.PREREG))  # default bound at definition time
    monkeypatch.setattr(afc, "run_experiment", boom)
    monkeypatch.setattr(afc, "prepare_bars", boom)
    import research.run as rr
    monkeypatch.setattr(rr, "get_candidates", boom)
    with pytest.raises(SystemExit) as e:
        afc.main(["final"])
    assert "disabled" in str(e.value)
    assert afc.FINAL_WINDOW.start == "2021-06-01" and afc.FINAL_WINDOW.end == "2025-05-01"


def test_final_go_must_name_the_current_prereg_hash(monkeypatch, tmp_path):
    prereg = tmp_path / "p.md"
    prereg.write_text("# t\n\nPrereg-SHA256: PENDING\n\nbody\n", encoding="utf-8")
    sha = afc.prereg_sha256(prereg.read_text(encoding="utf-8"))
    go = tmp_path / "FINAL_GO"
    go.write_text("approved " + "0" * 64, encoding="utf-8")
    with pytest.raises(SystemExit):
        afc.final_gate(go, prereg)
    go.write_text("approved " + sha, encoding="utf-8")
    assert afc.final_gate(go, prereg) == sha
    prereg.write_text("# t\n\nPrereg-SHA256: PENDING\n\nbody changed\n", encoding="utf-8")           # edited after approval
    with pytest.raises(SystemExit):
        afc.final_gate(go, prereg)
    monkeypatch.setattr(afc, "final_gate", lambda *a, **k: sha)
    monkeypatch.setattr(afc, "OUT", tmp_path)
    (tmp_path / "atr_floor_final").mkdir()
    with pytest.raises(SystemExit) as e:                                                        # used once
        afc.main(["final"])
    assert "once" in str(e.value)


def test_prereg_hash_ignores_only_the_hash_line_and_line_endings():
    a = "x\nPrereg-SHA256: PENDING\ny\n"
    b = "x\r\nPrereg-SHA256: 123abc\r\ny\r\n"
    c = "x\nPrereg-SHA256: PENDING\ny changed\n"
    assert afc.prereg_sha256(a) == afc.prereg_sha256(b) != afc.prereg_sha256(c)
    real = afc.PREREG.read_text(encoding="utf-8")
    stored = [ln for ln in real.splitlines() if ln.startswith("Prereg-SHA256:")]
    assert len(stored) == 1
    assert stored[0].split(":")[1].strip() in (afc.prereg_sha256(real), "PENDING")


# ── swap stress ────────────────────────────────────────────────────────────────────────────

def _w(o, c):
    return afc.rollover_weights(pd.Series(pd.to_datetime([o], utc=True)), pd.Series(pd.to_datetime([c], utc=True)))[0]


def test_rollover_weights():
    assert _w("2020-07-06 08:00", "2020-07-06 12:00") == 0                    # same trading day (Monday)
    assert _w("2020-07-06 08:00", "2020-07-07 08:00") == 1                    # Monday to Tuesday
    assert _w("2020-07-08 08:00", "2020-07-09 08:00") == 3                    # Wednesday night is charged three times
    assert _w("2020-07-06 08:00", "2020-07-10 08:00") == 1 + 1 + 3 + 1        # Mon to Fri: Tue, Wed, Thu(x3), Fri
    assert _w("2020-07-10 08:00", "2020-07-10 20:00") == 0                    # Friday flatten at 20:00 UTC is before the 21:00 UTC rollover
    assert _w("2020-07-06 20:30", "2020-07-06 21:30") == 1                    # summer: rollover at 21:00 UTC
    assert _w("2020-01-06 20:30", "2020-01-06 21:30") == 0                    # winter: rollover at 22:00 UTC, not yet crossed
    assert _w("2020-01-06 21:30", "2020-01-06 22:30") == 1


def test_swap_charge_in_r_scales_with_lots_over_risk_and_gold_costs_more():
    t = pd.DataFrame({"symbol": ["EURUSD", "XAUUSD"], "open_T": pd.to_datetime(["2020-07-06 08:00"] * 2, utc=True),
                      "close_T": pd.to_datetime(["2020-07-07 08:00"] * 2, utc=True), "volume": [0.5, 0.5], "risk_usd": [250.0, 250.0]})
    ch = afc.swap_charge_r(t)
    assert ch[0] == pytest.approx(7.0 * 0.5 / 250.0) and ch[1] == pytest.approx(20.0 * 0.5 / 250.0)
    assert afc.swap_charge_r(t, 0.5) == pytest.approx(ch / 2)


# ── bootstrap, analysis and the decision table ──────────────────────────────────────────────

def test_resampling_multiplicities():
    m = afc.mult_iid(50, 20)
    assert m.shape == (50, 20) and (m.sum(axis=0) == 50).all()
    b = afc.mult_block(50, 20, L=4)
    assert (b.sum(axis=0) == 50).all()
    assert np.array_equal(afc.mult_iid(50, 20), m) and np.array_equal(afc.mult_block(50, 20), b)        # deterministic


def test_delta_of_identical_arms_is_exactly_zero():
    rng = np.random.default_rng(0)
    n = rng.integers(0, 4, size=(3, 40)).astype(float)
    s = rng.normal(0, 1, size=(3, 40)) * n
    d = afc.delta((n, s), (n.copy(), s.copy()), afc.mult_iid(40, 200))
    assert d["est"] == 0 and d["lo"] == 0 and d["hi"] == 0 and d["se"] == 0
    shift = afc.delta((n, s), (n, s + 0.1 * n), afc.mult_iid(40, 200))                                   # +0.1 R on every trade
    assert shift["est"] == pytest.approx(0.1) and shift["lo"] == pytest.approx(0.1) and shift["hi"] == pytest.approx(0.1)


def test_power_arithmetic():
    assert afc.power(0.03, 0.084) == pytest.approx(0.80, abs=0.01)
    assert afc.power(0.03, 0.0) == pytest.approx(0.025, abs=0.001)
    assert afc.power(0.0234, 0.066) == pytest.approx(0.81, abs=0.02)


def _good(**kw):
    r = {"e1_lo": 0.01, "e1_est": 0.07, "e2_est": 1.0, "n1_lo": 0.01, "n1_hi": 0.08, "n1_est": 0.04, "null_est": 0.04, "dd_ok": True,
         "guards": {"a": True, "b": True}, "ratio_pooled": 0.95, "ratio_fx": 0.9}
    r.update(kw)
    return r


def test_decision_table():
    assert afc.classify(_good())["tier"] == "A"
    assert afc.classify(_good(e1_lo=-0.01))["tier"] == "B"                          # real gain not distinguishable from zero, cost effect reproduced
    assert afc.classify(_good(e1_lo=-0.01, n1_lo=-0.01))["tier"] == "C"
    assert afc.classify(_good(e1_est=0.01, e1_lo=-0.04))["tier"] == "D"             # point estimate below the smallest effect of interest
    assert afc.classify(_good(n1_hi=0.015, n1_lo=-0.03, n1_est=-0.005, null_est=-0.005))["tier"] == "D"      # cost mechanism not reproduced
    assert afc.classify(_good(dd_ok=False))["tier"] == "D"                          # drawdown worse
    assert afc.classify(_good(guards={"a": True, "b": False}))["tier"] == "B"       # a robustness guard fails -> not confirmed
    assert afc.classify(_good(ratio_fx=0.7))["tier"] == "B"                         # trade-count guard fails
    assert afc.classify(_good(e2_est=-0.1))["tier"] == "B"
    assert afc.classify(_good(null_est=-0.01, n1_est=-0.01, n1_lo=-0.03, n1_hi=0.05))["tier"] == "C"


def synth_outdir(tmp: Path, effect=0.03, seed=0, n_null=12) -> Path:
    """A fake finished run: the six arms, real run (-1) and null seeds, with a known effect of the floor on R."""
    rng = np.random.default_rng(seed)
    syms = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "XAUUSD"]
    days = pd.bdate_range("2019-01-02", "2020-12-30", tz="UTC")
    parts, rows = [], []
    for arm in afc.ARMS:
        a = afc.ARMS[arm]
        seeds = [-1] + [s for s in range(n_null) if a.max_seed is None or s < a.max_seed]
        for sd in seeds:
            g = np.random.default_rng([seed, sd + 1])                      # same orders in both arms of a pair (shared stream)
            n = 220
            placed = days[g.integers(0, len(days), n)] + pd.to_timedelta(g.integers(7 * 60, 21 * 60, n), unit="m")
            sym = g.choice(syms, n)
            base_r = g.normal(-0.1, 1.2, n)
            tre = a.atr_k is not None
            keep = np.ones(n, bool) if not tre else g.random(n) < 0.97
            r = base_r + (effect if tre else 0.0) + (g.normal(0, 0.05, n) if tre else 0.0)
            hold = g.integers(0, 40 * 60, n)
            t = pd.DataFrame({"arm": arm, "seed": sd, "symbol": sym, "direction": "BUY", "subtype": "LIMIT", "placed_T": placed,
                              "open_T": placed + pd.Timedelta(minutes=5), "close_T": placed + pd.to_timedelta(hold + 5, unit="m"),
                              "entry": 1.1, "sl0": 1.099, "tp": 1.103, "exit": 1.1, "volume": 0.5, "risk_usd": 250.0, "gross_pnl": r * 250.0,
                              "commission": 2.5, "net_pnl": r * 250.0, "r": r, "exit_reason": g.choice(["SL", "BE", "TP", "FLAT"], n),
                              "hold_min": hold, "spread_pts": 8.0, "be_moved": False})[keep]
            parts.append(t)
            rows.append({"arm": arm, "seed": sd, "widened_share": 0.8 if tre else 0.0, "trades": int(keep.sum()), "n_end": 0,
                         "max_dd_pct": -50.0 + (5.0 if tre else 0.0) + rng.normal(0, 1), "calendar_days": 727, "f_orders_placed": 100.0})
    out = tmp / "run"
    out.mkdir()
    tr = pd.concat(parts, ignore_index=True)
    tr.to_parquet(out / "trades.parquet", index=False)
    pd.DataFrame(rows).to_csv(out / "rows.csv", index=False)
    f = tr[(tr.seed == -1) & (tr.arm == "B0")][["symbol", "placed_T"]].drop_duplicates().rename(columns={"placed_T": "T"})
    f["atr_h1"], f["atr_rel"] = 0.001, rng.uniform(0.5, 2.0, len(f))
    f.to_parquet(out / "cand_features.parquet", index=False)
    return out


def test_analyse_runs_end_to_end_on_a_synthetic_run_and_is_deterministic(tmp_path):
    out = synth_outdir(tmp_path)
    r1 = afc.analyse(out, n_boot=300, check_quant=False)
    r2 = afc.analyse(out, n_boot=300, check_quant=False)
    assert afc._clean(r1) == afc._clean(r2)
    p = r1["primary"]
    for k in ("E1_real_day", "E1_real_week", "E1_real_block", "N1_day", "N1_week", "N1_block"):
        assert p[k]["se"] > 0 and p[k]["lo"] < p[k]["hi"]
    assert p["N1_week"]["est"] == pytest.approx(0.03, abs=0.03)             # the planted effect is recovered on the null (12 seeds)
    assert p["E1_real_binding_lo"] == min(p[f"E1_real_{w}"]["lo"] for w in ("day", "week", "block"))
    assert p["trades"]["ratio_real_fx"] < 1.0 and p["dd_ok"] is True
    assert set(r1["guards"]) == {"no_gold", "standardised", "swap", "stressed_cost", "neutral_intrabar"}
    assert r1["tier_illustration"]["tier"] in "ABCD"
    assert r1["power_final"]["mde_real"] == pytest.approx(2.8 * r1["power_final"]["se_real"])
    assert "matched" in r1 and r1["matched"]["real"]["n_common"] > 0
    import json
    json.dumps(afc._clean(r1))                                                 # serialisable


def test_analyse_with_no_effect_is_centred_on_zero(tmp_path):
    out = synth_outdir(tmp_path, effect=0.0, seed=4)
    p = afc.analyse(out, n_boot=300, check_quant=False)["primary"]
    assert abs(p["N1_week"]["est"]) < 0.05
    assert p["N1_week"]["lo"] < p["N1_week"]["est"] < p["N1_week"]["hi"]


def test_acceptance_check_detects_a_mismatch(tmp_path):
    out = synth_outdir(tmp_path, n_null=21)
    tr, _ = afc._prep(out)
    q = tmp_path / "quant"
    q.mkdir()
    parts = []
    for mine, theirs in (("B0", "B0"), ("K10", "M1_k10")):
        for sd in [-1] + list(range(20)):
            w = weekly_table(tr[(tr.arm == mine) & (tr.seed == sd)])
            w.insert(0, "seed", sd)
            w.insert(0, "variant", theirs)
            parts.append(w)
    qw = pd.concat(parts, ignore_index=True)
    qw.to_parquet(q / "weekly.parquet", index=False)
    assert afc.acceptance_vs_quant_s1(tr, q)["reproduced"] is True
    qw.loc[(qw.variant == "M1_k10") & (qw.seed == 3), "sum_r"] += 1e-6
    qw.to_parquet(q / "weekly.parquet", index=False)
    res = afc.acceptance_vs_quant_s1(tr, q)
    assert res["reproduced"] is False and res["K10"]["mismatched_runs"] >= 1 and res["B0"]["mismatched_runs"] == 0
