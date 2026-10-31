# Pre-registration R3-1: price-only time-series momentum on D1 data, six instruments (written 2026-10-31, FROZEN BEFORE ANY RUN)

GAMEPLAN section 8 item R3 and section 13 item 3, stage 1. **No momentum rule has been backtested on this data.** Nothing here touches live code, `sim.py` or `signals.py`.
DEV only: the code refuses any end after 2021-06-01 and any start on or after it. FINAL (2021-06-01 to 2025-04-30) is untouched; 2025-05 onward is not used. FINAL mode is disabled
unless the file `research/output/slow_momentum/FINAL_GO` exists, which only the owner creates. No `--force`. This file is committed locally before the first run; its sha256 is written into every output.
**Revision 1 (2026-10-31, after an independent hostile review, still before any run) is applied throughout and listed in "Log" at the end; the original commit fc8c4cf (sha256 2b522678...) stays in git history.**

## 0. What has been seen (so nobody mistakes this for blind)

* **A2' (slow-horizon ML learnability, `ai_slow_prereg.md`, `RESULTS.md`):** on H4 and D1 decision bars of the five FX pairs, DEV 2003-01 to 2021-05, causal features of price, volatility, time of day, spread and the USD factor
  (D1 features: returns over 1, 5, 10, 20 and 60 days, distance to 20- and 120-day highs and lows, close minus EMA50 and EMA200, a USD factor over 5 and 20 days) do not predict the sign of a stateless triple-barrier net-R
  trade: directional AUC 0.500 to 0.505, all four variants falsified. That is a close cousin of short-lookback momentum, so **the prior for L = 21 and 63 days is already low**; L = 126 and 252 days lie beyond every A2'
  feature window (EMA200 excepted). A2' used a weekly-sign label with a stop and a 3R target, not a vol-targeted position, so it does not test this strategy; it is evidence about the signal.
* **One descriptive cell:** D1_k15 with the larger boosted model showed a top-decile gain of +0.060 R, 1 of 12 cells, a weaker log-loss than the baseline and directional AUC 0.511. **It is not used here in any way**: no variant, parameter,
  universe choice or threshold in this document was taken from it, and it is not chased.
* The cost-by-horizon table (D1 0.0085 R), the D1 builder and its tests (`research.ai.slow.build_d1`), the era quality screens (`data_quality_by_era.md`, `quality_h1h4_2000_2013.txt`), the 50-point spread placeholder finding.
* **Direction-neutral data facts I looked at in this session, no returns of any rule:** H1 row counts and first/last dates per instrument; XAUUSD yearly spread median and zero share, **and its year-end closes (437 in 2004,
  1,098 in 2009, 1,419 in 2010, 1,898 in 2020, then 1,829 / 1,825 / 2,063 / 2,624 / 4,314 for 2021 to 2025)**. I therefore know gold had a long bull market 2004 to 2011 and again 2023 to 2025, **so I have seen the gold price path inside the FINAL window (2021-06 to 2025-04) at year-end resolution**.
  That is price history, not the outcome of any rule here, but it is knowledge that could bias the gold handling in section 2 and it makes any gold-heavy FINAL result less blind than the other instruments; the gold decision is made from the spread record only (no recorded spread before 2011), and the effect of gold is reported with and without.
* **Prior knowledge, from memory and not looked up (unverified):** the TSMOM literature (Moskowitz, Ooi and Pedersen 2012; Hurst, Ooi and Pedersen 2017) reports positive trend profits in currencies and commodities with 1 to 12 month
  look-backs, much of it before 2010, and trend-following in FX is widely said to have been weak since about 2010. These sample periods overlap this DEV window (2003 to 2021), so the *existence* of the family is not an independent
  discovery on this data; the grid below is the textbook one (1, 3, 6, 12 months and the 6-1 and 12-1 skip forms) and was written before any run.
* NOT seen: the P/L, Sharpe, hit rate or turnover of any position rule on any of these instruments, in DEV or FINAL.

## 1. Question and estimand

Does a vol-targeted, long/short, trailing-return-sign rule on daily bars, applied to an equal-risk portfolio of six instruments, earn a net-of-cost return per unit of risk that exceeds what the same sizing, turnover, long/short bias
and costs earn with the sign path carrying no timing information, and exceeds a vol-targeted always-long benchmark?

