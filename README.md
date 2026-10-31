# Forex Bot — MT5/FTMO trading bot with a backtest and test harness

A Windows bot for MetaTrader 5 / FTMO demo accounts, plus a research engine that replays the live strategy logic so it can be
tested honestly. **The strategy itself has not been shown to have an edge, and this repo says so.**

## What this project is

- **The bot** (`main.py`, `strategies/`, `utils/`, `agents/`): once per M5 candle close it checks six pairs (EURUSD, GBPUSD,
  USDJPY, AUDUSD, USDCAD, XAUUSD). The rule set is an H4 EMA trend, an H1 fair-value gap, a quarterly-theory time filter, an M15
  liquidity sweep and an M5 confirmation candle, combined into a confluence score. Trades use a 3R target, a structure-based
  stop, breakeven at 1R, fixed 0.25% risk, a 3% daily loss gate and a news filter. An optional LLM check can only veto a trade.
- **The research engine** (`research/`): a backtest and Monte Carlo that run the same signal code as the live bot, with a
  random-direction null test, spread and no-look-ahead checks, and the original 16-month result kept as a baseline.
- **The safety and test work**: demo-only guard, kill switch, Friday weekend-flatten, fail-closed news and a reviewer-agent gate
  (see `CLAUDE.md` and `progress.md`). MT5 is mocked throughout, so the 430-odd tests need no terminal.

## What I found

| Question | Result |
|---|---|
| Does the strategy make money in the faithful backtest? | No. 825 trades over 16 months: expectancy **-0.205 R** per trade (95% CI -0.31 to -0.10). |
| Is that different from random? | No. With the same entries, stops and costs but random direction, the null averages -0.177 R; the real strategy is statistically indistinguishable from it (p = 0.20). |
| Does a longer history change it? | On the extended 2021-2026 data the default run gives 3,509 trades at -0.116 R. Still negative, still no demonstrated edge. |
| Has it traded live? | No. There is no live track record. |

The value here is the process, not a profitable system: building a backtest that matches the live code, finding and removing
look-ahead and sizing problems, and reporting a negative result instead of tuning until it looks good. Strategy and risk-rule
changes are meant to be pre-specified, developed on earlier data and checked once on a later holdout (`CLAUDE.md`).

See `research/RESULTS.md` for the full results and `REVIEW_2026-10-21.md` for the open review findings. Run the bot on a demo
account only.

---

# Setup & Run Guide

## Prerequisites
- Windows PC (MT5 Python library is Windows only)
- MetaTrader 5 installed and logged into your FTMO demo account
- Python 3.10+
- Optional: an Anthropic API key (only for the LLM veto, see below)

## Installation

```bash
pip install -r requirements.txt
```

## Configuration

No credentials go in `config.py`. Log the MT5 terminal into the account you want to trade (Algo Trading on); the bot
attaches to whichever account the terminal holds and logs it at startup (`Connected to MT5 | Account: ...`).

The bot only trades demo accounts. It logs the account number, server and type (DEMO/CONTEST/REAL) at startup and
exits (code 2) on anything else, and it re-checks the account type every cycle, so switching the terminal to a real
account while the bot runs also stops it (nothing is closed or modified on that account). To trade a real or contest account you must deliberately set `FOREX_ALLOW_LIVE` to `YES`
(`setx FOREX_ALLOW_LIVE "YES"`); no other value counts.

If `ANTHROPIC_API_KEY` is set, an LLM reviews every proposed trade, but only in **shadow mode**: its answer is recorded and
never acted on, so the bot trades exactly what the rules say. To let it veto trades, set `FOREX_LLM_VETO` to `1`.
Every proposed trade, every trade stopped after the entry conditions passed, and every order the broker accepted (with its ticket)
is appended to `logs/candidates.jsonl` for offline evaluation. If the bot stops (kill switch, refused account) while it still has
positions open, it says so in the log and on the dashboard, because those positions then get no breakeven or Friday-flatten management.

To make the bot log in itself instead, set both environment variables (PowerShell, applies to new terminals):

```bash
setx FOREX_MT5_LOGIN "12345678"
setx FOREX_MT5_PASSWORD "your_password"
```

The LLM review is off unless the `ANTHROPIC_API_KEY` environment variable is set (the key is never logged):
```bash
setx ANTHROPIC_API_KEY "sk-ant-..."
```
The model is `LLM["model"]` in `config.py`. The LLM is veto-only: it can turn a trade the checklist proposed into HOLD, it can
never flip a direction or create a trade, and any failure (no key, HTTP error, bad answer) means HOLD.

## Running the Bot

```bash
python main.py
```

