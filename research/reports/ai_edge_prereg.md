# AI-edge pre-registration: is there learnable structure in net-R outcomes on DEV? (written 2026-10-29)

Scope: classical ML on price data only (no LLM). Window: **DEV only**, decision bars 2014-03-01 to 2021-05-31 (out-of-fold
period 2016-01-01 to 2021-05-31). FINAL (2021-06-01 to 2025-04-30) is not read: `research/ai/dataset.py::load_bars` refuses
any end after 2021-06-01 and filters the parquet read at file level. 2025-05 onward is not used at all. No `--force`.
Code: `research/ai/` (tests `research/tests/test_ai_*.py`). Nothing here edits live code, `sim.py`, `signals.py` or `nulltest.py`.

## 0. Order of events and what has been seen (so nobody mistakes it for blind)

Before this document was written, on DEV only and without fitting any predictive model:

* Data inventory, autocorrelation, variance ratios, volatility clustering, hour and weekday profiles of range and spread,
  cost per R (`research/ai/inventory.py`, outputs `research/output/ai/inventory/`). Descriptive; no P/L of any strategy.
* **A data finding that shaped the cost model**: from 2020-05 (GBPUSD also 2019-01..03) the FX `spread` field is exactly 50 points
  (5 pips) on 14 to 93 % of M5 bars per month and 1 point on most of the rest (`spread_regimes.csv`). The label cost therefore
  replaces those bars by the median of clean recorded spreads of the same pair, year and UTC hour (`dataset.add_clean_spread`). The
  simulator-faithful spread (sentinel kept) is the alternative `LabelSpec.spread_col="spread_pts"`; it is used only in the
  sensitivity check of section 8. I could not verify with the terminal whether 50 is a real quote or a placeholder.
* The label distribution (`dataset_h1_summary.json`), computed pooled over long and short so no direction is favoured:
  mean net R -0.059, sd 1.57, share with net R > 0 29.0 %, exits TP 18.1 % / SL 67.1 % / TIME 3.4 % / Friday flatten 11.5 %,
  stop floor binds on 2.2 % of bars, median stop 22.3 pips, median first-order cost 0.051 R, mean average-uniqueness 0.085.
  The label spec (section 2) was fixed in code defaults before this was computed and was not changed afterwards.
* Power arithmetic for the label (`power.json`), also direction-neutral.
* NOT seen: any model score, any IC, AUC or log-loss of any feature against any outcome, any top-decile result.

Information from outside this work that the design uses: stage 0 (live SMC rules: skill +0.020 R, SE 0.020, inconclusive, estimated against
the mirrored null) and the lead's notes that the mirrored null in `nulltest.py` was biased (far LIMIT entries became far STOP entries;
corrected by a `reflect` null, stage-0 skill being redone). **This study uses no simulator null at all**: its evidence is out-of-fold
log-loss and AUC against the base rate and against a non-directional model, a block-permutation null on its own labels, and a
paired same-bar comparison of the chosen side against the unchosen side. Where a later stage uses the simulator, use the `reflect` null.

## 1. Question

Does a set of causal features of H1 decision bars carry information about the sign of the net-R outcome of a stateless
triple-barrier trade (3R target, ATR stop, real spread and commission, Friday flatten), **beyond what non-directional
information already explains**? Directional information is what a tradable edge needs; non-directional information (time of
day, volatility state, spread, stop geometry) can raise or lower the win probability of both sides at once and cannot pick a side.

## 2. Label (fixed)

`research/ai/labels.py::LabelSpec` defaults: decision at each H1 close T; market entry at the last M5 close, long pays the spread
(ask = bid + recorded spread), short enters at the bid; stop = max(10 pips, 1.5 x ATR14 of H1); take-profit = 3 x stop; the path is the
M5 bars from T onward with the simulator's rules (stop gap-through fills at the open, stop wins ties, short exits at ask); time barrier
48 h; Friday flatten at the close of the M5 bar ending 20:00 UTC; decisions at or after Friday 20:00 invalid; commission
2 x $2.50 per lot expressed in R; net R = gross R - commission R. Stateless: every bar is labelled for both sides, independent of
any other position. Right-censored labels (data ended) are dropped. Verified against `research/sim.py` on synthetic paths (the test
compares R and exit reason for 30+ random trades, tolerance 0.03 R). Not modelled: breakeven, lot rounding, JPY P&L conversion
(second order), validator rejections, margin. Spread used: `spread_clean_pts` (see section 0).
Binary target y = 1 if net R > 0 (that is TP or a profitable timed/flattened exit).

