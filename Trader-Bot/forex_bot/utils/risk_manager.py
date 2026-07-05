"""
Risk Manager
Handles all risk calculations:
- ATR-scaled risk per trade (max 0.5%)
- Structure-based SL (swing high/low)
- 3:1 R:R take profit
- 5% monthly profit target
- 3% daily drawdown hard stop
"""

import logging
from datetime import datetime, timezone
from typing import Optional

import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)

RISK_PER_TRADE_PCT = 0.5
MONTHLY_TARGET_PCT = 5.0
DAILY_DRAWDOWN_PCT = 3.0
RR_RATIO           = 3.0
SWING_LOOKBACK     = 20
MIN_SL_PIPS        = 10


class RiskManager:

    def __init__(self):
        self.monthly_start_balance: Optional[float] = None
        self.monthly_start_date:    Optional[str]   = None
        self.daily_start_balance:   Optional[float] = None
        self.daily_start_date:      Optional[str]   = None

    def update_balance_tracking(self, balance: float):
        now        = datetime.now(timezone.utc)
        today      = now.strftime("%Y-%m-%d")
        this_month = now.strftime("%Y-%m")

        if self.daily_start_date != today:
            self.daily_start_balance = balance
            self.daily_start_date    = today
            logger.info(f"New trading day - daily reference: {balance:.2f}")

        if self.monthly_start_date != this_month:
            self.monthly_start_balance = balance
            self.monthly_start_date    = this_month
            logger.info(f"New month - monthly reference: {balance:.2f}")

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

        self.update_balance_tracking(balance)

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

    def calculate_position(self, symbol: str, direction: str, entry: float,
                           sl: float, balance: float, df=None) -> dict:
        """
        Calculate position size with ATR-based risk scaling.
        Risk scales between 0.125% and 0.5% based on volatility.
        """
        pip_size    = 0.01 if "JPY" in symbol else 0.0001
        sl_distance = abs(entry - sl)
        sl_pips     = sl_distance / pip_size

        if sl_pips < MIN_SL_PIPS:
            logger.warning(f"SL too tight ({sl_pips:.1f} pips) - using minimum {MIN_SL_PIPS} pips")
            sl_pips     = MIN_SL_PIPS
            sl_distance = sl_pips * pip_size
            if direction == "BUY":
                sl = round(entry - sl_distance, 5)
            else:
                sl = round(entry + sl_distance, 5)

        tp_distance = sl_distance * RR_RATIO
        if direction == "BUY":
            tp = round(entry + tp_distance, 5)
        else:
            tp = round(entry - tp_distance, 5)

        tp_pips = sl_pips * RR_RATIO

        # ATR-based risk scaling
        atr_factor  = self.calculate_atr_risk_factor(df) if df is not None else 1.0
        risk_pct    = RISK_PER_TRADE_PCT * atr_factor
        risk_amount = balance * (risk_pct / 100)

        if "JPY" in symbol:
            pip_value_per_lot = 1000 / entry
        else:
            pip_value_per_lot = 10.0

        volume = risk_amount / (sl_pips * pip_value_per_lot)
        volume = round(max(0.01, min(volume, 10.0)), 2)

        return {
            "volume":      volume,
            "sl":          sl,
            "tp":          tp,
            "sl_pips":     round(sl_pips, 1),
            "tp_pips":     round(tp_pips, 1),
            "risk_amount": round(risk_amount, 2),
            "risk_pct":    round(risk_pct, 3),
            "atr_factor":  atr_factor,
            "rr_ratio":    RR_RATIO,
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