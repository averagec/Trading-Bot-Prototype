# Progress log

Newest entry at the top. Claude adds one entry at the end of every task (see CLAUDE.md, "Working loop").
Format: date - what changed - result/verdict - what's next or open.

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
