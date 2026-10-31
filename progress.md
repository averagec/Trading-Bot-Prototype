# Progress log

Newest entry at the top. Claude adds one entry at the end of every task (see CLAUDE.md, "Working loop").
Format: date - what changed - result/verdict - what's next or open.

## 2026-10-30 (A2' slow-horizon study: null)
- Pre-registered (`research/reports/ai_slow_prereg.md`) and run once on DEV (2003-2021-05, 5 pairs, H4 and D1, k 1.0/1.5, logistic confirmatory, K=4). Verdict: **no variant learnable, all four falsified**
  (directional AUC 0.500 to 0.505; C1, C2, S and F all fail; features make log-loss slightly worse than the non-directional model). Top-decile net R has no lower bound above 0 at x1, x1.5 or x2.
  Under the pre-registered rule the slow-horizon family on OHLC + spread + time + USD features stops. Null is for effects the design can see (AUC above about 0.515 at H4, 0.524 at D1), not for smaller ones.
- Descriptive only, not a finding: D1_k15 with the larger boosted model shows top-decile gain +0.060 R [+0.012, +0.107], 1 of 12 cells; would need its own prereg and holdout, not pursued by default.
- `research.data.sentinel_mask` now ignores bars before 2018 (constant nominal 50 in 2000-2003); test added. Suite 558 passed. Results in `RESULTS.md`.
- Open: decide next arm (GAMEPLAN section 13: A7 cost layer, R3 slow factors, D3 vendor check); FINAL still untouched.

## 2026-10-29 (AI study report, spread sentinel, re-plan)
- ML-engineer report merged (`research/reports/ai_edge_approach.md`, `ai_edge_prereg.md`, `research/ai/`, 39 tests): on H1, DEV only, no stable directional structure net of cost (Holm LB of the
  log-loss gain -5.8e-4 / -5.2e-4 / -1.9e-6); the association that exists is entirely 2019H2 (unexplained). Cost per R by horizon M15 0.086, H1 0.047, H4 0.021, D1 0.0085. Recommendation adopted:
  repeat the protocol at H4/D1, add a vol/time-of-day cost layer, drop richer H1/M15 models, sequence models, LLMs and SMC meta-labelling. `RESULTS.md` and `GAMEPLAN.md` section 13 updated.
- NEW DATA FINDING: the broker's spread field is exactly 50 points on 14 to 93 % of FX M5 bars from 2020-05 to 2021-09 (EURUSD 94.6 % in 2020-11 read directly from the terminal; 0 % from 2021-10).
  Broker history, not our pipeline; likely a placeholder. It runs 4 months into FINAL (2021-06 to 2021-09). The simulator uses it as is, so the spread gate blocks those bars and exits are charged for them.
  Robustness re-run of stage 0 stopped at 2020-05-01 (60 seeds, post hoc, disclosed): still KILL (R2 skill -0.004 R, UB 0.037; R4 -0.004 R, UB 0.031). Open: treat 50 as missing in `prepare_bars`
  (flag + test) before any FINAL run.
- DONE (user said yes): `prepare_bars(sentinel_missing=False)` + `research.data.sentinel_mask` + `--sentinel-missing` on `nulltest`/`run` (candidates built uncached when on); `test_sentinel_missing.py` (8).
  Reviewer round 1 FAIL (5 stale tests in `test_run_end_option.py`, `decompose_rc.py` unpack) fixed; round 2 PASS; full suite 535 passed; sim.py/signals.py untouched; regression 861 trades / -0.210 R.
  Full-DEV run with the placeholder imputed: R2 skill +0.003 (UB 0.041), R4 -0.005 (UB 0.028), real -0.100 R vs null -0.103 R: KILL holds, drag falls about 0.02 R. Details in `RESULTS.md`.
  Open: `stoploss_experiment`, `same_bar_bound`, `null_bias_audit` not yet wired to the flag; the flag must be applied identically to actual and null in any FINAL run.
- Open (old): reviewer pass on the new research tooling and AI package; full suite; scikit-learn is only in `research/output/ai/pkgs` (not in `requirements.txt`); the agent and I both ran stray inline
  `python -` calls again, so `.claude/settings.local.json` may have those allow entries back.

