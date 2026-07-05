"""
Backtest Engine
Pulls 12 months of historical OHLCV from MT5 and runs every strategy
combination across all pairs.
"""

import sys
import logging
from pathlib import Path
from datetime import datetime, timezone
from itertools import combinations
from typing import Optional

import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

from strategies.smc_strategies import (
    detect_liquidity_sweep, detect_quarterly_theory,
    detect_candle_pattern, detect_trend, detect_fvg
)

logger = logging.getLogger(__name__)

LOOKBACK_MONTHS = 12
RISK_PER_TRADE  = 0.005
RR_RATIO        = 3.0
SL_PIPS_DEFAULT = 15
MIN_TRADES      = 10


class BacktestEngine:

    def __init__(self, pairs: list, timeframes: dict):
        self.pairs      = pairs
        self.timeframes = timeframes
        self.results    = {}

    def load_data(self, symbol: str, timeframe: str) -> Optional[pd.DataFrame]:
        try:
            import MetaTrader5 as mt5

            tf_map = {
                "M5":  mt5.TIMEFRAME_M5,
                "M15": mt5.TIMEFRAME_M15,
                "H1":  mt5.TIMEFRAME_H1,
                "H4":  mt5.TIMEFRAME_H4,
                "D1":  mt5.TIMEFRAME_D1,
            }

            if not mt5.initialize():
                logger.error("MT5 init failed")
                return None

            tf      = tf_map.get(timeframe)
            num     = int(timeframe[1:]) if timeframe[0] == "M" else (int(timeframe[1:]) * 60)
            candles = min(LOOKBACK_MONTHS * 30 * 24 * 60 // num, 50000)
            rates   = mt5.copy_rates_from_pos(symbol, tf, 0, candles)
            mt5.shutdown()

            if rates is None:
                return None

            df = pd.DataFrame(rates)
            df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
            df.rename(columns={"tick_volume": "volume"}, inplace=True)
            return df[["time", "open", "high", "low", "close", "volume"]].reset_index(drop=True)

        except Exception as e:
            logger.error(f"Data load failed for {symbol} {timeframe}: {e}")
            return None

    def backtest_combo(self, symbol: str, df_m15: pd.DataFrame,
                       df_h1: pd.DataFrame, df_h4: pd.DataFrame,
                       active_strategies: list) -> dict:
        trades   = []
        pip_size = 0.01 if "JPY" in symbol else 0.0001
        sl_dist  = SL_PIPS_DEFAULT * pip_size
        tp_dist  = sl_dist * RR_RATIO

        for i in range(200, len(df_m15) - 5):
            candle_time = df_m15.iloc[i]["time"]
            hour        = candle_time.hour

            in_session = (7 <= hour < 12) or (13 <= hour < 18)
            if not in_session:
                continue

            h1_slice  = df_h1[df_h1["time"] <= candle_time].tail(300)
            h4_slice  = df_h4[df_h4["time"] <= candle_time].tail(300)
            m15_slice = df_m15.iloc[max(0, i - 100):i + 1]

            if len(h1_slice) < 50 or len(h4_slice) < 50:
                continue

            signals = {}
            if "trend"            in active_strategies: signals["trend"]            = detect_trend(h4_slice)
            if "liquidity_sweep"  in active_strategies: signals["liquidity_sweep"]  = detect_liquidity_sweep(m15_slice)
            if "quarterly_theory" in active_strategies: signals["quarterly_theory"] = detect_quarterly_theory(h1_slice)
            if "candle_pattern"   in active_strategies: signals["candle_pattern"]   = detect_candle_pattern(m15_slice)
            if "fvg"              in active_strategies: signals["fvg"]              = detect_fvg(m15_slice)

            buy_score  = sum(s["score"] for s in signals.values() if s.get("signal") == "BUY")
            sell_score = sum(s["score"] for s in signals.values() if s.get("signal") == "SELL")

            if buy_score > sell_score and buy_score >= 40:
                direction = "BUY"
            elif sell_score > buy_score and sell_score >= 40:
                direction = "SELL"
            else:
                continue

            if i + 1 >= len(df_m15):
                continue

            entry = df_m15.iloc[i + 1]["open"]

            if direction == "BUY":
                sl = entry - sl_dist
                tp = entry + tp_dist
            else:
                sl = entry + sl_dist
                tp = entry - tp_dist

            outcome = self._simulate_trade(df_m15, i + 1, direction, entry, sl, tp)
            if outcome:
                trades.append({
                    "time":      candle_time.isoformat(),
                    "direction": direction,
                    "entry":     entry,
                    "outcome":   outcome["result"],
                    "pips":      outcome["pips"],
                })

        return self._calculate_stats(trades, symbol, active_strategies)

    def _simulate_trade(self, df, entry_idx, direction, entry, sl, tp, max_candles=50):
        for i in range(entry_idx + 1, min(entry_idx + max_candles, len(df))):
            candle = df.iloc[i]
            if direction == "BUY":
                if candle["low"] <= sl:
                    return {"result": "LOSS", "pips": round((sl - entry) / 0.0001, 1)}
                if candle["high"] >= tp:
                    return {"result": "WIN",  "pips": round((tp - entry) / 0.0001, 1)}
            else:
                if candle["high"] >= sl:
                    return {"result": "LOSS", "pips": round((entry - sl) / 0.0001, 1)}
                if candle["low"] <= tp:
                    return {"result": "WIN",  "pips": round((entry - tp) / 0.0001, 1)}

        last  = df.iloc[min(entry_idx + max_candles - 1, len(df) - 1)]["close"]
        pips  = round((last - entry if direction == "BUY" else entry - last) / 0.0001, 1)
        return {"result": "WIN" if pips > 0 else "LOSS", "pips": pips}

    def _calculate_stats(self, trades, symbol, strategies):
        if len(trades) < MIN_TRADES:
            return {"symbol": symbol, "strategies": strategies,
                    "total_trades": len(trades), "win_rate": 0.0,
                    "profit_factor": 0.0, "total_pips": 0.0,
                    "max_drawdown": 0.0, "expectancy": 0.0,
                    "combined_score": 0.0, "valid": False}

        wins   = [t for t in trades if t["outcome"] == "WIN"]
        losses = [t for t in trades if t["outcome"] == "LOSS"]

        win_rate      = len(wins) / len(trades) * 100
        total_win_pip = sum(t["pips"] for t in wins)
        total_los_pip = abs(sum(t["pips"] for t in losses))
        pf            = total_win_pip / total_los_pip if total_los_pip > 0 else 0
        total_pips    = sum(t["pips"] for t in trades)
        avg_win       = total_win_pip / len(wins) if wins else 0
        avg_loss      = total_los_pip / len(losses) if losses else 0
        expectancy    = (win_rate / 100 * avg_win) - ((1 - win_rate / 100) * avg_loss)

        running = peak = max_dd = 0
        for t in trades:
            running += t["pips"]
            if running > peak: peak = running
            dd = peak - running
            if dd > max_dd: max_dd = dd

        combined_score = (win_rate * 0.4) + (min(pf, 5) * 10 * 0.4) + (min(expectancy, 20) * 0.2)

        return {
            "symbol":         symbol,
            "strategies":     strategies,
            "combo_key":      "+".join(sorted(strategies)),
            "total_trades":   len(trades),
            "win_rate":       round(win_rate, 1),
            "profit_factor":  round(pf, 2),
            "total_pips":     round(total_pips, 1),
            "max_drawdown":   round(max_dd, 1),
            "expectancy":     round(expectancy, 1),
            "combined_score": round(combined_score, 2),
            "valid":          True,
        }

    def run(self) -> dict:
        all_strategies = ["trend", "liquidity_sweep", "quarterly_theory", "candle_pattern", "fvg"]
        combos         = []
        for r in range(2, len(all_strategies) + 1):
            combos.extend(combinations(all_strategies, r))

        all_results = []

        logger.info(f"Running {len(combos)} combos across {len(self.pairs)} pairs")

        for symbol in self.pairs:
            logger.info(f"\nLoading {symbol}...")
            df_m15 = self.load_data(symbol, "M15")
            df_h1  = self.load_data(symbol, "H1")
            df_h4  = self.load_data(symbol, "H4")

            if any(df is None for df in [df_m15, df_h1, df_h4]):
                logger.warning(f"{symbol}: Data load failed - skipping")
                continue

            logger.info(f"{symbol}: {len(df_m15)} candles | testing {len(combos)} combos...")

            for i, combo in enumerate(combos):
                result = self.backtest_combo(symbol, df_m15, df_h1, df_h4, list(combo))
                if result["valid"]:
                    all_results.append(result)
                if (i + 1) % 5 == 0:
                    logger.info(f"  {symbol}: {i+1}/{len(combos)} done...")

        all_results.sort(key=lambda x: x["combined_score"], reverse=True)

        best_per_symbol = {}
        for r in all_results:
            if r["symbol"] not in best_per_symbol:
                best_per_symbol[r["symbol"]] = r

        logger.info(f"\nDone. {len(all_results)} valid results.")

        return {
            "all_results":     all_results,
            "best_per_symbol": best_per_symbol,
            "top_10_overall":  all_results[:10],
            "total_combos":    len(combos),
            "pairs_tested":    len(self.pairs),
            "run_date":        datetime.now(timezone.utc).isoformat(),
        }