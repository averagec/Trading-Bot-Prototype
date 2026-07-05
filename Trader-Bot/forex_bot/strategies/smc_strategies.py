"""
Strategy Engine — Timeframe Hierarchy Model

Each strategy belongs to a specific timeframe:
  H4  → Trend (bias only)
  H1  → FVG + Structure levels (where to trade)
  M15 → Liquidity Sweep (setup forming at H1 level)
  H1  → Quarterly Theory (session filter)
  M5  → Candle Pattern (entry trigger)

Checklist (top down — all must pass):
  1. H4 trend exists and is not ranging
  2. H1 has a key level (FVG or structure)
  3. M15 sweep at or near the H1 level
  4. QT session allows trading
  5. M5 candle confirms direction
"""

import pandas as pd
import numpy as np
from datetime import datetime, timezone
from typing import Optional


# ══════════════════════════════════════════════════════════════════════════════
# H4 — TREND (Bias only)
# ══════════════════════════════════════════════════════════════════════════════
def get_pip_size(df: pd.DataFrame) -> float:
    """
    Dynamically determine pip size based on price level.
    JPY pairs trade above 50 — pip = 0.01
    Gold (XAUUSD) trades above 500 — pip = 0.1
    All others — pip = 0.0001
    """
    price = df["close"].iloc[-1]
    if price > 500:
        return 0.1      # XAUUSD
    elif price > 50:
        return 0.01     # JPY pairs
    else:
        return 0.0001   # Standard forex pairs

def get_min_gap(df: pd.DataFrame, pips: float = 5.0) -> float:
    """Return minimum FVG gap size in price terms for this pair."""
    return get_pip_size(df) * pips

def detect_trend_h4(df: pd.DataFrame) -> dict:
    if len(df) < 200:
        return {"signal": "NONE", "score": 0, "trend": "Unknown",
                "reason": "Insufficient H4 data", "strong": False}

    close  = df["close"]
    ema20  = close.ewm(span=20,  adjust=False).mean()
    ema50  = close.ewm(span=50,  adjust=False).mean()
    ema200 = close.ewm(span=200, adjust=False).mean()

    e20, e50, e200 = ema20.iloc[-1], ema50.iloc[-1], ema200.iloc[-1]
    price          = close.iloc[-1]

    recent = df.iloc[-50:]
    mid    = len(recent) // 2

    hh = recent["high"].iloc[-1] > recent["high"].iloc[:mid].max()
    hl = recent["low"].iloc[-1]  > recent["low"].iloc[:mid].min()
    lh = recent["high"].iloc[-1] < recent["high"].iloc[:mid].max()
    ll = recent["low"].iloc[-1]  < recent["low"].iloc[:mid].min()

    structure_bull = hh and hl
    structure_bear = lh and ll
    ema_bull       = e20 > e50 > e200 and price > e50
    ema_bear       = e20 < e50 < e200 and price < e50

    if structure_bull and ema_bull:
        return {"signal": "BUY", "score": 30, "trend": "Strong Uptrend",
                "reason": f"H4: HH/HL + bullish EMA stack ({price:.5f} > EMA200 {e200:.5f})",
                "strong": True}

    if structure_bear and ema_bear:
        return {"signal": "SELL", "score": 30, "trend": "Strong Downtrend",
                "reason": f"H4: LH/LL + bearish EMA stack ({price:.5f} < EMA200 {e200:.5f})",
                "strong": True}

    if structure_bull or ema_bull:
        return {"signal": "BUY", "score": 15, "trend": "Weak Uptrend",
                "reason": "H4: Partial bullish bias — structure or EMA only",
                "strong": False}

    if structure_bear or ema_bear:
        return {"signal": "SELL", "score": 15, "trend": "Weak Downtrend",
                "reason": "H4: Partial bearish bias — structure or EMA only",
                "strong": False}

    # Short term bias — price above/below EMA20 and EMA20/EMA50 aligned
    if price > e50 and price > e20:
        return {"signal": "BUY", "score": 10, "trend": "Short Term Uptrend",
                "reason": f"H4: Price {price:.5f} > EMA20 {e20:.5f} > EMA50 {e50:.5f}",
                "strong": False}

    if price < e20 and e20 < e50:
        return {"signal": "SELL", "score": 10, "trend": "Short Term Downtrend",
                "reason": f"H4: Price {price:.5f} < EMA20 {e20:.5f} < EMA50 {e50:.5f}",
                "strong": False}

    return {"signal": "NONE", "score": 0, "trend": "Ranging",
            "reason": "H4: Market ranging — no directional bias", "strong": False}

