# Pre-registration R3-1: price-only time-series momentum on D1 data, six instruments (written 2026-10-31, FROZEN BEFORE ANY RUN)

GAMEPLAN section 8 item R3 and section 13 item 3, stage 1. **No momentum rule has been backtested on this data.** Nothing here touches live code, `sim.py` or `signals.py`.
DEV only: the code refuses any end after 2021-06-01 and any start on or after it. FINAL (2021-06-01 to 2025-04-30) is untouched; 2025-05 onward is not used. FINAL mode is disabled
unless the file `research/output/slow_momentum/FINAL_GO` exists, which only the owner creates. No `--force`. This file is committed locally before the first run; its sha256 is written into every output.
Revisions made before the first run (after an independent attack) are listed in "Log" at the end.

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
  the 50-point FX placeholder 2019-01..03 (GBPUSD) and 2020-05..2021-05 (handled by `dataset.add_clean_spread`: sentinel and zero spreads replaced by the median of clean recorded values of the same pair, year and UTC hour; used for COSTS only). **Primary = all years included. Reported alongside,
  descriptive only:** without 2016-2017; without GBPUSD 2019; without XAUUSD (this one is also a gate, G4); without 2020-05 onward (placeholder months); starting 2010 and 2014 (starting-date sensitivity).
* **Calendar of rebalances (common to all instruments, weekly):** for ISO week k, R_k = the broker-server midnight that ends the primary weekday (Wednesday), converted to UTC with `research.data.server_to_utc` (21:00 or 22:00 UTC). A single weekday is used; the other four weekdays are a gate (G3d).
* **Per instrument and rebalance k:** the signal uses the last complete D1 bar whose close time T satisfies T <= R_k, and only if T >= R_k - 3 days (otherwise the instrument is flat at k). **Execution: the open of the first H1 bar with time >= R_k**, and only if that bar starts within 72 hours of R_k
  (otherwise flat). The position set at k is held to the execution price of k+1; there is no intra-week action. If the execution price at k+1 is missing, that period's P&L for the instrument is 0 and it is flat after. The counts of flat and missing instrument-weeks are written to the output.
  For the Friday weekday the execution is the Sunday-evening open, so the weekend gap is inside the P&L (never skipped).
* **Return convention (USD account, constant equity base, additive weekly returns, no within-week drift of weights):** instrument return over a period from execution price P0 to P1: USD-quoted pairs and gold P1/P0 - 1; USD-base pairs (USDJPY, USDCAD) 1 - P0/P1 (a long position in USD notional earns (P1-P0)/P1).
  Prices are the recorded bid. Weekly P&L = sum over instruments of w x return, minus costs.

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

No other variant, window, band, threshold, ensemble, monthly rebalance or carry/value term is run or reported as a result. Monthly rebalancing is out of scope (the weekday offsets in G3d already cover the date sensitivity at weekly frequency).
**Sizing:** ex-ante volatility sigma_i,k = standard deviation of the last 60 D1 close-to-close log returns **using bars with T <= R_k only** (at least 40 required, else flat), times sqrt(260). Raw weight = s x min(3, 0.10 / sigma_i,k). Portfolio weight w_i,k = raw / N_k, N_k = number of instruments live
at k (5 before gold, 6 after): equal risk per instrument (stand-alone), no correlation adjustment, no portfolio vol target (Sharpe is scale-free; realised annual vol and the mean gross notional sum |w| are reported). Price-only value proxies are not built (default: left out); carry and rate-based value are impossible (no rate data).

## 4. Costs and financing (fixed)

* **Spread:** at each execution the position change pays half the recorded-or-imputed spread of the execution bar: cost = sum |w_i,k - w_i,k-1| x 0.5 x spread_price / P_exec, spread_price = `spread_clean_pts` x point. A round trip therefore pays one spread. The first H1 bar after the NY close is the rollover hour,
  whose recorded spread is the widest of the day; that is used as recorded (not a median), so costs are, if anything, high. Stress: spread x2.
