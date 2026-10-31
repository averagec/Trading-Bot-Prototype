# Risk-profile simulation: pre-registration (written 2026-10-31, before any risk-profile simulation was run)

Part of `research/reports/quant_loss_risk_review.md`. Code `research/quant_risk.py`, tests `research/tests/test_quant_risk.py`. DEV only (2014-01-01 to 2021-06-01; FINAL
and 2025-05+ not read). The estimand is **how fast a no-edge (or negative-drag) strategy loses money and how often it breaches FTMO-style limits under different risk rules**;
it is not a search for profit and no rule here can create an edge. The profiles are fixed below, before any result; they are not tuned to the DEV outcome.

## 0. What has been seen

* `RESULTS.md` / `rule_changes.md` / `stop_floor.md`: FTMO Monte Carlo on the burnt 16 months (base: pass 10 % 0.2 %, max-loss fail 52 %), stop-floor real/null drawdowns, and the DEV real-signal
  drawdown -75 % at 0.25 % risk.
* My anatomy run on DEV (live settings, real signals, 3,970 trades): mean -0.131 R, max drawdown -74.7 % (equity), worst Prague-day low -4.58 %, 76 % of months negative (mean -1.45 % per month),
  worst month -9.2 %, worst week -5.2 %, losing-streak maximum 18 against an iid 14.6 (p05-p95 11-19), concurrent positions at most 4 (time share with 2 or more open: 3 %).
  Of the per-trade drag, 0.091 R is entry spread plus commission and -0.040 R is pre-cost.
* NOT seen: any drawdown/breach probability under any of the profiles below.

## 1. Model

Generator: the DEV day tables (Prague days, as FTMO) of the portfolio simulator, from (a) the one real-signal run and (b) 20 random-direction `reflect` null seeds (seeds 0 to 19, the same flips as
the other studies), each at the profile's own static settings. A day carries the ordered per-trade returns of the trades CLOSED that day (`ret_pct`, % of balance at open, at the sim's risk
fraction f0) and the intraday equity low relative to the day's start balance (`low_pct`). Weekend closes are moved to the next weekday.

Monte Carlo (`quant_risk.profile_mc`): stationary-free block bootstrap of 5-day blocks of whole days (as `montecarlo.ftmo_mc`), 20,000 paths, seed 21, horizons 6 / 12 / 24 months
(126 / 252 / 504 trading days). Each path starts at balance 1.0 (initial). Within a path, per trade: risk multiplier = m0 (= f / f0) x ladder(loss from initial) x streak state; the trade
return is scaled by it and compounded on the balance. FTMO limits (assumptions from third-party sources, **not verified against FTMO's own rule text**): daily loss 5 % of the INITIAL balance measured
from the start-of-day balance with floating P/L counted, reset at Prague midnight; static max loss 10 % of the initial balance (floor 0.90); profit target 10 % with at least 4 trading days;
a path that touches either loss limit fails and stops. The day's floating low is the day's `low_pct` scaled by the multiplier at the start of the day (an approximation: rules that change the
multiplier intraday are not reflected in the low).

Validation built into the tests: with m0 = 1 and no rules the engine matches `montecarlo.ftmo_mc` breach logic on hand-built days; scaling is exactly linear in the multiplier for a one-day path.

Edge scenarios (so the "risk management cannot create profit" claim is quantified, not asserted): the mean R per trade is shifted to mu in {as simulated (about -0.13), 0.00, +0.05, +0.10} by adding
delta = mu - mean R to every trade's R, i.e. delta x risk_pct to its return; the intraday low is shifted by min(0, delta x risk closed that day). This is a labelled approximation.

## 2. Profiles (fixed)

| Profile | f (% risk per trade) | Static (full simulation) | Dynamic overlays (Monte Carlo) |
|---|---|---|---|
| P0 current | 0.25 | max 3 open, 3 % daily gate (equity vs first UTC-day balance, blocks new entries only), correlation filter, Friday flatten 20:00, stale snapshot | none |
| P1 current + guards | 0.25 | identical to P0 (same simulation) | halt trading when loss from initial >= 8 %; stop new trades for the day when the day's realised loss >= 2 %; after 8 consecutive losing trades skip the rest of the day and the next day |
| P2 reduced + ladder | 0.15 | max 2 open, 2 % daily gate, rest as P0 | loss-from-initial ladder: >= 3 % -> x0.5, >= 5 % -> x0.25; halt at 8 %; day stop at 1.5 %; after 6 consecutive losses skip rest of day and next day |
| P3 small + ladder | 0.075 | max 2 open, 1.5 % daily gate, rest as P0 | ladder: >= 2 % -> x0.5, >= 4 % -> x0.25; halt at 6 %; day stop at 1 %; after 6 consecutive losses skip rest of day and next day |

Also the **risk-fraction curve**: P0's day table scaled linearly to f in {0.05, 0.075, 0.10, 0.15, 0.25, 0.35, 0.50, 1.00} with no overlays (the gate is not re-simulated at other f: a
stated simplification, immaterial because the 3 % gate fired 3 times in 89 months at f = 0.25).

## 3. Outputs and the demo-profile rule

Per profile, source (real path / null pool) and scenario mu, horizon: P(daily-loss breach), P(max-loss breach), P(halted by our own rule), P(10 % target passed), median and p05 final balance,
mean final balance, max drawdown median / p95 / p99 (worst 5 % / 1 % of paths), median months to a limit breach (among breaching paths) and to our halt, and **trades taken before the first breach
or halt (mean, median)** as the measure of evidence gathered. Expected loss per month (mean of final - 1 over months) is reported for every profile: this is the speed of loss.

**Demo-profile rule (fixed now):** among the profiles and the curve grid, the recommended forward-demo risk fraction is the largest f for which, under BOTH the real-path and null-pool sources at the
as-simulated drag, P(max-loss breach within 12 months) <= 1 %, P(daily breach within 12 months) <= 0.5 %, and the median number of trades taken before a halt or breach over 24 months is at least
500. If no f on the grid satisfies this, the smallest f is reported with the shortfall. Nothing here is a recommendation to trade real money.

## 4. What this cannot show

No profile changes the sign of the expectancy. Date range, 7.4 years of one price history, 20 null seeds that share candidates and prices (so the null paths are not independent histories), costs of
the 2014-2020 era (spread about 7 % of the stop; recent spreads are smaller), no news filter, no swaps, FTMO rules as assumed, the equity low scaling approximation above.

## Log of changes after registration

* 2026-10-31, after the main risk report (profiles and curve) was produced and before the demo rule was evaluated: the demo rule's third condition is made operational as
  "median over ALL simulated paths of the trades taken in 24 months" (a path that has breached or halted stops taking trades; a path with no event takes all its trades). My first output only
  carried the median among paths that had an event, which is undefined for most small-f paths. The thresholds (1 %, 0.5 %, 500) and the candidate set are unchanged.
