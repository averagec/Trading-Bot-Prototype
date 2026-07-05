"""
Run Backtest - Entry Point
Run this separately from the live bot:
    python backtest/run_backtest.py
"""

import sys
import logging
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))

from backtest.backtest_engine import BacktestEngine
from backtest.optimiser import Optimiser
from backtest.report_generator import ReportGenerator
from config import PAIRS

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("backtest.log", encoding="utf-8"),
    ]
)
logger = logging.getLogger("backtest")


def main():
    logger.info("=" * 60)
    logger.info("  BACKTEST STARTING")
    logger.info(f"  Pairs: {', '.join(PAIRS)}")
    logger.info(f"  Lookback: 12 months")
    logger.info("=" * 60)

    start = datetime.now()

    # 1. Run backtest
    engine  = BacktestEngine(pairs=PAIRS, timeframes={})
    results = engine.run()

    if not results["all_results"]:
        logger.error("No results - check MT5 connection")
        return

    logger.info(f"Complete in {(datetime.now() - start).seconds}s")

    # 2. Optimise
    optimiser = Optimiser(results)
    summary   = optimiser.generate_summary()

    logger.info("\n" + "=" * 60)
    logger.info("RESULTS")
    logger.info("=" * 60)
    logger.info(summary["recommendation"])

    # 3. Report
    ReportGenerator("backtest_report.xlsx").generate(results, summary)

    # 4. Apply to config?
    logger.info(f"\nOptimal threshold: {summary['optimal_threshold']}%")
    apply = input("Apply to config.py? (y/n): ").strip().lower()
    if apply == "y":
        optimiser.apply_to_config("config.py", dry_run=False)
        logger.info("Config updated!")
    else:
        logger.info("Config unchanged. See backtest_report.xlsx for full results.")


if __name__ == "__main__":
    main()