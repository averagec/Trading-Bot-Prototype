"""
Monte Carlo layer. Everything here resamples the *simulated trade/day outcomes* - it cannot invent edge,
it only shows how much of the result could be sequence luck and how fragile the account is.

Input unit is `ret_pct` (net P/L as % of balance when the trade opened). Because live sizing is a % of balance
this is approximately scale-free, so paths compound multiplicatively.

Methods
  * iid bootstrap          trades resampled with replacement
  * day-block bootstrap    whole FTMO days resampled (keeps same-day clustering and the 3% daily gate)
  * order shuffle          same trades, random order -> isolates sequencing risk
  * cost / skip stress     extra cost per trade (in R) and randomly missed trades
  * FTMO challenge sim     target / daily-loss / max-loss / min-days on resampled days, using the real
                           intraday equity lows (not just closed-trade P/L)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd


# ── trade-level paths ────────────────────────────────────────────────────────────

def _paths_from_returns(ret_pct: np.ndarray, idx: np.ndarray) -> np.ndarray:
    """idx: (paths, length) int indices into ret_pct -> equity multiple paths (paths, length+1), start=1."""
    r = ret_pct[idx] / 100.0
    eq = np.cumprod(1.0 + r, axis=1)
    return np.concatenate([np.ones((len(eq), 1)), eq], axis=1)


def _dd_pct(paths: np.ndarray) -> np.ndarray:
    peak = np.maximum.accumulate(paths, axis=1)
    return ((paths - peak) / peak).min(axis=1) * 100


def trade_mc(ret_pct: np.ndarray, n_paths: int = 20_000, n_trades: Optional[int] = None,
             mode: str = "iid", seed: int = 7) -> dict:
    """mode: 'iid' (with replacement) | 'shuffle' (permutation of the observed trades)."""
    ret_pct = np.asarray(ret_pct, float)
    ret_pct = ret_pct[~np.isnan(ret_pct)]
    n = len(ret_pct)
    if n < 5:
        return {"error": "too few trades"}
    L = n_trades or n
    rng = np.random.default_rng(seed)
    if mode == "shuffle":
        idx = np.argsort(rng.random((n_paths, n)), axis=1)[:, :L]
    else:
        idx = rng.integers(0, n, size=(n_paths, L))
    paths = _paths_from_returns(ret_pct, idx)
    final = (paths[:, -1] - 1) * 100
    dd = _dd_pct(paths)
    q = lambda a, p: float(np.quantile(a, p))
    return {
        "mode": mode, "paths": n_paths, "trades_per_path": L,
        "final_return_pct": {"p05": q(final, .05), "p25": q(final, .25), "p50": q(final, .5),
                             "p75": q(final, .75), "p95": q(final, .95)},
        "max_dd_pct": {"median": q(dd, .5), "p95": q(dd, .05), "p99": q(dd, .01)},   # worst 5% / 1% of paths
        "p_profit": float((final > 0).mean()),
        "p_dd_worse_than": {f"{t}%": float((dd <= -t).mean()) for t in (5, 10, 15, 20, 30)},
    }


def stress_tests(trades: pd.DataFrame, n_paths: int = 10_000, seed: int = 11) -> dict:
    """Extra cost per trade (in R) and random missed trades, on top of the iid bootstrap."""
    t = trades[trades["exit_reason"] != "END"]
    if len(t) < 5:
        return {}
    base = t["ret_pct"].to_numpy(float)
    # risk per trade as % of balance = |ret_pct / r|  (rows with r == 0 get the median)
    rp = (t["ret_pct"] / t["r"]).replace([np.inf, -np.inf], np.nan).abs()
    rp = rp.fillna(rp.median()).to_numpy()
    out = {}
    for extra_r in (0.0, 0.05, 0.10, 0.20, 0.30):
        adj = base - extra_r * rp
        res = trade_mc(adj, n_paths=n_paths, mode="iid", seed=seed)
        out[f"extra_cost_{extra_r:.2f}R"] = {"mean_r": float(np.nanmean(t["r"] - extra_r)),
                                             "p_profit": res["p_profit"],
                                             "median_return_pct": res["final_return_pct"]["p50"],
                                             "p95_dd_pct": res["max_dd_pct"]["p95"]}
    rng = np.random.default_rng(seed)
    for miss in (0.1, 0.25, 0.5):
        keep = base[rng.random(len(base)) > miss]
        if len(keep) >= 5:
            res = trade_mc(keep, n_paths=n_paths, mode="iid", seed=seed)
            out[f"missed_{int(miss*100)}pct"] = {"trades": len(keep), "p_profit": res["p_profit"],
                                                 "median_return_pct": res["final_return_pct"]["p50"]}
    return out


# ── FTMO challenge simulation (day-block bootstrap) ──────────────────────────────

@dataclass
class FtmoRules:
    initial: float = 100_000.0
    profit_target_pct: float = 10.0
    max_daily_loss_pct: float = 5.0     # of INITIAL balance, vs day-start balance (check against FTMO's current terms)
    max_loss_pct: float = 10.0          # static, of initial balance
    min_trading_days: int = 4
    max_days: int = 90                  # give-up horizon (FTMO has no time limit; this bounds the sim)
    block_len: int = 5                  # consecutive trading days resampled together


def ftmo_mc(days: pd.DataFrame, rules: FtmoRules = FtmoRules(), n_paths: int = 20_000, seed: int = 21) -> dict:
    if len(days) < 20:
        return {"error": "too few days"}
    pnl = days["pnl_pct"].to_numpy() / 100.0
    low = days["low_pct"].to_numpy() / 100.0
    traded = days["traded"].to_numpy()
    nD = len(days)
    rng = np.random.default_rng(seed)
    B = rules.block_len
    n_blocks = int(np.ceil(rules.max_days / B))
    starts = rng.integers(0, nD - B + 1, size=(n_paths, n_blocks))
    idx = (starts[:, :, None] + np.arange(B)[None, None, :]).reshape(n_paths, -1)[:, :rules.max_days]

    bal = np.full(n_paths, rules.initial)
    alive = np.ones(n_paths, bool)
    state = np.zeros(n_paths, int)                      # 0 running, 1 pass, 2 fail daily, 3 fail max
    t_done = np.full(n_paths, np.nan)
    tdays = np.zeros(n_paths, int)
    target = rules.initial * (1 + rules.profit_target_pct / 100)
    floor = rules.initial * (1 - rules.max_loss_pct / 100)
    daily_lim = rules.initial * rules.max_daily_loss_pct / 100
    max_dd_seen = np.zeros(n_paths)
    peak = bal.copy()

    for d in range(idx.shape[1]):
        i = idx[:, d]
        run = alive
        eq_low = bal * (1 + low[i])
        breach_daily = run & ((eq_low - bal) <= -daily_lim)
        breach_max = run & (eq_low <= floor)
        # a day that breaches both is attributed to the daily rule
        state[breach_max & ~breach_daily] = 3
        state[breach_daily] = 2
        t_done[(breach_daily | breach_max)] = d + 1
        alive &= ~(breach_daily | breach_max)
        run = alive
        bal = np.where(run, bal * (1 + pnl[i]), bal)
        tdays += (run & traded[i]).astype(int)
        peak = np.maximum(peak, bal)
        max_dd_seen = np.minimum(max_dd_seen, np.where(run, bal / peak - 1, max_dd_seen))
        passed = run & (bal >= target) & (tdays >= rules.min_trading_days)
        state[passed] = 1
        t_done[passed] = d + 1
        alive &= ~passed

    res = {
        "rules": rules.__dict__, "paths": n_paths,
        "p_pass": float((state == 1).mean()),
        "p_fail_daily_loss": float((state == 2).mean()),
        "p_fail_max_loss": float((state == 3).mean()),
        "p_unresolved_at_horizon": float((state == 0).mean()),
        "median_days_to_pass": float(np.nanmedian(t_done[state == 1])) if (state == 1).any() else None,
        "p90_days_to_pass": float(np.nanquantile(t_done[state == 1], .9)) if (state == 1).any() else None,
        "median_final_balance": float(np.median(bal)),
    }
    return res


def edge_summary(trades: pd.DataFrame) -> dict:
    """How many independent trades would be needed to see the observed edge, given its noise."""
    t = trades[trades["exit_reason"] != "END"]
    r = t["r"].to_numpy(float)
    r = r[~np.isnan(r)]
    if len(r) < 5:
        return {}
    m, s = r.mean(), r.std(ddof=1)
    se = s / np.sqrt(len(r))
    need = None
    if m > 0:
        need = int(np.ceil((1.96 + 0.84) ** 2 * (s / m) ** 2))        # ~80% power at 5% two-sided
    return {"n": len(r), "mean_r": float(m), "std_r": float(s), "std_error_r": float(se), "t_stat": float(m / se),
            "trades_needed_for_80pct_power_at_observed_edge": need}
