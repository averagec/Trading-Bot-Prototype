"""
Pre-Trade Validator

Performs final broker, execution-cost, risk and margin checks
before any order is submitted to MT5.
"""

import logging
import math
from datetime import datetime, timezone

from mt5_connector import connector

logger = logging.getLogger(__name__)


class PreTradeValidator:

    MAX_TICK_AGE_SECONDS = 10

    # Initial conservative threshold.
    # Replace with pair/session-specific values after collecting spread data.
    MAX_SPREAD_POINTS = {
        "EURUSD": 25,
        "GBPUSD": 35,
        "USDJPY": 30,
        "AUDUSD": 30,
        "USDCAD": 35,
        "XAUUSD": 80,
    }

    # Do not allow spread to consume more than this percentage
    # of the initial stop distance.
    MAX_SPREAD_TO_STOP_PCT = 10.0

    # Leave some free margin instead of using everything available.
    MAX_MARGIN_USAGE_PCT = 80.0

    # Allow a small tolerance for rounding.
    RISK_TOLERANCE_PCT = 1.0

    def validate(
        self,
        symbol: str,
        direction: str,
        volume: float,
        entry: float,
        sl: float,
        tp: float,
        allowed_risk: float,
        account: dict,
        is_pending: bool = False,
    ) -> dict:
        """
        Validate an order immediately before submission.

        Returns:
            {
                "allowed": bool,
                "reason": str,
                "checks": dict,
                "actual_risk": float,
                "spread_points": float,
                "required_margin": float,
            }
        """

        direction = direction.upper()
        checks = {}
        failures = []

        if direction not in ("BUY", "SELL"):
            return self._reject(
                f"Invalid direction: {direction}",
                checks,
            )

        symbol_info = connector.get_symbol_info(symbol)

        if not symbol_info:
            return self._reject(
                f"{symbol}: symbol information unavailable",
                checks,
            )

        tick = connector.get_tick(symbol)

        if not tick:
            return self._reject(
                f"{symbol}: live tick unavailable",
                checks,
            )

        point = symbol_info["point"]
        digits = symbol_info["digits"]
        volume_min = symbol_info["volume_min"]
        volume_max = symbol_info["volume_max"]
        volume_step = symbol_info["volume_step"]
        stops_level = symbol_info.get("stops_level", 0)

        # 1. Tick freshness
        tick_age = (
            datetime.now(timezone.utc) - tick["time"]
        ).total_seconds()

        tick_fresh = (
            0 <= tick_age <= self.MAX_TICK_AGE_SECONDS
        )

        checks["tick"] = {
            "passed": tick_fresh,
            "age_seconds": round(tick_age, 3),
        }

        if not tick_fresh:
            failures.append(
                f"stale tick ({tick_age:.1f}s old)"
            )

        # 2. Use the current executable market price
        market_price = (
            tick["ask"]
            if direction == "BUY"
            else tick["bid"]
        )

        # For a market order, the passed entry estimate should not be
        # trusted over the latest executable price.
        execution_entry = entry if is_pending else market_price

        # 3. Spread checks
        spread_points = tick["spread_points"]
        max_spread = self.MAX_SPREAD_POINTS.get(symbol)

        absolute_spread_ok = (
            max_spread is None
            or spread_points <= max_spread
        )

        stop_distance = abs(execution_entry - sl)
        stop_points = (
            stop_distance / point
            if point > 0
            else 0
        )

        spread_to_stop_pct = (
            spread_points / stop_points * 100
            if stop_points > 0
            else float("inf")
        )

        relative_spread_ok = (
            spread_to_stop_pct
            <= self.MAX_SPREAD_TO_STOP_PCT
        )

        checks["spread"] = {
            "passed": absolute_spread_ok and relative_spread_ok,
            "spread_points": round(spread_points, 1),
            "maximum_points": max_spread,
            "spread_to_stop_pct": round(
                spread_to_stop_pct,
                2,
            ),
        }

        if not absolute_spread_ok:
            failures.append(
                f"spread {spread_points:.1f} points exceeds "
                f"maximum {max_spread}"
            )

        if not relative_spread_ok:
            failures.append(
                f"spread is {spread_to_stop_pct:.1f}% "
                f"of stop distance"
            )

        # 4. Validate SL and TP direction
        if direction == "BUY":
            price_structure_ok = (
                sl < execution_entry < tp
            )
        else:
            price_structure_ok = (
                tp < execution_entry < sl
            )

        checks["price_structure"] = {
            "passed": price_structure_ok,
            "entry": execution_entry,
            "sl": sl,
            "tp": tp,
        }

        if not price_structure_ok:
            failures.append(
                f"invalid {direction} entry/SL/TP arrangement"
            )

        # 5. Broker minimum stop distance
        stop_level_ok = (
            stops_level <= 0
            or stop_points >= stops_level
        )

        checks["stop_level"] = {
            "passed": stop_level_ok,
            "stop_points": round(stop_points, 1),
            "minimum_points": stops_level,
        }

        if not stop_level_ok:
            failures.append(
                f"SL distance {stop_points:.1f} points is below "
                f"broker minimum {stops_level}"
            )

        # 6. Volume range and step
        volume_in_range = (
            volume_min <= volume <= volume_max
        )

        step_units = volume / volume_step
        volume_on_step = math.isclose(
            step_units,
            round(step_units),
            abs_tol=1e-8,
        )

        checks["volume"] = {
            "passed": volume_in_range and volume_on_step,
            "volume": volume,
            "minimum": volume_min,
            "maximum": volume_max,
            "step": volume_step,
        }

        if not volume_in_range:
            failures.append(
                f"volume {volume} outside broker range "
                f"{volume_min}-{volume_max}"
            )

        if not volume_on_step:
            failures.append(
                f"volume {volume} does not follow step "
                f"{volume_step}"
            )

        # 7. Recalculate actual risk using the latest entry price
        actual_risk = connector.calculate_profit(
            symbol=symbol,
            direction=direction,
            volume=volume,
            entry=execution_entry,
            exit_price=sl,
        )

        if actual_risk is None:
            risk_ok = False
            actual_risk_value = None
            failures.append(
                "unable to calculate actual monetary risk"
            )
        else:
            actual_risk_value = abs(actual_risk)

            maximum_risk = allowed_risk * (
                1 + self.RISK_TOLERANCE_PCT / 100
            )

            risk_ok = actual_risk_value <= maximum_risk

            if not risk_ok:
                failures.append(
                    f"actual risk ${actual_risk_value:.2f} exceeds "
                    f"allowed risk ${allowed_risk:.2f}"
                )

        checks["risk"] = {
            "passed": risk_ok,
            "actual_risk": (
                round(actual_risk_value, 2)
                if actual_risk_value is not None
                else None
            ),
            "allowed_risk": round(allowed_risk, 2),
        }

        # 8. Required margin
        required_margin = connector.calculate_margin(
            symbol=symbol,
            direction=direction,
            volume=volume,
            price=execution_entry,
        )

        free_margin = float(
            account.get("margin_free", 0)
        )

        if required_margin is None:
            margin_ok = False
            failures.append(
                "unable to calculate required margin"
            )
        else:
            maximum_margin = free_margin * (
                self.MAX_MARGIN_USAGE_PCT / 100
            )

            margin_ok = required_margin <= maximum_margin

            if not margin_ok:
                failures.append(
                    f"required margin ${required_margin:.2f} "
                    f"is too high for free margin "
                    f"${free_margin:.2f}"
                )

        checks["margin"] = {
            "passed": margin_ok,
            "required": (
                round(required_margin, 2)
                if required_margin is not None
                else None
            ),
            "free": round(free_margin, 2),
        }

        if failures:
            return {
                "allowed": False,
                "reason": "; ".join(failures),
                "checks": checks,
                "execution_entry": execution_entry,
                "actual_risk": actual_risk_value,
                "spread_points": spread_points,
                "required_margin": required_margin,
            }

        return {
            "allowed": True,
            "reason": "All pre-trade checks passed",
            "checks": checks,
            "execution_entry": round(
                execution_entry,
                digits,
            ),
            "actual_risk": round(actual_risk_value, 2),
            "spread_points": round(spread_points, 1),
            "required_margin": round(required_margin, 2),
        }

    def _reject(self, reason: str, checks: dict) -> dict:
        return {
            "allowed": False,
            "reason": reason,
            "checks": checks,
            "actual_risk": None,
            "spread_points": None,
            "required_margin": None,
        }


pre_trade_validator = PreTradeValidator()