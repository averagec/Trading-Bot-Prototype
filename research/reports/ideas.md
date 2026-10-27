# Three alternative strategy ideas + a trivial benchmark: results

Run 2026-10-26. Rules, parameters, split, tests and decision criteria were frozen in `research/reports/ideas_prereg.md`
before any backtest. Code: `research/ideas/` (generators `idea_a/b/c/z.py`, `simx.py`, `evaluate.py`), tests
`research/tests/test_ideas_*.py`. Outputs: `research/output/ideas_dev|ideas_holdout|ideas_robust/`,
`ideas_report.json`, `ideas_variants_log.csv`. Execution model: `research.sim.Simulator` (+ a time stop), MARKET entries
at the close of the decision M5 bar, 0.5 % risk, no break-even, bid/ask spreads, $2.50/lot/side commission,
pessimistic intrabar rule, flat by Friday 20:00 UTC, 1 position per symbol, live-bot gates (correlation, daily DD,
monthly cap) off.

## Verdict

**No evidence of directional edge in any of the four arms.** Nothing survives the pre-registered rule (holdout net
expectancy > 0 and Holm-adjusted p < 0.05 vs random direction, dev > 0, plateau, beats benchmark). The only arm that
"cleared dev" (C) failed to replicate in the locked holdout. One arm (B) shows *negative* directional skill that is
consistent across both windows; that is an unplanned observation, not a tradable result.

## Pre-registered ideas

* **A, London sweep-and-reverse** (EURUSD, GBPUSD; Europe/London clock): fade a sweep of the Asian-range extreme that
  closes back inside with a 6-bar structure break; stop beyond sweep + 2 pips; 2R; out 16:00 London. Reason: stop/
  breakout liquidity sits beyond the Asian range and failed breaks revert.
* **B, H4 trend pullback** (6 pairs): H4 EMA50/200 + ADX14 > 20, H1 pullback into [EMA20 - 1 ATR, EMA20], M15
  resumption bar; stop 1.5 H1-ATR; 2R; 48 h. Reason: multi-day trend persistence; better entry price.
* **C, prior-day-range breakout** (6 pairs): first H1 close beyond yesterday's range in the H4-trend direction, only
  after an at-least-average range day (volatility clustering); stop 0.25 ADR10; 2R; out 21:00 UTC.
* **Z, trivial benchmark**: every weekday 07:00 UTC trade the H4 trend sign, same exits as B.
* Split: DEV < 2026-05-04 (64.5 %), HOLDOUT >= 2026-05-04 (35.5 %, evaluated once per arm; ledger in
  `output/ideas_holdout/ledger.json`).

## Results (net R per trade; null = random direction, same trades/stops/costs, 200 seeds)

| Arm | Window | Trades | Net expR [95 % CI] | Gross expR | PF | Win % | Max DD | Null mean ± sd | z | p (1-sided) |
|---|---|---|---|---|---|---|---|---|---|---|
| A | dev | 260 | +0.048 [-0.12, +0.21] | +0.083 | 1.07 | 38 | -9 % | -0.027 ± 0.076 | +0.99 | 0.169 |
| A | **holdout** | 141 | **-0.041** [-0.25, +0.17] | +0.002 | 0.92 | 38 | -7 % | -0.004 ± 0.103 | -0.36 | 0.662 |
| B | dev | 830 | -0.178 [-0.27, -0.09] | -0.141 | 0.72 | 30 | -57 % | -0.073 ± 0.041 | -2.55 | 0.990 |
| B | **holdout** | 472 | **-0.179** [-0.29, -0.06] | -0.147 | 0.72 | 31 | -35 % | -0.099 ± 0.048 | -1.67 | 0.960 |
| C | dev | 92 | +0.176 [-0.09, +0.44] | +0.249 | 1.34 | 46 | -6 % | -0.094 ± 0.109 | +2.48 | 0.005 |
| C | **holdout** | 50 | **-0.225** [-0.53, +0.10] | -0.145 | 0.66 | 32 | -7 % | -0.005 ± 0.175 | -1.25 | 0.866 |
| Z | dev | 1263 | -0.049 [-0.12, +0.03] | -0.008 | 0.91 | 34 | -39 % | -0.035 ± 0.033 | -0.43 | 0.682 |
| Z | **holdout** | 688 | **+0.033** [-0.07, +0.14] | +0.082 | 1.03 | 38 | -20 % | -0.029 ± 0.045 | +1.39 | 0.085 |

