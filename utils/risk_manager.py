"""
Risk Manager
Handles all risk calculations:
- Fixed true risk per trade (FIXED_RISK_PCT, 0.25% of balance); the ATR/regime-scaled 0.5% cap only applies if it is None
- Structure-based SL (swing high/low)
- 3:1 R:R take profit
- 5% monthly profit target (pauses trading for the month)
- 3% daily drawdown hard stop (equity vs the first balance seen each UTC day)
The daily/monthly reference balances are saved per account login (BASELINES_FILE) so a restart does not reset them.
"""

import json
import logging
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd
from mt5_connector import connector

logger = logging.getLogger(__name__)

RISK_PER_TRADE_PCT = 0.5
# Fixed true risk per trade (% of balance). When set, ATR and regime scaling are
# ignored so every trade risks exactly this much. None restores the scaled 0.5% cap.
FIXED_RISK_PCT     = 0.25
MONTHLY_TARGET_PCT = 5.0
DAILY_DRAWDOWN_PCT = 3.0
RR_RATIO           = 3.0
SWING_LOOKBACK     = 20
MIN_SL_PIPS        = 10

BASELINES_FILE = Path(__file__).resolve().parent.parent / "risk_baselines.json"


class RiskManager:

    def __init__(self, baselines_file: Path = BASELINES_FILE):
        self.baselines_file = Path(baselines_file)
        self.login:                 Optional[int]   = None     # account the baselines below belong to
        self.monthly_start_balance: Optional[float] = None
        self.monthly_start_date:    Optional[str]   = None
        self.daily_start_balance:   Optional[float] = None
        self.daily_start_date:      Optional[str]   = None

    # ── Baseline persistence (one entry per account login) ─────────────────────

    def _read_baselines(self) -> dict:
        """All saved baselines {login: {...}}; empty when the file is missing or unreadable."""
        try:
            data = json.loads(self.baselines_file.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as e:
            logger.warning(f"Risk baselines file unreadable ({e}) - starting from the current balance")
            return {}

    @staticmethod
    def _valid_baseline(saved: dict, kind: str) -> tuple:
        """(date, balance) from a saved entry, or (None, None) if it is missing or malformed."""
        date, balance = saved.get(f"{kind}_date"), saved.get(f"{kind}_balance")
        if isinstance(date, str) and isinstance(balance, (int, float)) and balance > 0:
            return date, float(balance)
        return None, None

    def _restore_baselines(self, login: Optional[int]):
        """Switch to this account's saved baselines (none saved, or no login: start empty)."""
        saved = self._read_baselines().get(str(login), {}) if login is not None else {}
        if not isinstance(saved, dict):
            saved = {}
        self.login = login
        self.daily_start_date,   self.daily_start_balance   = self._valid_baseline(saved, "daily")
        self.monthly_start_date, self.monthly_start_balance = self._valid_baseline(saved, "monthly")
        if self.daily_start_date or self.monthly_start_date:
            logger.info(f"Risk baselines restored for account {login}: daily {self.daily_start_date} "
                        f"{self.daily_start_balance}, monthly {self.monthly_start_date} {self.monthly_start_balance}")

    def _save_baselines(self):
        """Write this account's baselines (atomically; other accounts' entries are kept). A failure is logged, not raised."""
        if self.login is None:
            return
        try:
            data = self._read_baselines()
            data[str(self.login)] = {
                "daily_date":      self.daily_start_date,   "daily_balance":   self.daily_start_balance,
                "monthly_date":    self.monthly_start_date, "monthly_balance": self.monthly_start_balance,
            }
            tmp = self.baselines_file.with_name(self.baselines_file.name + ".tmp")
            tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            os.replace(tmp, self.baselines_file)
        except OSError as e:
            logger.error(f"Could not save risk baselines to {self.baselines_file}: {e}")

    def update_balance_tracking(self, balance: float, login: Optional[int] = None):
        if login != self.login:
            self._restore_baselines(login)

        now        = datetime.now(timezone.utc)
        today      = now.strftime("%Y-%m-%d")
        this_month = now.strftime("%Y-%m")
        changed    = False

        if self.daily_start_date != today:
            self.daily_start_balance = balance
            self.daily_start_date    = today
            changed = True
            logger.info(f"New trading day - daily reference: {balance:.2f}")

        if self.monthly_start_date != this_month:
            self.monthly_start_balance = balance
            self.monthly_start_date    = this_month
            changed = True
            logger.info(f"New month - monthly reference: {balance:.2f}")

        if changed:
            self._save_baselines()

    def check_daily_drawdown(self, current_equity: float) -> dict:
        if self.daily_start_balance is None:
            return {"is_blocked": False, "reason": "Daily tracking not initialised"}

        daily_pnl     = current_equity - self.daily_start_balance
        daily_pnl_pct = (daily_pnl / self.daily_start_balance) * 100

        if daily_pnl_pct <= -DAILY_DRAWDOWN_PCT:
            return {
                "is_blocked": True,
                "daily_pnl":  round(daily_pnl, 2),
                "daily_pct":  round(daily_pnl_pct, 2),
                "reason":     f"Daily drawdown limit hit: {daily_pnl_pct:.2f}% (limit: -{DAILY_DRAWDOWN_PCT}%)",
            }

        remaining = DAILY_DRAWDOWN_PCT + daily_pnl_pct
        return {
            "is_blocked":  False,
            "daily_pnl":   round(daily_pnl, 2),
            "daily_pct":   round(daily_pnl_pct, 2),
            "buffer_left": round(remaining, 2),
            "reason":      f"Daily P&L: {daily_pnl_pct:+.2f}% | Buffer: {remaining:.2f}%",
        }

    def check_monthly_target(self, current_balance: float) -> dict:
        if self.monthly_start_balance is None:
            return {"is_reached": False, "reason": "Monthly tracking not initialised"}

        monthly_pnl     = current_balance - self.monthly_start_balance
        monthly_pnl_pct = (monthly_pnl / self.monthly_start_balance) * 100

        if monthly_pnl_pct >= MONTHLY_TARGET_PCT:
            return {
                "is_reached":  True,
                "monthly_pnl": round(monthly_pnl, 2),
                "monthly_pct": round(monthly_pnl_pct, 2),
                "reason":      f"Monthly target reached: {monthly_pnl_pct:.2f}%",
            }

        remaining = MONTHLY_TARGET_PCT - monthly_pnl_pct
        return {
            "is_reached":  False,
            "monthly_pnl": round(monthly_pnl, 2),
            "monthly_pct": round(monthly_pnl_pct, 2),
            "remaining":   round(remaining, 2),
            "reason":      f"Monthly P&L: {monthly_pnl_pct:+.2f}% | Remaining: {remaining:.2f}%",
        }

    def can_trade(self, account: dict) -> dict:
        balance = account.get("balance", 0)
        equity  = account.get("equity", 0)

        self.update_balance_tracking(balance, account.get("login"))

        daily   = self.check_daily_drawdown(equity)
        monthly = self.check_monthly_target(balance)

        if daily["is_blocked"]:
            logger.warning(f"TRADING BLOCKED - {daily['reason']}")
            return {"allowed": False, "reason": daily["reason"], "type": "daily_drawdown"}

        if monthly["is_reached"]:
            logger.info(f"TRADING PAUSED - {monthly['reason']}")
            return {"allowed": False, "reason": monthly["reason"], "type": "monthly_target"}

        return {
            "allowed": True,
            "daily":   daily,
            "monthly": monthly,
            "reason":  f"Daily: {daily['daily_pct']:+.2f}% | Monthly: {monthly['monthly_pct']:+.2f}%",
        }

    def calculate_atr_risk_factor(self, df: pd.DataFrame, lookback: int = 20) -> float:
        """
        Scale position size based on current volatility vs average.
        High volatility = smaller size. Low/normal volatility = full size.
        Returns multiplier between 0.25 and 1.0.
        """
        try:
            high  = df["high"]
            low   = df["low"]
            close = df["close"]
            tr1   = high - low
            tr2   = abs(high - close.shift(1))
            tr3   = abs(low  - close.shift(1))
            tr    = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
            atr   = tr.rolling(14).mean()

            current_atr = float(atr.iloc[-1])
            avg_atr     = float(atr.iloc[-lookback:].mean())

            if avg_atr == 0:
                return 1.0

            ratio = current_atr / avg_atr

            if ratio > 2.0:
                factor = 0.25
            elif ratio > 1.5:
                factor = 0.5
            elif ratio > 1.2:
                factor = 0.75
            else:
                factor = 1.0

            logger.info(f"ATR ratio: {ratio:.2f} -> risk factor: {factor}x")
            return factor

        except Exception as e:
            logger.debug(f"ATR risk factor error: {e}")
            return 1.0

    def find_structure_sl(self, df: pd.DataFrame, direction: str,
                          symbol: str) -> Optional[float]:
        if len(df) < SWING_LOOKBACK + 2:
            return None

        pip_size = 0.01 if "JPY" in symbol else 0.0001
        buffer   = pip_size * 3
        recent   = df.iloc[-(SWING_LOOKBACK + 1):-1]

        if direction == "BUY":
            swing_low = self._find_swing_low(recent)
            if swing_low is not None:
                sl = round(swing_low - buffer, 5)
                logger.info(f"Structure SL (BUY): swing low {swing_low:.5f} -> SL {sl:.5f}")
                return sl

        elif direction == "SELL":
            swing_high = self._find_swing_high(recent)
            if swing_high is not None:
                sl = round(swing_high + buffer, 5)
                logger.info(f"Structure SL (SELL): swing high {swing_high:.5f} -> SL {sl:.5f}")
                return sl

        return None

    def _find_swing_low(self, df: pd.DataFrame) -> Optional[float]:
        lows = df["low"].values
        for i in range(len(lows) - 2, 1, -1):
            if lows[i] < lows[i - 1] and lows[i] < lows[i + 1]:
                return float(lows[i])
        return float(df["low"].min())

    def _find_swing_high(self, df: pd.DataFrame) -> Optional[float]:
        highs = df["high"].values
        for i in range(len(highs) - 2, 1, -1):
            if highs[i] > highs[i - 1] and highs[i] > highs[i + 1]:
                return float(highs[i])
        return float(df["high"].max())

    def calculate_position(
        self,
        symbol: str,
        direction: str,
        entry: float,
        sl: float,
        balance: float,
        df=None,
        size_factor: float = 1.0,
    ) -> dict:
        """
        Calculate a broker-aware position size.

        Position sizing is based on the monetary loss between entry and SL,
        using MT5's order_calc_profit(). This supports forex, JPY pairs,
        XAUUSD and different account currencies.
        """
        direction = direction.upper()

        if direction not in ("BUY", "SELL"):
            raise ValueError(f"Invalid direction: {direction}")

        if balance <= 0:
            raise ValueError(f"Invalid account balance: {balance}")

        if entry <= 0 or sl <= 0:
            raise ValueError(f"Invalid entry or SL: entry={entry}, sl={sl}")

        # Ensure SL is on the correct side of entry
        if direction == "BUY" and sl >= entry:
            raise ValueError(
                f"{symbol}: BUY stop loss {sl} must be below entry {entry}"
            )

        if direction == "SELL" and sl <= entry:
            raise ValueError(
                f"{symbol}: SELL stop loss {sl} must be above entry {entry}"
            )

        # Obtain specifications directly from the broker
        symbol_info = connector.get_symbol_info(symbol)

        if not symbol_info:
            raise ValueError(
                f"Could not get broker specifications for {symbol}"
            )

        point = symbol_info["point"]
        digits = symbol_info["digits"]
        volume_min = symbol_info["volume_min"]
        volume_max = symbol_info["volume_max"]
        volume_step = symbol_info["volume_step"]
        stops_level = symbol_info.get("stops_level", 0)

        if point <= 0:
            raise ValueError(f"{symbol}: Invalid broker point size: {point}")

        if volume_step <= 0:
            raise ValueError(
                f"{symbol}: Invalid broker volume step: {volume_step}"
            )

        # Convert the stop distance into broker points
        sl_distance = abs(entry - sl)
        sl_points = sl_distance / point

        # Common display convention:
        # 5-digit EURUSD: 10 points = 1 pip
        # 3-digit USDJPY: 10 points = 1 pip
        # Other symbols: display the distance in broker points
        points_per_pip = 10 if digits in (3, 5) else 1
        pip_size = point * points_per_pip
        sl_pips = sl_distance / pip_size

        # Broker minimum stop-distance check
        if stops_level > 0 and sl_points < stops_level:
            raise ValueError(
                f"{symbol}: SL distance {sl_points:.1f} points is below "
                f"the broker minimum of {stops_level} points"
            )

        # Apply the strategy's minimum stop distance
        if sl_pips < MIN_SL_PIPS:
            logger.warning(
                f"{symbol}: SL too tight ({sl_pips:.1f} pips). "
                f"Expanding to {MIN_SL_PIPS} pips."
            )

            sl_pips = MIN_SL_PIPS
            sl_distance = sl_pips * pip_size
            sl_points = sl_distance / point

            if direction == "BUY":
                sl = round(entry - sl_distance, digits)
            else:
                sl = round(entry + sl_distance, digits)

        else:
            # Normalize the supplied SL to the broker's precision
            sl = round(sl, digits)

        # Calculate take profit using the configured R:R ratio
        tp_distance = sl_distance * RR_RATIO

        if direction == "BUY":
            tp = round(entry + tp_distance, digits)
        else:
            tp = round(entry - tp_distance, digits)

        tp_pips = sl_pips * RR_RATIO

        # Reduce risk during unusually high volatility
        atr_factor = (
            self.calculate_atr_risk_factor(df)
            if df is not None
            else 1.0
        )

        # Ensure the regime size factor remains between 0 and 1
        size_factor = max(0.0, min(size_factor, 1.0))

        # Final risk combines base risk, volatility adjustment and regime adjustment,
        # unless a fixed risk is configured
        if FIXED_RISK_PCT is not None:
            risk_pct = FIXED_RISK_PCT
        else:
            risk_pct = (
                RISK_PER_TRADE_PCT
                * atr_factor
                * size_factor
            )

        allowed_risk = balance * (risk_pct / 100)

        if allowed_risk <= 0:
            raise ValueError(
                f"{symbol}: Invalid allowed risk amount: {allowed_risk}"
            )

        # Ask MT5 how much one lot would lose if the SL were hit
        loss_per_lot = connector.calculate_profit(
            symbol=symbol,
            direction=direction,
            volume=1.0,
            entry=entry,
            exit_price=sl,
        )

        if loss_per_lot is None:
            raise ValueError(
                f"{symbol}: MT5 could not calculate the loss per lot"
            )

        loss_per_lot = abs(loss_per_lot)

        if loss_per_lot <= 0:
            raise ValueError(
                f"{symbol}: Invalid calculated loss per lot: {loss_per_lot}"
            )

        # Calculate raw volume from the permitted monetary risk
        raw_volume = allowed_risk / loss_per_lot

        # Always round DOWN so risk is not accidentally increased
        volume = math.floor(
            (raw_volume + 1e-12) / volume_step
        ) * volume_step

        volume = min(volume, volume_max)
        volume = round(volume, 8)

        # Do not force the minimum lot if it would exceed allowed risk
        if volume < volume_min:
            minimum_lot_risk = connector.calculate_profit(
                symbol=symbol,
                direction=direction,
                volume=volume_min,
                entry=entry,
                exit_price=sl,
            )

            minimum_lot_risk = (
                abs(minimum_lot_risk)
                if minimum_lot_risk is not None
                else None
            )

            raise ValueError(
                f"{symbol}: Calculated volume {raw_volume:.8f} is below "
                f"the broker minimum of {volume_min}. "
                f"Minimum-lot risk would be "
                f"${minimum_lot_risk:.2f}."
                if minimum_lot_risk is not None
                else
                f"{symbol}: Calculated volume {raw_volume:.8f} is below "
                f"the broker minimum of {volume_min}."
            )

        # Verify actual monetary risk after volume rounding
        actual_risk = connector.calculate_profit(
            symbol=symbol,
            direction=direction,
            volume=volume,
            entry=entry,
            exit_price=sl,
        )

        if actual_risk is None:
            raise ValueError(
                f"{symbol}: MT5 could not verify the final position risk"
            )

        actual_risk = abs(actual_risk)

        # Small tolerance for broker rounding
        if actual_risk > allowed_risk * 1.01:
            raise ValueError(
                f"{symbol}: Actual risk ${actual_risk:.2f} exceeds "
                f"allowed risk ${allowed_risk:.2f}"
            )

        logger.info(
            f"{symbol} {direction} position calculated | "
            f"Volume: {volume} | "
            f"Entry: {entry} | "
            f"SL: {sl} | "
            f"TP: {tp} | "
            f"Risk: ${actual_risk:.2f} ({risk_pct:.3f}%) | "
            f"R:R 1:{RR_RATIO}"
        )

        return {
            "volume": volume,
            "sl": sl,
            "tp": tp,
            "sl_points": round(sl_points, 1),
            "sl_pips": round(sl_pips, 1),
            "tp_pips": round(tp_pips, 1),
            "risk_amount": round(actual_risk, 2),
            "allowed_risk": round(allowed_risk, 2),
            "risk_pct": round(risk_pct, 3),
            "atr_factor": atr_factor,
            "rr_ratio": RR_RATIO,
            "loss_per_lot": round(loss_per_lot, 2),
        }

    def get_status(self, account: dict) -> dict:
        balance = account.get("balance", 0)
        equity  = account.get("equity", 0)
        daily   = self.check_daily_drawdown(equity)
        monthly = self.check_monthly_target(balance)

        return {
            "balance":       balance,
            "equity":        equity,
            "daily_pnl":     daily.get("daily_pnl", 0),
            "daily_pct":     daily.get("daily_pct", 0),
            "daily_blocked": daily["is_blocked"],
            "monthly_pnl":   monthly.get("monthly_pnl", 0),
            "monthly_pct":   monthly.get("monthly_pct", 0),
            "monthly_done":  monthly["is_reached"],
            "can_trade":     not daily["is_blocked"] and not monthly["is_reached"],
        }


risk_manager = RiskManager()