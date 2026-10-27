"""
Pull historical bars from the running MT5 terminal (read-only) and print a coverage report.

    python -m research.fetch_data
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from research.data import fetch_history, coverage_report


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    from config import PAIRS          # only PAIRS is used; credentials are never read here
    fetch_history(PAIRS)
    pd.set_option("display.width", 200, "display.max_columns", 20)
    print(coverage_report(PAIRS).to_string(index=False))


if __name__ == "__main__":
    main()
