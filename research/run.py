"""
Run the faithful backtest + Monte Carlo and write a report.

    python -m research.run                       # base run (faithful live logic)
    python -m research.run --variants            # + pre-declared sensitivity variants
    python -m research.run --tag mytest --start 2025-09-01 --oos-start 2026-05-01
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from research.data import prepare_bars, load_specs
from research.signals import SignalConfig, build_candidates, build_candidates_cached
from research.sim import Simulator, SimConfig
from research.metrics import summarize, breakdowns, day_table
from research.montecarlo import trade_mc, ftmo_mc, FtmoRules, stress_tests, edge_summary

OUT = Path(__file__).resolve().parent / "output"


def _cand_job(args):
    symbol, start, sig_cfg, refresh, point, *rest = args
    end = rest[0] if rest else None
    sentinel_missing = bool(rest[1]) if len(rest) > 1 else False
    bars = prepare_bars(symbol, start=start, end=end, sentinel_missing=sentinel_missing)
    if sentinel_missing:           # the candidate cache key does not see the spread treatment, so do not read or write it
        return build_candidates(symbol, bars, sig_cfg, point)
    return build_candidates_cached(symbol, bars, sig_cfg, point, refresh=refresh)


def get_candidates(symbols, specs, sig_cfg, start, refresh, workers, end=None, sentinel_missing=False):
    jobs = [(s, start, sig_cfg, refresh, specs[s]["point"], end, sentinel_missing) for s in symbols]
    if workers > 1:
        with ProcessPoolExecutor(max_workers=workers) as ex:
            frames = list(ex.map(_cand_job, jobs))
    else:
        frames = [_cand_job(j) for j in jobs]
    frames = [f for f in frames if len(f)]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def simulate(symbols, specs, cands, sim_cfg, start, end=None, sentinel_missing=False):
    m5 = {s: prepare_bars(s, start=start, end=end, sentinel_missing=sentinel_missing)["M5"] for s in symbols}
    return Simulator(symbols, m5, specs, cands, sim_cfg).run()


def _clean(o):
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, (np.floating, float)):
        return None if (o != o) else float(o)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (pd.Timestamp,)):
        return str(o)
    return o


def analyse(res, sim_cfg, oos_start=None, do_mc=True, n_paths=20_000):
    trades, equity = res["trades"], res["equity"]
    out = {"funnel": res["funnel"], "full": summarize(trades, equity, sim_cfg.initial_balance)}
    if len(trades) and oos_start:
        cut = pd.Timestamp(oos_start, tz="UTC")
        is_t, oos_t = trades[trades["open_T"] < cut], trades[trades["open_T"] >= cut]
        out["in_sample"] = summarize(is_t, equity[equity["T"] < cut], sim_cfg.initial_balance)
        out["out_of_sample"] = summarize(oos_t, equity[equity["T"] >= cut],
                                         float(equity.loc[equity["T"] >= cut, "balance"].iloc[0]))
    if do_mc and len(trades) >= 20:
        t = trades[trades["exit_reason"] != "END"]
        days = day_table(equity, trades)
        out["edge"] = edge_summary(trades)
        out["mc_iid"] = trade_mc(t["ret_pct"].to_numpy(), n_paths=n_paths, mode="iid")
        out["mc_shuffle"] = trade_mc(t["ret_pct"].to_numpy(), n_paths=n_paths, mode="shuffle")
        out["stress"] = stress_tests(trades)
        out["ftmo_1step_like_10pct"] = ftmo_mc(days, FtmoRules(profit_target_pct=10.0), n_paths=n_paths)
        out["ftmo_phase2_5pct"] = ftmo_mc(days, FtmoRules(profit_target_pct=5.0), n_paths=n_paths)
    return out


def fmt_report(tag, coverage, sim_cfg, sig_cfg, out, br) -> str:
    L = []
    f = out["full"]
    L.append(f"# Backtest + Monte Carlo report — `{tag}`\n")
    L.append("**Replays the live bot's logic on historical MT5 bars. Treat results as an optimistic upper bound** "
             "(bar data cannot reproduce real fills; no news filter, swaps or LLM).\n")
    L.append("## Setup\n```")
    L.append(f"signals : {asdict(sig_cfg)}")
    L.append(f"sim     : {asdict(sim_cfg)}")
    L.append(coverage + "```\n")
    L.append("## Headline (full period)\n```")
    for k in ("start", "end", "trades", "trades_per_month", "win_rate_pct", "tp_rate_pct", "exit_mix_pct",
              "expectancy_r", "expectancy_r_ci95", "median_r", "std_r", "profit_factor", "avg_win_r", "avg_loss_r",
              "net_pnl", "return_pct", "max_dd_equity_pct", "sharpe_daily_ann", "sortino_daily_ann",
              "longest_losing_streak", "avg_hold_min", "commission_in_r", "open_at_end"):
        if k in f:
            L.append(f"{k:24s}: {f[k]}")
    L.append("```\n")
    for key, title in (("in_sample", "In-sample"), ("out_of_sample", "Out-of-sample (held back)")):
        if key in out:
            s = out[key]
            L.append(f"**{title}:** trades={s.get('trades')} win%={s.get('win_rate_pct', float('nan')):.1f} "
                     f"expR={s.get('expectancy_r', float('nan')):.3f} PF={s.get('profit_factor', float('nan')):.2f} "
                     f"maxDD={s.get('max_dd_equity_pct', float('nan')):.1f}%\n")
    L.append("## Order funnel (why candidate signals did or did not become trades)\n```")
    for k, v in sorted(out["funnel"].items(), key=lambda kv: -kv[1]):
        L.append(f"{k:28s}: {v}")
    L.append("```\n")
    for name, tab in br.items():
        L.append(f"### {name}\n```\n{tab.to_string()}\n```\n")
    if "mc_iid" in out:
        L.append("## Monte Carlo\n```json")
        for k in ("edge", "mc_iid", "mc_shuffle", "stress", "ftmo_1step_like_10pct", "ftmo_phase2_5pct"):
            L.append(f"// {k}\n" + json.dumps(_clean(out[k]), indent=1))
        L.append("```\n")
    return "\n".join(L)


def variants(base_sig: SignalConfig, base_sim: SimConfig) -> dict:
    return {
        "base_faithful":     (base_sig, base_sim),
        "fixed_sweep":       (replace(base_sig, sweep_mode="fixed"), base_sim),
        "no_breakeven":      (base_sig, replace(base_sim, breakeven=False)),
        "rr_2":              (base_sig, replace(base_sim, rr_ratio=2.0)),
        "no_regime_filter":  (base_sig, base_sim) if False else (replace(base_sig, use_regime=False), base_sim),
        "no_monthly_cap":    (base_sig, replace(base_sim, monthly_target_pct=None)),
        "fresh_snapshot":    (base_sig, replace(base_sim, stale_snapshot=False)),
        "spread_x1.5":       (base_sig, replace(base_sim, spread_mult=1.5)),
        "spread_x2":         (base_sig, replace(base_sim, spread_mult=2.0)),
        "neutral_intrabar":  (base_sig, replace(base_sim, intrabar="neutral")),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="base")
    ap.add_argument("--start", default=None, help="trim M5 start (UTC date)")
    ap.add_argument("--end", default=None, help="drop every bar at or after this UTC date, on all timeframes (DEV-only runs)")
    ap.add_argument("--oos-start", default=None, help="date from which trades are reported as out-of-sample")
    ap.add_argument("--sentinel-missing", action="store_true",
                    help="treat the broker's 50-point placeholder spread (2020-05..2021-09) as missing and impute it; "
                         "candidates are then built uncached")
    ap.add_argument("--variants", action="store_true")
    ap.add_argument("--no-mc", action="store_true")
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--paths", type=int, default=20_000)
    args = ap.parse_args()

    from config import PAIRS
    symbols = list(PAIRS)
    specs = load_specs()
    base_sig, base_sim = SignalConfig(), SimConfig()
    jobs = variants(base_sig, base_sim) if args.variants else {"base_faithful": (base_sig, base_sim)}

    # default OOS split: last 30% of the M5 span
    m5 = prepare_bars(symbols[0], start=args.start, end=args.end)["M5"]
    first, last = m5["time"].iloc[0], m5["time"].iloc[-1]
    oos = args.oos_start or str((first + (last - first) * 0.7).date())
    cov = f"M5 span {first.date()} -> {last.date()} | OOS from {oos} | symbols {symbols}\n"

    cand_cache: dict = {}
    summary_rows = []
    outdir = OUT / args.tag
    outdir.mkdir(parents=True, exist_ok=True)

    for name, (sig_cfg, sim_cfg) in jobs.items():
        t0 = time.time()
        key = sig_cfg.signature()
        if key not in cand_cache:
            cand_cache[key] = get_candidates(symbols, specs, sig_cfg, args.start, args.refresh, args.workers,
                                             end=args.end, sentinel_missing=args.sentinel_missing)
        cands = cand_cache[key]
        res = simulate(symbols, specs, cands, sim_cfg, args.start, end=args.end,
                       sentinel_missing=args.sentinel_missing)
        is_base = name == "base_faithful"
        out = analyse(res, sim_cfg, oos_start=oos, do_mc=(is_base and not args.no_mc), n_paths=args.paths)
        d = outdir / name
        d.mkdir(exist_ok=True)
        res["trades"].to_csv(d / "trades.csv", index=False)
        res["equity"].to_parquet(d / "equity.parquet", index=False)
        res["orders"].to_csv(d / "orders.csv", index=False)
        (d / "summary.json").write_text(json.dumps(_clean(out), indent=1))
        br = breakdowns(res["trades"]) if is_base else {}
        (d / "report.md").write_text(fmt_report(f"{args.tag}/{name}", cov, sim_cfg, sig_cfg, out, br), encoding="utf-8")
        f = out["full"]
        oo = out.get("out_of_sample", {})
        summary_rows.append({
            "variant": name, "candidates": len(cands), "trades": f.get("trades"),
            "win%": round(f.get("win_rate_pct", np.nan), 1), "expR": round(f.get("expectancy_r", np.nan), 3),
            "expR_ci_lo": round(f.get("expectancy_r_ci95", {}).get("lo", np.nan), 3),
            "expR_ci_hi": round(f.get("expectancy_r_ci95", {}).get("hi", np.nan), 3),
            "PF": round(f.get("profit_factor", np.nan), 2), "ret%": round(f.get("return_pct", np.nan), 1),
            "maxDD%": round(f.get("max_dd_equity_pct", np.nan), 1),
            "OOS_n": oo.get("trades"), "OOS_expR": round(oo.get("expectancy_r", np.nan), 3) if oo else None,
        })
        print(f"[{name}] done in {time.time() - t0:.0f}s -> {d}")

    tab = pd.DataFrame(summary_rows)
    pd.set_option("display.width", 220, "display.max_columns", 30)
    print("\n" + tab.to_string(index=False))
    tab.to_csv(outdir / "variants_summary.csv", index=False)


if __name__ == "__main__":
    main()
