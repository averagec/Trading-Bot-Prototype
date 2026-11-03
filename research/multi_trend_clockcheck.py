"""Cross-check the broker D1 bars against the H1 cache for the same symbol (server-day clock and DST consistency). Data only, DEV window.

    python -m research.multi_trend_clockcheck AUDUSD
"""
import sys

import numpy as np, pandas as pd
from research import multi_trend_data as mtd
from research.ai import dataset as ds
from research.data import utc_to_server

sym = sys.argv[1]
h1 = ds.load_bars(sym, "H1", end="2021-06-01")
srv = utc_to_server(h1["time"])
h1["sday"] = pd.to_datetime(srv).dt.normalize().to_numpy()
g = h1.groupby("sday")
agg = pd.DataFrame({"o": g["open"].first(), "c": g["close"].last(), "n": g.size()})
d1, cnt = mtd.clean_d1(mtd.load_d1_raw(sym))
d1 = d1.set_index("date")
j = d1.join(agg, how="inner")
j = j[j["n"] >= 12]
print(sym, "common days", len(j), "of D1 bars", len(d1))
for col_d, col_h in (("close", "c"), ("open", "o")):
    rel = (j[col_d] / j[col_h] - 1).abs()
    print(col_d, "median |rel diff| %.2e" % rel.median(), "share > 1bp %.4f" % (rel > 1e-4).mean(), "share > 5bp %.4f" % (rel > 5e-4).mean())
    bad = rel[rel > 5e-4]
    print("  bad by year:", bad.groupby(bad.index.year).size().to_dict())
