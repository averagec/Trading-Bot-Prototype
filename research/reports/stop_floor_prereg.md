# Minimum-stop-floor experiment: pre-registration (written 2026-10-29, before any run of this experiment)

Part of the cost-hygiene work in `GAMEPLAN.md` sections 3 and 6. Code: `research/stoploss_experiment.py`; tests:
`research/tests/test_stoploss_experiment.py`; result: `research/reports/stop_floor.md`. DEV only. FINAL (2021-06-01 to 2025-04-30)
is not read, not run, not looked at; the burnt window (2025-05 onward) is not used at all here.

## 1. Question

The live bot widens every structure stop that is tighter than `MIN_SL_PIPS` (10 pips; `utils/risk_manager.py:354`) to exactly that
distance and sets the take-profit at 3 x the stop. In the stage-0 trades the median stop of EURUSD, AUDUSD, USDCAD and USDJPY is
exactly the floor, so most trades are pinned to it (`research/output/cost_record.txt`). Cost per R scales as 1/stop (commission in R
is about 0.5 / stop-in-pips for a 5-digit pair; spread in R is spread / stop).

**Q1 (mechanics, judged on the random-direction null):** does a wider floor lower the per-trade drag of the framework?
**Q2 (harm, judged on the real signals):** does it change the real rules' expectancy, and at what price in trade count, win rate,
time in trade, drawdown and daily-loss exposure?

The floor does not change the signal: the candidate set (direction, entry price, structure stop level) is identical at every floor
(section 3). It changes the stop distance for the pinned trades, the R-distribution, the cost per R, and through the spread filter
and the portfolio gates which candidates become trades. A mirrored-stop random-direction null has no structure (GAMEPLAN section 6):
null differences measure cost and simulator convention, never alpha. Harm is judged on the real signals.

## 2. What I have already seen (so nobody mistakes it for blind)

* Stage 0 (DEV, live settings, 3,970 trades): null -0.150 R, actual -0.131 R, skill +0.020 R (SE 0.020), INCONCLUSIVE.
* `cost_record.txt`: median stop distance and spread/stop by pair for the stage-0 R2 trades (cost columns only): 10 pips for EURUSD,
  AUDUSD, USDCAD (spread/stop 7 %), USDJPY 10 pips (8 %), GBPUSD 13.9 pips (2.9 %), XAUUSD 2.89 dollars (4.5 %).
