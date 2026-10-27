"""
Order Manager
Handles:
- Duplicate trade prevention (one trade per symbol)
- Cooldown tracking per symbol
- Limit/stop order placement based on strategy type
- Pending order expiry
"""

import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

COOLDOWN_CANDLES    = 5
PENDING_EXPIRY_MINS = 30
LIMIT_OFFSET_PIPS   = 2


class OrderManager:

    def __init__(self):
        self.last_trade_time:   dict = {}
        self.last_trade_dir:    dict = {}
        self.pending_orders:    dict = {}

    def can_trade_symbol(self, symbol: str, direction: str,
                         open_positions: list, open_pending: list) -> dict:
        open_symbols = [p["symbol"] for p in open_positions]
        if symbol in open_symbols:
            return {"allowed": False, "reason": f"{symbol} already has an open position"}

        pending_symbols = [p.get("symbol") for p in open_pending]
        if symbol in pending_symbols:
            return {"allowed": False, "reason": f"{symbol} already has a pending order"}

        if symbol in self.last_trade_time:
            last_time    = self.last_trade_time[symbol]
            last_dir     = self.last_trade_dir.get(symbol)
            cooldown_end = last_time + timedelta(minutes=COOLDOWN_CANDLES * 5)
            now          = datetime.now(timezone.utc)

            if now < cooldown_end and last_dir == direction:
                remaining = int((cooldown_end - now).total_seconds() / 60)
                return {"allowed": False, "reason": f"{symbol} in cooldown - {remaining} mins remaining"}

        return {"allowed": True, "reason": "Clear to trade"}

    def record_trade(self, symbol: str, direction: str):
        self.last_trade_time[symbol] = datetime.now(timezone.utc)
        self.last_trade_dir[symbol]  = direction
        logger.info(f"Trade recorded for {symbol} - cooldown started")

    def get_entry_type(self, confluence: dict) -> str:
        breakdown = confluence.get("breakdown", {})
        direction = confluence.get("direction", "NONE")

        sweep_sig  = breakdown.get("liquidity_sweep", {}).get("signal")
        fvg_sig    = breakdown.get("fvg", {}).get("signal")
        candle_sig = breakdown.get("candle_pattern", {}).get("signal")
        qt_sig     = breakdown.get("quarterly_theory", {}).get("signal")

        if sweep_sig == direction or fvg_sig == direction:
            return "LIMIT"
        if qt_sig == direction:
            return "LIMIT"
        if candle_sig == direction:
            return "MARKET"
        return "LIMIT"

    def get_limit_price(self, symbol: str, direction: str,
                        confluence: dict, current_price: float) -> float:
        breakdown = confluence.get("breakdown", {})
        pip_size  = 0.01 if "JPY" in symbol else 0.0001
        offset    = LIMIT_OFFSET_PIPS * pip_size

        fvg = breakdown.get("fvg", {})
        if fvg.get("signal") == direction and fvg.get("fvg_zone"):
            zone_bottom, zone_top = fvg["fvg_zone"]
            midpoint = (zone_bottom + zone_top) / 2
            return round(midpoint, 5)

        sweep = breakdown.get("liquidity_sweep", {})
        if sweep.get("signal") == direction and sweep.get("level"):
            level = sweep["level"]
            if direction == "BUY":
                return round(level + offset, 5)
            else:
                return round(level - offset, 5)

        if direction == "BUY":
            return round(current_price - (3 * pip_size), 5)
        else:
            return round(current_price + (3 * pip_size), 5)

    def track_pending(self, symbol: str, order_data: dict):
        """Remember an order we just placed (fallback expiry time for an order that carries no broker setup time)."""
        self.pending_orders[symbol] = {
            **order_data,
            "placed_at": datetime.now(timezone.utc),
        }

    def get_expired_pending(self, open_pending: list) -> list:
        """The bot's pending orders older than PENDING_EXPIRY_MINS, as [{"symbol", "ticket"}].

        Age comes from the broker's setup time on each order (`time_setup`, UTC), so an order placed before a
        restart still expires. Our own record is only the fallback when an order has no setup time."""
        now     = datetime.now(timezone.utc)
        expiry  = timedelta(minutes=PENDING_EXPIRY_MINS)
        expired = []

        for order in open_pending:
            placed_at = order.get("time_setup")
            if placed_at is None:
                tracked = self.pending_orders.get(order.get("symbol"), {})
                if tracked.get("ticket") == order.get("ticket"):
                    placed_at = tracked.get("placed_at")
            if placed_at and (now - placed_at) > expiry:
                expired.append({"symbol": order["symbol"], "ticket": order["ticket"]})

        open_tickets = {o.get("ticket") for o in open_pending}
        for symbol, tracked in list(self.pending_orders.items()):
            if tracked.get("ticket") not in open_tickets and (now - tracked["placed_at"]) > expiry:
                del self.pending_orders[symbol]       # long gone at the broker (filled, cancelled): forget it

        return expired

    def clear_pending(self, symbol: str):
        if symbol in self.pending_orders:
            del self.pending_orders[symbol]


order_manager = OrderManager()