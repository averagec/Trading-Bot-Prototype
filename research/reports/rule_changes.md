# Rule / risk-framework changes: pre-registered test (written 2026-10-26)

Pre-registration: `research/reports/rule_changes_prereg.md` (written before any variant ran; section 6 lists the deviations, all made before the holdout was opened).
Code: `research/sim.py` (new `SimConfig` options, all default off, base bit-identical), `research/experiments.py`, `research/decompose_rc.py`, `research/tests/test_rule_changes.py`.
Outputs: `research/output/rc/` (`dev_report.md`, `holdout_report.md`, `*_results.json`, `variant_log.csv`, `holdout_log.csv`, `decompose_h6.json`).

## Bottom line

* Nothing here creates edge. The signals are statistically indistinguishable from random direction under every rule set (actual minus random-direction null: base -0.028 R, z -0.74; best rule set +0.019 R, z +0.45).
* The framework changes do reduce structural drag and tail risk of a skill-less signal. The pre-registered composite H6 (Friday flatten 20:30 UTC + no entries from 20:00 UTC + no breakeven) lifts the zero-skill expectancy from -0.177 R to -0.075 R per trade (full sample), cuts equity drawdown -44% to -22%, and cuts the worst single trade -19.7 R to -1.2 R.
* The residual -0.075 R is almost exactly commission (0.043 R) plus my own pessimistic same-bar assumption (about 0.06 R). H6 removes everything else that the decomposition could find.
* Out of sample the gain shrinks and is not statistically confirmed: H6 holdout delta +0.046 R, CI [-0.096, +0.192], Bonferroni p = 1.0. The holdout has too few of the targeted events and the minimum detectable delta is 0.185 R.
* H2 (no late entries) is not supported out of sample. H4 (LIMIT-only) is rejected.

## 1. Pre-registered hypotheses and variant count

| ID | Change (exact value) | Class |
|---|---|---|
| H1 | Friday flatten: at first cycle >= Fri 20:30 UTC close all, cancel pendings, no entries | tail |
| H2 | No new orders / pending fills from 20:00 UTC, any day | drag |
| H3 | Breakeven removed | drag |
| H4 | LIMIT-only (skip LIMIT->STOP conversions) | drag |
| H5 | Fixed true risk 0.25 % (ATR/regime scaling off) | tail |
| H6 | Composite H1+H2+H3 (fixed in advance) | drag+tail |

Variant log (`variant_log.csv`): **20 variants including base** (19 alternatives), each simulated on the actual signals and on 40 random-direction seeds = 820 simulations.
12 pre-declared (base, H1-H6, overlay O1 = H6+H5, exploratory E1 BE 1.5R, E2 BE 2R, E3 H1 + no Friday entries from 15:00, E4 RR 2) and 8 post-hoc sensitivity variants S1-S8 (see section 6).
**17 comparisons against a base** form the multiple-testing family. In addition: 8 diagnostic configurations x 40 seeds for the residual-drag decomposition (no hypothesis test), and several throw-away DEV-only diagnostic scripts on existing runs.
The earlier agent's sensitivity run had already tried `no_breakeven` and `rr_2` on this same data, so the implicit family is at least 2 larger than the log.

Split (fixed in advance): DEV = trades opened before 2026-05-01 (592 base trades, 65 % of the span); HOLDOUT = from 2026-05-01 (233 trades), opened once, after the finalists were chosen mechanically (`holdout_log.csv` has one timestamp).
Finalists by the pre-registered screen: H1, H2, H3, H5, H6 (H4 failed). k = 5, holdout Bonferroni alpha = 0.01.

## 2. Results (R = trade R-multiple; delta = paired weekly-block bootstrap vs base; null = random-direction, 40 seeds)

