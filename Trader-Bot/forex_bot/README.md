# Forex Bot — Setup & Run Guide

## Prerequisites
- Windows PC (MT5 Python library is Windows only)
- MetaTrader 5 installed and logged into your FTMO demo account
- Python 3.10+
- Anthropic API key

## Installation

```bash
pip install -r requirements.txt
```

## Configuration

Open `config.py` and fill in:

```python
MT5_CONFIG = {
    "login":    12345678,          # Your FTMO account number
    "password": "your_password",
    "server":   "FTMO-Demo2",      # Check your MT5 server name
}
```

Set your Anthropic API key as an environment variable:
```bash
# Windows
set ANTHROPIC_API_KEY=sk-ant-...

# Or add to a .env file (then pip install python-dotenv and load it in config.py)
```

## Running the Bot

```bash
python main.py
```

The bot will:
1. Connect to MT5
2. Create the Excel dashboard (forex_bot_dashboard.xlsx)
3. Run analysis every 15 minutes across all configured pairs
4. Log all signals and trades to Excel automatically

## Excel Dashboard

Open `forex_bot_dashboard.xlsx` while the bot runs to monitor:

| Sheet | Purpose |
|-------|---------|
| **Config** | Adjust pairs, risk settings, thresholds |
| **Signals** | Live strategy signals with probability scores |
| **Trade Log** | Every trade with entry/SL/TP/P&L |
| **Dashboard** | Account balance, equity, performance stats |
| **News** | Upcoming high-impact economic events |

## Architecture

```
main.py (Orchestrator — runs every 15 mins)
│
├── mt5_connector.py       — MT5 connection, OHLCV data, order execution
│
├── strategies/
│   └── smc_strategies.py  — All 5 SMC strategies + confluence scorer
│       ├── Liquidity Sweeps    (25 pts)
│       ├── Quarterly Theory    (20 pts)
│       ├── Candle Patterns     (20 pts)
│       ├── Trend Detection     (20 pts)
│       └── Fair Value Gaps     (15 pts)
│
├── agents/
│   ├── news_agent.py      — ForexFactory calendar, news window blocking
│   └── llm_agent.py       — Claude AI decision maker
│
└── utils/
    └── excel_manager.py   — All Excel read/write operations
```

## Strategy Scoring

Trades only execute when ALL conditions are met:
- ✅ Confluence probability ≥ 65%
- ✅ LLM confidence ≥ 60%
- ✅ No high-impact news within 15 mins
- ✅ Max open positions not reached
- ✅ Daily loss limit not hit
- ✅ R:R ratio ≥ 2.0

## FTMO Notes

- FTMO demo accounts run on MT5
- Check your exact server name in MT5 → File → Login
- Daily loss limit is 5% — the bot respects this automatically
- Start with 0.01 lots to test execution before scaling

## News Source

The bot fetches the ForexFactory economic calendar. If blocked,
alternative sources can be configured in `config.py` under `NEWS["calendar_url"]`.
Free alternatives: investing.com calendar, myfxbook calendar API.
