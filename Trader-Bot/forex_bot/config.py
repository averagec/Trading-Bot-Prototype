"""
Forex Bot Configuration
Centralised settings for all agents and modules.
"""

# ── MT5 Connection ─────────────────────────────────────────────────────────────
MT5_CONFIG = {
    "login": 1513647939,               # Your FTMO demo account number
    "password": "3GA49eL?!$jRy*",           # Your MT5 password
    "server": "FTMO-Demo",             # e.g. "FTMO-Demo"
    "timeout": 10000,
}

# ── Trading Pairs ──────────────────────────────────────────────────────────────
PAIRS = [
    "EURUSD", "GBPUSD", "USDJPY",
    "AUDUSD", "USDCAD", "XAUUSD",
]

# ── Timeframes (MT5 constants mapped by name) ──────────────────────────────────
TIMEFRAMES = {
    "M5":  5,
    "M15": 15,
    "H1":  60,
    "H4":  240,
    "D1":  1440,
}

TRIGGER_TF   = "M5"    # Precise entry trigger
PRIMARY_TF   = "M15"   # Entry timeframe
CONFIRM_TF   = "H1"    # Mid confirmation
BIAS_TF      = "H4"    # Trend/bias timeframe

# Breakeven settings
BREAKEVEN = {
    "enabled":        True,
    "trigger_rr":     1.0,   # Move SL to breakeven when trade is up 1:1
    "buffer_pips":    2,     # Keep SL 2 pips beyond entry (not exactly at entry)
}

# ── Risk Management ────────────────────────────────────────────────────────────
RISK = {
    "risk_per_trade_pct": 1.0,      # % of account per trade
    "max_open_trades":    3,
    "max_daily_loss_pct": 5.0,      # FTMO daily loss limit buffer
    "min_rr_ratio":       2.0,      # Minimum reward:risk
}

# ── Strategy Confluence Scoring ────────────────────────────────────────────────
SCORE_WEIGHTS = {
    "liquidity_sweep": 25,
    "quarterly_theory": 20,
    "candle_pattern":  20,
    "trend_alignment": 20,
    "fvg":             15,
}

MIN_SCORE_TO_TRADE = 50   # Minimum confluence % to execute

# ── News / No-Trade Windows ────────────────────────────────────────────────────
NEWS = {
    "no_trade_before_mins": 15,   # Block X mins before high-impact news
    "no_trade_after_mins":  15,   # Block X mins after high-impact news
    "impact_filter":        ["High"],   # Impact levels to block on
    "calendar_url": "https://economic-calendar.tradingview.com/events",
}

# ── LLM (Claude) ───────────────────────────────────────────────────────────────
LLM = {
    "model":      "claude-sonnet-4-20250514",
    "max_tokens": 1000,
}

# ── Excel Workbook ─────────────────────────────────────────────────────────────
EXCEL = {
    "path": "forex_bot_dashboard.xlsx",
    "sheets": {
        "config":     "Config",
        "signals":    "Signals",
        "trade_log":  "Trade Log",
        "dashboard":  "Dashboard",
        "news":       "News",
    }
}

# ── Candle History to Fetch ────────────────────────────────────────────────────
CANDLE_COUNT = 500   # How many candles to pull per timeframe per pair
