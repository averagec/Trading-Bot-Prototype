# Gameplan: from "no demonstrated edge" to a go / no-go decision

Written 2026-10-29, revised the same day after an independent red-team review (changes listed in section 11).
16 calendar weeks (2026-11-02 to 2027-02-19, Christmas weeks slow) plus the forward demo.
Nothing here changes live code until the user asks after seeing a locked-holdout result, and then it goes through the
`reviewer` agent (CLAUDE.md). Status and results go to `progress.md` and `research/RESULTS.md`.

## 1. Where we stand (facts, with sources)

| Fact | Source |
|---|---|
| Live SMC rules, 16 months: 825 trades, -0.205 R per trade, -41.7 % return. Signal direction is indistinguishable from random (actual minus null -0.028 R, z -0.74; SE about 0.04). | `RESULTS.md`, `reports/rule_changes.md` |
| In the faithful replay the live sweep detector never fires, so the strategy has four varying components and `checks_passed` is constant. A "fixed sweep" version is a different strategy (about 1,127 trades). Any control in this plan must say which one it is. | `research/signals.py:55`, `tests/test_fidelity.py:76` |
| Unchanged live rules on the whole cache at the time (2021-02 to 2026-10): 3,509 trades, -0.116 R. Run by the reviewer on 2026-10-27, not pre-registered, aggregate only (no per-year or per-pair breakdown is known to have been produced). My arithmetic from the rounded figures implies roughly -0.09 R before 2025-05. | reviewer report; `progress.md` |
| Four alternative ideas: none passes. The pullback idea has negative skill in both windows; the daily breakout looked good on DEV (p 0.005) and failed the holdout (-0.225 R). | `reports/ideas.md` |
| Framework changes cut drag on a skill-less signal: composite H6 (Friday flatten, no late entries, no breakeven) moves zero-skill expectancy -0.177 to -0.075 R and max drawdown -44 % to -22 %. Out of sample it is directionally right but unconfirmed (+0.046 R, CI -0.096 to +0.192). Breakeven at 1.5R or 2R does nothing; removing it helps. H4 (drop STOP-type entries) is a clean negative: -0.059 R [-0.156, +0.034] on DEV. | `reports/rule_changes.md` |
| The remaining -0.075 R (with the full framework changes) was attributed to commission (0.043 R) plus my pessimistic same-bar rule (roughly 0.06 R); the two did not add up cleanly. **Update 2026-10-29: the same-bar part is withdrawn.** The tie rule decided 0 of 861 trades (`same_bar_bound.py`), so the drag is cost and structure. With the live settings the random-direction null loses -0.150 R per trade on 2014-2021 (stage 0). | `reports/rule_changes.md`, `RESULTS.md` |
| Data now: M5 about 1,000,000 bars per pair from 2013-05 (XAUUSD 2012-07), M15, H1, H4 from 2000 (XAUUSD 2004). Fetched 2026-10-29; not yet quality-checked by era. M1 only from 2024-02 (FX). | fetch log, probe |
| The LLM is veto-only (confirm or HOLD), wired into the live loop (`main.py:326`), never evaluated: no backtest includes it and nothing logs its answers. The configured model is `claude-sonnet-4-20250514`; whether that snapshot is still served is unchecked (if it is retired the veto returns HOLD on every failure and the bot trades nothing). | `agents/llm_agent.py`, `config.py` |
| Old FTMO Monte Carlo: 10 % target pass 0.2 % (base), 2.7 % with the rule changes. It models one phase, UTC days, a day-block bootstrap. | `reports/rule_changes.md`, `research/montecarlo.py` |

Honest summary: no demonstrated edge, and a probably negative expectancy. The ceiling for rule changes alone is about
break-even (skill-less -0.075 R, minus up to about 0.06 R if the audit shows my same-bar convention is that pessimistic,
leaves about -0.015 R). So the question is whether signal skill exceeds roughly +0.02 to +0.08 R; the measured skill is
-0.028 R ± 0.04. This plan is a structured search under a fixed budget, with "stop and keep the infrastructure" as an
accepted outcome.

## 2. Rules of the campaign

