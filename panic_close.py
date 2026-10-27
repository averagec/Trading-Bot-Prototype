"""
Panic button: cancel the bot's pending orders and close the bot's open positions, then exit.

    python panic_close.py

Only orders and positions with the bot's magic number (config.BOT_MAGIC) are touched; positions opened by hand
or by another EA are left alone. It connects through the same connector as the bot, so it attaches to the
account the MT5 terminal is logged into and refuses any non-demo account unless FOREX_ALLOW_LIVE=YES is set.
Stop the bot separately (Ctrl+C, or create a file named STOP in this folder); this script does not.
Exit code: 0 everything flat, 1 something could not be closed or MT5 is unavailable, 2 account refused.
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import BOT_MAGIC
from mt5_connector import connector
from utils.flatten import flatten_bot

logger = logging.getLogger("panic_close")


def panic_close() -> int:
    """Flatten the bot's orders and positions; returns the process exit code (see the module docstring)."""
    if not connector.connect():
        if connector.refused:
            logger.critical("PANIC CLOSE | account refused (not a demo account) - nothing was touched")
            return 2
        logger.error("PANIC CLOSE | could not connect to MT5 - nothing was closed")
        return 1
    try:
        positions = connector.get_open_positions()
        pending   = connector.get_pending_orders()
        result    = flatten_bot(connector, positions, pending, BOT_MAGIC, "PANIC CLOSE")
    finally:
        connector.disconnect()

    if result.nothing_to_do:
        logger.info("PANIC CLOSE | nothing of the bot's is open")
    logger.warning(f"PANIC CLOSE | cancelled {len(result.cancelled)} pending order(s); "
                   f"{result.failed_cancels} not cancelled, {result.still_open} position(s) not closed")
    return 1 if (result.still_open or result.failed_cancels) else 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s - %(message)s")
    sys.exit(panic_close())
