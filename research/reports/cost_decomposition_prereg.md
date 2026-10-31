# Cost decomposition and spread record: note written before the run (2026-10-29)

Descriptive work, no hypothesis test and no decision rule. It follows the stage-0 result (INCONCLUSIVE: skill about +0.02 R, random-direction null -0.150 R per trade) and asks only **where the -0.150 R goes**, so the next experiments can target the largest component.

* Window: DEV 2014-01-01 to 2021-05-31 (`--end 2021-06-01`); FINAL not touched. Candidates: `research/output/stage0_R2/base_candidates.parquet` (faithful rules).
* Method: `python -m research.decompose --tag decompose_dev --cands research/output/stage0_R2/base_candidates.parquet --start 2014-01-01 --end 2021-06-01 --seeds 40 --workers 12 --live-settings`. Random directions (no skill information), 40 seeds, base = live settings. A cumulative path (remove commission, then spread and the spread filter, then breakeven, then the Friday flatten) and one-at-a-time removals from the base.
* Sanity check stated in advance: with every friction removed (c4) the mean should be near 0 R (a random-direction trade with symmetric stops has no gross edge); a result far from 0 means the null or the simulator has a structural bias that must be understood before any cost number is trusted.
* Caveats: removing the spread filter also changes which trades are taken; steps interact, so the cumulative path depends on the order and the one-at-a-time deltas do not add up to the total; the spread uses imputed values where the broker recorded zero; Monte Carlo sd across seeds is about 0.015 R, so deltas below about 0.01 R are noise.
* Spread record (separate script `research/cost_record.py`): median non-zero recorded spread by pair, UTC hour and period, share of zero recorded spreads, and the ratio of spread to the stop distance of the stage-0 trades (cost columns only; no P/L used).
* Nothing here changes a rule. Any rule change suggested by it goes through `test-strategy-idea` with its own pre-registration.
