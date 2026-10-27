"""
Forex Bot Configuration
Centralised settings for all agents and modules.
"""

import os

# ── MT5 Connection ─────────────────────────────────────────────────────────────
# By default the bot attaches to whichever account the MT5 terminal is logged into; it never logs in itself.
# To force a specific account, set BOTH environment variables (credentials are never stored in this file):
#   setx FOREX_MT5_LOGIN "<account number>"     setx FOREX_MT5_PASSWORD "<password>"
MT5_CONFIG = {
    "login": int(os.environ["FOREX_MT5_LOGIN"]) if os.environ.get("FOREX_MT5_LOGIN") else None,
    "password": os.environ.get("FOREX_MT5_PASSWORD", ""),
    "server": "FTMO-Demo",
    "timeout": 10000,
}

# The bot refuses any account that is not a demo account. To trade a real or contest account you must set,
# deliberately and with this exact word:  setx FOREX_ALLOW_LIVE "YES"
ALLOW_LIVE_ACCOUNT = os.environ.get("FOREX_ALLOW_LIVE") == "YES"

# ── Trading Pairs ──────────────────────────────────────────────────────────────
PAIRS = [
    "EURUSD", "GBPUSD", "USDJPY",
    "AUDUSD", "USDCAD", "XAUUSD",
]

# ── Timeframes (names; mt5_connector.TF_MAP maps them to MT5 constants) ────────
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

# Weekend gap protection: from this UTC hour on Friday, close every bot position,
# cancel pending orders and place no new orders. None disables it.
FRIDAY_FLATTEN_UTC = 20.0   # 20:00 UTC
BOT_MAGIC          = 20250101   # magic number on every order the bot places; the flatten only touches these

# ── Risk Management ────────────────────────────────────────────────────────────
# Position risk (fixed 0.25%), R:R 3 and the 3% daily limit live in utils/risk_manager.py.
RISK = {
    "max_open_trades":    3,
}

# ── Entry threshold ────────────────────────────────────────────────────────────
# Also needs 4 of the 5 checklist items (see strategies/smc_strategies.py); the point values live there.
MIN_SCORE_TO_TRADE = 50   # Minimum confluence % to execute

# ── News / No-Trade Windows ────────────────────────────────────────────────────
NEWS = {
    "no_trade_before_mins": 15,   # Block X mins before high-impact news
    "no_trade_after_mins":  15,   # Block X mins after high-impact news
    "impact_filter":        ["High"],   # Impact levels to block on
    # Calendar cache / fail-closed (agents/news_agent.py)
    "fail_closed":          True,       # No usable calendar -> block ALL new entries (False = trade without a news filter)
    "cache_file":           "news_cache.json",   # Last good calendar, in the project folder
    "cache_max_age_hours":  24,         # Oldest cached calendar still trusted when every source fails
    "refresh_mins":         60,         # Refetch a good calendar at most this often
    "retry_mins":           10,         # After a failed round, wait this long before trying the sources again
    "rate_limit_backoff_mins": 60,      # Leave a source alone this long after HTTP 429 (Retry-After if longer)
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