# ══════════════════════════════════════════════════════════════════════════════
# H1 — FVG (Key levels to trade at)
# ══════════════════════════════════════════════════════════════════════════════

def detect_fvg_h1(df: pd.DataFrame, trend_direction: str,
                  min_gap_pips: float = None) -> dict:
    min_gap_pips = get_min_gap(df, pips=5.0)
    if len(df) < 10:
        print.info(f"FVG debug: bull_count={len(fvgs_bull)} bear_count={len(fvgs_bear)} price={price:.5f} trend={trend_direction}")
        return {"signal": "NONE", "score": 0, "fvg_type": "None",
                "reason": "Insufficient H1 data"}

    fvgs_bull = []
    fvgs_bear = []
    lookback  = min(len(df), 100)

    for i in range(2, lookback):
        c1 = df.iloc[-lookback + i - 2]
        c2 = df.iloc[-lookback + i - 1]
        c3 = df.iloc[-lookback + i]

        if c3["low"] > c1["high"] and (c3["low"] - c1["high"]) >= min_gap_pips:
            fvgs_bull.append({
                "top":    c3["low"],
                "bottom": c1["high"],
                "mid":    (c3["low"] + c1["high"]) / 2,
                "age":    lookback - i,
            })

        if c3["high"] < c1["low"] and (c1["low"] - c3["high"]) >= min_gap_pips:
            fvgs_bear.append({
                "top":    c1["low"],
                "bottom": c3["high"],
                "mid":    (c1["low"] + c3["high"]) / 2,
                "age":    lookback - i,
            })

    price = df.iloc[-1]["close"]

    if trend_direction == "BUY":
        for fvg in reversed(fvgs_bull[-5:]):
            if fvg["bottom"] <= price <= fvg["top"]:
                score = 25 if fvg["age"] <= 20 else 15
                return {
                    "signal":   "BUY",
                    "score":    score,
                    "fvg_type": "Bullish H1 FVG",
                    "fvg_zone": (fvg["bottom"], fvg["top"]),
                    "fvg_mid":  fvg["mid"],
                    "reason":   f"H1: Price at bullish FVG {fvg['bottom']:.5f}-{fvg['top']:.5f} (age: {fvg['age']}h)",
                }
        if fvgs_bull:
            nearest = min(fvgs_bull[-3:], key=lambda x: abs(x["mid"] - price))
            return {
                "signal":   "NONE",
                "score":    5,
                "fvg_type": "Bullish H1 FVG nearby",
                "fvg_zone": (nearest["bottom"], nearest["top"]),
                "fvg_mid":  nearest["mid"],
                "reason":   f"H1: Bullish FVG at {nearest['mid']:.5f} — price not there yet",
            }

    elif trend_direction == "SELL":
        for fvg in reversed(fvgs_bear[-5:]):
            if fvg["bottom"] <= price <= fvg["top"]:
                score = 25 if fvg["age"] <= 20 else 15
                return {
                    "signal":   "SELL",
                    "score":    score,
                    "fvg_type": "Bearish H1 FVG",
                    "fvg_zone": (fvg["bottom"], fvg["top"]),
                    "fvg_mid":  fvg["mid"],
                    "reason":   f"H1: Price at bearish FVG {fvg['bottom']:.5f}-{fvg['top']:.5f} (age: {fvg['age']}h)",
                }
        if fvgs_bear:
            nearest = min(fvgs_bear[-3:], key=lambda x: abs(x["mid"] - price))
            return {
                "signal":   "NONE",
                "score":    5,
                "fvg_type": "Bearish H1 FVG nearby",
                "fvg_zone": (nearest["bottom"], nearest["top"]),
                "fvg_mid":  nearest["mid"],
                "reason":   f"H1: Bearish FVG at {nearest['mid']:.5f} — price not there yet",
            }

    return {"signal": "NONE", "score": 0, "fvg_type": "None",
            "reason": "H1: No FVG aligned with trend direction"}


# ══════════════════════════════════════════════════════════════════════════════
# H1 — QUARTERLY THEORY (Session filter)
# ══════════════════════════════════════════════════════════════════════════════

