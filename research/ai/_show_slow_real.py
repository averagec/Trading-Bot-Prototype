"""Scratch viewer: real-run results of the slow-horizon study."""
import json
from pathlib import Path

D = Path(__file__).resolve().parents[1] / "output" / "ai" / "slow"


def ci(x, f="{:+.4f}"):
    return (f + " [" + f + ", " + f + "]").format(x["est"], x["lo"], x["hi"])


for n in ("H4_k10", "H4_k15", "D1_k10", "D1_k15"):
    for m in ("LR", "HGB_S_slow", "HGB_M_slow"):
        r = json.loads((D / f"real_{n}_{m}.json").read_text())
        o = r["overall"]
        t = o["top"]
        print(f"== {n} {m}: rows {o['rows']} base {o['base_rate']:.3f}  D1 {ci(o['D1_B1_minus_F'], '{:+.2e}')}  dirAUC {ci(o['dir_auc_ci'], '{:.4f}')}  "
              f"AUC F {o['auc_F']:.4f} B1 {o['auc_B1']:.4f}")
        print(f"   top: share {t['trade_share']:.3f} n {t['n']} gain {ci(t['gain_vs_random_side'])}  netR x1 {ci(t['net_r'])}  x1.5 {ci(t['net_r_cost_x1.5'])}  x2 {ci(t['net_r_cost_x2'])}")
        for e, v in r["eras"].items():
            print(f"   era {e}: rows {v['rows']} D1 {v['D1_B1_minus_F']['est']:+.2e} dirAUC {v['dir_auc']:.4f} [{v['dir_auc_ci']['lo']:.3f},{v['dir_auc_ci']['hi']:.3f}] "
                  f"gain {v['top']['gain_vs_random_side']['est']:+.3f} netR {v['top']['net_r']['est']:+.3f} x1.5 {v['top']['net_r_cost_x1.5']['est']:+.3f} x2 {v['top']['net_r_cost_x2']['est']:+.3f}")
        s = r["stability"]
        h = r["holm_week"]
        h4 = r["holm_4week"]
        print(f"   stability: positive years {s['share_positive_years']:.2f} of {s['years']}, min LOYO D1 {s['min_leave_one_year_out_D1']:+.2e}, min LOYO dirAUC {s['min_leave_one_year_out_dir_auc']:.4f}; "
              f"Holm LB week {h['lower_bound']:+.2e} p {h['p_boot']:.4f}; 4wk LB {h4['lower_bound']:+.2e}; falsifier {r['falsifier']}")
rep = D / "report.json"
if rep.exists():
    print(rep.read_text())
