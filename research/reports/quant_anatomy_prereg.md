# Loss anatomy of the live rules on DEV: pre-registration (written 2026-10-31, before the anatomy run)

Part of `research/reports/quant_loss_risk_review.md`. Code `research/quant_loss.py`, tests `research/tests/test_quant_loss.py`. **Descriptive only**: no hypothesis is tested,
no parameter is chosen, nothing here nominates a rule. DEV only (2014-01-01 to 2021-06-01); FINAL and 2025-05+ are not read.

## What has been seen

Aggregates in `RESULTS.md` (stage 0: 3,970 DEV trades, -0.131 R; exit mix on the burnt 16 months SL 58.7 % / BE 26.9 % / TP 14.4 %; commission 0.044 R; spread 7 % of the stop for the
10-pip pinned pairs; the decomposition path). My own reproduction run (same candidates, live settings): 3,970 trades, -0.1308 R and the gate funnel (see `quant_methods_prereg.md`).
No trade-level DEV table (MAE/MFE, streaks, drawdown episodes, by-bucket outcomes) has been looked at by me.

## Tables (fixed before the run)

* A1 cost per R: mean net R = pre-cost R (gross + spread proxy) - entry spread over the stop - commission over risk; overall, by pair, by exit reason, without gold.
* A2 stop-out and outcome shares by stop size in pips (gold excluded: its pip is 0.01 dollar), by spread over stop (%), by stop in ATR(H1, 14) units (causal ATR from closed H1 bars).
* A3 exit mix and each exit reason's share of total R; by pair, entry subtype, open year and data era (before / after 2020-05-01 where the broker's spread placeholder starts).
* A4 MAE / MFE in units of the initial risk distance: share of trades (and of SL exits) whose price reached +0.5R / +1R / +2R / +3R before the exit bar; stateless first-touch odds of
  +0.5/1/2/3 R before -1R (horizon 5 days, ties to the stop) against the driftless zero-cost benchmark 1/(1+L).
* A5 losing-streak distribution (loss = net P/L below zero, close order) against 5,000 iid sequences with the same loss rate and length.
* A6 drawdown anatomy (equity and balance): worst episodes with peak, trough, recovery; monthly and weekly return distribution; what filled the worst episode; concurrent positions and open risk.
* Gate counterfactuals (A7) are the `gates` set of `quant_methods.py` (see `quant_methods_prereg.md` section 7).

## Conventions

END trades are excluded as everywhere else. Flagged data: the tables are given for all pairs and without XAUUSD; the 2020-05+ placeholder-spread era is split out in A3; the sentinel-missing
re-run of the anatomy is optional. No bootstrap intervals are attached to descriptive bucket shares (their SE is about `sqrt(p(1-p)/n)`); they are read as shapes, not tested.

## Log of changes after registration

(none yet)
