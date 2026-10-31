"""Label-resolution check: labels on the H1 path (what the slow study uses, because M5 only exists from 2013) against the
same labels on the M5 path (validated against research/sim.py in test_ai_labels.py), for 2014-2021 decisions.
Label statistics only; no model is fitted.

    python -m research.ai.slow_check
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from research.ai import dataset as ds
from research.ai import slow as sl
from research.ai.build import OUT
from research.ai.labels import label_pair


def compare(symbol: str, spec: sl.SlowSpec, start: str = "2014-06-01") -> dict:
    specs = ds.load_specs()
    ls = spec.label_spec()
    h1 = ds.load_bars(symbol, "H1")
    h4 = ds.load_bars(symbol, "H4")
    m5 = ds.load_bars(symbol, "M5", start=ds.DEV_START)
    base = sl.decision_frame(spec, h1, h4)
    base = base[base["time"] >= pd.Timestamp(start, tz="UTC")].reset_index(drop=True)
    a = label_pair(symbol, base, h1, specs, ls, tf_min=spec.tf_min, bar_min=60)
    b = label_pair(symbol, base, m5, specs, ls, tf_min=spec.tf_min, bar_min=5)
    out = {"symbol": symbol, "variant": spec.name, "decisions": int(len(base))}
    for tag in ("L", "S"):
        both = a[f"valid_{tag}"] & b[f"valid_{tag}"]
        ra, rb = a.loc[both, f"r_{tag}"].to_numpy(), b.loc[both, f"r_{tag}"].to_numpy()
        same = a.loc[both, f"reason_{tag}"].to_numpy() == b.loc[both, f"reason_{tag}"].to_numpy()
        out[tag] = {"n": int(both.sum()), "same_exit_reason": float(same.mean()),
                    "mean_r_h1_path": float(ra.mean()), "mean_r_m5_path": float(rb.mean()),
                    "mean_abs_diff_r": float(np.abs(ra - rb).mean()), "p99_abs_diff_r": float(np.quantile(np.abs(ra - rb), 0.99)),
                    "h1_path_more_pessimistic_share": float((ra < rb - 1e-9).mean()),
                    "h1_path_more_optimistic_share": float((ra > rb + 1e-9).mean())}
    return out


if __name__ == "__main__":
    rows = []
    for sym in ("EURUSD", "USDJPY"):
        for name in ("H4_k10", "D1_k10"):
            rows.append(compare(sym, sl.VARIANTS[name]))
            print(json.dumps(rows[-1]), flush=True)
    (OUT / "slow" / "h1_vs_m5_labels.json").write_text(json.dumps(rows, indent=1))