### DEV (open_T < 2026-05-01)
| variant | n | expR [95% CI] | PF | maxDD | worst R | delta vs base [CI] | p raw | p Holm (17) | null expR | null delta (z) |
|---|---|---|---|---|---|---|---|---|---|---|
| base | 592 | -0.264 [-0.40, -0.14] | 0.56 | -41.1% | -19.7 | | | | -0.205 | |
| H1 flatten | 587 | -0.203 [-0.31, -0.10] | 0.63 | -31.0% | -2.5 | +0.061 [+0.007, +0.136] | 0.046 | 0.47 | -0.159 | +0.046 (12.7) |
| H2 no late | 536 | -0.216 [-0.36, -0.08] | 0.60 | -34.7% | -19.7 | +0.048 [+0.020, +0.078] | 0.001 | **0.018** | -0.180 | +0.025 (11.0) |
| H3 no BE | 521 | -0.214 [-0.38, -0.05] | 0.66 | -38.6% | -19.7 | +0.050 [-0.036, +0.139] | 0.136 | 0.90 | -0.177 | +0.028 (5.2) |
| H4 LIMIT-only | 417 | -0.323 [-0.49, -0.18] | 0.45 | -40.0% | -19.7 | -0.059 [-0.156, +0.034] | 0.886 | 1.0 | -0.215 | -0.010 (-1.7) |
| H5 fixed 0.25% | 592 | -0.264 | 0.60 | -34.3% | -19.7 | 0.000 | | | -0.205 | 0.000 |
| H6 composite | 485 | -0.076 [-0.22, +0.07] | 0.83 | -19.3% | -1.2 | +0.188 [+0.081, +0.303] | 0.0008 | **0.014** | -0.082 | +0.123 (17.8) |
| O1 H6 + fixed risk | 485 | -0.076 | 0.89 | -14.9% | -1.2 | +0.188 | | | -0.086 | +0.119 |

Exploratory (cannot be finalists): E1 BE 1.5R delta -0.002, E2 BE 2R +0.002, E3 H1+Fri-15:00 +0.064 (p 0.043), E4 RR 2 +0.025. None survives Holm. Moving the breakeven trigger from 1R to 1.5R/2R does nothing; only removing it helps in the null.
Reality check over the 17: best = H6, p = 0.024.

### HOLDOUT (open_T >= 2026-05-01), once
| variant | n | expR [95% CI] | PF | maxDD | worst R | delta [CI] | p raw | Bonf (k=5) | null delta (z) | verdict (pre-registered rule) |
|---|---|---|---|---|---|---|---|---|---|---|
| base | 233 | -0.054 [-0.25, +0.14] | 0.90 | -8.4% | -3.0 | | | | | |
| H1 | 234 | -0.068 | 0.85 | -9.8% | -1.1 | -0.014 [-0.079, +0.053] | 0.66 | 1.0 | +0.023 (7.6) | mixed: expectancy non-inferior, worst trade better, actual DD 1.4 pt worse |
| H2 | 219 | -0.066 | 0.86 | -9.3% | -3.0 | -0.012 [-0.063, +0.035] | 0.70 | 1.0 | +0.001 (0.4) | failed |
| H3 | 211 | +0.038 [-0.21, +0.28] | 1.07 | -6.9% | -3.0 | +0.092 [-0.024, +0.221] | 0.079 | 0.39 | +0.028 (3.2) | directionally consistent, not confirmed (holdout NOT blind for H3) |
| H5 | 233 | -0.054 | 0.91 | -5.4% | -3.0 | 0.000 | | | -0.002 | consistent (DD better) |
| H6 | 203 | -0.009 [-0.23, +0.22] | 0.95 | -7.9% | -1.1 | +0.046 [-0.096, +0.192] | 0.27 | 1.0 | +0.047 (6.1) | directionally consistent, not confirmed |

Minimum detectable delta on the holdout (80 % power, one-sided 5 %): H2 0.062, H1 0.084, H3 0.156, H6 0.185. Only a large effect could have been confirmed.
The base itself is much better in the holdout (-0.054 vs -0.264): 8 weekend-gap closes (-5.2 R) vs 18 (-30.1 R) in DEV, 14 late entries (+1.8 R) vs 58 (-42.8 R). Regime / event luck dominates.