def detect_quarterly_theory_h1(df: pd.DataFrame) -> dict:
    now  = datetime.now(timezone.utc)
    hour = now.hour

    if 0 <= hour < 6:
        return {
            "signal": "NONE", "score": 0,
            "quarter": 1, "phase": "Accumulation",
            "reason": "H1: Q1 Asian session — no trade",
            "block": True,
        }

    if 6 <= hour < 9:
        return {
            "signal": "WAIT", "score": 0,
            "quarter": 2, "phase": "Manipulation",
            "reason": "H1: Q2 manipulation window (06-09 UTC) — wait for sweep",
            "block": True,
        }

    if 9 <= hour < 12:
        if "time" not in df.columns:
            return {"signal": "NONE", "score": 10, "quarter": 2,
                    "phase": "London", "reason": "H1: London session active", "block": False}

        q2_early = df[df["time"].dt.hour.between(6, 8)]
        if not q2_early.empty:
            q2_high    = q2_early["high"].max()
            q2_low     = q2_early["low"].min()
            last_close = df.iloc[-1]["close"]

            if last_close > q2_high:
                return {"signal": "BUY", "score": 20, "quarter": 2,
                        "phase": "London", "block": False,
                        "reason": f"H1: London expanding above Q2 high ({q2_high:.5f})"}
            if last_close < q2_low:
                return {"signal": "SELL", "score": 20, "quarter": 2,
                        "phase": "London", "block": False,
                        "reason": f"H1: London expanding below Q2 low ({q2_low:.5f})"}

        return {"signal": "NONE", "score": 10, "quarter": 2,
                "phase": "London", "block": False,
                "reason": "H1: London session — no Q2 sweep confirmed yet"}

    if 12 <= hour < 18:
        if "time" not in df.columns:
            return {"signal": "NONE", "score": 15, "quarter": 3,
                    "phase": "New York", "reason": "H1: NY session active", "block": False}

        q2_candles = df[df["time"].dt.hour.between(6, 11)]
        if not q2_candles.empty:
            q2_high    = q2_candles["high"].max()
            q2_low     = q2_candles["low"].min()
            last_close = df.iloc[-1]["close"]

            if last_close > q2_high:
                return {"signal": "BUY", "score": 25, "quarter": 3,
                        "phase": "New York", "block": False,
                        "reason": f"H1: NY Q3 above London high ({q2_high:.5f})"}
            if last_close < q2_low:
                return {"signal": "SELL", "score": 25, "quarter": 3,
                        "phase": "New York", "block": False,
                        "reason": f"H1: NY Q3 below London low ({q2_low:.5f})"}

        return {"signal": "NONE", "score": 15, "quarter": 3,
                "phase": "New York", "block": False,
                "reason": "H1: NY session — awaiting directional confirmation"}

    return {"signal": "NONE", "score": 10, "quarter": 4,
            "phase": "Late", "block": False,
            "reason": "H1: Q4 late session — follow established direction"}


# ══════════════════════════════════════════════════════════════════════════════
# M15 — LIQUIDITY SWEEP (Setup at H1 level)
# ══════════════════════════════════════════════════════════════════════════════

def detect_sweep_m15(df: pd.DataFrame, trend_direction: str,
                     h1_level: Optional[float] = None,
                     lookback: int = 20) -> dict:
    if len(df) < lookback + 2:
        return {"signal": "NONE", "score": 0, "reason": "Insufficient M15 data"}

    recent = df.iloc[-lookback - 1:-1]
    prev   = df.iloc[-2]
    last   = df.iloc[-1]

    swing_high = recent["high"].max()
    swing_low  = recent["low"].min()

    swept_high = (prev["high"] > swing_high) and (last["close"] < swing_high)
    swept_low  = (prev["low"]  < swing_low)  and (last["close"] > swing_low)

    pip_size = get_pip_size(df)

    if swept_high and trend_direction == "SELL":
        wick      = prev["high"] - max(prev["open"], prev["close"])
        body      = abs(prev["close"] - prev["open"])
        confirmed = prev["close"] < prev["open"]
        near_level = False
        if h1_level:
            near_level = abs(swing_high - h1_level) < (20 * pip_size)
        score = 30 if (confirmed and near_level) else (20 if confirmed else 10)
        return {
            "signal":    "SELL",
            "score":     score,
            "level":     swing_high,
            "near_h1":   near_level,
            "confirmed": confirmed,
            "reason":    f"M15: Swept highs at {swing_high:.5f} {'near H1 level ' if near_level else ''}— {'confirmed' if confirmed else 'weak'} rejection",
        }

    if swept_low and trend_direction == "BUY":
        wick      = min(prev["open"], prev["close"]) - prev["low"]
        body      = abs(prev["close"] - prev["open"])
        confirmed = prev["close"] > prev["open"]
        near_level = False
        if h1_level:
            near_level = abs(swing_low - h1_level) < (20 * pip_size)
        score = 30 if (confirmed and near_level) else (20 if confirmed else 10)
        return {
            "signal":    "BUY",
            "score":     score,
            "level":     swing_low,
            "near_h1":   near_level,
            "confirmed": confirmed,
            "reason":    f"M15: Swept lows at {swing_low:.5f} {'near H1 level ' if near_level else ''}— {'confirmed' if confirmed else 'weak'} rejection",
        }

    return {"signal": "NONE", "score": 0,
            "reason": "M15: No sweep in trend direction"}


