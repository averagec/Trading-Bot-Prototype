"""Scratch viewer for result JSONs (prints the survive-spread table and learnability reports)."""
import json
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "output" / "ai"
which = sys.argv[1]
if which == "surv":
    s = json.loads((OUT / "predictability" / "summary.json").read_text())
    for r in s["survive_spread"]:
        g, n = r["gross_pips"], r["net_pips"]
        gr, nr = r["gross_r_1.5atr"], r["net_r_1.5atr"]
        print(f"{r['model']:6s} h={r['h']:2d} n={r['n_selected']:6d} gross {g['est']:6.3f} [{g['lo']:6.3f},{g['hi']:6.3f}] pips  "
              f"cost {r['mean_cost_pips']:.2f}  net {n['est']:6.3f} [{n['lo']:6.3f},{n['hi']:6.3f}]  "
              f"grossR {gr['est']:.4f} netR {nr['est']:.4f} [{nr['lo']:.4f},{nr['hi']:.4f}]  |move|/cost {r['mean_abs_move_over_cost']:.1f}")
    print(s["opportunity"])
elif which == "report":
    p = OUT / "learn" / "report.json"
    print(p.read_text())
