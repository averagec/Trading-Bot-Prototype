"""
Null test: does the signal's DIRECTION carry information?

Takes the exact candidate set the live logic produced (same cycle times, same entry prices, same stop distances,
same sizing, same gates, same costs) and flips each trade's direction at random (stop/target mirrored about the entry
price). If the real strategy has directional skill, its result should sit in the upper tail of this null
distribution. If it sits in the lower tail, the signals are systematically wrong-way.

Also reports the deterministic 'mirror everything' result.

    python -m research.nulltest --seeds 30
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from research.data import prepare_bars, load_specs
from research.metrics import summarize
from research.run import get_candidates, OUT, _clean
from research.signals import SignalConfig
from research.sim import Simulator, SimConfig


def mirror(c: pd.DataFrame, flip: np.ndarray) -> pd.DataFrame:
    c = c.copy()
    f = np.asarray(flip, bool)
    c.loc[f, "direction"] = c.loc[f, "direction"].map({"BUY": "SELL", "SELL": "BUY"})
    c.loc[f, "sl_raw"] = 2 * c.loc[f, "price"] - c.loc[f, "sl_raw"]
    cur = np.where(c["direction"] == "BUY", c["ask"], c["bid"])
    sub = np.where(c["direction"] == "BUY",
                   np.where(c["price"] < cur, "LIMIT", "STOP"),
                   np.where(c["price"] > cur, "LIMIT", "STOP"))
    c.loc[f, "subtype"] = sub[f]
    return c


def _run_seeds(args):
    seeds, cand_path, symbols, specs, start, sim_cfg = args
    cands = pd.read_parquet(cand_path)
    m5 = {s: prepare_bars(s, start=start)["M5"] for s in symbols}
    rows = []
    for sd in seeds:
        flip = np.ones(len(cands), bool) if sd is None else (np.random.default_rng(sd).random(len(cands)) < 0.5)
        res = Simulator(symbols, m5, specs, mirror(cands, flip), sim_cfg).run()
        s = summarize(res["trades"], res["equity"], sim_cfg.initial_balance)
        rows.append({"seed": -1 if sd is None else sd, "trades": s.get("trades"),
                     "expectancy_r": s.get("expectancy_r"), "profit_factor": s.get("profit_factor"),
                     "return_pct": s.get("return_pct"), "max_dd_pct": s.get("max_dd_equity_pct"),
                     "win_rate_pct": s.get("win_rate_pct")})
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=30)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--tag", default="null")
    ap.add_argument("--start", default=None)
    args = ap.parse_args()

    from config import PAIRS
    symbols, specs = list(PAIRS), load_specs()
    sig, sim = SignalConfig(), SimConfig()
    cands = get_candidates(symbols, specs, sig, args.start, False, args.workers)
    outdir = OUT / args.tag
    outdir.mkdir(parents=True, exist_ok=True)
    cand_path = outdir / "base_candidates.parquet"
    cands.to_parquet(cand_path, index=False)

    # actual
    t0 = time.time()
    m5 = {s: prepare_bars(s, start=args.start)["M5"] for s in symbols}
    res = Simulator(symbols, m5, specs, cands, sim).run()
    actual = summarize(res["trades"], res["equity"], sim.initial_balance)

    # null + mirror
    seeds = list(range(args.seeds)) + [None]
    chunks = [seeds[i::args.workers] for i in range(args.workers)]
    jobs = [(ch, cand_path, symbols, specs, args.start, sim) for ch in chunks if ch]
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        rows = [r for part in ex.map(_run_seeds, jobs) for r in part]
    df = pd.DataFrame(rows)
    null = df[df["seed"] >= 0]
    mir = df[df["seed"] < 0].iloc[0].to_dict()

    out = {
        "actual": {k: actual.get(k) for k in ("trades", "expectancy_r", "profit_factor", "return_pct",
                                              "max_dd_equity_pct", "win_rate_pct")},
        "mirror_all": mir,
        "null_n": int(len(null)),
        "null_expectancy_r": {"mean": float(null.expectancy_r.mean()), "sd": float(null.expectancy_r.std()),
                              "p05": float(null.expectancy_r.quantile(.05)), "p95": float(null.expectancy_r.quantile(.95))},
        "null_return_pct": {"mean": float(null.return_pct.mean()), "p05": float(null.return_pct.quantile(.05)),
                            "p95": float(null.return_pct.quantile(.95))},
        "p_value_upper (null >= actual)": float((null.expectancy_r >= actual["expectancy_r"]).mean()),
        "p_value_lower (null <= actual)": float((null.expectancy_r <= actual["expectancy_r"]).mean()),
        "z": float((actual["expectancy_r"] - null.expectancy_r.mean()) / null.expectancy_r.std()),
    }
    df.to_csv(outdir / "null_runs.csv", index=False)
    (outdir / "null_summary.json").write_text(json.dumps(_clean(out), indent=1))
    print(json.dumps(_clean(out), indent=1))
    print(f"done in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