1. Pre-register before any run (`test-strategy-idea` skill): rule, parameters, grid, success criterion, and exactly what has been seen.
2. DEV is where parameters may move. Freeze, then one FINAL run per frozen candidate. No `--force` without the user's word.
3. Every filter or model is compared with a null that replicates its whole selection procedure, plus the trivial benchmark Z.
4. Three separate estimands, three separate error budgets (section 3). Power gate: if a test cannot detect about 0.10 R it is
   reported as "not detectable at this sample size", not run and over-read.
5. Stop rule per idea: fails DEV against its null, or three parameter revisions without movement, or FINAL used.
6. Negative results go in `RESULTS.md` in plain numbers.
7. Maker/checker: any change to `sim.py`, `signals.py` or live code goes through the `reviewer`; every pre-registration is
   attacked by an independent quant agent before it is frozen (done for this plan).

## 3. Statistics: three questions, three budgets

| Estimand | Question | Method | Error control |
|---|---|---|---|
| Mechanics (entry, stop, target, breakeven) | does this reduce drag? | effect size and paired day-cluster bootstrap CI vs base; null delta used as a cost/convention measurement only | White reality check or SPA over the pre-declared grid; no stand-alone p-values |
| Skill claims (meta-labelling, regime features, LLM veto, slow-factor strategies) | is there predictive information beyond noise? | out-of-fold AUC and Brier first (far more powerful than a thinned mean), then expectancy; whole-pipeline permutation null (permute labels, rerun fit, threshold choice and evaluation) | one small Holm family over the skill claims (at most 6) |
| Confirmation | does the frozen candidate work on unseen data? | one run per candidate on FINAL, stressed costs | at most 2 candidates, alpha 0.025 each |

The grid is fixed in the pre-registration: at most 20 one-at-a-time mechanic variants plus 8 factorial cells, with parameter
values written down, not tuned. The already-registered hypotheses H1, H3 and H6 go first as a family of 3 on data they were
not derived from.

Power, honestly: one test at 0.10 R needs about 1,260 trades (sd of R about 1.25); under correction and day-clustering
the requirement is 2,700 to 3,000 or more. FINAL has about 2,000 trades (section 4), so the minimum detectable edge is about
0.10 to 0.12 R at 80 % power; a true 0.05 R edge would be detected about 30 % of the time. Edges below about 0.10 R are
declared not detectable, not "rejected".

## 4. Data and splits

Already seen, so not blind:

* 2025-05 to 2026-10: base backtest, rule-change study, four-idea study. Descriptive only from here on.
* Aggregate full-cache expectancy and trade count 2021-02 to 2026-10 (the -0.116 R figure). The pre-registration lists this
  as "seen: aggregate only", and any hypothesis about the live rules or their derivatives is therefore not fully blind on 2021-06 to 2025-04.
  The window remains a legitimate out-of-time test of hypotheses (H1, H3, H6, new mechanics) that were generated from 2025 to 2026 evidence.

The week-1 quality check is done (`research/reports/data_quality_by_era.md`): FX years from 2014 on are usable, with flagged years
(2016 and 2017 fail one weekly-open check; GBPUSD 2019 has a 1.5-month hole; 2019 has unexplained spikes still to look at;
XAUUSD 2013 fails) that are reported with and without. Zero recorded spreads (XAUUSD 2021, EURUSD 2025 and 65 % of 2026) are
imputed, so costs there are estimates. Split, as decided from that report:

| Window | Use |
|---|---|
| 2014-01 to 2021-05 (modelled conservative costs; flagged years reported with and without) | DEV: model fitting, mechanic screening, relative comparisons |
| 2021-06 to 2025-04 (about 2,000 to 2,650 trades) | FINAL: one run per frozen candidate, at most 2 |
| 2025-05 to 2026-10 | descriptive only |
| 2026-11 onward (forward demo) | plumbing and sim-vs-live divergence check; never pooled with sim trades; never a pass/fail gate |

If pre-2021 fails quality: DEV 2021-06 to 2023-06, FINAL 2023-07 to 2025-04 (about 1,000 trades), and the plan states
up front that it cannot confirm an edge below about 0.12 R.