## 2026-10-27 (live hygiene + news agent, two engineers in parallel)
- News: `agents/news_agent.py` no longer touches the shared MT5 session (the MetaTrader5 package has no calendar API, so
  that source could never work; removed). Sources -> disk cache (`news_cache.json`, hourly refetch, 429 back-off, cache
  fallback up to 24 h) -> decision. Fails CLOSED: no usable calendar blocks new entries (`NEWS["fail_closed"]`). Tests: `test_live_news_agent.py` (29).
- Live hygiene: LLM sends auth headers, temperature 0, veto-only (`apply_veto`); a down session or unreadable account means
  no entries (no fabricated balance unless the MT5 library is missing); `order_send` None guarded everywhere; daily and monthly
  risk baselines persist per login (`risk_baselines.json`); pending expiry uses the broker's setup time; Excel writes are
  best-effort; kill switch = `STOP` file (exit 0) + Ctrl+C during sleep shuts down + `panic_close.py` (shared `utils/flatten.py`);
  `detect_fvg_h1` crash fixed; config/README cleaned to the code; connector uses `BOT_MAGIC`; dead code deleted. Pre-commit hook now uses its own pytest temp dir.
- Verdict: suite 385 passed, regression `--start 2025-05-23` 858 trades / -0.207 R, no rule changed; combined reviewer PASS (385 passed, 858 / -0.207 R, no must-fix).
- Reviewer non-blocking follow-ups: a failed `positions_get` (None) reads as "no positions" (`panic_close` can exit 0 after a failed
  query; `_friday_flatten` retries so it self-heals); `news_agent._load_disk` naive timestamp in a hand-edited cache raises
  TypeError once; log per-source news failure reasons at WARNING; older `_run_cycle` tests should patch `STOP_FILE`;
  `test_importing_panic_close_does_nothing` asserts too little; after a demo-account reset delete `risk_baselines.json`
  (stale baseline can trip the daily/monthly gate); a STOP file made mid-cycle is seen next cycle; restart the running
  `main.py` (it still runs the old code in memory).
- Open (needs the pre-registered protocol or a user decision): liquidity-sweep window (item 2), pip/lot maths and XAUUSD stop
  buffers (gold treated like a 5-digit FX pair: 0.0003 SL buffer, 0.10 min SL, 0.0002 limit offset and breakeven buffer;
  lot maths itself is right), equity-based daily limit / Prague reset / 10% max-loss / consecutive-loss limit / `trades_today`,
  pending orders counted as risk, limit-to-STOP conversion, quarterly-theory `datetime.now()`, reconnect loop.
  Also: fail-closed news means no entries until ForexFactory answers (no source ever worked in the logged run); the RSS
  fallback uses article publish times, not event times; `main.py` logs one line per pair per cycle while news is blocked.

## 2026-10-29 (week 1: data, stage 0, live safety)
- Data: M5 fetched to 2013 (about 1,000,000 bars per pair), `fetch --tail` brought M15/H1/H4 level; per-year quality report
  (`research/reports/data_quality_by_era.md`, `research/output/quality_by_year.txt`) and a new `--since/--until/--by-year` option on
  `data_tools quality`. Flags resolved: 2016/2017 "weekly open hour 4" is the Christmas reopening (benign); 2019 spikes are rollover-hour
  bars (benign); GBPUSD 2019 has a real 1.5-month hole; zero recorded spreads (XAUUSD 2021, EURUSD 2025 and 65 % of 2026) are imputed.
  The resampler test (MT5 higher timeframes == resampled M5) passes over 13 years.
- Stage 0 prepared, NOT yet run: `research/reports/stage0_prereg.md` (revision 1 after an independent attack, before any DEV run),
  `nulltest` now has `--end`, `--sweep-mode`, `--live-settings`, `--symbols`, a paired week-block skill estimator, diagnostics and
  provenance; `research/stage0.py` implements the KILL / PROCEED / INCONCLUSIVE rule. Tests: `test_stage0.py`, `test_run_end_option.py`.
- Live (reviewer pending): LLM in shadow mode by default (`FOREX_LLM_VETO=1` re-enables the veto), `utils/candidate_log.py` writes
  `logs/candidates.jsonl` (candidate, blocked, order rows), the account is re-checked right before an order is placed, and a halt reports
  how many bot positions are still open. Tests: `test_live_candidate_log.py` (23); two older tests adjusted on purpose (the halt now reads
  the open positions once; the veto tests opt in to veto mode). The `news_agent` MT5 session bug was already gone from the committed code.
