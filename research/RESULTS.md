# Results — faithful backtest + Monte Carlo (run 2026-10-26)

Data: 6 pairs (EURUSD GBPUSD USDJPY AUDUSD USDCAD XAUUSD), M5 from 2025-06-19 (XAUUSD 2025-05-23) to 2026-10-26 (~16 months),
USD account $100k, FTMO commission $2.50/lot/side, per-bar broker spreads. Live logic replayed as-is (sweep bug included).
**Optimistic upper bound**: no news filter, swaps, real slippage or requotes. ~16 months is short and one market regime.

## Headline (base, faithful)

| | |
|---|---|
| Trades | 825 (≈48 / month) |
| Exit mix | SL 58.7% · breakeven 26.9% · TP 14.4% |
| Win rate (net P/L > 0) | 36.4% |
| Expectancy | **−0.205 R** per trade, 95% CI [−0.31, −0.10], t = −3.8 |
| Profit factor | 0.63 |
| Return / max drawdown | −41.7% / −44.2% (equity) |
| In-sample (first 70%) / out-of-sample | −0.258 R (629 trades) / −0.036 R (196 trades, CI wide) |
| Median risk actually used | ≈0.25% of balance (0.5% scaled down by ATR/regime factors) |

## The signal's direction carries no detectable edge

Null test (`nulltest.py`): same 825-ish trades (cycle times, entry prices, stop distances, sizing, gates, costs) but each
trade's direction flipped at random, 40 seeds:

* random-direction null: **−0.177 R** (sd 0.038); real strategy −0.205 R → z = −0.74, p(null ≤ actual) = 0.20
* mirror every trade: −0.170 R

→ The signals are statistically indistinguishable from a coin flip, and any skill is small (≈ −0.03 ± 0.04 R vs null).
The losses come from the framework around the signal, not from signals pointing the wrong way.

## Where a zero-skill signal's −0.17 R goes (`decompose.py`, random directions, 20 seeds)

| Configuration | mean R |
|---|---|
| all frictions | −0.173 |
| − commission | −0.128 (commission ≈ −0.045 R/trade) |
| − spread (and spread filter) | −0.134 |
| − pessimistic same-bar rule (neutral) | −0.084 (**≈ 0.05 R of this is my conservative modelling assumption**) |
| − breakeven rule | −0.049 ± 0.05, win rate 25.0% = the theoretical 1-in-4 for a 3R target |

Breakeven-at-1R also costs ≈ 0.035 R. With frictions removed and no breakeven the simulator returns ≈ 0, as it should.

## Risk facts the live bot never reported

* **Weekend gaps:** 26 trades closed at the Sunday reopen, summing −35 R; four of them gapped through the stop for
  −19.7 R (USDJPY short, ≈ −9.8% of the account at 0.5% risk), −8.3 R, −3.0 R, −2.2 R. The bot never flattens before the weekend.
* **Late entries:** 72 trades opened at/after 20:00 UTC averaged −0.57 R (rollover spreads/whipsaw).
* About 46% of fills are STOP orders: the live code turns an FVG-midpoint LIMIT into a STOP when price is on the wrong side.

## Monte Carlo (base trades; resampling cannot create edge)

