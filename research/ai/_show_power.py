"""Scratch viewer: planted-signal power table of the slow-horizon design."""
import json
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "output" / "ai" / "slow"
for n in ("H4_k10", "H4_k15", "D1_k10", "D1_k15"):
    p = OUT / f"power_{n}.json"
    if not p.exists():
        continue
    r = json.loads(p.read_text())
    print(f"{n}: rows {r['rows']} weeks {r['weeks']} reps {r['reps']}")
    for auc, v in r["by_auc"].items():
        print(f"  planted {auc}: realised {v['realised_dir_auc_median']:.3f}  P(dirAUC CI>0.5) {v['p_dir_auc_ci_excludes_half']:.2f}  "
              f"P(C1) {v['p_C1']:.2f} (week only {v['p_C1_week_only']:.2f})  P(S) {v['p_S']:.2f}  P(F) {v['p_F']:.2f}  "
              f"P(C1&S&F) {v['p_C1_S_F']:.2f}  gain {v['top_gain_R_median']:.3f} R  P(gain LB>0) {v['p_top_gain_lo_gt0']:.2f}")
