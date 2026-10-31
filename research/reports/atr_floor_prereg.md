# ATR-floor minimum stop: stage-1 pre-registration (written 2026-10-31, version 1, before any run of this experiment)

Prereg-SHA256: 23cd995403d23022ebe65cc3cb8ff74e5ab9574c815f4aafdc5dd48e61909c1b

How the hash is made: take this file, normalise line endings to LF, replace the single line that starts with `Prereg-SHA256:` by
`Prereg-SHA256: PENDING`, and take the sha256 of the UTF-8 bytes. A version's hash is recorded in the change log of the next
version and in the commit message trail; the git commit is the real time-stamp. Local commits only, nothing is pushed.

Protocol: `test-strategy-idea` skill, `GAMEPLAN.md` sections 2, 3, 13, `research/reports/quant_methods_prereg.md`.
Hypothesis owner: the quant review (`research/reports/quant_loss_risk_review.md`, section 2). This is a **cost / structure hypothesis, not a signal claim**.

## 0. What has been seen (so nobody mistakes any of it for blind)

DEV = 2014-01-01 to 2021-06-01 (exclusive). FINAL = 2021-06-01 to 2025-05-01 (about 47 months, 2021-06 to 2025-04 inclusive). Descriptive-only = 2025-05 onward.

Seen on DEV (the quant's runs, `research/output/quant_m1` and `quant_s1`, summaries in `summary.json`; arms k = 1.0 / 1.5 / 2.0 x ATR(H1,14), 40 reflect-null seeds for `quant_m1`, 20 for `quant_s1`, one real-signal run each, paired week-block bootstrap):

| run | k | null delta R/trade (SE) | real delta R/trade (SE) | null trades vs control | real trades vs control | null delta positive in |
|---|---|---|---|---|---|---|
| `quant_m1` (placeholder spread as recorded) | 1.0 | +0.0401 (0.012) | +0.0203 (0.017) | 1.028 | 1.010 | 7 / 8 yrs |
| | 1.5 | +0.0498 (0.014) | +0.0231 (0.022) | 0.915 | 0.891 | 7 / 8 |
| | 2.0 | +0.0647 (0.016) | +0.0660 (0.026) | 0.799 | 0.753 | 7 / 8 |
| `quant_s1` (50-point placeholder treated as missing) | 1.0 | +0.0346 (0.0115) | +0.0100 (0.0170) | 1.028 | 1.022 | 7 / 8 |
| | 1.5 | +0.0450 (0.0138) | +0.0084 (0.0219) | 0.909 | 0.890 | 7 / 8 |
| | 2.0 | +0.0552 (0.0157) | +0.0447 (0.0255) | 0.788 | 0.747 | 7 / 8 |

Also seen on DEV: control (B0) real -0.1308 R (`quant_m1`) / -0.1125 R (`quant_s1`, 4,203 trades), null -0.1386 / -0.1205; k = 2.5 and 3.0 (null +0.084 / +0.100, 69 % / 61 % of trades; added after seeing the trend, excluded from this study a priori);
the mechanism table in the quant report (commission 0.044 -> 0.030 / 0.021 / 0.016 R, average hold 3.6 h -> 5.8 / 10.1 / 15.4 h on the null, Friday-flatten exits 5 % -> 8 / 12 / 18 %); the scalar stop-floor study (`stop_floor.md`).
Before writing the rule in section 2 I also computed, from the stored weekly tables of `quant_m1` and `quant_s1`, the real-signal delta by ISO year (no new simulation): on `quant_s1` the real delta is positive in 5 of 8 years for each k;
on `quant_m1` in 6 of 8 for each k (the two years that differ are 2020 and 2021, the placeholder-spread era).

Seen elsewhere (burnt): 2025-05 onward, the live rules' backtest, rule-change and idea studies (descriptive only); the aggregate 2021-02 to 2026-10 expectancy of the live rules (-0.116 R, 3,509 trades), which overlaps FINAL. **No ATR-floor result on FINAL or on 2025-05 onward exists.** No FINAL outcome of any kind has been read for this study.

Consequence: DEV already contains the answer to "does the floor help on DEV". The DEV run in this stage (section 8) is a **re-measurement with a new implementation, 40 seeds instead of 20, day-cluster inference and added diagnostics**, whose job is to validate the code (it must reproduce `quant_s1`), and to
give the standard errors for the power calculation. It is not a new test and cannot add evidence for the hypothesis. Only the one-shot FINAL run can.

## 1. Hypothesis and estimand

H: capping the stop distance from below at k x ATR(H1,14), on top of the structure stop and the 10-pip floor, reduces the cost drag of the live rules (commission and spread per unit of risk, fill-bar stop-outs) and so raises mean R per trade, without worsening drawdown, on data not used to find it.

It does **not** claim better direction. On a driftless price every stop and target rule has zero expectation before costs; the random-direction null measures the cost / convention part and the real signals measure what that is worth on the actual trades.

## 2. Frozen rule (candidate transform made before the simulator sees the candidate; `sim.py` is not edited)

* ATR(H1,14) at the decision time T: simple mean of the true range (max of high-low, |high-previous close|, |low-previous close|) of the last 14 H1 bars that had **closed at or before T** (bar open + 1 h <= T); code `research/quant_methods.py::h1_atr_series / atr_at / add_atr`, unchanged. No ATR (first 14 bars): the candidate is left unchanged. The weekend-open gap enters the true range of the first H1 bar after a weekend; no adjustment (a known property, reported by weekday).
* Floor: `dist = |entry price - structure stop|`; if `dist < k x ATR` the stop is moved to `entry -/+ k x ATR` (BUY/SELL), otherwise unchanged. The floor can only widen a stop, never narrow it. Applied to market, limit and stop entries alike, measured from the candidate's entry price.
* The simulator's 10-pip floor (`min_sl_pips`, 0.10 dollar for XAUUSD, where it never binds) is then applied as usual to the widened stop. Order: structure stop, ATR floor, 10-pip floor.
* Sizing: unchanged. Fixed 0.25 % of the balance risked, `lots = risk / loss per lot at the final stop`, rounded down to the volume step. **A wider stop therefore means a smaller position: dollar risk per trade is the same (slightly smaller after rounding), commission and spread per unit of R fall, a full stop still costs about 1 R.** The R unit stays the actual risk of the trade, so mean R is comparable across arms; compounding makes dollar P/L path-dependent, which is why dollar-based numbers (return, drawdown) are reported but not used for the per-trade estimand.
* Target: 3 x the final stop distance (so the target moves out with the stop). Breakeven: trigger at +1 x the final stop distance (close-based), buffer 2 pips, exactly as live. The relative spread cap (spread <= 10 % of the final stop) is evaluated on the widened stop, so it blocks fewer trades. Intrabar tie: pessimistic (stop first) in the primary run; neutral as a sensitivity (section 5).
* Everything else is the live configuration: `SimConfig(fixed_risk_pct=0.25, friday_flatten_utc=20.0)`, max 3 open, correlation filter, 3 % daily gate, +5 % monthly pause, cooldown 25 min, commission $2.50 per lot per side, pending expiry 30 min, stale-snapshot quirk on.
* Data: sentinel-missing treatment (50-point placeholder spread replaced by the same-hour median, `prepare_bars(..., sentinel_missing=True)`) for **both control and treated**, with the matching candidate file (DEV: `research/output/sens_sentinel_R2/base_candidates.parquet`). Control = k none (the live rules). Pairs: the six in `config.PAIRS`.

## 3. The single frozen k and the rule that chose it

Rule R-k (written before it was applied; thresholds partly from the owner's brief): among k in {1.0, 1.5, 2.0}, applied to the sentinel-missing DEV run `quant_s1` (the data treatment of the confirmation), a k is admissible if
(a) mean null trades >= 80 % of control AND real-signal trades >= 80 % of control; (b) the null delta is positive in >= 6 of the 8 ISO years of DEV; (c) the pooled real-signal delta is positive. Freeze the **largest admissible k**. If none is admissible, no confirmation is registered.
Deviation from the brief, disclosed: the brief suggested also requiring a positive real delta in >= 6 of 8 years. I replaced it by (c) because a per-year real delta has an SE of about 0.05 R, so a true +0.02 R gain would fail a 6-of-8 test about 57 % of the time; the year counts were nonetheless computed (section 0) before the choice and **do not discriminate between the k values** (5 / 8 for all three on `quant_s1`; with the 6-of-8 requirement `quant_s1` would admit none, `quant_m1` would admit the same k as below).

Application (all inputs in section 0): k = 1.0: ratios 1.028 / 1.022, null years 7/8, real +0.0100 -> admissible. k = 1.5: 0.909 / 0.890, 7/8, +0.0084 -> admissible. k = 2.0: **0.788 / 0.747 -> fails (a)** (also 0.799 / 0.753 on `quant_m1`: a miss of 1.2 points on the null and 5 on the real signals).
**Frozen k = 1.5.** Stated plainly: k = 1.5 is chosen only by criterion (a), at a margin that the owner's 80 % threshold happens to draw between 1.5 and 2.0; k = 1.0 would have been admissible too. The three k values give DEV deltas that differ by less than their standard errors, so the choice carries little information; no other k and no other arm is tested in FINAL.

## 4. Estimands

Unit: a finished trade (exit reason END, open at the window end, excluded as in `summarize`). Day = UTC date of `open_T`; week = ISO week of `open_T` (the Friday flatten means no position crosses a weekend, so weeks are independent units).

**Primary (real signals, all six pairs, sentinel-missing, paired treated vs control):**
* E1 = change in mean R per trade, treated minus control.
* E2 = change in R per calendar month (total R / (calendar days of the control equity curve / 30.4), same denominator for both arms).
* E3 = change in the maximum equity drawdown (simulator equity, compounded; real run and mean of the null seeds).
Inference: paired cluster bootstrap, 5,000 resamples, seed 12345, resampling whole day-clusters (each cluster carries the trades of both arms opened that day), 95 % equal-tailed percentile interval. The same is done with ISO-week clusters; **the binding lower bound is the lower of the day-cluster and the week-cluster bounds** (day clusters ignore dependence between adjacent days). E3 has no interval (a path statistic); it is a point-estimate guard.
Caveat stated up front: with a negative expectancy, trading fewer trades raises R per month and shrinks drawdown by itself. E2 and E3 are therefore guards against harm, not evidence of a better structure; the structure claim rests on E1 (and on the null estimand N1).

**Secondary (reported, no decision weight except where marked):**
* N1 (decision-relevant for the tier B): the same E1 on the `reflect` random-direction null, 40 seeds (0..39, flips `default_rng(seed).random(n) < 0.5` on the candidate frame in file order, identical for both arms). Used only as a measurement of cost and simulator-convention effects, never as an alpha claim.
* By-pair, FX-only (5 pairs) and XAUUSD-only deltas; all / no-XAUUSD / no-GBPUSD-2019 (the one data hole) subsets; by ISO year; by weekday of the decision (Monday separately, for the weekend-gap ATR property).
* Mechanism: trades, commission per R, spread per R, share of stops pinned at the 10-pip floor, share widened by the ATR floor, average hold, exit mix, fill-bar stop-outs, worst trade, worst Prague day.
* Matched / unmatched decomposition: orders present in both arms (same symbol, same placed time) versus orders in only one arm, as total R and mean R (`stoploss_experiment.matched_split`). The floor changes which later candidates are blocked (open-position guard, cooldown, correlation, spread cap), so the policy-level E1 mixes a pure stop-geometry effect on matched orders with a changed trade set; both pieces are reported and E1 is the primary because it is the comparison of the two policies.
* Swap stress (post-processing of the trades, both arms): the data carry no swap rates, so a stylised rollover charge of $7 per lot per night (XAUUSD $20), counted at each 21:00 UTC crossed between fill and exit, Wednesday night x3, is subtracted from R. The wider stop holds longer (3.6 h -> about 10 h on the null), so this is the one cost that grows with the floor.
* Intrabar-neutral sensitivity: `intrabar="neutral"`, real run plus null seeds 0..9.

## 5. Success criterion for the one-shot FINAL run (frozen now; all on the sentinel-missing FINAL window 2021-06-01 to 2025-05-01)

Success S (all must hold): (S1) E1 binding lower bound > 0; (S2) E1 point estimate >= +0.02 R; (S3) E3: treated real max drawdown not deeper than control's, and the null-seed mean not deeper; (S4) E2 point estimate > 0;
(S5) guards: no-XAUUSD E1 point estimate > 0 and swap-stress E1 point estimate > 0; (S6) null E1 point estimate > 0.
Alpha: one-sided 0.025 (the 95 % lower bound), one candidate (GAMEPLAN: at most 2, 0.025 each).

Decision table, evaluated in this order and reported as such, with no further interpretation:

| tier | condition | meaning |
|---|---|---|
| A CONFIRMED (structure) | S1-S6 | the floor reduced the cost drag on unseen data, on the real signals |
| D FAILED | real E1 upper bound < +0.02, or N1 upper bound < +0.02 (cost mechanism not reproduced), or (real E1 point estimate < 0 and S3 fails) | no support; no retuning |
| B MECHANISM REPRODUCED, NOT CONFIRMED | not A, not D, N1 lower bound > 0 and N1 point estimate >= +0.02, real E1 point estimate > 0 | the cost effect appears on random trades out of time; the real-signal effect is not distinguishable from zero |
| C NOT DETECTABLE | everything else | the test could not tell; says nothing against the hypothesis |

"Failure" means tier D, and is reported plainly with the numbers. A failure is not followed by a different k, a different ATR window, a subset (pair, session, year) or a re-run; tier C is also not followed by one. FINAL is used once; no `--force`.

## 6. Power for the FINAL run (stated honestly)

Inputs from DEV `quant_s1`: real E1 SE 0.0219 at 4,203 control trades over 89.05 months; null E1 SE 0.0138. FINAL has about 47 months and, at the DEV rate of 47 control trades a month, about 2,200 trades (2,000 to 2,650). SE scales as 1 / sqrt(months): factor 1.38, so
**real E1 SE about 0.030 (0.028 to 0.032), null E1 SE about 0.019** (to be recomputed from the DEV run's own day- and week-cluster SE and shown in its report).

| true real-signal gain | power of S1 (one-sided 0.025, SE 0.030) | | true null gain | power of the N1 lower-bound test (SE 0.019) |
|---|---|---|---|---|
| +0.020 R (about the DEV real estimate) | 10 % | | +0.020 | 18 % |
| +0.045 R (the DEV null estimate) | 32 % | | +0.035 | 45 % |
| +0.065 R | 58 % | | +0.045 (DEV null estimate) | 66 % |
| +0.084 R | 80 % (the minimum detectable gain) | | +0.053 | 80 % |

So FINAL can detect a real-signal gain of about **0.08 R or more** and a null (cost) gain of about 0.05 R or more. The DEV real-signal estimate (+0.008 to +0.023 R) is far below that: if it is the truth, FINAL has about a 10 % chance of tier A and will most likely return tier C.
A real gain below about 0.08 R is **not detectable** with this window, which is different from rejected. S2 (>= +0.02) is not binding: any lower bound above 0 at SE 0.03 already implies a point estimate above 0.05. The owner should therefore decide whether a 10 to 32 % chance of a decisive answer is worth burning the FINAL window on this candidate (it is one of at most two).

## 7. What a result would and would not mean

* Tier A would mean: on unseen 2021-06 to 2025-04 data the floor lowered the loss per trade of this signal-less strategy by a measurable amount. A cost-saving structural change, **not evidence of skill**: direction stays at chance (stage 0), the expectancy stays negative (the DEV real run at k = 1.5 is -0.104 R per trade, about -4.4 R per month), the FTMO pass probability stays near zero.
* It changes live stop placement and the stop used for position size, target, breakeven trigger and the spread cap, so a live change (`MIN_SL_PIPS` replaced by max(floor, k x ATR(H1,14)) in the risk and order code, with closed-bar-only ATR from the broker, the NaN rule above, and gold units) needs its own reviewed change, tests and the `reviewer` agent, and only if the owner asks after seeing the FINAL result. Nothing live is changed by this study.
* Holding time rises (about 3.6 h to 10 h): more rollover and swap exposure and more trades ending at the Friday flatten; the simulator has no swap model beyond the stylised stress above.
* XAUUSD: its 10-pip floor is 0.10 dollar and never binds, so for gold the ATR floor changes the stop from a fraction of a dollar to about 1.5 x a dollar-scale ATR. Part of any gold gain is "repairing a non-binding floor", not ATR scaling, and the data for gold are the weakest (zero-spread stretches, imputed spreads). Gold is **kept** in the primary (it is in the live pair list), and reported separately; S5 requires the no-gold estimate to be positive too.
* Tier B or C leaves the owner with a mechanism that is plausible and measured on DEV, and no confirmation. That is a judgement call about a cost-only change, not a statistical result.

## 8. DEV work in this stage (DEV only, no FINAL)

* Implement in new files only: `research/atr_floor_confirm.py` (refuses `--end` later than 2021-06-01 and any start on or after it; a `final` command that is disabled unless the file `research/output/atr_floor/FINAL_GO` exists, which only the owner creates and which must hold the sha256 of the final frozen version of this file; there is no `--force`) and `research/tests/test_atr_floor_confirm.py` (synthetic data).
* One DEV run, tag `atr_floor_dev`: control, k = 1.5, null seeds 0..39, intrabar-neutral sensitivity on seeds 0..9. Acceptance check, before anything is read as a result: control and k = 1.5 reproduce the `quant_s1` real-run trade counts (4,203 and 3,740) and, on seeds 0..19, its null mean R. A mismatch is a bug to be understood, not tuned around.
* A hand check of a sample of treated trades against the raw H1 and M5 bars (ATR, floor, lot size, SL/TP levels, exit).
* Report negative results as they come.

## 9. Stop rules

At most 3 revisions of this document before FINAL, counting the attack-driven revision(s) made before any run; after the DEV run a change is allowed only as a disclosed post-DEV revision that counts toward the 3, and the first result stays on record. No change after the owner creates `FINAL_GO`. FINAL is run once, with no `--force`. Stop and report if the DEV re-measurement fails to reproduce `quant_s1`.

## Change log

* v1, 2026-10-31: first version, before any run.