* iid bootstrap: P(profit) 0.015%; median −41%; 95th-percentile worst drawdown −57%.
* Order shuffle: max drawdown −43% median — sequence luck is not the issue, the mean is.
* FTMO-style challenge (10% target, 5% daily, 10% max loss, day-block bootstrap): pass 0.2%, fail by max-loss 52%, unresolved at 90 days 48%.
  Phase-2-style 5% target: pass 4.7%. Daily-loss breaches 0% because the bot's own 3% gate stops trading first.
  (Rules are assumptions — verify against FTMO's current terms.)
* Cost stress: extra 0.1–0.3 R per trade and 10–50% missed trades only worsen an already negative mean.

## Sensitivity variants (exploratory — do NOT pick a winner from these; each has ~800–1100 trades)

| Variant | trades | expR | PF | return | OOS expR |
|---|---|---|---|---|---|
| base (faithful) | 825 | −0.205 | 0.63 | −41.7% | −0.036 |
| fixed sweep | 1127 | −0.173 | 0.69 | −42.7% | −0.032 |
| no breakeven | 732 | −0.142 | 0.75 | −33.5% | +0.089 |
| RR 2 | 844 | −0.218 | 0.61 | −44.5% | −0.140 |
| no regime filter | 953 | −0.219 | 0.60 | −67.2% | −0.058 |
| spread ×1.5 / ×2 | 770 / 682 | −0.235 / −0.253 | 0.60 / 0.58 | −43.7% / −41.7% | −0.126 / −0.216 |
| neutral intrabar | 825 | −0.214 | 0.62 | −43.0% | −0.049 |

Every variant is negative in full sample. Fixing the sweep does not rescue it. The monthly 5% cap and the stale-snapshot quirk never mattered.

## Validation behind these numbers

56 tests pass (live-code equivalence, no-look-ahead, hand-checked mechanics, Monte Carlo). All 487 SL/TP exits re-derived independently
from raw bars (0 mismatches). Replay vs the real live bot's Excel log, 20–27 Jul 2026: direction 99.9%, probability 99.6% of 931 cycles.

## What this does and does not say

* It says: as currently built (3R target, breakeven at 1R, structure stops, commission, no weekend flatten), the bot has negative
  expectancy on 16 months of data, and its signals show no measurable directional skill versus random.
* It does not say the underlying SMC ideas can't work, that this regime is representative, or what live results would be.
* To be viable, a signal would need roughly +0.15 R of real directional edge just to cover frictions at these settings.
  Candidate changes (weekend flatten, no entries after ~20:00 UTC, breakeven at ≥1.5R or removed, limit-only entries) are untested
  hypotheses; test them on data the choices were not made on (more history: raise MT5 "Max bars in chart" and re-fetch).

## Stage 0 (run 2026-10-29): is there directional skill on the development window?

Pre-registered in `research/reports/stage0_prereg.md` (revision 1, before any DEV run). Window 2014-01-01 to 2021-05-31, six pairs, live
settings (fixed 0.25 % risk, Friday flatten 20:00 UTC), 200 random-direction null seeds per run, paired week-block bootstrap. Code state:
git HEAD `bd9c09a` plus uncommitted changes (diff hash in each `research/output/stage0_R*/null_summary.json`). FINAL (2021-06 to 2025-04) was not touched.

**Verdict by the pre-registered rule: INCONCLUSIVE.** No lower bound above zero, and not every upper bound below +0.05 R.

| Run | Trades | Actual expR | Null mean expR (sd) | Skill = actual minus null | SE | 95 % CI | LB (Bonferroni) |
|---|---|---|---|---|---|---|---|
| R2, live rules (dead sweep) | 3,970 | -0.131 | -0.150 (0.018) | **+0.020** | 0.020 | [-0.019, +0.058] | -0.025 |
| R4, working sweep (a different strategy) | 5,284 | -0.141 | -0.156 (0.015) | **+0.015** | 0.017 | [-0.019, +0.049] | -0.023 |

Variants of the same simulation (R2 skill, upper bound): without XAUUSD +0.021 (0.060); clean subset (GBPUSD 2019 removed) +0.019 (0.058);
both +0.021 (0.061). R4: +0.018 (0.053), +0.015 (0.049), +0.017 (0.053). The kill needed all eight upper bounds below 0.05; six of eight were above it, so no kill.

What the numbers say, in plain terms:
* The skill estimate is small and positive but indistinguishable from zero (z about 1.0 in both runs); an edge above about +0.06 R is excluded at roughly 97.5 % one-sided confidence, a skill of 0 to +0.05 R is not.
* **The cost and convention drag is the bigger fact.** A random-direction version of the same trades loses -0.150 R per trade (-0.156 in R4); the real rules lose -0.131 R. Over 3,970 trades at 0.25 % risk that is -73 % return and a -75 % maximum equity drawdown. Even at the top of the confidence interval (+0.058) the live rules would still lose about -0.09 R per trade.
* Win rate 41 % (R2) and 40.5 % (R4); the deterministic mirror of every trade loses -0.182 R.

Descriptive only (no decisions; each year has 160 to 630 trades, SE 0.04 to 0.10): skill by year R2: 2014 +0.044, 2015 +0.004, 2016 +0.069, 2017 -0.077, 2018 -0.040, 2019 +0.004, 2020 +0.124 (the only year whose own lower bound is above 0, one of eight looks), 2021 (5 months) +0.008. By pair: EURUSD +0.051, AUDUSD +0.040, USDJPY +0.027, GBPUSD -0.001, USDCAD -0.005, XAUUSD -0.002 (SE 0.04 to 0.07 each).

Diagnostics (should be close for a valid null): `(actual + mirror_all) / 2` = -0.157 against null mean -0.150 (gap 0.006, inside the 0.02 flag); BUY share 52.7 % actual vs 50.0 % null; the actual run is blocked by the correlation filter about twice as often as the nulls (758 vs 374), as expected when correlated USD pairs all point the same way.

Caveats: the estimand is direction plus structure-stop placement relative to a mirrored stop; DEV costs use imputed spreads where the broker recorded zero; R2 and R4 are overlapping looks, not independent tests; the paired SE (0.020) came out where the pre-registration expected it (0.02), not at the cautious 0.03.

## Same-bar convention audit (run 2026-10-29, burnt window 2025-05-23 onward, base run, 861 trades)

Pre-registration `research/reports/same_bar_audit_prereg.md` (with its logged deviation). **Result: the simulator's rule "the stop wins a tie within
an M5 bar" decided 0 of 861 trades.** No stop-out (SL or breakeven) shared its exit bar with the take-profit, on the fill bar or elsewhere
(`research/same_bar_bound.py`; self-check 123 of 123 take-profit exits show their target inside the mapped bar). The upper bound on the expectancy
change from any different tie rule is 0.000 R per trade. The earlier `neutral_intrabar` variant (voiding fill-bar exits) was slightly worse,
-0.219 R against -0.210 R. So **the earlier estimate of about 0.06 R "same-bar pessimism" is withdrawn**: the residual drag on a skill-less signal is
cost and structure, not the intrabar convention. No M1 data was needed and the simulator default is unchanged.

## Cost decomposition on DEV (run 2026-10-29): and a failed sanity check

`python -m research.decompose --tag decompose_dev ...` (random directions, 40 seeds, DEV 2014-01 to 2021-05, live settings as the base; note `research/reports/cost_decomposition_prereg.md`).

| Configuration (cumulative) | mean R per trade (sd across seeds) | win % | trades |
|---|---|---|---|
| c0 all frictions | -0.152 (0.018) | 39.7 | 4,027 |
| c1 - commission | -0.108 (0.018) | 41.1 | 4,023 |
| c2 - spread and spread filter | -0.111 (0.016) | 42.2 | 5,799 |
| c3 - breakeven | -0.113 (0.018) | 25.0 | 5,317 |
| c4 - Friday flatten (every friction off) | **-0.137 (0.017)** | 22.8 | 5,319 |

One at a time from the base: no commission -0.108, no spread -0.167 (more trades pass the spread filter), no breakeven -0.150, no Friday flatten -0.172.

* Commission costs about 0.044 R per trade. Removing the spread (with its filter) does not improve the mean (-0.152 to -0.111 includes the commission step; spread alone -0.167), because the filter also changes which trades are taken, so spread and filter cannot be separated this way. Breakeven removal changes nothing here (win rate 41 % to 25 %, mean unchanged). The Friday flatten helps by about 0.02 to 0.025 R (gap exposure).
* **Sanity check failed.** The pre-registered expectation was that with every friction removed the mean sits near 0 R. It sits at -0.137 R (-0.113 with the flatten kept; win rate 22.8 to 25 %). The mirrored random-direction null therefore carries a **structural negative bias of about 0.11 R per trade whose source is not yet identified**. The earlier 16-month decomposition reached about 0 only after also switching the pessimistic same-bar rule to neutral and credited about 0.05 R to it; the later same-bar audit found no ties in the real trades and a slightly *worse* real result under the neutral rule, so the two do not agree. A null-bias audit (`research/null_bias_audit.py`) is running to locate it (entry type after the flip, fill-bar exits, gap fills).
* **Consequence for stage 0:** the stage-0 skill estimate is the real run minus this biased null. If the bias is larger for the null than for the real trades, +0.020 R overstates skill; stage 0 stays INCONCLUSIVE, but its skill number must not be read as unbiased until the audit is done. Absolute levels of the null (-0.150 R) are also not a statement about a skill-less trader.

## Stage 0, corrected (run 2026-10-29, same DEV window 2014-01 to 2021-05): VERDICT KILL

Revision 2 of `research/reports/stage0_prereg.md` (written after the first result, after the cost decomposition's sanity check failed; disclosed there). Instrument changes only:
null geometry `reflect` (whole trade reflected about the decision-time mid, replacing the biased `mirror`) and, for the primary runs, the cleanest simulator conventions
(neutral fill-bar rule, targets paid at gapped prices). Pre-run gate (frictionless reflected null within +-0.03 R of 0): observed -0.025 R, passed narrowly. Thresholds and rule unchanged; FINAL untouched.

| Run | Conventions | Trades | Actual expR | Null mean expR | Skill | SE | 95 % CI | UB95 |
|---|---|---|---|---|---|---|---|---|
| R2' live rules (dead sweep), **primary** | clean | 3,941 | -0.120 | -0.126 | **+0.006** | 0.020 | [-0.033, +0.044] | 0.044 |
| R4' working sweep, **primary** | clean | 5,212 | -0.131 | -0.127 | **-0.004** | 0.017 | [-0.037, +0.029] | 0.029 |
| R2' live rules, secondary | defaults | 3,970 | -0.131 | -0.139 | +0.008 | | | |
| R4' working sweep, secondary | defaults | 5,284 | -0.141 | -0.139 | -0.002 | | | |

All eight upper bounds (R2' and R4', each with / without gold and on the clean subset) are below the +0.05 R kill threshold; the largest is 0.049 (R2', clean subset without gold). No lower bound is above 0.
**Verdict: KILL, as defined in the pre-registration: an edge above +0.05 R per trade is excluded at about 97.5 % one-sided confidence on DEV, for the live rules' signal and for the working-sweep variant.**
Diagnostic: `(actual + mirror_all)/2` against the null mean: -0.122 vs -0.126 (R2'), -0.131 vs -0.127 (R4'), gaps of 0.004.

* How wrong the first number was: the biased null moved the skill estimate by less than I feared (I guessed up to 0.1 R): R2 +0.020 to +0.008 (defaults), R4 +0.015 to -0.002. The null's absolute level with frictions on barely changed (-0.150 to -0.139); the frictionless level changed a lot (-0.119 to -0.059).
* Skill is about zero; the live rules lose about -0.12 to -0.13 R per trade because of the framework (costs about 0.044 commission plus spread, stops pinned at the 10-pip floor, structure and gaps), not because of the signal direction. Over 3,941 trades at 0.25 % risk: -70 % return, -72 % maximum equity drawdown.
* Consequence per the pre-registration: the pipeline and ML arms that build on the SMC signal stop; the time goes to cost hygiene (stop-floor experiment, spread record) and to new signal sources (slow factors, the ML engineer's raw-feature approach). The first (biased) stage-0 numbers above stay on record as superseded.

## Minimum-stop-floor experiment (quant subagent, run 2026-10-29, DEV 2014-01 to 2021-05; full report `research/reports/stop_floor.md`)

Pre-registered (`research/reports/stop_floor_prereg.md`); FINAL untouched; 27 tests. The floor is `min_sl_pips` (live `MIN_SL_PIPS`, 10): any structure stop tighter than it is widened to it and the target set at 3x.
Null = reflected random-direction (60 seeds); real = one run of the live rules; changes versus the 10-pip control.

| Floor (pips) | 10 (live) | 15 | 20 | 30 |
|---|---|---|---|---|
| Null mean R | -0.136 | -0.119 | -0.103 | -0.063 |
| Null change vs 10 (SE) | | +0.017 (0.010) | +0.034 (0.013) | +0.073 (0.014) |
| Real mean R | -0.131 | -0.102 | -0.094 | -0.077 |
| Real change vs 10 (SE) | | +0.029 (0.014) | +0.037 (0.020) | +0.054 (0.024) |
| Real trades per month | 44.6 | 43.3 | 41.4 | 34.7 |
| Paired skill vs null (SE) | +0.006 (0.019) | +0.017 (0.020) | +0.009 (0.020) | -0.014 (0.022) |
| Real max equity drawdown / worst Prague-day low | -75 % / -4.6 % | -65 % / -4.5 % | -60 % / -3.2 % | -46 % / -1.9 % |

* **Verdict by the pre-registered rule:** 20 and 30 pips are "cost-only" (the null drag falls; the real-signal gain is not separable from noise at the bar, F30's lower bound is -0.001); 15 pips and the pair-aware variant are neutral; none helps, none hurts, none is nominated for a FINAL confirmation.
* **Mechanism (30 pips):** commission falls 0.044 to 0.018 R and entry spread over stop 0.047 to 0.025 R (about 0.047 of the 0.073); the rest is fewer fill-bar stop-outs (3.8 % to 1.5 %) and a changed exit mix (more Friday-flatten exits, 5 % to 15 %). Skill stays at zero at every floor.
* **Price:** trades -22 % (fewer, because positions last 3.6 times longer and hit the max-open and correlation gates), average hold 3.5 h to 12.5 h, targets 90 pips away. Real rules still lose -2.7 R per month (-5.8 at 10 pips); the floor changes the size of the loss and the drawdown profile, not its sign.
* **XAUUSD is separate:** its structure stops are 1.2 to 2.9 dollars, so no scalar floor up to 30 "pips" (0.30 dollars) binds; a gold floor would have to be in dollars or ATR terms.
* **Not a live recommendation.** A live change would be `MIN_SL_PIPS` (`utils/risk_manager.py`), needs a pre-registered floor, the reviewer and one FINAL confirmation (alpha 0.025, trades per month at least 80 % of control).
* **Mirror vs reflect:** the biased `mirror` null overstates the gain at 15 and 20 pips (+0.035, +0.063 against +0.017, +0.034) because wider stops hide its STOP-entry artifact; they agree at 30 pips.
* **Frictionless residual:** about -0.065 R at every scalar floor (win rate 23.5 %, not 25 %), floor-independent, so only differences between floors are read.

## AI learnability study, H1 (ML-engineer subagent, run 2026-10-29, DEV only; full report `research/reports/ai_edge_approach.md`)

Pre-registered (`research/reports/ai_edge_prereg.md`) before any model was fitted; FINAL not read (the loader refuses ends after 2021-06-01); classical ML only; 39 tests.
Label: net R of a stateless triple-barrier trade (spread + commission, 1.5 x ATR stop, 3R target, 48 h limit, Friday flatten), H1 decisions, five FX pairs, purged expanding walk-forward by half-year.
Primary statistic: log-loss gain D1 of directional features over a non-directional model (time of day, volatility, spread, stop geometry).

* **Verdict: no stable directional structure net of cost.** Holm lower bounds of D1: LR -5.8e-4, HGB_S -5.2e-4, HGB_M -1.9e-6 (near miss). LR and HGB_M beat the block-permutation null (z 3.2, 4.8), so it is not pure noise,
  but all of it is 2019H2 (directional AUC 0.61 to 0.70 there; 0.40 to 0.57 in every other half-year; without 2019H2 pooled AUC 0.495 to 0.501, D1 negative; that exclusion is post hoc). 2019H2 is unexplained.
* Economic reading: top-decile net R of HGB_M +0.029 R [-0.023, +0.086]; at 24 h top/bottom decile +0.11 R [-0.01, +0.23] net, mostly 2019.
* Cost in R by decision horizon: M15 0.086, H1 0.047, H4 0.021, D1 0.0085. The live bot's pinned 10-pip stops put it in the M15-like regime.
* Non-directional structure that does exist: forward range is forecastable (IC 0.4 to 0.5), cost per R varies by hour through the stop (EURUSD 0.035 to 0.056 R); returns mean-revert mildly (variance ratio 0.925 at 24 h).
* Power: minimum detectable top-decile gain about 0.06 R, directional AUC about 0.022 above 0.5; a true 0.05 R edge is found about 60 % of the time.
* Recommendation (adopted in GAMEPLAN section 13): run the same protocol at H4 and D1; add a volatility / time-of-day cost layer; skip richer H1/M15 models, sequence models, LLMs, and SMC meta-labelling.
* **New data finding: spread sentinel.** The broker's spread field is exactly 50 points on 14 to 93 % of FX M5 bars from 2020-05 to 2021-09 (EURUSD 58 % of 2020, 65 % of 2021); read back directly from the terminal on 2026-10-29 (EURUSD 2020-11: 94.6 % at 50, 2022-03: 0 %),
  so it is the broker's history, not our pipeline. Likely a placeholder, not verifiable. Stage 0 used it as is; see the robustness re-run below.

### Stage 0 robustness re-run before the spread sentinel (2026-10-29, post hoc, disclosed; DEV 2014-01 to 2020-04-30, 60 seeds)

Same flags as the primary runs (`--live-settings --null-geometry reflect --clean-conventions`), only `--end 2020-05-01` and `--seeds 60` (tags `sens_pre2020_R2`, `sens_pre2020_R4`; verdict file `research/output/sens_pre2020_verdict.log`). A sensitivity check, not a new test: it cannot change the registered verdict.

| Run | Trades | Skill | SE | Upper bound | Lower bound (Bonferroni) |
|---|---|---|---|---|---|
| R2 (live rules) | 3,447 | -0.004 R | 0.021 | 0.037 | -0.050 |
| R4 (working sweep) | 4,539 | -0.004 R | 0.018 | 0.031 | -0.045 |

All eight upper bounds are below +0.05 R (largest 0.039): **KILL holds on the window without the 50-point spread placeholder.** The sentinel does not drive the stage-0 verdict.

### Stage 0 with the 50-point spread placeholder treated as missing (2026-10-29, post hoc sensitivity, full DEV 2014-01 to 2021-05, 60 seeds)

New flag `--sentinel-missing` (`research.data.sentinel_mask`; default off; reviewer PASS round 2; `test_sentinel_missing.py`). Same settings as the primary runs otherwise (`--live-settings --null-geometry reflect --clean-conventions`);
tags `sens_sentinel_R2`, `sens_sentinel_R4`, verdict in `research/output/sens_sentinel_verdict.log`. The imputed spreads are an estimate (same-hour median of clean bars).

| Run | Trades | Real R | Null R | Skill | SE | Upper bound |
|---|---|---|---|---|---|---|
| R2 (live rules) | 4,173 (3,941 primary) | -0.100 (-0.120) | -0.103 (-0.126) | +0.003 | 0.020 | 0.041 |
| R4 (working sweep) | 5,548 | | | -0.005 | 0.017 | 0.028 |

* Absolute-spread-cap blocks fall from 1,689 to 79 (R2); the placeholder inflated the drag by about 0.02 R, and the random-direction version still loses about 0.10 R per trade.
* **KILL holds** (all eight upper bounds below 0.05; largest 0.044). Skill is zero within noise in both treatments; the verdict does not depend on the 50-point placeholder.

## AI slow-horizon learnability study A2' (ML-engineer subagent, run 2026-10-30, DEV only; pre-registration `research/reports/ai_slow_prereg.md`)

Question: at H4 and D1 decision bars (5 FX pairs, decisions 2003-01 to 2021-05, out-of-fold 2006-01 to 2021-05, 31 half-year walk-forward blocks, 14-day purge), do causal price/volatility/time/USD-factor features predict
the sign of a stateless triple-barrier net R (stop k x ATR14 of the decision timeframe, 3R target, Friday flatten, H1 path, sentinel-aware spread, nominal pre-2010 spreads) beyond a non-directional model?
Primary statistic D1 = log-loss gain of directional over non-directional features (logistic, C 0.05); K = 4 variants, Holm 0.05/4, whole-pipeline shifted-feature permutation null (B = 100), stability and falsifier rules fixed beforehand.
FINAL was not read. The rules were applied mechanically. Code state: `research/ai/slow*.py`; outputs `research/output/ai/slow/`.

**Verdict: no variant is learnable; all four are falsified, so by the pre-registered rule the slow-horizon family stops.**

| Variant | C1 (Holm + 4-week) | C2 (permutation) | S (stability) | F (falsifier) | Overall |
|---|---|---|---|---|---|
| H4_k10 | fail (D1 -7.2e-4 [-1.4e-3, -1.9e-5]; Holm p 0.98) | fail (null mean -1.1e-3, sd 9.4e-4; p 0.28) | fail (6 of 16 years positive; leave-one-out min -8.9e-4) | falsified (0 of 3 eras at 0.52) | not learnable |
| H4_k15 | fail (-9.6e-4 [-2.2e-3, +2.3e-4]; p 0.94) | fail (null -2.1e-3, sd 8.6e-4; p 0.13) | fail (7 of 16; min -1.3e-3) | falsified (1 of 3) | not learnable |
| D1_k10 | fail (-2.7e-3 [-5.5e-3, -3.6e-5]; p 0.98) | fail (null -4.8e-3, sd 2.1e-3; p 0.20) | fail (5 of 16; min -3.3e-3) | falsified (1 of 3) | not learnable |
| D1_k15 | fail (-3.3e-3 [-6.7e-3, -5.3e-5]; p 0.98) | fail (null -5.5e-3, sd 2.5e-3; p 0.25) | fail (5 of 16; min -3.9e-3) | falsified (1 of 3) | not learnable |

The directional features make the log-loss WORSE than the non-directional model in all four variants (D1 negative; three of the four intervals exclude zero on the negative side); they do better than the permuted-feature null only in the sense that the null (noise features) is worse still.
Directional AUC overall (within a bar; 95 % week-block interval): H4_k10 0.5002 [0.487, 0.514], H4_k15 0.5015 [0.487, 0.517], D1_k10 0.5049 [0.482, 0.524], D1_k15 0.5053 [0.482, 0.524]. Pooled AUC of F is not above that of the non-directional model (H4_k10 0.5759 against 0.5775).

Logistic model by era (D1 gain x1e3 / directional AUC; the falsifier needs >= 0.52 in two of three eras):

| Variant | 2003-2009 (nominal spreads; OOF from 2006) | 2010-2013 | 2014-2021 |
|---|---|---|---|
| H4_k10 | -2.12 / 0.486 | -0.06 / 0.517 | -0.33 / 0.498 |
| H4_k15 | -2.65 / 0.492 | +0.78 / 0.5275 | -0.99 / 0.492 |
| D1_k10 | -4.26 / 0.512 | +0.87 / 0.530 | -3.83 / 0.485 |
| D1_k15 | -5.34 / 0.514 | +0.97 / 0.529 | -4.60 / 0.485 |

Only 2010-2013 (four years, the era of recorded 9 to 11 point spreads) ever reaches 0.52 and its intervals contain 0.5 (e.g. D1_k15 0.529 [0.493, 0.570]); the 2014-2021 era is at or below 0.5 in every variant.

Top-decile rule (side with the larger p, traded above the 90th percentile of training predictions, 14 to 23 % of bars), logistic; net R of the chosen side with extra cost subtracted as a multiple of the first-order cost; gain = chosen minus unchosen side at the same bars:

| Variant | gain over unchosen side (R) | net R x1 | net R x1.5 | net R x2 |
|---|---|---|---|---|
| H4_k10 | +0.024 [-0.002, +0.049] | -0.016 [-0.046, +0.015] | -0.038 [-0.068, -0.008] | -0.061 [-0.091, -0.030] |
| H4_k15 | +0.029 [+0.003, +0.054] | +0.003 [-0.024, +0.032] | -0.012 [-0.039, +0.017] | -0.026 [-0.053, +0.003] |
| D1_k10 | +0.020 [-0.027, +0.065] | +0.016 [-0.032, +0.063] | +0.008 [-0.040, +0.055] | -0.001 [-0.048, +0.047] |
| D1_k15 | +0.027 [-0.020, +0.070] | +0.020 [-0.028, +0.064] | +0.014 [-0.034, +0.058] | +0.008 [-0.040, +0.052] |

No chosen-side net R has a lower bound above zero at any cost level. By era the H4 net R is negative in 2003-2009 and 2014-2021 and positive only in 2010-2013 (+0.05 for H4_k10).

Descriptive gradient-boosted results (HGB_S_slow / HGB_M_slow; no confirmatory role): D1 gain between -2.0e-3 and +5.7e-5 (none positive with an interval above 0); directional AUC 0.509 to 0.512 (intervals contain 0.5); HGB_S on H4_k10 shows a top-decile gain of +0.039 [+0.010, +0.069] but chosen-side net R of -0.003 at x1 and -0.048 at x2.
**One surprising cell, reported as is:** D1_k15 with HGB_M_slow has top-decile gain +0.060 [+0.012, +0.107] and chosen-side net R +0.056 [+0.006, +0.105] at x1, +0.050 [+0.0001, +0.099] at x1.5, +0.043 [-0.006, +0.092] at x2, positive in all three eras (+0.049, +0.063, +0.056), although its log-loss gain is negative (-2.0e-3) and its directional AUC is 0.511 [0.493, 0.528].
It is one of eight descriptive boosted-model cells (twelve model-variant cells with the logistic ones), was not a registered hypothesis, and cannot rescue the family under the pre-registered rules. If anyone wants to pursue it, it needs its own pre-registration, a different holdout, and the multiplicity of this search stated; FINAL has not been touched.

Reading, with the lead's pre-stated interpretation: this is a null for effects of the size the design can see (simulated power: H4 sees a realised directional AUC of about 0.52, D1 about 0.545; the full-pass minimum detectable gain was about 0.05 to 0.12 R over the unchosen side). Beyond that, the observed directional-AUC intervals themselves rule out an AUC above about 0.514 to 0.517 (H4)
and 0.524 (D1), which corresponds to a gain of roughly 0.03 to 0.05 R. A smaller effect is neither seen nor excluded, and at D1 cost (0.008 to 0.02 R) a 0.02 R edge would be attractive but is undetectable with 960 weeks of five correlated pairs. Together with the H1 study (null, carried by 2019H2): no stable directional structure in OHLC + spread + time + USD-factor features at 1 hour to 4 days on this broker's 2003-2021 history.
Caveats: nominal pre-2010 spreads (an assumption); H1-path labels (agree with M5-path labels in 99.8 to 100 % of exits); one label family; one linear confirmatory model; 14-day embargo with trailing features adjacent to the test blocks.


## Sessions, pairs, weekends and loss anatomy (2026-10-31, DEV only)

Full reports: `reports/session_pair_weekend_analysis.md` (data analyst) and `reports/quant_loss_risk_review.md` (quant). Neither finds directional skill.
Analyst: 4,173 trades, -0.100 R vs null -0.104 (skill +0.004, SE 0.019); no pair and no session/weekday bucket shows skill after correction; the live bot never trades weekends.
Quant: 3,970 trades, -0.131 R, 0.091 R of it cost; an ATR-scaled minimum stop reduces the null drag by 0.04-0.10 R (hypothesis, not confirmed on FINAL); at the live 0.25% risk the 10% static loss limit is breached within 12 months in 86% of resampled paths.
The two baselines differ by treatment of the broker 50-point placeholder spread (as recorded vs imputed), not by error.