Holdout p-values after Holm over the four arms: A 1.00, B 1.00, C 1.00, Z 0.34. Bonferroni over all 36 simulated
variants: all 1.00. Day-cluster bootstrap CIs are 10-55 % wider than the i.i.d. ones shown (B, Z trade many pairs on the
same days); e.g. Z holdout [-0.13, +0.20] vs [-0.07, +0.14].

Pooled dev + holdout (the best single estimate, still weak): A +0.017 R (SE 0.067, n=401); B -0.178 (SE 0.035,
n=1302); C +0.035 (SE 0.108, n=142); Z -0.020 (SE 0.031, n=1951). Pooled actual minus null: A +0.036 ± 0.061,
B -0.095 ± 0.031, C +0.098 ± 0.094, Z +0.013 ± 0.027.

**Cost burden** (same trades, gross vs net): 0.03-0.05 R per trade for the FX/gold arms with 15-30 pip stops
(A 0.035-0.043, B 0.031-0.036, Z 0.041-0.047), 0.07-0.08 R for C (smaller stops relative to spread). Z shows the
clearest picture: a plain trend-direction bracket is about +0.02 gross and -0.02 net pooled (dev -0.008 / -0.049,
holdout +0.082 / +0.033); costs alone turn "nothing" into a small loss.

**Comparison with the benchmark:** B minus Z = -0.129 ± 0.059 (dev) and -0.212 ± 0.079 (holdout). The pullback
machinery makes the trend trade worse, not better. A and C do not beat Z on holdout either (both below +0.033).

## Robustness: +/-20 % one-at-a-time, DEV only (28 variants)

Net expR on dev for the perturbed values: A 0.098, 0.039, 0.027, 0.022, -0.012, 0.088, 0.052, 0.050 (base 0.048);
B -0.108 ... -0.205 (all 8 negative, base -0.178); C 0.079 ... 0.213 (all 8 positive, base 0.176); Z -0.034 ... -0.046
(all 4 negative, base -0.049). By the pre-registered rule all four count as "plateau" (A 7/8 same sign, B 8/8, C 8/8,
Z 4/4, max swing <= 1.6 SE). This is a weak diagnostic: the perturbed variants reuse almost the same trades, so
C's flat positive surface did not protect it from failing out of sample. Edge does not live in a narrow spike for any
arm, but for A, Z and C it is also not distinguishable from zero anywhere, and for B the whole surface is negative.

## FTMO Monte Carlo (only arms that clear dev: C)

C on dev+holdout day tables: 10 % target pass 4.7 %, 5 % target pass 26.8 %, max-loss fail 0.7 %, unresolved at 90
days 94.5 % / 72 %. That is a statement about a low-frequency strategy (9 trades/month at 0.5 % risk) with a mean near
zero (pooled +0.035 R), not about edge. A and Z, B did not clear dev, so no Monte Carlo was run.

## Statistical power (be sceptical of every number above)

Trade-level sd of R is 1.2-1.4. Standard error of mean R: A 0.07-0.11, B 0.04-0.06, C 0.11-0.17, Z 0.03-0.05. The
minimum edge detectable at 80 % power in the holdout is 0.15 R (Z), 0.16 R (B), 0.30 R (A), 0.47 R (C). Trades needed for
80 % power (5 % two-sided, i.i.d.): edge 0.20 R -> ~320; 0.10 R -> ~1,260; 0.05 R -> ~5,000-6,000. Since 0.05-0.10 R
is the scale of a plausible real edge in liquid FX after costs, and trades on one day are correlated, the realistic
requirement is 1,500-2,000 independent trades for a 0.10 R edge. At the observed rates (A 25/month, C 9/month,
B 81/month, Z 121/month, many of those overlapping across pairs) that is roughly 5-7 years (A), 14+ years (C), 2-3 years
(B, Z) of data, i.e. 16 months cannot settle any of this.