- Full suite 428 passed.
- Direction from the user (2026-10-29): rules only, focus on research, data collection and testing. The LLM arms (A4, A5) are out of scope and
  the LLM-snapshot check is no longer needed; GAMEPLAN section 7b adds a data-collection track. Same-bar audit pre-registered
  (`research/reports/same_bar_audit_prereg.md`, Part A is a cheap bracket, M1 fetch only if triggered).
- STAGE 0 RESULT (DEV 2014-01 to 2021-05, 200 null seeds): **INCONCLUSIVE.** R2 (live rules) skill +0.020 R, SE 0.020, CI [-0.019, +0.058],
  3,970 trades, actual -0.131 R vs null -0.150 R; R4 (working sweep) skill +0.015, SE 0.017, 5,284 trades. Six of eight upper bounds are above
  +0.05 so no kill, no lower bound above 0 so no proceed. The bigger fact is the drag: a random-direction version of the same trades loses
  -0.150 R per trade (-73 % over the window); even the top of the CI (+0.058) leaves about -0.09 R. Details in `research/RESULTS.md`. FINAL untouched.
- Reviewer round 1 on the week-1 change set: FAIL on one item (`decide()` crashed on JSON null); fixed with tests; round 2 PASS (436 tests).
- SAME-BAR AUDIT DONE (burnt window): the stop-wins-ties rule decided **0 of 861** trades (`research/same_bar_bound.py`, tested, self-check 123/123),
  upper bound 0.000 R; the old "about 0.06 R same-bar pessimism" is withdrawn; no M1 fetch needed; sim default unchanged. My pre-registered Part A
  (an `optimistic_intrabar` variant) was invalid (it cannot change ties); logged as a deviation in the prereg and the variant removed.
  Full suite 442 passed. The new tool and tests have not had a reviewer pass yet (research tooling only; sim.py/signals.py untouched).
- COST DECOMPOSITION (DEV, 40 seeds) FAILED ITS SANITY CHECK: with every friction removed the random-direction null averages -0.137 R (-0.113 with the
  Friday flatten kept), not about 0; commission is about 0.044 R, the Friday flatten helps about 0.02 R, breakeven removal changes nothing. So the
  mirrored null has a structural negative bias of about 0.11 R whose source is unknown, and the stage-0 skill estimate (+0.020 R) may be inflated by it.
  Null-bias audit running (`research/null_bias_audit.py`). Spread record (`research/output/cost_record.txt`): median stop is exactly the 10-pip floor for
  EURUSD/AUDUSD/USDCAD, spread about 7 % of the stop, commission about 0.045 R; pre-2021 recorded spreads are constant across sessions.
- Two subagents running (2026-10-29): a quant on the minimum-stop-floor experiment (`research/reports/stop_floor*.md`) and an ML engineer on an AI approach
  to finding an edge (`research/reports/ai_edge_*.md`, `research/ai/`). Both told about the null-bias finding. Allow-list: seven `python -`/`python -c` entries removed.
- NULL BIAS FOUND AND FIXED (instrument): `nulltest.mirror` kept the entry price and flipped only the stop, turning far retracement limits into far stop orders;
  in the simulator STOP entries that exit on their fill bar averaged -2.10 R (1,869 null trades vs 68 real). New `reflect` null (whole trade reflected about the decision
  mid), `--null-geometry`, `--clean-conventions` (neutral fill-bar rule + targets paid at gapped prices), `build_sim_config`; frictionless reflected null -0.059 R (defaults),
  -0.025 R (clean). Remaining unexplained -0.025 R, same for real and null. Prereg Revision 2 written after the first result and disclosed.
- **STAGE 0 CORRECTED: VERDICT KILL** (R2' skill +0.006 R, SE 0.020, UB 0.044; R4' -0.004, SE 0.017, UB 0.029; all 8 upper bounds < 0.05; lower bounds < 0). An SMC-signal edge above
  +0.05 R is excluded at about 97.5 % confidence on DEV. Real rules lose -0.12 to -0.13 R per trade (-70 % over the window) from the framework, not the signal. First (biased)
  numbers superseded but kept. Details in `research/RESULTS.md`. FINAL untouched. Direction now: cost hygiene + new signal sources (slow factors, ML on raw features); SMC-based
  pipeline/meta-labelling arms stopped. Sim defaults (`tp_gap_fill` False: targets never pay gapped prices; fill-bar rule) are conservative modelling choices worth about 0.04-0.06 R
  on the null; not changed (needs reviewer + re-baseline).
