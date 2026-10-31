"""Sample-size bookkeeping of the slow-horizon datasets (no model; direction-neutral label counts).

    python -m research.ai.slow_counts
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from research.ai import slow as sl
from research.ai.build import OUT
from research.ai.slow_study import dataset_path, stack_variant


def counts(name: str) -> dict:
    df = pd.read_parquet(dataset_path(name))
    st = stack_variant(df)
    oof = st[st["T"] >= pd.Timestamp(sl.OOF_FIRST, tz="UTC")]
    bars = oof.groupby("rid").size()
    pair = oof.assign(bar=oof["rid"] // 2).groupby("bar")["y"].agg(["size", "sum"])
    both = pair[pair["size"] == 2]
    out = {"decision_rows": int(len(df)), "stacked_rows": int(len(st)), "oof_rows": int(len(oof)),
           "oof_bars_with_both_sides": int(len(both)), "oof_weeks": int(oof["week"].nunique()),
           "oof_base_rate": float(oof["y"].mean()),
           "oof_share_bars_exactly_one_side_wins": float((both["sum"] == 1).mean()),
           "oof_sum_uniqueness": float(oof["w"].sum()),
           "oof_years": sorted(int(y) for y in oof["year"].unique()),
           "per_era_oof_rows": {e: int((oof["era"] == e).sum()) for e in [x[0] for x in sl.ERAS]}}
    n_dir = out["oof_share_bars_exactly_one_side_wins"] * out["oof_bars_with_both_sides"]
    out["discordant_bars"] = int(n_dir)
    # SE of a directional AUC if the discordant bars were independent (a lower bound on the true SE)
    out["se_dir_auc_if_independent"] = float(np.sqrt(1.0 / (3.0 * n_dir))) if n_dir > 0 else float("nan")
    return out


if __name__ == "__main__":
    res = {n: counts(n) for n in sl.VARIANTS}
    (OUT / "slow" / "counts.json").write_text(json.dumps(res, indent=1))
    for n, v in res.items():
        print(n, {k: v[k] for k in ("stacked_rows", "oof_rows", "oof_bars_with_both_sides", "oof_weeks", "oof_base_rate",
                                    "oof_share_bars_exactly_one_side_wins", "discordant_bars", "oof_sum_uniqueness",
                                    "se_dir_auc_if_independent")}, v["per_era_oof_rows"])
