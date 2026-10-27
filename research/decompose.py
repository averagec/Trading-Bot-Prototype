"""
Where does the loss come from? Re-run the random-direction null with cost/rule components switched off one by one.
Because direction is random, the average R of each configuration is what the *framework* (rules + costs) gives a
signal with no skill.   python -m research.decompose --seeds 20
"""
import argparse, json, sys
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import numpy as np, pandas as pd
from research.data import load_specs
from research.nulltest import _run_seeds
from research.sim import SimConfig
from research.run import OUT, _clean


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--seeds", type=int, default=20); ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args()
    from config import PAIRS
    symbols, specs = list(PAIRS), load_specs()
    cand_path = OUT / "null" / "base_candidates.parquet"
    base = SimConfig()
    steps = {
        "1 base (all frictions)":                     base,
        "2 - commission":                             replace(base, commission_per_lot_side=0.0),
        "3 - commission - spread":                    replace(base, commission_per_lot_side=0.0, spread_mult=0.0, use_spread_filter=False),
        "4 - ... - pessimistic intrabar (neutral)":   replace(base, commission_per_lot_side=0.0, spread_mult=0.0, use_spread_filter=False, intrabar="neutral"),
        "5 - ... - breakeven rule":                   replace(base, commission_per_lot_side=0.0, spread_mult=0.0, use_spread_filter=False, intrabar="neutral", breakeven=False),
    }
    seeds = list(range(a.seeds))
    out = {}
    for name, cfg in steps.items():
        chunks = [seeds[i::a.workers] for i in range(a.workers)]
        jobs = [(ch, cand_path, symbols, specs, None, cfg) for ch in chunks if ch]
        with ProcessPoolExecutor(max_workers=a.workers) as ex:
            rows = [r for part in ex.map(_run_seeds, jobs) for r in part]
        df = pd.DataFrame(rows)
        out[name] = {"mean_expectancy_r": round(df.expectancy_r.mean(), 4), "sd": round(df.expectancy_r.std(), 4),
                     "mean_win_rate": round(df.win_rate_pct.mean(), 1), "mean_trades": round(df.trades.mean(), 0)}
        print(f"{name:46s} expR {out[name]['mean_expectancy_r']:+.3f} (sd {out[name]['sd']:.3f})  win% {out[name]['mean_win_rate']}  trades {out[name]['mean_trades']:.0f}", flush=True)
    (OUT / "null" / "decomposition.json").write_text(json.dumps(_clean(out), indent=1))

if __name__ == "__main__":
    main()
