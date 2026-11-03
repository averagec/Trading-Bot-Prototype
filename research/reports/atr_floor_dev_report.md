# ATR floor (k = 1.0): DEV re-measurement, stage 1 (2026-10-31)

Pre-registration: `atr_floor_prereg.md` version 2, sha256 `af3a2f966e5ff68f8665d7fe17ca9371f45e468b0373652f08eb6112d0510b25` (commit 23fdc4b, before this run). Run: `python -m research.atr_floor_confirm dev --tag atr_floor_dev --workers 10`
(one run, 303 s), outputs `research/output/atr_floor_dev/`. DEV only (2014-01-01 to 2021-06-01); FINAL and 2025-05 onward were not loaded; no `--force`.
This is a **re-measurement, not a test**: same candidates, simulator and ATR code as the quant's `quant_s1`; it adds 40 seeds, cluster inference and diagnostics. Nothing here is evidence for the hypothesis that DEV did not already contain.

## 1. Acceptance check (prereg section 8): passed

Per ISO week and symbol, trade counts and sum of R of the control and of k = 1.0, for the real run and null seeds 0..19, equal the stored `quant_s1` tables to 1e-9: 21 runs each, 0 mismatches.
Hand check against raw bars (`atr_floor_handcheck.py`, `handcheck.txt`; 30 random treated real trades with numpy seed 20261031 plus 10 XAUUSD trades): ATR recomputed by a plain loop from raw H1 bars, the stop is at least k x ATR in 40 / 40 and equals max(matched control stop, k x ATR) in 40 / 40 (23 have a matched control order; the other 17, 12 of them gold, are trades the control did not take, for which the check is stop >= k x ATR); lot size within one step of the 0.25 % rule in 40 / 40 (40 exact); target = 3 x stop in 40 / 40; an independent bar-by-bar replay (stop, target, breakeven, Friday flatten, pessimistic fill-bar rule) reproduces exit reason, exit bar and exit price in 40 / 40 (exit reasons in the sample: SL 27, TP 6, BE 6, FLAT 1). Shared code could still carry a shared bug in the simulator itself; the replay re-implements its documented rules, not its code.
Tests: `research/tests/test_atr_floor_confirm.py` 22 passed; full suite 671 passed (214 s), including `test_fidelity.py::test_no_lookahead`.

## 2. Primary results (real signals, all six pairs, sentinel-missing, 4,203 control vs 4,294 treated trades)

| estimand | estimate | 95 % interval (day / week / 4-week block) |
|---|---|---|
| E1 mean R per trade, treated minus control | **+0.0100** (control -0.1125, treated -0.1025) | [-0.025, +0.044] / [-0.022, +0.043] / [-0.024, +0.044]; binding lower bound -0.0253 |
| E2 R per calendar month (89.05 months) | +0.37 R (-5.31 -> -4.94 R / month), SE 0.81 | week [-1.19, +1.95]; at equal trade count the gain is +0.47 R / month |
| E3 max equity drawdown, real | -71.4 % -> -67.6 % (null mean -74.3 % -> -63.5 %) | point estimate only |
| N1 E1 on the reflect null (40 seeds) | **+0.0334** (control -0.1180, treated -0.0846) | [+0.012, +0.056] on all three cluster types; binding lower bound +0.0115 |

By ISO year (2014..2021) the real delta is -0.028, +0.092, +0.021, +0.052, +0.024, -0.060, -0.025, +0.030 (5 of 8 positive); the null delta is -0.019, +0.058, +0.022, +0.053, +0.050, +0.038, +0.040, +0.037 (7 of 8).
Cluster choice barely matters (SE 0.0168 to 0.0175); the lowest of the three bounds is the day cluster.

## 3. Secondary results

| cut | real E1 | null N1 |
|---|---|---|
| without XAUUSD | +0.0215 [-0.011, +0.055] | +0.0384 [+0.016, +0.060] |
| XAUUSD only (280 -> 576 trades) | -0.019 (SE 0.083) | +0.038 (SE 0.053) |
| without GBPUSD 2019 | +0.0099 | +0.0329 |
| without the flagged years 2016 and 2017 / 2019 / all three | +0.0005 / +0.0200 / +0.0142 | +0.0325 / +0.0339 / +0.0335 |
| before / after 2020-05 (placeholder-spread era, imputed) | +0.0147 / -0.0136 (SE about 0.018 / 0.045) | +0.0344 / +0.0302 |
| composition-standardised (control pair mix) | +0.0210 [-0.011, +0.053] | |
| adverse swap stress ($7 / $20 per lot per night; nights per trade 0.31 -> 0.46) | +0.0092 | +0.0321 |
| **intrabar neutral** (nothing exits on the fill bar) | **+0.0009** [-0.031, +0.033] | +0.0254 [+0.003, +0.049] (10 seeds) |
| stressed cost (spread x 1.5, commission $3.125), 10 seeds | +0.0265 [-0.016, +0.071] | +0.0314 [+0.003, +0.060] |

By pair (real, treated minus control, control weight): EURUSD +0.050 (20 %), GBPUSD +0.055 (17 %), USDCAD +0.063 (20 %), AUDUSD -0.034 (16 %), USDJPY -0.023 (20 %), XAUUSD -0.019 (7 %). Three pairs positive, three negative, each with an SE of about 0.04 to 0.08: no pair pattern is separable from noise.
By weekday of the decision (real): Mon +0.008, Tue +0.013, Wed +0.043, Thu -0.001, Fri -0.040 (null: +0.034, +0.047, +0.019, +0.049, +0.006). By ATR relative to its 100-bar median, top quintile: control -0.069, treated +0.018; other quintiles within noise of each other (descriptive).
Matched / unmatched (real): 3,457 orders in both arms, mean R change +0.018 on those (null +0.036); 837 orders only in the treated arm average -0.143 R, 746 only in the control arm -0.121 R. So the aggregate gain is a geometry effect on matched orders of about +0.018 R, partly given back by the changed trade set.

