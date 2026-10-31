# Same-bar convention audit: pre-registration (written 2026-10-29)

Part of `GAMEPLAN.md` week 1. Written before any run of this audit.

## Question

How much of the simulator's residual drag on a skill-less signal (about -0.075 R after the framework changes, versus about 0.043 R
commission) is my pessimistic same-bar convention, and does the default need to change?

The conventions (`research/sim.py`, `_bar_exit`, `_try_fill`): when SL and TP are both inside one M5 bar the stop wins; on the bar an order
fills only the stop can hit (`intrabar="pessimistic"`); pending orders fill on a touch of their price. `intrabar` has three modes
(`pessimistic`, `neutral`, `optimistic`) and `tp_gap_fill` is a separate option.

## Data seen already

Only the burnt window. The earlier v1 variants run (2025-05 to 2026-10, 16 months) gave base (pessimistic) -0.205 R and `neutral_intrabar`
-0.214 R, so a neutral rule was not better. No M1 data has been loaded and no optimistic-mode run exists.

## Audit window

The burnt window only, 2025-05-23 to the end of the cache, because the audit measures a simulator convention, not signal skill, and
must not read any FINAL (2021-06 to 2025-04) trade outcome.

## Part A: bracket (no new data)

Add the variant `optimistic_intrabar` (`intrabar="optimistic"`, everything else the base) to `research.run.variants`, then run
`python -m research.run --tag audit_A --start 2025-05-23 --variants --no-mc` and read expectancy for `base_faithful`, `neutral_intrabar`,
`optimistic_intrabar`. Bracket B = optimistic expectancy minus pessimistic expectancy: an upper bound on what any better intrabar
resolution could recover on these trades (a real order of events lies between the two modes).

Decision:
* B < 0.03 R: the convention cannot move expectancy by more than 0.03 R; Part B is not run; the sim default is unchanged; the number is
  recorded in `RESULTS.md` and the 0.06 R figure is withdrawn.
* B >= 0.03 R: Part B.

## Part B: M1 re-resolution (only if triggered)

Fetch FX M1 for the audit window (ask-gated; the broker has FX M1 from 2024-02), and for each trade in the base run whose exit or fill
bar contains both a stop-side and a target-side touch, find from the M1 path which came first. Report: number of ambiguous bars, share
where the target came first, and the mean R change per trade if resolved by M1.

Decision: mean resolved gain >= +0.03 R per trade (pooled) means adding an M1-calibrated same-bar option to the simulator, default off,
through the `reviewer`; a smaller gain means the default stays and the number is recorded. The simulator default never changes without
the reviewer and a re-baseline of every earlier number.

## What I will not do

No tuning of the 0.03 R thresholds after seeing B, no use of FINAL trades, no change of the default inside this audit.

## Log of changes after registration (2026-10-29)

1. **Part A as written was invalid and was replaced.** The `optimistic_intrabar` variant (`intrabar="optimistic"`) returned exactly the base result
   (861 trades, -0.210 R, same everything). Reading `_bar_exit` showed why: that mode only decides whether a take-profit can trigger on the bar an
   order fills; a stop still wins every tie, so it cannot bracket the tie convention. Applying the written rule ("B < 0.03, stop") to that zero
   would have been wrong, so it was not applied. The variant was removed again.
2. **Replacement, done on the same burnt window (2025-05-23 onward, base run, 861 trades):** `research/same_bar_bound.py` counts the stop-outs
   (SL or breakeven) whose exit bar also reached the take-profit, the only trades a different tie rule could change. Result: **0 of 861**
   (0 on the fill bar either), so the upper bound on the expectancy change is 0.000 R per trade. Self-check: 123 of 123 take-profit exits show
   their target inside the mapped exit bar, none unmatched (the first version mapped one bar late, because the simulator stamps a step with the
   bar's close time, and the self-check exposed it).
3. Together with the earlier `neutral_intrabar` run (-0.219 R against -0.210 R, i.e. voiding fill-bar exits is slightly worse, not better), the
   decision rule's conclusion stands for a different reason: **the same-bar convention does not explain the drag; Part B (M1 fetch) is not
   run; the simulator default is unchanged; the "about 0.06 R same-bar pessimism" estimate is withdrawn.**
