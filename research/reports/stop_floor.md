# Minimum-stop-floor experiment: result (run 2026-10-29, DEV 2014-01-01 to 2021-05-31)

Pre-registration: `research/reports/stop_floor_prereg.md` (revision 1 before the run: null geometry; two logged analysis fixes after it).
Code `research/stoploss_experiment.py`, tests `research/tests/test_stoploss_experiment.py` (27 pass), raw output
`research/output/stoploss/sf_dev1/` (`summary.json`, `rows.csv`, `weekly.parquet`, ...), tables `research/output/stoploss/sf_dev1_tables.txt`.
FINAL (2021-06 to 2025-04) was not read, run or looked at (the worker asserts it; `--end` later than 2021-06-01 is refused). No live file, `sim.py`,
`signals.py` or `nulltest.py` was edited.

## Verdict

**A wider floor lowers the framework's drag on the random-direction null, roughly in proportion to the floor, and the real rules move the same
way, but on the real signals the improvement is not separable from noise at the pre-registered bar, and no floor makes the expectancy positive.**
Per the decision rule: F20 and F30 are COST-ONLY (null drag REDUCES; real signals NEUTRAL), F15 and S20 are NEUTRAL, none HELPS, none HURTS, none
is nominated for a FINAL confirmation. Even F30 leaves the real rules at -0.077 R per trade (-2.7 R per month), and costs a fifth of the trades and
3.6 times the time in trade. The floor does not change the signal: the candidate set is identical at every floor (verified in code and by a test);
it changes the stop distance of the pinned trades, the R-distribution, the cost per R and which candidates survive the spread filter and the portfolio gates.

Sanity checks, stated up front:

* **Reproduction passes.** Control (C10) with the original `mirror` null (30 seeds): -0.1495 R against stage 0's -0.150; real run 3,970 trades, -0.1308 R
  against -0.131.
* **The frictionless check does NOT give 0 R, at any floor** (see the next section), so the null level is not zero-skill truth. Only differences
  between floors are read as cost effects, and the real results are shown next to the null results.

## Results (DEV, live settings, 6 pairs; null = `reflect` geometry, 60 seeds; real = one run)

