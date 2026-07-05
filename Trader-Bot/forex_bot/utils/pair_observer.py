"""
Pair Observer
Tracks and learns the behaviour of each trading pair over time.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)

DATA_FILE = "pair_data.json"


class PairObserver:

    def __init__(self):
        self.data = self._load()

    def _load(self) -> dict:
        if Path(DATA_FILE).exists():
            try:
                with open(DATA_FILE, "r") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _save(self):
        try:
            with open(DATA_FILE, "w") as f:
                json.dump(self.data, f, indent=2, default=str)
        except Exception as e:
            logger.error(f"Failed to save pair data: {e}")

    def _init_symbol(self, symbol: str):
        if symbol not in self.data:
            self.data[symbol] = {
                "trades":              [],
                "strategy_stats":      {},
                "session_atr":         {},
                "signal_history":      [],
                "total_trades":        0,
                "winning_trades":      0,
                "losing_trades":       0,
                "total_pips":          0.0,
                "avg_win_pips":        0.0,
                "avg_loss_pips":       0.0,
                "best_strategy_combo": None,
            }

    def calculate_atr(self, df: pd.DataFrame, period: int = 14) -> float:
        if len(df) < period + 1:
            return 0.0
        high  = df["high"]
        low   = df["low"]
        close = df["close"]
        tr1   = high - low
        tr2   = abs(high - close.shift(1))
        tr3   = abs(low  - close.shift(1))
        tr    = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr   = tr.rolling(period).mean().iloc[-1]
        return round(float(atr), 5)

    def get_session(self) -> str:
        hour = datetime.now(timezone.utc).hour
        if   0  <= hour < 7:  return "Asian"
        elif 7  <= hour < 12: return "London"
        elif 12 <= hour < 17: return "NY"
        else:                  return "Late"

    def record_signal(self, symbol: str, confluence: dict, action: str):
        self._init_symbol(symbol)
        session   = self.get_session()
        breakdown = confluence.get("breakdown", {})
        fired     = [k for k, v in breakdown.items() if v.get("signal") == confluence.get("direction")]
        combo_key = "+".join(sorted(fired))

        record = {
            "time":        datetime.now(timezone.utc).isoformat(),
            "session":     session,
            "direction":   confluence.get("direction"),
            "probability": confluence.get("probability"),
            "strategies":  fired,
            "combo_key":   combo_key,
            "action":      action,
            "outcome":     None,
            "pips":        None,
        }

        self.data[symbol]["signal_history"].append(record)
        if len(self.data[symbol]["signal_history"]) > 500:
            self.data[symbol]["signal_history"] = self.data[symbol]["signal_history"][-500:]
        self._save()

    def record_trade_outcome(self, symbol: str, pips: float, won: bool, combo_key: str = ""):
        self._init_symbol(symbol)
        sym = self.data[symbol]
        sym["total_trades"] += 1
        sym["total_pips"]   += pips

        if won:
            sym["winning_trades"] += 1
            sym["avg_win_pips"]    = round(
                (sym["avg_win_pips"] * (sym["winning_trades"] - 1) + pips) / sym["winning_trades"], 1)
        else:
            sym["losing_trades"] += 1
            sym["avg_loss_pips"]  = round(
                (sym["avg_loss_pips"] * (sym["losing_trades"] - 1) + abs(pips)) / sym["losing_trades"], 1)

        if combo_key:
            if combo_key not in sym["strategy_stats"]:
                sym["strategy_stats"][combo_key] = {"trades": 0, "wins": 0, "win_rate": 0.0}
            combo = sym["strategy_stats"][combo_key]
            combo["trades"] += 1
            if won:
                combo["wins"] += 1
            combo["win_rate"] = round((combo["wins"] / combo["trades"]) * 100, 1)

        self._save()

    def update_atr(self, symbol: str, df: pd.DataFrame):
        self._init_symbol(symbol)
        session = self.get_session()
        atr     = self.calculate_atr(df)

        if session not in self.data[symbol]["session_atr"]:
            self.data[symbol]["session_atr"][session] = []

        self.data[symbol]["session_atr"][session].append(atr)
        if len(self.data[symbol]["session_atr"][session]) > 50:
            self.data[symbol]["session_atr"][session] = self.data[symbol]["session_atr"][session][-50:]
        self._save()

    def get_win_rate(self, symbol: str) -> float:
        self._init_symbol(symbol)
        total = self.data[symbol]["total_trades"]
        wins  = self.data[symbol]["winning_trades"]
        return round((wins / total) * 100, 1) if total > 0 else 0.0

    def get_avg_session_atr(self, symbol: str) -> float:
        self._init_symbol(symbol)
        session  = self.get_session()
        readings = self.data[symbol]["session_atr"].get(session, [])
        return round(float(np.mean(readings)), 5) if readings else 0.0

    def get_combo_win_rate(self, symbol: str, combo_key: str) -> float:
        self._init_symbol(symbol)
        combo = self.data[symbol]["strategy_stats"].get(combo_key, {})
        return combo.get("win_rate", 0.0)

    def get_confidence_boost(self, symbol: str, confluence: dict) -> float:
        breakdown = confluence.get("breakdown", {})
        direction = confluence.get("direction")
        fired     = [k for k, v in breakdown.items() if v.get("signal") == direction]
        combo_key = "+".join(sorted(fired))
        win_rate  = self.get_combo_win_rate(symbol, combo_key)
        trades    = self.data[symbol]["strategy_stats"].get(combo_key, {}).get("trades", 0)

        if trades < 3:
            return 0.0

        adjustment = (win_rate - 50) * 0.3
        return round(adjustment, 1)

    def get_symbol_summary(self, symbol: str) -> dict:
        self._init_symbol(symbol)
        sym = self.data[symbol]
        return {
            "symbol":       symbol,
            "session":      self.get_session(),
            "total_trades": sym["total_trades"],
            "win_rate":     self.get_win_rate(symbol),
            "avg_win_pips": sym["avg_win_pips"],
            "avg_atr":      self.get_avg_session_atr(symbol),
            "best_combo":   sym["best_strategy_combo"],
        }


pair_observer = PairObserver()