# research/ — faithful backtest + Monte Carlo for the live bot

Replays the **live bot's actual decision logic** (not the old `backtest/` package) on historical MT5 bars, then
uses Monte Carlo to quantify luck, drawdown and prop-firm (FTMO-style) pass/fail risk.
Nothing here modifies, or is imported by, the live bot. The live detectors are imported **unmodified**.

## Run

```bash
python -m research.fetch_data                 # read-only history pull from the running, logged-in MT5 terminal
python -m research.run --tag base             # faithful run + Monte Carlo + report
python -m research.run --tag v1 --variants    # + pre-declared sensitivity variants (exploratory, not for picking winners)
python -m research.nulltest --seeds 30        # does signal DIRECTION beat random? (flip-direction null)
python -m research.compare_live               # replay vs. what the live bot logged in the Excel dashboard
python -m pytest research/tests -q            # fidelity + mechanics + Monte Carlo tests
```
Output goes to `research/output/<tag>/<variant>/` (`report.md`, `summary.json`, `trades.csv`, `equity.parquet`, `orders.csv`).

## How it works

| Stage | File | What it does |
|---|---|---|
| Data | `data.py` | MT5 history (closed bars only), broker-time → UTC (NY-close rule, verified against weekend gaps), per-bar spread, symbol specs |
| Phase A | `signals.py` | Per M5 cycle, using only bars closed by then: H4 trend, H1 FVG, quarterly theory (bar-time port), M15 sweep, M5 candle, regime filter, entry type/price, structure SL. Emits candidates that pass every *stateless* entry rule |
| Phase B | `sim.py` | One shared account. Stateful gates + fills: risk gates, max-open, symbol guard/cooldown, correlation filter, sizing, spread validator, pending LIMIT/STOP orders (30 min expiry), bid/ask fills, SL/TP/breakeven |
| Stats | `metrics.py` | R-multiples, expectancy + bootstrap CI, PF, drawdown, breakdowns |
| MC | `montecarlo.py` | iid / shuffle bootstrap, cost & missed-trade stress, FTMO challenge simulation on resampled days (uses real intraday equity lows) |
| Null | `nulltest.py` | Random-direction null on the same trades/sizes/costs |

## Fidelity — what was checked

* `tests/test_fidelity.py`: quarterly-theory port == live function (all 24 hours); confluence composition == live
  `run_confluence`; position sizing == live `RiskManager.calculate_position` (EURUSD/USDJPY/XAUUSD, several risk factors);
  **no look-ahead** (candidates up to time T are identical with or without future data).
* `tests/test_sim_mechanics.py`: hand-built price paths — TP = +3R, SL = −1R, spread blocks a fill, same-bar SL/TP → SL,
  fill-bar rule, breakeven, order expiry, one-position guard, commission both sides.
* Independent re-derivation of every SL/TP exit from raw bars: 487/487 match.
* `compare_live.py`: against the real live bot's Excel log (2026-07-20..27, current code version): direction matches
  99.9% and probability 99.6% of ~930 cycles. (June rows differ: the live code was different then.)

## Deliberate reproductions of live quirks

* **Sweep never fires** in `faithful` mode (the live window includes the candle it compares against). `fixed` mode is a variant.
* Positions/pending read once per cycle (`stale_snapshot=True`) → several pairs can open in one cycle, max-open/correlation checks miss them.
* Breakeven pip size uses 0.0001 for everything except JPY pairs (gold included); pending LIMIT becomes STOP when price is on the wrong side;
  no weekend flatten; daily-loss reference = first balance of the UTC day.

## NOT modelled (results are an optimistic upper bound)

News blocking (no historical calendar; the live filter was also off in practice), the LLM layer, swaps, requotes/real slippage
(parameter `stop_slippage_points`), broker-specific weekend/rollover rules. Intrabar order is unknown from OHLC: default is
**pessimistic** (SL wins ties; TP ignored on the fill bar); `intrabar="neutral"` is the opposite-leaning sensitivity.
Commission defaults to FTMO's $2.50/lot/side. EURUSD history spreads are recorded as 0 on most recent bars; those are
imputed with the same-hour non-zero median (~0.2 pip).

## Data limits

The terminal caps M5 history at 99,999 bars (≈16 months). To go further: MT5 → Tools → Options → Charts →
*Max bars in chart* = Unlimited, restart MT5, re-run `fetch_data` (the broker must still hold the older M5 data).
~16 months and a few hundred trades is **not** enough to establish an edge: see `edge_summary` in each report.
