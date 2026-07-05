"""
Correlation Filter
Prevents overexposure to the same currency across multiple trades.

Tracks net currency exposure across all open positions and pending orders.
Blocks new trades that would exceed the exposure limit for any single currency.

Example:
  EURUSD BUY  -> +EUR, -USD
  GBPUSD BUY  -> +GBP, -USD  (USD now doubly short)
  USDJPY SELL -> -USD, +JPY  (USD now triply short) -> BLOCKED
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

MAX_CURRENCY_EXPOSURE = 2
CORRELATED_PAIRS = {
    "EURUSD": ["GBPUSD", "AUDUSD", "NZDUSD"],
    "GBPUSD": ["EURUSD", "AUDUSD"],
    "AUDUSD": ["EURUSD", "GBPUSD", "NZDUSD"],
    "USDJPY": ["USDCAD", "USDCHF"],
    "USDCAD": ["USDJPY", "USDCHF"],
    "XAUUSD": [],
}


class CorrelationFilter:

    def check(self, symbol: str, direction: str,
              open_positions: list, pending_orders: list) -> dict:
        base, quote = self._get_currencies(symbol)
        if not base or not quote:
            return {"allowed": True, "reason": "Could not parse currencies"}

        exposure = self._build_exposure(open_positions, pending_orders)

        if direction == "BUY":
            adding_long  = base
            adding_short = quote
        else:
            adding_long  = quote
            adding_short = base

        current_long = exposure.get(f"{adding_long}_long", 0)
        if current_long >= MAX_CURRENCY_EXPOSURE:
            return {
                "allowed": False,
                "reason":  f"Max {adding_long} long exposure reached ({current_long}/{MAX_CURRENCY_EXPOSURE})",
                "exposure": exposure,
            }

        current_short = exposure.get(f"{adding_short}_short", 0)
        if current_short >= MAX_CURRENCY_EXPOSURE:
            return {
                "allowed": False,
                "reason":  f"Max {adding_short} short exposure reached ({current_short}/{MAX_CURRENCY_EXPOSURE})",
                "exposure": exposure,
            }

        correlated     = CORRELATED_PAIRS.get(symbol, [])
        open_symbols   = [p["symbol"] for p in open_positions] + \
                         [p.get("symbol") for p in pending_orders]
        correlated_open = [s for s in correlated if s in open_symbols]

        if len(correlated_open) >= MAX_CURRENCY_EXPOSURE:
            return {
                "allowed": False,
                "reason":  f"Too many correlated pairs open: {', '.join(correlated_open)}",
                "exposure": exposure,
            }

        total_exposure = sum(exposure.values())
        return {
            "allowed":        True,
            "reason":         f"Exposure OK | {adding_long} long: {current_long+1} | {adding_short} short: {current_short+1}",
            "exposure":       exposure,
            "total_exposure": total_exposure,
        }

    def _get_currencies(self, symbol: str):
        if len(symbol) < 6:
            return None, None
        return symbol[:3].upper(), symbol[3:6].upper()

    def _build_exposure(self, open_positions: list, pending_orders: list) -> dict:
        exposure   = {}
        all_trades = open_positions + pending_orders

        for trade in all_trades:
            symbol    = trade.get("symbol", "")
            direction = trade.get("type", trade.get("direction", ""))
            base, quote = self._get_currencies(symbol)
            if not base or not quote:
                continue

            if direction == "BUY":
                long_ccy  = base
                short_ccy = quote
            else:
                long_ccy  = quote
                short_ccy = base

            exposure[f"{long_ccy}_long"]   = exposure.get(f"{long_ccy}_long", 0) + 1
            exposure[f"{short_ccy}_short"] = exposure.get(f"{short_ccy}_short", 0) + 1

        return exposure

    def get_exposure_summary(self, open_positions: list, pending_orders: list) -> str:
        exposure   = self._build_exposure(open_positions, pending_orders)
        if not exposure:
            return "No open exposure"

        parts      = []
        currencies = set(k.split("_")[0] for k in exposure.keys())
        for ccy in sorted(currencies):
            long  = exposure.get(f"{ccy}_long", 0)
            short = exposure.get(f"{ccy}_short", 0)
            if long > 0 or short > 0:
                parts.append(f"{ccy}(+{long}/-{short})")

        return " | ".join(parts)


correlation_filter = CorrelationFilter()