The bot will:
1. Connect to MT5 (demo account only)
2. Create the Excel dashboard (forex_bot_dashboard.xlsx) if it does not exist
3. Run one cycle every 5 minutes, synced to the M5 candle close, across all pairs in `config.py`
4. Write signals, trades and account status to Excel (best effort: if the workbook is open in Excel the writes fail with one
   ERROR in the log and the bot carries on)

## Stopping the Bot

| Action | Effect |
|--------|--------|
| Ctrl+C | Clean shutdown at any point, including during the 5-minute sleep. Open positions are left as they are. |
| Create a file named `STOP` in the project folder | At the start of the next cycle the bot logs CRITICAL, disconnects and exits (code 0). It closes and cancels nothing. Delete the file before restarting. |
| `python panic_close.py` | Cancels the bot's pending orders and closes the bot's open positions (magic number only; manual trades are left alone). Refuses non-demo accounts like the bot does. It does not stop the bot: stop it first or create `STOP`. |

Open positions keep their server-side SL/TP when the bot is stopped, but lose breakeven management and the Friday flatten.

## Excel Dashboard

Open `forex_bot_dashboard.xlsx` while the bot runs to monitor (it is a display only; nothing is read back from it):

| Sheet | Purpose |
|-------|---------|
| **Config** | Snapshot of the main settings when the workbook was created (edit `config.py`, not this sheet) |
| **Signals** | Strategy signals with probability scores |
| **Trade Log** | Every trade with entry/SL/TP |
| **Dashboard** | Account balance, equity, bot status |
| **News** | Upcoming high-impact economic events |

## Architecture

```
main.py (Orchestrator — one cycle per M5 candle close, every 5 minutes)
│
├── mt5_connector.py       — MT5 connection, OHLCV data, order execution
├── panic_close.py         — standalone panic button
│
├── strategies/
│   └── smc_strategies.py  — 5 SMC signals + confluence scorer
│       ├── H4 Trend            (up to 30 pts)
│       ├── H1 Fair Value Gaps  (up to 25 pts)
│       ├── Quarterly Theory    (up to 25 pts)
│       ├── M15 Liquidity Sweep (up to 30 pts)
│       └── M5 Candle Pattern   (up to 15 pts)   (score = sum of agreeing signals, capped at 100)
│
├── agents/
│   ├── news_agent.py      — economic calendar, news window blocking
│   └── llm_agent.py       — optional Claude veto
│
└── utils/
    ├── risk_manager.py    — position sizing, SL/TP, daily and monthly limits
    ├── order_manager.py, correlation_filter.py, pre_trade_validator.py, regime_detector.py, pair_observer.py
    ├── flatten.py         — shared by the Friday flatten and panic_close.py
    └── excel_manager.py   — Excel dashboard writes
```

## Strategy Rules (as coded)

A new trade is only placed when ALL of these hold:
- Inside the session window: 07:00-22:00 UTC (London 07-13, New York 13-22)
- Confluence score >= 50 (`MIN_SCORE_TO_TRADE`) and at least 4 of the 5 checklist items pass
- The M5 candle agrees with the direction (or shows no pattern)
- No high-impact news window is active
- Fewer than 3 open positions (`RISK["max_open_trades"]`), no open or pending order on that pair, correlation filter clear
- The risk gate allows trading: daily limit not hit and monthly target not reached
- The account could be read (if MT5 returns no account info the bot places nothing that cycle)
- The LLM, if enabled, does not veto

Sizing and exits (`utils/risk_manager.py`):
- Fixed risk of 0.25% of balance per trade (`FIXED_RISK_PCT`)
- Stop loss at the recent swing high/low (at least 10 pips), take profit at 3R
- Breakeven (SL moved to entry +2 pips) once the trade is up 1R
- Daily limit: no new trades once equity is 3% below the first balance seen that UTC day
- Monthly target: no new trades for the rest of the month once balance is 5% above the month's first balance
- The daily and monthly reference balances are saved per account in `risk_baselines.json`, so a restart does not reset them
- Friday flatten: from 20:00 UTC on Friday the bot closes its positions and cancels its pending orders, and places nothing until Monday
- Pending orders are cancelled after 30 minutes (measured from the broker's order time, so this survives a restart)

## FTMO Notes

- FTMO demo accounts run on MT5
- Check your exact server name in MT5 → File → Login
- FTMO's own daily loss rule differs from the bot's limit above (equity basis and reset time are not the same); check FTMO's current terms
- Start with the demo account and watch the log before trusting anything

## News Source

`agents/news_agent.py` reads an economic calendar and blocks entries around high-impact events. The windows, cache and
fail-closed behaviour are set in `NEWS` in `config.py`.
