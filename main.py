"""
Forex Bot - Main Orchestrator
Runs every 5 mins synced to M5 candle close.
Entry requires full checklist pass - not just a score threshold.
"""

import logging
import os
import time
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from config import (PAIRS, TRIGGER_TF, PRIMARY_TF, CONFIRM_TF, BIAS_TF,
                    RISK, MIN_SCORE_TO_TRADE, EXCEL, BREAKEVEN, FRIDAY_FLATTEN_UTC, BOT_MAGIC)
from mt5_connector import connector
from strategies.smc_strategies import run_confluence
from agents.news_agent import news_agent
from agents.llm_agent import llm_agent, apply_veto
from utils.excel_manager import excel_manager
from utils.risk_manager import risk_manager
from utils.order_manager import order_manager
from utils.pair_observer import pair_observer
from utils.regime_detector import regime_detector
from utils.correlation_filter import correlation_filter
from utils.pre_trade_validator import pre_trade_validator
from utils.flatten import flatten_bot

try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    MT5_AVAILABLE = False

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("forex_bot.log", encoding="utf-8"),
    ]
)
logger = logging.getLogger("orchestrator")

# Kill switch: create a file with this name in the project folder and the bot exits at the start of its next cycle
STOP_FILE = Path(__file__).resolve().parent / "STOP"