| | C10 (live) | F15 | F20 | F30 | S20 |
|---|---|---|---|---|---|
| floor | 10 pips | 15 | 20 | 30 | max(10, 20 x spread) |
| **Null (reflect)** trades per run | 4,130 | 4,070 | 3,879 | 3,285 | 4,623 |
| null mean R (sd across seeds) | -0.1364 (0.021) | -0.1192 (0.022) | -0.1029 (0.019) | -0.0634 (0.019) | -0.1181 (0.018) |
| **D_null vs C10** (paired week-block SE) | | +0.017 (0.010) | **+0.034 (0.013)** | **+0.073 (0.014)** | +0.018 (0.009) |
| null mean R, `mirror` geometry (30 seeds) | -0.1495 | -0.1142 | -0.0868 | -0.0696 | -0.1263 |
| D_null, mirror | | +0.035 (0.010) | +0.063 (0.012) | +0.080 (0.014) | +0.023 (0.009) |
| win rate, null | 40.4 % | 41.5 | 42.0 | 43.9 | 39.4 |
| cost per R: commission + entry spread / stop | 0.044 + 0.047 | 0.033 + 0.040 | 0.026 + 0.034 | 0.018 + 0.025 | 0.032 + 0.037 |
| trades pinned at the floor / mean stop (pips) | 50 % / 38 | 71 % / 42 | 81 % / 46 | 87 % / 58 | 72 % / 99 |
| avg hold (min), null | 215 | 306 | 439 | 770 | 375 |
| frictionless L1 (no costs; BE and flatten kept), 20 seeds | -0.054 (0.004) | -0.049 (0.004) | -0.058 (0.004) | -0.044 (0.003) | -0.028 (0.004) |
| frictionless L2 (no costs, no BE, no flatten), win rate | -0.073 (0.004), 23.5 % | -0.065, 23.7 % | -0.068, 23.5 % | -0.062, 23.6 % | -0.014, 24.8 % |
| **Real signals** trades (per month) | 3,970 (44.6) | 3,851 (43.3) | 3,683 (41.4) | 3,089 (34.7) | 4,372 (49.1) |
| real mean R | -0.1308 | -0.1021 | -0.0939 | -0.0773 | -0.1311 |
| **D_act vs C10** (SE) | | +0.029 (0.014) | +0.037 (0.020) | +0.054 (0.024) | -0.000 (0.015) |
| paired skill vs reflect null (SE) | +0.006 (0.019) | +0.017 (0.020) | +0.009 (0.020) | -0.014 (0.022) | -0.013 (0.018) |
| paired skill vs mirror null (SE) | +0.019 (0.020) | +0.012 (0.021) | -0.007 (0.021) | -0.008 (0.024) | -0.005 (0.020) |
| D_skill = D_act - D_null (SE) | | +0.012 (0.012) | +0.003 (0.015) | -0.020 (0.020) | -0.019 (0.012) |
| real win rate / profit factor | 41.0 % / 0.79 | 42.2 / 0.83 | 42.4 / 0.83 | 43.6 / 0.85 | 39.2 / 0.78 |
| real R per month | -5.83 | -4.42 | -3.89 | -2.68 | -6.44 |
| real return / max equity drawdown | -73 % / -75 % | -63 / -65 | -59 / -60 | -46 / -46 | -76 / -78 |
| real worst trade (R) | -12.7 | -11.2 | -8.4 | -5.1 | -7.6 |
| real worst intraday low in a Prague day; days <= -3 % | -4.58 %; 1 | -4.47; 1 | -3.23; 1 | -1.89; 0 | -3.76; 1 |
| **Verdict** (cost effect / real effect) | control | NONE / NEUTRAL | REDUCES / NEUTRAL | REDUCES / NEUTRAL (noise) | NONE / NEUTRAL |
| | | NEUTRAL | **COST-ONLY** | **COST-ONLY** | NEUTRAL |

Decision-rule inputs: delta = 0.02 R, z = 2.24 (Bonferroni over 4). F30: D_act 0.054 - 2.24 x 0.024 = -0.001, one hair short; its SE exceeds delta, so the
rule labels it "not separable from noise" and the minimum detectable change is 2.8 x SE = 0.07 R. F15 and F20 real lower bounds are -0.003 and -0.007.
Trades per month versus control: 0.97, 0.93, 0.78, 1.10. D_act has the same sign in 6, 6, 7 and 5 of 8 DEV years. Without XAUUSD the numbers
move by at most 0.01 R (D_null +0.019, +0.037, +0.082, +0.020; D_act +0.031, +0.042, +0.061, +0.016) and no sign changes.

## Does the floor lower the drag, and by what mechanism?

* **Yes on the null, monotone in the floor:** -0.136, -0.119, -0.103, -0.063 (reflect). The mirror geometry agrees in direction but exaggerates F15 and F20
  (+0.035, +0.063 against +0.017, +0.034): in the mirror null the STOP-entry orders carry a fill-bar artefact (STOP -0.183 against LIMIT -0.117 at C10,
  fill-bar stop-outs 5.9 % against 3.3 %) that wider stops mostly remove (STOP -0.113 at F15), a gain that does not exist for trades the strategy really
  takes. In the reflect null LIMIT and STOP entries are alike (-0.130, -0.144; fill-bar stop-outs 3.7 % and 4.0 %), which is the point of that geometry.
  At F30 both geometries agree (+0.073 and +0.080).
