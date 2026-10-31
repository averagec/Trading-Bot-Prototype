# ATR-floor minimum stop: stage-1 pre-registration (written 2026-10-31, version 2, still before any run of this experiment)

Prereg-SHA256: af3a2f966e5ff68f8665d7fe17ca9371f45e468b0373652f08eb6112d0510b25

How the hash is made: take this file, normalise line endings to LF, replace the single line that starts with `Prereg-SHA256:` by
`Prereg-SHA256: PENDING`, and take the sha256 of the UTF-8 bytes (helper in `research/atr_floor_confirm.py`, command `prereg-hash`). Each version's hash is recorded in the
change log of the next version. A local, unpushed git commit can be rewritten, so it is not an external time-stamp: **before creating `FINAL_GO` the owner stores the hash of the final
version outside the repository** (for example in a message to themselves) and `FINAL_GO` must contain it.

Protocol: `test-strategy-idea` skill, `GAMEPLAN.md` sections 2, 3, 13, `research/reports/quant_methods_prereg.md`.
Hypothesis owner: the quant review (`research/reports/quant_loss_risk_review.md`, section 2). This is a **cost / structure hypothesis, not a signal claim**.

## 0. What has been seen (so nobody mistakes any of it for blind)

DEV = 2014-01-01 to 2021-06-01 (exclusive). FINAL = 2021-06-01 to 2025-05-01 (about 47 months, 2021-06 to 2025-04 inclusive). Descriptive-only = 2025-05 onward.

