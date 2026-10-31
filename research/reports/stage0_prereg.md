# Stage 0 pre-registration: does the live rules' signal carry information? (written 2026-10-29, revision 1 the same day)

Written before any run of this campaign on the DEV window. Part of `GAMEPLAN.md` (sections 3, 4, 5). Revision 1 was made after an
independent attack on the first draft and **before any DEV result existed**; what changed is listed at the end. If anything has
to change after the first DEV run, the change is logged at the bottom with the reason and the date, and the first result stays
on record.

## 0. What has already been seen (so nobody mistakes it for blind)

* 2025-05 to 2026-10: the 825-trade base backtest (-0.205 R, null z -0.74, actual minus null -0.028 R ± 0.04), the rule-change
  study, the four-idea study. Burnt.
* Aggregate only, whole cache 2021-02 to 2026-10: 3,509 trades, -0.116 R (reviewer run, 2026-10-27). No per-year or per-pair
  breakdown is known to exist.
* Data-quality tables by year and the two data investigations below (no P/L).
* A timing probe of the null harness on 2026-09-01 to 2026-10-29 (burnt window, output deleted) to learn run time only.
* NOT seen: any trade, expectancy or null result on 2014-01-01 to 2021-05-31 (DEV) or on 2021-06-01 to 2025-04-30 (FINAL)
  other than through the aggregate above.

## 1. Question and estimand

Do the live rules' trades beat a random-direction version of themselves, net of costs?

skill = E[R | actual trades] minus E[R | same candidates with each direction flipped with probability 1/2, same entry price, stop
distance, sizing, gates and costs] (`research.nulltest`: the stop is mirrored about the entry). Unit: R per trade.
**The estimand is direction plus structure-stop placement relative to a mirrored stop**, not pure direction: the actual stop
sits just beyond a swing, the mirrored one at an arbitrary level, so a sweep-prone structure stop pushes skill down.

## 2. Window and data

DEV = `--start 2014-01-01 --end 2021-06-01` (M5 from 2014-01-01; H1, H4 and M15 keep their earlier history as warm-up, which is
DEV-era data; every timeframe is cut at the end date, so FINAL bars cannot be read). Pairs: the six in `config.PAIRS`.
Costs: the simulator defaults (bar spread, zero recorded spreads imputed from the same-hour non-zero median inside the loaded
window, commission $2.50 per lot per side). The imputation uses the whole DEV window (a mild future look inside DEV); it moves
the cost level identically for actual and null and cannot move skill, but DEV levels must not be read as break-even claims.

Data flags, resolved by reading data only:

* 2016 and 2017 "weekly open at UTC hour 4" fail: this is exactly one reopening per year, Mon 2016-12-26 and Tue 2017-12-26 at
  04:00 UTC, the Christmas holiday. Not a clock shift. No exclusion.
* 2019 "spikes" (85 to 181 flagged M5 bars per pair): 235 of the 292 distinct flagged timestamps are in the 21:00 to 22:00 UTC
  rollover hour, none coincides across all five pairs. Rollover and weekly-open range expansion, not bad ticks. No exclusion.
* GBPUSD 2019: about 7,900 M5 bars missing (a real hole). Excluded in the "clean" subset only.
* XAUUSD: treated like a 5-digit pair for stop buffers and has zero-spread stretches. Reported with and without gold.

## 3. Runs

Both `python -m research.nulltest --start 2014-01-01 --end 2021-06-01 --seeds 200 --workers 12 --live-settings` with a distinct tag:

