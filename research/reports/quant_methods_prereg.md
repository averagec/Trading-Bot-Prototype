# Structural / method ideas and gate counterfactuals: pre-registration (written 2026-10-31, before any arm below was simulated)

Part of the quant loss-and-risk review (`research/reports/quant_loss_risk_review.md`). Protocol: `test-strategy-idea` skill, `GAMEPLAN.md`
sections 2 and 3. Code: `research/quant_methods.py`; tests: `research/tests/test_quant_methods.py`. DEV only (2014-01-01 to 2021-06-01).
FINAL (2021-06 to 2025-04) is not read, run, or looked at; the code refuses `--end` later than 2021-06-01 and asserts that no bar or
candidate at or after it is loaded. 2025-05 onward is not used. No `--force`. Nothing is nominated for FINAL by this document; DEV can only
nominate (stop-floor rule), the owner decides on a locked run.

## 0. What has already been seen (so nobody mistakes it for blind)

* Aggregates quoted in `RESULTS.md` / `stop_floor.md` / `rule_changes.md`: stage 0 (DEV, 3,970 trades, real -0.131 R, reflect null about -0.126 to -0.139 R),
  the cost decomposition (commission 0.044 R, Friday flatten +0.02 R, breakeven removal 0), the stop-floor study (null -0.136 at 10 pips to -0.063 at 30 pips,
  real -0.131 to -0.077), the burnt-window rule-change and idea studies (16 months, 2025-05 on).
* My own reproduction run (2026-10-31, one real-signal simulation on DEV with the stage-0 candidates, live settings): 3,970 trades, -0.1308 R,
  funnel counts (candidates 23,853; spread-relative blocks 4,389; spread-absolute 1,693; correlation 758; max-open 195; daily gate 3; monthly pause 6;
  expired unfilled 4,086). Nothing else of mine has been simulated at the time of writing.
* A descriptive look at the candidate stop distance in ATR(H1,14) units (`research/output/quant_atr_geometry.csv`, no P/L): median stop is 0.35 to 0.55 ATR,
  ATR(H1) about 14 to 21 pips for FX, so a 1.0 / 1.5 / 2.0 x ATR floor is roughly 15 / 23 / 30 pips on EURUSD, comparable to the stop-floor study's F15 / F30.
  The k grid below was fixed after this look and before any P/L.
* NOT seen: any result of the arms below.

## 1. Why the null can measure these and what it cannot

Direction is random in the `reflect` null (whole trade reflected about the decision-time mid; see `nulltest.py`), so on a driftless price the expectation of any
stop, target, breakeven or time-exit rule is zero before costs (optional stopping). What an arm can change on the null is therefore (a) cost per R (commission
and spread over the stop), (b) simulator conventions (gap fills pay worse for stops than for targets, fill-bar rule, limit fill-on-touch), and (c) the number of
trades. Null deltas are cost-and-convention evidence, never an alpha claim (GAMEPLAN section 6). The frictionless null is not zero (-0.025 to -0.07 R), so only
differences between arms are read. A skill-less signal in this framework loses `N x E[R] x risk` per month, so both R per trade and R per month are reported.

## 2. Design

* Candidates: `research/output/stage0_R2/base_candidates.parquet` (live rules = dead sweep, DEV), prices from `prepare_bars` with the default spread imputation;
  the 50-point placeholder spread is used as is in the primary run and treated as missing in the sensitivity run (section 5).
* Base config B0 = the live settings: `SimConfig(fixed_risk_pct=0.25, friday_flatten_utc=20.0)`, default conventions (the same base as the stop-floor study, so numbers
  are comparable; 10-pip floor, 3R target, breakeven 1R, max 3 open, correlation filter, 3 % daily gate, +5 % monthly pause).
* Null: `reflect`, seeds 0 to 39, `default_rng(seed).random(n) < 0.5` exactly as stage 0, **the same flips for every arm** (pairing). Real signals: one deterministic run per arm.
* Estimator: paired week-block bootstrap (2,000 resamples, seed 12345), whole ISO weeks resampled jointly across all arms and seeds (`stoploss_experiment.Boot/contrast`).
  Reported with all pairs, without XAUUSD, and without GBPUSD-2019 (the one real data hole); by DEV year (ISO year) for sign consistency (descriptive).

## 3. Arms (fixed grid; nothing is added to the decision family except the single composite C1 defined in section 4)