* **Commission:** the live account pays $2.50 per lot per side; for a vol-targeted notional that is about 0.5 bp per side on a USD-quoted major. Charged as 0.5 bp x |change in weight| (gold 0.5 bp as well). Immaterial but stated.
* **Swap / rollover financing: unknown.** MT5 history carries no swap. The broker's long and short swap depend on the policy-rate differential (which moved from near 0 to 5 % over this sample) plus a broker mark-up, and can be a credit or a charge by direction. A holding strategy pays it every night
  it is invested, so it **could be material**: at 10 % stand-alone vol per instrument, a six-instrument book has a gross notional of roughly 1.0 to 1.4 times equity and a portfolio volatility of only about 4 to 6 %, so a charge of 1 % a year on gross notional is about 0.25 Sharpe units.
  Treatment: charge c x sum |w_i,k| x (calendar days held)/365 each period, **unsigned** (every position pays, ignoring the sign of the differential, which is not available): **S0 c = 0 (primary estimand), S1 c = 1.5 % a year, S2 c = 3.5 % a year.** S1 is about one broker mark-up plus an average adverse differential
  for FX, a little low for gold; S2 is a hostile case. The **break-even charge** (the c at which mean net P&L is zero) is reported for every variant, because the real number is unknown and may be better (carry-aligned positions can earn it) or worse. A verdict at S0 alone would be flattering and is never used alone.

## 5. Inference (fixed)

* **Per-variant net Sharpe with a 95 % interval:** circular block bootstrap of the weekly net P&L, resampling whole weeks of all instruments together (cross-sectional dependence kept), block 13 weeks (a quarter; look-backs create multi-week dependence in the P&L), repeated with block 26; the **wider** interval is reported;
  B = 5,000 per block length, seed 20261031. Also reported on the same resamples: ex-gold Sharpe, each instrument's stand-alone Sharpe, and the difference to the benchmark.
* **Benchmark Z (trivial):** BL = always long every instrument, same 60-day vol sizing, 1/N_k, same weekday, same costs and swap charges (long positions pay the same unsigned charge). Reported: SR_BL, SR_v - SR_BL with its bootstrap interval. BL on a no-carry price series is expected to be near zero for FX and strongly positive for gold, so it
  shows how much of any result is the gold bull market.