Seen on DEV (the quant's runs, `research/output/quant_m1` and `quant_s1`, summaries in `summary.json`; arms k = 1.0 / 1.5 / 2.0 x ATR(H1,14), 40 reflect-null seeds for `quant_m1`, 20 for `quant_s1`, one real-signal run each, paired week-block bootstrap):

| run | k | null delta R/trade (SE) | real delta R/trade (SE) | null trades vs control (pooled / FX only) | real trades vs control (pooled / FX only) | null delta positive in |
|---|---|---|---|---|---|---|
| `quant_m1` (placeholder spread as recorded) | 1.0 | +0.0401 (0.012) | +0.0203 (0.017) | 1.028 / 0.952 | 1.010 / 0.934 | 7 / 8 yrs |
| | 1.5 | +0.0498 (0.014) | +0.0231 (0.022) | 0.915 / 0.818 | 0.891 / 0.798 | 7 / 8 |
| | 2.0 | +0.0647 (0.016) | +0.0660 (0.026) | 0.799 / 0.706 | 0.753 / 0.667 | 7 / 8 |
| `quant_s1` (50-point placeholder treated as missing) | 1.0 | +0.0346 (0.0115) | +0.0100 (0.0170) | 1.028 / 0.958 | 1.022 / 0.948 | 7 / 8 |
| | 1.5 | +0.0450 (0.0138) | +0.0084 (0.0219) | 0.909 / 0.819 | 0.890 / 0.800 | 7 / 8 |
| | 2.0 | +0.0552 (0.0157) | +0.0447 (0.0255) | 0.788 / 0.701 | 0.747 / 0.664 | 7 / 8 |

Also seen on DEV: control (B0) real -0.1308 R (`quant_m1`) / -0.1125 R (`quant_s1`, 4,203 trades), null -0.1386 / -0.1205; real k = 1.0 on `quant_s1` without XAUUSD +0.0215 (SE 0.0170), null without XAUUSD +0.0410 (0.0111);
k = 2.5 and 3.0 (null +0.084 / +0.100, 69 % / 61 % of trades; added after seeing the trend, excluded from this study a priori); the mechanism table in the quant report (commission 0.044 -> 0.030 / 0.021 / 0.016 R, average hold 3.6 h -> 5.8 / 10.1 / 15.4 h on the null, Friday-flatten exits 5 % -> 8 / 12 / 18 %);
the scalar stop-floor study (`stop_floor.md`); the candidate stop geometry (`quant_atr_geometry.csv`, no P/L: median structure stop 0.35 to 0.55 ATR, 79 to 91 % of candidates below 1 ATR, 97 to 99 % below 2 ATR).
Composition, found by the attack on version 1 and verified in `quant_s1/comp.parquet`: XAUUSD trades are 280 in the control real run and 601 at k = 1.5 (2.06x at k = 1.0), because gold's 10-pip floor is 0.10 dollar and never binds, so the relative spread cap blocks most gold candidates in the control and the ATR floor admits them;
the pooled trade counts therefore hide a larger fall in FX trades (columns above). Control versus k = 1.5 funnel (real, `quant_s1`): spread-relative blocks 4,560 -> 553, open-position guard 7,031 -> 10,086, max-open blocks 235 -> 1,694, correlation blocks 848 -> 2,014.
The real-signal delta by ISO year (computed from the stored weekly tables before version 1 was written): on `quant_s1` positive in 5 of 8 years for each k (negative in 2014, and in 2019 and 2020 at k = 1.0 / 1.5); on `quant_m1` positive in 6 of 8 for each k. Between the two runs only 2020 changes sign (k = 1.5: -0.038 on `quant_s1`, +0.036 on `quant_m1`).

Seen elsewhere (burnt): 2025-05 onward, the live rules' backtest, rule-change and idea studies (descriptive only); the aggregate 2021-02 to 2026-10 expectancy of the live rules (-0.116 R, 3,509 trades), which overlaps FINAL. **No ATR-floor result on FINAL or on 2025-05 onward exists.** No FINAL outcome of any kind has been read for this study.

Consequence: DEV already contains the answer to "does the floor help on DEV". The DEV run in this stage (section 8) is a **deterministic re-measurement with a new driver, 40 seeds instead of 20, cluster inference and added diagnostics**. It shares the candidates, `sim.py` and the quant's `add_atr` with the quant's runs, so reproducing `quant_s1` validates the wiring only and cannot reveal a bug in shared code; an independent loop-based ATR in the tests and a hand check against raw bars cover that. It is not a new test and adds no evidence for the hypothesis. Only the one-shot FINAL run can.

## 1. Hypothesis and estimand

H: capping the stop distance from below at k x ATR(H1,14), on top of the structure stop and the 10-pip floor, reduces the cost drag of the live rules (commission and spread per unit of risk, fill-bar stop-outs) and so raises mean R per trade, without worsening drawdown, on data not used to find it.

It does **not** claim better direction. On a driftless price every stop and target rule has zero expectation before costs; the random-direction null measures the cost / convention part and the real signals measure what that is worth on the actual trades.
**Honest description of the rule:** because the structure stops are mostly far below 1 ATR (above), a floor of k = 1.0 replaces the structure stop for about 80 to 90 % of the candidates. In practice this is a volatility-scaled stop of about 1 x ATR(H1,14) that keeps a structure stop only where it is already wider. It is not a rarely binding safety net, and the quality of the ATR estimate (weekend gap, data holes) matters for most trades.

## 2. Frozen rule (candidate transform made before the simulator sees the candidate; `sim.py` is not edited)

* ATR(H1,14) at the decision time T: simple mean of the true range (max of high-low, |high-previous close|, |low-previous close|) of the last 14 H1 bars that had **closed at or before T** (bar open + 1 h <= T); code `research/quant_methods.py::h1_atr_series / atr_at / add_atr`, unchanged. No ATR (first 14 bars): the candidate is left unchanged. The weekend-open gap enters the true range of the first H1 bar after a weekend and so the ATR for the next 14 H1 bars; no adjustment (a known property, reported by weekday).
* Floor: `dist = |entry price - structure stop|`; if `dist < k x ATR` the stop is moved to `entry -/+ k x ATR` (BUY/SELL), otherwise unchanged. The floor can only widen a stop, never narrow it. Applied to market, limit and stop entries alike, measured from the candidate's entry price.
* The simulator's 10-pip floor (`min_sl_pips`, 0.10 dollar for XAUUSD) is then applied as usual to the widened stop. Order: structure stop, ATR floor, 10-pip floor.
* Sizing: unchanged. Fixed 0.25 % of the balance risked, `lots = risk / loss per lot at the final stop`, rounded down to the volume step. **A wider stop therefore means a smaller position: dollar risk per trade is the same (slightly smaller after rounding), commission and spread per unit of R fall, a full stop still costs about 1 R.** The R unit stays the actual risk of the trade, so mean R is comparable across arms; compounding makes dollar P/L path-dependent, which is why dollar-based numbers (return, drawdown) are reported but not used for the per-trade estimand. Rounding shortfall (allowed minus actual risk), `sizing_rejected`, `validator_volume` and `validator_margin` are reported for both arms. Conventions that matter more for wide stops and are left at the simulator defaults: stop slippage 0 points, gaps fill stops at the gapped open, targets are capped at the target price (`tp_gap_fill` False); the simulated account is $100,000.
* Target: 3 x the final stop distance (so the target moves out with the stop). Breakeven: trigger at +1 x the final stop distance (close-based), buffer 2 pips, exactly as live. The relative spread cap (spread <= 10 % of the final stop) is evaluated on the widened stop, so it blocks far fewer trades (the main source of the changed trade set, section 4). Intrabar: the pessimistic default (on the fill bar only the stop counts, stop wins ties); this convention penalises the control more (fill-bar stop-outs 3.8 % against 0.9 % at k = 1.5), so the neutral convention (nothing exits on the fill bar) is a guard in section 5.
* Everything else is the live configuration: `SimConfig(fixed_risk_pct=0.25, friday_flatten_utc=20.0)`, max 3 open, correlation filter, 3 % daily gate, +5 % monthly pause, cooldown 25 min, commission $2.50 per lot per side, pending expiry 30 min, stale-snapshot quirk on.
* Data: sentinel-missing treatment (50-point placeholder spread replaced by the same-UTC-hour median, `prepare_bars(..., sentinel_missing=True)`) for **both control and treated**, with the matching candidate file (DEV: `research/output/sens_sentinel_R2/base_candidates.parquet`). Control = no floor (the live rules). Pairs: the six in `config.PAIRS`. **Disclosure: the cost layer is not causal.** The sentinel flag uses the calendar-month share of 50-point bars and the imputation uses a median over the whole loaded window; this is symmetric across the two arms but feeds the spread cap, which is the main selector of the trade set. The share of imputed bars per pair and month is reported for DEV and for FINAL.

## 3. The single frozen k and the rule that chose it

Rule R-k, revised after the attack (version 1 used pooled trade counts and froze k = 1.5; the change and its reason are in the change log). Among k in {1.0, 1.5, 2.0}, a k is admissible if
(a) trade counts, **FX-only and pooled, real signals and null, on both `quant_m1` and `quant_s1`**, are all >= 80 % of the control; (b) the null delta is positive in >= 6 of the 8 ISO years of DEV on `quant_s1` (the data treatment of the confirmation); (c) the pooled real-signal delta on `quant_s1` is positive.
Freeze the **largest admissible k**. If none is admissible, no confirmation is registered. The 80 % threshold and the year count come from the owner's brief; the brief also suggested requiring a positive real delta in >= 6 of 8 years, which I replaced by (c) (a per-year real delta has an SE of about 0.05 R, so a true +0.02 R gain would fail a 6-of-8 test about 57 % of the time). **That substitution turned a rule that would have returned "no k admissible" on `quant_s1` into one that returns a k**; (c) is nearly vacuous (every real delta is positive) and the year counts do not discriminate between the k values (section 0). In effect R-k is a trade-count filter.

Application (inputs in section 0): k = 1.0 passes every cell (FX-only ratios 0.934 to 0.958, null years 7/8, real +0.0100). k = 1.5 fails (a) on `quant_m1` (real FX-only 0.798) and sits on the line on `quant_s1` (0.800), i.e. it is not clearly admissible. k = 2.0 fails (a) clearly (FX-only 0.664 to 0.706; pooled 0.747 to 0.799).
**Frozen k = 1.0.** Plainly: DEV cannot tell the three k values apart (their deltas differ by less than their standard errors); k = 1.0 is the only one that satisfies every version of the criterion; the correction moves the choice to the k with the smaller DEV estimates, so the power in section 6 is lower than version 1 stated. k = 2.0 had the largest real DEV delta (+0.045 on `quant_s1`, +0.066 on `quant_m1`) and is excluded because it removes about a third of the FX trades. No other k and no other arm is tested in FINAL.

## 4. Estimands

Unit: a finished trade (exit reason END, open at the window end, excluded as in `summarize`; the number of END trades per arm is reported). Cluster keys use `placed_T` (the decision time, identical for a given order in both arms): day = UTC date, week = ISO week. Friday flatten means no position crosses a weekend.

**Primary (real signals, all six pairs, sentinel-missing, paired treated vs control):**
* E1 = change in mean R per trade, treated minus control.
* E2 = change in R per calendar month (total R / (calendar days of the control equity curve / 30.4), same denominator for both arms).
* E3 = change in the maximum equity drawdown (simulator equity, compounded; real run and mean of the null seeds).
Inference: paired cluster bootstrap, 5,000 resamples, seed 12345, resampling whole clusters (each cluster carries the trades of both arms with that key), 95 % equal-tailed percentile interval, for three cluster definitions: day, ISO week, and a 4-week moving block of ISO weeks (weeks are not independent: shared volatility regimes, compounding, the monthly pause). **The binding lower bound is the lowest of the three.** This makes the effective alpha smaller than 0.025 and the true power lower than the table in section 6 (which is therefore an upper bound). E3 is a path statistic and has no interval; it is a point-estimate guard.
Caveat stated up front: with a negative expectancy, trading fewer trades raises R per month and shrinks drawdown by itself (at k = 1.0 the pooled real trade count rises from 4,203 to 4,294 only because gold trades double; FX trades fall about 5 %), so E2 and E3 are harm guards, not evidence of a better structure; the structure claim rests on E1 and on N1.

**Secondary (reported; decision weight only where section 5 says so):**
* N1: the same E1 on the `reflect` random-direction null, 40 seeds (0..39, flips `default_rng(seed).random(n) < 0.5` on the candidate frame in file order, identical for both arms). A measurement of cost and simulator-convention effects, never an alpha claim.
* Composition-standardised E1: the per-pair differences (treated minus control) weighted by the control's trade shares. The floor adds gold trades (the worst-expectancy pair), so pooled E1 is, if anything, diluted; the standardised value shows what is left when the pair mix is held fixed. Also by-pair, FX-only, XAUUSD-only; all / no-XAUUSD / no-GBPUSD-2019 (the one data hole) subsets; by ISO year; by weekday of the decision (Monday separately, for the weekend-gap ATR property); by decile of ATR relative to its 100-bar rolling median. These cuts are descriptive; no rule is changed by them.
* Mechanism: trades, commission per R, spread per R, share of stops widened by the ATR floor and pinned at the 10-pip floor, average hold, exit mix, fill-bar stop-outs, worst trade, worst Prague day, and the funnel counters of both arms (spread-relative blocks, open-position guard, max-open, correlation, cooldown, expiry, sizing/volume/margin rejections).
* Matched / unmatched decomposition: orders present in both arms (same symbol, same placed time) versus orders in only one arm, as total R and mean R (`stoploss_experiment.matched_split`). The floor changes which later candidates are blocked (spread cap, open-position guard, max-open, correlation), so E1 mixes a pure stop-geometry effect on matched orders with a changed trade set (survivorship in the sense that the treated arm trades some candidates the control skipped, and vice versa). E1 is primary because it compares the two policies as run; the decomposition says how much of it is which.
* Stressed costs (GAMEPLAN section 3 asks for them): (i) spread x 1.5 and commission $3.125 per lot per side, both arms, real run plus null seeds 0..9; (ii) swap stress in post-processing, both arms: the data carry no swap rates, so a stylised adverse charge of $7 per lot per night (XAUUSD $20), counted at each 17:00 America/New_York rollover crossed between fill and exit (21:00 UTC in US summer time, 22:00 UTC in winter), Wednesday night x3, is subtracted from R; half of it is reported too. The charge in R per night falls as 1 / stop while the number of nights rises with the hold time, so the net effect is computed, not assumed. Swap rates have either sign in reality; only the adverse case is a guard.
* Intrabar-neutral sensitivity: `intrabar="neutral"`, real run plus null seeds 0..9.
* Moving-block sensitivity and `n_end` per arm.

## 5. Success criterion for the one-shot FINAL run (frozen now; sentinel-missing FINAL window 2021-06-01 to 2025-05-01)

Evidence tests: (S1) E1 binding lower bound > 0; (S2) E1 point estimate >= +0.02 R. Alpha: one-sided 0.025 (95 % lower bound), one candidate (GAMEPLAN: at most 2, 0.025 each).
Harm guards, which are **non-discriminating** (they move with the trade count under a negative expectancy and with cost arithmetic, and add little evidence): (S3) treated real max drawdown not deeper than control's and the null-seed mean not deeper; (S4) E2 point estimate > 0; (S6) N1 point estimate > 0.
Robustness guards (point estimate > 0 each; individually weak): (S5) no-XAUUSD E1, composition-standardised E1, adverse-swap E1, stressed-cost E1, neutral-intrabar E1.
Trade-count guard (as the quant report required for a FINAL candidate): (S7) real trades >= 80 % of control, pooled and FX-only.

Decision table, evaluated in this order and reported as such, with no further interpretation:

| tier | condition | consequence |
|---|---|---|
| A CONFIRMED (structure) | S1 to S7 | the floor reduced the cost drag on unseen data, on the real signals. The only tier on which a request for a reviewed live-code change is supported |
| D BENEFIT NOT SHOWN (failure) | not A, and any of: real E1 point estimate < +0.02; N1 upper bound < +0.02 (cost mechanism not reproduced); S3 fails | reported plainly with the numbers. It does not prove the effect is zero; it says the pre-registered benefit was not shown. Idea closed, no live change |
| B COST EFFECT REPRODUCED, REAL GAIN UNPROVEN | not A, not D, N1 lower bound > 0 and N1 point estimate >= +0.02 | the effect appears on random trades out of time; the real-signal gain (point estimate >= +0.02) is not distinguishable from zero or a guard failed. Idea closed as "unconfirmed, cost-only" in `RESULTS.md`; no live change; no second FINAL use |
| C NOT DETECTABLE | everything else | the test could not tell. Idea closed as unconfirmed; no live change; no second FINAL use |

A failure (tier D) is not followed by a different k, ATR window, subset (pair, session, year) or re-run; neither is B or C. FINAL is used once; no `--force`.

## 6. Power for the FINAL run (stated honestly; k = 1.0)

Inputs from DEV `quant_s1`, k = 1.0: real E1 SE 0.0170 over 89.05 months (4,294 treated trades), null E1 SE 0.0115. FINAL has about 47 months and, at the DEV rate of 47 control trades a month, about 2,200 trades (2,000 to 2,650). SE scales as 1 / sqrt(months): factor 1.38, so
**real E1 SE about 0.023, null E1 SE about 0.016** (to be recomputed from the DEV run's own cluster SEs and shown in its report). The table is an upper bound on power: the lowest-of-three bound lowers it, and spread savings are probably smaller on FINAL than on DEV (spreads in recent years are lower, so the spread-per-stop part of the gain, 0.008 of the 0.040 R null gain at k = 1.0 in the quant mechanism table, and part of the fill-bar stop-out saving may shrink while the commission saving, 0.014 R, stays), so the null gain on FINAL is likely below the DEV estimate.

| true real-signal gain | power of S1 (one-sided 0.025, SE 0.023) | | true null gain | power of the N1 lower-bound test (SE 0.016) |
|---|---|---|---|---|
| +0.010 R (DEV real estimate, pooled) | 6 % | | +0.015 | 16 % |
| +0.0215 R (DEV real estimate without gold) | 15 % | | +0.020 | 24 % |
| +0.035 R (DEV null estimate) | 32 % | | +0.030 | 47 % |
| +0.045 R | 49 % | | +0.0346 (DEV null estimate) | 59 % |
| +0.066 R | 80 % (minimum detectable gain) | | +0.044 | 79-80 % (minimum detectable) |

So FINAL can detect a real-signal gain of about **0.066 R or more** and a null (cost) gain of about 0.044 R or more. The DEV real-signal estimate (+0.010 to +0.022 R) is far below that: if it is the truth, **FINAL has about a 6 to 15 % chance of tier A** and will most likely return tier D or C. A real gain below about 0.07 R is **not detectable** with this window, which is different from rejected.
S2 (>= +0.02) is not binding: any lower bound above 0 at SE 0.023 already implies a point estimate above 0.045.
Recommendation to the owner (not a gate, and not mine to apply): do not create `FINAL_GO` unless (i) the DEV run's recomputed real E1 SE is at most 0.030, and (ii) you accept a chance of 6 to 15 % of a decisive real-signal answer. The cheaper first step is N1 alone on the descriptive-only window 2025-05 onward (random-direction trades, so it measures cost, not alpha, and does not spend the unburnt FINAL window); it would need its own short pre-registration and is not done here.

## 7. What a result would and would not mean

* Tier A would mean: on unseen 2021-06 to 2025-04 data the floor lowered the loss per trade of this signal-less strategy by a measurable amount. A cost-saving structural change, **not evidence of skill**: direction stays at chance (stage 0), the expectancy stays negative (the DEV real run at k = 1.0 is -0.1025 R per trade, about -4.9 R per month), the FTMO pass probability stays near zero.
* It changes live stop placement and the stop used for position size, target, breakeven trigger and the spread cap, so a live change (`MIN_SL_PIPS` replaced by max(floor, k x ATR(H1,14)) in the risk and order code, with closed-bar-only ATR from the broker, the NaN rule above, and gold units) needs its own reviewed change, tests and the `reviewer` agent, and only if the owner asks after seeing a tier A result. Nothing live is changed by this study.
* Holding time rises (3.6 h to about 6 h on the null at k = 1.0): more rollover and swap exposure and more trades ending at the Friday flatten; the simulator has no swap model beyond the stylised stress above.
* XAUUSD: the 10-pip floor is 0.10 dollar and never binds, so for gold the ATR floor changes the stop from a fraction of a dollar to about one dollar-scale ATR and doubles the number of gold trades (the spread cap stops blocking them). Part of any gold effect is "repairing a non-binding floor", not ATR scaling, and gold has the weakest data (zero-spread stretches, imputed spreads). Gold is **kept** in the primary (it is in the live pair list) and reported separately; S5 requires the no-gold and standardised estimates to be positive too.
* A result in tiers B, C or D leaves the idea closed as unconfirmed. DEV evidence for it stays what it is: a measured cost mechanism on a signal-less strategy.

## 8. DEV work in this stage (DEV only, no FINAL)

* Implement in new files only: `research/atr_floor_confirm.py` (refuses `--end` later than 2021-06-01 and any start on or after it; a `final` command that is disabled unless the file `research/output/atr_floor/FINAL_GO` exists and contains the sha256 of the final version of this file; no `--force`) and `research/tests/test_atr_floor_confirm.py` (synthetic data, including an independent loop-based ATR and the truncation test).
* One DEV run, tag `atr_floor_dev`: control and k = 1.0, null seeds 0..39; intrabar-neutral and stressed-cost sensitivities on seeds 0..9. **Acceptance check before anything is read as a result:** the real run and null seeds 0..19 must reproduce the stored `quant_s1` weekly tables of the control and of `M1_k10` (per ISO week and symbol, trade count and sum of R, to 1e-9). A mismatch is a bug to be understood, not tuned around, and stops the stage.
* A hand check against raw H1 and M5 bars: 30 random treated trades (numpy seed 20261031) plus 10 gold trades (ATR recomputed independently, floored stop, lot size, SL/TP levels, exit).
* Report negative results as they come. Report the DEV cluster SEs and recompute the section 6 power table with them.

## 9. FINAL pins (not executed in this stage)

* Candidates for FINAL are built by `research.run.get_candidates(symbols, specs, SignalConfig(), start="2021-06-01", refresh=False, workers=N, end="2025-05-01", sentinel_missing=True)` with the default `SignalConfig` (faithful signals, the same as DEV), simulated on the same window with `prepare_bars(..., sentinel_missing=True)`, so the imputation window is the same for candidates and simulator. M5 and M15 start at the window start (the first days have less warm-up; identical for both arms), higher timeframes keep earlier history. The sha256 of the candidate parquet is stored in the run's provenance. The FINAL command refuses to run if the output tag exists (once only).
* The FINAL run uses the same code path as the DEV run (one function with a window object), the same seeds 0..39 and the same sensitivities.

## 10. Stop rules

At most 3 revisions of this document before FINAL, counting attack-driven revisions made before any run (version 2 is revision 1 of 3). After the DEV run a change is allowed only as a disclosed post-DEV revision that counts toward the 3, and the first result stays on record. No change after the owner creates `FINAL_GO`. FINAL is run once, no `--force`. Stop and report if the DEV re-measurement fails to reproduce `quant_s1`.

## Change log

* v1, 2026-10-31, commit 8f06591, sha256 23cd995403d23022ebe65cc3cb8ff74e5ab9574c815f4aafdc5dd48e61909c1b: first version, before any run. Froze k = 1.5.
* v2, 2026-10-31, before any run, after an independent hostile review (14 points; all accepted in whole or in part, none rejected outright). Changes:
  * A1 (blocker): k rule revised to use FX-only as well as pooled trade counts, on both data treatments. Version 1's "margin" claim was wrong: gold's trade count doubles and hid a 20 % fall in FX trades at k = 1.5. **Frozen k changes from 1.5 to 1.0**, as a consequence of applying the corrected criterion, toward the smaller DEV estimates. The 80 % threshold itself is kept (owner's brief).
  * A2: wrong sentence about which years differ corrected (only 2020); the substitution of criterion (c) and its effect are disclosed; R-k is described as a trade-count filter.
  * A3 (blocker): decision table rebuilt: a "benefit not shown" failure tier that is reachable (real point estimate < +0.02, cost mechanism not reproduced, or drawdown worse); only tier A supports a live change; B, C and D close the idea; the sentence offering a judgement call after B or C was removed.
  * A4: S3, S4, S6 relabelled non-discriminating harm guards; guards separated from evidence tests.
  * A5: composition-standardised E1 added (guard), funnel counters of both arms added to the mechanism table, gold's changed weight stated.
  * A6: the rule described honestly as replacing the structure stop for most trades; ATR-relative-to-median deciles and weekday cuts pre-declared as descriptive.
  * A7: neutral-intrabar E1 added to the robustness guards (the pessimistic fill-bar convention penalises the control more).
  * A8: power table recomputed for k = 1.0 with null rows at 0.015 to 0.044, expected decay of the spread saving stated, trade-count guard S7, stressed-cost sensitivity added (GAMEPLAN requires stressed costs), recommendation on `FINAL_GO` and the cheaper N1-on-2025-05+ option stated. Not adopted: making `FINAL_GO` conditional inside this document (that is the owner's decision), and running N1 on 2025-05+ now (needs its own prereg).
  * A9: acceptance by exact weekly-table equality, independent loop-based ATR test, hand-check sample fixed at 30 + 10 gold.
  * A10: non-causal cost layer disclosed; FINAL candidate construction pinned (section 9); imputed-bar share reported. Not adopted: hashing the parquet inside `FINAL_GO` (it does not exist before the run); recorded in provenance instead.
  * A11: moving 4-week block added, lowest-of-three bound, effective alpha and power caveat; cluster key `placed_T`; `n_end` reported.
  * A12: swap rollover at 17:00 New York, net effect computed, half-charge shown, "the one cost that grows" removed, guard labelled weak.
  * A13: sizing and rounding counters and sim conventions listed.
  * A14: external storage of the final hash by the owner.