Known data issues to handle: old H1/H4 history fails the quality report (Saturday bars, shifted broker clock, zero spreads);
XAUUSD has 100 % zero spreads 2021-02 to 2021-09 and 30 to 40 % zero over its H1/H4 history, imputed from a global hour median
(`data.py`), so results are reported with and without XAUUSD; the H4 grid is DST-dependent; gold is treated like a 5-digit
pair for stop buffers.

## 5. Timeline

Weeks start on Monday. Dates are targets; run times and your approvals gate them.

| Week | Dates | Track | What happens | Done when |
|---|---|---|---|---|
| 0 | Oct 29 to Nov 1 | Data | M5 fetch to 2013 done; plan revised after red-team | this file |
| 1 | Nov 2 to 6 | Data | Quality report by era and pair (add a `--since/--until` option to the CLI), decide the split | split written down |
| 1 | Nov 2 to 6 | Pre-reg | Pre-register stage 0 and the same-bar audit decision rule (what result changes the sim default) | prereg dated, attacked by an independent agent |
| 1 to 2 | Nov 2 to 13 | Stage 0 | Skill estimate (actual minus null) on DEV for the faithful rules and the fixed-sweep variant. **Kill rule: if the upper 95 % bound is below +0.05 R, stop the pipeline and AI arms** and spend the remaining time on cost hygiene plus track R3 (slow factors) as the new strategy source. | number in `RESULTS.md` |
| 1 to 2 | Nov 2 to 13 | Pipeline | Same-bar audit on M1 (FX M1 from 2024-02): measure the pessimism of the same-bar rule on the base trades | measured number; sim default changes only by the pre-set rule |
| 2 to 3 | Nov 9 to 20 | Live safety (gated by your go) | `news_agent` session bug, account re-check before order placement, stop alert, check that the LLM model snapshot is still served, candidate/outcome logging with the LLM running in **shadow** mode (logged, not acting). Each through the reviewer. | reviewer PASS |
| 3 | Nov 16 to 20 | Forward demo | Restart on demo only after the live fixes (any live change resets the clock). Rules only; LLM shadow. | demo running |
| 3 to 6 | Nov 16 to Dec 11 | Pipeline | Mechanic screen on DEV (section 6) | `reports/pipeline.md`, at most 2 mechanics kept, then freeze |
| 1 to 10 | Nov 2 to Jan 8 | FX research | R1, R3, shrunken R4 (section 8) | one report per item |
| 7 to 10 | Dec 14 to Jan 8 | AI methods | A2 (after mechanics freeze, so labels match the final rules), A4 if it clears the power rule, baselines (section 7) | `reports/ai_methods.md` |
| 11 to 14 | Jan 11 to Feb 5 | Consolidate | Combine survivors into at most 2 candidates, red-team, freeze, one FINAL run each | FINAL numbers recorded |
| 15 to 16 | Feb 8 to 19 | Decision | Go / no-go report; demo divergence report | section 9 filled in |

Critical path: quality check, split freeze, stage 0, same-bar audit, sim defaults, mechanic screen, freeze, A2 labels,
candidate freeze, FINAL, decision. Slack is about 2 weeks and the holiday weeks eat most of it. Cut from the first draft:
the LLM event classifier, the event study and the regime descriptive study, and A3 as a separate arm.

## 6. Pipeline refinement: entry, stop, target, breakeven

What the null can and cannot tell us: with symmetric stops in R, a random-direction run has about zero gross expectancy for any
stop or target rule, so null deltas measure cost per R and simulator conventions (same-bar rule, fill-on-touch for limits,
gap fills), not alpha. A variant that wins on the null may just exploit the pessimistic same-bar rule. So: run the M1 audit
first, report null deltas as cost-hygiene evidence only, and re-test everything structure-dependent on the real signals,
where power is low (minimum detectable delta about 0.19 R for composites), so a mechanic is kept only on a CI that excludes
harm and a mechanism that makes sense.

