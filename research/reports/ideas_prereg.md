# Pre-registration: three alternative strategy ideas + a trivial benchmark

Written 2026-10-26, **before any backtest of these ideas was run**. What I had seen when writing it: the layout of the
cached data (bar counts, hour-of-day histograms, spread statistics) and the previous RESULTS.md, which is about the
*existing* live strategy only. I have seen no return, hit rate or P/L of any rule below.

Prior (stated so it can be held against me): most retail FX rules on 16 months of data have zero or negative net edge
after costs. My expectation is that at most one of these shows anything, and that the sample will be too small to confirm
it. A null result is a legitimate outcome of this exercise.

---------------------------------------------------------------------------------------------------------------------

## 0. Protocol common to all arms

### 0.1 Data split (fixed now)

* Span of the cached M5 data: 2025-06-19 .. 2026-10-26 (XAUUSD from 2025-05-23).
* **DEV window: decision time T < 2026-05-04 00:00 UTC** (64.5 % of the span, ~319 days).
* **HOLDOUT window: T >= 2026-05-04 00:00 UTC** (35.5 %, 175 days). 2026-05-04 is a Monday, so no weekend is cut.
* Dev stage physically truncates every timeframe to bars closed before the split (`open + tf <= split`); the generators
  never see holdout bars during development. The holdout stage runs **once per arm**; a ledger file
  (`research/output/ideas_holdout/ledger.json`) refuses a second run unless `--force` is given, and forced re-runs are
  reported as such.
* Indicator warm-up uses all earlier history (H4 from 2023-08, H1 from 2024-11, M15 from 2025-03, M5 from 2025-06).

### 0.2 Bar-close convention

Decision time `T` = close of an M5 bar (bar open + 5 min), the same convention as `signals.py`. A bar of timeframe `tf`
may be used only if `open + tf <= T`. All indicators are causal recursions/rolling windows on closed bars. Every
generator has a test that truncates the data at a cut time and checks candidates up to the cut are identical with and
without future bars.

### 0.3 Execution model (identical for all arms, fixed now)

* `research.sim.Simulator` (subclass `TimedSimulator` in `research/ideas/simx.py` adds only a **time stop**; the base
  class is not edited). Entries are **MARKET at the close of the decision M5 bar** (buy at ask, sell at bid); the first
  exit check is the next bar. Bid/ask from the recorded per-bar spread (imputed as in `data.py`).
* Stop given by the rule; target = `rr x stop distance` via `SimConfig.rr_ratio`; the simulator's 10-pip minimum stop
  and pre-trade spread filter (spread <= 10 % of stop; absolute spread caps) stay on.
* **No break-even**, risk 0.5 % of balance per trade (`atr_factor = size_factor = 1`), commission $2.50/lot/side,
  intrabar rule pessimistic (SL wins ties; TP ignored on the fill bar).
* Live-bot-specific gates are **off** so the edge of each rule is measured in isolation: correlation filter off, daily
  drawdown gate off, monthly-target pause off, max open trades 50. One open position per symbol (simulator guard).
* **Time stops** (market exit at the close of the first M5 bar at/after the stop time, reason `TIME`, net of spread) are
  part of each rule; additionally every position is flattened at **Friday 20:00 UTC** (the live bot's weekend gaps cost
  -35 R in RESULTS.md; no rule here holds over a weekend). Entries whose flatten time is < 30 min away are skipped.
* "Gross" = the same candidates with zero spread (ask = bid, `spread_mult = 0`), zero commission and spread filter off.

### 0.4 Primary comparison, null, and multiple testing

* **Primary statistic per arm:** net expectancy (mean R per trade) in the HOLDOUT window minus the mean of the
  **random-direction null**: same candidate set, times, stop distances, sizing, gates and costs, but each trade's
  direction is flipped with probability 1/2 (stop mirrored about entry). K = 200 seeds. One-sided p = (1 + #{null >=
  actual}) / (K + 1). z = (actual - null mean)/null sd.
* **Family:** the four arms (A, B, C, Z). Holm-Bonferroni across the four holdout p-values at alpha = 0.05 (one-sided).
  As a deliberately harsh second view I also report Bonferroni over **all** simulated variants (counted in the variant
  log, see 0.6).
* **Secondary (descriptive):** paired directional-skill estimate = mean over matched trades of (R_actual - R_mirrored)/2
  with bootstrap CI; day-cluster bootstrap CI of expectancy (trades of different pairs on the same day are correlated);
  profit factor, win rate, max drawdown, per-pair and per-month stability, gross vs net.
* Power: SE of mean R = sd_R / sqrt(N); I report the N needed for 80 % power at 5 % two-sided for edges of 0.05/0.10/0.20 R.

### 0.5 Decision rules (fixed now)

* An arm **"clears dev"** if dev net expectancy > 0 AND dev random-null one-sided p <= 0.05 (uncorrected; dev is
  exploratory). Only arms that clear dev get the FTMO Monte Carlo (`research.montecarlo.ftmo_mc`).