## 3. Features (fixed; `research/ai/features.py`, tests `test_ai_features.py`)

N (non-directional, identical for the long and short row of a bar): hour of T (sin, cos), weekday of T (sin, cos), hours to Friday flatten
(capped 48), ATR percentile over 1000 H1 bars, log(ATR14/ATR100), H4 ATR percentile, spread relative to its trailing median (recorded
values only, causal sentinel handling), a missing-spread flag, stop in ATR units, first-order cost over stop.
S (directional): z-scored H1 returns over 1, 4, 12, 24, 120 bars; distance to the 20- and 120-bar high and low in ATR units; H4 trend
(close minus EMA50 in ATR units) and 6-bar H4 return from H4 bars closed at T; the USD factor over 4 and 24 H1 bars (other four pairs, oriented
to USD). Short rows carry the mirrored features (returns negated, high/low distances swapped). Every feature has a truncation test and a scramble-of-
unclosed-bars test; a deliberately leaky mutation of the H4 timing makes four of those tests fail (checked once by hand).

## 4. Evaluation protocol (fixed)

* Rows: one long and one mirrored short row per (pair, bar): about 430,000 valid labels (5 pairs x 2 sides).
* Folds: expanding walk-forward, 11 test blocks (calendar half-years 2016H1 ... 2021H1), all pairs split by the same time cut. Training rows are
  those whose label **ended** at least 7 days before the block starts (purge by label end plus 7-day embargo; the longest label is 48 h).
* Training weights: average uniqueness (Lopez de Prado) per label, normalised to mean 1. Evaluation is unweighted per row, with
  **week-block bootstrap** (calendar weeks shared by all pairs) for every interval.
* Models (the only three settings ever fitted; no tuning on results): `LR` logistic L2, C = 0.05, standardised inputs; `HGB_S`
  sklearn HistGradientBoosting depth 2, 100 iterations, rate 0.05, min leaf 2000, L2 10; `HGB_M` depth 4, 200 iterations, rate 0.03, min leaf 1000, L2 10.
  For each: F = all features (S + N) and B1 = N features only, same learner, same folds. No seeds are searched (seed 0).
* scikit-learn 1.7.2 was installed on 2026-10-29 into the private directory `research/output/ai/pkgs` (`pip install --target`, `PIP_USER=0`, TMP/TEMP on A:); the project's
  site-packages and `requirements.txt` are untouched. Threads capped at 4.

## 5. Statistics and thresholds (fixed before any fit)

Primary: **D1 = mean over all out-of-fold rows of [logloss(B1) - logloss(F)]** (positive = the directional features help), per model setting.
Secondary (reported, not decision criteria except where stated): directional AUC (within a bar, among bars where exactly one side wins:
does p_long - p_short rank the winning side; B1 has exactly 0.5 by construction), pooled AUC of F and B1, D0 = logloss(base rate) - logloss(B1) (how much
non-directional state explains), the top-decile table of section 6, D1 by half-year block.

**"There is something to learn" = for at least one of the three model settings, both:**
 (C1) the week-block bootstrap interval of D1, at one-sided Holm-corrected level (alpha 0.05 over 3 settings: lower bound of the 96.7 % interval), is above 0; and
 (C2) the block-permutation null (section 7) puts the observed D1 above all B replicates **and** at z >= 2.4 against the null mean and sd (one-sided p about 0.008, Holm for 3 settings).
Nothing else counts. A directional AUC whose interval excludes 0.5 while C1/C2 fail is reported as "weak, not established".
**"Worth taking to the next stage" additionally requires** the top-decile gain below to have a lower interval bound above 0 under costs x 1.5; otherwise the
verdict is "learnable but not tradable at these costs".

## 6. Top-decile rule (descriptive, pre-defined)