| Area | Variants (grid values fixed in the pre-registration) |
|---|---|
| Entry | market at the decision close vs limit at the retrace (a new test; H4 only dropped STOP-type entries, and limit fill-on-touch is optimistic); one-bar delay; spread gate (skip if spread above x % of the stop); entry windows by hour |
| Stop loss | structure stop as live (compare on real signals, not on the null, since the mirrored null has no structure); ATR stop; buffer beyond structure; minimum stop-to-spread ratio; maximum stop cap; time stop |
| Target | 3R as live vs 2R vs 4R; partial at 1R plus runner; structure trail |
| Breakeven | none, at 1R as live, at 1.5R and 2R, with buffer, trail only after 2R |
| Exit timing | Friday flatten stays as the control |

Kept mechanic: CI of the paired delta excludes harm on DEV, same sign in at least 70 % of DEV years (about 8 years if pre-2021
passes), robust to stressed costs, and a stated mechanism. Output: sensitivity map, not a "best parameter".

## 7. AI methodology testing

Each arm gets the same candidate population, costs, splits and scorecard: out-of-fold AUC and Brier, expectancy vs take-all, vs
a selection-replicating permutation null, vs benchmark Z, trades kept, drawdown, year-by-year stability, stressed costs.
Labels are stateless (every candidate simulated in isolation, not only the trades the take-all run happened to take), the
embargo exceeds the longest hold, folds purge across all six pairs at once, and uncertainty is a day-cluster bootstrap.

| Arm | Method | Notes |
|---|---|---|
| A1 Control | the live rules, **stated as faithful (dead sweep) or fixed sweep** | two controls, never mixed |
| A2 Meta-labelling | strongly regularised logistic regression on at most 5 pre-specified features first; trees only if the logistic shows signal; ATR percentile, ADX and at most 3 pre-registered regime flags folded in as features | features rebuilt causally with their own no-look-ahead test; no imputed-spread feature (the imputation uses full-series medians); built after the mechanics freeze |
| A3 Heuristic baselines | spread-to-stop filter, hour filter, benchmark Z used as a filter | the bar any model must clear |
| A4 LLM veto replay | **Dropped by the user on 2026-10-29: the bot trades on rules alone and the work focuses on research, data collection and testing.** The LLM stays in shadow mode (off unless an API key is set) and is not evaluated. | |
| A5 | Dropped: the LLM event classifier (no historical event data in the sim, calendar sources failing live) | |
| A6 | Not planned: sequence or reinforcement models | about 3,000 correlated trades is far too few |

An arm earns a place in a candidate only if it beats take-all, the selection-replicating null and the heuristic baselines on
DEV, and does not reverse on a held-back DEV block.

## 7b. Data collection track (runs the whole campaign; added 2026-10-29)

The priority is evidence, so data comes first. Everything below is read-only history or logging, on A:.

| Item | What | When |
|---|---|---|
| D1 Keep the cache current | `python -m research.data_tools fetch --tail` (all timeframes, incremental, detects broker revisions); re-run the quality report after each pull | weekly, and before every FINAL run |
| D2 M1 history | FX M1 from 2024-02 (ask-gated fetch) for the same-bar audit, only if Part A of `reports/same_bar_audit_prereg.md` says it is needed | week 1 to 2, conditional |
| D3 Cross-vendor check | the importer (`research.data_import`, with a dry run that refuses bad files) can compare the broker's M5/H1 prices and spreads against a second vendor's file for a few sample years; needs you to supply the vendor file (for example a Dukascopy export) | optional, any time |
| D4 Spread and cost record | per pair, hour and year, from the cache: the real spread by hour, the share of zero recorded spreads, rollover-hour behaviour. This is the cost model every result depends on | week 2 to 3 |
| D5 Forward record | the live bot's `logs/candidates.jsonl` (rules only, LLM shadow off without a key): every proposed trade, block and order. It is data, not a test | from the demo restart |
| D6 More history | M5 is capped at about 1,000,000 bars (13.5 years); H1/H4 already go back to 2000 and D1 further. Nothing more is needed for the current questions | none |

## 8. FX research track (weeks 1 to 10)

All items are hypotheses to test, not claims. Anything from the literature is tagged verified, unverified or contradicted by
our data.

* R1 Microstructure from our own data: spread by hour, year and pair; volatility seasonality by session. Feeds the cost model and
  the entry-window variants.
* R3 Slow factors on D1/W1 with history from 2000: time-series momentum, carry proxies, value proxies across the six pairs.
  More years per trade, so more power per calendar year, but few trades and long drawdowns, so also judged against FTMO time
  and drawdown limits. This is the main fallback if stage 0 kills the intraday rules.