* The unfinished `decompose_dev` job's console log (read 2026-10-29, null on DEV, 40 seeds, no P/L from real signals): cumulative path
  base -0.152; no commission -0.108; plus no spread and no spread filter -0.111 (5,799 trades); plus no breakeven -0.113 (win rate 25.0 %);
  plus no Friday flatten -0.137. One-at-a-time: no spread alone -0.167. **So the frictionless random-direction null is about -0.11 to
  -0.14 R, not 0, and removing the spread does not help.** Commission is about 0.044 R of the 0.150. The assumption that "about 0.115 R of
  the drag is spread + commission" is not supported by that log; a structural (convention) component of roughly -0.11 R remains
  when every cost is off. This changes what I expect: the floor may act on that component too (gap fills, stop-type entry fills and
  stop-on-fill-bar scale with the stop's exposure to bar noise), which is why the analysis splits the null by entry subtype and exit
  reason and why a frictionless check is run at every floor.
* I have NOT looked at any trade-level result at any floor other than 10 pips, nor at the stop-distance distribution beyond the medians
  above. A descriptive scan of the candidate stop distances (share below each floor, per pair; no P/L) is part of the run and is reported
  as such.

## 3. Candidates and what the floor touches (read from the code, verified by a test)

* Candidates: `research/output/stage0_R2/base_candidates.parquet` (faithful rules = live code, DEV 2014-01-01 to 2021-05-31, 6 pairs).
  `signals.build_candidates` takes the structure stop from `RiskManager.find_structure_sl` (swing +/- 3 pips) and keeps the candidate
  only if that stop is on the correct side of the entry. `MIN_SL_PIPS` is read only in `RiskManager.calculate_position` (the sizing
  path) and, in the research code, only in `sim.size_position`. The candidate stored column is the raw `sl_raw`; the floor is applied in
  `size_position` at order time. **The candidate set therefore does not depend on `min_sl_pips`**, so one parquet serves every
  floor. A test patches `utils.risk_manager.MIN_SL_PIPS` and checks `find_structure_sl` does not move.
* `Simulator` widens a stop tighter than `cfg.min_sl_pips` to exactly that many pips (`pip` = 10 points for 3/5-digit symbols, 1 point
  otherwise, so **XAUUSD "10 pips" = 0.10 dollars**) and sets TP = 3 x the stop. Sizing is fixed 0.25 % risk. Because the stop is wider,
  lots fall in proportion (risk is constant), which is why commission in R falls.
* The mirrored null (`nulltest.mirror`) reflects `sl_raw` about the entry; the floor is symmetric, so mirror-then-floor equals
  floor-then-mirror.
* Gold: its structure stops are about 2.9 dollars = 289 "pips", far above 30 pips (0.30 dollars). Every scalar floor in the grid
  leaves gold untouched by construction; its results move only through the portfolio (balance path, max-open, correlation). XAUUSD is
  therefore not an informative pair for the scalar grid and is shown separately; the headline is reported with and without it.

## 4. Variants (fixed; no others will be added to the decision family)

| Name | Floor | Why |
|---|---|---|
| C10 | 10 pips (the live value) | control |
| F15 | 15 pips | the smallest meaningful step up (+50 %): above the 10-pip pin of 4 of 6 pairs, near GBPUSD's median 13.9 |
| F20 | 20 pips | doubling the floor; cost per R roughly halves for pinned trades; TP at 60 pips |
| F30 | 30 pips | tripling; TP at 90 pips is about a day's range in EURUSD, so this is the end of the useful range: beyond it most trades would be ended by the Friday flatten, not by the stop or target. No value above 30 is tried. |
| S20 | max(10 pips, 20 x the decision bar's spread) per trade | the one pair- and era-specific variant: stop at least 20 spreads wide, i.e. spread/stop at most 5 %, half of the validator's 10 % limit. Spreads were 7 to 12 points in 2014 to 2020 and 2 to 3 points after 2021, so this floor is mostly 14 to 24 pips early and 10 pips late; for gold (spread 0.09 to 0.41 dollars) it is the only variant that binds. The decision-bar spread is known at decision time (no look-ahead). Implemented by widening `sl_raw` in the candidate frame (the sim cannot take a per-pair floor); a test proves this equals the sim's own widening. |

Four treatments against one control = a family of 4. An ATR-based floor is not tried: it needs ATR columns the candidates do not carry
and would add a feature-construction step (and a look-ahead test) for a variant that I have no prior reason to prefer.

## 5. Design

* Window: `start=2014-01-01`, `end=2021-06-01` passed to `prepare_bars` for every symbol; the worker asserts that no bar at or after
  `end` is loaded and that every candidate time is before `end`. Base config = `--live-settings`: `SimConfig(fixed_risk_pct=0.25,
  friday_flatten_utc=20.0)` plus the single change `min_sl_pips` (all other fields are the sim defaults, as in stage 0).
* Null (revision 1, see the end): the primary null is the **`reflect` geometry** of `research/nulltest.py` (a flipped trade is
  reflected about the decision-time mid: entry, stop and target), kind N, 60 seeds (0 to 59) per variant. The original **`mirror`**
  geometry (entry kept, stop mirrored; what stage 0 used) is run on seeds 0 to 29 (kind NM) and reported next to it. Each seed flips
  directions with `default_rng(seed).random(n) < 0.5` exactly as in stage 0, **the same flips for every variant and for both geometries**
  (pairing). Why 60: the seed-to-seed SD of the null mean is 0.018 R (stage 0), so the Monte Carlo SE of a
  60-seed mean is 0.0023 R, ten times below the 0.02 R effect threshold; paired differences are tighter still. Seeds 0 to 59 are a subset
  of stage 0's 200, so the control with the `mirror` geometry (NM, 30 seeds, sd of the mean about 0.003) must reproduce stage 0's null mean (-0.150).
* Real signals: one deterministic run per variant on the unflipped candidates.
* Frictionless diagnostics, seeds 0 to 19, every variant, on the `reflect` geometry: L1 = no commission, spread 0, no spread filter (breakeven and Friday flatten
  kept); L2 = L1 plus no breakeven plus no Friday flatten (a pure 3R / 1R stop-and-target geometry). Expectation stated in advance
  for L2: near 0 R and a win rate near 25 % if the simulator is unbiased. The decompose log suggests it is not (-0.137 R); if L2 is
  far from 0 at the control, the null differences are reported as "convention plus cost" and the split by subtype carries the
  interpretation.
* Statistics: every contrast uses one paired week-block bootstrap (2,000 resamples, seed 12345, whole ISO weeks of `open_T` resampled
  jointly across all runs of all variants, END trades excluded), the same family of code as `nulltest.paired_skill`. A unit test checks the
  generic estimator reproduces `paired_skill` on the skill contrast.

## 6. Metrics

**Primary:** null mean R per trade at each floor, N(v), and its paired contrast with control D_null(v) = N(v) - N(C10) with SE. Cost per R
= mean(commission / risk) (exact) + mean(spread at entry / stop distance) (a proxy: entry spread only), reported next to the measured
change in N.

**Secondary (all reported, none gate the decision unless stated):** trades (null mean, actual), trades per month and total R per month
(trades x mean R / months in the window, about 89) since an FTMO objective cares about R per unit time, not per trade; win rate (net P/L > 0, as `summarize`);
exit mix (SL / TP / BE / FLAT shares) and mean R by exit reason; average hold in minutes; share of trades pinned at the floor; mean stop
in pips; stage-0 funnel counts (spread_rel, correlation, max-open, cooldown, flattened); real-signal expectancy A(v), D_act(v) =
A(v) - A(C10) with SE; skill(v) = A(v) - N(v) via `nulltest.paired_skill` and D_skill = D_act - D_null; profit factor, return, max equity
drawdown, worst trade R, worst intraday low and days with intraday low at or below -3 % and -5 % (`metrics.day_table`, Prague day
as FTMO); the matched-trade split (same order at floor v and at C10 versus orders that exist at only one of them); null and real
results by pair, by entry subtype (MARKET / LIMIT / STOP), by calendar year (descriptive).

## 7. Decision rule

Let delta = 0.02 R (about one stage-0 skill SE, about 13 % of the 0.150 drag) and z = 2.24 (one-sided 0.0125, Bonferroni over the 4
treatments).

* **Cost effect (null):** REDUCES if D_null >= +delta and D_null - z SE_null > 0; INCREASES if D_null <= -delta and D_null + z SE_null < 0;
  otherwise NONE.
* **Real signals:** HELPS if D_act >= +delta and D_act - z SE_act > 0; HURTS if D_act <= -delta and D_act + z SE_act < 0; otherwise
  NEUTRAL. A NEUTRAL with SE_act above delta is reported as "not separable from noise", with the minimum detectable change 2.8 SE_act, not as
  "no effect".
* **Verdict per treatment:** HELPS / HURTS as above; COST-ONLY when the cost effect is REDUCES and the real signals are NEUTRAL (the
  mechanical drag is lower, the improvement is not separable on the real rules); otherwise NEUTRAL. A treatment can only be listed as a
  candidate for a FINAL confirmation if it is HELPS, its trades per month are at least 80 % of the control's, and D_act has the same
  sign in at least 5 of 8 DEV years. DEV can only nominate; it can never confirm.
* The monotone dose response of N over C10 < F15 < F20 < F30 is a plausibility check on the cost story, not a test.
* Everything by pair, year, subtype, with and without gold is descriptive; no decision uses it. If the headline and the no-gold
  variant disagree in sign, that is stated.

## 8. What would stop me or change the interpretation

* The control (C10) `mirror`-geometry null mean (NM, seeds 0 to 29) differs from stage 0's -0.150 by more than 0.01 R, or the control actual run does not
  give 3,970 trades and -0.131 R: stop, find the cause, do not interpret the other floors.
* Any loaded bar or candidate at or after 2021-06-01: stop (the worker asserts this).
* The L2 sanity run far from 0 at the control (expected from the decompose log): not a stop, but the cost interpretation is then
  explicitly limited to the change and the null result is read together with the subtype split.
* A crashed run is repeated unchanged and logged. Parameters are not tuned after seeing results; no variant is added.

## 9. What this experiment does not do

No change to live code or to `sim.py`/`signals.py`; no recommendation to change `MIN_SL_PIPS`; no FINAL run; no test of stop placement
logic (swing lookback, buffer), no ATR stop, no change of the 3R ratio or breakeven (the 2-pip breakeven buffer is absolute, so it shrinks in R
as the stop widens, and that effect is part of the measured floor effect, not isolated). The daily-loss figures use the Prague midnight day
but not FTMO's exact equity rules.

## Log of changes after registration

* Revision 1 (below) was made before any run of this experiment.
* 2026-10-29, after the run and a first look at the analysis output (no rule or parameter changed): an analysis bug. Weeks were bucketed
  into calendar years by the Monday of the ISO week, which created a phantom "2013" year (the week of 2013-12-30 holds the first days of
  2014) and made the by-year sign count out of 9 instead of the 8 DEV years the rule names. Fixed to the ISO year (Monday + 3 days) and the
  analysis re-run on the same stored simulation output; the rule text, thresholds and variants are unchanged.
* The paired `paired_skill` outputs are stored as `skill_paired` (the generic-estimator skill contrast is `skill`; they agree, and a test
  checks the generic estimator reproduces `paired_skill`).

## Revision 1 (2026-10-29, before any run): null geometry

The lead's null-bias audit found that the frictionless random-direction null loses about 0.12 R because `nulltest.mirror` keeps the
entry price and only flips the stop: a far retracement LIMIT becomes a far STOP order in the other direction, and in the simulator STOP
entries that exit on their fill bar lose about 2.1 R on average (the fill bar can open beyond the stop before price reaches the entry).
A second effect, about 0.045 R and shared by real and null, is that gapped stops fill at the worse price while targets never pay better
than the target. `nulltest` now offers `reflect` (the whole trade reflected about the decision-time mid), same stop distance and entry
mechanics as the real trade. Consequences for this plan (nothing else changes):

1. The primary null is `reflect`; `mirror` is run on 30 seeds and reported next to it so the difference is visible. The decision rule
   (section 7) uses the `reflect` null for D_null; the `mirror` contrast is reported with its own classification but does not decide.
2. Frictionless checks L1 / L2 use `reflect` at every floor. Their expectation (near 0 R, win rate near 25 % for L2) is now a real test of
   the new null: a residual at the control is reported at every floor, including whether it depends on the floor.
3. The null level is not read as "zero-skill truth": only differences between floors are interpreted as cost effects. Real-signal results
   are reported next to the null results, not only as differences from them, and are unaffected by the geometry.
4. The reproduction check is on the `mirror` control (it is the geometry stage 0 used).
5. Cost: 5 variants x (60 N + 30 NM + 20 L1 + 20 L2 seeds) plus 5 real-signal runs, about 655 simulations.
This revision does not use any result of this experiment.
