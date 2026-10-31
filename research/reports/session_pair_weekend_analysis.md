# Session, pair and weekend analysis (DEV only)

Status: PRE-REGISTRATION BLOCK WRITTEN BEFORE ANY BREAKDOWN WAS COMPUTED. Sections 1 to 3 below were written first and are not edited
after the numbers exist; everything from section 4 on is appended after the runs. The sha256 of sections 1 to 3 is stored in
`research/output/analyst_main/prereg_sha256.txt` when the first run starts.

## 1. What was already known before this analysis (disclosure)

Seen before writing the hypotheses (from `RESULTS.md`, `progress.md`, `GAMEPLAN.md`; none of it is a bucket breakdown of the DEV trade list that this analysis uses):

* DEV aggregate (2014-01 to 2021-05, sentinel treated as missing): actual -0.100 R, random-direction (reflect) null -0.103 R, skill +0.003 R (SE 0.020). Stage-0 verdict KILL for edge above +0.05 R.
* Stage-0 descriptive skill by pair and by year (SE 0.04 to 0.10 each) and a cost decomposition of the null (commission about 0.044 R; Friday flatten worth about 0.02 to 0.025 R on the null).
* The stop-floor experiment: structure stops are pinned at the 10-pip floor; gold stops are 1.2 to 2.9 dollars so the floor never binds for gold.
* On the already-seen window 2025-05 to 2026-10 (base run, no flatten): 26 trades closed at the Sunday reopen (sum -35 R, one -19.7 R USDJPY gap) and 72 trades opened at or after 20:00 UTC averaged -0.57 R. These motivated hypotheses H1 and H2; the direction is therefore not blind. DEV is fresh for them.
* ML-engineer finding: cost per R by horizon, EURUSD cost per R varies 0.035 to 0.056 by hour through the stop.
* FINAL (2021-06 to 2025-04) has not been read for outcomes here, and this code refuses to.

## 2. Definitions