* **Cost per R (F30 against C10, reflect null):** commission falls 0.0437 to 0.0182 (+0.0255 R, exact), the entry-spread-over-stop proxy 0.0471 to 0.0253
  (+0.022). That is about 0.047 of the 0.073; the spread filter also blocks fewer candidates (relative-spread blocks 4,473 to 2,059 per run), and the
  remaining 0.02 to 0.025 is convention: fill-bar stop-outs fall from 3.8 % to 1.5 % of trades. The frictionless runs confirm that this last part is small:
  L1 moves only +0.011 (SE about 0.005) from C10 to F30, L2 +0.010.
* **Where it shows up (null, exact identity):** of the +0.073, +0.065 is exit mix and +0.008 is within-reason payoff. SL exits fall from 56 % to 50 % of
  trades, Friday-flatten exits rise from 5 % to 15 % (mean R +0.42 to +0.46), TP exits fall from 13.9 % to 12.3 % (so the stop widening costs targets: 90 pips
  at F30), and breakeven exits pay less in R (+0.105 to +0.039) because the 2-pip breakeven buffer is absolute. The win rate rises 40.4 % to 43.9 %.
* **Same orders versus a different set of trades (F30, null, per run):** 2,735 orders exist at both floors and improve by +0.065 R each (-0.132 to -0.066);
  1,395 control orders disappear (mean -0.146 R) and 549 new ones appear (mean -0.049). Reasons: more candidates pass the spread filter, but open positions last
  3.6 times longer, so max-open blocks rise from 382 to 2,763 candidates and correlation blocks from 410 to 986 per run. Real signals look the same (2,646
  common orders +0.047 R each; 1,324 dropped at -0.148, 443 added at -0.086).
* **The structural residual does not depend on the scalar floor.** L2 is -0.073, -0.065, -0.068, -0.062 for C10, F15, F20, F30 (the differences are 1 to 2
  SE of 0.005); win rate 23.5 % rather than 25 %. So whatever biases the frictionless null (the lead's audit: gap fills pay worse than the stop level while
  targets never pay better) does not scale with the stop width, and the null's differences between floors are mostly cost, not a changing bias. S20 is the
  exception (L2 -0.014) because it also widens gold's stops, where fill-bar stop-outs are 11 %.

## Does it help or hurt the real signals?

* Real expectancy: -0.131, -0.102, -0.094, -0.077, -0.131 (S20). The improvements D_act (+0.029, +0.037, +0.054) are about as large as the null's (+0.017,
  +0.034, +0.073); D_skill is +0.012, +0.003, -0.020 (SE 0.012 to 0.020): no evidence that the wider floor helps or hurts the signal relative to the null, and the
  paired skill stays at zero within noise at every floor (-0.014 to +0.017 against reflect, SE about 0.02).
* Each D_act is about 2 SE from zero before the multiplicity correction and fails the pre-registered bound. It is consistent with a real improvement of 0.03 to
  0.05 R and with none. Direction by pair: EURUSD, GBPUSD positive in both null and real; USDCAD positive at F20/F30; AUDUSD and USDJPY mixed. These per-pair
  SEs are 0.03 to 0.06 and descriptive only.
* **XAUUSD should be treated separately.** Its structure stops are about 1.2 to 2.9 dollars, so no scalar floor up to 30 pips (0.30 dollars) binds: D_null for
  gold is -0.002, -0.000, -0.010 (SE 0.004 to 0.010) and D_act about 0 (SE 0.01 to 0.02). Gold is the worst real pair (-0.21 R, 303 trades) with 11 % fill-bar stop-outs,
  the pip convention (pip = point = 0.01) makes the live "10 pips" mean 0.10 dollars, and the live stop buffers treat it like a 5-digit pair. Any gold floor
  would have to be set in dollars or ATR terms and tested on its own; S20 shows it can be done (gold trades 303 to 751 per real run) but gives -0.027 +/- 0.07.
* **Price of the benefit:** trades per month 44.6 to 34.7 at F30 (ratio 0.78, below the 0.8 guard), average hold 3.5 hours to 12.5 hours, more weekend gap
  exposure (Friday-flatten exits 5 % to 15 %), a TP at 90 pips that is rarely reached. On the other side, the worst trade is -12.7 R at C10 and -5.1 R at F30 (gap
  stops are smaller in R), the worst Prague-day intraday low improves from -4.58 % (close to a 5 % daily limit) to -1.89 %, and the maximum drawdown from
  -75 % to -46 % (one path each; descriptive).