# ══════════════════════════════════════════════════════════════════════════════
# M5 — CANDLE PATTERN (Entry trigger)
# ══════════════════════════════════════════════════════════════════════════════

def detect_entry_candle_m5(df: pd.DataFrame, trend_direction: str) -> dict:
    if len(df) < 3:
        return {"signal": "NONE", "score": 0, "pattern": "None"}

    c1, c2, c3 = df.iloc[-3], df.iloc[-2], df.iloc[-1]

    def body(c):       return abs(c["close"] - c["open"])
    def is_bull(c):    return c["close"] > c["open"]
    def is_bear(c):    return c["close"] < c["open"]
    def upper_wick(c): return c["high"] - max(c["open"], c["close"])
    def lower_wick(c): return min(c["open"], c["close"]) - c["low"]
    def full_range(c): return c["high"] - c["low"]

    if trend_direction == "BUY":
        if (is_bear(c2) and is_bull(c3) and
                c3["open"] <= c2["close"] and
                c3["close"] >= c2["open"] and
                body(c3) > body(c2)):
            return {"signal": "BUY", "score": 15, "pattern": "Bullish Engulfing",
                    "reason": "M5: Bullish engulfing — entry confirmed"}

        if (full_range(c3) > 0 and
                lower_wick(c3) >= body(c3) * 2.5 and
                upper_wick(c3) <= body(c3) * 0.5 and
                is_bull(c3)):
            return {"signal": "BUY", "score": 12, "pattern": "Bullish Pin Bar",
                    "reason": "M5: Bullish pin bar — rejection of lows"}

        if (is_bear(c1) and body(c2) < body(c1) * 0.3 and
                is_bull(c3) and c3["close"] > (c1["open"] + c1["close"]) / 2):
            return {"signal": "BUY", "score": 15, "pattern": "Morning Star",
                    "reason": "M5: Morning star — bullish reversal"}

    elif trend_direction == "SELL":
        if (is_bull(c2) and is_bear(c3) and
                c3["open"] >= c2["close"] and
                c3["close"] <= c2["open"] and
                body(c3) > body(c2)):
            return {"signal": "SELL", "score": 15, "pattern": "Bearish Engulfing",
                    "reason": "M5: Bearish engulfing — entry confirmed"}

        if (full_range(c3) > 0 and
                upper_wick(c3) >= body(c3) * 2.5 and
                lower_wick(c3) <= body(c3) * 0.5 and
                is_bear(c3)):
            return {"signal": "SELL", "score": 12, "pattern": "Bearish Pin Bar",
                    "reason": "M5: Bearish pin bar — rejection of highs"}

        if (is_bull(c1) and body(c2) < body(c1) * 0.3 and
                is_bear(c3) and c3["close"] < (c1["open"] + c1["close"]) / 2):
            return {"signal": "SELL", "score": 15, "pattern": "Evening Star",
                    "reason": "M5: Evening star — bearish reversal"}

    return {"signal": "NONE", "score": 0, "pattern": "None",
            "reason": "M5: No entry candle pattern"}


# ══════════════════════════════════════════════════════════════════════════════
# CONFLUENCE — TOP DOWN HIERARCHY
# ══════════════════════════════════════════════════════════════════════════════