* **"Evidence of edge"** requires ALL of: holdout net expectancy > 0; Holm-adjusted holdout p < 0.05 vs the null; dev net
  expectancy > 0; robustness plateau (below); and holdout net expectancy above the benchmark Z's.
* **"Suggestive"** (weaker, not evidence): dev and holdout net expectancy both > 0 and both unadjusted null p <= 0.10.
* Otherwise: **no evidence**.
* **Plateau criterion** (dev only, one-at-a-time +/-20 % on each key numeric parameter, not tuned): a rule lives on a
  plateau if the base and >= 75 % of its perturbed variants have dev net expectancy of the same sign, and no perturbed
  variant swings by more than 2 SE (of the base estimate) from the base. A spike (one or two good cells) = fragile.
* **No parameter is changed after seeing dev results.** If dev looks bad, that is the result. Implementation bugs found
  during development are fixed, logged in the variant log as `kind=bugfix`, and counted.

### 0.6 Variant accounting

Every simulation call made through `research/ideas/evaluate.py` appends a row to
`research/output/ideas_variants_log.csv` (UTC timestamp, arm, parameter JSON, window, kind in {base, robust, null,
gross, holdout, bugfix, debug}, trades, net expectancy). Pre-planned counts: 4 base dev + 28 robustness dev
(8 + 8 + 8 + 4) + 4 holdout + the null/gross replicates of the base configurations.

---------------------------------------------------------------------------------------------------------------------

## Arm A: London sweep-and-reverse of the Asian range

**Pairs:** EURUSD, GBPUSD. **Clock:** Europe/London local time (so the window tracks the London open through DST).

**Economic reason.** Resting stop orders and breakout entries cluster just beyond the Asian high/low. The London open
brings dealer/real-money flow that frequently probes those levels. If the probe is a liquidity grab (price is accepted
back inside the range and micro-structure breaks the other way) rather than the start of a trend, price tends to travel
back across the range. The hypothesis is *reversion after a failed break*, not that "smart money" exists.
Honest scepticism: failed-break reversion is also what a random walk produces after any excursion and a break-even stop
placement removes the cushion; the null test is what separates the two.

**Rules (all values fixed).**

1. Asian range for London-local date D: high AH and low AL of M5 bars with local open time in [00:00, 07:00). Skip the
   day if fewer than 72 of the 84 bars exist or if AH - AL < 10 pips. Width W = AH - AL.
2. Decision times: closes of M5 bars with local open time in [07:00, 09:55] (T from 07:05 to 10:00 local).
3. Sweep up: any bar from 07:00 on with high > AH; track SH = highest high since the first such bar. If SH - AH > 1.0 x W
   the up-sweep is voided for the day (it is a trend break, not a sweep). Sweep down mirrored with AL, SL_ = lowest low.
4. **SELL trigger** at the close of a bar when (i) an un-voided up-sweep has occurred at or before this bar, (ii) the bar
   closes below AH (back inside), and (iii) its close is below the lowest low of the previous 6 M5 bars (structure
   break). **BUY** mirrored. First valid trigger per pair per day only.
5. Entry market at that close. Stop = SH + 2 pips (SELL) / SL_ - 2 pips (BUY) (then the simulator's 10-pip minimum
   applies). Target 2R. Time stop 16:00 London local, same day.

**Fixed parameters and rationale (none tuned).** 00:00-07:00 / 07:00-10:00: standard Asia/London-open convention
(the 07-10 window was the suggested one; local time instead of fixed UTC so DST does not shift it by an hour).
72/84 bars and 10 pips: only reject broken/holiday data and ranges smaller than the simulator's minimum stop.
Excursion cap 1.0 W: a sweep deeper than a whole range is a breakout. 6 bars: 30 minutes of micro-structure.
2 pips buffer: about one typical London spread plus noise. RR 2: suggested. Time stop 16:00: end of the London
afternoon / NY morning, no overnight holding.

**Key numeric parameters for the +/-20 % check:** confirmation bars 6 (5, 7), buffer 2 pips (1.6, 2.4), RR 2 (1.6, 2.4),
excursion cap 1.0 (0.8, 1.2).

---------------------------------------------------------------------------------------------------------------------

## Arm B: higher-timeframe trend pullback (deliberately simple baseline)

**Pairs:** EURUSD GBPUSD USDJPY AUDUSD USDCAD XAUUSD. **Decision times:** M15 closes with T in (07:00, 20:00] UTC.

**Economic reason.** Trend persistence at multi-day horizons in FX is the best documented systematic effect (carry and
trend premia, slow diffusion of macro/central-bank news, flow autocorrelation). Buying a shallow retracement within an
established trend improves the entry relative to buying the breakout; it does not by itself add edge, which is why the
benchmark Z (same trend, no pullback timing) is included.