* Window: DEV = bars from 2014-01-01 up to but excluding 2021-06-01 (`--end 2021-06-01`). All six pairs. The 50-point spread placeholder is treated as missing (`sentinel_missing=True`, imputed with the same-UTC-hour median of clean bars). Zero recorded spreads are imputed the same way (existing `prepare_bars` rule). Flagged years (2016, 2017: one weekly-open check; GBPUSD 2019: 1.5-month hole) are reported with and without wherever a pair or year table is a headline.
* Simulation: the stage-0 "primary" configuration: live signals (faithful, the sweep never fires), fixed 0.25 % risk, Friday flatten 20:00 UTC, neutral fill-bar rule and targets paid at gapped prices ("clean conventions"), $2.50 per lot per side commission, per-bar spreads. Random-direction null = `nulltest.reflect` (whole trade reflected about the decision-time mid), same candidates, same gates, 40 seeds (20 for the pair-subset runs). The "no flatten" runs set `friday_flatten_utc=None` and nothing else.
* Time: every hour is the UTC hour of the decision time `placed_T` (the close of the M5 bar on which the signal was evaluated) unless a table says "fill". A trade is bucketed by decision time in both actual and null runs, so the bucket is the same event in both. Local-clock sessions are not used for the confirmatory buckets; a London-local-hour table is exploratory.
* Sessions (the bot's own quarterly-theory phases, hour of `placed_T`): Asia = 00:00 to 06:59 (blocked by the QT filter and the 07-22 session gate); QT-manipulation = 07:00 to 08:59 (blocked by QT); **London = 09:00 to 11:59; New York = 12:00 to 17:59; Late = 18:00 to 21:59**; Closed = 22:00 to 23:59 (blocked by the session gate). Tradeable window in practice = 09:00 to 21:59 UTC, Monday to Friday before 20:00 on Fridays. Sub-windows used for cost: Overlap = 13:00 to 16:59; Rollover = 20:00 to 21:59.
* R = net P/L (after commission and bid/ask spread) divided by the dollar risk at the sized stop. END trades (open when data ended) are excluded.
* Cost per R of a trade = commission in R (commission / risk_usd) + entry spread in R (spread price / stop distance in price). Spread is paid once per round trip on the bid/ask chart, so this is the first-order cost; mean cost per R of a bucket = mean over its trades. Stop in pips = |entry - sl0| / pip size with pip size = point x 10 for 3- and 5-digit symbols and = point for gold (the live definition, so a gold "pip" is one cent).
* Skill of a bucket = mean R of actual trades in the bucket minus the mean over null seeds of the per-seed bucket mean R. Day-cluster bootstrap: whole UTC decision days are resampled with replacement, jointly for the actual run and every null seed (2000 resamples, seed 12345). One-sided Bonferroni lower bound for the skill family (below).
* Effective independent bets: from the correlation matrix of daily summed R per pair (days with no trade count as 0): N_eff = (sum of eigenvalues)^2 / sum of squared eigenvalues (between 1 and 6); and the trade-level design effect from the intra-day correlation of R (one-way ANOVA estimator over days with at least 2 trades): N_eff,trades = N / (1 + (mean cluster size - 1) x ICC).
* USD side of a trade: long USD = BUY USDJPY, USDCAD or SELL EURUSD, GBPUSD, AUDUSD, XAUUSD; the other direction is short USD. Two trades the same day on the same USD side are "the same bet".

## 3. Pre-declared hypotheses

**Family S (signal; tests of skill = actual minus null). K = 14 comparisons: 3 session phases + 6 pairs + 5 weekdays, all in the decision-time buckets above. A bucket counts as a signal only if its one-sided Bonferroni lower bound (z = 2.69, alpha 0.05 / 14) is above 0. Prior stated now: none will pass, the overall skill is zero. Per-pair skill by pair was already described in stage 0, so S-pairs is not a blind test. Anything that fails is reported as "no skill detected", not as "no skill".** The detectable effect per bucket is stated next to each estimate (about 2.8 x SE for 80 % power at the unadjusted level).

**Family C (structure and cost; estimates with CIs and a pre-declared direction, not skill claims). 6 items:**

* C1. Cost per R is higher for entries decided 20:00 to 21:59 UTC (rollover) than for 09:00 to 19:59, by a ratio of at least 1.3, because the broker widens spreads into the daily rollover.
* C2. The random-direction null's mean R (cost plus structure, no signal) is lower for rollover entries than for 09:00 to 19:59 entries (difference below 0).
* C3. Holding through the weekend lowers expectancy: for identical candidates, mean R with the live Friday 20:00 flatten minus mean R without it is above 0, in the actual run and in the null.
* C4. XAUUSD's cost per R (commission plus spread over stop) is lower than every FX pair's, because its structure stops are 100 to 300 live "pips", and its null mean R is above the FX pooled null mean R.
* C5. Outcomes cluster by day: the intra-day correlation of R is above 0, same-day trades share the USD side more often than 50 %, and N_eff,trades is clearly below N.
* C6. Spread by UTC hour: for the five FX pairs the median spread (clean bars) is lowest between 07:00 and 17:00 UTC and highest around 21:00 to 23:00 UTC and around the weekly open; the live window (09:00 to 21:59) therefore contains the single most expensive tradeable hour (21:00 to 21:59) and excludes no cheap hour except 07:00 to 08:59, which the QT filter removes.

**Exploratory (no inference claimed; numbers reported with CIs and counted in the comparison tally):** every hour of day (13 tradeable hours), weekday x session, month of year, year, pair x session, London-local-hour, the weekly gap-fill statistics, any pair subset ranking, the ex-post "remove this bucket" counterfactuals.

**Pair-subset runs (pre-declared set, 8 subsets):** all six; each pair removed in turn (six runs, one of which is the ex-gold set); EURUSD + GBPUSD + USDJPY only. The simulation is re-run per subset (shared account, max-open and correlation gates), not filtered ex post. No subset is nominated; a subset is only described.

**Tally rule:** the final report states the number of buckets examined (confirmatory 14 + 6, exploratory counted per table). Any exploratory bucket whose CI excludes 0 is a hypothesis for a pre-registered holdout, not a result.

**Stop rule / FINAL:** no FINAL outcome is read. Recommended follow-up experiments (section 8) are specified with exact rules and power but are not run here.

---

# RESULTS (appended after the runs; sections 1 to 3 above are unchanged; they refer to the follow-up experiments as "section 8", which are section 12 here)

## 4. Answers in brief

All numbers are DEV (2014-01 to 2021-05), primary stage-0 configuration, 4,173 closed trades, 40 random-direction null seeds, day-cluster bootstrap. Overall: E[R] -0.100, null -0.104, skill +0.004 (SE 0.019). The run reproduces the stored stage-0 sentinel run exactly (same 4,173 trades, same R series, same funnel).

1. **Weekends.** The live bot does not enter on Saturday or Sunday and, with the Friday 20:00 UTC flatten working, holds nothing over the weekend. Its first possible entry of a week is Monday 09:00 UTC and its last is Friday 19:55 UTC (section 5.1). The replay differs from live in one respect: it does not block Sunday, and made 18 Sunday-21:00 decisions in 7.4 years (0.4 % of trades, mean +0.043 R). If the flatten did not exist, 5.2 % of trades (220 of 4,201) would be open across a weekly closure; the flatten is worth **+0.013 R per trade for the real signals [-0.002, +0.028] and +0.009 R for the random-direction null [+0.002, +0.016]**, about +57 R over the 89 months (0.64 R per month) and it removes the two largest weekend tails (-12.5 R USDJPY, -11.2 R USDCAD, both breakeven stops gapped through). It does not help against news gaps inside the week (the third-worst trade, -12.7 R GBPUSD on 2017-06-08, is a Thursday).
2. **Pairs.** No pair has detectable skill (per-pair skill -0.035 to +0.037, SE 0.04 to 0.075, best Bonferroni lower bound -0.07), and no pair subset changes the sign: expectancy for the eight subsets runs -0.081 to -0.120 with SE about 0.025, so the differences are noise. What a subset does change is volume: three pairs give 27.9 trades a month against 46.9, a -46 % against -70 % maximum equity drawdown and half the monthly loss in R (-2.4 against -4.7), which is simply fewer draws of a -0.1 R expectancy. Cost per R is 0.070 (gold) to 0.103 (USDJPY). Gold does behave differently (stops $2.89 not pinned to the floor, lowest cost per R, 20 % wins because its breakeven stop earns nothing) but its null is not distinguishable from the FX pairs'. Outcomes are nearly independent across pairs (effective number of independent daily series 5.98 of 6), although the signals themselves point the same USD side on 67 % of same-day pairs.
3. **Sessions.** Tradeable window in practice is 09:00 to 21:59 UTC (the 07-22 session gate plus the quarterly-theory block before 09:00). By session the skill is London -0.006, New York -0.010, Late +0.058; by weekday -0.038 to +0.072. **0 of the 14 pre-registered skill tests pass** (best Bonferroni lower bound -0.040). Conditions are about equally cheap from 05:00 to 18:00 UTC; cost per R is 0.086 to 0.100 across 09 to 20 UTC and 0.106 at 21 UTC, so the whole hour-of-day cost range is 0.02 R against a total cost of 0.094 R. The live window matches where data says conditions are cheap, and no cost-driven session rule is worth more than about 0.01 R. One exploratory pattern (decisions at 20:00 to 21:59 UTC: skill +0.127, CI [-0.000, +0.262], n = 299) is a hypothesis only and cannot be tested at useful power on the held-out window.

## 5. Question 1: are we trading on weekends?

### 5.1 What the live code does (read from the source)

* `main.py::_friday_flatten_due`: true from `FRIDAY_FLATTEN_UTC` (20.0, `config.py`) on Friday, and for the whole of Saturday and Sunday (`now.weekday() in (5, 6)`). `_run_cycle` calls it before the account check, the risk gate and any analysis; when due it cancels the bot's pending orders, closes the bot's positions (magic `BOT_MAGIC`, `utils/flatten.py`), retries each cycle, and returns, so no order is placed from Friday 20:00 UTC to Sunday 23:59 UTC.
* `main.py::_analyse_pair`: session gate `7 <= UTC hour < 22`, no weekday test. `strategies/smc_strategies.py::detect_quarterly_theory_h1` blocks (`block: True`) every hour before 09:00 UTC. Together: entries only 09:00 to 21:59 UTC, so Monday 00:00 to 08:59 is also closed, and the first entry of a week is Monday 09:00.
* Net live window: **Monday 09:00 to Friday 19:59 UTC.** The data show the broker's week reopening at 21:00 UTC on 250 of 381 weekends and at 22:00 UTC on 130 (US daylight saving), so the flatten fires 1 to 2 hours before the Friday close and the Sunday reopen is never traded.
* Not guarded: the flatten needs the bot running and MT5 connected at 20:00 Friday. If it fails after the market closes, positions stay open over the weekend and no code closes them on Monday (the retry stops at Sunday 23:59). The replay assumes success.
* Replay versus live: `research/sim.py` blocks entries only on Fridays from the flatten hour. It does not block Sunday, so the 18 Sunday decisions (all at 21:xx UTC, 14 filled at 21:xx and 4 at 22:xx) would not occur live.

### 5.2 Entry weekday and hour (run L, live settings)

```
placed_hour   9   10  11  12  13  14  15  16  17  18  19  20  21
placed_dow                                                      
Mon           95  74  70  71  89  97  66  66  49  36  45  32  21
Tue          107  65  64  67  74  80  77  70  66  44  45  38  30
Wed          102  87  84  72  76  78  61  64  67  47  61  40  36
Thu          111  83  77  81  93  95  74  50  63  49  54  51  33
Fri           83  50  76  81  87  82  74  57  42  50  46   0   0
Sat            0   0   0   0   0   0   0   0   0   0   0   0   0
Sun            0   0   0   0   0   0   0   0   0   0   0   0  18
```

Same table by FILL time (`open_T`):

```
open_hour  9   10  11  12  13   14  15  16  17  18  19  20  21  22
open_dow                                                          
Mon        75  84  68  72  81  101  71  68  48  38  43  28  28   6
Tue        93  68  64  65  78   74  83  74  62  48  43  36  31   8
Wed        88  90  75  78  74   80  65  60  69  52  62  33  43   6
Thu        89  95  77  78  96   96  67  62  55  52  54  43  42   8
Fri        69  49  83  68  92   85  76  61  39  51  55   0   0   0
Sat         0   0   0   0   0    0   0   0   0   0   0   0   0   0
Sun         0   0   0   0   0    0   0   0   0   0   0   0  14   4
```

Saturday decisions 0; Sunday decisions 18 (mean R +0.043); Friday decisions at or after 20:00 UTC 0; fills on Saturday 0, on Sunday 18. The live bot blocks all of Saturday and Sunday (`_friday_flatten_due`), so any Sunday decision here is a simulator-versus-live difference, quantified in section 5.3.

### 5.3 What the Friday flatten is worth

Run L has the live flatten; run N is identical except `friday_flatten_utc=None` (same candidates, same sizing, same 40 seeds). Pre-registered claim C3.

|  | n with flatten (L) | n holding (N) | E[R] with flatten | E[R] holding | flatten minus hold [95% CI] |
|---|---|---|---|---|---|
| actual signals | 4173 | 4201 | -0.100 | -0.113 | +0.013 [-0.002, +0.028] |
| random-direction null | 4408/seed | 4431/seed | -0.104 | -0.113 | +0.009 [+0.002, +0.016] |

Equity effect (compounded at 0.25 % risk per trade): max equity drawdown with flatten -69.7 % (null mean -70.2 %), holding -72.6 % (null mean -73.0 %); worst single day -4.85 % vs -4.85 %.

Exit mix with flatten (run L, actual): BE 1065 (+0.099 R), FLAT 209 (+0.437 R), SL 2283 (-1.068 R), TP 616 (+2.961 R).

* Total R: with flatten -417.6 R, without -474.2 R over 89 months (+56.7 R, 0.64 R per month, roughly +0.16 % of the account per month at 0.25 % risk). Both pre-registered directions hold; only the null's interval excludes zero. C3 is **supported in direction, small, and statistically clear only for the null**.
* The flatten does not change the worst single day (-4.85 % in both), because that day is not a weekend event. It trims the maximum equity drawdown from -72.6 % to -69.7 % (compounded).

### 5.4 Weekend exposure without the flatten

| run | group | n | E[R] [95% CI] | SL exit % | closed on the first bar after reopen % | worst R | sum R (per run) |
|---|---|---|---|---|---|---|---|
| actual | crossed a closure | 220 | +0.252 [-0.010, +0.515] | 31 | 33 | -12.5 | +55.4 |
| actual | did not cross | 3981 | -0.133 [-0.179, -0.087] | 58 | 0 | -12.7 | -529.6 |
| null | crossed a closure | 235/seed | +0.278 [+0.142, +0.426] | 35 | 31 | -12.5 | +65.5 |
| null | did not cross | 4196/seed | -0.135 [-0.162, -0.108] | 58 | 0 | -12.7 | -567.0 |

Actual trades stopped out on the first bar after a reopen: 38; mean R beyond the planned -1R -0.41, median +0.00, worst -3.70. (Spreads on the reopen bar are inflated in the data, see section 5.6; this includes that artefact.)

Actual trades that closed on the first bar after a reopen (run N), by exit reason:

| exit | n | mean R | sum R | worst R |
|---|---|---|---|---|
| BE | 20 | -1.30 | -26.0 | -12.5 |
| SL | 38 | -1.45 | -55.0 | -4.7 |
| TP | 14 | +3.33 | +46.6 | 2.9 |

All 72 of them: sum -34.4 R; the other 4129 trades: sum -439.8 R.

Eight worst trades without the flatten (actual):

```
symbol direction               open_T              close_T exit_reason      r  cross  at_reopen
GBPUSD       BUY Thu 2017-06-08 17:55 Thu 2017-06-08 21:05          BE -12.73  False      False
USDJPY      SELL Fri 2017-04-21 19:00 Sun 2017-04-23 21:05          BE -12.46   True       True
USDCAD      SELL Fri 2016-04-15 19:55 Sun 2016-04-17 21:05          BE -11.22   True       True
USDCAD      SELL Fri 2016-08-05 12:35 Fri 2016-08-05 12:40          SL  -9.50  False      False
XAUUSD       BUY Mon 2021-05-31 09:55 Mon 2021-05-31 10:00          SL  -5.45  False      False
XAUUSD       BUY Tue 2020-09-01 14:05 Tue 2020-09-01 14:10          SL  -5.15  False      False
USDCAD      SELL Wed 2016-12-14 19:05 Wed 2016-12-14 19:10          SL  -4.80  False      False
GBPUSD       BUY Fri 2016-11-04 19:20 Sun 2016-11-06 22:05          SL  -4.74   True       True
```

* Crossers have a positive raw mean (+0.25 R) only because a trade that is still open at Friday 20:00 has by definition survived (many are already at breakeven plus 2 pips). That is selection, not evidence that weekends pay: the matched comparison below holds the decision fixed.
* 72 trades closed on the first bar after a reopen: sum -34.4 R (20 breakeven stops -26.0 R, 38 stop losses -55.0 R, 14 targets +46.6 R). The target gains are paid at the gapped price (`tp_gap_fill`, the clean convention), so the simulator is generous on the upside; the stop losses are charged at the gapped open plus the data's inflated first-bar spread (section 5.6).
* The two weekend tails are breakeven stops: a breakeven stop sits 2 pips from entry and fills at the reopen price when the market gaps through it, turning a 0 R trade into -11 to -12.5 R. At 0.25 % risk, -12.5 R is -3.1 % of the account.

dR = R without flatten minus R with flatten, per matched decision (positive = holding was better).

|  | group | matched n | mean dR [95% CI] | sum dR (per run) |
|---|---|---|---|---|
| actual | all matched | 4130 | -0.013 [-0.025, -0.002] | -55.3 |
| actual | N-trade crossed a closure | 174 | -0.210 [-0.495, +0.069] | -36.6 |
| actual | not crossed | 3956 | -0.005 [-0.007, -0.002] | -18.7 |
| null (seed-matched) | all matched | 4349/seed | -0.007 [-0.014, -0.001] | -32.4 |
| null (seed-matched) | N-trade crossed a closure | 188/seed | -0.091 [-0.236, +0.062] | -17.1 |
| null (seed-matched) | not crossed | 4161/seed | -0.004 [-0.005, -0.002] | -15.2 |

### 5.5 Friday-late and Monday-early entries

Claim C2-adjacent, exploratory. In run L (flatten on) the Friday 15:00 to 19:59 decisions look like any other hour (E[R] -0.134, null -0.132, skill -0.002). Without the flatten the same window falls to -0.290 R [-0.480, -0.102] (null -0.252), so the exposure that costs money is the hold over the weekend, not the Friday hours.

| bucket | n | E[R] [95% CI] | null R | skill (SE) | MDE80 | cost R [CI] | win % | stop pips | stop/spread |
|---|---|---|---|---|---|---|---|---|---|
| Mon 09:00-12:59 | 310 | -0.233 [-0.386, -0.081] | -0.157 | -0.076 (0.069) | 0.194 | 0.099 [+0.094, +0.104] | 38.1 | 10.0 | 17.5 |
| Tue-Thu + Mon 13:00-17:59 | 3117 | -0.088 [-0.138, -0.034] | -0.096 | +0.008 (0.023) | 0.064 | 0.094 [+0.092, +0.096] | 42.8 | 10.0 | 19.4 |
| Fri 09:00-14:59 | 459 | -0.080 [-0.218, +0.058] | -0.115 | +0.035 (0.062) | 0.174 | 0.092 [+0.088, +0.096] | 41.6 | 10.9 | 19.6 |
| Fri 15:00-19:59 | 269 | -0.134 [-0.260, +0.001] | -0.132 | -0.002 (0.054) | 0.150 | 0.091 [+0.086, +0.097] | 43.9 | 10.5 | 20.4 |

Same windows in the run WITHOUT the flatten (run N), where Friday-late entries can be held across the weekend:

| bucket | n | E[R] [95% CI] | null R | skill (SE) | MDE80 | cost R [CI] | win % | stop pips | stop/spread |
|---|---|---|---|---|---|---|---|---|---|
| Fri 15:00-19:59 (no flatten) | 278 | -0.290 [-0.480, -0.102] | -0.252 | -0.038 (0.076) | 0.212 | 0.092 [+0.087, +0.097] | 40.6 | 10.5 | 20.5 |
| Fri 20:00+ (no flatten) | 53 | +0.065 [-0.392, +0.516] | -0.241 | +0.306 (0.195) | 0.545 | 0.101 [+0.091, +0.112] | 56.6 | 10.0 | 16.2 |
| Mon-Thu 20:00+ (no flatten) | 291 | +0.018 [-0.154, +0.190] | -0.111 | +0.129 (0.069) | 0.194 | 0.103 [+0.098, +0.107] | 44.7 | 10.0 | 16.7 |

### 5.6 Gaps, reopen spreads and gap fills (market data, not P/L)

| pair | weekends | median |gap| pips | p90 | max | median trade stop (pips) | % of gaps >= the median stop | % > 10 pips | % gaps up |
|---|---|---|---|---|---|---|---|---|
| EURUSD | 381 | 5.8 | 23.5 | 178 | 10 | 29.9 | 30 | 41 |
| GBPUSD | 380 | 8.9 | 28.9 | 208 | 14 | 32.1 | 46 | 43 |
| USDJPY | 381 | 7.0 | 29.6 | 151 | 10 | 37.0 | 37 | 41 |
| AUDUSD | 381 | 5.7 | 20.0 | 73 | 10 | 29.1 | 29 | 44 |
| USDCAD | 381 | 7.2 | 22.0 | 131 | 10 | 36.2 | 36 | 44 |
| XAUUSD | 371 | 93.0 | 351.0 | 2380 | 289 | 12.1 | n/a | 51 |

Gap fill (price re-crosses the last Friday close) within 1 h / 4 h / 24 h / 5 days of the reopen, % of weekends:

| pair | 1 h | 4 h | 24 h | 5 d |
|---|---|---|---|---|
| EURUSD | 52 | 71 | 87 | 94 |
| GBPUSD | 48 | 65 | 86 | 94 |
| USDJPY | 52 | 77 | 87 | 94 |
| AUDUSD | 53 | 74 | 87 | 96 |
| USDCAD | 52 | 72 | 88 | 96 |
| XAUUSD | 87 | 88 | 92 | 95 |

FX pairs pooled, by gap size (exploratory):

| |gap| | weekends | fill 1 h % | 4 h % | 24 h % | 5 d % |
|---|---|---|---|---|---|
| <2 pips | 360 | 84 | 94 | 98 | 99 |
| 2-5 | 389 | 72 | 88 | 95 | 99 |
| 5-10 | 478 | 51 | 75 | 91 | 97 |
| >10 | 677 | 22 | 48 | 74 | 89 |

Median recorded spread in pips on the Nth M5 bar after the weekly reopen (multiple of the pair's overall median clean spread):

| bar | EURUSD | GBPUSD | USDJPY | AUDUSD | USDCAD | XAUUSD |
|---|---|---|---|---|---|---|
| +0 min | 1.7 (2.4x) | 3.0 (2.7x) | 2.0 (2.5x) | 2.6 (2.9x) | 4.1 (4.6x) | 5.0 (0.2x) |
| +5 min | 0.9 (1.4x) | 2.3 (2.1x) | 1.3 (1.6x) | 1.8 (2.0x) | 3.0 (3.3x) | 29.0 (1.2x) |
| +10 min | 1.0 (1.4x) | 2.4 (2.2x) | 1.3 (1.6x) | 1.8 (2.0x) | 3.0 (3.3x) | 29.0 (1.2x) |
| +25 min | 0.9 (1.3x) | 2.2 (2.0x) | 1.2 (1.5x) | 1.6 (1.8x) | 3.0 (3.3x) | 30.0 (1.2x) |
| +55 min | 0.8 (1.1x) | 1.4 (1.3x) | 0.9 (1.1x) | 1.1 (1.2x) | 1.8 (2.0x) | 32.0 (1.3x) |

Reopen hour (UTC) of the first bar, count of weekends: EURUSD {4: 1, 21: 250, 22: 130}; GBPUSD {1: 1, 4: 1, 21: 250, 22: 125, 23: 3}; USDJPY {4: 1, 21: 250, 22: 130}; AUDUSD {4: 1, 21: 250, 22: 130}; USDCAD {4: 1, 21: 250, 22: 130}; XAUUSD {21: 232, 22: 132, 23: 7}.

Reading it:

* One weekend in three gaps past a typical 10-pip stop on FX (30 to 37 % of weekends at the median stop; 46 % for GBPUSD with its 14-pip median stop). p90 gap 20 to 30 pips; extremes 73 to 208 pips. Gold: median gap $0.93, p90 $3.51, maximum $23.80.
* The first M5 bar after the reopen carries the opening-tick spread (1.7 to 4.1 pips on FX, 2.4 to 4.6 times the pair's median) and is back to 1.1 to 2 times within an hour. The simulator applies that first-bar spread to the whole bar, which inflates gap-through losses for shorts; the option `reopen_spread_zero` exists but was not used here, so the weekend loss figures are an upper bound on the gap cost.
* Gap fills (price re-crosses the last Friday close): 48 to 53 % within 1 hour, 65 to 77 % within 4 hours, 87 % within 24 hours, 94 to 96 % within 5 days. Small gaps fill almost always and fast; gaps above 10 pips fill 22 % / 48 % / 74 % / 89 % at 1 h / 4 h / 24 h / 5 d. This is a market fact, not a strategy: no costs, the first bar's own range counts, and the spreads are 2 to 4 times normal in the first hour. It is carried into section 9 as a hypothesis.

### 5.7 The window that was already seen (2025-05 to 2026-10, descriptive only)

Base run 2025-05 to 2026-10 (n=825, mean -0.205 R; no flatten, pessimistic conventions, ATR-scaled risk): 48 trades crossed a weekend closure (mean -0.39 R, sum -18.6 R); 16 closed on the first bar after a reopen (sum -40.4 R); worst trade -19.7 R; 63 decided at or after 20:00 UTC (mean -0.62 R); Sunday decisions 0.

The seen-window finding that late entries lose (-0.57 R in `RESULTS.md`; -0.62 R here by decision time, n = 63) **does not replicate on DEV**: decisions at or after 20:00 UTC average +0.027 R (n = 299) in the live-settings run. Differences in conventions (that run has no flatten, pessimistic same-bar rule, ATR-scaled sizing) mean the two are not directly comparable, and most of that window's loss sits in weekend-gap trades. Treat it as an unreplicated observation, not as a rule.

## 6. Question 2: are we optimizing the number of pairs?

### 6.1 Per pair

| pair | n | E[R] [95% CI] | null R | skill (SE) | LB bonf | MDE80 | cost R [CI] | win % | stop pips | stop/spread | skill clean |
|---|---|---|---|---|---|---|---|---|---|---|---|
| EURUSD | 846 | -0.054 [-0.148, +0.043] | -0.050 | -0.004 (0.041) | -0.115 | 0.115 | 0.092 [+0.089, +0.094] | 44.9 | 10.0 | 17.5 | +0.030 |
| GBPUSD | 713 | -0.149 [-0.253, -0.049] | -0.135 | -0.014 (0.044) | -0.132 | 0.123 | 0.078 [+0.075, +0.080] | 45.4 | 14.3 | 25.0 | -0.010 |
| USDJPY | 817 | -0.074 [-0.168, +0.026] | -0.111 | +0.037 (0.040) | -0.070 | 0.111 | 0.103 [+0.100, +0.106] | 45.8 | 10.0 | 14.7 | +0.010 |
| AUDUSD | 670 | -0.079 [-0.183, +0.029] | -0.113 | +0.034 (0.044) | -0.085 | 0.123 | 0.102 [+0.099, +0.105] | 44.2 | 10.0 | 15.7 | +0.067 |
| USDCAD | 848 | -0.132 [-0.233, -0.033] | -0.097 | -0.035 (0.043) | -0.151 | 0.121 | 0.101 [+0.098, +0.105] | 40.3 | 10.0 | 22.6 | -0.002 |
| XAUUSD | 279 | -0.144 [-0.331, +0.052] | -0.157 | +0.013 (0.075) | -0.189 | 0.210 | 0.070 [+0.065, +0.074] | 20.1 | 289.0 | 19.9 | -0.044 |

Cost and stop geometry per pair (stop pips use the live pip definition; gold pip = 1 cent, so its stop is 100-300 'pips'):

| pair | n | mean entry spread (pips) | median stop (pips) | median stop (price) | % stops at the 10-pip floor | spread R | commission R | cost R | sd of R | mean hold (h) | exit mix SL/BE/TP/FLAT % |
|---|---|---|---|---|---|---|---|---|---|---|---|
| EURUSD | 846 | 0.57 | 10.0 | 0.001 | 59 | 0.049 | 0.043 | 0.092 | 1.42 | 3.7 | 54/26/15/5 |
| GBPUSD | 713 | 0.66 | 14.3 | 0.00143 | 28 | 0.042 | 0.036 | 0.078 | 1.41 | 4.1 | 53/29/13/5 |
| USDJPY | 817 | 0.64 | 10.0 | 0.1 | 57 | 0.055 | 0.048 | 0.103 | 1.38 | 3.7 | 53/27/14/6 |
| AUDUSD | 670 | 0.65 | 10.0 | 0.001 | 66 | 0.057 | 0.045 | 0.102 | 1.38 | 4.1 | 54/26/15/5 |
| USDCAD | 848 | 0.60 | 10.0 | 0.001 | 53 | 0.048 | 0.053 | 0.101 | 1.48 | 3.0 | 58/23/16/4 |
| XAUUSD | 279 | 14.18 | 289.0 | 2.89 | 0 | 0.049 | 0.020 | 0.070 | 1.53 | 2.3 | 59/19/16/6 |

Exits by pair, n (mean R). The breakeven stop sits 2 'pips' beyond entry: 0.2 R at a 10-pip FX stop, about 0 R for gold (live quirk):

| pair | SL | BE | TP | FLAT |
|---|---|---|---|---|
| EURUSD | 457 (-1.06) | 217 (+0.13) | 131 (+2.98) | 41 (+0.45) |
| GBPUSD | 378 (-1.06) | 210 (+0.04) | 92 (+2.96) | 33 (+0.45) |
| USDJPY | 430 (-1.06) | 221 (+0.13) | 115 (+2.95) | 51 (+0.56) |
| AUDUSD | 363 (-1.05) | 173 (+0.13) | 98 (+2.96) | 36 (+0.42) |
| USDCAD | 490 (-1.09) | 192 (+0.10) | 134 (+2.95) | 32 (+0.28) |
| XAUUSD | 165 (-1.09) | 52 (-0.06) | 46 (+2.98) | 16 (+0.35) |

* No pair clears the Bonferroni bar (z = 2.69, 14 tests); the best lower bound is -0.070 (USDJPY). Detectable skill per pair is 0.11 to 0.21 R at 80 % power, so a true skill of +0.05 to +0.10 would go unseen. The clean-subset column (2016, 2017 and GBPUSD 2019 removed) moves single pairs by up to 0.06 and changes no conclusion.
* The random-direction null by pair runs -0.050 [-0.105, +0.005] (EURUSD) to -0.157 [-0.266, -0.050] (XAUUSD), -0.097 to -0.135 for the rest, intervals overlapping. The ordering does not follow cost per R (EURUSD has high cost and the best null), so it is structure plus noise, not something to select on.
* Cost per R: gold 0.070, GBPUSD 0.078, EURUSD 0.092, USDCAD 0.101, AUDUSD 0.102, USDJPY 0.103, made of spread over stop (0.042 to 0.057) plus commission (0.020 to 0.053). GBPUSD is cheap because its structure stops are wider (median 14.3 pips, only 28 % at the 10-pip floor against 53 to 66 % for the other FX pairs). Stop-to-spread is 15 to 25 times.

### 6.2 Is gold different?

* Yes in mechanics, no in detectable outcome. Median stop $2.89 (289 "pips" in the live definition, one pip = one cent); 0 % at the floor, so the 10-pip floor and the fixed pip buffers mean nothing for it; the structure buffer is 0.0003 dollars, i.e. none. Commission is 0.020 R (a $2.89 stop on a 100-ounce lot) against 0.036 to 0.053 for FX; cost per R is 0.026 lower than FX [-0.031, -0.021] (C4 first half supported).
* Win rate 20 % against 40 to 46 %: the breakeven stop is 2 pips x 0.0001 = $0.0002 from entry, i.e. zero, so gold's breakeven exits earn -0.06 R (52 trades) where FX breakeven exits earn +0.04 to +0.13 R (the 2-pip buffer is 0.2 R on a 10-pip stop). That is a live-code quirk already flagged in `progress.md`; its size here is about 52 trades x 0.2 R = 10 R over 7.4 years, below detectability.
* Null: gold -0.157 against FX -0.100, difference -0.057 [-0.170, +0.055]; C4's second half (gold null above FX) is **not supported**. Gold is 279 trades (6.7 %); its skill SE is 0.075.
* Caveat: gold's recorded spread median by hour on clean bars is $0.24 to $0.32, while the mean entry spread in the trades is $0.14 (zero spreads before and during 2021 are imputed from a global same-hour median, and the era mix differs), so gold's cost is probably understated.

### 6.3 Clustering and effective number of independent bets

Correlation of daily summed R (days without a trade count as 0), actual trades:

```
        EURUSD  GBPUSD  USDJPY  AUDUSD  USDCAD  XAUUSD
EURUSD    1.00    0.06   -0.01   -0.01    0.01    0.04
GBPUSD    0.06    1.00    0.01    0.05    0.04    0.00
USDJPY   -0.01    0.01    1.00    0.01   -0.00    0.00
AUDUSD   -0.01    0.05    0.01    1.00    0.04    0.03
USDCAD    0.01    0.04   -0.00    0.04    1.00   -0.00
XAUUSD    0.04    0.00    0.00    0.03   -0.00    1.00
```

|  |  |
|---|---|
| trades | 4173 |
| active decision days | 1547 |
| mean trades per active day | 2.70 |
| share of active days with >= 2 trades | 72 % |
| N_eff (eigenvalue) of the 6 daily-R series, actual | 5.98 of 6 |
| N_eff (eigenvalue), null mean of 20 seeds | 5.98 of 6 |
| intra-day correlation of R (ICC), actual | +0.047 |
| ICC, null mean over seeds (sd) | +0.011 (0.017) |
| design effect and effective N of trades, actual | DEFF 1.13; N_eff,trades 3701 of 4173 |
| same-day pairs on different symbols | 4035 |
| share of those pairs on the same USD side, actual [95% CI] | 66.7 % [63.7, 69.7] |
| same, null mean over seeds (random directions: about 50 % if independent) | 48.3 % |

* Daily R across pairs is essentially uncorrelated (largest |rho| 0.06; N_eff 5.98 of 6, same as the null). Same-day trades share the USD side far more often than chance (66.7 % [63.7, 69.7] against 48.3 % for random directions), because they follow one H4 USD trend, yet their R outcomes stay close to independent: with a 3R target and a 1R stop, a trade's outcome is dominated by its own path. The intra-day correlation of R is +0.047 (null +0.011, sd 0.017), design effect 1.13, effective N of trades 3,701 of 4,173. C5 is **partly supported**: the USD-side agreement and ICC > 0 are real, but the reduction in effective N is only 11 %.
* The correlation matrix uses sparse daily series (2.7 trades per active day), which understates co-movement; the USD-side agreement is the more informative number. The live correlation filter only blocks 846 of 24,562 candidates.

### 6.4 Pair subsets (re-simulated; pre-declared set of 8)

| subset | trades | per month | E[R] [95% CI] | null R | skill (SE) | sum R / month | max equity DD % (actual / null mean) | daily return sd % | worst day % | sd of daily R |
|---|---|---|---|---|---|---|---|---|---|---|
| all six | 4173 | 46.9 | -0.100 [-0.144, -0.055] | -0.104 | +0.004 (0.019) | -4.7 | -70 / -70 | 0.50 | -4.8 | 2.47 |
| without EURUSD | 3401 | 38.2 | -0.118 [-0.168, -0.068] | -0.118 | +0.000 (0.021) | -4.5 | -68 / -67 | 0.45 | -4.2 | 2.29 |
| without GBPUSD | 3551 | 39.9 | -0.095 [-0.143, -0.048] | -0.098 | +0.003 (0.021) | -3.8 | -61 / -63 | 0.45 | -2.6 | 2.32 |
| without USDJPY | 3426 | 38.5 | -0.120 [-0.170, -0.069] | -0.099 | -0.021 (0.022) | -4.6 | -65 / -61 | 0.46 | -4.1 | 2.34 |
| without AUDUSD | 3598 | 40.4 | -0.099 [-0.147, -0.052] | -0.101 | +0.002 (0.020) | -4.0 | -64 / -63 | 0.46 | -4.8 | 2.36 |
| without USDCAD | 3397 | 38.2 | -0.081 [-0.133, -0.034] | -0.105 | +0.024 (0.022) | -3.1 | -56 / -63 | 0.45 | -4.6 | 2.30 |
| without XAUUSD | 3926 | 44.1 | -0.098 [-0.143, -0.055] | -0.104 | +0.006 (0.020) | -4.3 | -69 / -68 | 0.48 | -4.9 | 2.38 |
| EURUSD+GBPUSD+USDJPY | 2481 | 27.9 | -0.085 [-0.141, -0.027] | -0.098 | +0.014 (0.024) | -2.4 | -46 / -51 | 0.38 | -4.6 | 2.06 |

* No subset is distinguishable from all six on expectancy (all within +-0.02 of -0.100, SE about 0.025). Removing a pair removes its trades and the cross-pair gate interactions; skill stays within +-0.024 of zero.
* Volume drives everything else. Trades per month fall from 46.9 to 27.9 (three pairs), maximum equity drawdown from -70 % to -46 %, daily return sd from 0.50 % to 0.38 %, and the monthly loss in R from -4.7 to -2.4. With a negative expectancy every extra trade adds expected loss, so "more pairs" is not a source of edge or of a cost saving; it is only more variance and more commission.
* The portfolio does diversify variance in the usual way (sd of daily R 2.47 for six pairs against 2.06 for three, growing less than the square root of the trade count: 1.20 against 1.30), but this lowers the loss only by trading less.

## 7. Question 3: sessions, weekdays, months and the live filters

### 7.1 Where the cycles go (all six pairs, every M5 cycle, DEV)

Statuses are sequential in the signal code (regime, then H4 bias, then the QT block, then the 4-of-5 checklist and score gate; silent drop = no valid structure stop). 'live' says what the live code does at that hour before any of this (07-22 session gate, QT block before 09:00). Percent of all M5 cycles at that hour.

| hour | live gate | cycles | regime skip % | no H4 bias % | QT block % | below checklist/score % | no valid stop % | candidate % |
|---|---|---|---|---|---|---|---|---|
| 0 | session gate | 137184 | 13.2 | 7.9 | 79.0 | 0.0 | 0.0 | 0.0 |
| 1 | session gate | 137133 | 13.0 | 7.1 | 79.9 | 0.0 | 0.0 | 0.0 |
| 2 | session gate | 137150 | 12.8 | 6.9 | 80.3 | 0.0 | 0.0 | 0.0 |
| 3 | session gate | 137185 | 12.9 | 6.9 | 80.3 | 0.0 | 0.0 | 0.0 |
| 4 | session gate | 137573 | 12.9 | 6.9 | 80.3 | 0.0 | 0.0 | 0.0 |
| 5 | session gate | 137582 | 13.0 | 7.0 | 79.9 | 0.0 | 0.0 | 0.0 |
| 6 | session gate | 137620 | 13.1 | 7.2 | 79.7 | 0.0 | 0.0 | 0.0 |
| 7 | QT block | 137735 | 13.1 | 7.2 | 79.7 | 0.0 | 0.0 | 0.0 |
| 8 | QT block | 137735 | 13.1 | 7.2 | 79.7 | 0.0 | 0.0 | 0.0 |
| 9 | open | 137711 | 13.2 | 6.9 | 0.0 | 78.1 | 0.4 | 1.4 |
| 10 | open | 137682 | 13.2 | 6.8 | 0.0 | 78.2 | 0.4 | 1.4 |
| 11 | open | 137710 | 13.2 | 6.8 | 0.0 | 78.1 | 0.4 | 1.5 |
| 12 | open | 137734 | 13.2 | 6.8 | 0.0 | 78.3 | 0.4 | 1.4 |
| 13 | open | 137688 | 13.5 | 6.8 | 0.0 | 78.0 | 0.3 | 1.4 |
| 14 | open | 137696 | 13.6 | 6.8 | 0.0 | 77.8 | 0.4 | 1.4 |
| 15 | open | 137789 | 13.6 | 6.8 | 0.0 | 78.0 | 0.3 | 1.3 |
| 16 | open | 137768 | 13.6 | 6.8 | 0.0 | 77.9 | 0.3 | 1.4 |
| 17 | open | 137442 | 13.6 | 7.0 | 0.0 | 77.7 | 0.4 | 1.4 |
| 18 | open | 136999 | 13.5 | 7.0 | 0.0 | 77.7 | 0.4 | 1.3 |
| 19 | open | 136194 | 13.5 | 7.1 | 0.0 | 77.6 | 0.4 | 1.4 |
| 20 | open | 131841 | 13.6 | 7.1 | 0.0 | 77.4 | 0.5 | 1.4 |
| 21 | open | 118792 | 13.9 | 7.5 | 0.0 | 76.8 | 0.5 | 1.3 |
| 22 | session gate | 125927 | 13.6 | 7.9 | 0.0 | 76.8 | 0.5 | 1.3 |
| 23 | session gate | 135988 | 13.2 | 7.9 | 0.0 | 77.0 | 0.5 | 1.5 |

Stateful gates inside the simulator, by decision hour (counts of candidate cycles, actual run L, live settings; `blocked_*` counts every candidate that cycle was blocked before the per-pair gates):

| hour | candidates | orders placed | guard: position open | guard: pending | cooldown | correlation filter | sizing rejected | spread > absolute cap | spread > 10 % of stop | other validator | max-open gate | time gate |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 9 | 1952 | 939 | 303 | 215 | 5 | 31 | 0 | 2 | 428 | 0 | 10 | 0 |
| 10 | 1910 | 699 | 543 | 212 | 1 | 41 | 0 | 1 | 386 | 0 | 7 | 0 |
| 11 | 2065 | 737 | 616 | 227 | 4 | 59 | 0 | 0 | 381 | 0 | 19 | 0 |
| 12 | 1931 | 689 | 576 | 185 | 8 | 57 | 0 | 1 | 363 | 0 | 18 | 0 |
| 13 | 1940 | 754 | 541 | 186 | 8 | 60 | 0 | 2 | 351 | 0 | 14 | 0 |
| 14 | 1988 | 831 | 522 | 185 | 10 | 72 | 0 | 0 | 309 | 0 | 28 | 0 |
| 15 | 1816 | 739 | 479 | 191 | 5 | 46 | 0 | 3 | 324 | 0 | 11 | 0 |
| 16 | 1885 | 668 | 533 | 230 | 3 | 68 | 0 | 2 | 350 | 0 | 11 | 0 |
| 17 | 1934 | 625 | 630 | 190 | 2 | 101 | 0 | 0 | 346 | 0 | 16 | 0 |
| 18 | 1793 | 527 | 586 | 189 | 3 | 84 | 0 | 1 | 344 | 0 | 38 | 0 |
| 19 | 1912 | 570 | 677 | 185 | 1 | 101 | 0 | 8 | 324 | 0 | 23 | 0 |
| 20 | 1885 | 402 | 569 | 143 | 0 | 74 | 0 | 6 | 314 | 0 | 15 | 343 |
| 21 | 1551 | 346 | 479 | 98 | 1 | 52 | 0 | 53 | 336 | 0 | 19 | 153 |

Whole run: 24562 candidates, 8526 orders (35 %), 4174 fills, 4173 closed trades. Blocks: guard_open_position 7054, validator_spread_rel 4556, expired_unfilled 4310, guard_pending 2436, correlation 846, blocked_time 496, blocked_monthly_target 286, blocked_max_open 229, validator_spread_abs 79, guard_cooldown 51, cancelled_time 42, blocked_daily_drawdown 3.

* Outside 09:00 to 21:59 UTC the live code blocks outright: the session gate before 07:00 and from 22:00, the QT block from 07:00 to 08:59 (about 80 % of cycles at those hours would be QT-blocked anyway after the regime and bias filters). Inside the window the hourly profile is flat: regime skip 13.5 %, no H4 bias 6.9 %, checklist or score gate fails 77.8 %, no valid stop 0.4 %, candidate 1.4 %.
* The stateful gates then remove: position already open on that pair 7,054, pending order already open 2,436, spread above 10 % of the stop **4,556 (18.5 % of candidates)**, correlation 846, Friday-after-20:00 time gate 496, monthly target 286, max-open 229, absolute spread cap 79, cooldown 51. Of 24,562 candidates 8,526 become orders (35 %); 4,310 of those orders expire unfilled (51 %) and 4,174 fill.
* The spread filter, which is already the live cost-avoidance rule, bites hardest at 09 UTC (22 % of candidates) and 21 UTC (22 %) against 16 to 18 % at 14 to 15 UTC and 20 UTC. The absolute spread cap is almost only a 21:00 hour event (53 of 79 blocks).

### 7.2 Confirmatory session and weekday families (14 pre-registered skill tests)

| session | n | E[R] [95% CI] | null R | skill (SE) | LB bonf | MDE80 | cost R [CI] | win % | stop pips | stop/spread | skill clean |
|---|---|---|---|---|---|---|---|---|---|---|---|
| ALL | 4173 | -0.100 [-0.144, -0.055] | -0.104 | +0.004 (0.019) | -0.048 | 0.054 | 0.094 [+0.092, +0.095] | 42.5 | 10.0 | 19.4 | +0.015 |
| London | 1228 | -0.115 [-0.195, -0.039] | -0.109 | -0.006 (0.035) | -0.100 | 0.098 | 0.096 [+0.094, +0.099] | 41.3 | 10.0 | 18.2 | -0.012 |
| NewYork | 2169 | -0.106 [-0.168, -0.040] | -0.095 | -0.010 (0.027) | -0.083 | 0.075 | 0.091 [+0.089, +0.092] | 42.5 | 11.1 | 20.0 | -0.005 |
| Late | 776 | -0.060 [-0.160, +0.047] | -0.119 | +0.058 (0.042) | -0.056 | 0.119 | 0.099 [+0.096, +0.102] | 44.2 | 10.0 | 17.0 | +0.110 |

| weekday | n | E[R] [95% CI] | null R | skill (SE) | LB bonf | MDE80 | cost R [CI] | win % | stop pips | stop/spread | skill clean |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Mon | 811 | -0.122 [-0.224, -0.020] | -0.106 | -0.016 (0.044) | -0.134 | 0.123 | 0.095 [+0.091, +0.099] | 41.4 | 10.0 | 19.1 | +0.014 |
| Tue | 827 | -0.107 [-0.212, -0.003] | -0.069 | -0.038 (0.046) | -0.160 | 0.127 | 0.094 [+0.090, +0.097] | 42.7 | 10.0 | 20.0 | +0.004 |
| Wed | 875 | -0.115 [-0.209, -0.015] | -0.093 | -0.022 (0.044) | -0.140 | 0.122 | 0.094 [+0.091, +0.097] | 41.1 | 10.0 | 17.9 | -0.001 |
| Thu | 914 | -0.063 [-0.161, +0.037] | -0.135 | +0.072 (0.042) | -0.040 | 0.117 | 0.094 [+0.090, +0.098] | 44.0 | 10.0 | 18.9 | +0.038 |
| Fri | 728 | -0.100 [-0.199, +0.007] | -0.121 | +0.021 (0.045) | -0.099 | 0.126 | 0.092 [+0.088, +0.096] | 42.4 | 10.8 | 20.0 | +0.018 |

* **0 of 14 pass.** Largest z among the 14 is Thursday at 1.73 (skill +0.072, SE 0.042); smallest Bonferroni-adjusted lower bound -0.040. Detectable skill at 80 % power is 0.054 R overall and 0.075 to 0.127 R per bucket. This is "no skill detected at that size", not proof of none.
* The random-direction null is -0.095 (New York) to -0.119 (Late) by session, -0.069 (Tue) to -0.135 (Thu) by weekday: within sampling error of a common -0.10, so no session or weekday has a structurally cheaper or dearer null that is distinguishable.

### 7.3 Cost by hour and the live session filter (claims C1, C2, C6)

| hour | EURUSD | GBPUSD | USDJPY | AUDUSD | USDCAD | FX median | x window median (09-21 h) | spread cost of a 10-pip stop (% of R) | XAUUSD ($) |
|---|---|---|---|---|---|---|---|---|---|
| 0 | 0.70 | 1.00 | 0.80 | 0.90 | 0.90 | 0.90 | 1.12 | 9.0 | 0.25 |
| 1 | 0.70 | 1.10 | 0.80 | 0.90 | 0.90 | 0.90 | 1.12 | 9.0 | 0.24 |
| 2 | 0.70 | 1.00 | 0.80 | 0.90 | 0.90 | 0.90 | 1.12 | 9.0 | 0.24 |
| 3 | 0.70 | 1.00 | 0.80 | 0.90 | 0.90 | 0.90 | 1.12 | 9.0 | 0.25 |
| 4 | 0.70 | 1.00 | 0.80 | 0.90 | 0.90 | 0.90 | 1.12 | 9.0 | 0.25 |
| 5 | 0.70 | 1.00 | 0.80 | 0.90 | 0.80 | 0.80 | 1.00 | 8.0 | 0.24 |
| 6 | 0.70 | 1.10 | 0.80 | 0.90 | 0.80 | 0.80 | 1.00 | 8.0 | 0.24 |
| 7 | 0.70 | 1.10 | 0.80 | 0.90 | 0.80 | 0.80 | 1.00 | 8.0 | 0.24 |
| 8 | 0.70 | 1.10 | 0.80 | 0.90 | 0.80 | 0.80 | 1.00 | 8.0 | 0.24 |
| 9 | 0.70 | 1.10 | 0.80 | 0.90 | 0.80 | 0.80 | 1.00 | 8.0 | 0.24 |
| 10 | 0.70 | 1.10 | 0.80 | 0.90 | 0.80 | 0.80 | 1.00 | 8.0 | 0.24 |
| 11 | 0.70 | 1.10 | 0.80 | 0.90 | 0.80 | 0.80 | 1.00 | 8.0 | 0.24 |
| 12 | 0.70 | 1.10 | 0.80 | 0.90 | 0.80 | 0.80 | 1.00 | 8.0 | 0.24 |
| 13 | 0.70 | 1.10 | 0.80 | 0.90 | 0.80 | 0.80 | 1.00 | 8.0 | 0.24 |
| 14 | 0.70 | 1.10 | 0.80 | 0.90 | 0.80 | 0.80 | 1.00 | 8.0 | 0.24 |
| 15 | 0.70 | 1.10 | 0.80 | 0.90 | 0.80 | 0.80 | 1.00 | 8.0 | 0.24 |
| 16 | 0.70 | 1.10 | 0.80 | 0.90 | 0.80 | 0.80 | 1.00 | 8.0 | 0.24 |
| 17 | 0.70 | 1.10 | 0.80 | 0.90 | 0.80 | 0.80 | 1.00 | 8.0 | 0.24 |
| 18 | 0.70 | 1.10 | 0.80 | 0.90 | 0.80 | 0.80 | 1.00 | 8.0 | 0.24 |
| 19 | 0.70 | 1.10 | 0.80 | 0.90 | 0.90 | 0.90 | 1.12 | 9.0 | 0.24 |
| 20 | 0.70 | 1.10 | 0.80 | 0.90 | 0.90 | 0.90 | 1.12 | 9.0 | 0.27 |
| 21 | 0.70 | 1.20 | 0.90 | 1.10 | 1.20 | 1.10 | 1.38 | 11.0 | 0.28 |
| 22 | 0.70 | 1.20 | 0.80 | 0.90 | 1.10 | 0.90 | 1.12 | 9.0 | 0.32 |
| 23 | 0.70 | 1.05 | 0.80 | 0.90 | 0.90 | 0.90 | 1.12 | 9.0 | 0.29 |

Window median (hours 9 to 21, FX pooled) = 0.80 pips. Gold column is the median recorded spread in dollars (cents / 100 x 1).

|  | rollover 20-21:59 | 09:00-19:59 | difference [95% CI] |
|---|---|---|---|
| trades (actual) | 299 | 3874 |  |
| cost per R, actual trades | 0.102 | 0.093 | +0.009 [+0.004, +0.014] |
| cost ratio rollover / base [95% CI] | 1.10 |  | [1.05, 1.15] |
| E[R], random-direction null | -0.099 | -0.104 | +0.005 [-0.095, +0.110] |
| E[R], actual signals | +0.027 | -0.110 | +0.137 [-0.043, +0.313] |

Rollover bucket on its own: n=299, E[R] +0.027 [-0.141, +0.199], null -0.099, skill +0.127 (SE 0.068, 95% CI [-0.000, +0.262]), MDE80 0.190. Exploratory (not one of the 14 confirmatory tests).

* C6: **supported with a small magnitude.** FX median spread is 0.8 pips from 05:00 to 18:00 UTC, 0.9 from 19:00 to 20:00 and from 22:00 to 04:00, and 1.1 at 21:00 (1.38 times the window median). Gold's median spread goes from $0.24 to $0.27 at 20:00, $0.28 at 21:00 and $0.32 at 22:00. The recorded spread resolution is 0.1 pip, so one tick separates most hours.
* C1: direction supported, magnitude not. Rollover decisions (20:00 to 21:59) cost 0.102 R against 0.093 R for 09:00 to 19:59, a ratio of 1.10 [1.05, 1.15], below the pre-declared 1.3.
* C2: **not supported.** The null's mean R is -0.099 for rollover against -0.104 for 09:00 to 19:59 (difference +0.005 [-0.095, +0.110]). Cost differs by 0.009 R and the null shows no penalty.
* The live 07-22 window therefore matches where conditions are cheap: it excludes no cheap hour except 07:00 to 08:59, which costs the same as any other hour but is removed by the QT block (the signal value of those hours cannot be assessed here, no candidates exist there). The most expensive tradeable hour, 21:00, is inside the window but carries 3.3 % of trades.
* Cost per R varies more by **year** than by hour or pair: 0.075 (2020) and 0.078 (2018) up to 0.110 to 0.113 (2016, 2017), with stop-to-spread from 51 down to 14 (table 8.3). The spread regime of the broker's history is a larger cost driver than the session.

### 7.4 Hour of day (exploratory)

| hour UTC | n | E[R] [95% CI] | null R | skill (SE) | MDE80 | cost R [CI] | win % | stop pips | stop/spread |
|---|---|---|---|---|---|---|---|---|---|
| 9 | 498 | -0.162 [-0.278, -0.046] | -0.077 | -0.086 (0.052) | 0.144 | 0.095 [+0.092, +0.099] | 41.0 | 10.0 | 19.0 |
| 10 | 359 | -0.028 [-0.185, +0.125] | -0.124 | +0.096 (0.061) | 0.171 | 0.100 [+0.096, +0.104] | 42.9 | 10.0 | 16.7 |
| 11 | 371 | -0.136 [-0.275, +0.012] | -0.138 | +0.002 (0.062) | 0.174 | 0.094 [+0.090, +0.098] | 40.2 | 10.4 | 18.7 |
| 12 | 372 | -0.154 [-0.298, +0.009] | -0.116 | -0.039 (0.066) | 0.185 | 0.094 [+0.090, +0.098] | 39.0 | 10.8 | 18.7 |
| 13 | 419 | -0.002 [-0.132, +0.141] | -0.012 | +0.010 (0.061) | 0.172 | 0.089 [+0.085, +0.093] | 43.9 | 12.0 | 20.0 |
| 14 | 432 | -0.170 [-0.292, -0.043] | -0.126 | -0.044 (0.055) | 0.153 | 0.090 [+0.086, +0.093] | 42.4 | 11.3 | 21.3 |
| 15 | 352 | -0.081 [-0.222, +0.060] | -0.101 | +0.020 (0.061) | 0.171 | 0.086 [+0.082, +0.090] | 42.6 | 12.4 | 22.1 |
| 16 | 307 | -0.184 [-0.331, -0.041] | -0.112 | -0.073 (0.063) | 0.177 | 0.090 [+0.085, +0.094] | 42.0 | 11.0 | 21.5 |
| 17 | 287 | -0.043 [-0.232, +0.133] | -0.117 | +0.074 (0.073) | 0.204 | 0.098 [+0.093, +0.102] | 45.6 | 10.0 | 18.0 |
| 18 | 226 | -0.128 [-0.292, +0.051] | -0.111 | -0.017 (0.077) | 0.217 | 0.096 [+0.091, +0.102] | 45.6 | 10.0 | 16.7 |
| 19 | 251 | -0.104 [-0.273, +0.063] | -0.152 | +0.048 (0.073) | 0.204 | 0.098 [+0.093, +0.103] | 41.4 | 10.0 | 18.8 |
| 20 | 161 | -0.044 [-0.271, +0.180] | -0.152 | +0.109 (0.086) | 0.240 | 0.099 [+0.093, +0.105] | 42.2 | 10.0 | 20.0 |
| 21 | 138 | +0.111 [-0.149, +0.371] | -0.038 | +0.149 (0.102) | 0.287 | 0.106 [+0.100, +0.112] | 49.3 | 10.0 | 14.3 |

### 7.5 Rollover bucket on its own, and the one pattern worth recording

Decisions at 20:00 to 21:59 UTC (n = 299): E[R] +0.027 [-0.141, +0.199], null -0.099, **skill +0.127 (SE 0.068, 95 % CI [-0.000, +0.262])**; against -0.110 for 09:00 to 19:59 (difference +0.137 [-0.043, +0.313]). In the run without a flatten the Monday-to-Thursday 20:00-and-later entries give skill +0.129 (SE 0.069, n = 291, largely the same trades). Late-session (18:00 to 21:59) skill is positive in EURUSD +0.167, AUDUSD +0.147, GBPUSD +0.094, USDJPY +0.072, but not USDCAD -0.135 or XAUUSD -0.004. It is exploratory, one of 87 exploratory buckets; section 10 gives the tally and why it cannot be confirmed.

## 8. Remaining tables (exploratory; numbers and tally in section 10)

### 8.1 Weekday x session
| weekday session | n | E[R] [95% CI] | null R | skill (SE) | MDE80 | cost R [CI] | win % | stop pips | stop/spread |
|---|---|---|---|---|---|---|---|---|---|
| Mon London | 239 | -0.243 [-0.417, -0.077] | -0.165 | -0.078 (0.081) | 0.227 | 0.100 [+0.095, +0.105] | 39.7 | 10.0 | 17.4 |
| Mon NewYork | 438 | -0.060 [-0.198, +0.095] | -0.056 | -0.004 (0.061) | 0.169 | 0.092 [+0.087, +0.096] | 42.9 | 10.5 | 20.0 |
| Mon Late | 134 | -0.111 [-0.355, +0.152] | -0.158 | +0.047 (0.095) | 0.267 | 0.098 [+0.092, +0.105] | 39.6 | 10.0 | 17.0 |
| Tue London | 236 | -0.087 [-0.275, +0.110] | -0.048 | -0.039 (0.085) | 0.237 | 0.094 [+0.088, +0.099] | 43.6 | 10.1 | 19.8 |
| Tue NewYork | 434 | -0.087 [-0.233, +0.054] | -0.071 | -0.016 (0.061) | 0.171 | 0.091 [+0.087, +0.095] | 43.3 | 10.6 | 20.8 |
| Tue Late | 157 | -0.191 [-0.410, +0.036] | -0.095 | -0.096 (0.101) | 0.284 | 0.101 [+0.094, +0.108] | 39.5 | 10.0 | 17.6 |
| Wed London | 273 | -0.149 [-0.324, +0.045] | -0.118 | -0.032 (0.082) | 0.229 | 0.094 [+0.089, +0.099] | 37.7 | 10.0 | 17.7 |
| Wed NewYork | 418 | -0.134 [-0.265, +0.005] | -0.102 | -0.032 (0.062) | 0.174 | 0.092 [+0.088, +0.097] | 40.9 | 11.0 | 18.3 |
| Wed Late | 184 | -0.021 [-0.241, +0.215] | -0.037 | +0.016 (0.095) | 0.265 | 0.098 [+0.091, +0.104] | 46.7 | 10.0 | 17.3 |
| Thu London | 271 | -0.019 [-0.187, +0.148] | -0.124 | +0.105 (0.069) | 0.193 | 0.099 [+0.094, +0.104] | 44.6 | 10.0 | 17.6 |
| Thu NewYork | 456 | -0.139 [-0.284, +0.006] | -0.111 | -0.028 (0.058) | 0.162 | 0.089 [+0.085, +0.093] | 42.8 | 12.1 | 20.7 |
| Thu Late | 187 | +0.059 [-0.154, +0.274] | -0.206 | +0.266 (0.090) | 0.252 | 0.099 [+0.092, +0.105] | 46.0 | 10.0 | 16.7 |
| Fri London | 209 | -0.081 [-0.266, +0.125] | -0.087 | +0.006 (0.088) | 0.246 | 0.094 [+0.089, +0.100] | 40.7 | 10.0 | 19.9 |
| Fri NewYork | 423 | -0.108 [-0.245, +0.036] | -0.138 | +0.030 (0.059) | 0.166 | 0.089 [+0.085, +0.093] | 42.6 | 11.6 | 20.0 |
| Fri Late | 96 | -0.104 [-0.266, +0.073] | -0.128 | +0.024 (0.065) | 0.183 | 0.100 [+0.092, +0.109] | 45.8 | 10.0 | 16.8 |

### 8.2 Month of year
| month | n | E[R] [95% CI] | null R | skill (SE) | MDE80 | cost R [CI] | win % | stop pips | stop/spread |
|---|---|---|---|---|---|---|---|---|---|
| 01 | 380 | -0.022 [-0.161, +0.122] | -0.074 | +0.051 (0.058) | 0.163 | 0.087 [+0.081, +0.092] | 43.7 | 11.5 | 24.7 |
| 02 | 373 | -0.099 [-0.246, +0.052] | -0.046 | -0.054 (0.068) | 0.190 | 0.090 [+0.085, +0.095] | 41.8 | 10.0 | 21.0 |
| 03 | 416 | -0.084 [-0.208, +0.054] | -0.069 | -0.015 (0.059) | 0.166 | 0.087 [+0.080, +0.093] | 45.0 | 10.7 | 23.3 |
| 04 | 382 | -0.034 [-0.175, +0.116] | -0.094 | +0.060 (0.062) | 0.172 | 0.088 [+0.083, +0.093] | 45.3 | 10.0 | 26.0 |
| 05 | 416 | -0.102 [-0.237, +0.035] | -0.147 | +0.045 (0.059) | 0.167 | 0.096 [+0.090, +0.101] | 44.0 | 10.0 | 18.0 |
| 06 | 313 | -0.117 [-0.317, +0.088] | -0.157 | +0.040 (0.082) | 0.230 | 0.094 [+0.089, +0.099] | 41.2 | 10.6 | 19.9 |
| 07 | 307 | -0.073 [-0.266, +0.125] | -0.115 | +0.042 (0.076) | 0.213 | 0.098 [+0.093, +0.104] | 43.3 | 10.0 | 18.9 |
| 08 | 308 | -0.105 [-0.290, +0.092] | -0.098 | -0.007 (0.083) | 0.232 | 0.106 [+0.100, +0.111] | 42.2 | 10.0 | 14.8 |
| 09 | 348 | -0.168 [-0.314, -0.006] | -0.138 | -0.030 (0.067) | 0.189 | 0.098 [+0.092, +0.103] | 41.4 | 10.0 | 16.7 |
| 10 | 274 | -0.060 [-0.215, +0.111] | -0.034 | -0.027 (0.081) | 0.226 | 0.095 [+0.090, +0.100] | 42.0 | 10.4 | 18.5 |
| 11 | 350 | -0.264 [-0.379, -0.143] | -0.116 | -0.148 (0.064) | 0.180 | 0.098 [+0.093, +0.103] | 37.7 | 10.6 | 16.0 |
| 12 | 306 | -0.075 [-0.234, +0.084] | -0.161 | +0.086 (0.063) | 0.177 | 0.095 [+0.090, +0.102] | 40.5 | 10.0 | 19.8 |

### 8.3 Year
| year | n | E[R] [95% CI] | null R | skill (SE) | MDE80 | cost R [CI] | win % | stop pips | stop/spread |
|---|---|---|---|---|---|---|---|---|---|
| 2014 | 535 | -0.008 [-0.120, +0.109] | -0.050 | +0.043 (0.053) | 0.148 | 0.081 [+0.077, +0.085] | 48.0 | 10.0 | 37.8 |
| 2015 | 536 | -0.119 [-0.238, +0.011] | -0.083 | -0.036 (0.059) | 0.166 | 0.107 [+0.103, +0.110] | 40.7 | 11.5 | 14.7 |
| 2016 | 624 | -0.103 [-0.228, +0.017] | -0.134 | +0.031 (0.052) | 0.144 | 0.110 [+0.107, +0.113] | 42.0 | 12.3 | 14.3 |
| 2017 | 557 | -0.270 [-0.392, -0.149] | -0.196 | -0.073 (0.049) | 0.138 | 0.113 [+0.110, +0.117] | 39.3 | 10.0 | 14.2 |
| 2018 | 551 | -0.118 [-0.229, -0.001] | -0.103 | -0.015 (0.050) | 0.139 | 0.078 [+0.074, +0.083] | 44.3 | 10.0 | 50.0 |
| 2019 | 429 | -0.176 [-0.303, -0.047] | -0.156 | -0.020 (0.049) | 0.136 | 0.095 [+0.091, +0.099] | 40.3 | 10.0 | 23.0 |
| 2020 | 661 | +0.060 [-0.052, +0.176] | -0.050 | +0.110 (0.054) | 0.151 | 0.075 [+0.071, +0.079] | 44.5 | 11.3 | 50.9 |
| 2021 | 280 | -0.120 [-0.297, +0.059] | -0.052 | -0.068 (0.080) | 0.223 | 0.093 [+0.088, +0.098] | 37.5 | 10.1 | 16.9 |

### 8.4 Pair x session
| pair session | n | E[R] [95% CI] | null R | skill (SE) | MDE80 | cost R [CI] | win % | stop pips | stop/spread |
|---|---|---|---|---|---|---|---|---|---|
| EURUSD London | 255 | -0.125 [-0.289, +0.051] | -0.084 | -0.041 (0.069) | 0.192 | 0.094 [+0.090, +0.098] | 40.0 | 10.0 | 16.7 |
| EURUSD NewYork | 426 | -0.072 [-0.207, +0.067] | -0.022 | -0.049 (0.061) | 0.169 | 0.090 [+0.087, +0.093] | 44.4 | 10.0 | 18.5 |
| EURUSD Late | 165 | +0.102 [-0.106, +0.323] | -0.065 | +0.167 (0.085) | 0.238 | 0.094 [+0.089, +0.100] | 53.9 | 10.0 | 16.4 |
| GBPUSD London | 218 | -0.212 [-0.384, -0.036] | -0.134 | -0.078 (0.078) | 0.218 | 0.080 [+0.075, +0.085] | 40.4 | 14.4 | 22.0 |
| GBPUSD NewYork | 383 | -0.131 [-0.274, +0.014] | -0.122 | -0.009 (0.059) | 0.165 | 0.075 [+0.072, +0.078] | 48.0 | 15.1 | 27.0 |
| GBPUSD Late | 112 | -0.089 [-0.342, +0.181] | -0.182 | +0.094 (0.101) | 0.284 | 0.082 [+0.076, +0.089] | 46.4 | 11.1 | 25.3 |
| USDJPY London | 244 | -0.131 [-0.294, +0.046] | -0.127 | -0.005 (0.074) | 0.209 | 0.107 [+0.102, +0.111] | 43.9 | 10.0 | 13.9 |
| USDJPY NewYork | 403 | -0.074 [-0.199, +0.059] | -0.122 | +0.048 (0.055) | 0.154 | 0.099 [+0.095, +0.103] | 46.2 | 10.0 | 17.1 |
| USDJPY Late | 170 | +0.010 [-0.208, +0.234] | -0.061 | +0.072 (0.093) | 0.261 | 0.109 [+0.104, +0.115] | 47.6 | 10.0 | 12.7 |
| AUDUSD London | 203 | -0.149 [-0.319, +0.040] | -0.132 | -0.017 (0.082) | 0.228 | 0.098 [+0.093, +0.103] | 43.8 | 10.0 | 18.9 |
| AUDUSD NewYork | 349 | -0.084 [-0.227, +0.065] | -0.109 | +0.025 (0.059) | 0.165 | 0.103 [+0.099, +0.107] | 44.1 | 10.0 | 15.0 |
| AUDUSD Late | 118 | +0.055 [-0.200, +0.345] | -0.092 | +0.147 (0.113) | 0.316 | 0.107 [+0.100, +0.113] | 44.9 | 10.0 | 14.3 |
| USDCAD London | 243 | +0.041 [-0.154, +0.245] | -0.039 | +0.081 (0.088) | 0.247 | 0.106 [+0.101, +0.110] | 44.4 | 10.0 | 20.0 |
| USDCAD NewYork | 436 | -0.168 [-0.315, -0.030] | -0.108 | -0.060 (0.059) | 0.165 | 0.097 [+0.093, +0.101] | 39.7 | 10.9 | 25.0 |
| USDCAD Late | 169 | -0.290 [-0.479, -0.090] | -0.155 | -0.135 (0.092) | 0.257 | 0.108 [+0.102, +0.114] | 36.1 | 10.0 | 20.0 |
| XAUUSD London | 65 | -0.168 [-0.580, +0.279] | -0.251 | +0.084 (0.157) | 0.439 | 0.079 [+0.070, +0.089] | 20.0 | 247.0 | 19.5 |
| XAUUSD NewYork | 172 | -0.096 [-0.327, +0.151] | -0.085 | -0.010 (0.093) | 0.262 | 0.067 [+0.061, +0.073] | 20.9 | 300.7 | 20.0 |
| XAUUSD Late | 42 | -0.307 [-0.660, +0.112] | -0.303 | -0.004 (0.175) | 0.489 | 0.065 [+0.055, +0.075] | 16.7 | 314.0 | 22.3 |

### 8.5 London local hour
| London hour | n | E[R] [95% CI] | null R | skill (SE) | MDE80 | cost R [CI] | win % | stop pips | stop/spread |
|---|---|---|---|---|---|---|---|---|---|
| 9 | 222 | -0.214 [-0.385, -0.037] | -0.084 | -0.130 (0.078) | 0.219 | 0.093 [+0.088, +0.098] | 37.4 | 10.6 | 20.2 |
| 10 | 430 | -0.075 [-0.213, +0.063] | -0.058 | -0.017 (0.058) | 0.161 | 0.098 [+0.094, +0.101] | 44.0 | 10.0 | 17.4 |
| 11 | 384 | -0.106 [-0.253, +0.023] | -0.176 | +0.070 (0.059) | 0.164 | 0.097 [+0.093, +0.101] | 41.7 | 10.0 | 17.7 |
| 12 | 326 | -0.177 [-0.327, -0.012] | -0.148 | -0.029 (0.067) | 0.187 | 0.092 [+0.088, +0.096] | 39.3 | 10.4 | 19.5 |
| 13 | 417 | -0.099 [-0.251, +0.068] | -0.060 | -0.038 (0.066) | 0.185 | 0.095 [+0.091, +0.099] | 38.1 | 11.0 | 17.8 |
| 14 | 429 | -0.017 [-0.143, +0.122] | -0.062 | +0.044 (0.056) | 0.157 | 0.087 [+0.083, +0.090] | 46.9 | 11.7 | 23.2 |
| 15 | 403 | -0.130 [-0.275, +0.005] | -0.095 | -0.035 (0.059) | 0.166 | 0.090 [+0.086, +0.093] | 41.9 | 11.4 | 20.1 |
| 16 | 332 | -0.178 [-0.307, -0.035] | -0.119 | -0.059 (0.061) | 0.170 | 0.084 [+0.080, +0.088] | 41.0 | 12.9 | 23.2 |
| 17 | 292 | +0.055 [-0.105, +0.219] | -0.078 | +0.133 (0.066) | 0.186 | 0.095 [+0.091, +0.100] | 47.6 | 10.0 | 18.8 |
| 18 | 269 | -0.278 [-0.458, -0.109] | -0.146 | -0.133 (0.073) | 0.205 | 0.095 [+0.090, +0.100] | 40.9 | 10.0 | 18.0 |
| 19 | 225 | -0.014 [-0.199, +0.184] | -0.082 | +0.068 (0.083) | 0.232 | 0.100 [+0.095, +0.106] | 44.9 | 10.0 | 15.2 |
| 20 | 219 | -0.088 [-0.266, +0.096] | -0.188 | +0.100 (0.073) | 0.206 | 0.097 [+0.092, +0.102] | 43.4 | 10.0 | 20.0 |
| 21 | 158 | -0.050 [-0.273, +0.174] | -0.156 | +0.106 (0.089) | 0.248 | 0.102 [+0.096, +0.107] | 40.5 | 10.0 | 19.3 |
| 22 | 67 | +0.144 [-0.225, +0.510] | +0.006 | +0.138 (0.146) | 0.409 | 0.112 [+0.103, +0.119] | 56.7 | 10.0 | 13.3 |

### 8.6 Gold against the FX pairs (claim C4)
|  | XAUUSD | five FX pairs | XAUUSD minus FX [95% CI] |
|---|---|---|---|
| cost per R, actual trades | 0.070 | 0.096 | -0.026 [-0.031, -0.021] |
| E[R], random-direction null | -0.157 | -0.100 | -0.057 [-0.170, +0.055] |

## 9. Verdict on each pre-registered hypothesis

| Item | Pre-declared | Result | Verdict |
|---|---|---|---|
| S (14 skill tests: 3 sessions, 6 pairs, 5 weekdays) | any bucket with Bonferroni LB > 0 (z = 2.69) | 0 of 14; best LB -0.040 (Thursday); largest z 1.73 | no skill detected (MDE 0.075 to 0.21 per bucket) |
| C1 rollover cost ratio >= 1.3 | ratio >= 1.3 | 1.10 [1.05, 1.15] | direction yes, size no |
| C2 null lower at rollover | difference < 0 | +0.005 [-0.095, +0.110] | not supported |
| C3 flatten raises E[R] (actual and null) | both > 0 | actual +0.013 [-0.002, +0.028]; null +0.009 [+0.002, +0.016] | supported in direction; small |
| C4 gold cost per R lowest; gold null above FX | both | cost -0.026 [-0.031, -0.021] yes; null -0.057 [-0.170, +0.055] no | half supported |
| C5 clustering | ICC > 0, same USD side > 50 %, N_eff clearly < N | ICC +0.047 (null +0.011), 66.7 % [63.7, 69.7], N_eff 3,701 of 4,173 | partly supported |
| C6 spread by hour | cheapest 07 to 17, dearest 21 to 23 | flat 0.8 pips 05 to 18; 21:00 is 1.38 times; 22 to 04 only 1.12 times | supported, small |

## 10. Comparisons made

Confirmatory: 14 skill tests (family S), 6 claims (about 10 estimates). Exploratory buckets with skill estimates: hours 13, weekday x session 15, months 12, years 8, pair x session 18, London-local hours 14, Friday/Monday windows 4 + 3 = 87; plus 4 gap-size rows, 8 pair subsets and the rollover row. Of the 87, **5 have |z| >= 1.96** (Thursday Late +0.266, z 2.95; November -0.148, z -2.29; 2020 +0.110, z 2.03; EURUSD Late +0.167, z 1.96; London hour 17 +0.133, z 2.00), against about 4.4 expected from chance alone at 5 % two-sided. The strongest, Thursday Late, has a Bonferroni-adjusted p of about 0.27 over 87 looks. None is a result. Many exploratory buckets share the same trades (UTC hours, London hours, sessions), so they are not independent looks.

## 11. What we can leverage

### (a) Cost and structure actions (follow from structure; do not depend on signal skill)

| # | Action | Evidence | Size |
|---|---|---|---|
| 1 | Keep the Friday 20:00 flatten; confirm in live that it runs and closes everything | +0.009 to +0.013 R per trade; +57 R over DEV; removes the -12.5 R and -11.2 R weekend-gap tails; maximum drawdown -69.7 % against -72.6 % | small mean, large tail value |
| 2 | Wider stops are the dominant cost lever, not hours or pairs | cost per R is 0.094, about 0.050 from spread over stop plus 0.044 commission, both inversely proportional to the stop; GBPUSD (14.3-pip median stop) costs 0.078, gold 0.070, 10-pip-floor pairs 0.10; the whole range across hours is 0.020 and across pairs 0.033 | consistent with the stop-floor study (+0.073 R null at 30 pips); not re-tested here |
| 3 | No hour or pair exclusion on cost grounds | removing the 21:00 hour (3.3 % of trades, cost +0.009 to 0.013 R higher) is worth under 0.001 R per trade; the existing 10 %-of-stop spread filter already removes 18.5 % of candidates and is densest at 09 and 21 UTC | negligible |
| 4 | Do not pick pairs or a pair count to raise expectancy | eight subsets all -0.081 to -0.120 with SE 0.025; fewer pairs only means fewer trades (27.9 against 46.9 per month) | none; a volume decision only |
| 5 | Measure the live spread regime before trusting any cost number | cost per R by year 0.075 (2020) to 0.113 (2017); stop-to-spread 14 to 51 | up to 0.04 R, outside our control; feeds the `cost_record` track |
| 6 | Sunday/Monday-open exposure is already closed in live code | zero Saturday decisions, first live entry Monday 09:00 | none to gain |
| 7 | Gold breakeven buffer (0.0002 dollars) earns nothing | gold BE exits -0.06 R against +0.04 to +0.13 for FX | about 10 R in 7.4 years; a live-code change needing its own protocol, not recommended on this evidence |

### (b) Anything that looks like signal (hypotheses only)

* Decisions at 20:00 to 21:59 UTC: skill +0.127 [-0.000, +0.262] (n = 299). One of 87 looks; the seen-window shows the opposite sign. Untestable at useful power (section 12, F2).
* Thursday (skill +0.072) and Thursday Late (+0.266): weak, exploratory, multiplicity-adjusted p 0.27.
* Weekend gap fills (small gaps close within hours): a market regularity, not a tested strategy; costs and first-hour spreads untested (section 12, F3).
* Everything else here is consistent with zero skill, as stage 0 found.

## 12. Recommended pre-registered follow-up experiments

Rules are exact so they can be copied into a pre-registration; none has been run. All use the repository's tooling, DEV for development and at most one FINAL run, and the stage-0 estimator conventions (day-cluster bootstrap, 40+ null seeds, reflect geometry, sentinel missing, clean conventions).

**F1. Friday exit timing as tail control (cost hygiene, uses the simulator options that already exist).** Variants fixed now: control = flatten 20:00 UTC; A = flatten 17:00 UTC; B = no entry from Friday 15:00 UTC with flatten 20:00. Identical candidates, `fixed_risk_pct = 0.25`. Primary outcome: paired mean R difference on DEV (run on the null, and the real signals reported alongside) and the number of trades closed at a loss of 3 R or worse. Power: the null paired SE is 0.0036 R at about 4,400 trades, so a difference of 0.010 R is detectable at about 80 % power; the real-signal SE is 0.0076, so only about 0.021 R. A variant is kept only if the null difference lower bound is above 0 and the 3R+ loss count does not rise; then one FINAL run, alpha 0.025. Expected gain is small (the whole flatten is +0.009 to +0.013 R), so this is a choice about tails, not about expectancy.

**F2. Late-hours skill (hypothesis from section 7.5).** Rule: decision time 20:00 to 21:59 UTC, Monday to Thursday, live rules unchanged; statistic = actual mean R minus null mean R (40 seeds, same trades). Sample and power: FINAL holds about 2,000 trades of which about 7 % fall in this window (about 145 to 190); SE about 0.019 x sqrt(4,173 / 150) = 0.10, so the minimum detectable skill is 0.28 R (150 trades; 0.24 R at 190) and the power to detect the DEV estimate of 0.127 is about 25 %. Needed for 80 % power at 0.127: about 730 late trades, roughly 10,000 trades in total, about 18 years at this strategy's volume. **Recommendation: do not spend a FINAL run on it.** Treat it as unfalsifiable at this sample size and let the forward demo log the bucket (about 40 trades a year).

**F3. Weekend gap-fade as a stand-alone signal (hypothesis from section 5.6).** Universe: five FX pairs, every weekly reopen (about 1,900 on DEV, 677 with a gap above 10 pips). Rule: at the close of the 12th M5 bar after the reopen (60 minutes), if the gap is at least 10 pips and price has not touched the Friday close, enter at market toward the Friday close; target = Friday close; stop = the same distance beyond the entry (1:1), at least 10 pips; time stop 24 hours; costs, sizing and commission as the simulator. Comparator: the same trades with random direction reflected about entry. Power: per-event sd of R about 1.0, so 677 events give SE 0.04 and a minimum detectable edge of about 0.11 R; a 60-minute fill rate of 22 % at the target is not an edge by itself because the stop is hit first about as often. Success: lower bound of (actual minus null) above 0 on DEV, then one FINAL run. Treat the prior as low: the spread is 1.1 to 2 times normal in that hour and the first-bar quirk of section 5.6 makes DEV optimistic about stops.

**F4. Cost record (data collection, no inference).** Log the live bid/ask spread per pair per M5 for the first demo months and compare cost per R by hour and weekday with section 7.3. Purpose: replace the imputed 2014 to 2021 spread regime (cost 0.075 to 0.113 by year) with the live one before any further cost-based decision.

## 13. Data caveats

1. Recorded spreads have 0.1-pip resolution, so the hourly medians differ by one tick; real hour effects smaller than that are invisible.
2. Spreads: the 50-point placeholder is treated as missing and zero spreads are imputed with the full-series same-hour median (a cost estimate that uses future bars; it affects costs, not signals). Gold has long zero-spread stretches (all of 2021 up to the end of DEV, 30 to 40 % elsewhere); gold's mean entry spread in the trades ($0.14) is below its clean-bar medians ($0.24 to $0.32).
3. Flagged DEV years: 2016 and 2017 (weekly-open check), GBPUSD 2019 (1.5-month hole, spikes). The clean-subset columns in 6.1, 7.2 change no conclusion; 2021 is five months.
4. Reopen bars: the first bar after a weekly reopen carries the opening-tick spread, applied to the whole bar, which inflates gap-through losses for shorts; take-profits at a gap are paid at the gapped price (`tp_gap_fill`), which is generous. The two do not cancel; the weekend loss totals are approximate.
5. Replay versus live: Sunday is not blocked in the replay; the flatten is assumed to succeed; no news filter (the -12.7 R GBPUSD trade of 2017-06-08 is a news gap); no swaps (the daily 22:00 UTC rollover and the Wednesday triple swap are not charged); stop slippage 0; limit orders fill on touch. The primary configuration uses the "clean" fill-bar rule, not the live-default pessimistic one.
6. Buckets are by decision time; actual and null trade sets differ slightly through the stateful gates, and the null mean is averaged over seeds (its sampling error is inside the bootstrap).
7. The bootstrap resamples decision days. Trades that last days, and the compounding of the account, are ignored; the ICC is 0.047, so the day clusters carry little; dependence across days (multi-day holds) is not captured. Exploratory buckets reuse the same trades.
8. R is net P/L over the dollar risk at sizing; with 0.25 % risk per trade the -70 % drawdowns are compounded and not an account-level risk statement.
9. The correlation analysis uses sparse daily series; the faithful sweep never fires, so the checklist needs all four other checks.
10. Candidate file reused from the stage-0 sentinel run (`research/output/sens_sentinel_R2/base_candidates.parquet`, produced at git HEAD bd9c09a; `signals.py` and `sim.py` are unchanged since). Run L reproduces that run exactly (4,173 trades, same R series, same funnel).
11. The seen-window table uses a different convention set and counts differently from `RESULTS.md` (trades closing on the first bar after a reopen: 16 here by exact bar match against 26 there; decisions at or after 20:00: 63 by decision time against 72 by fill time).
12. FINAL (2021-06 to 2025-04) was not read: `assert_dev_window` and `assert_no_final_rows` refuse it, and are tested.

## 14. Reproduction and verification

* Code: `research/analysis_sessions.py` (guards, features, bootstrap, weekend and clustering helpers, simulation by scenario with a funnel counted by hour, signal-stage gate diagnostic), `research/analysis_tables.py` (all tables), tests `research/tests/test_analysis_sessions.py`. Commands: `python -m research.analysis_sessions sim|gates|tables --tag analyst_main` (about 25 minutes for `sim` on 8 workers, 30 minutes for `gates`, 2 minutes for `tables`). Outputs in `research/output/analyst_main/` (`tables.md`, `tables.json`, per-scenario trade parquet files, run CSVs, funnels).
* Seeds: null seeds 0 to 39 (0 to 19 for the six leave-one-out sets and the three-pair set), bootstrap seed 12345, 2,000 resamples. No wall-clock or unseeded randomness.
* Hand checks against the raw trade list (`research/output/sens_sentinel_R2/actual_trades.csv`): n and mean R re-computed with plain pandas agree with the tables to four decimals (all 4,173 trades -0.1001; Late 776 -0.0604; London 1,228 -0.1151; EURUSD 846 -0.0541; Thursday 914 -0.0630; 20:00 to 21:59 299 +0.0275; 09:00 to 19:59 -0.1099; Sunday decisions 18); cost per R of three trades recomputed by hand from entry, stop, spread points and commission (USDCAD spread 0.0100 + commission 0.0532 = 0.0632 R).
* Prereg integrity: sha256 of the file at the time of the first run (it contained only sections 1 to 3, 8123 bytes) is in `research/output/analyst_main/prereg_sha256.txt`; verify with `head -c 8123 research/reports/session_pair_weekend_analysis.md | sha256sum`; sections 1 to 3 were not edited afterwards.
* Tests: `research/tests/test_analysis_sessions.py` 27 passed (guards, features, bootstrap, weekend closures, gap fills, spread hours, clustering, funnel-by-hour totals, scenario set). Full suite run once at the end: 625 passed (about 3.4 minutes). Temporary pytest directory removed.