## Observations that are NOT findings

* B has negative directional skill in both windows (actual minus null -0.105 dev, -0.080 holdout, pooled 3 sigma; negative on 5 of 6
  pairs in dev and on all 6 in holdout). Buying shallow H1 pullbacks in an H4 trend lost more than random direction. Trading the mirror is
  NOT supported: the mirrored B has a net expectancy of about -0.01 to -0.02 R (the `mirror_all_expR` field), i.e.
  zero after costs, and choosing it now would be selecting on the holdout. Any such idea needs its own pre-registration
  and fresh data.
* C's dev result (z = +2.5, p = 0.005) is the textbook multiple-looks pattern: 4 arms x several windows, n = 92. It did
  not replicate (holdout -0.225). Treat dev-only "significance" at these sample sizes as noise.
* Z's holdout +0.033 (p 0.085) was negative in dev (-0.049); pooled -0.020 ± 0.031.

## Deviations, caveats and things to know

1. Warm-up guards in B/C/Z were changed from "len(H4) < 300" to causal index checks (H4 index >= 250, H1 >= 60) while
   writing the no-look-ahead test, **before any dev backtest**; no result informed it (the test is what exposed the
   data-length dependence).
2. The look-ahead tests were initially too weak (a one-bar leak only affects candidates right at the cut); I rewrote them
   to cut exactly at candidate times and to scramble all not-yet-closed bars, then verified by mutation that injected
   leaks (HTF index +1, next-bar close, today's range in ADR) are caught. Real-data cuts include the dev/holdout split.
3. During my runs other agents edited `sim.py` and `data.py` (new opt-in `SimConfig` fields, data-quality logging).
   Defaults are unchanged; I re-ran the four dev base configs afterwards and reproduced trades and expectancy exactly
   (`kind=repro_check`, not counted). The cached data files were not changed (mtimes 14:42-14:45).
4. data.py now prints "H4 not on the H4 grid" FAILs. The H4 bars follow the broker's NY-close grid (open hours 1/5/9/..
   or 2/6/10/.. UTC), not 0/4/8; my code aligns on actual open + 4 h <= T, so it is unaffected. Whoever owns that check
   should expect the grid to be DST-dependent.
5. Costs are an optimistic upper bound (no swaps, slippage, news, requotes). Drawdown figures come from compounding with
   overlapping positions and are only indicative. Spread for the 07:00 Z entries and London opens is the recorded bar
   spread; real fills at the open can be worse.
6. Variant count: 4 base dev + 28 robustness + 4 holdout = 36 selection-relevant simulations, plus null/gross replicates
   (not selection) and 4 reproducibility checks. Primary family for the multiple-testing correction is 4.
7. Random-direction nulls use K = 200 seeds (smallest possible p = 0.005). The paired "skill" estimate and its CI are in
   `ideas_report.json`.
8. "Mirror" in the null keeps the stop distance and flips side; the gross run keeps the stop distance and zero spread.

## What I would need before trusting any of this

* **More data**: at least 5-8 years of M5/H1 (the terminal caps M5 at ~16 months; third-party tick/M1 history converted
  with a documented spread model is the realistic route), ideally across several regimes (2022 USD rally, 2023-24
  ranges, 2025-26). A truly untouched holdout of >= 1,500 trades per arm.
* **Tests**: out-of-sample walk-forward with the rules frozen; day-cluster or block bootstrap everywhere; a cross-
  sectional check (does it work on pairs not used in development, e.g. NZD, CHF, EURJPY); a stability test of the edge to
  +1 pip extra spread and to 1 bar of entry delay; spread/slippage taken from tick data at the actual decision times.
* **Mechanism checks** for any survivor: where does the gross edge come from (session, volatility regime, one pair, a few
  months)? A rule whose gross edge is under about 0.1 R cannot pay 0.03-0.08 R of costs reliably.
* A new pre-registration for any rule derived from what was seen here (e.g. the B anomaly), with its own holdout.