**Rules.**

1. H4 trend (last closed H4 bar): long regime if EMA50 > EMA200 and ADX(14) > 20; short regime if EMA50 < EMA200 and
   ADX > 20; otherwise no trade.
2. H1 pullback zone (last closed H1 bar; E = EMA20(close), A = ATR(14)): long zone [E - 1.0 A, E], short zone [E, E + 1.0 A].
3. M15 trigger at T: the previous M15 close is inside the zone; and the M15 bar that just closed is bullish
   (close > open) with close > previous M15 high (long), mirrored for short.
4. Entry market; **stop 1.5 x A(H1) from entry; target 2R**; time stop 48 h; minimum 4 h between candidates on the same
   symbol (independence of trades).

**Rationale for values.** EMA50/200, ADX 14 > 20, EMA20, ATR 14: conventional, chosen for being conventional. Zone depth
1 ATR: a retracement deeper than one hourly ATR below the 20-EMA is more likely a trend failure than a pause. Stop
1.5 ATR(H1): outside typical hourly noise. RR 2 suggested. 48 h: a 1.5-ATR/3-ATR bracket on H1 resolves in 1-2 days.
07-20 UTC: avoids the thin late-NY/rollover hours where spreads widen.

**Key numeric parameters for +/-20 %:** ADX threshold 20 (16, 24), zone depth 1.0 (0.8, 1.2), stop multiple 1.5
(1.2, 1.8), RR 2 (1.6, 2.4).

---------------------------------------------------------------------------------------------------------------------

## Arm C: prior-day-range breakout with a volatility-regime filter

**Pairs:** all six. **Day:** UTC calendar day. **Decision times:** H1 closes.

**Economic reason.** Volatility clusters (the strongest regularity in return data): after an above-average range day,
the next day is more likely to be volatile too, so a close beyond yesterday's extreme has a higher chance to be followed
by range expansion instead of being an in-noise poke, and the fixed spread cost is a smaller share of the stop.
Direction is taken from the H4 trend so the trade is "break in the direction of the slow trend". Honest scepticism: the
volatility filter predicts *size* of moves, not direction; the direction part rests entirely on trend continuation.

**Rules.**

1. PDH/PDL = high/low of the last complete UTC day (>= 18 H1 bars). ADR10 = mean daily range over the 10 most recent
   complete days (including the prior day). **Filter:** prior-day range >= 1.0 x ADR10.
2. The first H1 close of the UTC day (from 00:00) outside [PDL, PDH] is "the breakout". It is tradable only if it happens
   at an H1 close with T in [08:00, 17:00] UTC (a breakout during the thin Asian hours, or one that is already in
   place at the open, is not chased) and its direction equals the H4 trend sign (EMA50 > EMA200 -> long only; < -> short only).
3. Entry market at that H1 close. **Stop = 0.25 x ADR10** from entry; target 2R; time stop 21:00 UTC same day.
   One trade per pair per day.

**Rationale for values.** ADR10 gives ~two weeks of local volatility; stop of a quarter of an average day is a
sizeable-but-not-wide intraday stop; 2R = half an average day. Time stop 21:00 UTC = before the NY close/rollover.

**Key numeric parameters for +/-20 %:** filter 1.0 (0.8, 1.2), stop 0.25 ADR (0.20, 0.30), RR 2 (1.6, 2.4),
ADR lookback 10 (8, 12).

---------------------------------------------------------------------------------------------------------------------

## Arm Z: trivial benchmark (any complexity must beat this)

**Pairs:** all six. **Rule.** Once per weekday at T = 07:00 UTC, trade in the direction of the H4 trend sign (EMA50 vs
EMA200 on the last closed H4 bar, no strength filter, no pullback, no confirmation). Entry market; stop 1.5 x ATR(14)
of H1; target 2R; time stop 48 h (the same exit machinery as B). The simulator's one-position-per-symbol guard stops
daily re-entry while a trade is open.

**Why.** Z is B without the ADX filter, the pullback and the M15 trigger, i.e. it isolates what the "timing" machinery
adds. If B does not beat Z, the extra rules are decoration. Z also has by far the most trades, so it gives the best
estimate of what the *cost structure plus a plain trend direction* does with a 2R bracket.

**Key numeric parameters for +/-20 %:** stop multiple 1.5 (1.2, 1.8), RR 2 (1.6, 2.4).

---------------------------------------------------------------------------------------------------------------------

## What would change my mind / what is out of scope

* Not modelled: swaps (all trades are closed intraday or within 48 h; a few cross a rollover), news, real slippage,
  requotes. Results are an optimistic upper bound on execution.
* A rule that shows a positive dev result and a negative holdout result is reported as a failed replication.
* Parameter changes after looking at results are not permitted; new ideas would require a new pre-registration and a
  new, untouched holdout (the present holdout is spent once the evaluation has run).
