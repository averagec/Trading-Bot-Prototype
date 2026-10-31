"""
Residual-drag decomposition AFTER the pre-registered composite H6 (Friday flatten 20:30 + no entries >=20:00 + no breakeven).
Random-direction null (same 40 flip seeds as experiments.py), full sample, frictions removed one by one.
Diagnostic only: no actual-signal comparison, not part of the hypothesis-test family.   python -m research.decompose_rc
"""
import json, sys
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import numpy as np, pandas as pd
from research.data import load_specs
from research.experiments import RC, cfg_of, N_SEEDS
from research.nulltest import _run_seeds
from research.run import _clean


def main(workers=14):
    from config import PAIRS
    symbols, specs = list(PAIRS), load_specs()
    cand_path = RC / "candidates.parquet"
    out = {}
    for label, parent in (("H6", "H6_composite_H1H2H3"), ("base", "base")):
        c0 = cfg_of(parent)
        steps = {
            "1 all frictions": c0,
            "2 - commission": replace(c0, commission_per_lot_side=0.0),
            "3 - commission - spread (+filter)": replace(c0, commission_per_lot_side=0.0, spread_mult=0.0, use_spread_filter=False),
            "4 - ... - pessimistic same-bar rule (neutral)": replace(c0, commission_per_lot_side=0.0, spread_mult=0.0, use_spread_filter=False, intrabar="neutral"),
        }
        seeds = list(range(N_SEEDS))
        for name, cfg in steps.items():
            chunks = [seeds[i::workers] for i in range(workers)]
            jobs = [(ch, cand_path, symbols, specs, None, cfg, None) for ch in chunks if ch]
            with ProcessPoolExecutor(max_workers=workers) as ex:
                rows = [r for part_rows, _ in ex.map(_run_seeds, jobs) for r in part_rows]
            df = pd.DataFrame(rows)
            out[f"{label} | {name}"] = {"mean_expectancy_r": float(df.expectancy_r.mean()), "sd": float(df.expectancy_r.std()),
                                        "mean_win_rate": float(df.win_rate_pct.mean()), "mean_trades": float(df.trades.mean())}
            print(f"{label:5s} {name:48s} expR {df.expectancy_r.mean():+.3f} (sd {df.expectancy_r.std():.3f}) win% {df.win_rate_pct.mean():.1f} trades {df.trades.mean():.0f}", flush=True)
    (RC / "decompose_h6.json").write_text(json.dumps(_clean(out), indent=1))


if __name__ == "__main__":
    main()
