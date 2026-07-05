"""
Regime Detector
Identifies the current market regime for each pair before trading.

Regimes:
- TRENDING: Clear directional movement - trade normally
- RANGING:  Price oscillating between levels - skip
- VOLATILE: ATR spiking, unpredictable - skip

Uses:
- ADX (Average Directional Index) for trend strength
- ATR ratio for volatility detection
- EMA alignment for directional bias
- Structure analysis for ranging detection
"""

import logging
import numpy as np
import pandas as pd
from typing import Optional

logger = logging.getLogger(__name__)

# ── Thresholds ─────────────────────────────────────────────────────────────────
ADX_TREND_THRESHOLD   = 25    # ADX above this = trending
ADX_RANGE_THRESHOLD   = 20    # ADX below this = ranging
ATR_VOLATILE_MULT     = 2.0   # ATR > 2x average = volatile
ATR_PERIOD            = 14    # ATR calculation period
ADX_PERIOD            = 14    # ADX calculation period
EMA_FAST              = 20
EMA_SLOW              = 50


class RegimeDetector:

    def detect(self, df: pd.DataFrame, symbol: str) -> dict:
        """
        Main entry point — detects regime for a given OHLCV dataframe.
        Returns regime dict with type, confidence, and trading recommendation.
        """
        if len(df) < ADX_PERIOD * 3:
            return self._unknown()

        atr        = self._calculate_atr(df)
        adx, pdi, ndi = self._calculate_adx(df)
        avg_atr    = self._average_atr(df)
        ema_fast   = df["close"].ewm(span=EMA_FAST, adjust=False).mean()
        ema_slow   = df["close"].ewm(span=EMA_SLOW, adjust=False).mean()

        current_atr  = atr.iloc[-1]
        current_adx  = adx.iloc[-1]
        current_pdi  = pdi.iloc[-1]
        current_ndi  = ndi.iloc[-1]
        current_ema_f = ema_fast.iloc[-1]
        current_ema_s = ema_slow.iloc[-1]
        current_price = df["close"].iloc[-1]

        # ── Volatile check (highest priority) ─────────────────────────────────
        if avg_atr > 0 and current_atr > avg_atr * ATR_VOLATILE_MULT:
            volatility_ratio = round(current_atr / avg_atr, 2)
            return {
                "regime":      "VOLATILE",
                "action":      "SKIP",
                "confidence":  min(100, int(volatility_ratio * 40)),
                "adx":         round(current_adx, 1),
                "atr":         round(current_atr, 5),
                "atr_ratio":   volatility_ratio,
                "reason":      f"ATR {volatility_ratio}x above average - market too volatile",
                "can_trade":   False,
                "size_factor": 0.0,
            }

        # ── Trending check ─────────────────────────────────────────────────────
        ema_bullish = current_ema_f > current_ema_s and current_price > current_ema_f
        ema_bearish = current_ema_f < current_ema_s and current_price < current_ema_f
        ema_aligned = ema_bullish or ema_bearish

        if current_adx >= ADX_TREND_THRESHOLD and ema_aligned:
            direction  = "BUY" if ema_bullish else "SELL"
            confidence = min(100, int(current_adx * 2.5))
            return {
                "regime":      "TRENDING",
                "action":      "TRADE",
                "direction":   direction,
                "confidence":  confidence,
                "adx":         round(current_adx, 1),
                "atr":         round(current_atr, 5),
                "atr_ratio":   round(current_atr / avg_atr, 2) if avg_atr > 0 else 1.0,
                "reason":      f"ADX {current_adx:.1f} - strong {'uptrend' if ema_bullish else 'downtrend'}",
                "can_trade":   True,
                "size_factor": 1.0,   # Full position size
            }

        # Weak trend — trade but reduce size
        if current_adx >= ADX_TREND_THRESHOLD and not ema_aligned:
            return {
                "regime":      "TRENDING",
                "action":      "TRADE_REDUCED",
                "confidence":  int(current_adx * 1.5),
                "adx":         round(current_adx, 1),
                "atr":         round(current_atr, 5),
                "atr_ratio":   round(current_atr / avg_atr, 2) if avg_atr > 0 else 1.0,
                "reason":      f"ADX {current_adx:.1f} trending but EMA not aligned - reduce size",
                "can_trade":   True,
                "size_factor": 0.5,   # Half position size
            }

        # ── Ranging check ──────────────────────────────────────────────────────
        if current_adx < ADX_RANGE_THRESHOLD:
            return {
                "regime":      "RANGING",
                "action":      "SKIP",
                "confidence":  int((ADX_RANGE_THRESHOLD - current_adx) * 5),
                "adx":         round(current_adx, 1),
                "atr":         round(current_atr, 5),
                "atr_ratio":   round(current_atr / avg_atr, 2) if avg_atr > 0 else 1.0,
                "reason":      f"ADX {current_adx:.1f} - market ranging, no clear direction",
                "can_trade":   False,
                "size_factor": 0.0,
            }

        # ── Transitioning (between ranging and trending) ───────────────────────
        return {
            "regime":      "TRANSITIONING",
            "action":      "TRADE_REDUCED",
            "confidence":  int(current_adx * 2),
            "adx":         round(current_adx, 1),
            "atr":         round(current_atr, 5),
            "atr_ratio":   round(current_atr / avg_atr, 2) if avg_atr > 0 else 1.0,
            "reason":      f"ADX {current_adx:.1f} - market transitioning, proceed with caution",
            "can_trade":   True,
            "size_factor": 0.5,
        }

    # ── Indicators ─────────────────────────────────────────────────────────────

    def _calculate_atr(self, df: pd.DataFrame) -> pd.Series:
        """Calculate Average True Range."""
        high  = df["high"]
        low   = df["low"]
        close = df["close"]
        tr1   = high - low
        tr2   = abs(high - close.shift(1))
        tr3   = abs(low  - close.shift(1))
        tr    = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        return tr.rolling(ATR_PERIOD).mean()

    def _average_atr(self, df: pd.DataFrame, lookback: int = 50) -> float:
        """Calculate the average ATR over a longer lookback for comparison."""
        atr = self._calculate_atr(df)
        recent = atr.iloc[-lookback:].dropna()
        return float(recent.mean()) if len(recent) > 0 else 0.0

    def _calculate_adx(self, df: pd.DataFrame):
        """
        Calculate ADX, +DI and -DI.
        ADX measures trend strength (not direction).
        +DI vs -DI gives direction.
        """
        high  = df["high"]
        low   = df["low"]
        close = df["close"]

        # True Range
        tr1 = high - low
        tr2 = abs(high - close.shift(1))
        tr3 = abs(low  - close.shift(1))
        tr  = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

        # Directional movement
        up_move   = high - high.shift(1)
        down_move = low.shift(1) - low

        pos_dm = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
        neg_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)

        # Smoothed
        atr_s   = tr.rolling(ADX_PERIOD).mean()
        pdi_s   = (pos_dm.rolling(ADX_PERIOD).mean() / atr_s) * 100
        ndi_s   = (neg_dm.rolling(ADX_PERIOD).mean() / atr_s) * 100

        # ADX
        dx      = abs(pdi_s - ndi_s) / (pdi_s + ndi_s + 1e-8) * 100
        adx     = dx.rolling(ADX_PERIOD).mean()

        return adx, pdi_s, ndi_s

    def _unknown(self) -> dict:
        return {
            "regime":      "UNKNOWN",
            "action":      "SKIP",
            "confidence":  0,
            "adx":         0,
            "atr":         0,
            "atr_ratio":   1.0,
            "reason":      "Insufficient data for regime detection",
            "can_trade":   False,
            "size_factor": 0.0,
        }

    def get_summary(self, symbol: str, df_h4: pd.DataFrame, df_h1: pd.DataFrame) -> dict:
        """
        Get regime on both H4 (bias) and H1 (confirmation).
        Both must allow trading for the bot to proceed.
        H4 regime takes priority.
        """
        h4_regime = self.detect(df_h4, symbol)
        h1_regime = self.detect(df_h1, symbol)

        # H4 is the master — if H4 says skip, we skip regardless
        if not h4_regime["can_trade"]:
            return {
                "can_trade":   False,
                "h4_regime":   h4_regime["regime"],
                "h1_regime":   h1_regime["regime"],
                "size_factor": 0.0,
                "reason":      f"H4 {h4_regime['regime']}: {h4_regime['reason']}",
            }

        # If H4 says trade but H1 says skip — reduce size
        if not h1_regime["can_trade"]:
            return {
                "can_trade":   True,
                "h4_regime":   h4_regime["regime"],
                "h1_regime":   h1_regime["regime"],
                "size_factor": 0.25,   # Quarter size — H4 trend but H1 ranging
                "reason":      f"H4 trending but H1 {h1_regime['regime']} - reduced size",
            }

        # Both allow trading — use lowest size factor
        size_factor = min(h4_regime["size_factor"], h1_regime["size_factor"])
        return {
            "can_trade":   True,
            "h4_regime":   h4_regime["regime"],
            "h1_regime":   h1_regime["regime"],
            "size_factor": size_factor,
            "adx_h4":      h4_regime["adx"],
            "adx_h1":      h1_regime["adx"],
            "reason":      f"H4: {h4_regime['regime']} | H1: {h1_regime['regime']}",
        }


# Singleton
regime_detector = RegimeDetector()