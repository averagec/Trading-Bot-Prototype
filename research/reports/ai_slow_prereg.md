# Pre-registration A2': slow-horizon (H4 and D1) learnability study (written 2026-10-30; NOT yet run on real outcomes)

Part of GAMEPLAN section 13 (arm A2'). Same question and protocol as the H1 study (`ai_edge_prereg.md`, `ai_edge_approach.md`), moved to decision
horizons where cost is a small fraction of R. **No model has been fitted to real outcomes.** The code refuses to do so without a token that the lead sends after
reviewing this document (`python -m research.ai.slow_study --stage real --go LEAD_GO`). DEV only: every loader refuses an end after 2021-06-01
(`dataset.check_end`; the parquet read is filtered at file level); FINAL (2021-06-01 to 2025-04-30) is untouched; 2025-05 onward is not used. No `--force`.

## 0. What has been seen (so nobody mistakes this for blind)

* Everything in `ai_edge_approach.md`: the H1 learnability test (null result, carried by 2019H2), the cheap-label feasibility study (including that a 24-hour top/bottom-decile
  rule showed +3.6 pips gross, almost all of it in 2019), the cost-by-horizon table (M15 0.086 R, H1 0.047, H4 0.021, D1 0.0085), variance ratios, volatility clustering.
  **Hypothesis generation therefore saw 2016-2021 data**, which overlaps this study's 2014-2021 era; the horizons and features differ, but the era is not virgin.
* This round, direction-neutral and model-free: the label distributions of all eight datasets (pooled over long and short), per era; the maximum label duration; sample counts;
  H1-path against M5-path label agreement (section 2); and the planted-signal power simulation (section 7), which uses real outcomes only to build a synthetic feature of known strength (no real feature is used).
* **The stop multiples k = 1.0 and 1.5 were fixed in code (`slow.VARIANTS`) before any dataset was built.** The label statistics were looked at afterwards; they show that at D1 the 3R target is almost never reached
  (section 2). That finding did not change the registered variants; it is raised to the lead as decision 1.
* NOT seen: any model score, IC, AUC, log-loss or top-decile result of any feature against any outcome at H4 or D1.

## 1. Question

At decision horizons of 4 hours to a few days, do causal features of price, volatility, time and USD factor carry information about the sign of the **net-R** outcome of a stateless triple-barrier trade, beyond what
non-directional information (time, volatility state, spread, stop geometry) already explains, in more than one data era, and stably over years? Directional information is what an edge needs; the horizon is chosen
because cost in R falls from 0.047 (H1) to about 0.02 to 0.05 (H4) and 0.013 to 0.02 (D1) per trade (section 2).

## 2. Data, labels, costs (fixed)

* **Data**: five FX pairs (EURUSD, GBPUSD, USDJPY, AUDUSD, USDCAD; no XAUUSD). Decision bars from 2003-01-01 to 2021-05-31; 2000-2002 are indicator warm-up only (nominal 5-pip constant spreads, the 2002 H1 flag).
  The 2000-2013 H1/H4 history was screened by the lead (complete, consistent clock, no Saturday bars). H4 bars come from the H4 file (broker grid). **D1 is built from H1** (below). The barrier path is the **H1 bars** (M5 does not exist before 2013).
* **D1 rule**: group H1 bars by the broker SERVER date (`research.data.utc_to_server`: UTC+2 in winter, UTC+3 while US DST is in force). A D1 bar opens with its first H1 bar (21:00 or 22:00 UTC) and its close time T is the close of its last H1 bar;
  it is kept only if (i) it has at least 12 H1 bars and (ii) its last H1 bar closes at or after the next server midnight (a calendar rule, so a day in progress or an early holiday close is never a "closed" D1 bar, and a truncated data set gives a strict prefix).
  The week has five D1 bars; the Friday one closes at the weekly close and is never a decision (decisions at or after Friday 20:00 UTC are invalid). Tests: 24 bars per day and the right opening/closing hour across both 2024 US DST changes, OHLC equal to the H1 aggregate, prefix stability under truncation.
* **Label** (`labels.py`, generalised to a path bar length; the M5 version is unchanged and tested against `research/sim.py`): decision at the close T of the H4 or D1 bar; market entry at the close of the last H1 bar (long pays the recorded-or-imputed spread of that bar); stop = max(10 pips, k x ATR14 of the
  DECISION timeframe) (the floor never binds here: median stops 32 to 172 pips); take-profit = 3R; stop wins ties inside an H1 bar; gap-through stops fill at the bar open; Friday flatten at the close of the H1 bar ending 20:00 UTC;
  time barrier 7 calendar days (the flatten always pre-empts it except when a Friday has no bars, e.g. a holiday: longest observed label 7.2 days, 99th percentile 4.1 to 5.0 days); commission 2 x $2.50 per lot in R; both sides labelled independently of any position (stateless); net R = gross R - commission R.
  y = 1 if net R > 0. Uniqueness weights as before. Not modelled: breakeven, lot rounding, JPY P&L conversion, validator rejections.
* **Label resolution**: H1-path labels against M5-path labels (2014-2021, EURUSD and USDJPY, H4 and D1, k = 1.0; `output/ai/slow/h1_vs_m5_labels.json`): same exit reason in 99.8 to 100 % of labels, mean absolute difference 0.002 to 0.006 R (99th percentile 0.01 to 0.045 R). A synthetic test shows H1-path R is never above M5-path R for identical spreads (the stop-wins rule only lowers it).
* **Spread**: sentinel-aware clean spread (`dataset.add_clean_spread`): zero recorded spreads and the 50-point placeholder (`research.data.sentinel_mask`, **restricted to bars from 2018-01-01**, see note) are replaced by the median of clean recorded values of the same pair, year and UTC hour.
  **Note for the lead: `research.data.sentinel_mask` alone flags EVERY bar of 2000-2003 (the broker's nominal constant 50-point spread) as missing, because the month-share rule sees 100 % equal to 50; I wrap it with `dataset.sentinel_mask_era` (test included). Any use of `prepare_bars(sentinel_missing=True)` over the pre-2004 years would impute those years wrongly.**
  Before 2010 the spread field is a constant per year, not a record (EURUSD 5 pips 2000-03, 4 in 2004, 3 in 2005, 2 in 2006-09; USDJPY 5/4/3/3; USDCAD 5-6/4/3): **nominal costs**. They are used as recorded; results are reported by era and the top-decile net R is also stated at costs x 1.5 and x 2.
* **Eras (fixed)**: E1 2003-2009 (nominal constant spreads; out-of-fold from 2006), E2 2010-2013 (varying spreads, 9 to 11 points), E3 2014-2021-05 (recorded, with the sentinel period 2019 GBPUSD and 2020-05 onward handled).

Median cost in R by era and variant (spread + commission over the stop, direction-neutral; `label_summary.json`):

| Variant | stop (pips, median) | E1 2003-09 | E2 2010-13 | E3 2014-21 | TP share | Friday-flatten share | pooled mean net R | label sd |
|---|---|---|---|---|---|---|---|---|
| H4_k10 | 38 | 0.089 | 0.043 | 0.033 | 15.2 % | 21.5 % | -0.059 | 1.49 |
| H4_k15 | 57 | 0.059 | 0.029 | 0.022 | 8.4 % | 40.4 % | -0.035 | 1.30 |
| D1_k10 | 100 | 0.035 | 0.022 | 0.013 | 1.3 % | 69.1 % | -0.016 | 0.92 |
| D1_k15 | 150 | 0.023 | 0.015 | 0.008 | 0.2 % | 86.0 % | -0.012 | 0.66 |

The pooled mean net R of a direction-neutral trade is about minus its cost: no structural drag besides cost. **At D1 the 3R target is essentially unreachable inside a week (0.2 to 1.3 % of labels), so the D1 label is in practice "profit or loss at the Friday flatten, with a stop"**, a weekly-sign task; H4 keeps a live target (8 to 15 %).

## 3. Variants: K = 4 (the confirmatory family)

| Name | Decision bar | k (stop = k x ATR14) | target | why |
|---|---|---|---|---|
| H4_k10 | H4 | 1.0 | 3R = 3 ATR | tight stop; the 3 ATR target is reachable in the holding window; highest cost in R of the four |
| H4_k15 | H4 | 1.5 | 3R = 4.5 ATR | same geometry in ATR units as the H1 study (comparability); lower cost |
| D1_k10 | D1 | 1.0 | 3R = 3 ATR | tight stop; the most informative of the D1 pair if any target is reachable |
| D1_k15 | D1 | 1.5 | 3R = 4.5 ATR | H1-study geometry; cost 0.008 to 0.023 R |

k >= 2 puts the target beyond 6 ATR, which no hold of at most 4 days reaches; k < 1 makes the stop smaller than one typical bar range. The time barrier follows the horizon (7 days; the Friday flatten ends every ordinary label by Friday 20:00).
Two sensitivity datasets per decision timeframe without Friday flatten (14-day barrier, weekend holds with gap fills; names `*_nf`) are built and, if the lead wants, run descriptively with embargo 21 days: **S1, not part of the family, no decision rests on it**.

## 4. Features (`slow.py`; fixed; causal)

N (identical for the long and short row): hour of T (sin, cos; zero at D1 where the hour only encodes DST), weekday (sin, cos), hours to the Friday flatten (cap 120), ATR percentile (H4 1000 bars, D1 500), log(ATR14/ATR100), higher-timeframe ATR percentile (H4: D1 ATR; D1: 250-bar),
spread relative to its trailing 240-H1 median (recorded values only, causal sentinel rule from 2018), missing-spread flag, stop in ATR units, first-order cost over stop.
S (directional): z-scored returns over five horizons (H4: 1, 6, 18, 42, 126 bars = 4 h, 1 d, 3 d, 7 d, 21 d; D1: 1, 5, 10, 20, 60 days), distance to the 20- and 120-bar high and low in ATR units, own-timeframe trend (close minus EMA50 in ATR units),
higher-timeframe trend (H4: D1 close minus D1 EMA50 from D1 bars closed at T; D1: close minus EMA200), USD factor over two horizons (H4: 1 and 5 days; D1: 5 and 20 days; other four pairs oriented to USD). Short rows carry mirrored features. The live SMC signals are not used.
No-look-ahead tests (synthetic broker-clock data): truncation (cut inside a bar; features at all decisions before the cut identical, including D1 built from H1 and H4 context from D1), scrambling every unclosed bar in H1 and H4, a test that the scramble really moves later features, mirror involution, labels strictly after the decision.

## 5. Protocol (fixed)

* **Rows**: a long and a mirrored short row per (pair, decision bar); rows with a missing feature or an invalid/censored label are dropped. Primary model: **logistic regression, L2, C = 0.05, standardised, uniqueness weights (the only member of the confirmatory family)**;
  descriptive only, no confirmatory role: `HGB_S_slow` (depth 2, 100 iterations, rate 0.05, min leaf 200) and `HGB_M_slow` (depth 3, 150, 0.03, min leaf 100). F = S + N features, B1 = N features only, same learner, same folds. Seed 0; no tuning on results.
* **Folds**: expanding walk-forward, 31 half-year test blocks from 2006-01-01 to 2021-05-31, all pairs cut at the same time (first fold trains on 2003-2005). **Purge**: a training row's label must have ended at least **14 days** before the block starts (longest observed label 7.2 days, 99th percentile 5.0; the
  `_nf` variants: 21 days against a longest 17.2). 14 days is twice the longest label. Feature look-backs (up to 126 H4 bars = 21 days, 120 D1 bars, EMA200) use only past data and cannot leak the future; they make train and test inputs adjacent, which the embargo does not remove and which is the accepted situation for trailing features.
  Half-year blocks hold about 1,000 (D1) to 7,000 (H4) rows each; annual stability (section 6) uses calendar years because half-year D1 blocks are too noisy to be judged one by one.
* **Uncertainty**: calendar-**week** block bootstrap (all pairs share the week). Justified because the Friday flatten ends every ordinary label by the same Friday, so labels of different weeks do not overlap (the exception is a Friday without bars, 0.x % of labels).
  Because slow features are serially correlated across weeks, **a 4-week-block interval is computed as well and must agree** (C1 below). Bootstrap sizes: 4,000 for the decision intervals, 2,000 for descriptive ones.
* **Primary statistic** (per variant): D1 = mean over all out-of-fold rows of [logloss(B1) - logloss(F)], i.e. the gain of the directional features over the non-directional model. Secondary: directional AUC (within a bar: does p_long - p_short rank the winning side), pooled AUC, top-decile gain over the unchosen side
  (the side with the larger p, traded if p clears the 90th percentile of training-fold predictions; paired with the average of the two sides at the same bars: simulator-free), the top-decile chosen-side net R at costs x1, x1.5, x2 (extra cost subtracted as a multiple of the first-order cost), by era and by year.
* **Null**: whole-pipeline shifted-feature permutation (S features moved by a random whole number of weeks, at least 26, same shift for all pairs, same hour of the week; F refit in all folds; B1 reused), B = 100 per variant. Machinery tested with planted signals.
* **No simulator null is used anywhere** in this study (the stage-0 mirrored null was biased; where a later stage needs one, use the `reflect` geometry).

## 6. Decision rules (fixed before any fit)

For each variant (primary model), "**there is something to learn**" requires ALL of:
* **C1**: Holm over the four variants (step-down, family alpha 0.05, one-sided bootstrap p-values of D1 <= 0 from the week-block bootstrap) rejects, AND the 4-week-block lower bound at the single-test level 0.05/4 is above 0.
* **C2**: the observed D1 exceeds all 100 permutation replicates and sits at z >= 2.5 against the null mean and sd.
* **S (stability)**: D1 is positive in at least **70 % of calendar years** (16 years 2006-2021, the last one partial; 12 of 16) AND the leave-one-year-out D1 is above 0 for **every** single omitted year (the pre-declared "with and without any single block" check; also reported for the directional AUC). The 2019 episode of the H1 study makes this binding by design.
* **F (falsifier)**: the pooled directional AUC (point estimate) is at least **0.52 in at least two of the three eras** (E1, E2, E3). If fewer than two eras reach it, that variant is falsified; if all four variants are falsified the whole slow-horizon family stops (no further label, horizon or model variants on OHLC + spread + time + USD features without new data or a new mechanism).
"**Worth taking to the next stage**" additionally requires the top-decile chosen-side net R to have a lower 95 % bound above 0 at costs x1.5 (x2 reported). Everything else (HGB models, `_nf` sensitivity, era tables, by-year tables) is descriptive and cannot rescue or sink a variant.
A directional AUC with an interval above 0.5 without C1 to S is reported as "weak, not established".

## 7. Power (stated before running)

Counts (out of fold = decisions from 2006-01-01; `output/ai/slow/counts.json`; labels, not models):

| Variant | stacked rows | out-of-fold rows | out-of-fold weeks | bars with exactly one side winning (the bars that carry directional information) | sum of uniqueness weights (OOF) | lower bound of the SE of directional AUC if those bars were independent |
|---|---|---|---|---|---|---|
| H4_k10 | 274,579 | 229,245 | 805 | 71,162 of 114,615 (62 %) | 40,050 | 0.0022 |
| H4_k15 | 274,568 | 229,234 | 805 | 85,457 (75 %) | 27,431 | 0.0020 |
| D1_k10 | 37,768 | 31,522 | 804 | 14,553 of 15,761 (92 %) | 12,190 | 0.0048 |
| D1_k15 | 37,768 | 31,522 | 804 | 15,277 (97 %) | 11,280 | 0.0047 |

The bars are not independent: five correlated pairs, a shared USD factor, overlapping labels within a week and serially correlated slow features. The honest effective size is closer to the number of weeks (about 800) times a few than to the row count. So the power was **simulated with the real design**:
real decision times, pairs, weeks, label end times, weights and the real outcomes of both sides (hence their real dependence); all features replaced by noise except one directional feature planted from the outcomes (`slow_power.py`; the within-bar directional AUC of the planted feature is
Phi(sqrt(2) x gamma)); then the real pipeline (logistic model, folds, purge, blocks, bootstraps) counts how often C1, S and F fire. 12 replications per cell; C2 (permutation) is not simulated and could only lower the rates. "Realised AUC" is what the model achieves out of fold (below the planted value because of shrinkage and estimation).

Simulated power (12 replications per cell; planted within-bar directional AUC -> realised out-of-fold AUC; probabilities are shares of replications; C2 not simulated; gain = median top-decile gain over the unchosen side in R per trade):

| Variant | planted -> realised AUC | P(AUC interval above 0.5) | P(C1) | P(S) | P(F) | P(C1 and S and F) | top-decile gain (R) |
|---|---|---|---|---|---|---|---|
| H4_k10 | 0.50 -> 0.500 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.002 |
| H4_k10 | 0.52 -> 0.519 | 1.00 | 0.83 | 0.83 | 0.25 | 0.25 | 0.050 |
| H4_k10 | 0.54 -> 0.540 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 0.109 |
| H4_k10 | 0.56 -> 0.560 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 0.168 |
| H4_k15 | 0.50 -> 0.500 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.002 |
| H4_k15 | 0.52 -> 0.519 | 1.00 | 1.00 | 1.00 | 0.25 | 0.25 | 0.045 |
| H4_k15 | 0.54 -> 0.540 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 0.105 |
| H4_k15 | 0.56 -> 0.560 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 0.158 |
| D1_k10 | 0.50 -> 0.498 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.001 |
| D1_k10 | 0.52 -> 0.514 | 0.83 | 0.00 | 0.00 | 0.17 | 0.00 | 0.024 |
| D1_k10 | 0.54 -> 0.535 | 1.00 | 0.75 | 0.42 | 1.00 | 0.25 | 0.075 |
| D1_k10 | 0.56 -> 0.556 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 0.124 |
| D1_k15 | 0.50 -> 0.499 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.001 |
| D1_k15 | 0.52 -> 0.513 | 0.67 | 0.00 | 0.00 | 0.08 | 0.00 | 0.021 |
| D1_k15 | 0.54 -> 0.535 | 1.00 | 0.67 | 0.67 | 1.00 | 0.50 | 0.053 |
| D1_k15 | 0.56 -> 0.556 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 0.086 |

Reading (honest):
* **False positives**: at planted AUC 0.50 none of C1, S, F fired and the AUC interval excluded 0.5 in 0 of 12 (cells with 12 replications cannot show rates below about 0.2; the permutation null C2 adds protection in the real run).
* **H4 is well powered**: a realised directional AUC of 0.519 is already seen by the interval (100 %) and by C1 and S (83 to 100 %), at a top-decile gain of about 0.05 R. **The binding criterion at H4 is the falsifier F** (point estimate at least 0.52 in two of three eras): at a true 0.519 it fires only 25 % of the time, at 0.54 always. So at H4 the full pass has about 80 % power from a realised AUC of about **0.53 to 0.54 (gain about 0.08 to 0.11 R)**, while an effect of 0.51 to 0.52 would typically be called "weak, not established" or falsified.
* **D1 is weak**: about 960 weeks and 15,000 discordant bars, but C1 and S need a realised AUC of about **0.545 to 0.55 (gain about 0.09 to 0.12 R per trade)**; at 0.535 the full pass fires in 25 to 50 % of replications, at 0.514 never. **D1 cannot detect an edge below roughly 0.08 to 0.10 R per trade over the unchosen side**, although at a cost of 0.008 to 0.02 R an edge of 0.03 to 0.05 R would already be attractive. If that is unattractive, D1 should be run as descriptive only or pooled with more history/pairs; this is decision 4 for the lead.
* Effect sizes of this kind are large for FX; a real effect of 0.51 to 0.52 AUC is more plausible and falls below the D1 MDE and around the H4 falsifier. The study is therefore decisive for "large effect or none", not for small effects.

## 8. Descriptive outputs promised (all reported, none decisive)

Per variant: D1 and directional AUC by era and by year with leave-one-year-out versions; the 4-week interval; HGB_S_slow and HGB_M_slow results; top-decile gain and net R by era; costs x1.5 and x2; `_nf` sensitivity if run; TP/SL/flatten mix of the selected trades; the share of D1 improvement contributed by each year.

## 9. FINAL (specified, not run)

If and only if a variant is "worth taking to the next stage": freeze the variant, the primary model setting, the threshold rule and the label spec; train on all DEV rows whose labels ended before 2021-05-25; apply to 2021-06-01 to 2025-04-30 once (about 3.9 years: at D1 roughly 800 bars per pair), with the sentinel-aware spread applied identically
(EURUSD is 85 to 87 % sentinel in 2021-06..09) and the same week-block bootstrap. Success = lower bound of the top-decile gain under costs x1.5, spread x1.5 and a one-bar delay above 0 at alpha 0.025 (at most two candidates), plus D1 positive in at least 3 of the 4 calendar years. One run, no `--force`. A model that cannot beat the non-directional baseline robustly does not count.

## 10. Compute and code

`research/ai/slow.py` (D1 builder, features, dataset), `slow_study.py` (driver with the go-token guard), `slow_power.py`, `slow_counts.py`, `slow_check.py`; generalised `labels.py` (path bar length), `build.stack_sides`, `learnability.shift_directional`, `models.SETTINGS`.
Build: seconds per variant. Real stage: logistic fits seconds (D1) to under a minute (H4) per variant; permutation B = 100: minutes (D1) to about an hour (H4). Threads capped at 4. Tests: `research/tests/test_ai_slow.py` (22 tests) and the 39 earlier AI tests, 61 in all, passing; no live code, `sim.py`, `signals.py` or `nulltest.py` was touched.

## 11. Decisions requested from the lead before a go

1. **D1 label and the Friday flatten.** With the flatten, 86 % (k 1.5) and 69 % (k 1.0) of D1 labels end at the Friday close and the 3R target is hit in 0.2 to 1.3 %; D1 is a weekly-sign task, not a 3R-target task. Options: (a) keep as registered (consistent with the live framework and the H1 study) and report the `_nf` variants descriptively; (b) make the no-flatten, 14-day-barrier D1 labels primary (TP 3 to 12 %, time exits 27 to 53 %, weekend gap risk priced), which is closer to how a slow strategy would trade but is not what the live bot does. My recommendation: (a).
2. **Model family.** Confirmatory = logistic only (family of 4). Adding the shallow boosted model as a second confirmatory setting doubles the family to 8 (Holm alpha 0.05/8 for the smallest p) and costs power; the H1 study's boosted models added no robust signal. Recommendation: keep logistic only.
3. **Falsifier threshold.** Point estimate >= 0.52 in two of three eras is the binding rule at H4 (power 25 % at a true realised 0.519, 100 % at 0.54). That is the lead's specification; I kept it. Alternatives: use the lower end of the era interval, or 0.51. Changing it now (before any fit) costs nothing; after a fit it must not change.
4. **D1 power.** The D1 pair detects only a realised AUC of about 0.545 to 0.55 (0.09 to 0.12 R over the unchosen side). Run D1 as confirmatory anyway (a clear effect would show), or demote it to descriptive and run only H4 (family of 2, Holm 0.05/2)?
5. **Pre-2010 costs.** Registered as recorded (nominal constants, 2 to 5 pips), E1 reported on its own, x1.5 and x2 stress for the top-decile net R. If you prefer the base case at x1.5 for E1, say so before the run.
6. **Data fix on your side**: `research.data.sentinel_mask` flags the whole 2000-2003 constant spread as missing (the month-share rule sees 100 % equal to 50). Harmless for the 2013+ simulator runs, wrong if `prepare_bars(sentinel_missing=True)` is used on H1/H4 from 2000-2003. A one-line restriction to bars from 2018 (as `dataset.sentinel_mask_era` does) would fix it.
7. **Sequence of the real run (after your go)**: `--stage real` for the four variants (about 10 minutes), `--stage perm --n-perm 100` (about an hour for H4), `--stage report`; the verdicts follow section 6 mechanically.

## Log of changes after registration

2026-10-30, BEFORE any real fit (lead's answers to section 11):
1. D1 labels: (a) kept as registered (Friday flatten); `_nf` descriptive only.
2. Confirmatory family = logistic only, K = 4 (Holm 0.05/4). HGB models descriptive.
3. Falsifier: unchanged, point AUC >= 0.52 in at least two of three eras.
4. D1 stays in the confirmatory family. Pre-stated reading: a D1 null means "no effect larger than about 0.09 to 0.12 R", NOT "no edge".
5. Pre-2010 costs: as recorded; x1.5 and x2 stress reported.
6. `research.data.sentinel_mask` now ignores bars before 2018-01-01 (`SENTINEL_FROM`); test added (`test_sentinel_missing.py`). No effect on M5 runs from 2013 on that reached the placeholder months.
7. Real run order: `--stage real`, then `--stage perm --n-perm 100`, then `--stage report`; verdicts mechanical per section 6.