* **FTMO objective:** all floors lose money on DEV (-5.8 to -2.7 R per month, about -1.5 % to -0.7 % of the account per month at 0.25 % risk). A wider
  floor changes the size of the loss and the drawdown profile, not its sign. No floor tested turns a pass probability from "essentially zero" into a meaningful one.
* S20 (a pair- and era-aware floor) lifts the trade count by 10 % and the null by +0.018 but leaves the real signals unchanged (-0.0003 +/- 0.015); the null
  gain is spread, not signal, and the real rules do not share it. Not worth a second variant.

## Limitations

* One development window of 89 months and one deterministic real-signal path per floor; SEs come from a week-block bootstrap of that single path and do
  not include path-dependence of the portfolio (balance compounding, max-open, correlation gates). Year signs (6, 6, 7, 5 of 8) are descriptive, and 2021 is five months.
* The null is not unbiased (frictionless residual -0.05 to -0.07 R, win rate 23.5 %); I interpret only differences between floors. The paired skill depends on
  the null geometry by about 0.013 R (C10: +0.006 reflect, +0.019 mirror), inside its SE of 0.02.
* The spread cost uses recorded spreads with the broker's zero spreads imputed (stage-0 caveat); "cost per R" is exact for commission and a proxy (entry spread
  only) for spread. Commission is the fixed 2.50 per lot per side. Slippage is zero.
* Wider floors lengthen trades 1.4 to 3.6 times: overnight and weekend gap exposure and swap are not modelled, only the Friday flatten.
* Two of the four treatments pass the cost-effect threshold; the pre-registered real-signal test has little power (minimum detectable change about 0.04 to 0.07 R),
  so "NEUTRAL" here means "not separable", not "no effect".

## What I did not test

Floors other than 15, 20, 30 pips and the one spread-multiple variant (no ATR floor, no pair-specific tuned floor, no dollar floor for gold); a stop-width cap;
the structure-stop buffer or swing lookback; the 3R ratio at wide stops (a 30-pip stop may want a smaller R multiple); the absolute 2-pip breakeven buffer
(scaling it with the stop); time stops; entry types; any FINAL or burnt-window data; swaps; live fills and slippage; FTMO's exact daily-loss rule.

## What a live change would need (not recommended here)

The only live lever is `MIN_SL_PIPS` in `utils/risk_manager.py:33` (read in `calculate_position`, line 354); `SimConfig.min_sl_pips` takes its default from it. Any change
would need: a pre-registration naming the floor (F20 or F30 are the only candidates this evidence even nominates for follow-up, and neither meets the rule), a
reviewer pass on the live change and a test that fails without it, and one locked confirmation on FINAL (alpha 0.025, at most two candidates, stressed costs,
trades per month at least 80 % of control). Gold needs its own treatment. Based on DEV alone the right statement is: the floor is a cost lever worth about +0.03 to +0.07 R
per trade on the null and probably similar on the real rules, that is, a smaller loss, not a profit.

## For the lead to merge elsewhere

* `RESULTS.md`/`progress.md`: the paragraph above plus the table; the frictionless residual is floor-independent for scalar floors (about -0.065 R, L2).
* `GAMEPLAN.md` section 6: stop-floor family done (4 treatments, none HELPS); `progress.md` open item: three accidental no-op invocations of an inline
  interpreter (`python -` with empty input) occurred while editing files; no data was read or written by them. The `.claude/settings.local.json` allow entries for
  `python -` / `python -c` are therefore still active.
* The `reflect` geometry makes the mirror-null improvement from wider floors look larger than it is for F15 and F20; any earlier "cost hygiene on the null" number
  based on `mirror` should be read with that in mind.