| Run | Tag | Signal | Role |
|---|---|---|---|
| R2 | `stage0_R2` | faithful (dead sweep, the live code) | kill-set run |
| R4 | `stage0_R4` | `--sweep-mode fixed` (a working sweep: a different strategy, a superset of R2's candidates) | kill-set run |

`--live-settings` pins fixed risk 0.25 % and the Friday flatten at 20:00 UTC (hard-coded in `nulltest.py`, equal today to `config.py` and
`utils/risk_manager.py`). Null seeds 0 to 199. Earlier drafts' R1, R3 and R5 are dropped: R1 and R3 duplicated the same hypotheses,
and the no-gold variant is a post-hoc subset of the same simulation (below), not a separate run.

R2 and R4 are strongly correlated and are **two looks at overlapping data, not two independent tests**.

Order of work: the two null runs first; only then, descriptive only, `python -m research.run --tag stage0_actual --start 2014-01-01 --end 2021-06-01 --no-mc`
for per-year tables, so nobody forms a story from unadjusted per-year R before the skill estimate exists.

## 4. Estimator

For every run the stored per-(ISO week, symbol) tables (`weekly_table`: trade count and sum of R by the ISO week of `open_T`, **END trades
excluded** exactly as `summarize` excludes them) give

* skill_hat = actual mean R minus the mean over the 200 null seeds of each seed's mean R;
* SE by a **paired week-block bootstrap** (2,000 resamples, seed 12345): whole ISO weeks are resampled with replacement, jointly
  for the actual run and every null seed, so price-path noise shared by actual and null cancels;
* UB = skill_hat + 1.96 SE (kill test); LB = skill_hat - 2.24 SE (proceed test, one-sided 0.0125, Bonferroni over the two runs).

Four variants per run, all post-hoc subsets of the same stored tables: `all`, `no_gold` (XAUUSD removed), `clean` (GBPUSD 2019 removed),
`clean_no_gold`. Descriptive only, no decisions: skill by year and by pair.

## 5. Decision rule (implemented and tested in `research/stage0.py`)

* **PROCEED**: LB > 0 on the `all` variant of R2 or of R4. Selected among two looks; anything built on it still has to pass the FINAL
  confirmation (alpha 0.025, at most two candidates).
* **KILL**: UB < +0.05 R in all four variants of both runs. Recorded as "an edge above 0.05 R is excluded at about 97.5 % one-sided
  confidence on DEV". The pipeline and AI arms stop; the time goes to cost hygiene and track R3 (slow factors).
* **Precedence**: if the PROCEED and KILL conditions could both hold (a very small SE around a small positive skill), PROCEED wins.
  KILL also requires both kill-set runs to be present; one run alone is INCONCLUSIVE.
* **INCONCLUSIVE**: anything else, including any missing, null or NaN estimate (a `null` read back from JSON counts as NaN). Funds only the cheap items (cost hygiene, the same-bar audit,
  track R3); the pipeline and AI arms wait for more data, not for a re-run.
* If skill_hat is clearly negative (UB < 0) it is reported as negative skill, not mirrored; mirroring would be a new idea with its own
  pre-registration on fresh data.

Expected behaviour (assumptions, to be checked against the SE that is actually obtained; the rule does not change if they are wrong):

| true skill | P(UB < 0.05) for one variant, SE = 0.02 | SE = 0.03 | P(PROCEED) for one run, SE = 0.02 | SE = 0.03 |
|---|---|---|---|---|
| -0.03 R | 0.98 | 0.76 | below 0.01 | below 0.01 |
| 0.00 R | 0.70 | 0.39 | 0.01 | 0.01 |
| +0.05 R | 0.025 | 0.025 | 0.60 | 0.28 |

Kill needs all eight estimates below threshold, so its real probability is lower than the one-variant figures. This is why INCONCLUSIVE exists.

## 6. Power statement

Measured trade-level sd of R in the base run is 1.57 (not 1.25). With about 3,500 to 5,500 DEV trades (the earlier rate is 40 to 52 a
month; DEV is 89 months) the SE of the actual mean alone is about 0.021 to 0.027 before clustering. The paired design is expected to give
a skill SE nearer 0.02 than the unpaired 0.03, but that is an expectation, not a promise. Skill below about 0.05 R cannot be separated
from zero here and is explicitly outside what this stage can claim.

## 7. Diagnostics reported with every run (change nothing)

Funnel counts for the actual run against the mean of the null seeds (cooldown, correlation, max-open, blocked_*), the BUY share
(actual against null), and `(actual + mirror_all) / 2` against the null mean (a gap above about 0.02 R flags state or asymmetry effects, e.g. the
BUY-first cooldown guard and the weekend-reopen spread on shorts). Provenance stored in the output: git HEAD, a hash of the uncommitted diff,
the argv, the dumped `SignalConfig` and `SimConfig`, seeds and thresholds.

## 8. What I will not do

No parameter changes, no re-runs with other settings after seeing the DEV result, no use of FINAL, no `--force`. Crashed runs are repeated
unchanged and logged.

## Log of changes after registration

(none yet)

## Revision 1 (2026-10-29, before any DEV run), in response to the independent attack

1. Kill set reduced to R2 and R4 (R1, R3, R5 dropped), each with and without gold and on the clean subset; PROCEED now needs a lower
   bound above zero, not an upper bound above 0.05 (the first draft would have proceeded on noise about two times in three).
2. Estimator changed from an unpaired day-cluster bootstrap to a paired week-block bootstrap; END trades excluded consistently.
3. Estimand renamed to include the structure-stop placement confound.
4. Data flags resolved by reading the data (Christmas reopening, rollover-hour range expansion), exclusion rule fixed (GBPUSD 2019 only).
5. Output tags, provenance, diagnostics and the order of work specified; trade-level sd corrected to 1.57; expected kill and false-proceed
   probabilities stated.

## Revision 2 (2026-10-29): written AFTER the first DEV result, in response to a failed sanity check. Disclosed as such.

**What happened.** The first stage-0 result (null geometry `mirror`) was INCONCLUSIVE: R2 skill +0.020 R (SE 0.020), R4 +0.015 R (SE 0.017). A cost decomposition run
afterwards (`reports/cost_decomposition_prereg.md`) failed its pre-registered sanity check: a frictionless random-direction run averaged -0.137 R instead of about 0.
`research/null_bias_audit.py` located the cause: `mirror` keeps the entry price and flips only the stop, so a retracement limit far from the market becomes a stop order
the other way, equally far; in the simulator STOP entries that exit on their fill bar average -2.10 R (1,869 of 42,792 null trades, 77 % of the null's loss) while the
real trades have 68 such exits (-1.17 R) in 3,970. The null was handicapped in a way the real trades are not, so the first skill estimate is biased upward and is
**superseded** (it stays on record unchanged in `RESULTS.md`).

**Change (instrument only).** (1) Null geometry `reflect`: the whole trade (entry, stop) is reflected about the decision-time mid, so entry type and distance from the market are
preserved. (2) Primary runs use the cleanest simulator conventions (`--clean-conventions`: neutral fill-bar rule, targets paid at gapped prices), chosen because the frictionless audit
with `reflect` gave -0.025 R under them against -0.059 R under the defaults (8 seeds). **Gate, checked before any corrected skill estimate: the frictionless mean of the primary
configuration must be within +-0.03 R of 0. Observed -0.0251 R: passes narrowly.** The remaining -0.025 R is not explained (gaps hit stops more than targets because the stop is
nearer, and the null seeds share one candidate set); it applies to real and null trades alike.

**Unchanged.** Window (DEV 2014-01-01 to 2021-05-31, FINAL untouched), 200 seeds, the paired week-block bootstrap, Z_UB 1.96 / Z_LB 2.24, KILL_UB 0.05, `research/stage0.py`'s rule
(PROCEED / KILL / INCONCLUSIVE, both runs needed to kill).

| Run | Tag | Signal | Conventions | Role |
|---|---|---|---|---|
| R2' | `stage0v2_R2` | faithful | clean | primary, kill set |
| R4' | `stage0v2_R4` | fixed sweep | clean | primary, kill set |
| R2'live | `stage0v2_R2_live` | faithful | defaults | secondary, reported |
| R4'live | `stage0v2_R4_live` | fixed sweep | defaults | secondary, reported |

All with `--null-geometry reflect --live-settings`. The verdict uses the two primary runs only; the secondary runs show how much the conventions matter.

**Honest limits.** This is a second look at the same DEV data after the first result was seen; the correction is principled (symmetry of the null) and was triggered by the failed
sanity check, not by the sign of the skill, but I knew the corrected null is less punishing, which lowers skill. The thresholds and the decision rule were not touched. Both results
are reported, and the corrected one is the one that counts. The reflected null still has some fill-bar exposure (2.2 % of null trades against 1.7 % of real trades).
