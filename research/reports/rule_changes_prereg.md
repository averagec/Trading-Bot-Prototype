# Pre-registration: rule / risk-framework changes (written before any variant was simulated)

Written 2026-10-26, before running any variant of `SimConfig`. Only the *unmodified base* (`base_faithful`, 825 trades,
-0.205 R) had been run, by an earlier agent. Everything below is fixed now; any later deviation is logged as a deviation.

## 0. Honest disclosure of what is already known (this limits how "blind" anything can be)

* The earlier report already showed, on the **full** 16 months (so including my holdout): weekend-gap trades sum -35 R,
  late (>=20:00 UTC) entries average -0.57 R, `no_breakeven` -0.142 R (OOS +0.089), `rr_2` -0.218 R (OOS -0.140).
  H1, H2 and H3 below were *motivated by those full-sample diagnostics*, so the choice of **which rules to test** is not
  independent of the holdout. The parameter values (20:30, 20:00, 0.25 %) are fixed from market-structure reasoning, not tuned.
  For H3 the holdout is **not blind** (the earlier OOS number is already known); I treat H3 holdout confirmation as weak evidence.
* After fixing the split below (by calendar only) I printed base-only descriptive counts by window: base trades dev 592 /
  holdout 233; Sunday-reopen closes dev 18 (-30.1 R) / holdout 8 (-5.2 R); entries >=20:00 dev 58 (-0.74 R avg) / holdout 14 (+0.13 R avg).
  Consequence, stated now: the **holdout contains few of the events H1/H2 target**, so it has little power for them.
* Mean realised risk of the base is 0.276 % of balance (median 0.25 %, range 0.03-0.5 %).

## 1. Data split (chronological, fixed)

* Data: 6 pairs, M5, first trade 2025-06-02, last 2026-10-23. Simulations always run over the full history (the account
  is stateful and causal, so later data cannot affect earlier trades). Trades are assigned to a window by `open_T`.
* **DEV** (may be looked at freely while developing): `open_T < 2026-05-01 00:00 UTC` (= 65 % of the calendar span).
* **HOLDOUT** (locked): `open_T >= 2026-05-01`. Reported only for finalists, once each, via an explicit `--unseal` step that is logged.
  During development the code only prints DEV-window statistics.
* **Walk-forward folds** (fixed rules, no parameter is fitted, so "walk-forward" = out-of-sample stability of each fixed rule
  per fold, difference vs base): F1 2025-06-02..09-30, F2 2025-10-01..12-31, F3 2026-01-01..04-30 (all DEV);
  F4 2026-05-01..07-31, F5 2026-08-01..10-26 (HOLDOUT; shown only after unsealing).

## 2. Pre-registered hypotheses (exact parameters)

Base = `SimConfig()` defaults (live behaviour). Class D = "drag" hypothesis (expected to raise expectancy in R for a zero-skill
signal). Class T = "tail" hypothesis (expected to cut tail risk; expectancy only needs to be non-inferior).

| ID | Change | Exact value | Economic reason | Expected effect (prior) |
|---|---|---|---|---|
| H1 (T) | Friday flatten | at the first M5 cycle with time >= **Fri 20:30 UTC**: close every position at the bar-close bid/ask, cancel all pending orders; no new orders or fills on Friday from 20:30 | Weekend gaps jump the stop (up to -19.7 R). With random direction a gap has mean ~0, so this should cut **variance/tail**, not expectancy; flatten adds one extra spread and truncates winners | expectancy ~0 (within +/-0.02 R), worst-trade R and DD better |
| H2 (D) | No late entries | no new orders and no pending fills at cycle time >= **20:00 UTC** (pendings cancelled) on any day | Rollover-hour spreads/thin liquidity; 72 late entries averaged -0.57 R | +0.01..0.04 R if the late loss is structural |
| H3 (D) | Breakeven removed | `breakeven=False` | For a martingale price a stop-move cannot add expectancy; it only adds BE-buffer/spread/commission leakage (decompose.py: ~0.035 R) | +0.03..0.06 R |
| H4 (D?) | LIMIT-only | skip every candidate whose subtype is STOP (the LIMIT->STOP conversion when price is already on the wrong side of the FVG midpoint) | STOP entries buy strength / sell weakness at a worse price than the signal's intended level; ~46 % of fills | ~0 framework effect expected (roughly half the trades; any difference is subtype-specific skill = noise-prone) |
| H5 (T) | Fixed true risk | `fixed_risk_pct=0.25` (ATR and regime scaling ignored; risk = 0.25 % of balance for every trade) | Removes the 0.03-0.5 % dispersion so the worst case is bounded and known; keeps median risk | expectancy in R unchanged (non-inferior); DD/tail per trade bounded. Dollar risk ~9 % below base mean (0.276 %), so some DD improvement is just smaller size - reported but not credited |
| H6 (D+T) | Composite C = H1 + H2 + H3 | all three together, exact values as above | The three rules aimed at structural drag/tail, specified in advance so no data-driven combination is needed | additive in drag; tail from H1 |

