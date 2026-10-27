# forex_bot

MT5/FTMO bot (Windows only): H4 trend + H1 FVG + quarterly-theory clock + M15 sweep + M5 candle, confluence score,
3R target, structure SL. `main.py` is the orchestrator and runs one cycle per M5 close.
`research/` is a read-only-to-live-code backtest + Monte Carlo that replays the live logic.

## Commands

There is no build step; this is plain Python 3.10+.

```bash
pip install -r requirements.txt                 # install dependencies
python main.py                                  # run the live bot (connects to MT5 and places orders)
python validate.py                              # print what the bot detects per pair, for comparison with TradingView

python -m research.fetch_data                   # read-only history pull from the logged-in MT5 terminal (appends)
python -m research.data_tools probe             # show the terminal's bar cap and broker history depth
python -m research.run --tag NAME               # faithful backtest + Monte Carlo -> research/output/NAME/
python -m research.run --tag NAME --no-mc       # backtest only (fast, ~10 s)
python -m research.nulltest --seeds 30          # does signal direction beat random?
```

## Tests

```bash
python -m pytest research/tests -q              # full suite (~2 min, 170+ tests); MT5 is mocked, no terminal needed
python -m pytest research/tests/test_live_friday_flatten.py -q   # live-bot change tests only
```

Regression check after touching `sim.py`, `signals.py` or any live sizing/risk code: `python -m research.run --tag check --no-mc`
must still give the base result in `research/output/v1/base_faithful/summary.json` (825 trades, expectancy -0.205 R).
That baseline was made on the old 16-month cache; the cache now starts 2021-02, so on the current data compare with
`python -m research.run --tag check --no-mc --start 2025-05-23` (about 858 trades, -0.207 R, a close match, not exact).
Delete your `check` output folder afterwards.

## Code quality rules (strict)

1. **Never test, run or "try out" live-trading paths.** Do not call `python main.py`, `connector.connect()`,
   `order_send`, `place_*_order`, `close_position` or `cancel_pending_order` against a real terminal to verify a change.
   Test with mocks (`unittest.mock`, see `research/tests/test_live_friday_flatten.py`). Any change to live order, sizing or
   risk code needs a test that fails without the change, and the full suite must pass before it is called done.
2. **No look-ahead and no untested rule changes.** Anything that decides a trade at time T may use only bars closed at T
   (`research/tests/test_fidelity.py::test_no_lookahead` must keep passing). A strategy or risk-rule change must be
   pre-specified, developed on earlier data and checked once on a later holdout (compare against the random-direction
   null in `research/nulltest.py`); never pick parameters by looking at the holdout, and report negative results as they are.
3. **Secrets and data hygiene.** Never print, log, commit or paste MT5 credentials or API keys (`config.py` currently holds
   a password - move secrets to environment variables, do not copy them anywhere). Keep all data, caches, outputs and test temp
   files on A: (`research/data`, `research/output`, `research/.pytest_tmp`), never on C:. Live code
   (`main.py`, `config.py`, `mt5_connector.py`, `utils/`, `strategies/`, `agents/`) is edited only for the specific
   change requested, and must never import from `research/`.

## Layout notes

- Live logic: `main.py`, `strategies/smc_strategies.py`, `utils/{risk_manager,order_manager,regime_detector,correlation_filter,pre_trade_validator}.py`.
- Live settings that matter: `FRIDAY_FLATTEN_UTC` (20.0, `config.py`) and `FIXED_RISK_PCT` (0.25, `utils/risk_manager.py`).
- The old `backtest/` package is not trustworthy; use `research/` (see `research/README.md` and `research/RESULTS.md`).
- Review notes with unapplied live-bot fixes: `REVIEW_2026-10-21.md`.

## Working loop

- Start of a task: read the top of `progress.md` for where things stand.
- New strategy idea or risk-rule change: follow the `test-strategy-idea` skill.
- Before calling a change to live code, `research/sim.py` or `research/signals.py` done: run the `reviewer` subagent
  and fix everything it lists until it returns PASS. Stop and ask the user after 3 FAIL rounds.
- End of a task: add an entry to the top of `progress.md` (what changed, verdict, what's open).