* R4 Short desk review (deep-research skill): what is known about retail intraday FX edge after costs, liquidity-sweep style
  signals, meta-labelling and LLM use in systematic trading; output a list of testable claims with sources.
* Cut from the first draft: event study (needs a working calendar source and historical event data), regime descriptive study.

## 9. Decision gate (weeks 15 to 16)

Thresholds are proposals to confirm in week 0, set before any FINAL run.

| Outcome | Condition |
|---|---|
| Continue on demo ("not rejected") | FINAL expectancy lower 95 % CI bound above zero under stressed costs (spread x 1.5, slippage, one-bar delay), confirmation alpha 0.025 |
| Go to a challenge attempt | the above, plus: at least 70 % of years positive, no single year above half of total R, still positive with the top 1 % of trades removed, results with and without XAUUSD, demo fills and slippage within the stressed-cost assumptions, a two-phase Monte Carlo on a halved edge (winner's curse) above a pass-rate threshold you set, live risk code checked against FTMO's current terms, a fee-at-risk ceiling |
| No-go | anything else; write-up in `RESULTS.md`; bot stays demo-only or off; research engine and harness remain |

Arithmetic to keep in mind: at fixed 0.25 % risk and about 50 trades a month, a true 0.10 R edge earns about +1.25 % a month, so
the 10 % target takes roughly 8 months. A challenge only works if risk is raised, and ruin probability rises with it.

FTMO modelling gaps to close before any challenge decision: the Monte Carlo is single-phase, uses UTC days (FTMO resets at
Prague midnight), bootstraps day blocks from a few hundred days, and the FTMO terms in it were not re-verified against the
current rules (`montecarlo.py:100-105`).

## 10. Main risks

Look-ahead leaks (extend `test_no_lookahead` to every new feature), pre-2021 cost realism, DST grid shifts, the unregistered
aggregate number contaminating hypotheses about the live rules, multiple-testing creep, LLM memory leakage, a simulator that
is too pessimistic or too generous (the audit), regime luck, a few events dominating the mean (USDJPY gaps, -19.7 R), correlated
USD pairs, an LLM model snapshot that is no longer served, the `pair_observer` confidence boost (`main.py:307`) that has no
research counterpart, news that fails closed or open in the live bot and changes the live trade rate, an underpowered demo.

## 11. Decisions

Recorded 2026-10-29:

1. Goal: do the whole plan; the default order stands (find a positive-expectancy demo configuration first, a challenge only after the section 9 gate).
2. M5 fetch to 2013: approved and done.
3. LLM: dropped. "Trade on rules alone, focus on researching, collecting data and testing" (2026-10-29). No API budget is needed; arms A4 and A5 are out of scope.

Still open:

4. Restart the bot on demo after the live fixes (proposed Nov 16 to 20).
5. Live-safety fixes: now proposed as before the restart, one to two weeks, each through the reviewer. Your go?
6. Gate thresholds in section 9 and the pass-rate target for the Monte Carlo.

## 12. Changes after the red-team review

1. FINAL is now an unburnt block (2021-06 to 2025-04), not 2025-01 to 2026-10 pooled with the demo; demo is never pooled.
2. Stage 0 kill rule added (skill estimate first).
3. Error budgets split by estimand; White/SPA for mechanics; Holm only for skill claims; alpha 0.025 for at most 2 confirmations.
4. Null-first reframed: nulls measure cost and convention; same-bar audit comes first; structure stops are tested on real signals.
5. Removed my wrong claim of a "LIMIT-to-STOP skipping artefact" (H4 was a clean negative).
6. A4 prompt rebuilt from relative fields; live-prompt mismatch and model-snapshot risk stated; pre/post-cutoff differential and random-veto control added; may be declared undetectable.
7. A2 rewritten (stateless labels, AUC and Brier first, regularised logistic first, permutation null, built after mechanics freeze).
8. Gate changed from "expectancy above 0" to a stressed-cost lower CI bound; demo is a divergence check; extra challenge conditions and the time-to-target arithmetic added.
9. Live fixes before the restart, LLM in shadow mode, checkpoint dates corrected, FTMO modelling gaps listed.
10. Dropped the event classifier, event study and regime descriptive study; folded the regime arm into A2; control A1 split into faithful and fixed sweep.

## 13. Re-plan after the stage-0 KILL and the first AI study (2026-10-29)

What happened: stage 0 (corrected reflected null, DEV) excluded an SMC-signal edge above +0.05 R at about 97.5 % (R2' skill +0.006 R, UB 0.044; R4' -0.004 R, UB 0.029).
The stop-floor study showed wider stops cut cost drag (null -0.136 R at 10 pips to -0.063 R at 30) but none makes the rules positive. The ML engineer's pre-registered
learnability test (`reports/ai_edge_approach.md`) found no stable directional structure net of cost at H1 (carried by 2019H2 alone).

Consequences for the sections above (they stay as the record of the first plan):

| Item | Status |
|---|---|
| Section 6 pipeline mechanics (entry, stop, target, breakeven) on the SMC signal | **Stopped** by the stage-0 rule. Stop floor was run as cost hygiene only: no live change recommended. |
| A2 meta-labelling on SMC candidates | **Stopped** (nothing to filter: skill +0.006 R). Replaced by A2' below. |
| A3 heuristic baselines | Kept only as the bar for A2'. |
| A4, A5 LLM arms | Dropped by the user. A6 sequence and RL models: not planned, confirmed by the ML report (about 10^4 effective samples). |
| Section 11 items 5, 6 (gate thresholds) | Wait until a candidate exists. |

New arms (each needs its own pre-registration; DEV only; FINAL once, alpha 0.025):

1. **A2' Slow-horizon learnability** (H4 and D1 decision bars, stops 48 to 126 pips, cost 0.021 and 0.0085 R). Same protocol as the H1 study: stateless triple-barrier net-R
   labels, purged walk-forward, directional vs non-directional features, week-block bootstrap, K = 4 variants (H4 and D1 x two stop multiples), Holm. Needs a quality screen of
   the 2000-2013 H1/H4 history first (Saturday bars, clock shifts, zero spreads). Falsifier: no directional AUC above 0.5 by about 0.02 in more than one era, then stop.
2. **A7 Volatility / time-of-day cost layer.** Forward range is forecastable (IC 0.4 to 0.5) and cost per R varies with the hour through the stop (EURUSD 0.035 to 0.056 R).
   Judge by cost per R of a random-direction run in the simulator with the `reflect` null, not by an alpha claim.
3. **R3 Slow factors** (section 8) remains the source of new signals; A2' is its ML counterpart.
4. **D7 Spread sentinel (data track).** From 2020-05 to 2021-09 (17 months; the last four are inside FINAL) the broker's recorded spread is exactly 50 points on 14 to 93 % of M5 bars (EURUSD 58 % of 2020, 65 % of 2021);
   the terminal returns the same values, so it is the broker's history and not our pipeline. It is not credible as a real 5-pip EURUSD spread (USDJPY median 1 point the same months)
   and is treated as a placeholder. The simulator uses the field as is, so the spread gate (EURUSD 25 points) blocks, and exits are charged for, those bars; the effect is the same for
   actual and null, but absolute levels in that year are suspect. A robustness re-run of stage 0 that stops at 2020-05-01 is recorded in `research/RESULTS.md`. Remaining step: treat 50 as missing
   in `prepare_bars` (impute like a zero) behind a flag, with a test, and re-quote cost levels. FINAL is partly affected (2021-06 to 2021-09: EURUSD 85 to 87 % of bars at 50 points; from 2021-10 about 0 %, checked in the data only, no P/L), so the sentinel
   treatment must be fixed before any FINAL run and applied identically to actual and null.
5. **D3** (second vendor for 2019H2) stays optional.

Timeline: week of Nov 2 sentinel handling and the 2000-2013 screen; Nov 9 to 20 A2' (prereg first); December A7 and, if A2' shows structure, the one FINAL confirmation; the decision
report (section 9) is now framed as "no positive-expectancy configuration found" unless A2' or R3 produces a candidate. Rules-only demo restart and `candidates.jsonl` remain a data track.