Declared overlay (not a hypothesis test; counted in the variant log): **O1 = H6 + H5** (composite with fixed 0.25 % risk), used only for the
FTMO/drawdown comparison.

Pre-declared exploratory variants (DEV only, logged and counted in the multiple-testing family, **cannot be finalists**):
E1 breakeven trigger 1.5 R; E2 breakeven trigger 2.0 R; E3 H1 plus no new entries on Friday from 15:00 UTC; E4 `rr_ratio=2` (re-run; known worse).
Not tested: trailing stop (more degrees of freedom than I can pay for in 16 months), lower R:R beyond E4, per-pair rules, any signal change.

Family size so far: base + 6 hypotheses + 4 exploratory + 1 overlay = **11 variants compared against base** (more are added to the log if I run more; the log is the count).

## 3. Metrics and tests

Per variant and window: trades, expectancy R (+ trade-level bootstrap 95 % CI), PF, max equity drawdown (window-rebased), worst single-trade R,
1st-percentile trade R, Sunday-reopen close R (trades with close on Sunday), late-entry R, FLAT-exit R.

**Primary comparison (actual signals):** paired difference in expectancy, delta = E[R | variant] - E[R | base] per trade, inference by a
**paired weekly block bootstrap** (blocks = ISO week of `open_T`, 10 000 resamples, seed 12345; trade sets differ across variants, so the ratio sum(R)/n is
recomputed inside each resample). One-sided p for "delta > 0" from the centred bootstrap.

**Framework vs skill:** for every variant the **random-direction null** (`nulltest.mirror`, flips seeds 0..39, the *same* 40 flip vectors for every variant,
same candidates and costs). The framework effect = mean over seeds of (null_variant - null_base) per window, with SE across seeds (paired).
It is conditional on this price history. Skill component = actual - null for the same rule set.

**Multiple testing:** (i) Holm and Bonferroni over every variant in the log (N = family size, DEV window, one-sided p from the primary comparison);
(ii) White's reality check (studentised max of delta over the family, same bootstrap draws). Null-framework p-values get the same Holm treatment.
What does not survive is reported as not surviving.

## 4. Decision rules (mechanical, fixed now)

**DEV screen** (decides finalists; H6 is always a finalist):
* Class D (H2, H3, H4): advance iff DEV actual delta > 0 **and** DEV null-framework delta > 0 with z > 2.
* Class T (H1, H5): advance iff DEV actual delta >= -0.02 R **and** DEV null delta >= -0.02 R **and** the tail metric improves
  (H1: worst-trade R and Sunday-close R better in actual, mean null max-DD better; H5: null mean max-DD better).
* Finalists = H6 plus those that advance. k = number of finalists.

**HOLDOUT (once per finalist):**
* Class D / H6: "confirmed" iff holdout actual delta > 0 with one-sided paired p < 0.05/k (Bonferroni) **and** holdout null delta > 0 (z > 2);
  "directionally consistent" iff delta > 0 but p misses; otherwise "failed".
* Class T: "consistent" iff holdout expectancy delta >= -0.02 R and worst-trade R / max-DD not worse.
* Power note will be computed: the minimum detectable delta (80 % power, alpha 0.05 one-sided) from the paired SE.

**Adoption standard:** a rule is called trustworthy only if (a) the mechanism shows in the zero-skill null with high z,
(b) holdout is not contradicting (delta >= 0, tails not worse), (c) there is a stated reason independent of the data. Passing these makes it a
*risk-control/cost-reduction* rule. It does **not** make the strategy profitable: that needs real expectancy > 0 with CI above zero on the holdout, which is not expected.

