"""
Risk-profile simulation (pre-registration: research/reports/quant_risk_prereg.md).

Layer 1 (`days`): full portfolio simulations of the DEV window at each profile's static settings (risk fraction, max open, daily gate), real signals and
random-direction `reflect` null seeds, reduced to Prague-day sources: per day the ordered per-trade returns closed that day and the intraday equity low.

Layer 2 (`profile_mc`): block bootstrap of those days with the path-dependent rules the simulator does not have (loss ladder, halt, daily stop, loss-streak pause)
and FTMO-style limits (5 % daily, 10 % static, 10 % target). Reduces to `montecarlo.ftmo_mc` when no rule is active (tested).

    python -m research.quant_risk days   --tag quant_risk_days --workers 8
    python -m research.quant_risk report --days quant_risk_days --tag quant_risk

DEV only; the FINAL window is refused. Nothing here touches MT5, sim.py, signals.py or the live code. Risk management cannot create expectancy: every number below is a
statement about the speed of loss or the probability of a limit breach given the simulated trade stream.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from research.metrics import day_table
from research.run import OUT, _clean

TZ = "Europe/Prague"
F0_DEFAULT = 0.25


# ── day sources ─────────────────────────────────────────────────────────────────────────────

@dataclass
class DaySource:
    """Concatenated Prague weekdays of one or several simulated runs. `seg` marks the run each day belongs to (blocks never straddle runs)."""
    low: np.ndarray            # (N,) intraday equity low, % of the day's start balance, at risk fraction f0
    rets: np.ndarray           # (N, K) per-trade return % of balance at open, in close order, zero padded
    risk: np.ndarray           # (N, K) risk % of balance of each trade
    mask: np.ndarray           # (N, K) bool, True where a trade exists
    seg: np.ndarray            # (N,) run id
    mean_r: float              # mean R per trade over all trades of the source
    f0: float = F0_DEFAULT

    @property
    def n_days(self) -> int:
        return len(self.low)

    @property
    def n_trades(self) -> int:
        return int(self.mask.sum())

    def mean_ret_pct_per_trade(self) -> float:
        return float(self.rets[self.mask].mean())


def day_arrays(trades: pd.DataFrame, equity: pd.DataFrame, f0: float = F0_DEFAULT, tz: str = TZ) -> dict:
    """One run -> per-weekday arrays. Trades are placed on the Prague day of their CLOSE (END trades dropped); a weekend close goes to the next Monday."""
    d = day_table(equity, trades, tz)
    days = list(d.index)
    pos = {k: i for i, k in enumerate(days)}
    t = trades[trades["exit_reason"] != "END"].sort_values("close_T")
    cd = pd.to_datetime(t["close_T"], utc=True).dt.tz_convert(tz).dt.tz_localize(None).dt.floor("D")
    dow = cd.dt.dayofweek
    cd = cd + pd.to_timedelta(np.where(dow == 5, 2, np.where(dow == 6, 1, 0)), unit="D")
    keys = cd.dt.strftime("%Y-%m-%d").to_numpy()
    per_day: dict = {}
    for k, ret, rk in zip(keys, t["ret_pct"].to_numpy(float), t["risk_pct"].to_numpy(float)):
        if k in pos:
            per_day.setdefault(pos[k], []).append((ret, rk))
    K = max([len(v) for v in per_day.values()] + [1])
    rets, risk = np.zeros((len(days), K)), np.zeros((len(days), K))
    mask = np.zeros((len(days), K), bool)
    for i, lst in per_day.items():
        for j, (ret, rk) in enumerate(lst):
            rets[i, j], risk[i, j], mask[i, j] = ret, rk, True
    return {"low": d["low_pct"].to_numpy(float), "rets": rets, "risk": risk, "mask": mask,
            "r": t["r"].to_numpy(float), "n_days": len(days), "days": days}


def build_source(runs: list, f0: float = F0_DEFAULT) -> DaySource:
    """runs: list of `day_arrays` dicts."""
    K = max(r["rets"].shape[1] for r in runs)

    def pad(a, fill=0.0):
        out = np.full((a.shape[0], K), fill, dtype=a.dtype)
        out[:, :a.shape[1]] = a
        return out
    low = np.concatenate([r["low"] for r in runs])
    rets = np.vstack([pad(r["rets"]) for r in runs])
    risk = np.vstack([pad(r["risk"]) for r in runs])
    mask = np.vstack([pad(r["mask"], False) for r in runs])
    seg = np.concatenate([np.full(len(r["low"]), i) for i, r in enumerate(runs)])
    mean_r = float(np.concatenate([r["r"] for r in runs]).mean())
    return DaySource(low, rets, risk, mask, seg, mean_r, f0)


def scale_source(src: DaySource, m: float) -> DaySource:
    """Linear risk scaling of the whole source (returns and lows) by m: the same trades at m x the risk fraction."""
    return replace(src, low=src.low * m, rets=src.rets * m, risk=src.risk * m, f0=src.f0 * m)


def shift_mean_r(src: DaySource, mu: float) -> DaySource:
    """Same trades with the mean R moved to `mu`: ret += delta x risk %, and the day's low gets min(0, delta x risk closed that day).
    Labelled approximation: the true intraday path of a changed edge is unknown."""
    delta = mu - src.mean_r
    rets = src.rets + delta * src.risk * src.mask
    low = src.low + np.minimum(0.0, delta * src.risk.sum(axis=1))
    return replace(src, rets=rets, low=low, mean_r=mu)


# ── profiles and the Monte Carlo ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Profile:
    name: str
    f: float                                   # % risk per trade of the profile
    ladder: tuple = ()                         # ((loss_from_initial, multiplier), ...) ascending; loss as a fraction (0.03 = 3 %)
    halt_loss: Optional[float] = None          # stop trading for good when loss from initial >= this (fraction)
    day_stop: Optional[float] = None           # stop new trades for the rest of the day when the day's realised loss >= this fraction of the initial balance
    streak_n: Optional[int] = None             # after this many consecutive losing trades ...
    streak_skip_days: int = 1                  # ... skip the rest of the day and this many following days


@dataclass(frozen=True)
class Rules:
    """FTMO-style limits as assumed in montecarlo.FtmoRules (third-party summary; not verified against FTMO's own text)."""
    daily_loss: float = 0.05                   # of the initial balance
    max_loss: float = 0.10                     # static, of the initial balance
    target: float = 0.10
    min_days: int = 4
    block_len: int = 5


def block_starts(seg: np.ndarray, B: int) -> np.ndarray:
    n = len(seg)
    ok = np.arange(n - B + 1)
    return ok[seg[ok] == seg[ok + B - 1]]


def profile_mc(src: DaySource, prof: Profile, horizon: int, n_paths: int = 20_000, seed: int = 21, rules: Rules = Rules()) -> dict:
    """Bootstrap `horizon` trading days; apply the profile's per-trade multiplier and rules; record limit breaches, halts, passes, drawdown, trades taken."""
    rng = np.random.default_rng(seed)
    B = rules.block_len
    starts = block_starts(src.seg, B)
    if not len(starts):
        raise ValueError("source too short for the block length")
    n_blocks = int(np.ceil(horizon / B))
    idx = (rng.choice(starts, size=(n_paths, n_blocks))[:, :, None] + np.arange(B)[None, None, :]).reshape(n_paths, -1)[:, :horizon]
    m0 = prof.f / src.f0
    K = src.rets.shape[1]
    floor = 1.0 - rules.max_loss

    bal = np.ones(n_paths)
    run = np.ones(n_paths, bool)                                # not failed, not passed
    halted = np.zeros(n_paths, bool)
    fail_d = np.zeros(n_paths, bool)
    fail_m = np.zeros(n_paths, bool)
    passed = np.zeros(n_paths, bool)
    consec = np.zeros(n_paths, int)
    skip_until = np.zeros(n_paths, int)
    tdays = np.zeros(n_paths, int)
    trades = np.zeros(n_paths, int)
    peak = np.ones(n_paths)
    maxdd = np.zeros(n_paths)
    t_breach = np.full(n_paths, np.nan)
    t_halt = np.full(n_paths, np.nan)
    t_pass = np.full(n_paths, np.nan)
    trades_at_event = np.full(n_paths, np.nan)

    def ladder_mult(L):
        mult = np.ones_like(L)
        for thr, mu in prof.ladder:
            mult = np.where(L >= thr - 1e-12, mu, mult)
        return mult

    for d in range(horizon):
        i = idx[:, d]
        active = run & ~halted & (d >= skip_until)
        m_start = m0 * ladder_mult(1.0 - bal)
        low_m = m_start * src.low[i] / 100.0                    # fraction of the day's start balance (negative when a loss)
        bd = active & (-bal * low_m >= rules.daily_loss - 1e-12)
        bm = active & ~bd & (bal * (1.0 + low_m) <= floor + 1e-12)
        failed_now = bd | bm
        if failed_now.any():
            fail_d |= bd
            fail_m |= bm
            t_breach[failed_now] = d + 1
            trades_at_event[failed_now] = trades[failed_now]
            bal = np.where(failed_now, bal * (1.0 + low_m), bal)
            run &= ~failed_now
        active &= run
        day_start = bal.copy()
        day_over = np.zeros(n_paths, bool)
        traded = np.zeros(n_paths, bool)
        for k in range(K):
            v = active & ~day_over & src.mask[i, k]
            if not v.any():
                continue
            mult = m0 * ladder_mult(1.0 - bal)
            r = mult * src.rets[i, k] / 100.0
            bal = np.where(v, bal * (1.0 + r), bal)
            trades += v
            traded |= v
            loss, win = v & (r < 0), v & (r >= 0)
            consec = np.where(loss, consec + 1, np.where(win, 0, consec))
            if prof.streak_n is not None:
                trig = v & (consec >= prof.streak_n)
                skip_until = np.where(trig, d + 1 + prof.streak_skip_days, skip_until)
                day_over |= trig
                consec = np.where(trig, 0, consec)
            if prof.day_stop is not None:
                day_over |= v & ((day_start - bal) >= prof.day_stop - 1e-12)
            if prof.halt_loss is not None:
                h = v & ~halted & ((1.0 - bal) >= prof.halt_loss - 1e-12)
                if h.any():
                    halted |= h
                    t_halt[h] = d + 1
                    trades_at_event[h & np.isnan(trades_at_event)] = trades[h & np.isnan(trades_at_event)]
        tdays += traded
        peak = np.maximum(peak, bal)
        maxdd = np.minimum(maxdd, bal / peak - 1.0)
        p = run & (bal >= 1.0 + rules.target) & (tdays >= rules.min_days)
        if p.any():
            passed |= p
            t_pass[p] = d + 1
            run &= ~p

    q = lambda a, qq: float(np.quantile(a, qq))   # noqa: E731
    fin = bal - 1.0
    ev = ~np.isnan(trades_at_event)
    out = {
        "profile": prof.name, "f": prof.f, "horizon_days": horizon, "paths": n_paths,
        "p_fail_daily": float(fail_d.mean()), "p_fail_max_loss": float(fail_m.mean()),
        "p_any_limit_breach": float((fail_d | fail_m).mean()),
        "p_halted_own_rule": float((halted & ~(fail_d | fail_m)).mean()), "p_pass": float(passed.mean()),
        "final_return_pct": {"mean": float(fin.mean() * 100), "p05": q(fin, .05) * 100, "p50": q(fin, .5) * 100, "p95": q(fin, .95) * 100},
        "max_dd_pct": {"median": q(maxdd, .5) * 100, "p95": q(maxdd, .05) * 100, "p99": q(maxdd, .01) * 100},
        "months_to_breach_median": float(np.nanmedian(t_breach) / 21.0) if np.isfinite(t_breach).any() else None,
        "months_to_halt_median": float(np.nanmedian(t_halt) / 21.0) if np.isfinite(t_halt).any() else None,
        "trades_per_path_mean": float(trades.mean()),
        "trades_taken_median": float(np.median(trades)), "trades_taken_p10": float(np.quantile(trades, .10)),
        "trades_before_first_event_median": float(np.nanmedian(trades_at_event)) if ev.any() else None,
        "trades_before_first_event_mean": float(np.nanmean(trades_at_event)) if ev.any() else None,
        "p_no_event": float((~ev).mean()),
        "mean_return_pct_per_month": float(fin.mean() * 100 / (horizon / 21.0)),
    }
    return out


def months_to_limit_table(src: DaySource, fs, horizon: int = 504, n_paths: int = 10_000, seed: int = 21) -> list:
    """Speed of loss vs risk fraction: for each f the share of paths that breach a limit within 6/12/24 months and the median months to the breach."""
    rows = []
    for f in fs:
        prof = Profile(f"f={f}", f)
        r = {"f": f}
        for H, lab in ((126, "6m"), (252, "12m"), (504, "24m")):
            res = profile_mc(src, prof, min(H, horizon), n_paths, seed)
            r[f"p_breach_{lab}"] = res["p_any_limit_breach"]
            r[f"p_fail_max_{lab}"] = res["p_fail_max_loss"]
            r[f"p_fail_daily_{lab}"] = res["p_fail_daily"]
            r[f"mean_ret_{lab}"] = res["final_return_pct"]["mean"]
            r[f"months_to_breach_median_{lab}"] = res["months_to_breach_median"]
            r[f"dd_p95_{lab}"] = res["max_dd_pct"]["p95"]
            r[f"dd_p99_{lab}"] = res["max_dd_pct"]["p99"]
        rows.append(r)
    return rows


# ── layer 1: simulate the day sources ───────────────────────────────────────────────────────

PROFILE_CONFIGS = {              # static settings (SimConfig overrides) per simulation config
    "C25": {"fixed_risk_pct": 0.25, "max_open_trades": 3, "daily_dd_pct": 3.0},
    "C15": {"fixed_risk_pct": 0.15, "max_open_trades": 2, "daily_dd_pct": 2.0},
    "C075": {"fixed_risk_pct": 0.075, "max_open_trades": 2, "daily_dd_pct": 1.5},
}
PROFILES = {
    "P0": ("C25", Profile("P0 current", 0.25)),
    "P1": ("C25", Profile("P1 current+guards", 0.25, halt_loss=0.08, day_stop=0.02, streak_n=8, streak_skip_days=1)),
    "P2": ("C15", Profile("P2 reduced+ladder", 0.15, ladder=((0.03, 0.5), (0.05, 0.25)), halt_loss=0.08, day_stop=0.015, streak_n=6, streak_skip_days=1)),
    "P3": ("C075", Profile("P3 small+ladder", 0.075, ladder=((0.02, 0.5), (0.04, 0.25)), halt_loss=0.06, day_stop=0.01, streak_n=6, streak_skip_days=1)),
}
F_CURVE = (0.05, 0.075, 0.10, 0.15, 0.25, 0.35, 0.50, 1.00)
MU_SCENARIOS = (None, 0.0, 0.05, 0.10)             # None = as simulated
N_SEEDS_DEFAULT = 20
START, DEV_END = "2014-01-01", "2021-06-01"
_STATE: dict = {}


def _load(cand_path, symbols, start, end):
    from research.data import prepare_bars, load_specs
    from research.stoploss_experiment import assert_dev_window
    key = (str(cand_path), tuple(symbols), start, end)
    if _STATE.get("key") != key:
        specs = load_specs()
        cands = pd.read_parquet(cand_path)
        m5 = {s: prepare_bars(s, start=start, end=end)["M5"] for s in symbols}
        assert_dev_window(cands, m5, end)
        _STATE.clear()
        _STATE.update(key=key, specs=specs, cands=cands, m5=m5)
    return _STATE["specs"], _STATE["cands"], _STATE["m5"]


def _work(task: dict) -> dict:
    from research.nulltest import reflect
    from research.quant_methods import GateSim
    from research.stoploss_experiment import base_config
    sd, symbols = task["seed"], task["symbols"]
    specs, cands, m5 = _load(task["cand_path"], symbols, task["start"], task["end"])
    cs = cands if sd is None else reflect(cands, np.random.default_rng(sd).random(len(cands)) < 0.5)
    out = []
    for name, over in PROFILE_CONFIGS.items():
        cfg = replace(base_config(), **over)
        res = GateSim(symbols, m5, specs, cs, cfg).run()
        arr = day_arrays(res["trades"], res["equity"], f0=cfg.fixed_risk_pct)
        done = res["trades"][res["trades"]["exit_reason"] != "END"]
        out.append({"config": name, "seed": -1 if sd is None else sd, "arr": arr, "trades": int(len(done)), "mean_r": float(done["r"].mean()),
                    "max_dd_pct": float((res["equity"]["equity"] / res["equity"]["equity"].cummax() - 1).min() * 100),
                    "return_pct": float((res["equity"]["balance"].iloc[-1] / cfg.initial_balance - 1) * 100)})
    return {"seed": task["seed"], "runs": out}


def days_cmd(a) -> None:
    from research.stoploss_experiment import CANDS_DEFAULT
    if not a.tag.startswith("quant_"):
        sys.exit("tags must start with quant_")
    if pd.Timestamp(a.end) > pd.Timestamp(DEV_END):
        sys.exit(f"--end must be no later than {DEV_END}: the FINAL window is not to be read")
    outdir = OUT / a.tag
    if outdir.exists():
        sys.exit(f"{outdir} exists: output tags are never reused")
    outdir.mkdir(parents=True)
    from config import PAIRS
    symbols = list(PAIRS)
    tasks = [{"seed": sd, "cand_path": str(a.cands or CANDS_DEFAULT), "symbols": symbols, "start": START, "end": a.end}
             for sd in [None] + list(range(a.seeds))]
    t0, parts = time.time(), []
    with ProcessPoolExecutor(max_workers=a.workers) as ex:
        futs = [ex.submit(_work, t) for t in tasks]
        for i, f in enumerate(as_completed(futs), 1):
            parts.append(f.result())
            print(f"[{i}/{len(tasks)}] {time.time() - t0:.0f}s", flush=True)
    meta = []
    for cfgname in PROFILE_CONFIGS:
        for kind in ("A", "N"):
            runs = [r for p in parts for r in p["runs"] if r["config"] == cfgname and ((r["seed"] < 0) == (kind == "A"))]
            runs.sort(key=lambda r: r["seed"])
            if not runs:
                continue
            arrs = [r["arr"] for r in runs]
            K = max(x["rets"].shape[1] for x in arrs)
            np.savez_compressed(outdir / f"{cfgname}_{kind}.npz", n=len(arrs), K=K,
                                **{f"{k}_{j}": x[k] for j, x in enumerate(arrs) for k in ("low", "rets", "risk", "mask", "r")})
            meta.extend({"config": cfgname, "kind": kind, **{k: r[k] for k in ("seed", "trades", "mean_r", "max_dd_pct", "return_pct")}} for r in runs)
    pd.DataFrame(meta).to_csv(outdir / "runs.csv", index=False)
    (outdir / "provenance.json").write_text(json.dumps(_clean({"argv": sys.argv[1:], "configs": PROFILE_CONFIGS, "seeds": a.seeds, "end": a.end}), indent=1))
    print(f"done in {time.time() - t0:.0f}s -> {outdir}")


def load_source(daysdir: Path, cfgname: str, kind: str) -> DaySource:
    z = np.load(daysdir / f"{cfgname}_{kind}.npz")
    runs = [{"low": z[f"low_{j}"], "rets": z[f"rets_{j}"], "risk": z[f"risk_{j}"], "mask": z[f"mask_{j}"], "r": z[f"r_{j}"]} for j in range(int(z["n"]))]
    f0 = PROFILE_CONFIGS[cfgname]["fixed_risk_pct"]
    return build_source(runs, f0)


def report_cmd(a) -> None:
    daysdir = OUT / a.days
    outdir = OUT / a.tag
    if not a.tag.startswith("quant_"):
        sys.exit("tags must start with quant_")
    if outdir.exists():
        sys.exit(f"{outdir} exists: output tags are never reused")
    outdir.mkdir(parents=True)
    res: dict = {"profiles": {}, "curve": {}}
    t0 = time.time()
    for key, (cfgname, prof) in PROFILES.items():
        for kind, label in (("A", "real"), ("N", "null_pool")):
            src = load_source(daysdir, cfgname, kind)
            for mu in MU_SCENARIOS:
                s2 = src if mu is None else shift_mean_r(src, mu)
                for H in (126, 252, 504):
                    r = profile_mc(s2, prof, H, a.paths)
                    r["source_mean_r"] = src.mean_r
                    r["mu"] = "as_simulated" if mu is None else mu
                    res["profiles"][f"{key}|{label}|{r['mu']}|{H}"] = r
        print(f"{key} done {time.time() - t0:.0f}s", flush=True)
    for kind, label in (("A", "real"), ("N", "null_pool")):
        src = load_source(daysdir, "C25", kind)
        res["curve"][label] = months_to_limit_table(src, F_CURVE, 504, min(a.paths, 10_000))
        # the same curve with the mean R moved to zero and to +0.10
        for mu in (0.0, 0.10):
            res["curve"][f"{label}_mu{mu}"] = months_to_limit_table(shift_mean_r(src, mu), F_CURVE, 504, min(a.paths, 10_000))
    (outdir / "summary.json").write_text(json.dumps(_clean(res), indent=1))
    print(f"done in {time.time() - t0:.0f}s -> {outdir / 'summary.json'}")


def demo_cmd(a) -> None:
    """The pre-registered demo-profile rule (quant_risk_prereg.md section 3): over the four profiles and the f grid, as-simulated drag, BOTH sources.
    Metric definitions (logged in the prereg): P(max-loss breach) and P(daily breach) within 12 months (252 days); trades taken = median over ALL paths of the
    trades taken in 24 months (504 days), a path that is halted or has breached simply stops taking trades."""
    daysdir = OUT / a.days
    outdir = OUT / a.tag
    if not a.tag.startswith("quant_") or outdir.exists():
        sys.exit("tag must start with quant_ and must be new")
    outdir.mkdir(parents=True)
    cands = [(k, cfg, prof) for k, (cfg, prof) in PROFILES.items()] + [(f"f={f}", "C25", Profile(f"f={f}", f)) for f in F_CURVE]
    rows = []
    for label, cfgname, prof in cands:
        row = {"candidate": label, "f": prof.f}
        for kind, src_label in (("A", "real"), ("N", "null_pool")):
            src = load_source(daysdir, cfgname, kind)
            r12 = profile_mc(src, prof, 252, a.paths)
            r24 = profile_mc(src, prof, 504, a.paths)
            row[f"{src_label}_p_max_12m"] = r12["p_fail_max_loss"]
            row[f"{src_label}_p_daily_12m"] = r12["p_fail_daily"]
            row[f"{src_label}_trades_median_24m"] = r24["trades_taken_median"]
            row[f"{src_label}_p_halt_24m"] = r24["p_halted_own_rule"]
            row[f"{src_label}_p_breach_24m"] = r24["p_any_limit_breach"]
        row["passes_rule"] = bool(all(row[f"{s}_p_max_12m"] <= 0.01 and row[f"{s}_p_daily_12m"] <= 0.005 and row[f"{s}_trades_median_24m"] >= 500
                                      for s in ("real", "null_pool")))
        rows.append(row)
    df = pd.DataFrame(rows)
    df.to_csv(outdir / "demo_rule.csv", index=False)
    ok = df[df["passes_rule"]]
    print(df.round(4).to_string(index=False))
    print("largest f passing:", ok.sort_values("f").tail(1)[["candidate", "f"]].to_dict("records"))


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    d = sp.add_parser("days")
    d.add_argument("--tag", required=True)
    d.add_argument("--seeds", type=int, default=N_SEEDS_DEFAULT)
    d.add_argument("--workers", type=int, default=6)
    d.add_argument("--cands", default=None)
    d.add_argument("--end", default=DEV_END)
    r = sp.add_parser("report")
    r.add_argument("--days", required=True)
    r.add_argument("--tag", required=True)
    r.add_argument("--paths", type=int, default=20_000)
    dm = sp.add_parser("demo")
    dm.add_argument("--days", required=True)
    dm.add_argument("--tag", required=True)
    dm.add_argument("--paths", type=int, default=20_000)
    a = ap.parse_args(argv)
    {"days": days_cmd, "report": report_cmd, "demo": demo_cmd}[a.cmd](a)


if __name__ == "__main__":
    main()