| Arm | Rule (exact) | Class | Prior expectation of the null delta per trade vs B0 (stated before data) |
|---|---|---|---|
| B0 | live settings | control | |
| M1_k10 / M1_k15 / M1_k20 | stop distance at least k x ATR(H1, 14) at the decision (k = 1.0 / 1.5 / 2.0), applied by widening `sl_raw` before the simulator; the 10-pip floor still applies; target stays 3 x the stop. ATR = mean true range of the last 14 H1 bars **closed** at or before the decision time | cost lever (pair- and volatility-aware stop floor; also fixes gold, whose pip floor never binds) | +0.02 / +0.04 / +0.06 R, fewer trades (-5 / -12 / -25 %) |
| M2_c7 / M2_c5 / M2_c3 | skip when spread exceeds 7 / 5 / 3 % of the stop (live: 10 %) | cost lever | +0.005 / +0.01 / +0.02 R at 5 to 40 % fewer trades; unclear because the cap interacts with the 10-pip floor |
| M3_h20 / M3_h21 | no new orders and no pending fills from 20:00 / 21:00 UTC onward (live: until 22:00) | cost lever (rollover-hour spreads) | +0.01 / +0.005 R |
| M4_o1 / M4_o2 | at most 1 / 2 open positions (live: 3) | exposure arm, no cost claim | about 0 per trade; R per month and drawdown fall roughly in proportion to the trades |
| M5_rr2 / M5_rr4 | target 2R / 4R (live 3R) | theory-neutral on a driftless price (cost and convention) | 0 to +0.02 R (rr2), 0 to -0.02 R (rr4) |
| M6_t24 | close any position still open 24 h after the fill (at the bar close, after the bar's SL/TP) | **negative control** | 0 within noise; a |delta| above 0.02 R means the null is convention-sensitive and every null read is qualified |
| C1 | composite: the best M1 arm and the best M2 arm (rule in section 4) together | one composite | sum of parts, probably less |

M1, M2, M3 and M5 arms (10) plus C1 are the **treatment family, K = 11, one-sided alpha 0.05 / 11, z = 2.61**. M4 (exposure) and M6 (control) are
reported without a verdict and are not in the family.

## 4. Decision rules (fixed now)

* Cost effect on the null: REDUCES if D_null >= +0.02 R and D_null - 2.61 SE > 0; INCREASES if D_null <= -0.02 R and D_null + 2.61 SE < 0; else NONE.
  Real signals: HELPS / HURTS by the same bounds on D_act; a NEUTRAL with SE above 0.02 is "not separable from noise", with minimum detectable change 2.8 SE.
* **Cost lever** (a candidate for the owner's later holdout decision): REDUCES, null trades at least 70 % of B0's, same sign of D_null in at least 6 of the 8 DEV
  years, sign unchanged without XAUUSD and without GBPUSD-2019, and still REDUCES (or at least D_null >= +0.01) on the sentinel-missing sensitivity run.
  **Exposure reducer**: improves R per month and drawdown mainly by trading less (null trades below 70 %, or D_null inside +-0.02).
* **Composite C1 rule**: among M1 arms with REDUCES and null trades >= 70 %, take the one with the largest D_null; same for M2; if either family has no such arm, C1 uses
  only the other; if neither, C1 is not run. C1 is simulated once. It counts as one of the three allowed parameter revisions.
* **Ranking** (report section 2): cost levers by D_null per trade, then by change in null R per month; exposure reducers and negative results listed after, with the
  numbers. An arm that does not clear its bound is reported as "not separable" or "no effect", with its minimum detectable change.
* Stop rule: an arm family that fails on DEV against the null is dropped (not re-tuned); at most three parameter revisions in total (C1 plus two more, each logged below).

## 5. Robustness (run after the main result, only for B0 and arms flagged REDUCES or in C1)

* Sentinel-missing sensitivity: candidates `research/output/sens_sentinel_R2/base_candidates.parquet` and bars from `prepare_bars(sentinel_missing=True)`, 20 null seeds.
* With and without XAUUSD; without GBPUSD 2019; by DEV year.
* Not done here: stressed costs on the FINAL window, anything on 2021-06 onward, swaps, slippage.

## 6. Power (stated before the run)

From the stop-floor study the paired SE of a null delta was 0.009 to 0.014 (60 seeds); with 40 seeds expect 0.010 to 0.016, so the minimum detectable null delta is about
0.03 to 0.045 R. The real-signal SE was 0.014 to 0.024, minimum detectable change 0.04 to 0.07 R: the real-signal read is **not powered** for effects of the expected size and
is reported for consistency only. Anything between +0.02 and the MDE is "consistent with a real gain, not separable".

## 7. Gate counterfactuals (descriptive, no verdict, no family)

Arm set `gates`: B0 and nine one-at-a-time removals (relative spread cap, all spread filtering, correlation filter, max-open, 3 % daily gate, monthly pause, cooldown,
Friday flatten, stale-snapshot quirk off) plus the regime filter off (candidates rebuilt with `use_regime=False`, so its null flips differ; compared as means). 20 null seeds + the real
run. Reported as the change in total R, mean R per trade and trades when the gate is removed; a gate "saves" R if removing it lowers the total. In a system whose every trade has a
negative expectation any gate that blocks trades saves total R by blocking, so the informative number is the mean R of the extra trades the gate blocks (selectivity), reported as
(total R change) / (trades change). News filtering is not modelled (no historical calendar) and cannot be measured here.

## Log of changes after registration

* 2026-10-31, before any gate result: the gate counterfactual set is run with 16 null seeds instead of the 20 written in section 7, because the shared machine was at 100 % load and the methods run (40 seeds) took about 36 minutes per
  batch of 9 tasks. Power for the gate table falls slightly; no other change.
* 2026-10-31, after the main `methods` result (revision 1 of at most 3): the three ATR floors all reduced the null's drag monotonically (k = 1.0 / 1.5 / 2.0), with the trade count falling only to 80 % at k = 2.0 and
  the gain not yet flattening. To find where it ends I add two arms to the M1 family before running them: M1_k25 (2.5 x ATR) and M1_k30 (3.0 x ATR), 40 seeds, same flips, new tag. They are treated like the other M1 arms
  (family size becomes 12 treatments + composite; z stays 2.61 as a stated simplification, a Bonferroni z for K = 13 would be 2.66). The composite rule gave C1 = M1_k20 alone (no M2 arm met REDUCES), so no composite run is made.
* 2026-10-31, robustness (section 5) is run on the arms flagged REDUCES plus the borderline M2_c7 and M2_c5 (20 seeds, sentinel-missing candidates and bars).
