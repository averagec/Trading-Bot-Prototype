"""
Validation Script
Run this to see exactly what the bot detects on each pair.
Compare output against TradingView to verify accuracy.

Usage:
    python validate.py
"""

import sys
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).parent))

from mt5_connector import connector
from strategies.smc_strategies import (
    detect_trend_h4, detect_fvg_h1,
    detect_quarterly_theory_h1, detect_sweep_m15,
    detect_entry_candle_m5, get_pip_size
)
from config import PAIRS


def validate_pair(symbol: str):
    print(f"\n{'='*60}")
    print(f"  {symbol}")
    print(f"{'='*60}")

    df_m5  = connector.get_ohlcv(symbol, "M5",  count=100)
    df_m15 = connector.get_ohlcv(symbol, "M15", count=200)
    df_h1  = connector.get_ohlcv(symbol, "H1",  count=300)
    df_h4  = connector.get_ohlcv(symbol, "H4",  count=300)

    if any(df is None for df in [df_m5, df_m15, df_h1, df_h4]):
        print("  ERROR: Failed to fetch data")
        return

    pip_size = get_pip_size(df_h4)
    price    = df_m15.iloc[-1]["close"]
    print(f"  Current price: {price:.5f}")
    print(f"  Pip size:      {pip_size}")

    # Step 1: H4 Trend
    print(f"\n  [H4 TREND]")
    trend = detect_trend_h4(df_h4)
    print(f"  Signal:    {trend['signal']}")
    print(f"  Trend:     {trend['trend']}")
    print(f"  Strong:    {trend['strong']}")
    print(f"  Reason:    {trend['reason']}")

    direction = trend["signal"]
    if direction == "NONE":
        print(f"\n  STOP: No H4 trend — all lower TF checks skipped")
        print(f"\n  EMA DEBUG:")
        close  = df_h4["close"]
        ema20  = close.ewm(span=20,  adjust=False).mean().iloc[-1]
        ema50  = close.ewm(span=50,  adjust=False).mean().iloc[-1]
        ema200 = close.ewm(span=200, adjust=False).mean().iloc[-1]
        print(f"  Price:  {price:.5f}")
        print(f"  EMA20:  {ema20:.5f}")
        print(f"  EMA50:  {ema50:.5f}")
        print(f"  EMA200: {ema200:.5f}")
        print(f"  EMA bull: {ema20 > ema50 > ema200 and price > ema50}")
        print(f"  EMA bear: {ema20 < ema50 < ema200 and price < ema50}")

        recent = df_h4.iloc[-50:]
        mid    = len(recent) // 2
        hh = recent["high"].iloc[-1] > recent["high"].iloc[:mid].max()
        hl = recent["low"].iloc[-1]  > recent["low"].iloc[:mid].min()
        lh = recent["high"].iloc[-1] < recent["high"].iloc[:mid].max()
        ll = recent["low"].iloc[-1]  < recent["low"].iloc[:mid].min()
        print(f"  HH: {hh} | HL: {hl} | LH: {lh} | LL: {ll}")
        print(f"  Structure bull: {hh and hl} | Structure bear: {lh and ll}")
        return

    # Step 2: H1 FVG
    print(f"\n  [H1 FVG] (looking for {direction} FVGs)")
    fvg = detect_fvg_h1(df_h1, direction)
    print(f"  Signal:    {fvg['signal']}")
    print(f"  FVG Type:  {fvg.get('fvg_type', 'None')}")
    print(f"  Reason:    {fvg['reason']}")
    if fvg.get("fvg_zone"):
        print(f"  Zone:      {fvg['fvg_zone'][0]:.5f} - {fvg['fvg_zone'][1]:.5f}")
        print(f"  Midpoint:  {fvg.get('fvg_mid', 0):.5f}")

    # Manual FVG scan
    print(f"\n  [FVG MANUAL SCAN - last 50 H1 candles]")
    lookback = min(len(df_h1), 50)
    min_gap  = pip_size * 5
    fvg_count = 0
    for i in range(2, lookback):
        c1 = df_h1.iloc[-lookback + i - 2]
        c2 = df_h1.iloc[-lookback + i - 1]
        c3 = df_h1.iloc[-lookback + i]
        if c3["low"] > c1["high"] and (c3["low"] - c1["high"]) >= min_gap:
            gap_pips = (c3["low"] - c1["high"]) / pip_size
            inside   = c1["high"] <= price <= c3["low"]
            print(f"  BULL FVG: {c1['high']:.5f}-{c3['low']:.5f} | {gap_pips:.1f} pips | {'PRICE INSIDE' if inside else 'price outside'}")
            fvg_count += 1
        if c3["high"] < c1["low"] and (c1["low"] - c3["high"]) >= min_gap:
            gap_pips = (c1["low"] - c3["high"]) / pip_size
            inside   = c3["high"] <= price <= c1["low"]
            print(f"  BEAR FVG: {c3['high']:.5f}-{c1['low']:.5f} | {gap_pips:.1f} pips | {'PRICE INSIDE' if inside else 'price outside'}")
            fvg_count += 1
    if fvg_count == 0:
        print(f"  No FVGs found (min gap: {min_gap:.5f} = {min_gap/pip_size:.1f} pips)")

    # Step 3: QT
    print(f"\n  [H1 QUARTERLY THEORY]")
    qt = detect_quarterly_theory_h1(df_h1)
    print(f"  Signal:  {qt['signal']}")
    print(f"  Phase:   {qt['phase']}")
    print(f"  Block:   {qt.get('block', False)}")
    print(f"  Reason:  {qt['reason']}")

    # Step 4: M15 Sweep
    print(f"\n  [M15 SWEEP] (looking for {direction} sweep)")
    h1_level = fvg.get("fvg_mid")
    sweep    = detect_sweep_m15(df_m15, direction, h1_level)
    print(f"  Signal:  {sweep['signal']}")
    print(f"  Reason:  {sweep['reason']}")

    # Step 5: M5 Candle
    print(f"\n  [M5 CANDLE] (looking for {direction} pattern)")
    candle = detect_entry_candle_m5(df_m5, direction)
    print(f"  Signal:  {candle['signal']}")
    print(f"  Pattern: {candle.get('pattern', 'None')}")
    print(f"  Reason:  {candle['reason']}")

    # Summary
    print(f"\n  [CHECKLIST SUMMARY]")
    checks = {
        "h4_trend_exists":    trend["signal"] in ("BUY", "SELL"),
        "h1_level_present":   fvg["signal"] in ("BUY", "SELL"),
        "qt_not_blocking":    not qt.get("block", False),
        "m15_sweep_present":  sweep["signal"] == direction,
        "m5_entry_confirmed": candle["signal"] == direction,
    }
    for check, passed in checks.items():
        print(f"  {'OK' if passed else 'FAIL'} {check}")
    print(f"\n  Score: {sum(checks.values())}/5")


def main():
    print("Connecting to MT5...")
    connected = connector.connect()
    if not connected:
        print("MT5 not connected — running in simulation mode")

    print(f"Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")

    for symbol in PAIRS:
        validate_pair(symbol)

    connector.disconnect()
    print("\nValidation complete.")


if __name__ == "__main__":
    main()