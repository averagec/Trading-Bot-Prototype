"""
MT5 Connector
Handles all MetaTrader 5 interactions: connection, OHLCV data, order management.

IMPORTANT: MetaTrader5 Python library only works on Windows.
Run this bot on the same Windows machine where MT5 is installed and logged in.
"""

import sys
import time
import logging
from datetime import datetime, timezone
from typing import Optional

import pandas as pd

try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    MT5_AVAILABLE = False
    print("[WARN] MetaTrader5 library not found. Running in SIMULATION mode.")

from config import MT5_CONFIG, TIMEFRAMES, CANDLE_COUNT

logger = logging.getLogger(__name__)


# ── Timeframe mapping ──────────────────────────────────────────────────────────
TF_MAP = {
    "M1":  1,
    "M5":  5,
    "M15": 15,
    "M30": 30,
    "H1":  16385,
    "H4":  16388,
    "D1":  16408,
    "W1":  32769,
    "MN1": 49153,
} if MT5_AVAILABLE else {}


class MT5Connector:

    def __init__(self):
        self.connected = False

    def connect(self) -> bool:
        """Initialise and log into MT5."""
        if not MT5_AVAILABLE:
            logger.warning("MT5 not available — simulation mode active.")
            return False

        if not mt5.initialize(
            login=MT5_CONFIG["login"],
            password=MT5_CONFIG["password"],
            server=MT5_CONFIG["server"],
            timeout=MT5_CONFIG["timeout"],
        ):
            logger.error(f"MT5 init failed: {mt5.last_error()}")
            return False

        info = mt5.account_info()
        if info is None:
            logger.error(f"Account login failed: {mt5.last_error()}")
            return False

        self.connected = True
        logger.info(f"Connected to MT5 | Account: {info.login} | Balance: {info.balance} {info.currency}")
        return True

    def disconnect(self):
        if MT5_AVAILABLE and self.connected:
            mt5.shutdown()
            self.connected = False
            logger.info("MT5 disconnected.")

    # ── Market Data ────────────────────────────────────────────────────────────

    def get_ohlcv(self, symbol: str, timeframe: str, count: int = CANDLE_COUNT) -> Optional[pd.DataFrame]:
        """
        Fetch OHLCV candles for a symbol/timeframe.
        Returns a DataFrame with columns: time, open, high, low, close, tick_volume
        """
        if not MT5_AVAILABLE or not self.connected:
            return self._simulated_ohlcv(symbol, timeframe, count)

        tf = TF_MAP.get(timeframe)
        if tf is None:
            logger.error(f"Unknown timeframe: {timeframe}")
            return None

        rates = mt5.copy_rates_from_pos(symbol, tf, 0, count)
        if rates is None:
            logger.error(f"Failed to get rates for {symbol} {timeframe}: {mt5.last_error()}")
            return None

        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
        df.rename(columns={"tick_volume": "volume"}, inplace=True)
        df = df[["time", "open", "high", "low", "close", "volume"]]
        return df.reset_index(drop=True)

    def get_tick(self, symbol: str) -> Optional[dict]:
        """Get latest bid/ask tick for a symbol."""
        if not MT5_AVAILABLE or not self.connected:
            return {"bid": 1.1000, "ask": 1.1002, "time": datetime.now(timezone.utc)}

        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            return None
        return {"bid": tick.bid, "ask": tick.ask, "time": datetime.fromtimestamp(tick.time, tz=timezone.utc)}

    def get_account_info(self) -> Optional[dict]:
        """Return key account metrics."""
        if not MT5_AVAILABLE or not self.connected:
            return {"balance": 10000.0, "equity": 10000.0, "margin_free": 10000.0, "currency": "USD"}

        info = mt5.account_info()
        if info is None:
            return None
        return {
            "balance":     info.balance,
            "equity":      info.equity,
            "margin_free": info.margin_free,
            "currency":    info.currency,
            "profit":      info.profit,
        }

    def get_open_positions(self) -> list:
        """Return all currently open positions."""
        if not MT5_AVAILABLE or not self.connected:
            return []

        positions = mt5.positions_get()
        if positions is None:
            return []

        result = []
        for p in positions:
            result.append({
                "ticket":    p.ticket,
                "symbol":    p.symbol,
                "type":      "BUY" if p.type == 0 else "SELL",
                "volume":    p.volume,
                "open_price": p.price_open,
                "sl":        p.sl,
                "tp":        p.tp,
                "profit":    p.profit,
                "open_time": datetime.fromtimestamp(p.time, tz=timezone.utc),
            })
        return result

    # ── Order Execution ────────────────────────────────────────────────────────

    def place_order(self, symbol: str, direction: str, volume: float,
                    sl: float, tp: float, comment: str = "forex_bot") -> Optional[dict]:
        """
        Place a market order.
        direction: 'BUY' or 'SELL'
        Returns order result dict or None on failure.
        """
        if not MT5_AVAILABLE or not self.connected:
            logger.info(f"[SIM] Would place {direction} {volume} lots on {symbol} | SL:{sl} TP:{tp}")
            return {"retcode": 0, "order": 999999, "simulated": True}

        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            logger.error(f"Cannot get tick for {symbol}")
            return None

        price     = tick.ask if direction == "BUY" else tick.bid
        order_type = mt5.ORDER_TYPE_BUY if direction == "BUY" else mt5.ORDER_TYPE_SELL

        request = {
            "action":      mt5.TRADE_ACTION_DEAL,
            "symbol":      symbol,
            "volume":      volume,
            "type":        order_type,
            "price":       price,
            "sl":          sl,
            "tp":          tp,
            "deviation":   20,
            "magic":       20250101,
            "comment":     comment,
            "type_time":   mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        result = mt5.order_send(request)
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            logger.error(f"Order failed: {result.retcode} — {result.comment}")
            return None

        logger.info(f"Order placed: {direction} {volume} {symbol} @ {result.price} | Ticket: {result.order}")
        return {
            "retcode": result.retcode,
            "order":   result.order,
            "price":   result.price,
            "volume":  result.volume,
        }
    def place_pending_order(self, symbol: str, direction: str, order_type: str,
                            price: float, volume: float, sl: float, tp: float,
                            comment: str = "forex_bot") -> Optional[dict]:
        if not MT5_AVAILABLE or not self.connected:
            logger.info(f"[SIM] Would place {direction} {order_type} {volume} lots on {symbol} @ {price}")
            return {"retcode": 0, "order": 999998, "simulated": True}

        if direction == "BUY":
            mt5_type = mt5.ORDER_TYPE_BUY_LIMIT  if order_type == "LIMIT" else mt5.ORDER_TYPE_BUY_STOP
        else:
            mt5_type = mt5.ORDER_TYPE_SELL_LIMIT if order_type == "LIMIT" else mt5.ORDER_TYPE_SELL_STOP

        request = {
            "action":       mt5.TRADE_ACTION_PENDING,
            "symbol":       symbol,
            "volume":       volume,
            "type":         mt5_type,
            "price":        price,
            "sl":           sl,
            "tp":           tp,
            "deviation":    20,
            "magic":        20250101,
            "comment":      comment,
            "type_time":    mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_RETURN,
        }

        result = mt5.order_send(request)
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            logger.error(f"Pending order failed: {result.retcode} - {result.comment}")
            return None

        logger.info(f"Pending {direction} {order_type} placed: {symbol} @ {price} | Ticket: {result.order}")
        return {"retcode": result.retcode, "order": result.order, "price": price}

    def get_pending_orders(self) -> list:
        if not MT5_AVAILABLE or not self.connected:
            return []
        orders = mt5.orders_get()
        if orders is None:
            return []
        result = []
        for o in orders:
            if o.magic != 20250101:
                continue
            result.append({
                "ticket": o.ticket, "symbol": o.symbol,
                "type":   "BUY" if o.type in (0, 2, 4) else "SELL",
                "price":  o.price_open, "sl": o.sl, "tp": o.tp,
                "volume": o.volume_initial,
            })
        return result

    def cancel_pending_order(self, ticket: int) -> bool:
        if not MT5_AVAILABLE or not self.connected:
            logger.info(f"[SIM] Would cancel pending order #{ticket}")
            return True
        request = {"action": mt5.TRADE_ACTION_REMOVE, "order": ticket}
        result  = mt5.order_send(request)
        success = result.retcode == mt5.TRADE_RETCODE_DONE
        if success:
            logger.info(f"Pending order #{ticket} cancelled")
        else:
            logger.error(f"Failed to cancel #{ticket}: {result.retcode}")
        return success

    def close_position(self, ticket: int) -> bool:
        """Close an open position by ticket number."""
        if not MT5_AVAILABLE or not self.connected:
            logger.info(f"[SIM] Would close position #{ticket}")
            return True

        position = mt5.positions_get(ticket=ticket)
        if not position:
            logger.error(f"Position {ticket} not found.")
            return False

        p = position[0]
        direction = mt5.ORDER_TYPE_SELL if p.type == 0 else mt5.ORDER_TYPE_BUY
        tick = mt5.symbol_info_tick(p.symbol)
        price = tick.bid if p.type == 0 else tick.ask

        request = {
            "action":    mt5.TRADE_ACTION_DEAL,
            "symbol":    p.symbol,
            "volume":    p.volume,
            "type":      direction,
            "position":  ticket,
            "price":     price,
            "deviation": 20,
            "magic":     20250101,
            "comment":   "bot_close",
        }

        result = mt5.order_send(request)
        return result.retcode == mt5.TRADE_RETCODE_DONE

    # ── Simulation fallback ────────────────────────────────────────────────────

    def _simulated_ohlcv(self, symbol: str, timeframe: str, count: int) -> pd.DataFrame:
        """Generate synthetic OHLCV for testing without MT5."""
        import numpy as np
        np.random.seed(42)
        dates = pd.date_range(end=datetime.now(timezone.utc), periods=count, freq="15min")
        close = 1.1000 + np.cumsum(np.random.randn(count) * 0.0005)
        high  = close + np.abs(np.random.randn(count) * 0.0003)
        low   = close - np.abs(np.random.randn(count) * 0.0003)
        open_ = close + np.random.randn(count) * 0.0002
        vol   = np.random.randint(100, 1000, count)
        return pd.DataFrame({
            "time": dates, "open": open_, "high": high,
            "low": low, "close": close, "volume": vol
        })


# Singleton instance
connector = MT5Connector()
