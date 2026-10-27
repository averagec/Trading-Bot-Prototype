"""
Flatten helper shared by the Friday flatten (main.py) and the panic button (panic_close.py).
Cancels the bot's pending orders and closes the bot's open positions (magic == the bot's magic number only).
Positions opened by hand or by another EA are never touched. Every cancel/close is isolated, so one failure
(or exception) never stops the rest.
"""

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class FlattenResult:
    cancelled:      list = field(default_factory=list)   # pending orders (dicts as passed in) cancelled at the broker
    failed_cancels: int  = 0                             # pending orders that could not be cancelled
    still_open:     int  = 0                             # bot positions that could not be closed
    nothing_to_do:  bool = False                         # no bot position and no pending order existed


def bot_positions(positions: list, magic: int) -> list:
    """The positions that belong to the bot. A position without magic information is not ours."""
    return [p for p in positions if p.get("magic") == magic]


def flatten_bot(conn, positions: list, pending: list, magic: int, tag: str) -> FlattenResult:
    """Cancel every order in `pending` (already the bot's own) and close every position in `positions` with
    p["magic"] == magic, through the connector `conn`. `tag` prefixes the log lines."""
    result = FlattenResult()
    mine   = bot_positions(positions, magic)
    others = len(positions) - len(mine)
    if others:
        logger.warning(f"{tag} | leaving {others} non-bot position(s) open (magic != {magic})")
    if not mine and not pending:
        result.nothing_to_do = True
        return result

    logger.warning(f"{tag} | closing {len(mine)} position(s), cancelling {len(pending)} pending order(s)")
    for order in pending:
        try:
            if conn.cancel_pending_order(order["ticket"]):
                result.cancelled.append(order)
            else:
                result.failed_cancels += 1
                logger.error(f"{tag} | could not cancel pending #{order['ticket']} {order['symbol']}")
        except Exception as e:
            result.failed_cancels += 1
            logger.error(f"{tag} | error cancelling pending #{order.get('ticket')}: {e}", exc_info=True)
    for position in mine:
        try:
            if conn.close_position(position["ticket"]):
                logger.warning(f"{tag} | closed #{position['ticket']} {position['symbol']}")
            else:
                result.still_open += 1
                logger.error(f"{tag} | could not close #{position['ticket']} {position['symbol']}")
        except Exception as e:
            result.still_open += 1
            logger.error(f"{tag} | error closing #{position.get('ticket')}: {e}", exc_info=True)
    return result
