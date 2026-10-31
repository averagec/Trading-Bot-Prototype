"""
Stage-0 verdict (research/reports/stage0_prereg.md section 5): combine the two kill-set runs into KILL / PROCEED /
INCONCLUSIVE using the numbers `research.nulltest` stored for each run.

    python -m research.stage0                       # reads output/stage0_R2 and output/stage0_R4
    python -m research.stage0 --tags A B
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research.nulltest import KILL_UB_R
from research.run import OUT

VARIANTS = ("all", "no_gold", "clean", "clean_no_gold")
REQUIRED_RUNS = 2        # the kill set is R2 and R4 together; one run alone can never kill
NAN = float("nan")


def _num(x) -> float:
    """A stored estimate as a float; None (what `_clean` writes for NaN), a missing value or junk becomes NaN."""
    try:
        return float(x)
    except (TypeError, ValueError):
        return NAN


def _get(run: dict, variant: str, key: str) -> float:
    block = run.get(variant) if isinstance(run, dict) else None
    return _num(block.get(key)) if isinstance(block, dict) else NAN


def decide(runs: dict) -> dict:
    """runs: {name: nulltest `skill` block}. Each block holds `paired_skill` results for the four variants.

    PROCEED      the Bonferroni lower bound is above 0 for at least one run on the full universe (takes precedence)
    KILL         both kill-set runs are present and every upper bound (all four variants, every run) is below KILL_UB_R
    INCONCLUSIVE anything else, including no runs, fewer than REQUIRED_RUNS, or any missing / null / NaN estimate"""
    proceed = [n for n, e in runs.items() if _get(e, "all", "lb_bonf") > 0]
    kill = (len(runs) >= REQUIRED_RUNS
            and all(_get(e, v, "ub95") < KILL_UB_R for e in runs.values() for v in VARIANTS))
    if proceed:
        verdict, why = "PROCEED", f"lower bound above 0 for {', '.join(proceed)} (selected among {len(runs)} runs)"
    elif kill:
        verdict, why = "KILL", f"every upper bound is below +{KILL_UB_R} R in all {len(VARIANTS)} variants of {len(runs)} runs"
    elif len(runs) < REQUIRED_RUNS:
        verdict, why = "INCONCLUSIVE", f"needs both kill-set runs, got {len(runs)}"
    else:
        verdict, why = "INCONCLUSIVE", "no lower bound above 0 and not every upper bound below the kill threshold (or an estimate is missing)"
    table = {n: {v: {k: (_get(e, v, k) if _get(e, v, k) == _get(e, v, k) else None)
                     for k in ("trades", "skill_r", "se_skill", "ub95", "lb_bonf")} for v in VARIANTS}
             for n, e in runs.items()}
    return {"verdict": verdict, "reason": why, "kill_threshold_r": KILL_UB_R, "runs": table}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tags", nargs="+", default=["stage0_R2", "stage0_R4"])
    args = ap.parse_args()
    runs = {}
    for tag in args.tags:
        summary = json.loads((OUT / tag / "null_summary.json").read_text())
        runs[tag] = summary["skill"]
    result = decide(runs)
    (OUT / "stage0_verdict.json").write_text(json.dumps(result, indent=1))
    print(json.dumps(result, indent=1))


if __name__ == "__main__":
    main()