### Walk-forward (fixed rules, per fold: delta actual (delta null)); F1-F3 DEV, F4-F5 HOLDOUT
| | F1 Jun-Sep25 | F2 Q4 25 | F3 Jan-Apr26 | F4 May-Jul26 | F5 Aug-Oct26 | folds better (actual / null) |
|---|---|---|---|---|---|---|
| base expR (n) | -0.282 (188) | -0.219 (171) | -0.284 (233) | -0.121 (118) | +0.014 (115) | |
| H1 | +0.027 (+0.020) | +0.123 (+0.088) | +0.045 (+0.034) | +0.020 (+0.026) | -0.049 (+0.019) | 4/5, 5/5 |
| H2 | +0.092 (+0.047) | +0.010 (+0.010) | +0.042 (+0.019) | +0.018 (+0.011) | -0.044 (-0.010) | 4/5, 4/5 |
| H3 | +0.072 (+0.077) | -0.002 (+0.036) | +0.071 (-0.016) | -0.007 (+0.031) | +0.222 (+0.027) | 3/5, 4/5 |
| H6 | +0.166 (+0.139) | +0.245 (+0.170) | +0.165 (+0.074) | +0.031 (+0.057) | +0.064 (+0.035) | 5/5, 5/5 |

## 3. Framework versus signal skill

| full sample | actual expR | null expR (sd) | actual - null | z |
|---|---|---|---|---|
| base | -0.205 | -0.177 (0.038) | -0.028 | -0.74 |
| H1 | -0.164 | -0.138 (0.034) | -0.027 | -0.77 |
| H6 | -0.056 [-0.18, +0.07] | -0.075 (0.042) | +0.019 | +0.45 |

How much a skill-less strategy loses less: **H6 +0.102 R/trade (null z 19.6)**, of which in DEV +0.123 and in HOLDOUT +0.047 (z 6.1). The shrink out of sample shows the gain depends on how many gap / late / breakeven events a window contains. H1 alone +0.039 R (full), H2 +0.019, H3 +0.029, all conditional on this price history (the null varies direction, not prices). H5 changes size, not R.
Residual drag after H6, random direction (`decompose_h6.json`): all frictions -0.075; minus commission -0.032 (commission = 0.043 R); minus spread -0.071 (spread contributes ~0, within noise); minus the pessimistic same-bar rule -0.007. I.e. what is left is commission and my conservative intrabar assumption.

## 4. Tail and FTMO (full-sample day-block bootstrap, 20 000 paths; rules are my assumptions)

| | p(pass 10%) | p(fail max-loss) | p(unresolved at 90d) | p(pass 5%) | zero-skill null: pass10 / fail |
|---|---|---|---|---|---|
| base | 0.2% | 51.9% | 47.9% | 4.7% | 1.4% / 44% |
| H6 | 2.7% | 25.3% | 72.0% | 17.8% | 4.4% / 28% |
| O1 (H6 + fixed 0.25%) | 1.7% | 9.7% | 88.5% | 16.7% | 2.7% / 15% |

Equity max drawdown full sample: base -44.2%, H6 -21.9%, O1 -14.9%. Worst trade -19.7 R -> -1.2 R. Weekend-gap closes: base 26 trades -35.3 R; H1/H6 none (the flatten exits themselves sum +11.4 R / +20.8 R, survivors, not a cost). The reduction in ruin risk is real; the chance of reaching +10% is still about 3%, because expectancy is still not positive.
H1 alone does not help FTMO max-loss failure (54.6% vs 51.9%): the day-block bootstrap failure is driven by the steady negative drift, not by gaps.

## 5. What survives multiple-testing correction

