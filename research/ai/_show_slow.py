"""Scratch viewer: direction-neutral label statistics of the slow-horizon datasets."""
import json
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "output" / "ai" / "slow"
s = json.loads((OUT / "label_summary.json").read_text())
for n, v in s.items():
    mix = {k: round(x, 3) for k, x in v["exit_mix"].items()}
    print(f"{n}: decisions {v['decision_rows']} valid {v['valid_labels']} meanR {v['pooled_mean_net_r']:.3f} sd {v['sd_net_r']:.2f} "
          f"win {v['win_rate_net_r_gt0']:.3f} mix {mix} uniq {v['mean_uniqueness']:.3f} sumw {v['sum_uniqueness_weights']:.0f} "
          f"floor {v['floor_binds_share']:.3f} stop {v['median_stop_pips']:.0f} cost {v['median_cost_r']:.3f}")
    for e, g in v["by_era"].items():
        print(f"    {e}: decisions {g['decisions']} valid {g['valid_labels']} meanR {g['pooled_mean_net_r']:.3f} win {g['win_rate']:.3f} "
              f"cost_r {g['median_cost_r']:.3f} stop {g['median_stop_pips']:.0f}")