Prior expectation recorded now: the sum of all plausible improvements is about +0.10 R, leaving roughly -0.07..-0.10 R/trade of residual drag (commission 0.045 R + the
pessimistic same-bar assumption ~0.05 R, neither of which these rules touch) on a zero-skill signal.

## 5. Implementation commitments

New fields on `SimConfig`, all defaulting to off/None so base results are bit-identical: `friday_flatten_utc`, `friday_no_entry_from_utc`, `no_entry_from_utc`,
`skip_stop_entries`, `fixed_risk_pct`. Tests in `research/tests/test_rule_changes.py`. Regression: pytest + `run --tag regress_check --no-mc` must equal
`output/v1/base_faithful/summary.json`. Every simulation run goes through `research/experiments.py`, which appends a row to a variant log
(`research/output/rc/variant_log.csv`); the total count in the final report is taken from that file.

---

## 6. Deviations and post-hoc additions (appended 2026-10-26, AFTER the DEV run, BEFORE any holdout look)

**What happened.** The DEV run of the 11 pre-registered/declared variants showed that H1 (Friday flatten) has a large *positive* effect in the
random-direction null (+0.046 R per trade, z = 12.7), contradicting my prior of ~0 (a gap has mean ~0 for random direction). I decomposed it
on DEV data only (no holdout statistic was computed). Findings:
* In the null, 38 trades per seed span a weekend; base mean R of those = -0.31 vs +0.32 if flattened. Everything else is identical.
* Cause: an asymmetry in `sim.py::_bar_exit` (pre-existing, not mine): SL gaps through to the gapped open (e.g. -19.7 R), but a TP is filled at the
  TP price even if the bar opens far beyond it (a limit order would fill at the better open). Under random direction a large weekend gap therefore
  costs the loser its full gap but pays the winner only +3 R. Reopen-bar spreads (median 4.8 pips EURUSD, 10 pips USDJPY) amplify it. So the H1
  "framework gain" in the null is partly a simulator convention, and the DEV p-values for H1/H6 are inflated by it.
* The real gaps in DEV are genuine in the data (USDJPY +190 pips, AUDUSD -84 pips); the sizes are not an artifact, only the one-sided payoff.

**Added (post-hoc, counted in the family, never used to select anything):** `SimConfig.tp_gap_fill` (default False = original convention). Sensitivity variants
S1 = base with gap fill, S2 = H1, S3 = H2, S4 = H3, S5 = H6, each with gap fill, compared to S1 (not to `base`). Family size for correction becomes
**16** (11 + 5). Finalists are still selected ONLY by the pre-registered screen on the original (non-gap-fill) DEV results; S-variants are unsealed
together with their parent finalists purely as a robustness read, and conclusions about expectancy for H1/H6 will be taken from the gap-fill-corrected null.

**Other caveats recorded now:**
* H1, H2, H3 were chosen after full-sample diagnostics that include DEV, so the DEV Holm p-values are not clean out-of-sample evidence; the HOLDOUT (and for H3 not even that) is the only
  quasi-clean test, and it contains few of the targeted events. The family of rules I *could* have picked from those diagnostics is larger than 11.
* H5's DD credit is computed per unit of average risk (decision rule's "not credited" clause); H5 cannot change expectancy in R.

**Second post-hoc addition (before any holdout look).** Decomposing the gap-fill-corrected null showed H1's remaining null gain (+0.033 R, z 5.9) comes from
weekend-held SELL positions being stopped at the Sunday reopen (null SELL mean R -1.8 vs BUY +0.07 over the same weekends). Cause: the reopen M5 bar carries its
opening-tick spread (median 4.8 pips EURUSD, 10 pips USDJPY vs 0.2 normal) and `_bar_exit` adds that spread to the whole bar's high for shorts (ask = high + spread),
which is probably pessimistic. Added `SimConfig.reopen_spread_zero` (default False) and sensitivity variants S6 = base, S7 = H1, S8 = H6 each with BOTH
`tp_gap_fill=True` and `reopen_spread_zero=True` (the most generous treatment of holding through the weekend), compared to S6. This bounds how much of H1's
expectancy credit is a modelling convention. Family size for correction: **18 comparisons** (11 + 4 + 2... counted exactly in the report from `variant_log.csv`).