* **Null that replicates the whole selection procedure (shifted-sign null):** the sign path of each variant is moved in time against the returns, everything else recomputed. For a lag m (in weeks), each instrument's signal sequence over its own live span is rotated circularly by m, i.e. S'_i,k = S_i,(live-start + ((k - live-start - m) mod K_i)).
  Sizing, N_k, spreads, returns, execution and costs are recomputed from the shifted signs at the original dates; the same m applies to all instruments and all six variants. This keeps each sign path's persistence, flip rate (hence turnover), long/short bias, and the cross-instrument alignment of the signals, and removes the
  timing relation to the next period's return. Lags m = 52 ... (K_gold - 52) weeks (K_gold = the gold live span, about 540 weeks), **every integer lag, no randomness**, about 480 null replicates. For each lag all six variants are evaluated, giving a null distribution of SR_v^m for each v **and of the maximum over variants** (the selection statistic).
  Individual p_v = (1 + #{SR_v^m >= SR_v}) / (1 + M); selection-adjusted p_v,max = (1 + #{max_u SR_u^m >= SR_v}) / (1 + M). Because the sign paths are persistent, neighbouring lags are highly dependent (the effective number of independent draws is of the order of ten, not 480); the p-values are valid
  in the exchangeability sense but have an effective resolution near 0.1, and that is by design the honest granularity. A second, descriptive null with an independent lag per instrument (breaks cross-instrument alignment, uses a seeded sample of 2,000 draws) is reported; if it contradicts the primary one the result is called fragile.
* **Multiple testing:** one Holm family of K = 6 (the six variants), family alpha 0.05 (DEV), applied to the individual p_v. The selection-adjusted p_v,max (maximum over the six, the single-step equivalent) is a second requirement for the chosen candidate (G1). Both are nominal in the sense that this arm sits inside a campaign that has already
  made other looks at overlapping data (stage 0, the H1 and slow ML studies); the campaign-wide error is larger than 0.05 and is stated in the report, not corrected.

## 6. DEV rule that chooses the single frozen candidate

A variant is **eligible** only if ALL of these hold on the primary run (Wednesday, all years, S1 unless stated):
* **G1** Holm over the six individual p_v (S0 Sharpe against the shifted-sign null) rejects v at 0.05, AND p_v,max <= 0.05.
* **G2** SR_v(S1) > 0 (point) and SR_v(S0) > SR_BL(S0) (point).
* **G3 stability (all four):** (a) leave-one-year-out SR_v(S1) > 0 for every omitted calendar year; (b) S1 net P&L positive in at least 10 of the 18 calendar years 2003 to 2020 (the partial 2021 is reported); (c) SR_v(S1) > 0 in at least 2 of the 3 eras; (d) SR_v(S0) > 0 for at least 4 of the 5 rebalance weekdays (Mon to Fri; execution rule unchanged).
* **G4** ex-gold (FX5 only, same sizing with N_k = 5) SR_v(S1) > 0 (point): the result must not be gold alone.
* **G5** materiality: SR_v(S1) >= 0.30. Below this, FINAL could not speak (section 8) and the net return would not be worth the drawdown risk of an FTMO-style account.

**Selection:** among eligible variants take the one with the highest SR_v(S1); that is **the frozen candidate**, written to `research/output/slow_momentum_dev/frozen_candidate.json` together with this file's sha256. **If none is eligible there is no candidate and FINAL is not used.** Nothing is chosen by looking at FINAL; the cost, spread x2,
start-date, flagged-year and monthly/other-parameter views are descriptive and cannot add or remove eligibility.

## 7. What each outcome means

* **No eligible variant (the expected outcome, see section 8):** "no momentum configuration in this family is distinguishable from sign-free noise on 2003 to 2021-05 net of spread and a plausible financing charge". Not "momentum does not exist": it is "not detectable at DEV power", and a positive Sharpe whose interval includes 0 is called that, not rejected.
  Stop the family; no re-tuning of L, rebalance frequency or universe after seeing it except within the revision budget (section 9).
* **An eligible variant:** a DEV-selected hypothesis with a Sharpe optimistically biased by the selection (the max-null p partly corrects, the point estimate is not corrected), **not** a validated edge. It is a different strategy from the live SMC bot: it would need its own live code (position sizing in lots from ex-ante vol, weekly rebalancing,
  swap handling, a position cap and FTMO drawdown handling), its own risk review and a `reviewer` pass later. Nothing in this task builds or changes that.
* **FINAL (only if a candidate exists and the owner creates `FINAL_GO`):** one run of the single frozen candidate on 2021-06-01 to 2025-04-30 (about 204 weekly periods; the 4-month EURUSD 50-point placeholder and the XAUUSD 2021 zero spreads handled by the same sentinel-aware spread; look-back history from DEV bars), net Sharpe at S1 and S0, same bootstrap (block 13 and 26, the wider), same benchmark. One-sided alpha 0.025.
  **CONFIRMED** = the lower bound of the S1 net Sharpe (one-sided 97.5 %) > 0. **CONSISTENT BUT NOT CONFIRMED** = point SR(S1) > 0 and lower bound <= 0. **NOT REPLICATED** = point SR(S1) <= 0. No other candidate, no `--force`, no second look.

## 8. Power (stated before running; analytic, normal approximation SE(SR) = sqrt((1 + SR^2/2) x f / T) with T in years and f the variance inflation for autocorrelation, shown at 1.0 and 1.3)

| Window | T (years) | one-sided alpha | power at true SR 0.3 | 0.5 | 0.75 | 1.0 | SR for 80 % power |
|---|---|---|---|---|---|---|---|
| DEV 2003 to 2021-05, single test | 18.4 | 0.05 | 0.35 / 0.29 | 0.65 / 0.55 | 0.88 / 0.80 | 0.97 / 0.92 | 0.64 / 0.75 |
| DEV, first Holm step | 18.4 | 0.0083 | 0.13 / 0.10 | 0.35 / 0.27 | 0.67 / 0.54 | 0.87 / 0.75 | 0.89 / 1.08 |
| FINAL 2021-06 to 2025-04, one candidate | 3.9 | 0.025 | 0.08 / 0.07 | 0.15 / 0.13 | 0.26 / 0.21 | 0.36 / 0.29 | not reached (the normal approximation tops out near 0.6 at SR 2) |

Honest reading:
* **DEV is powered for a Sharpe of about 0.65 to 1.1, not for 0.3 to 0.5.** The shifted-sign null is wider than the iid standard error (persistent signs have few independent bets), so the real DEV power against the null is lower than the table. A true net Sharpe of 0.3 to 0.5 (typical of what the literature claims for such rules before costs, unverified) would pass G1 with probability
  about 10 to 35 %. The expected DEV outcome is therefore "no eligible variant" even if a modest edge exists; that is "not detectable", not "rejected".
* **FINAL has very low power: 7 to 15 % at a true Sharpe of 0.3 to 0.5, about 36 % at 1.0, and about 50 % only at a realised Sharpe near 1.4 to 1.9.** If the true Sharpe is 0.4, the probability that FINAL even shows a positive point estimate is about 78 % and that it reaches CONFIRMED about 10 %. So (i) CONFIRMED on FINAL needs a very large realised Sharpe and, given a DEV-selected
  candidate, is more likely a lucky window than a precisely measured edge; (ii) CONSISTENT BUT NOT CONFIRMED is the most probable outcome of a real modest edge and settles nothing; (iii) NOT REPLICATED happens about 22 % of the time even when the true Sharpe is 0.4, so it is weak evidence against. FINAL is the only unburnt window; the owner should decide whether to spend it on a test with this power (section 10).
* The DEV run reports the realised bootstrap standard error and the null standard deviation, so the table above can be checked against the data after the fact (a post-hoc check, not a revision).

## 9. Stop rule

* At most **3 revisions after the first DEV result** (a revision = any change to a rule, parameter, universe, gate or threshold). Each is logged below with date and reason, and the first result stays on record. Pre-run revisions (after the attack, before any run) are logged and do not count.
* FINAL: once, for one frozen candidate chosen by the rule in section 6, only with `FINAL_GO`. No candidate means no FINAL. A candidate that fails DEV in a later revision loses its freeze.
* Stop and report (do not iterate further) if: no variant is eligible after 3 revisions, or FINAL has been run.

## 10. Decisions the owner must make before any FINAL

1. Whether to spend the only unburnt window on a test with the power in section 8 (recommendation: only if a candidate has DEV net Sharpe at S1 near or above 0.6, otherwise prefer forward evidence or a better-powered design such as more instruments).
2. Whether S1 and S2 reflect the broker's actual swap table (the owner can read the MT5 symbol swap values for the six instruments; the real charge replaces the stress numbers in the FINAL report as a disclosed input, but never in the DEV selection).
3. Creating `research/output/slow_momentum/FINAL_GO` (only the owner).

## 11. Outputs and code (fixed names)

`research/slow_momentum.py` (library plus CLI `dev`, `final`), `research/tests/test_slow_momentum.py`, outputs in `research/output/slow_momentum_dev/` (`summary.json`, `variants.csv`, `weekly_pnl.csv`, `per_year.csv`, `per_instrument.csv`, `null_distribution.csv`, `sensitivity.csv`, `frozen_candidate.json` only if eligible, `prereg_sha256.txt`).
Seeds fixed; the DEV run is one deterministic run. Existing files are not edited.

## Log of changes after registration

(none yet)