Mechanism (real run, control -> treated): share of candidates widened by the floor 0 -> 84.7 %; average hold 3.5 h -> 5.6 h; commission per R 0.0436 -> 0.0302; fill-bar stop-outs 3.4 % -> 1.6 %; Friday-flatten exits 5.0 % -> 6.9 %; worst trade -12.7 R -> -9.0 R; exit mix SL 55.6 -> 54.2 %, BE 25.2 -> 24.9 %, TP 14.4 -> 13.9 %.
Funnel (real): spread-relative blocks 4,560 -> 1,772; open-position guard 7,031 -> 8,878; max-open blocks 235 -> 663; correlation blocks 848 -> 1,371; cooldown 59 -> 20. FX trades fall 3,923 -> 3,718 (-5.2 %), XAUUSD rises 280 -> 576, the pooled count therefore rises 2.2 %.
Imputed spread share in DEV (M5 bars): zero-spread 1.5 to 3.2 % of bars by pair (month averages); 50-point placeholder flagged on average 12.6 % of EURUSD bars per month overall, 58 % per month in 2020 and 86 % in 2021 (month averages, EURUSD); none before 2020.

## 4. What this says, plainly

* The pre-registered mechanism reproduces on the null: +0.033 R per trade with a lower bound above zero under every cluster definition and in 7 of 8 years, with commission per R falling by a third and fill-bar stop-outs by half.
* On the real signals the gain is **+0.010 R, indistinguishable from zero** (lower bound -0.025), positive in 5 of 8 years, and it **falls to +0.001 R under the neutral fill-bar convention**: the real-signal gain is about the size of the convention difference. Without gold it is +0.02 R.
* The floor works partly by getting around the spread cap: at spread x 1.5 the control loses a third of its trades to the cap while the treated arm gains 33 %. How much it helps depends on how often the cap binds, which is a property of the broker's spreads in the evaluation window.
* The expectancy stays negative (-0.1025 R per trade, about -4.9 R per month; drawdown -68 %). This is a cost-saving structural change, not evidence of skill.
* Tier the pre-registered table would give on DEV (illustration only, DEV is not a test): D, because the real point estimate is below +0.02. All guards (no-gold, standardised, swap, stressed cost, neutral, drawdown, trade count, E2, N1 sign) are satisfied; the neutral one narrowly.

## 5. Power for FINAL, recomputed from these standard errors (prereg section 6)

Largest of the three cluster SEs, scaled by sqrt(89.05 / 47) = 1.38: real E1 SE 0.0241, null N1 SE 0.0156 (prereg guess 0.023 / 0.016). Minimum detectable (80 % power, one-sided 0.025): real **0.067 R**, null **0.044 R**.
Power of the real-signal test (S1) at a true gain of 0.010 / 0.0215 / 0.035 / 0.045 / 0.066: 6 % / 14 % / 31 % / 46 % / 78 %. Power of the null lower-bound test at 0.015 / 0.02 / 0.03 / 0.0346 (DEV estimate) / 0.044: 16 % / 25 % / 49 % / 60 % / 81 %.
These are upper bounds (the lowest-of-three bound lowers them), and the spread part of the null gain may shrink in recent years. A true real-signal gain below about 0.07 R is not detectable on FINAL; that is not the same as rejected.
Prereg condition (i) for recommending `FINAL_GO` (real SE at most 0.030) is met (0.0241); condition (ii) is the owner's judgement: at the DEV estimate the chance of tier A is about 6 to 14 %.

## 6. What the owner must decide before any FINAL run

1. Whether to spend one of the at most two FINAL candidates on a test whose chance of a decisive real-signal answer is about 6 to 14 %, and whose cost-mechanism part (N1) has about 60 % power at the DEV size. The cheaper alternative in the prereg (N1 alone on the descriptive-only window 2025-05 onward, which spends no unburnt data) needs its own short pre-registration.
2. Whether the pessimistic fill-bar convention is acceptable as the primary: the real gain nearly disappears under the neutral one (+0.001 R); S5 only requires the neutral estimate to be positive.
3. Gold: keep in the primary (as frozen), or accept the XAUUSD trade-count doubling and its weak data as a known confound (reported separately; the no-gold and standardised estimates are +0.02).
4. Store the hash of the final prereg outside the repository and create `research/output/atr_floor/FINAL_GO` containing it (the `final` command refuses to start otherwise; it also needs a reviewed look at the FINAL candidate construction in prereg section 9, which has not been executed or tested beyond the lock).
5. A live change is only supported after a tier A result, and then goes through the `reviewer` agent.

## 7. Caveats and open items

* No post-DEV revision of the prereg was needed (revision count 1 of 3). The additional DEV cuts in section 3 (flagged years, before / after 2020-05, weekday, pair) are descriptive and were run once; they changed nothing.
* The flagged-years and era cuts were produced with a short ad hoc analysis on the stored trades (same bootstrap code), not by the module.
* The hand check validates the floor, sizing and trade replay on a sample of 40, not every trade, and shares the bars with the simulator.
* FINAL-mode code (`final` command, candidate construction) is covered only by lock tests; its pipeline is the DEV code path and has not been run on FINAL data.
