"""
Loss anatomy of the live rules on the DEV window (descriptive; pre-registration: research/reports/quant_anatomy_prereg.md).

Replays the stage-0 DEV candidates once with the live settings (one deterministic real-signal run; nothing is tuned), then decomposes the
trade log: cost per R, stop-out rate by stop size / spread-to-stop / stop-in-ATR, exit mix, MAE and MFE, first-touch odds of 0.5R/1R/2R/3R
against the stop, losing streaks against an iid benchmark, drawdown anatomy and concurrent exposure.

    python -m research.quant_loss anatomy --tag quant_anatomy

DEV only (2014-01-01 to 2021-06-01); the FINAL window is refused. Read-only to the live code, sim.py and signals.py.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from research.data import prepare_bars, load_specs
from research.metrics import max_drawdown_pct
from research.quant_methods import add_atr, START, DEV_END
from research.run import OUT, _clean
from research.sim import Simulator
from research.stoploss_experiment import CANDS_DEFAULT, base_config, pip_size, assert_dev_window

LEVELS = (0.5, 1.0, 2.0, 3.0)         # R levels for the first-touch odds
SCAN_BARS = 1440                       # stateless scan horizon: 5 days of M5 bars


# ── cost per R ──────────────────────────────────────────────────────────────────────────────

def cost_table(t: pd.DataFrame, specs: dict, by: Optional[str] = None) -> pd.DataFrame:
    """Mean R split into: pre-cost R (direction + structure) , entry spread over the stop (proxy), commission over risk.
    net = gross - commission, and gross already contains the spread paid at the fill, so pre-cost = gross + spread proxy."""
    t = t[t["exit_reason"] != "END"].copy()
    point = t["symbol"].map({s: sp["point"] for s, sp in specs.items()}).to_numpy(float)
    dist = (t["entry"] - t["sl0"]).abs().to_numpy(float)
    t["spread_r"] = t["spread_pts"].to_numpy(float) * point / dist
    t["comm_r"] = (t["commission"] / t["risk_usd"]).to_numpy(float)
    t["gross_r"] = (t["gross_pnl"] / t["risk_usd"]).to_numpy(float)
    t["pre_cost_r"] = t["gross_r"] + t["spread_r"]
    g = t.groupby(by) if by else t.assign(_all="all").groupby("_all")
    out = g.agg(n=("r", "size"), mean_r=("r", "mean"), pre_cost_r=("pre_cost_r", "mean"), spread_r=("spread_r", "mean"),
                comm_r=("comm_r", "mean"), win_pct=("net_pnl", lambda x: 100 * (x > 0).mean()))
    out["cost_r"] = out["spread_r"] + out["comm_r"]
    return out


# ── bucket tables ───────────────────────────────────────────────────────────────────────────

def exit_shares(g: pd.DataFrame) -> pd.Series:
    n = len(g)
    return pd.Series({"n": n, "mean_r": g["r"].mean(),
                      "sl_pct": 100 * (g["exit_reason"] == "SL").mean(), "be_pct": 100 * (g["exit_reason"] == "BE").mean(),
                      "tp_pct": 100 * (g["exit_reason"] == "TP").mean(), "flat_pct": 100 * (g["exit_reason"] == "FLAT").mean(),
                      "fillbar_stop_pct": 100 * ((g["hold_min"] == 0) & g["exit_reason"].isin(["SL", "BE"])).mean(),
                      "loss_pct": 100 * (g["net_pnl"] < 0).mean()})


def bucket_table(t: pd.DataFrame, col: str, bins) -> pd.DataFrame:
    x = t[t["exit_reason"] != "END"].copy()
    x["bucket"] = pd.cut(x[col], bins)
    return x.groupby("bucket", observed=True).apply(exit_shares, include_groups=False)


def enrich_stops(t: pd.DataFrame, specs: dict, cands_atr: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """stop in pips, spread/stop %, and (if candidates with `atr_h1` are given) stop in H1-ATR units, joined on (symbol, placed_T == candidate T)."""
    x = t[t["exit_reason"] != "END"].copy()
    pip = x["symbol"].map({s: pip_size(sp) for s, sp in specs.items()}).to_numpy(float)
    point = x["symbol"].map({s: sp["point"] for s, sp in specs.items()}).to_numpy(float)
    dist = (x["entry"] - x["sl0"]).abs().to_numpy(float)
    x["stop_pips"] = dist / pip
    x["spread_to_stop_pct"] = 100 * x["spread_pts"].to_numpy(float) * point / dist
    if cands_atr is not None:
        a = cands_atr[["symbol", "T", "atr_h1"]].copy()
        a["placed_T"] = pd.to_datetime(a["T"], utc=True)
        x["placed_T"] = pd.to_datetime(x["placed_T"], utc=True)
        x = x.merge(a[["symbol", "placed_T", "atr_h1"]].drop_duplicates(["symbol", "placed_T"]), on=["symbol", "placed_T"], how="left")
        x["stop_atr"] = dist_of(x) / x["atr_h1"]
    return x


def dist_of(x: pd.DataFrame) -> pd.Series:
    return (x["entry"] - x["sl0"]).abs()


# ── MAE / MFE and first-touch odds ──────────────────────────────────────────────────────────

def _symbol_arrays(m5: pd.DataFrame, point: float):
    T = (pd.to_datetime(m5["time"], utc=True) + pd.Timedelta(minutes=5)).astype("int64").to_numpy()      # bar CLOSE time, as the simulator
    return T, m5["high"].to_numpy(float), m5["low"].to_numpy(float), m5["spread_pts"].to_numpy(float) * point


def excursions(t: pd.DataFrame, m5: dict, specs: dict, levels=LEVELS, scan_bars: int = SCAN_BARS) -> pd.DataFrame:
    """Per finished trade (same index as the non-END rows of `t`): MFE and MAE in units of the initial risk distance |entry - sl0|.

    Bars used: those whose close time lies in (open_T, close_T]. The exit bar itself is excluded from `mfe_pre` (what price had done before the
    exit bar) and included in `mfe_incl`; a fill-bar exit has no later bar, so mfe_pre = 0 and mae = 1 by construction (it was stopped on its fill bar).
    Prices follow the simulator: BUY uses bid high/low, SELL uses ask = bid + spread.

    First-touch odds (`ft_<level>`): a STATELESS scan from the fill, over at most `scan_bars` M5 bars (ignoring breakeven and the flatten): +level R reached
    strictly before -1R (bar ties go to the stop, as in the simulator) -> 1, stop first -> 0, neither within the horizon -> NaN.
    """
    t = t[t["exit_reason"] != "END"]
    arr = {s: _symbol_arrays(m5[s], specs[s]["point"]) for s in m5}
    out = []
    for idx, r in zip(t.index, t.itertuples(index=False)):
        T, H, L, S = arr[r.symbol]
        o, c = pd.Timestamp(r.open_T).value, pd.Timestamp(r.close_T).value
        a = int(np.searchsorted(T, o, side="right"))
        b = int(np.searchsorted(T, c, side="right"))
        dist = abs(r.entry - r.sl0)
        row = {"idx": idx, "mfe_pre": 0.0, "mfe_incl": 0.0, "mae": 1.0 if (b <= a and r.exit_reason in ("SL", "BE")) else 0.0}
        if b > a:
            if r.direction == "BUY":
                fav, adv = (H[a:b] - r.entry) / dist, (r.entry - L[a:b]) / dist
            else:
                fav, adv = (r.entry - (L[a:b] + S[a:b])) / dist, ((H[a:b] + S[a:b]) - r.entry) / dist
            row["mfe_incl"] = float(max(fav.max(), 0.0))
            row["mfe_pre"] = float(max(fav[:-1].max(), 0.0)) if len(fav) > 1 else 0.0
            row["mae"] = float(max(adv.max(), 0.0))
        # stateless first-touch scan from the fill
        z = min(len(T), a + scan_bars)
        if z > a:
            if r.direction == "BUY":
                f2, a2 = (H[a:z] - r.entry) / dist, (r.entry - L[a:z]) / dist
            else:
                f2, a2 = (r.entry - (L[a:z] + S[a:z])) / dist, ((H[a:z] + S[a:z]) - r.entry) / dist
            stop_hit = np.nonzero(a2 >= 1.0)[0]
            ks = int(stop_hit[0]) if len(stop_hit) else None
            for lv in levels:
                hit = np.nonzero(f2 >= lv)[0]
                kt = int(hit[0]) if len(hit) else None
                if kt is None and ks is None:
                    row[f"ft_{lv}"] = np.nan
                elif kt is None:
                    row[f"ft_{lv}"] = 0.0
                elif ks is None:
                    row[f"ft_{lv}"] = 1.0
                else:
                    row[f"ft_{lv}"] = 1.0 if kt < ks else 0.0
        else:
            for lv in levels:
                row[f"ft_{lv}"] = np.nan
        out.append(row)
    e = pd.DataFrame(out).set_index("idx")
    return e


def excursion_tables(t: pd.DataFrame, e: pd.DataFrame) -> dict:
    x = t.loc[e.index].join(e)
    res = {}
    # reaching a level at all before the exit (high-based, bars before the exit bar)
    rows = []
    for name, g in (("all", x), ("SL", x[x.exit_reason == "SL"]), ("BE", x[x.exit_reason == "BE"]),
                    ("TP", x[x.exit_reason == "TP"]), ("FLAT", x[x.exit_reason == "FLAT"])):
        rows.append({"group": name, "n": len(g), **{f"mfe_pre>={lv}R_pct": 100 * float((g["mfe_pre"] >= lv).mean()) for lv in (0.5, 1.0, 2.0, 3.0)},
                     **{f"mfe_incl>={lv}R_pct": 100 * float((g["mfe_incl"] >= lv).mean()) for lv in (0.5, 1.0)},
                     "median_mae_R": float(g["mae"].median()), "mean_mfe_pre_R": float(g["mfe_pre"].mean())})
    res["reach"] = pd.DataFrame(rows).set_index("group")
    # stateless first-touch odds vs the iid driftless benchmark 1/(1+L)
    ft = []
    for lv in LEVELS:
        v = x[f"ft_{lv}"].dropna()
        ft.append({"level_R": lv, "n_resolved": len(v), "censored_pct": 100 * float(x[f"ft_{lv}"].isna().mean()),
                   "p_target_before_stop_pct": 100 * float(v.mean()), "driftless_zero_cost_pct": 100 / (1 + lv)})
    res["first_touch"] = pd.DataFrame(ft).set_index("level_R")
    # breakeven trigger: close-based in the sim; how many SL exits touched +1R on the high first
    sl = x[x.exit_reason == "SL"]
    res["be_gap"] = {"sl_exits": int(len(sl)), "sl_exits_mfe_pre_ge_1R": int((sl["mfe_pre"] >= 1.0).sum()),
                     "sl_exits_mfe_pre_ge_0.5R": int((sl["mfe_pre"] >= 0.5).sum()),
                     "sl_exits_mfe_incl_ge_1R": int((sl["mfe_incl"] >= 1.0).sum())}
    return res


# ── streaks, drawdown, exposure ─────────────────────────────────────────────────────────────

def streak_lengths(loss: np.ndarray) -> list:
    out, cur = [], 0
    for f in loss:
        if f:
            cur += 1
        elif cur:
            out.append(cur); cur = 0
    if cur:
        out.append(cur)
    return out


def streak_table(t: pd.DataFrame, n_sim: int = 5000, seed: int = 11) -> dict:
    """Losing streaks of finished trades in close order (loss = net P/L < 0) against an iid benchmark with the same loss rate and length."""
    x = t[t["exit_reason"] != "END"].sort_values("close_T")
    loss = (x["net_pnl"] < 0).to_numpy()
    p, n = float(loss.mean()), len(loss)
    obs = streak_lengths(loss)
    rng = np.random.default_rng(seed)
    sims_max, sims_ge = [], {k: [] for k in (5, 8, 10, 12, 15)}
    for _ in range(n_sim):
        s = streak_lengths(rng.random(n) < p)
        sims_max.append(max(s) if s else 0)
        for k in sims_ge:
            sims_ge[k].append(sum(1 for L in s if L >= k))
    cnt = pd.Series(obs).value_counts().sort_index()
    return {"n": n, "loss_rate": p, "observed_max": int(max(obs)), "observed_mean_streak": float(np.mean(obs)),
            "iid_max_mean": float(np.mean(sims_max)), "iid_max_p05_p95": [float(np.quantile(sims_max, .05)), float(np.quantile(sims_max, .95))],
            "p_iid_max_ge_observed": float(np.mean(np.array(sims_max) >= max(obs))),
            "streaks_ge": {k: {"observed": int(sum(1 for L in obs if L >= k)), "iid_mean": float(np.mean(v))} for k, v in sims_ge.items()},
            "length_counts": {int(k): int(v) for k, v in cnt.items()}}


def drawdown_anatomy(equity: pd.DataFrame, trades: pd.DataFrame, top: int = 5) -> dict:
    """Balance and equity drawdown, the worst episodes (peak, trough, recovery), months, and what filled the worst episode."""
    eq = equity.set_index("T")["equity"]
    bal = equity.set_index("T")["balance"]
    peak = eq.cummax()
    dd = eq / peak - 1.0
    out = {"max_dd_equity_pct": float(dd.min() * 100), "max_dd_balance_pct": max_drawdown_pct(bal.to_numpy()),
           "final_equity_pct_of_start": float(eq.iloc[-1] / eq.iloc[0] * 100), "share_of_time_below_prior_peak_pct": float((dd < -1e-9).mean() * 100)}
    # episodes: contiguous spans below a peak
    under = dd < -1e-9
    ep, start = [], None
    for ts, u in under.items():
        if u and start is None:
            start = ts
        if not u and start is not None:
            ep.append((start, ts)); start = None
    if start is not None:
        ep.append((start, None))
    rows = []
    for s, e in ep:
        seg = dd.loc[s:] if e is None else dd.loc[s:e]
        tr = seg.idxmin()
        rows.append({"start": s, "trough": tr, "recovered": e, "depth_pct": float(seg.min() * 100),
                     "days_to_trough": (tr - s).days, "days_total": ((e or dd.index[-1]) - s).days})
    ep_df = pd.DataFrame(rows).sort_values("depth_pct").head(top) if rows else pd.DataFrame()
    out["episodes"] = ep_df
    d = bal.resample("1D").last().dropna()
    m = bal.resample("MS").last().pct_change().dropna() * 100
    w = bal.resample("W-SUN").last().pct_change().dropna() * 100
    out["monthly"] = {"n": int(len(m)), "negative_pct": float((m < 0).mean() * 100), "mean": float(m.mean()), "worst": float(m.min()),
                      "p05": float(m.quantile(.05)), "best": float(m.max())}
    out["weekly"] = {"worst": float(w.min()), "p05": float(w.quantile(.05)), "negative_pct": float((w < 0).mean() * 100)}
    out["daily_worst_pct"] = float(d.pct_change().dropna().min() * 100)
    # worst-episode composition: trades closed between the start and the trough of the deepest episode
    if len(ep_df):
        r0 = ep_df.iloc[0]
        t = trades[(trades["close_T"] >= r0["start"]) & (trades["close_T"] <= r0["trough"]) & (trades["exit_reason"] != "END")]
        out["worst_episode_trades"] = {"n": int(len(t)), "sum_r": float(t["r"].sum()),
                                       "by_exit_sum_r": t.groupby("exit_reason")["r"].sum().round(1).to_dict(),
                                       "by_symbol_sum_r": t.groupby("symbol")["r"].sum().round(1).to_dict()}
    return out


def exposure_table(trades: pd.DataFrame) -> dict:
    """Concurrent open positions and open risk from the trade intervals (the live max-open gate is read once per cycle, so the count can pass 3)."""
    t = trades[trades["exit_reason"] != "END"]
    t = t[pd.to_datetime(t["close_T"], utc=True) > pd.to_datetime(t["open_T"], utc=True)]       # fill-bar exits hold no time and carry no overlap
    ev = pd.concat([pd.DataFrame({"T": pd.to_datetime(t["open_T"], utc=True), "d": 1, "risk": t["risk_pct"].to_numpy(float)}),
                    pd.DataFrame({"T": pd.to_datetime(t["close_T"], utc=True), "d": -1, "risk": -t["risk_pct"].to_numpy(float)})])
    ev = ev.sort_values(["T", "d"])             # closes before opens at the same instant
    ev["open"] = ev["d"].cumsum()
    ev["open_risk"] = ev["risk"].cumsum()
    dt = ev["T"].shift(-1) - ev["T"]
    w = dt.dt.total_seconds().fillna(0.0)
    tot = float(w.sum())
    share = {int(k): float(w[ev["open"] == k].sum() / tot * 100) for k in sorted(ev["open"].unique())}
    return {"max_open": int(ev["open"].max()), "max_open_risk_pct": float(ev["open_risk"].max()),
            "time_share_by_open_count_pct": share, "share_time_ge_1_open_pct": float(100 - share.get(0, 0.0))}


def worst_trades(t: pd.DataFrame, k: int = 10) -> pd.DataFrame:
    x = t[t["exit_reason"] != "END"].nsmallest(k, "r")
    return x[["symbol", "direction", "open_T", "close_T", "exit_reason", "r", "hold_min", "subtype"]]


# ── driver ──────────────────────────────────────────────────────────────────────────────────

def _fmt(df: pd.DataFrame, nd: int = 3) -> str:
    return df.round(nd).to_string()


def anatomy_cmd(a) -> None:
    if not a.tag.startswith("quant_"):
        sys.exit("tags must start with quant_")
    outdir = OUT / a.tag
    if outdir.exists():
        sys.exit(f"{outdir} exists: output tags are never reused")
    outdir.mkdir(parents=True)
    from config import PAIRS
    symbols, specs = list(PAIRS), load_specs()
    bars = {s: prepare_bars(s, start=START, end=DEV_END, sentinel_missing=a.sentinel_missing) for s in symbols}
    m5 = {s: bars[s]["M5"] for s in symbols}
    cands = pd.read_parquet(a.cands)
    assert_dev_window(cands, m5, DEV_END)
    cands_atr = add_atr(cands, {s: bars[s]["H1"] for s in symbols})
    cfg = base_config()
    res = Simulator(symbols, m5, specs, cands, cfg).run()
    tr, eq = res["trades"], res["equity"]
    done = tr[tr["exit_reason"] != "END"]
    summ = {"trades": int(len(done)), "mean_r": float(done["r"].mean()), "funnel": res["funnel"]}
    print("real run:", summ["trades"], "trades, mean R", round(summ["mean_r"], 4))
    tr.to_csv(outdir / "trades.csv", index=False)
    eq.to_parquet(outdir / "equity.parquet", index=False)

    tabs, text = {}, []

    def add(title, obj):
        text.append(f"\n=== {title} ===")
        text.append(_fmt(obj) if isinstance(obj, pd.DataFrame) else json.dumps(_clean(obj), indent=1, default=str))

    add("A1 cost per R (all)", cost_table(tr, specs))
    add("A1 cost per R by pair", cost_table(tr, specs, "symbol"))
    add("A1 cost per R by exit reason", cost_table(tr, specs, "exit_reason"))
    nog = tr[tr["symbol"] != "XAUUSD"]
    add("A1 cost per R without XAUUSD", cost_table(nog, specs))
    x = enrich_stops(tr, specs, cands_atr)
    add("A2 outcome by stop size (pips; gold's pip is 0.01 dollar)", bucket_table(x[x.symbol != "XAUUSD"], "stop_pips", [0, 10.01, 12, 15, 20, 30, 1000]))
    add("A2 outcome by spread / stop (%)", bucket_table(x, "spread_to_stop_pct", [0, 3, 5, 7, 8.5, 10]))
    add("A2 outcome by stop in H1-ATR units", bucket_table(x, "stop_atr", [0, 0.25, 0.4, 0.55, 0.75, 1.0, 5]))
    add("A3 exit mix by pair", x.groupby("symbol").apply(exit_shares, include_groups=False))
    add("A3 exit mix by entry subtype", x.groupby("subtype").apply(exit_shares, include_groups=False))
    add("A3 exit mix overall", exit_shares(x).to_frame("all").T)
    contrib = x.groupby("exit_reason").agg(n=("r", "size"), mean_r=("r", "mean"), sum_r=("r", "sum"))
    contrib["share_of_total_loss_pct"] = 100 * contrib["sum_r"] / x["r"].sum()
    add("A3 R contribution by exit reason", contrib)
    by_year = x.assign(year=pd.to_datetime(x["open_T"], utc=True).dt.year).groupby("year").apply(exit_shares, include_groups=False)
    add("A3 by year (open year)", by_year)
    sent = pd.to_datetime(x["open_T"], utc=True)
    era = np.where(sent >= pd.Timestamp("2020-05-01", tz="UTC"), "2020-05..2021-05 (50-pt spread placeholder era)", "2014-01..2020-04")
    add("A3 by data era", x.assign(era=era).groupby("era").apply(exit_shares, include_groups=False))
    e = excursions(tr, m5, specs)
    et = excursion_tables(tr, e)
    add("A4 reach of +0.5/1/2/3 R before the exit bar (high-based)", et["reach"])
    add("A4 stateless first-touch odds, +level before -1R", et["first_touch"])
    add("A4 stop-outs that touched +0.5R / +1R first", et["be_gap"])
    e.join(tr[["symbol", "exit_reason"]]).to_csv(outdir / "excursions.csv")
    add("A5 losing streaks", streak_table(tr))
    dda = drawdown_anatomy(eq, tr)
    add("A6 drawdown anatomy", {k: v for k, v in dda.items() if k != "episodes"})
    add("A6 worst drawdown episodes", dda["episodes"])
    add("A6 exposure", exposure_table(tr))
    add("A6 ten worst trades", worst_trades(tr))
    add("gate funnel of the real run", pd.Series(res["funnel"]).sort_values(ascending=False).to_frame("count"))
    (outdir / "anatomy.txt").write_text("\n".join(text), encoding="utf-8")
    (outdir / "summary.json").write_text(json.dumps(_clean({"summary": summ, "streaks": streak_table(tr), "reach": et["reach"].to_dict("index"),
                                                           "first_touch": et["first_touch"].to_dict("index"), "be_gap": et["be_gap"],
                                                           "exposure": exposure_table(tr),
                                                           "drawdown": {k: v for k, v in dda.items() if k != "episodes"}}), indent=1, default=str))
    print("\n".join(text))


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    r = sp.add_parser("anatomy")
    r.add_argument("--tag", required=True)
    r.add_argument("--cands", default=str(CANDS_DEFAULT))
    r.add_argument("--sentinel-missing", action="store_true")
    a = ap.parse_args(argv)
    {"anatomy": anatomy_cmd}[a.cmd](a)


if __name__ == "__main__":
    main()
