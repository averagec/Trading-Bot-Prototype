# Results — faithful backtest + Monte Carlo (run 2026-10-26)

Data: 6 pairs (EURUSD GBPUSD USDJPY AUDUSD USDCAD XAUUSD), M5 from 2025-06-19 (XAUUSD 2025-05-23) to 2026-10-26 (~16 months),
USD account $100k, FTMO commission $2.50/lot/side, per-bar broker spreads. Live logic replayed as-is (sweep bug included).
**Optimistic upper bound**: no news filter, swaps, real slippage or requotes. ~16 months is short and one market regime.

## Headline (base, faithful)

| | |
|---|---|
| Trades | 825 (≈48 / month) |
| Exit mix | SL 58.7% · breakeven 26.9% · TP 14.4% |
| Win rate (net P/L > 0) | 36.4% |
| Expectancy | **−0.205 R** per trade, 95% CI [−0.31, −0.10], t = −3.8 |
| Profit factor | 0.63 |
| Return / max drawdown | −41.7% / −44.2% (equity) |
| In-sample (first 70%) / out-of-sample | −0.258 R (629 trades) / −0.036 R (196 trades, CI wide) |
| Median risk actually used | ≈0.25% of balance (0.5% scaled down by ATR/regime factors) |

## The signal's direction carries no detectable edge

Null test (`nulltest.py`): same 825-ish trades (cycle times, entry prices, stop distances, sizing, gates, costs) but each
trade's direction flipped at random, 40 seeds:

* random-direction null: **−0.177 R** (sd 0.038); real strategy −0.205 R → z = −0.74, p(null ≤ actual) = 0.20
* mirror every trade: −0.170 R

→ The signals are statistically indistinguishable from a coin flip, and any skill is small (≈ −0.03 ± 0.04 R vs null).
The losses come from the framework around the signal, not from signals pointing the wrong way.

## Where a zero-skill signal's −0.17 R goes (`decompose.py`, random directions, 20 seeds)

| Configuration | mean R |
|---|---|
| all frictions | −0.173 |
| − commission | −0.128 (commission ≈ −0.045 R/trade) |
| − spread (and spread filter) | −0.134 |
| − pessimistic same-bar rule (neutral) | −0.084 (**≈ 0.05 R of this is my conservative modelling assumption**) |
| − breakeven rule | −0.049 ± 0.05, win rate 25.0% = the theoretical 1-in-4 for a 3R target |

Breakeven-at-1R also costs ≈ 0.035 R. With frictions removed and no breakeven the simulator returns ≈ 0, as it should.

## Risk facts the live bot never reported

* **Weekend gaps:** 26 trades closed at the Sunday reopen, summing −35 R; four of them gapped through the stop for
  −19.7 R (USDJPY short, ≈ −9.8% of the account at 0.5% risk), −8.3 R, −3.0 R, −2.2 R. The bot never flattens before the weekend.
* **Late entries:** 72 trades opened at/after 20:00 UTC averaged −0.57 R (rollover spreads/whipsaw).
* About 46% of fills are STOP orders: the live code turns an FVG-midpoint LIMIT into a STOP when price is on the wrong side.

## Monte Carlo (base trades; resampling cannot create edge)

* iid bootstrap: P(profit) 0.015%; median −41%; 95th-percentile worst drawdown −57%.
* Order shuffle: max drawdown −43% median — sequence luck is not the issue, the mean is.
* FTMO-style challenge (10% target, 5% daily, 10% max loss, day-block bootstrap): pass 0.2%, fail by max-loss 52%, unresolved at 90 days 48%.
  Phase-2-style 5% target: pass 4.7%. Daily-loss breaches 0% because the bot's own 3% gate stops trading first.
  (Rules are assumptions — verify against FTMO's current terms.)
* Cost stress: extra 0.1–0.3 R per trade and 10–50% missed trades only worsen an already negative mean.

## Sensitivity variants (exploratory — do NOT pick a winner from these; each has ~800–1100 trades)

| Variant | trades | expR | PF | return | OOS expR |
|---|---|---|---|---|---|
| base (faithful) | 825 | −0.205 | 0.63 | −41.7% | −0.036 |
| fixed sweep | 1127 | −0.173 | 0.69 | −42.7% | −0.032 |
| no breakeven | 732 | −0.142 | 0.75 | −33.5% | +0.089 |
| RR 2 | 844 | −0.218 | 0.61 | −44.5% | −0.140 |
| no regime filter | 953 | −0.219 | 0.60 | −67.2% | −0.058 |
| spread ×1.5 / ×2 | 770 / 682 | −0.235 / −0.253 | 0.60 / 0.58 | −43.7% / −41.7% | −0.126 / −0.216 |
| neutral intrabar | 825 | −0.214 | 0.62 | −43.0% | −0.049 |

Every variant is negative in full sample. Fixing the sweep does not rescue it. The monthly 5% cap and the stale-snapshot quirk never mattered.

## Validation behind these numbers

56 tests pass (live-code equivalence, no-look-ahead, hand-checked mechanics, Monte Carlo). All 487 SL/TP exits re-derived independently
from raw bars (0 mismatches). Replay vs the real live bot's Excel log, 20–27 Jul 2026: direction 99.9%, probability 99.6% of 931 cycles.

## What this does and does not say

* It says: as currently built (3R target, breakeven at 1R, structure stops, commission, no weekend flatten), the bot has negative
  expectancy on 16 months of data, and its signals show no measurable directional skill versus random.
* It does not say the underlying SMC ideas can't work, that this regime is representative, or what live results would be.
* To be viable, a signal would need roughly +0.15 R of real directional edge just to cover frictions at these settings.
  Candidate changes (weekend flatten, no entries after ~20:00 UTC, breakeven at ≥1.5R or removed, limit-only entries) are untested
  hypotheses; test them on data the choices were not made on (more history: raise MT5 "Max bars in chart" and re-fetch).