- STOP-FLOOR EXPERIMENT DONE (quant subagent; `research/reports/stop_floor.md`, 27 tests): a wider minimum stop cuts the null's drag monotonically (-0.136 at 10 pips to -0.063 at 30,
  +0.073 +/- 0.014) and the real rules move similarly (-0.131 to -0.077, +0.054 +/- 0.024, not separable from noise at the pre-registered bar); trades -22 % and holds 3.6x longer at 30 pips;
  max drawdown -75 % to -46 %; real rules still lose -2.7 R/month. None helps/hurts by the rule, none nominated for FINAL. Gold needs its own floor (dollars/ATR). No live change recommended.
  The two accidental `python -` invocations by the agent re-added `python -` / `python -c "print(1)"` allow entries; removed again (the agents were told not to use inline python).
- Open: ML engineer report; reviewer pass on `same_bar_bound.py`, `decompose.py`, `cost_record.py`, `null_bias_audit.py` with the next batch; `.claude/settings.local.json` again contains
  `python -` and `python -c ...` allow entries (ask the user whether to remove them again).

## 2026-10-29 (gameplan)
- Wrote `GAMEPLAN.md`: 16-week plan (2026-11-02 to 2027-02-19) with tracks for data, pipeline refinement (entry, stop,
  target, breakeven), AI methodology arms (meta-labelling, heuristic baselines, LLM veto replay), FX research, forward
  demo, and a go / no-go gate. Revised after an independent red-team review (section 12 of the plan lists the changes):
  unburnt FINAL block, stage-0 kill rule, separate error budgets, null-first reframed, gate on a stressed-cost CI bound.
- User decisions: do the whole plan (demo first, challenge only after the gate); M5 fetch approved; LLM API budget undecided
  (A4 parked).
- Data: `python -m research.data_tools fetch --max-depth --tfs M5` run 2026-10-29: M5 about 1,000,000 bars per pair from
  2013-05 (XAUUSD 2012-07), M15/H1/H4 from 2000. NOT yet quality-checked by era; the full-history quality table still shows
  odd weekly-open hours and high zero-spread shares for XAUUSD M15/H1/H4. One AUDUSD M5 bar was revised by the broker.
- Open: week-1 items (quality by era with a `--since/--until` CLI option, pre-register stage 0 and the same-bar audit rule);
  your go on the live-safety fixes before the demo restart; gate thresholds. No code changed.

## 2026-10-27 (refusal stops the bot)
- Live: a refused account now exits the process (`SystemExit(2)`, CRITICAL log, dashboard "STOPPED - ACCOUNT REFUSED" with no
  balance) instead of idling in simulation mode: `connector.refused` set by `connect()`, handled in `ForexBot.startup()`.
  The account type is re-checked every cycle (`connector.account_still_allowed()`, first thing in `_run_cycle`, before the
  Friday flatten; an unreadable `account_info` does not stop the bot); a switch to a real account while running stops the
  bot without closing or touching anything. `_modify_sl` now also requires `connector.connected`.
  `_stop_refused` guards its cleanup (disconnect, dashboard write) so a PermissionError (workbook open in Excel) cannot
  replace the exit. Tests: `research/tests/test_live_refusal_stop.py` (24). Other connect failures (library missing,
  init/auth failure) still fall back to simulation mode, unchanged.
- Verdict: reviewer round 1 PASS (225 tests, regression 858 / -0.207 R); its dashboard-guard follow-up applied; delta review
  PASS, suite 227 passed. Minor follow-ups from it: give `disconnect()` and `update_dashboard` separate try blocks (a failing
  disconnect currently skips the dashboard write); decide whether an exception from `account_still_allowed()` should fail
  closed (today it is logged by run() and the bot retries next cycle without trading that cycle).
- Open: `agents/news_agent.py:72-80` calls `mt5.initialize()`/`shutdown()` on the shared session each time its calendar cache
  expires; afterwards `account_info()` may be None, and the per-cycle check treats None as "no verdict", so it can be blind
  for part of a run (unverified without a terminal). Fix that next. Also: re-check the account just before order placement
  in `_analyse_pair`; alert when the bot stops with bot positions still open (they keep server-side SL/TP but lose breakeven
  and the Friday flatten); rest of the demo/live guard list below minus the three items done here.

