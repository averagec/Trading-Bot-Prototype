---
name: test-strategy-idea
description: Use when proposing, building or evaluating a new strategy idea or a risk-rule change for the forex bot. Enforces pre-registration, DEV-only development and a single locked holdout run.
---

# Testing a strategy idea or rule change

The protocol already lives in `research/reports/ideas_prereg.md` (section 0) and `research/ideas/evaluate.py`.
This skill is the order of operations. Do not skip or reorder steps.

1. **Pre-register first.** Before any backtest of the idea, add a section to `research/reports/ideas_prereg.md`
   (or `rule_changes_prereg.md` for risk-rule changes): the exact rule, parameters, stop/target, what result would
   count as success, and what you had already seen. Date it. No P/L of this rule may have been seen.
2. **Implement** as a new `research/ideas/idea_<x>.py` using `common.py` / `simx.py`. Do not edit `research/sim.py`.
   Add a no-look-ahead test (truncate data at a cut time; candidates up to the cut must be identical).
   Run `python -m pytest research/tests -q -p no:cacheprovider`.
3. **Develop on DEV only:** `python -m research.ideas.evaluate dev --arms <X> --seeds 200`, then
   `python -m research.ideas.evaluate robust --arms <X>`. Compare against the random-direction null
   (`research/nulltest.py`). Parameters may be changed only here, and every change is logged by the harness.
4. **Freeze.** Write the final parameters into the prereg file before the holdout.
5. **Holdout once:** `python -m research.ideas.evaluate holdout --arms <X> --seeds 200`. This needs the user's approval.
   Never pass `--force` without the user asking for it, and report any forced re-run.
6. **Report:** `python -m research.ideas.evaluate report`, then write the result into `research/RESULTS.md`,
   negative results included, in plain numbers (trades, expectancy in R, vs null).
7. **Live code** is changed only if the user asks after seeing the holdout result, and that change goes through the
   `reviewer` agent.

Stop and report (do not keep iterating) if: the idea fails on DEV against the null, three parameter revisions
haven't moved it, or the holdout has been run.