**Primary estimand, per variant v:** SR_v = the annualised net Sharpe ratio of the weekly P&L of the equal-risk portfolio, `mean(weekly net P&L) / sd(weekly net P&L) * sqrt(52)`, with spread costs and **zero swap** (the only cost that can be measured
here). No cash interest is credited. Two stressed versions with an explicit financing charge are in section 5 and enter the decision (they are the survival test of the cost assumption).

## 2. Data, universe, handling (fixed)

* **Universe:** EURUSD, GBPUSD, USDJPY, AUDUSD, USDCAD, XAUUSD. **Gold is included from its first rebalance on or after 2011-01-01**; its price history from 2004-06 is used only for look-backs and volatility. Reason (spread record only): XAUUSD H1 `spread` is zero on 100 % of bars
  2004-2009 and 85 % in 2010 (nothing recorded), 4 to 11 % zero from 2011; before 2011 its cost would be a pure guess. FX5 trade from the first rebalance in 2003. Before 2011 the portfolio is the five FX pairs; after, six. The universe is the bot's own pair list, so
  it is conditioned on a trader's choice of liquid, still-existing instruments (survivorship in the choice of instruments, not removable here; stated as a caveat).
* **Bars:** the DEV H1 files through `research.ai.dataset.load_bars` (every loader refuses an end after 2021-06-01; the parquet read is filtered at file level). **D1 bars are built from H1 with `research.ai.slow.build_d1`** (broker NY-close day, server date, at least 12 H1 bars, last
  bar closing at or after server midnight), exactly as A2'. H4/M5 are not used.
