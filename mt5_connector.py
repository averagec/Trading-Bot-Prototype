"""
MT5 Connector
Handles all MetaTrader 5 interactions: connection, OHLCV data, order management.

IMPORTANT: MetaTrader5 Python library only works on Windows.
Run this bot on the same Windows machine where MT5 is installed and logged in.
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

import pandas as pd

try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    MT5_AVAILABLE = False
    print("[WARN] MetaTrader5 library not found. Running in SIMULATION mode.")

from config import MT5_CONFIG, CANDLE_COUNT, ALLOW_LIVE_ACCOUNT, BOT_MAGIC

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
        self.refused = False          # True when connect() refused a non-demo account (the bot must stop, not simulate)
        self.server_utc_offset_hours = 0

    def detect_server_utc_offset(self) -> int:
        """
        Detect the broker server's offset from actual UTC.
        """
        if not MT5_AVAILABLE or not self.connected:
            return 0

        tick = mt5.symbol_info_tick("EURUSD")

        if tick is None:
            logger.warning(
                "Could not detect MT5 server UTC offset"
            )
            return 0

        system_timestamp = datetime.now(
            timezone.utc
        ).timestamp()

        difference_seconds = (
            tick.time - system_timestamp
        )

        offset_hours = round(
            difference_seconds / 3600
        )

        if abs(offset_hours) > 14:
            logger.error(
                f"Unreasonable MT5 server offset: "
                f"{offset_hours} hours"
            )
            return 0

        self.server_utc_offset_hours = offset_hours

        logger.info(
            f"MT5 server time offset detected: "
            f"UTC{offset_hours:+d}"
        )

        return offset_hours

    @staticmethod
    def _check_account(info) -> tuple:
        """(allowed, description) for an mt5 account_info: only demo accounts unless FOREX_ALLOW_LIVE=YES."""
        mode = {mt5.ACCOUNT_TRADE_MODE_DEMO: "DEMO", mt5.ACCOUNT_TRADE_MODE_CONTEST: "CONTEST",
                mt5.ACCOUNT_TRADE_MODE_REAL: "REAL"}.get(info.trade_mode, f"UNKNOWN({info.trade_mode})")
        who = f"Account: {info.login} | Server: {info.server} | Type: {mode}"
        return (info.trade_mode == mt5.ACCOUNT_TRADE_MODE_DEMO or ALLOW_LIVE_ACCOUNT), who

    def account_still_allowed(self) -> bool:
        """Re-check the terminal's account type (called every cycle: the terminal may be switched while the bot runs).
        An unreadable account_info is not a verdict, so it does not stop the bot."""
        if not MT5_AVAILABLE or not self.connected:
            return True
        info = mt5.account_info()
        if info is None:
            return True
        allowed, who = self._check_account(info)
        if not allowed:
            logger.error(f"ACCOUNT NO LONGER ALLOWED | {who} | set FOREX_ALLOW_LIVE=YES only if you mean to trade it")
        return allowed

    def connect(self) -> bool:
        """Initialise and log into MT5."""
        self.refused = False
        if not MT5_AVAILABLE:
            logger.warning("MT5 not available — simulation mode active.")
            return False

        if MT5_CONFIG["login"] and MT5_CONFIG["password"]:
            args = dict(
                login=MT5_CONFIG["login"],
                password=MT5_CONFIG["password"],
                server=MT5_CONFIG["server"],
                timeout=MT5_CONFIG["timeout"],
            )
        else:
            # No credentials configured: attach to whichever account the terminal is logged into
            args = dict(timeout=MT5_CONFIG["timeout"])
            logger.info("No MT5 login configured - attaching to the terminal's logged-in account.")

        if not mt5.initialize(**args):
            logger.error(f"MT5 init failed: {mt5.last_error()}")
            return False

        info = mt5.account_info()
        if info is None:
            logger.error(f"Account login failed: {mt5.last_error()}")
            return False

        allowed, who = self._check_account(info)
        if not allowed:
            logger.error(f"REFUSING to trade a non-demo account | {who} | "
                         f"set FOREX_ALLOW_LIVE=YES only if you mean to trade it")
            mt5.shutdown()
            self.refused = True
            return False
        if info.trade_mode != mt5.ACCOUNT_TRADE_MODE_DEMO:
            logger.warning(f"TRADING A NON-DEMO ACCOUNT (FOREX_ALLOW_LIVE=YES) | {who}")

        self.connected = True
        self.detect_server_utc_offset()
        logger.info(f"Connected to MT5 | {who} | Balance: {info.balance} {info.currency}")
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

        # Start at position 1 so the currently forming candle is excluded
        rates = mt5.copy_rates_from_pos(symbol, tf, 1, count)
        if rates is None:
            logger.error(f"Failed to get rates for {symbol} {timeframe}: {mt5.last_error()}")
            return None

        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
        df["time"] = df["time"] - pd.to_timedelta(
            self.server_utc_offset_hours,
            unit="h",
        )
        df.rename(columns={"tick_volume": "volume"}, inplace=True)
        df = df[["time", "open", "high", "low", "close", "volume"]]
        return df.reset_index(drop=True)

    def get_tick(self, symbol: str) -> Optional[dict]:
        """Return the latest bid/ask tick and spread information."""

        if not MT5_AVAILABLE or not self.connected:
            logger.error(
                f"{symbol}: Cannot obtain a live tick without MT5"
            )
            return None

        tick = mt5.symbol_info_tick(symbol)
        info = mt5.symbol_info(symbol)

        if tick is None or info is None:
            logger.error(
                f"{symbol}: Could not retrieve tick or symbol information"
            )
            return None

        spread_price = tick.ask - tick.bid
        spread_points = (
            spread_price / info.point
            if info.point > 0
            else 0
        )
        raw_tick_time = datetime.fromtimestamp(
            tick.time_msc / 1000.0,
            tz=timezone.utc,
        )

        normalized_tick_time = raw_tick_time - timedelta(
            hours=self.server_utc_offset_hours
        )

        return {
                "bid": float(tick.bid),
                "ask": float(tick.ask),
                "time": normalized_tick_time,
                "raw_server_time": raw_tick_time,
                "time_msc": int(tick.time_msc),
                "spread_price": float(spread_price),
                "spread_points": float(spread_points),
            }

    def get_account_info(self) -> Optional[dict]:
        """Return key account metrics, or None when the account cannot be read (callers must then not open trades).
        Only the library-missing simulation mode gets a synthetic account; a down session never does."""
        if not MT5_AVAILABLE:
            return {"balance": 10000.0, "equity": 10000.0, "margin_free": 10000.0, "currency": "USD"}
        if not self.connected:
            return None

        info = mt5.account_info()
        if info is None:
            return None
        return {
            "login":       info.login,
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
                "magic":     p.magic,
                "type":      "BUY" if p.type == 0 else "SELL",
                "volume":    p.volume,
                "open_price": p.price_open,
                "sl":        p.sl,
                "tp":        p.tp,
                "profit":    p.profit,
                "open_time": datetime.fromtimestamp(p.time, tz=timezone.utc),
            })
        return result
    
    def get_symbol_info(self, symbol: str) -> Optional[dict]:
            """
            Retrieve the broker's contract and trading specifications
            for a symbol.
            """
            if not MT5_AVAILABLE or not self.connected:
                logger.error(
                    f"{symbol}: Cannot obtain symbol information "
                    f"without an active MT5 connection"
                )
                return None

            # Ensure the symbol is available in Market Watch
            if not mt5.symbol_select(symbol, True):
                logger.error(
                    f"{symbol}: Could not select symbol: {mt5.last_error()}"
                )
                return None

            info = mt5.symbol_info(symbol)

            if info is None:
                logger.error(
                    f"{symbol}: Could not retrieve symbol information: "
                    f"{mt5.last_error()}"
                )
                return None

            return {
                "symbol": symbol,
                "digits": int(info.digits),
                "point": float(info.point),
                "tick_size": float(info.trade_tick_size),
                "tick_value": float(info.trade_tick_value),
                "tick_value_profit": float(info.trade_tick_value_profit),
                "tick_value_loss": float(info.trade_tick_value_loss),
                "contract_size": float(info.trade_contract_size),
                "volume_min": float(info.volume_min),
                "volume_max": float(info.volume_max),
                "volume_step": float(info.volume_step),
                "stops_level": int(info.trade_stops_level),
                "freeze_level": int(info.trade_freeze_level),
                "trade_mode": int(info.trade_mode),
                "currency_base": str(info.currency_base),
                "currency_profit": str(info.currency_profit),
                "currency_margin": str(info.currency_margin),
            }

    def calculate_profit(
            self,
            symbol: str,
            direction: str,
            volume: float,
            entry: float,
            exit_price: float,
        ) -> Optional[float]:
            """
            Ask MT5 to calculate the monetary profit or loss between
            the entry and exit prices.

            The result is returned in the account's deposit currency.
            """
            if not MT5_AVAILABLE or not self.connected:
                logger.error(
                    f"{symbol}: Cannot calculate profit without "
                    f"an active MT5 connection"
                )
                return None

            direction = direction.upper()

            if direction == "BUY":
                order_type = mt5.ORDER_TYPE_BUY
            elif direction == "SELL":
                order_type = mt5.ORDER_TYPE_SELL
            else:
                logger.error(
                    f"{symbol}: Invalid direction for profit calculation: "
                    f"{direction}"
                )
                return None

            if volume <= 0:
                logger.error(
                    f"{symbol}: Invalid volume for profit calculation: "
                    f"{volume}"
                )
                return None

            if entry <= 0 or exit_price <= 0:
                logger.error(
                    f"{symbol}: Invalid prices for profit calculation: "
                    f"entry={entry}, exit={exit_price}"
                )
                return None

            profit = mt5.order_calc_profit(
                order_type,
                symbol,
                volume,
                entry,
                exit_price,
            )

            if profit is None:
                logger.error(
                    f"{symbol}: Profit calculation failed: "
                    f"{mt5.last_error()}"
                )
                return None

            return float(profit)

    def calculate_margin(
            self,
            symbol: str,
            direction: str,
            volume: float,
            price: float,
        ) -> Optional[float]:
            """
            Ask MT5 to calculate the margin required for an order.
            """
            if not MT5_AVAILABLE or not self.connected:
                logger.error(
                    f"{symbol}: Cannot calculate margin without "
                    f"an active MT5 connection"
                )
                return None

            direction = direction.upper()

            if direction == "BUY":
                order_type = mt5.ORDER_TYPE_BUY
            elif direction == "SELL":
                order_type = mt5.ORDER_TYPE_SELL
            else:
                logger.error(
                    f"{symbol}: Invalid direction for margin calculation: "
                    f"{direction}"
                )
                return None

            if volume <= 0:
                logger.error(
                    f"{symbol}: Invalid volume for margin calculation: "
                    f"{volume}"
                )
                return None

            if price <= 0:
                logger.error(
                    f"{symbol}: Invalid price for margin calculation: "
                    f"{price}"
                )
                return None

            margin = mt5.order_calc_margin(
                order_type,
                symbol,
                volume,
                price,
            )

            if margin is None:
                logger.error(
                    f"{symbol}: Margin calculation failed: "
                    f"{mt5.last_error()}"
                )
                return None

            return float(margin)
    # ── Order Execution ────────────────────────────────────────────────────────

    @staticmethod
    def _order_send(request: dict, what: str):
        """mt5.order_send, but a None result (terminal busy or disconnected) is logged and returned as None."""
        result = mt5.order_send(request)
        if result is None:
            logger.error(f"order_send returned None ({what}): {mt5.last_error()}")
        return result

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
            "magic":       BOT_MAGIC,
            "comment":     comment,
            "type_time":   mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        result = self._order_send(request, f"market {direction} {symbol}")
        if result is None:
            return None
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
            "magic":        BOT_MAGIC,
            "comment":      comment,
            "type_time":    mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_RETURN,
        }

        result = self._order_send(request, f"pending {direction} {order_type} {symbol}")
        if result is None:
            return None
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
            if o.magic != BOT_MAGIC:
                continue
            result.append({
                "ticket": o.ticket, "symbol": o.symbol,
                "type":   "BUY" if o.type in (0, 2, 4) else "SELL",
                "price":  o.price_open, "sl": o.sl, "tp": o.tp,
                "volume": o.volume_initial,
                # broker's order setup time in UTC (same server-offset correction as get_tick): lets expiry survive a restart
                "time_setup": datetime.fromtimestamp(o.time_setup, tz=timezone.utc)
                              - timedelta(hours=self.server_utc_offset_hours),
            })
        return result

    def cancel_pending_order(self, ticket: int) -> bool:
        if not MT5_AVAILABLE or not self.connected:
            logger.info(f"[SIM] Would cancel pending order #{ticket}")
            return True
        request = {"action": mt5.TRADE_ACTION_REMOVE, "order": ticket}
        result  = self._order_send(request, f"cancel #{ticket}")
        if result is None:
            return False
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            logger.error(f"Failed to cancel #{ticket}: {result.retcode}")
            return False
        logger.info(f"Pending order #{ticket} cancelled")
        return True

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
        if tick is None:
            logger.error(f"No tick for {p.symbol} - cannot close #{ticket}")
            return False
        price = tick.bid if p.type == 0 else tick.ask

        request = {
            "action":    mt5.TRADE_ACTION_DEAL,
            "symbol":    p.symbol,
            "volume":    p.volume,
            "type":      direction,
            "position":  ticket,
            "price":     price,
            "deviation": 20,
            "magic":     BOT_MAGIC,
            "comment":   "bot_close",
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        result = self._order_send(request, f"close #{ticket}")
        return result is not None and result.retcode == mt5.TRADE_RETCODE_DONE

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
    
    def log_time_diagnostic(self, symbol: str):
        if not MT5_AVAILABLE or not self.connected:
            logger.error(
                "Cannot run time diagnostic without MT5"
            )
            return

        now_utc = datetime.now(timezone.utc)

        raw_tick = mt5.symbol_info_tick(symbol)
        normalized_tick = self.get_tick(symbol)

        logger.info(
            f"TIME TEST | system UTC: {now_utc.isoformat()}"
        )

        if raw_tick is not None:
            raw_tick_time = datetime.fromtimestamp(
                raw_tick.time_msc / 1000.0,
                tz=timezone.utc,
            )

            logger.info(
                f"TIME TEST | raw server time: "
                f"{raw_tick_time.isoformat()}"
            )

        if normalized_tick is not None:
            difference = (
                normalized_tick["time"] - now_utc
            ).total_seconds()

            logger.info(
                f"TIME TEST | normalized UTC: "
                f"{normalized_tick['time'].isoformat()} | "
                f"difference: {difference:.1f}s"
            )

        df = self.get_ohlcv(
            symbol,
            "M5",
            count=2,
        )

        if df is not None and not df.empty:
            for index, row in df.iterrows():
                logger.info(
                    f"TIME TEST | normalized bar[{index}]: "
                    f"{row['time'].isoformat()}"
                )


# Singleton instance
connector = MT5Connector()