## 2026-10-27 (demo/live guard)
- Live: `connect()` logs `Account | Server | Type (DEMO/CONTEST/REAL)` and refuses any non-demo account (ERROR, `mt5.shutdown()`,
  returns False so the bot idles in simulation mode) unless `FOREX_ALLOW_LIVE` is exactly `YES` (`ALLOW_LIVE_ACCOUNT` in config.py).
  Tests: `research/tests/test_live_account_guard.py`. Closes REVIEW_2026-10-21 item 8 in part (no kill switch / STOP file yet).
- Verdict: reviewer PASS, full suite 203 passed, regression (`--start 2025-05-23`) 858 trades / -0.207 R.
- Open (reviewer follow-ups): stop the bot on a refusal instead of idling in simulation mode (that loop shows a fake 10,000
  balance, "RUNNING" on the dashboard, synthetic candles, and writes fake signals to `pair_data.json` and the Excel sheet);
  `_modify_sl` (main.py:~587) should check `connector.connected`, not just `MT5_AVAILABLE`; re-check `trade_mode` every cycle
  (an account switch while running is not caught); `news_agent` calls `mt5.initialize()`/`shutdown()` on the live session
  each cycle (existing bug); `connect()` does not `shutdown()` when `account_info()` is None; kill switch / panic-close.

## 2026-10-27 (login + data)
- Live: no credentials in `config.py`; `connect()` attaches to the terminal's logged-in account unless both
  `FOREX_MT5_LOGIN` and `FOREX_MT5_PASSWORD` are set. Cause: the hard-coded (stale) account made the terminal drop its
  logged-in account on every `python main.py`. Tests: `research/tests/test_live_config_secrets.py`.
- Data: MT5 bar cap raised to 1,000,000. M5 cache is 400,000 bars (2021-06; XAUUSD 2021-02), H1/H4 back to 2000/2004, M15 to ~2013.
  Quality: old H1/H4 history fails (Saturday bars, shifted broker clock, zero spreads) but is warm-up only; XAUUSD has
  100% zero spreads 2021-02..2021-09 (imputed). `quality_table(since=)` added; the real-cache test checks from 2020-06-01.
- Verdict: reviewer PASS. Full suite 193 passed. The 825 / -0.205 baseline no longer reproduces on the extended cache
  (default run: 3,509 trades, -0.116 R over 2021-02..2026-10); `--start 2025-05-23` gives about 858 / -0.207.
- Open: demo/live guard (REVIEW item 8; the bot now trades whatever account the terminal holds); log `server` and `trade_mode`
  at connect; stop the simulation-mode bot and restart it; new backtest baseline on the extended data with a fresh holdout.

## 2026-10-27 (live-bot risk hygiene)
- Live: fixed 0.25% risk (`FIXED_RISK_PCT`), Friday flatten from 20:00 UTC (`FRIDAY_FLATTEN_UTC`), flatten only touches
  positions with `BOT_MAGIC`, each cancel/close isolated, `close_position` handles MT5 returning None, flatten window
  stays open Fri 20:00 -> Sun 23:59 UTC to retry failed closes (ERROR summary of positions still open), dead
  `RiskManager.get_symbol_info`/`calculate_profit` deleted.
- Verdict: reviewer round 1 PASS (with findings, fixed), round 2 PASS, round 3 PASS. Full suite 186 passed; regression 825 trades / -0.205 R unchanged.
- Open: restart the live bot to load these changes; MT5 Max bars 1000000 (needs GUI steps) then probe + fetch + holdout of >=1,500 trades;
  reviewer nits (false "nothing open" log on failed position read, fetch-order race, `cancel_pending_order` None guard,
  hardcoded magic literal in connector, unused numpy import in risk_manager.py, weekend log noise on a stuck position).

## 2026-10-27 (earlier)
- Harness set up: hooks (post_edit_live.py), permissions in .claude/settings.json, CLAUDE.md rules,
  reviewer agent (.claude/agents/reviewer.md), test-strategy-idea skill, this log.
- Open: unapplied live-bot fixes in REVIEW_2026-10-21.md; project is not under git. (The MT5 password is no longer in config.py; the old one belonged to a dead account.)