class ForexBot:

    def __init__(self):
        self.running      = False
        self.cycle_count  = 0
        self.trades_today = 0
        self.last_processed_candle = {}

    def startup(self):
        logger.info("=" * 60)
        logger.info("  FOREX BOT STARTING UP")
        logger.info("=" * 60)

        if not Path(EXCEL["path"]).exists():
            logger.info("Creating Excel dashboard...")
            excel_manager.create_workbook()
        else:
            logger.info(f"Using existing workbook: {EXCEL['path']}")

        connected = connector.connect()
        if connected:
            logger.info("MT5 connection established.")
        elif connector.refused:
            self._stop_refused("account refused at startup")
        else:
            logger.warning("MT5 not connected - running in simulation mode.")

        account = connector.get_account_info()
        excel_manager.update_dashboard(account or {}, bot_status="STARTING")
        logger.info("Bot initialised. Starting main loop...")

    def run(self):
        self.startup()
        self.running = True
        connector.log_time_diagnostic("XAUUSD")

        while self.running:
            try:                                  # the sleep is inside the try so Ctrl+C there also shuts down cleanly
                self._logged_cycle()
                self._sleep_until_next_candle()
            except KeyboardInterrupt:
                logger.info("Keyboard interrupt - shutting down.")
                self.shutdown()
                break

    def _logged_cycle(self):
        """One cycle with its header; an ordinary exception is logged and the loop carries on."""
        try:
            self.cycle_count += 1
            logger.info(f"\n{'-' * 50}")
            logger.info(f"  CYCLE #{self.cycle_count} | {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
            logger.info(f"{'-' * 50}")
            self._run_cycle()
        except Exception as e:
            logger.error(f"Cycle error: {e}", exc_info=True)

    @staticmethod
    def _sleep_until_next_candle():
        """Sync to the next M5 candle close."""
        now       = datetime.now(timezone.utc)
        seconds   = now.minute * 60 + now.second
        remainder = seconds % 300
        sleep_for = 300 - remainder
        logger.info(f"Sleeping {sleep_for}s until next M5 candle close...")
        time.sleep(sleep_for)

    def _stop_refused(self, why: str):
        """Exit the process because the terminal's account is not one the bot may trade (see the connector's log line).
        Positions on that account are left alone; nothing is closed, cancelled or modified."""
        self._halt(why, "STOPPED - ACCOUNT REFUSED", 2)

    def _halt(self, why: str, status: str, exit_code: int):
        """Exit the process now: CRITICAL log, MT5 disconnect, dashboard `status` with no balance, SystemExit(exit_code).
        Nothing is closed, cancelled, placed or modified."""
        logger.critical(f"BOT STOPPED - {why}. Nothing was closed, cancelled, placed or changed.")
        self.running = False
        try:
            connector.disconnect()
            excel_manager.update_dashboard({}, bot_status=status)
        except Exception as e:      # e.g. the workbook is open in Excel; the exit below must still happen
            logger.error(f"Cleanup before stopping failed: {e}")
        raise SystemExit(exit_code)

    def _run_cycle(self):
        # Kill switch: a STOP file in the project folder ends the bot before anything else happens
        if STOP_FILE.exists():
            self._halt(f"STOP file found ({STOP_FILE}) - delete it before restarting", "STOPPED - STOP FILE", 0)

        # The terminal's account can be switched while the bot runs, so re-check it before anything else
        if not connector.account_still_allowed():
            self._stop_refused("account is no longer allowed")

        account   = connector.get_account_info()
        positions = connector.get_open_positions()
        pending   = connector.get_pending_orders()

        # Weekend gap protection - runs before every other gate so a blocked or
        # maxed-out account still gets flat. Retried each cycle until nothing is left.
        if self._friday_flatten_due():
            self._friday_flatten(positions, pending)
            return

        if not account:
            # Without an account the risk gate cannot run, so fail closed: no new entries (open positions still managed)
            logger.warning("Account info unavailable - no new entries this cycle.")
            if positions:
                self._manage_breakeven(positions)
            return

        logger.info(
            f"Account | Balance: {account['balance']} | "
            f"Equity: {account['equity']} | "
            f"Open: {len(positions)} | Pending: {len(pending)}"
        )
        excel_manager.update_dashboard(account, bot_status="RUNNING")

        trade_check = risk_manager.can_trade(account)
        if not trade_check["allowed"]:
            status = "MONTHLY TARGET HIT" if trade_check["type"] == "monthly_target" else "DAILY LIMIT HIT"
            logger.warning(f"Trading blocked - {trade_check['reason']}")
            excel_manager.update_dashboard(account, bot_status=status)
            if positions:
                self._manage_breakeven(positions)
            return

        logger.info(f"Risk status - {trade_check['reason']}")

        if len(positions) >= RISK["max_open_trades"]:
            logger.info(f"Max positions ({RISK['max_open_trades']}) reached - managing existing only.")
            if positions:
                self._manage_breakeven(positions)
            return

        # Cancel expired pending orders
        expired = order_manager.get_expired_pending(pending)
        for exp in expired:
            connector.cancel_pending_order(exp["ticket"])
            order_manager.clear_pending(exp["symbol"])

        if positions:
            self._manage_breakeven(positions)

        upcoming_news = news_agent.get_upcoming_events(hours_ahead=4)
        if upcoming_news:
            excel_manager.write_news(upcoming_news)

        for symbol in PAIRS:
            try:
                self._analyse_pair(symbol, account, positions, pending)
            except Exception as e:
                logger.error(f"Error analysing {symbol}: {e}", exc_info=True)

    @staticmethod
    def _friday_flatten_due(now: datetime = None) -> bool:
        """True from FRIDAY_FLATTEN_UTC on Friday through Sunday 23:59 (UTC), so a close that failed is retried."""
        if FRIDAY_FLATTEN_UTC is None:
            return False
        now = now or datetime.now(timezone.utc)
        if now.weekday() in (5, 6):
            return True
        return now.weekday() == 4 and now.hour + now.minute / 60.0 >= FRIDAY_FLATTEN_UTC

    def _friday_flatten(self, positions: list, pending: list):
        """Cancel the bot's pending orders and close the bot's open positions (magic == BOT_MAGIC); see utils/flatten.py.
        Whatever could not be closed is retried on the next cycle."""
        result = flatten_bot(connector, positions, pending, BOT_MAGIC, "FRIDAY FLATTEN")
        if result.nothing_to_do:
            logger.info("Friday flatten window - nothing of the bot's open, no new entries until Monday.")
            return
        for order in result.cancelled:
            order_manager.clear_pending(order["symbol"])
        if result.still_open:
            logger.error(f"FRIDAY FLATTEN | {result.still_open} bot position(s) still open after this attempt - retrying next cycle")

    def _analyse_pair(self, symbol: str, account: dict, positions: list, pending: list):
        logger.info(f"\nAnalysing {symbol}...")

        # Session filter - London 07-13 UTC, NY 13-22 UTC (07:00-22:00 UTC in total)
        hour = datetime.now(timezone.utc).hour
        in_london = 7 <= hour < 13
        in_ny     = 13 <= hour < 22
        if not (in_london or in_ny):
            logger.info(f"{symbol}: Outside trading session - skipping")
            return

        # Duplicate / cooldown guard
        guard = order_manager.can_trade_symbol(symbol, "BUY", positions, pending)
        if not guard["allowed"]:
            logger.info(f"{symbol}: {guard['reason']}")
            return

        # News check
        news_summary = news_agent.summary(symbol)
        news_blocked = news_summary["news_window"]["is_blocked"]
        if news_blocked:
            logger.info(f"{symbol}: News blocked - {news_summary['news_window']['reason']}")

        # Fetch OHLCV across all timeframes
        df_trigger = connector.get_ohlcv(symbol, TRIGGER_TF)
        df_entry   = connector.get_ohlcv(symbol, PRIMARY_TF)
        df_confirm = connector.get_ohlcv(symbol, CONFIRM_TF)
        df_bias    = connector.get_ohlcv(symbol, BIAS_TF)

        if any(
            df is None or df.empty
            for df in [df_trigger, df_entry, df_confirm, df_bias]
        ):
            logger.warning(
                f"{symbol}: Failed to fetch complete OHLCV data"
            )
            return

        latest_closed_m5 = df_trigger.iloc[-1]["time"]

        if self.last_processed_candle.get(symbol) == latest_closed_m5:
            logger.info(
                f"{symbol}: M5 candle {latest_closed_m5} "
                f"already processed"
            )
            return

        self.last_processed_candle[symbol] = latest_closed_m5

        logger.info(
            f"{symbol}: Analysing closed candles | "
            f"M5={df_trigger.iloc[-1]['time']} | "
            f"M15={df_entry.iloc[-1]['time']} | "
            f"H1={df_confirm.iloc[-1]['time']} | "
            f"H4={df_bias.iloc[-1]['time']}"
        )

        # Update pair learning
        pair_observer.update_atr(symbol, df_trigger)

        # Regime detection
        regime = regime_detector.get_summary(symbol, df_bias, df_confirm)
        logger.info(f"{symbol}: Regime - H4: {regime['h4_regime']} | H1: {regime['h1_regime']} | {regime['reason']}")

        if not regime["can_trade"]:
            logger.info(f"{symbol}: Regime SKIP - {regime['reason']}")
            return 

        
        # M5=df_trigger, M15=df_entry, H1=df_confirm, H4=df_bias
        confluence = run_confluence(df_entry, df_confirm, df_bias, df_trigger)
        confluence["symbol"]     = symbol
        confluence["m5_trigger"] = confluence["breakdown"].get("candle_pattern", {}).get("signal", "NONE")
        confluence["m5_pattern"] = confluence["breakdown"].get("candle_pattern", {}).get("pattern", "None")

        direction     = confluence["direction"]
        probability   = confluence["probability"]
        checklist_met = confluence.get("checklist_met", False)
        checks_passed = confluence.get("checks_passed", 0)
        checks_failed = confluence.get("checks_failed", [])

        # Historical confidence boost from pair observer
        boost         = pair_observer.get_confidence_boost(symbol, confluence)
        adjusted_prob = min(100, probability + boost)

        logger.info(
            f"{symbol} | Direction: {direction} | Score: {probability}% "
            f"(adj: {adjusted_prob}%) | Checklist: {checks_passed}/5 | "
            f"M5: {confluence['m5_trigger']} | News: {'BLOCKED' if news_blocked else 'CLEAR'}"
        )

        if checks_failed:
            logger.info(f"{symbol}: Failed checks - {', '.join(checks_failed)}")

        # Record signal for learning
        action = "BLOCKED" if news_blocked else ("SIGNAL" if checklist_met else "SKIPPED")
        pair_observer.record_signal(symbol, confluence, action)

        # LLM review (optional, veto-only): it may turn the checklist's trade into HOLD, never flip or create one
        use_llm = bool(os.environ.get("ANTHROPIC_API_KEY")) and direction in ("BUY", "SELL")
        if use_llm:
            llm_decision = llm_agent.make_trade_decision(symbol, confluence, news_summary, account, positions)
            final_dir    = apply_veto(direction, llm_decision)
            final_conf   = llm_decision["confidence"]
            final_reason = llm_decision["reasoning"]
        else:
            final_dir    = direction
            final_conf   = int(adjusted_prob)
            final_reason = f"Checklist {checks_passed}/5 | Boost: {boost:+.1f}%"

        # Write signal to Excel
        excel_manager.write_signal({
            **confluence,
            "news_blocked":   news_blocked,
            "llm_decision":   final_dir,
            "llm_confidence": final_conf,
            "llm_reasoning":  final_reason,
        })

        # Entry conditions - ALL must pass
        m5_aligned = (confluence["m5_trigger"] == final_dir or confluence["m5_trigger"] == "NONE")

        should_trade = (
            not news_blocked
            and checks_passed >= 4
            and adjusted_prob >= MIN_SCORE_TO_TRADE
            and final_dir in ("BUY", "SELL")
            and m5_aligned
        )
        

        if not should_trade:
            return

        # Final duplicate check with actual direction
        guard2 = order_manager.can_trade_symbol(symbol, final_dir, positions, pending)
        if not guard2["allowed"]:
            logger.info(f"{symbol}: {guard2['reason']}")
            return
        # Correlation filter
        corr_check = correlation_filter.check(symbol, final_dir, positions, pending)
        if not corr_check["allowed"]:
            logger.info(f"{symbol}: Correlation SKIP - {corr_check['reason']}")
            return
        logger.info(f"{symbol}: Exposure - {correlation_filter.get_exposure_summary(positions, pending)}")

        # Determine entry type and place order
        entry_type    = order_manager.get_entry_type(confluence)
        tick          = connector.get_tick(symbol)
        if not tick:
            return
        current_price = tick["ask"] if final_dir == "BUY" else tick["bid"]

        size_factor = regime.get("size_factor", 1.0)
        if entry_type == "MARKET":
            self._place_market_order(symbol, final_dir, confluence, account, df_trigger, size_factor)
        else:
            limit_price = order_manager.get_limit_price(symbol, final_dir, confluence, current_price)
            self._place_limit_order(symbol, final_dir, limit_price, confluence, account, df_trigger, size_factor)

    def _place_market_order(self, symbol, direction, confluence, account, df, size_factor=1.0):
        tick    = connector.get_tick(symbol)
        if not tick:
            return
        price   = tick["ask"] if direction == "BUY" else tick["bid"]
        balance = account.get("balance", 0)

        sl = risk_manager.find_structure_sl(df, direction, symbol)
        if sl is None:
            return
        if direction == "BUY" and sl >= price:
            return
        if direction == "SELL" and sl <= price:
            return
        try:
            position = risk_manager.calculate_position(symbol=symbol,
                direction=direction,
                entry=price,
                sl=sl,
                balance=balance,
                df=df,
                size_factor=size_factor,)
        except ValueError as e:
            logger.warning(f"{symbol}: Position rejected: {e}")
            return
        validation = pre_trade_validator.validate(
            symbol=symbol,
            direction=direction,
            volume=position["volume"],
            entry=price,
            sl=position["sl"],
            tp=position["tp"],
            allowed_risk=position["allowed_risk"],
            account=account,
        )

        if not validation["allowed"]:
            logger.warning(
                f"{symbol}: MARKET order blocked — "
                f"{validation['reason']}"
            )
            return
        
        logger.info(
            f"MARKET {direction} | {symbol} | {position['volume']} lots | "
            f"Entry: {price} | SL: {position['sl']} ({position['sl_pips']}p) | "
            f"TP: {position['tp']} ({position['tp_pips']}p) | "
            f"R:R 1:{position['rr_ratio']} | Risk: ${position['risk_amount']}"
        )

        result = connector.place_order(
            symbol=symbol, direction=direction,
            volume=position["volume"], sl=position["sl"], tp=position["tp"],
            comment=f"bot_{confluence.get('probability')}pct",
        )

        if result:
            order_manager.record_trade(symbol, direction)
            self._log_trade(symbol, direction, position, price, confluence, "MARKET")

    def _place_limit_order(self, symbol, direction, limit_price, confluence, account, df, size_factor=1.0):
        balance = account.get("balance", 0)

        sl = risk_manager.find_structure_sl(df, direction, symbol)
        if sl is None:
            return
        if direction == "BUY" and sl >= limit_price:
            return
        if direction == "SELL" and sl <= limit_price:
            return

        try:
            position = risk_manager.calculate_position(
                symbol=symbol,
                direction=direction,
                entry=limit_price,
                sl=sl,
                balance=balance,
                df=df,
                size_factor=size_factor,
            )
        except ValueError as e:
            logger.warning(
                f"{symbol}: Pending position rejected — {e}"
            )
            return

        tick = connector.get_tick(symbol)
        if not tick:
            logger.warning(
                f"{symbol}: Pending order rejected — no live tick"
            )
            return
        current_price = tick["ask"] if direction == "BUY" else tick["bid"]

        if direction == "BUY":
            order_subtype = "LIMIT" if limit_price < current_price else "STOP"
        else:
            order_subtype = "LIMIT" if limit_price > current_price else "STOP"

        validation = pre_trade_validator.validate(
            symbol=symbol,
            direction=direction,
            volume=position["volume"],
            entry=limit_price,
            sl=position["sl"],
            tp=position["tp"],
            allowed_risk=position["allowed_risk"],
            account=account,
            is_pending=True,
        )

        if not validation["allowed"]:
            logger.warning(
                f"{symbol}: Pending {order_subtype} blocked — "
                f"{validation['reason']}"
            )
            return

        logger.info(
            f"PENDING {direction} {order_subtype} | {symbol} | {position['volume']} lots | "
            f"Entry: {limit_price} | SL: {position['sl']} ({position['sl_pips']}p) | "
            f"TP: {position['tp']} ({position['tp_pips']}p) | "
            f"R:R 1:{position['rr_ratio']} | Risk: ${position['risk_amount']}"
        )

        result = connector.place_pending_order(
            symbol=symbol,
            direction=direction,
            order_type=order_subtype,
            price=limit_price,
            volume=position["volume"],
            sl=position["sl"],
            tp=position["tp"],
            comment=f"bot_pending_{confluence.get('probability')}pct",
        )

        if result:
            order_manager.record_trade(symbol, direction)
            order_manager.track_pending(symbol, {
                "ticket":    result.get("order"),
                "symbol":    symbol,
                "direction": direction,
                "price":     limit_price,
            })
            self._log_trade(symbol, direction, position, limit_price, confluence, f"PENDING_{order_subtype}")

    def _log_trade(self, symbol, direction, position, entry, confluence, order_type):
        tags = " | ".join([
            k for k, v in confluence.get("breakdown", {}).items()
            if v.get("signal") == direction
        ])
        excel_manager.write_trade({
            "open_time": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
            "symbol":    symbol,
            "direction": direction,
            "volume":    position["volume"],
            "entry":     entry,
            "sl":        position["sl"],
            "tp":        position["tp"],
            "status":    order_type,
            "pnl":       0,
            "tags":      f"{tags} | {order_type} | Risk: ${position['risk_amount']} | SL: {position['sl_pips']}p",
        })
        self.trades_today += 1
        logger.info(f"Trade logged. Type: {order_type} | Risk: ${position['risk_amount']}")

    def _manage_breakeven(self, positions):
        if not BREAKEVEN["enabled"]:
            return

        for position in positions:
            try:
                ticket     = position["ticket"]
                symbol     = position["symbol"]
                direction  = position["type"]
                entry      = position["open_price"]
                current_sl = position["sl"]

                if current_sl == 0:
                    continue

                pip_size    = 0.01 if "JPY" in symbol else 0.0001
                buffer      = BREAKEVEN["buffer_pips"] * pip_size
                sl_distance = abs(entry - current_sl)

                tick = connector.get_tick(symbol)
                if not tick:
                    continue

                if direction == "BUY":
                    breakeven_sl = round(entry + buffer, 5)
                    pips_up      = tick["bid"] - entry
                    triggered    = pips_up >= sl_distance * BREAKEVEN["trigger_rr"]
                    already_be   = current_sl >= entry
                else:
                    breakeven_sl = round(entry - buffer, 5)
                    pips_up      = entry - tick["ask"]
                    triggered    = pips_up >= sl_distance * BREAKEVEN["trigger_rr"]
                    already_be   = current_sl <= entry

                if triggered and not already_be:
                    logger.info(f"BREAKEVEN | {symbol} #{ticket} | SL {current_sl} -> {breakeven_sl}")
                    self._modify_sl(ticket, symbol, breakeven_sl, position["tp"])

            except Exception as e:
                logger.error(f"Breakeven error for {position.get('symbol')}: {e}")

    def _modify_sl(self, ticket, symbol, new_sl, tp):
        if not MT5_AVAILABLE or not connector.connected:
            logger.info(f"[SIM] Would move SL to {new_sl} for #{ticket}")
            return
        request = {
            "action":   mt5.TRADE_ACTION_SLTP,
            "symbol":   symbol,
            "position": ticket,
            "sl":       new_sl,
            "tp":       tp,
        }
        result = mt5.order_send(request)
        if result is None:
            logger.error(f"order_send returned None modifying SL for #{ticket}: {mt5.last_error()}")
            return
        if result.retcode == mt5.TRADE_RETCODE_DONE:
            logger.info(f"SL moved to breakeven for #{ticket}")
        else:
            logger.error(f"Failed to modify SL for #{ticket}: {result.retcode}")

    def shutdown(self):
        self.running = False
        account = connector.get_account_info()          # before disconnect: afterwards there is no account to read
        connector.disconnect()
        excel_manager.update_dashboard(account or {}, bot_status="STOPPED")
        logger.info("Bot shut down cleanly.")


if __name__ == "__main__":
    bot = ForexBot()
    bot.run()