For each bar the side with the larger predicted p is the candidate; it is traded if p clears the 90th percentile of the model's predictions on a random
60,000-row sample of the fold's training rows (no look at the test block). Reported: share traded; mean net R of the chosen side; **gain = chosen side minus
the average of the two sides at the same bars** (a paired, simulator-free direction effect); the same with costs x 1.5 (subtracting 0.5 x first-order cost). Week-block intervals.

## 7. Null that replicates the whole procedure

Per replicate: shift the S-feature block (all pairs together, same hour of the week) by a random whole number of weeks, at least 26, circularly in calendar time,
relative to the labels and N features; stack, refit F in all 11 folds, recompute D1, directional AUC and top-decile gain. B1 does not use S, so its predictions are
reused. This keeps label dynamics, volatility clustering, cross-pair structure, calendar structure and the N-label relationship intact and removes only the
directional information, including the cost of fitting extra features on noise. B = 100 for LR, 40 for HGB_S and HGB_M (time); a Holm decision therefore
uses the z-criterion plus "above every replicate". A machinery test shows that a planted signal is found, no signal gives D1 ~ 0 and directional AUC ~ 0.5, and the shift removes a planted signal.

## 8. Cheap-label feasibility study (Part 1c/1d, `predictability.py`)

Outcome: the next h H1 bars (h = 1, 4, 24) from the decision close, in pips; forward range likewise. Family: 13 directional features x 3 horizons = 39 Spearman IC
tests (Bonferroni two-sided 5 %: |z| > 3.2), 7 volatility features x 2 horizons = 14 IC tests, 2 models (LR, HGB_S) x 3 horizons x 2 feature sets out-of-fold direction
AUC. Intervals by week-block bootstrap. Then: long the top decile, short the bottom decile of the out-of-fold score; gross signed move against spread + 0.5 pip commission, in pips and in R of
a 1.5 ATR stop. No decision rule beyond reporting; it informs the effort plan only.
Sensitivity (run only if C1 and C2 are met, else not needed): repeat with `spread_col="spread_pts"` (sentinel kept) and on 2014-2019 only.

## 9. Stop rule and FINAL

* If C1 and C2 fail for all three settings: stop. Report the null result; no further model families on this label ("a model that cannot beat the strong simple baseline out of sample does not count"). Pointers for what remains: cost and stop-geometry work (non-directional), slow factors (daily/weekly), regime/volatility gating of a cheaper-cost rule.
* If they pass: the next study (not this one) is a test of the same protocol on a cost-efficient strategy family (wider stops / longer holds so that cost is a small fraction of R), not
  meta-labelling on the live SMC candidates (de-prioritised after the corrected stage 0, see the log), then freeze **one** model setting, one threshold rule and one label spec,
  and run **one** FINAL confirmation (2021-06-01 to 2025-04-30): out-of-sample D1 and top-decile gain under stressed costs (spread x 1.5, one-bar delay), alpha 0.025, trained on all DEV rows
  with label end before 2021-05-25, evaluated by the same week-block bootstrap. Not run here.
* At most one revision of any threshold or setting without new data; none after seeing a result.

## Log of changes after registration

* 2026-10-29, before any model was fitted: the lead reported that the corrected stage 0 (reflected null) gives KILL for the SMC signal (skill +0.006 R, SE 0.020, UB 0.044;
  working-sweep variant -0.004 R, SE 0.017). Consequence for this document: the follow-up in section 9 no longer proposes meta-labelling on SMC candidates; the question is whether *any* structure
  exists in raw price/spread/time features net of cost, and which holding horizon / stop distance makes cost small in R. The label, features, folds, models and thresholds are unchanged.
* 2026-10-29, after the run: no thresholds, settings, folds or label spec were changed. B was 100 (LR) and 40 (HGB_S, HGB_M) as registered. Result: C1 failed for all three settings (Holm lower bounds -5.8e-4, -5.2e-4, -1.9e-6); C2 passed for LR and HGB_M; verdict "not established". Post-hoc descriptive checks added after seeing the result and flagged as such in `ai_edge_approach.md`: by-half-year and by-pair stability, exclusion of 2019H2, by-year cheap-label gross pips, autocorrelation by half-year. The sensitivity runs of section 8 were not needed (criteria not met).