* DEV, Holm over 17: **H6 (p 0.014) and H2 (p 0.018)**; the S-variants of H6/H2 also. Reality check p = 0.024 (H6). H1 (0.47) and H3 (0.90) do not survive on actual signals.
* In the zero-skill null (high power) H1, H2, H3, H6 and the BE exploratories all pass Holm easily (z 4-18), i.e. the mechanisms exist in this history. H4 and H5 do not.
* HOLDOUT, Bonferroni k = 5: nothing is confirmed. H2 reverses sign and its null effect vanishes (z 0.4). H6 and H3 are directionally consistent only.
* Caveat on the DEV survivors: H1/H2/H3 were suggested by full-sample diagnostics that include DEV, so these DEV p-values are not clean out-of-sample evidence; the family I could have picked from is larger than 17. The late-entry penalty is not explained by entry spread (0.040 R vs 0.033 R) and does not replicate in the holdout (null late mean -0.12 vs -0.10).

## 6. Deviations, simulator findings (for the owners of sim/data)

1. The first DEV run showed H1 with a +0.046 R null gain, contradicting my prior (gap mean zero for random direction). DEV-only decomposition found: all of it comes from 38 weekend-spanning trades per seed (base -0.30 R vs +0.32 R if flattened); BUY holding gain +0.9 R in total vs SELL -15.3 R. Two simulator conventions contribute: (a) `_bar_exit` fills a TP at the TP price even if the bar opens far beyond it, while SL gaps through to the open (asymmetric against the holder); (b) the reopen bar carries its opening-tick spread (median 4.8 pips EURUSD, 10 pips USDJPY vs 0.2 normal) and the sim adds it to the bar's high for shorts. Also 295 of 427 weekend reopens print the bid below Friday's last close (a bid-dip pattern, not a symmetric gap).
2. I added `tp_gap_fill` and `reopen_spread_zero` (default off) and ran S1-S8. Result: H1's null gain falls +0.046 -> +0.033 -> +0.032, H2's +0.025 -> +0.013, H6's +0.123 -> +0.110 -> +0.109; every conclusion is unchanged (H6 still z > 13 in DEV, +0.045 z 5.7 in holdout). The tail benefit (worst trade, DD) is unaffected. Neither option is "correct" (the second over-corrects for shorts); they bracket the weekend treatment. Not changed: signals.py, data.py, run.py, nulltest.py.
3. Family size grew from 11 to 17 comparisons because of S1-S8; correction uses 17. H5's DD credit is per unit of average risk (0.25% vs the base mean 0.272%).
4. A tool problem (a syntax error in my own script) and a re-run after changing a dataclass field re-simulated everything; results were bit-identical.

## 7. Verdict

Trustworthy enough to adopt as risk/cost hygiene (not as alpha): **fixed true risk (H5)** (mechanical, no expectancy effect); **Friday flatten (H1)** for tail control: the mechanism holds in both windows in the null (z 13.8 full, 7.6 holdout) and removes the -19.7/-8.3 R gap losses, but its actual-signal expectancy gain is not confirmed and it did not reduce holdout drawdown; **breakeven removal (H3)** is a modest cost saving (null +0.028 R, consistent in both windows; z 3.2 in the holdout) with weak actual-signal evidence and a holdout that was not blind.
Not adopt: H2 (does not replicate), H4 (hurts; STOP entries are not the problem), BE at 1.5R/2R, RR 2.
The composite H6 is the best pre-registered configuration but only its H1 and H3 parts have out-of-sample support; I did not test H1+H3 without H2 (that would be a second look at the holdout).

## 8. Unproven / limits

* About 16 months, one regime, 233 holdout trades with MDE 0.06-0.19 R: nothing here can establish a +0.05 R effect.
* The null is conditional on this price history; the weekend and late-entry effects rest on a handful of large events (USDJPY +190 pips, AUDUSD -84 pips, ...). A different history could flip the sign of the realised gap benefit while leaving the variance reduction intact.
* Fills are optimistic (no slippage/requotes, no swaps, no news), same-bar rule pessimistic (worth ~0.06 R), reopen-bar spread/bid semantics unverified against the broker, FTMO rules assumed.
* The strategy is still at best break-even after the changes: H6 actual -0.056 R, CI includes 0; no evidence of directional skill. A real edge, if any, has to come from the signal; more history (MT5 "Max bars in chart") is the first thing needed.
