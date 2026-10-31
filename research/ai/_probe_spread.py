"""Scratch probe (descriptive): monthly share of M5 bars with recorded spread exactly 50 points, 2019-2021 (DEV only)."""
import pandas as pd

from research.ai import dataset as ds

pd.set_option("display.width", 220, "display.max_columns", 40, "display.max_rows", 100)
cols = {}
for sym in ds.FX5:
    df = ds.load_bars(sym, "M5", start="2019-01-01")
    m = df["time"].dt.strftime("%Y-%m")
    cols[sym] = (df["spread"] == 50).groupby(m).mean().mul(100).round(1)
out = pd.DataFrame(cols)
print(out.to_string())
