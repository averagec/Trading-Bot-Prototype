"""
Where does the loss come from? Re-run the random-direction null with cost/rule components switched off.
Because direction is random, the average R of each configuration is what the *framework* (rules + costs) gives a
signal with no skill. Reads no skill: the directions are random.

    python -m research.decompose --seeds 20                                    # the old default: the 16-month 'null' candidates
    python -m research.decompose --tag decompose_dev --cands research/output/stage0_R2/base_candidates.parquet \
        --start 2014-01-01 --end 2021-06-01 --seeds 40 --workers 12 --live-settings

Two views of the same frictions: a cumulative path (each step removes one more) and one-at-a-time (each removes only one from the base).
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


def build_steps(base: SimConfig) -> dict:
    """{'cumulative': {name: cfg}, 'one_at_a_time': {name: cfg}} for a base configuration."""
    no_comm = replace(base, commission_per_lot_side=0.0)
    no_spread = replace(base, spread_mult=0.0, use_spread_filter=False)
    no_be = replace(base, breakeven=False)
    cum = {"c0 base (all frictions)": base,
           "c1 - commission": no_comm,
           "c2 - ... - spread": replace(no_comm, spread_mult=0.0, use_spread_filter=False),
           "c3 - ... - breakeven": replace(no_comm, spread_mult=0.0, use_spread_filter=False, breakeven=False)}
    one = {"o1: no commission": no_comm, "o2: no spread": no_spread, "o3: no breakeven": no_be}
    if base.friday_flatten_utc is not None:
        cum["c4 - ... - Friday flatten"] = replace(cum["c3 - ... - breakeven"], friday_flatten_utc=None)
        one["o4: no Friday flatten"] = replace(base, friday_flatten_utc=None)
    return {"cumulative": cum, "one_at_a_time": one}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=20)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--cands", default=None, help="candidate parquet written by a nulltest run (default: output/null)")
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None, help="drop every bar at or after this UTC date on all timeframes")
    ap.add_argument("--live-settings", action="store_true", help="base = the live bot's settings (fixed 0.25 %% risk, Friday flatten 20:00 UTC)")
    ap.add_argument("--tag", default="null", help="output folder under research/output")
    a = ap.parse_args()
    from config import PAIRS
    symbols, specs = list(PAIRS), load_specs()
    cand_path = Path(a.cands) if a.cands else OUT / "null" / "base_candidates.parquet"
    base = SimConfig(fixed_risk_pct=0.25, friday_flatten_utc=20.0) if a.live_settings else SimConfig()
    steps = build_steps(base)
    seeds = list(range(a.seeds))
    out = {"settings": {"base": "live" if a.live_settings else "defaults", "start": a.start, "end": a.end,
                        "seeds": a.seeds, "cands": str(cand_path)}}
    for view, cfgs in steps.items():
        out[view] = {}
        for name, cfg in cfgs.items():
            chunks = [seeds[i::a.workers] for i in range(a.workers)]
            jobs = [(ch, cand_path, symbols, specs, a.start, cfg, a.end) for ch in chunks if ch]
            with ProcessPoolExecutor(max_workers=a.workers) as ex:
                rows = [r for part_rows, _ in ex.map(_run_seeds, jobs) for r in part_rows]
            df = pd.DataFrame(rows)
            out[view][name] = {"mean_expectancy_r": round(df.expectancy_r.mean(), 4), "sd": round(df.expectancy_r.std(), 4),
                               "mean_win_rate": round(df.win_rate_pct.mean(), 1), "mean_trades": round(df.trades.mean(), 0)}
            print(f"{view[:3]} {name:34s} expR {out[view][name]['mean_expectancy_r']:+.3f} (sd {out[view][name]['sd']:.3f})  "
                  f"win% {out[view][name]['mean_win_rate']}  trades {out[view][name]['mean_trades']:.0f}", flush=True)
    d = OUT / a.tag
    d.mkdir(parents=True, exist_ok=True)
    (d / "decomposition.json").write_text(json.dumps(_clean(out), indent=1))

if __name__ == "__main__":
    main()