def run_confluence(df_m15: pd.DataFrame, df_h1: pd.DataFrame,
                   df_h4: pd.DataFrame, df_m5: Optional[pd.DataFrame] = None) -> dict:
    """
    Top-down confluence:
    Step 1: H4 trend sets bias
    Step 2: H1 FVG identifies the level
    Step 3: H1 QT confirms session
    Step 4: M15 sweep confirms setup
    Step 5: M5 candle triggers entry
    """
    # Step 1
    trend     = detect_trend_h4(df_h4)
    direction = trend["signal"]

    if direction == "NONE":
        return _no_trade("H4 ranging — no directional bias", trend, {}, {}, {}, {})

    # Step 2
    fvg      = detect_fvg_h1(df_h1, direction)
    h1_level = fvg.get("fvg_mid")

    # Step 3
    qt = detect_quarterly_theory_h1(df_h1)
    if qt.get("block", False):
        return _no_trade(f"QT blocking: {qt['reason']}", trend, fvg, qt, {}, {})

    # Step 4
    sweep = detect_sweep_m15(df_m15, direction, h1_level)

    # Step 5
    candle = detect_entry_candle_m5(df_m5 if df_m5 is not None else df_m15, direction)

    # Checklist
    checklist = {
        "h4_trend_exists":    trend["signal"] in ("BUY", "SELL"),
        "h1_level_present":   fvg["signal"] in ("BUY", "SELL"),
        "qt_not_blocking":    not qt.get("block", False),
        "m15_sweep_present":  sweep["signal"] == direction,
        "m5_entry_confirmed": candle["signal"] == direction,
    }

    checks_passed = sum(checklist.values())
    checks_failed = [k for k, v in checklist.items() if not v]
    checklist_met = checks_passed >= 4

    signals = {
        "trend":            trend,
        "fvg":              fvg,
        "quarterly_theory": qt,
        "liquidity_sweep":  sweep,
        "candle_pattern":   candle,
    }

    total_score = sum(
        s["score"] for s in signals.values()
        if s.get("signal") == direction
    )
    probability = round(min(total_score, 100), 1)

    return {
        "direction":     direction,
        "probability":   probability,
        "raw_score":     total_score,
        "breakdown":     signals,
        "checklist":     checklist,
        "checklist_met": checklist_met,
        "checks_passed": checks_passed,
        "checks_failed": checks_failed,
        "h1_level":      h1_level,
        "trend_strong":  trend.get("strong", False),
        "timestamp":     datetime.now(timezone.utc).isoformat(),
    }


def _no_trade(reason, trend, fvg, qt, sweep, candle):
    return {
        "direction":     "NONE",
        "probability":   0.0,
        "raw_score":     0,
        "breakdown": {
            "trend":            trend,
            "fvg":              fvg    or {"signal": "NONE", "score": 0, "reason": "Not evaluated"},
            "quarterly_theory": qt     or {"signal": "NONE", "score": 0, "reason": "Not evaluated"},
            "liquidity_sweep":  sweep  or {"signal": "NONE", "score": 0, "reason": "Not evaluated"},
            "candle_pattern":   candle or {"signal": "NONE", "score": 0, "reason": "Not evaluated"},
        },
        "checklist": {
            "h4_trend_exists":    trend.get("signal") in ("BUY", "SELL"),
            "h1_level_present":   False,
            "qt_not_blocking":    not qt.get("block", False) if qt else True,
            "m15_sweep_present":  False,
            "m5_entry_confirmed": False,
        },
        "checklist_met": False,
        "checks_passed": 1,
        "checks_failed": ["h1_level_present", "m15_sweep_present", "m5_entry_confirmed"],
        "h1_level":      None,
        "trend_strong":  False,
        "reason":        reason,
        "timestamp":     datetime.now(timezone.utc).isoformat(),
    }


# Legacy wrappers for backtest compatibility
def detect_liquidity_sweep(df, lookback=20):
    return detect_sweep_m15(df, "BUY", lookback=lookback)

def detect_quarterly_theory(df):
    return detect_quarterly_theory_h1(df)

def detect_candle_pattern(df):
    return detect_entry_candle_m5(df, "BUY")

def detect_trend(df):
    return detect_trend_h4(df)

def detect_fvg(df, min_gap_pips=0.0003):
    return detect_fvg_h1(df, "BUY", min_gap_pips)