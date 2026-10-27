"""Performance metrics + breakdowns for a simulated trade log."""

from __future__ import annotations

import numpy as np
import pandas as pd


def bootstrap_mean_ci(x: np.ndarray, n: int = 10_000, alpha: float = 0.05, seed: int = 1) -> dict:
    x = np.asarray(x, float)
    x = x[~np.isnan(x)]
    if len(x) < 2:
        return {"lo": np.nan, "hi": np.nan, "p_le_0": np.nan}
    rng = np.random.default_rng(seed)
    means = rng.choice(x, size=(n, len(x)), replace=True).mean(axis=1)
    return {"lo": float(np.quantile(means, alpha / 2)), "hi": float(np.quantile(means, 1 - alpha / 2)),
            "p_le_0": float((means <= 0).mean())}


def max_drawdown_pct(equity: np.ndarray) -> float:
    equity = np.asarray(equity, float)
    if len(equity) == 0:
        return 0.0
    peak = np.maximum.accumulate(equity)
    return float(((equity - peak) / peak).min() * 100)


def _streak(flags: np.ndarray) -> int:
    best = cur = 0
    for f in flags:
        cur = cur + 1 if f else 0
        best = max(best, cur)
    return best


def summarize(trades: pd.DataFrame, equity: pd.DataFrame, initial_balance: float) -> dict:
    """Headline numbers. 'END' trades (still open when data ended) are excluded from trade statistics."""
    out: dict = {}
    t = trades[trades["exit_reason"] != "END"] if len(trades) else trades
    out["trades"] = int(len(t))
    out["open_at_end"] = int(len(trades) - len(t)) if len(trades) else 0
    if len(equity):
        out["start"] = str(equity["T"].iloc[0]); out["end"] = str(equity["T"].iloc[-1])
        days = max((equity["T"].iloc[-1] - equity["T"].iloc[0]).days, 1)
        out["calendar_days"] = int(days)
        out["final_balance"] = float(equity["balance"].iloc[-1])
        out["return_pct"] = float((equity["balance"].iloc[-1] / initial_balance - 1) * 100)
        out["max_dd_equity_pct"] = max_drawdown_pct(equity["equity"].to_numpy())
        out["max_dd_balance_pct"] = max_drawdown_pct(equity["balance"].to_numpy())
        d = equity.set_index("T")["equity"].resample("1D").last().dropna()
        r = d.pct_change().dropna()
        out["sharpe_daily_ann"] = float(r.mean() / r.std() * np.sqrt(252)) if r.std() > 0 else np.nan
        neg = r[r < 0]
        out["sortino_daily_ann"] = float(r.mean() / neg.std() * np.sqrt(252)) if len(neg) > 1 and neg.std() > 0 else np.nan
    if not len(t):
        return out

    r = t["r"].to_numpy(float)
    wins, losses = t[t["net_pnl"] > 0], t[t["net_pnl"] < 0]
    out["win_rate_pct"] = float(len(wins) / len(t) * 100)
    out["tp_rate_pct"] = float((t["exit_reason"] == "TP").mean() * 100)
    out["exit_mix_pct"] = (t["exit_reason"].value_counts(normalize=True) * 100).round(1).to_dict()
    out["expectancy_r"] = float(np.nanmean(r))
    out["median_r"] = float(np.nanmedian(r))
    out["std_r"] = float(np.nanstd(r, ddof=1)) if len(r) > 1 else np.nan
    out["expectancy_r_ci95"] = bootstrap_mean_ci(r)
    gp, gl = wins["net_pnl"].sum(), -losses["net_pnl"].sum()
    out["profit_factor"] = float(gp / gl) if gl > 0 else np.nan
    out["avg_win_r"] = float(wins["r"].mean()) if len(wins) else np.nan
    out["avg_loss_r"] = float(losses["r"].mean()) if len(losses) else np.nan
    out["avg_hold_min"] = float(t["hold_min"].mean())
    out["net_pnl"] = float(t["net_pnl"].sum())
    out["commission_total"] = float(t["commission"].sum())
    out["commission_in_r"] = float((t["commission"] / t["risk_usd"]).mean())
    out["longest_losing_streak"] = _streak((t.sort_values("close_T")["net_pnl"] <= 0).to_numpy())
    if "calendar_days" in out:
        out["trades_per_month"] = float(len(t) / (out["calendar_days"] / 30.4))
    return out


def breakdowns(trades: pd.DataFrame) -> dict:
    t = trades[trades["exit_reason"] != "END"].copy() if len(trades) else trades
    if not len(t):
        return {}

    def agg(g):
        return pd.Series({
            "n": len(g), "win%": round((g["net_pnl"] > 0).mean() * 100, 1),
            "exp_R": round(g["r"].mean(), 3), "sum_R": round(g["r"].sum(), 1),
            "PF": round(g.loc[g.net_pnl > 0, "net_pnl"].sum() / max(-g.loc[g.net_pnl < 0, "net_pnl"].sum(), 1e-9), 2),
        })

    t["month"] = t["open_T"].dt.strftime("%Y-%m")
    t["hour_utc"] = t["open_T"].dt.hour
    return {
        "by_symbol":    t.groupby("symbol").apply(agg, include_groups=False),
        "by_direction": t.groupby("direction").apply(agg, include_groups=False),
        "by_month":     t.groupby("month").apply(agg, include_groups=False),
        "by_hour":      t.groupby("hour_utc").apply(agg, include_groups=False),
        "by_exit":      t.groupby("exit_reason").apply(agg, include_groups=False),
        "by_pattern":   t.groupby("pattern").apply(agg, include_groups=False),
        "by_subtype":   t.groupby("subtype").apply(agg, include_groups=False),
    }


def day_table(equity: pd.DataFrame, trades: pd.DataFrame, tz: str = "Europe/Prague") -> pd.DataFrame:
    """
    One row per FTMO trading day (midnight CE(S)T): % balance change and the intraday equity LOW
    relative to the day's starting balance. Used as the resampling unit for the FTMO challenge simulation.
    """
    e = equity.copy()
    e["day"] = e["T"].dt.tz_convert(tz).dt.strftime("%Y-%m-%d")
    g = e.groupby("day")
    start_bal = g["balance"].first()
    end_bal = g["balance"].last()
    low_eq = g["equity"].min()
    tab = pd.DataFrame({
        "pnl_pct": (end_bal / start_bal - 1) * 100,
        "low_pct": (low_eq / start_bal - 1) * 100,
    })
    if len(trades):
        td = trades.copy()
        td["day"] = td["open_T"].dt.tz_convert(tz).dt.strftime("%Y-%m-%d")
        tab["traded"] = tab.index.isin(td["day"])
    else:
        tab["traded"] = False
    return tab[tab.index.map(lambda d: pd.Timestamp(d).dayofweek < 5)]      # no weekend "days"