* **DEV start = 2003-01-01** (first rebalance on or after that date). 2000-03 to 2002-12 is warm-up only for signals and volatility (the same rule as A2': that history is complete, the clock is consistent, spreads are nominal constants). The longest look-back (252 days) and the volatility
  window (60 days) are therefore available from the first rebalance. End: bars strictly before 2021-06-01; the last scored period ends at the last execution before that date.
* **Eras (as A2'):** E1 2003-2009 (nominal constant spread field: EURUSD 5 pips 2000-03, 4 in 2004, 3 in 2005, 2 in 2006-09; used as recorded and stressed), E2 2010-2013 (recorded 9 to 11 points on EURUSD), E3 2014-2021-05 (recorded). **Flagged years** from `data_quality_by_era.md`: 2016-2017
  (one Christmas reopen, not a clock shift, no exclusion), GBPUSD 2019 (H1 gaps of about 15 h in 62 places; D1 close-to-close is still defined), 2019 spikes (rollover hour), XAUUSD 2013 (fail on M5 gaps and 4 Saturday bars; H1 not separately screened), XAUUSD 2021 (zero spreads imputed),
  the 50-point FX placeholder 2019-01..03 (GBPUSD) and 2020-05..2021-05 (handled by `dataset.add_clean_spread`: sentinel and zero spreads replaced by the median of clean recorded values of the same pair, year and UTC hour; used for COSTS only). **Gold screen (Revision 1):** XAUUSD H1 2011 to 2021-05 was never screened. Before the DEV run the standard `research.data_quality` report is run on it (data only, no returns); **any gold calendar year with a FAIL-level issue is set flat for gold in that year in the primary** (rule declared now, outcome logged before the run). **Primary = all other years included. Reported alongside,
  descriptive only:** without 2016-2017; without GBPUSD 2019; without XAUUSD (this one is also a gate, G4); without 2020-05 onward (placeholder months); starting 2010 and 2014 (starting-date sensitivity).
* **Calendar of rebalances (common to all instruments, weekly):** for ISO week k, R_k = the broker-server midnight that ends the primary weekday (Wednesday), converted to UTC with `research.data.server_to_utc` (21:00 or 22:00 UTC). A single weekday is used. **Wednesday is chosen for being mid-week (no weekend gap, no Monday or Friday thin liquidity, holidays less often on it); the choice is arbitrary and frozen, and the other four weekdays are reported (descriptive) and may not be selected from.**
* **Per instrument and rebalance k:** the signal uses the last complete D1 bar whose close time T satisfies T <= R_k, and only if T >= R_k - 3 days (otherwise the instrument is flat at k). **Execution: the open of the first H1 bar with time >= R_k**, and only if that bar starts within 72 hours of R_k
  (otherwise flat). The position set at k is held to the execution price of k+1; there is no intra-week action. **Missing prices (Revision 1):** the exit of period k is the execution price of k+1 (first H1 open >= R_(k+1) within 72 hours); if there is none, the period is marked at the last available H1 close before R_(k+1) and the instrument is flat for the next period only (rebuilt at k+2). P&L is never zeroed.
  The counts of flat instrument-weeks, stale-signal fallbacks (for example a holiday Wednesday whose D1 bar is missing, so Tuesday's bar is used) and marked-at-last-close periods are written to the output.
  For the Friday weekday the execution is the Sunday-evening open, so the weekend gap is inside the P&L (never skipped).
* **Return convention (USD account, constant equity base, additive weekly returns, no within-week drift of weights):** instrument return over a period from execution price P0 to P1: USD-quoted pairs and gold P1/P0 - 1; USD-base pairs (USDJPY, USDCAD) 1 - P0/P1 (a long position in USD notional earns (P1-P0)/P1).
  Prices are the recorded bid. Weekly P&L = sum over instruments of w x return, minus costs.
* **Conventions pinned (Revision 1):** a period belongs to the calendar year of its start R_k; N_k counts live instruments, flat or not; the look-back L counts D1 bars of the instrument's own built series (a dropped stub day shifts it by one day; the number of dropped days per year is reported); sign(0) = 0 = flat;
  a window with fewer than L+1 bars is flat; Sharpe annualisation is sqrt(52) with weekly periods (2021 is partial and is reported, not scored in the year gates); gold's first H1 bar at or after R_k is one hour later than FX because of its daily break; the spread of the execution bar is the bar's recorded field (assumed to
  describe its opening quotes; MT5 does not say, so this is an assumption, and the x2 stress covers it).

## 3. Strategy family: six variants, written down now (K = 6)

Signal for instrument i at k: s = sign of the log return of the D1 close over the window [t - L, t - skip] (t = the signal bar), 0 if exactly zero. Long if +1, short if -1, always invested (no flat band).

| Id | L (D1 bars) | skip | meaning |
|---|---|---|---|
| V1 | 21 | 0 | 1 month |
| V2 | 63 | 0 | 3 months |
| V3 | 126 | 0 | 6 months |
| V4 | 252 | 0 | 12 months |
| V5 | 126 | 21 | 6-1 |
| V6 | 252 | 21 | 12-1 |

No other variant, window, band, threshold, ensemble, monthly rebalance or carry/value term is run or reported as a result. Monthly rebalancing is out of scope (the five weekday offsets, reported descriptively, cover the date sensitivity at weekly frequency).
**Sizing:** ex-ante volatility sigma_i,k = standard deviation of the last 60 D1 close-to-close log returns **using bars with T <= R_k only** (at least 40 required, else flat), times sqrt(260). Raw weight = s x min(3, 0.10 / sigma_i,k). Portfolio weight w_i,k = raw / N_k, N_k = number of instruments live
at k (5 before gold, 6 after): equal risk per instrument (stand-alone), no correlation adjustment, no portfolio vol target (Sharpe is scale-free; realised annual vol and the mean gross notional sum |w| are reported). Price-only value proxies are not built (default: left out); carry and rate-based value are impossible (no rate data).

## 4. Costs and financing (fixed)

* **Spread:** at each execution the position change pays half the recorded-or-imputed spread of the execution bar: cost = sum |w_i,k - w_i,k-1| x 0.5 x spread_price / P_exec, spread_price = `spread_clean_pts` x point. A round trip therefore pays one spread. The first H1 bar after the NY close is the rollover hour,
  whose recorded spread is the widest of the day; that is used as recorded (not a median), so costs are, if anything, high. Stress: spread x2.
* **Commission:** the live account pays $2.50 per lot per side; for a vol-targeted notional that is 0.25 bp per side on a USD-quoted major ($2.50 on $100,000; gold is lower, a 100 oz lot is about $200,000). Charged as 0.25 bp x |change in weight| for all six (conservative for gold). Immaterial but stated.
* **Swap / rollover financing: unknown.** MT5 history carries no swap. The broker's long and short swap depend on the policy-rate differential (which moved from near 0 to 5 % over this sample) plus a broker mark-up, and can be a credit or a charge by direction. A holding strategy pays it every night
  it is invested, so it **could be material**: at 10 % stand-alone vol per instrument, a six-instrument book has a gross notional of roughly 1.0 to 1.4 times equity and a portfolio volatility of only about 4 to 6 %, so a charge of 1 % a year on gross notional is about 0.25 Sharpe units.
  Treatment: charge c x sum |w_i,k| x (calendar days held)/365 each period, **unsigned** (every position pays, ignoring the sign of the differential, which is not available): **S0 c = 0 (primary estimand); S1 c = 1.5 % a year for FX and 3.0 % for gold; S2 c = 3.5 % for FX and 5.0 % for gold** (Revision 1: gold's long swap is a large charge at any positive rate). S1 is about one broker mark-up plus an average adverse differential
  for FX; S2 is a hostile case. These numbers are assumptions, not data. **The owner can read the six MT5 swap values (long and short); they would be added to the FINAL report as a disclosed input, never to the DEV selection.** The charge depends only on |w|, not on the sign, so it is nearly identical in the actual run and in every null replicate: the
  timing test (G1) at S0 is therefore the same test as at S1 up to flat days, and the net-of-swap question is carried by G2 and G5. The **break-even charge** c* (the c at which mean net P&L is zero) is reported for every variant; it is only a re-expression of the mean P&L per unit of gross notional, useful once the real swap table is known.
  A verdict at S0 alone would be flattering and is never used alone.

## 5. Inference (fixed)

* **Per-variant net Sharpe with a 95 % interval (reporting only; no gate uses it):** circular block bootstrap of the weekly net P&L, resampling whole weeks of all instruments together (cross-sectional dependence kept), blocks of 13, 26 and 52 weeks (sign persistence of the 12-month rules is about 50 weeks), B = 5,000 each, seed 20261031; the interval with the **lowest lower bound** is the one
  quoted (percentile intervals, which are biased in small samples). Also reported on the same resamples: ex-gold Sharpe, each instrument's stand-alone Sharpe, and the difference to the benchmark.
* **Benchmark Z (trivial):** BL = always long every instrument as quoted, same 60-day vol sizing, 1/N_k, same weekday, same costs and swap. It is **not a gate** in DEV (long as quoted is arbitrary for the FX orientation and BL is mostly the gold drift); it is reported with SR_v - SR_BL and its interval so nobody mistakes gold beta for timing. It is a condition at FINAL (section 7).
* **Null that replicates the whole selection procedure: the block-permuted sign null (Revision 1, replaces the same-lag shift as primary).** The sign matrix of each variant (weeks x instruments, computed once from the real data) is cut into blocks of 26 weeks, **the blocks are permuted by a random derangement (no block stays in its own place)** and re-concatenated, so each sign path keeps its
  persistence inside a block, its long/short bias, its approximate flip rate, and the cross-instrument alignment of FX5, while the timing relation to the next period's return is removed. Sizing (the vol scale from data at each date), N_k, spreads, returns, execution, costs and swap are then recomputed at the original dates exactly as in the real run. The same permutation is applied to all six variants in a replicate, so the
  null distribution of the **maximum over the six variants** (the selection statistic) is available. Gold (live from 2011) is permuted among its own live-span blocks by an independent derangement (its FX alignment is lost; stated). M = 2,000 replicates, seed 20261101, so the smallest attainable p-value is 1/2,001 = 0.0005 (the earlier same-lag shift had about 440 strongly dependent draws and an effective resolution near 0.1, which could not support a Holm threshold of 0.0083; it is demoted below).
  Individual p_v = (1 + #{SR_v^m >= SR_v}) / (1 + M); selection-adjusted p_v,max = (1 + #{max_u SR_u^m >= SR_v}) / (1 + M). **What the null tests:** that the *timing* of the sign path carries no information; the long/short bias (for example a mostly-long 12-month sign in a trending asset) is kept in the null and its profit is not credited to the strategy. Known imperfection, direction stated: a block moved to a later place
  may carry a sign computed from returns that overlap the new place (blocks within about 2 blocks of each other), which raises the null SR slightly, i.e. makes the test conservative; the joins add a few extra flips and slightly higher turnover, which makes the null cost slightly higher (liberal); the null mean turnover relative to the actual is written to the output so the second effect can be judged.
* **Secondary, descriptive nulls:** (i) the same-lag circular shift of the sign paths over each instrument's live span, every lag from 78 to (K_gold - 78) weeks (about 390 draws, effective independent draws about 10): empirical p and the z-score against the null mean and sd; (ii) an independent derangement per instrument. If either contradicts the primary result for the chosen candidate the result is called fragile in the report; neither can change eligibility.
* **Multiple testing:** one Holm family of K = 6 (the six variants), family alpha 0.05 (DEV), on the individual p_v. The selection-adjusted p_v,max (the single-step maximum test) is a second requirement for the chosen candidate (G1). The effective number of independent variants is about 2 to 3 (V5 and V6 overlap V3 and V4, V3 and V4 overlap), so Holm over 6 is valid but conservative and p_max makes it partly redundant; both are kept because
  they were written before the run. Both are nominal in the sense that this arm sits inside a campaign that has already made other looks at overlapping data (stage 0, the H1 and slow ML studies) and that GAMEPLAN allows at most two FINAL candidates in total at alpha 0.025, of which this arm would use one; the campaign-wide error is larger than 0.05 and is stated in the report, not corrected.

## 6. DEV rule that chooses the single frozen candidate

A variant is **eligible** only if ALL of these hold on the primary run (Wednesday, all years except the gold-screen exclusions, S1 unless stated):
* **G1** Holm over the six individual p_v (S0 Sharpe against the block-permuted null) rejects v at 0.05, AND p_v,max <= 0.05.
* **G2** SR_v(S1) > 0 (point). (Revision 1: the BL-comparison gate was dropped; the null already controls bias and BL is arbitrary in FX orientation.)
* **G3 stability (all three):** (a) SR_v(S1) stays > 0 after removing the three best calendar years by S1 net P&L (leave-one-year-out is almost always true at Sharpe 0.3 and is reported only); (b) S1 net P&L positive in at least **13** of the 18 calendar years 2003 to 2020 (a coin-flip strategy passes this with probability about 0.05; the partial 2021 is reported);
  (c) SR_v(S1) > 0 in at least 2 of the 3 eras. The five rebalance weekdays are **descriptive** (signals one day apart overlap by more than 90 %, so they are not independent evidence); the 5-weekday table is reported, and a primary result that flips sign on a different weekday is called fragile.
* **G4** ex-gold (FX5 only, same sizing with N_k = 5) SR_v(S1) > 0 (point): the result must not be gold alone.
* **G5** materiality: SR_v(S1) >= 0.30. By the document's own conversion (1 % a year is about 0.25 Sharpe) S1 costs about 0.3 to 0.4 Sharpe, so G5 is close to implied by a G1 pass; it is kept because it states the economic floor.
* **Gate calibration on synthetic data (before the DEV run; thresholds are NOT tuned from it):** `python -m research.slow_momentum calibrate` runs the gates on synthetic return panels with the real calendar (iid, zero true Sharpe, and a planted trend process at several strengths) and writes the pass probability of each gate and of eligibility as a function of the realised S0 Sharpe to `calibration.json`. It uses no project returns.

**Selection:** among eligible variants take the one with the highest SR_v(S1); that is **the frozen candidate**, written to `research/output/slow_momentum_dev/frozen_candidate.json` together with this file's sha256. **If none is eligible there is no candidate and FINAL is not used.** Nothing is chosen by looking at FINAL; the cost, spread x2,
start-date, flagged-year and monthly/other-parameter views are descriptive and cannot add or remove eligibility.

## 7. What each outcome means

* **No eligible variant (the expected outcome, see section 8):** "no momentum configuration in this family is distinguishable from sign-free noise on 2003 to 2021-05 net of spread and a plausible financing charge". Not "momentum does not exist": it is "not detectable at DEV power", and a positive Sharpe whose interval includes 0 is called that, not rejected.
  Stop the family; no re-tuning of L, rebalance frequency or universe after seeing it except within the revision budget (section 9).
* **An eligible variant:** a DEV-selected hypothesis with a Sharpe optimistically biased by the selection (the max-null p partly corrects, the point estimate is not corrected), **not** a validated edge. It is a different strategy from the live SMC bot: it would need its own live code (position sizing in lots from ex-ante vol, weekly rebalancing,
  swap handling, a position cap and FTMO drawdown handling), its own risk review and a `reviewer` pass later. Nothing in this task builds or changes that.
* **FINAL (only if a candidate exists and the owner creates `FINAL_GO`, whose content must include the line `candidate_sha256=<sha256 of frozen_candidate.json>`):** one run of the single frozen candidate on 2021-06-01 to 2025-04-30 (about 204 weekly periods; the 4-month EURUSD 50-point placeholder and the XAUUSD 2021 zero spreads handled by the same sentinel-aware spread; look-back history from DEV bars). One-sided alpha 0.025. Bootstrap blocks 13 and 26 (lowest lower bound; with 204 weeks the interval is unstable and says so).
  **FINAL is not blind (Revision 1): I have seen the gold price path inside it (section 0), and general knowledge of the 2021 to 2025 USD, JPY and rate-hike trends cannot be excluded for FX either.** So the confirmation estimand is the **FX5-only portfolio** (gold excluded, because its FINAL path is known to be a strong trend that a long-biased variant would simply ride), net Sharpe at S1.
  **CONFIRMED** = the one-sided 97.5 % lower bound of the FX5-only S1 net Sharpe > 0, AND the six-instrument S1 point estimate > 0, AND SR_v - SR_BL point estimate > 0 (its interval is reported). **CONSISTENT BUT NOT CONFIRMED** = FX5-only point SR(S1) > 0 and the lower bound <= 0. **NOT REPLICATED** = FX5-only point SR(S1) <= 0. The six-instrument and gold results are reported but cannot confirm.
  No other candidate, no `--force`, no second look. A CONFIRMED here counts as one of the two FINAL slots GAMEPLAN allows at alpha 0.025; the owner allocates the slots across arms (this one, the ATR-floor study, others) before any FINAL run.

## 8. Power (stated before running; analytic plug-in normal approximation SE(SR) = sqrt((1 + SR^2/2) x f / T), z = SR/SE, T in years, f the variance inflation for autocorrelation, shown at 1.0 and 1.3; true Sharpe here means the S0 Sharpe)

| Window | T (years) | one-sided alpha | power at true SR 0.3 | 0.5 | 0.75 | 1.0 | SR for 80 % power |
|---|---|---|---|---|---|---|---|
| DEV 2003 to 2021-05, single test | 18.4 | 0.05 | 0.35 / 0.29 | 0.65 / 0.55 | 0.88 / 0.80 | 0.97 / 0.92 | 0.64 / 0.75 |
| DEV, first Holm step | 18.4 | 0.0083 | 0.13 / 0.10 | 0.35 / 0.27 | 0.67 / 0.54 | 0.87 / 0.75 | 0.89 / 1.08 |
| FINAL 2021-06 to 2025-04, one candidate (the FX5-only confirmation estimand is a little weaker still) | 3.9 | 0.025 | 0.08 / 0.07 | 0.15 / 0.13 | 0.26 / 0.21 | 0.36 / 0.29 | not reached (power is 0.63 / 0.52 at SR 2 and approaches 0.80 only as SR grows without bound) |

Honest reading:
* **DEV is powered for a Sharpe of about 0.65 to 1.1, not for 0.3 to 0.5.** The shifted-sign null is wider than the iid standard error (persistent signs have few independent bets), so the real DEV power against the null is lower than the table. A true S0 Sharpe of 0.3 to 0.5 (typical of what the literature claims for such rules before costs, unverified) would pass the first Holm step with probability
  about 10 to 35 % by the table, and joint eligibility (G1 to G5, where G5 needs roughly S0 0.7 or more) is lower still; the joint probabilities at several planted strengths are in `calibration.json` and are reported next to the DEV result, not used to change a threshold. The expected DEV outcome is therefore "no eligible variant" even if a modest edge exists; that is "not detectable", not "rejected".
* **FINAL has very low power: 7 to 15 % at a true Sharpe of 0.3 to 0.5, about 36 % at 1.0, and about 50 % only at a realised Sharpe near 1.4 to 1.9.** If the true Sharpe is 0.4, the probability that FINAL even shows a positive point estimate is about 78 % and that it reaches CONFIRMED about 10 %. So (i) CONFIRMED on FINAL needs a very large realised Sharpe and, given a DEV-selected
  candidate, is more likely a lucky window than a precisely measured edge; (ii) CONSISTENT BUT NOT CONFIRMED is the most probable outcome of a real modest edge and settles nothing, and the extra conditions on gold, FX5 and the benchmark lower CONFIRMED further; (iii) NOT REPLICATED happens about 22 % of the time even when the true Sharpe is 0.4, so it is weak evidence against. FINAL is the only unburnt window; the owner should decide whether to spend it on a test with this power (section 10).
* The DEV run reports the realised bootstrap standard error and the null standard deviation, so the table above can be checked against the data after the fact (a post-hoc check, not a revision).

## 9. Stop rule

* **After the first DEV result, no gate, threshold, window, universe, null or statistic may be edited** (that would be a fourth look at the same data under a new rule). Allowed: bug fixes in code with the first result kept on record, and descriptive additions. A new variant family needs its own pre-registration and spends alpha: round 2 at 0.025, round 3 at 0.0125, at most 3 rounds in all. Each is logged below with date and reason.
  Pre-run revisions (after the attack, before any run) are logged and do not count.
* FINAL: once, for one frozen candidate chosen by the rule in section 6, only with `FINAL_GO` containing the candidate's hash. No candidate means no FINAL. A candidate whose freeze is voided by a later bug fix loses it.
* Stop and report (do not iterate further) if: no variant is eligible after 3 rounds, or FINAL has been run.

## 10. Decisions the owner must make before any FINAL

1. Whether to spend the only unburnt window on a test with the power in section 8 (recommendation: only if a candidate has DEV net Sharpe at S1 near or above 0.6, otherwise prefer forward evidence or a better-powered design such as more instruments).
2. Whether S1 and S2 reflect the broker's actual swap table (the owner can read the MT5 symbol swap values, long and short, for the six instruments; they can be added to the FINAL report as a disclosed input, but never to the DEV selection).
3. Creating `research/output/slow_momentum/FINAL_GO` (only the owner; it must contain the candidate's hash), and how the two FINAL slots are shared with other arms.

## 11. Outputs and code (fixed names)

`research/slow_momentum.py` (library plus CLI `dev`, `final`), `research/tests/test_slow_momentum.py`, outputs in `research/output/slow_momentum_dev/` (`summary.json`, `variants.csv`, `weekly_pnl.csv`, `per_year.csv`, `per_instrument.csv`, `null_distribution.csv`, `sensitivity.csv`, `calibration.json`, `frozen_candidate.json` only if eligible, `prereg_sha256.txt`).
Seeds fixed; the DEV run is one deterministic run. Existing files are not edited.

## Log of changes after registration

**Revision 1, 2026-10-31, before any run** (independent hostile review of commit fc8c4cf; every valid point applied):
1. BLOCKER, null resolution. The same-lag circular shift gave about 440 strongly dependent draws (effective about 10), so a Holm threshold of 0.0083 was either anti-conservative or unattainable. Primary null replaced by a block-permuted (derangement) sign null with 2,000 replicates (min p 0.0005); the circular shift (lags from 78 weeks) and the per-instrument version are descriptive. Section 5.
2. BLOCKER, FINAL not blind. Disclosed knowledge of the 2021 to 2025 gold path (and general knowledge of FX trends); the FINAL confirmation estimand is now the FX5-only portfolio, with pooled-point and benchmark-point conditions. FINAL_GO must carry the candidate hash. Section 7.
3. G2 BL gate removed (benchmark kept as report and as a FINAL condition). 4. G3(b) raised to 13 of 18 years; G3(a) is now "after removing the best three years"; the weekday gate G3(d) demoted to descriptive; synthetic gate calibration added (no project returns). 5. Section 8 wording fixed (plug-in SE, power limit wording, S0 meaning, joint eligibility reported).
6. Missing-price rule: exit at the first available open or last close, never zero P&L; flat for one period only. 7. Swap: gold charged more (3.0 % S1, 5.0 % S2), the sign-independence of the charge stated, the owner's swap table named as a FINAL-report input; c* described as a re-expression.
8. Rationale for Wednesday stated; gold 2011-2021 H1 quality screen declared (flat in any year with a FAIL); the wider-bootstrap-interval is reporting only. 9. Null semantics sentence (bias kept), the leakage and turnover directions stated; lag exclusion superseded by derangement; gold permuted separately, stated.
10. FINAL slots and effective K (about 2 to 3) stated. 11. Stop rule: no gate edits after DEV; new families spend alpha. 12. Bootstrap blocks 13/26/52 and small-sample caveat at FINAL. 13. Commission corrected to 0.25 bp; spread-field assumption stated. 14. Conventions pinned (year of R_k, N_k, L in bars, sign 0, Sharpe sqrt 52, gold hour).
Not adopted as asked: obtaining the six swap values now (the owner has them, I do not; S1/S2 stay assumptions and the owner's table is a disclosed FINAL input); the reviewer's t-based p-value from n_eff (replaced by a null with real resolution instead).
