# Quant review: where the bot loses, structural ideas, and a robust risk profile (2026-10-31)

Reviewer role: quant trader / risk manager. DEV only (2014-01-01 to 2021-06-01); FINAL (2021-06 to 2025-04) was not read, run or looked at, no `--force`, no holdout command was run.
Pre-registrations (each written before its runs): `quant_anatomy_prereg.md`, `quant_methods_prereg.md`, `quant_risk_prereg.md`. Code: `research/quant_loss.py`, `quant_methods.py`,
`quant_risk.py`; tests `research/tests/test_quant_{loss,methods,risk}.py`; outputs `research/output/quant_*`. No live file, `sim.py`, `signals.py`, `progress.md` or `RESULTS.md` was edited.

## 0. Summary

* **Where the loss comes from** (3,970 DEV trades, live settings, -0.131 R per trade, -73 % over 89 months, equity drawdown -74.7 %): entry spread 0.047 R plus commission 0.044 R = **0.091 R of cost (70 %)**; the remaining
  **-0.040 R is "pre-cost"** (direction plus structure plus gap and convention effects; the zero-skill reflect null has the same level). 56 % of trades are full stops (-1.05 R), 25 % breakeven (+0.10), 14 % targets (+2.95), 5 % Friday flatten (+0.40).
  The stateless odds of reaching +1R before the stop are 46.8 % (50 % without costs), +3R 23.4 % (25 %). Stop-outs are clean: only 6 % of stopped trades had touched +1R first, 32 % had touched +0.5R. Losing streaks are what a 59 % loss rate implies (max 18 against an iid 14.6, p = 0.11): no clustering beyond chance.
* **What actually protects R (gate counterfactuals, null, 16 seeds):** the **relative spread cap (spread <= 10 % of the stop) is worth about +843 R over 7.4 years on the null: the 1,439 extra trades its removal would admit lose -0.59 R each (section 1.7)**; Friday flatten +52 R (+0.011 R per trade); correlation filter +7 R; max-open +4 R; the 3 % daily gate, the +5 % monthly pause, the cooldown and the stale-snapshot quirk are worth about 0. The regime filter blocks trades at the average loss (-0.127 R vs -0.139): it reduces trade count, it does not select. News filtering cannot be measured (no historical calendar).
* **Best structural lever found (hypothesis, DEV only): an ATR-scaled minimum stop** (stop >= k x ATR(H1,14)): on the random-direction null k = 1.0 / 1.5 / 2.0 cut the drag by +0.040 / +0.050 / +0.065 R per trade (SE 0.012 to 0.016, 7 of 8 DEV years positive), with the trade count at 103 % / 91 % / 80 % of control and R per month going from -6.4 to -4.7 / -3.8 / -2.7. Real signals move the same way (+0.020 / +0.023 / +0.066 R) but are not separable from noise (SE 0.017 to 0.026). It makes the loss smaller; it does not make it positive. The gain does not flatten at k = 2.0: k = 2.5 / 3.0 give +0.084 / +0.100 R but only 69 % / 61 % of the trades (R per month -1.7 / -1.1), i.e. the strategy turns into a slow, day-long-hold system. With the 50-point placeholder spread treated as missing the ATR gains are slightly smaller but unchanged in sign and size (+0.035 / +0.045 / +0.055).
* **Risk:** on a strategy with a mean of -0.13 R, risk management only sets the speed of loss. At the live 0.25 % risk the 10 % static loss limit is breached within 6 / 12 / 24 months in **45 % / 86 % / 98 %** of simulated paths (median 5.8 months), the 5 % daily limit never in this resample (0.00 %; that is a property of resampling 1,860 DEV days, whose worst day was -4.58 %, not a bound on the true risk: one -12.7 R gap at 0.25 % risk costs 3.2 %), and the expected equity drawdown at 12 months is -10.9 % median, -14.1 % (95 %), -15.9 % (99 %). The binding FTMO limit is the static 10 %, not the daily one.
  Halving and laddering risk (profile P2) moves the 12-month breach probability to 0.0 % and slows the loss from -1.45 % to about -0.5 % per month; it cannot make the expectancy positive (pass probability 0.0 to 0.7 % in every profile at the simulated drag).
* **Recommended forward-demo profile (fixed rule, section 3.4): P2 = 0.15 % risk per trade, max 2 open positions (pending orders counted), daily gate 2 %, loss ladder (x0.5 from -3 %, x0.25 from -5 %), halt at -8 %, day stop at -1.5 %, pause after 6 consecutive losses.** P3 (0.075 %) is the more conservative alternative with the same evidence rate.

Top three recommendations: (1) run the forward demo on P2 (a live-code spec is in 3.5; no live change was made); (2) take the ATR-floor idea (k about 1.0 to 1.5) to a pre-registered locked-holdout decision by the owner, because it is the only cost lever that is large, monotone, year-stable and does not reduce the trade count; (3) keep the spread-relative gate exactly as it is and fix the placeholder-spread data problem and the XAUUSD geometry before anything else, because those are where unforced losses sit.

## 1. Loss anatomy (DEV, live settings, real signals, one deterministic run; reproduces stage 0: 3,970 trades, -0.1308 R)

Sources: `research/output/quant_anatomy/` (`anatomy.txt`, `trades.csv`, `equity.parquet`, `excursions.csv`, `summary.json`). Descriptive; shares have an SE of about sqrt(p(1-p)/n) and are read as shapes.

### 1.1 Cost per R
| | n | mean R | pre-cost R | entry spread / stop | commission / risk | cost total |
|---|---|---|---|---|---|---|
| all | 3,970 | -0.131 | -0.040 | 0.047 | 0.044 | 0.091 |
| without XAUUSD | 3,667 | -0.124 | -0.032 | 0.047 | 0.046 | 0.092 |
| EURUSD | 742 | -0.053 | +0.035 | 0.046 | 0.043 | 0.089 |
| AUDUSD | 624 | -0.101 | -0.005 | 0.051 | 0.045 | 0.096 |
| USDJPY | 768 | -0.125 | -0.024 | 0.052 | 0.048 | 0.100 |
| USDCAD | 852 | -0.153 | -0.054 | 0.046 | 0.053 | 0.099 |
| GBPUSD | 681 | -0.184 | -0.109 | 0.039 | 0.036 | 0.075 |
| XAUUSD | 303 | -0.213 | -0.145 | 0.048 | 0.021 | 0.068 |

Pre-cost R = gross P/L over risk plus the spread proxy (entry spread over the stop distance, a proxy: the recorded spread at the decision bar). Cost per R is almost the same for every pair (0.07 to 0.10 R); the pairs differ in pre-cost R, which is where direction and structure sit (descriptive; per-pair SE 0.04 to 0.09; pair studies belong to the other agent).

### 1.2 Exit mix and where the R goes
| exit | trades | share | mean R | sum R |
|---|---|---|---|---|
| SL (full stop) | 2,226 | 56.1 % | -1.052 | -2,341 |
| BE (breakeven stop) | 991 | 25.0 % | +0.099 | +98 |
| TP (3R) | 557 | 14.0 % | +2.953 | +1,645 |
| FLAT (Friday 20:00) | 196 | 4.9 % | +0.402 | +79 |
| total | 3,970 | | -0.131 | -519 |

Win rate (net P/L > 0) 41.0 %; a full stop costs 1.052 R, not 1.000, because commission and spread are added to the stop-out. Fill-bar stop-outs (STOP entries that exit on the bar they fill) are 3.6 % of trades (11 % for XAUUSD, 7.5 % in the 2020-05 to 2021-05 placeholder-spread era against 3.1 % before). LIMIT entries average -0.101 R, STOP entries -0.165 R (n = 2,106 / 1,864; SE about 0.05 each, descriptive).
By open year, mean R: 2014 -0.038, 2015 -0.145, 2016 -0.077, 2017 -0.283, 2018 -0.115, 2019 -0.195, 2020 -0.068, 2021 (5 months) -0.184; no year is positive.

### 1.3 Stop-out rate by stop size, spread-to-stop and stop in ATR
| bucket | n | SL % | BE % | TP % | mean R |
|---|---|---|---|---|---|
| stop <= 10 pips (the floor) | 1,930 | 55.0 | 26.3 | 14.9 | -0.090 |
| 10 to 15 pips | 827 | 58.5 | 24.4 | 13.1 | -0.210 |
| 15 to 30 pips | 780 | 54.3 | 25.5 | 13.0 | -0.129 |
| > 30 pips | 130 | 48.5 | 26.2 | 11.5 | -0.049 |
| spread/stop <= 3 % | 1,547 | 53.6 | 25.7 | 14.3 | -0.084 |
| spread/stop 3 to 8.5 % | 1,755 | 58.8 | 24.4 | 12.8 | -0.207 |
| spread/stop 8.5 to 10 % | 666 | 54.5 | 24.8 | 16.8 | -0.038 |
| stop < 0.25 ATR(H1) | 57 | 66.7 | 14.0 | 19.3 | -0.163 (29.8 % stopped on the fill bar) |
| stop 0.25 to 0.55 ATR | 914 | 55.9 | 24.0 | 15.9 | -0.079 |
| stop 0.55 to 1.0 ATR | 2,032 | 55.5 | 26.1 | 13.6 | -0.142 |
| stop > 1.0 ATR | 966 | 56.7 | 24.0 | 12.9 | -0.155 |

Reading: the full-stop share sits in a band of 48 to 60 % whatever the stop width (a martingale property); the mean-R differences between buckets (SE 0.04 to 0.08) are not distinguishable from noise except for the extreme bucket, stops tighter than a quarter of an H1 ATR, where 30 % of trades are stopped on their fill bar. What the stop width changes is the cost per R (section 2) and the holding time, not the hit rate. Gold is excluded from the pip buckets (its "pip" is 0.01 dollar).

### 1.4 MAE / MFE (units of the initial risk distance; bars before the exit bar, bid for BUY, ask for SELL)
| group | n | reached +0.5R % | +1R % | +2R % | median MAE |
|---|---|---|---|---|---|
| all | 3,970 | 59.6 | 43.4 | 19.9 | 1.01 R |
| SL exits | 2,226 | **32.2** | **6.1** | 0.2 | 1.14 R |
| BE exits | 991 | 99.9 | 99.9 | 28.0 | 0.42 R |
| TP exits | 557 | 96.9 | 93.9 | 86.2 | 0.38 R |
| FLAT exits | 196 | 61.7 | 37.2 | 14.8 | 0.47 R |

Stateless first-touch odds (a plain bracket from the fill, 5-day horizon, ties to the stop; breakeven and flatten ignored): +0.5R before -1R 62.5 % (zero-cost driftless 66.7 %), +1R 46.8 % (50 %), +2R 30.5 % (33.3 %), +3R 23.4 % (25 %). The shortfall of 1.6 to 4.2 points is the cost-and-structure drag in probability terms: 3R at 23.4 % is -0.065 R before commission, then -0.044 R of commission. Breakeven exits reach +2R 28 % of the time, so BE cuts some winners short, but the null shows BE removal changes the mean by about zero on DEV (decomposition, `RESULTS.md`). Of 2,226 full stops, 717 (32 %) had touched +0.5R and 135 (6 %) +1R (192 including the exit bar) without the close-based breakeven trigger firing.

### 1.5 Losing streaks (loss = net P/L < 0; close order)
Loss rate 59.0 %; observed longest streak **18** against an iid mean of 14.6 (5th to 95th percentile 11 to 19; P(iid >= 18) = 0.11). Streaks of at least 5 / 8 / 10 / 12 / 15: observed 114 / 28 / 11 / 3 / 1 against iid 116 / 24 / 8.4 / 2.9 / 0.6. The losing runs are consistent with independent trades. Consequence for circuit breakers: a "pause after N losses" rule cannot change expectancy, and with a 59 % loss rate a streak of 8 occurs about 28 times in 89 months (every three months), so N must be at least 6 to 8 to avoid pausing on ordinary variance.

### 1.6 Drawdown, exposure and tails
* Equity drawdown -74.7 % (balance -74.6 %), final equity 26.9 % of start; the account is below a previous peak 99.8 % of the time and no new high is made after July 2014. Months: 76 % negative, mean -1.45 %, 5th percentile -5.3 %, worst -9.2 %; weeks: worst -5.2 %; worst Prague day (equity low) **-4.58 %**, which is within 0.4 points of FTMO's 5 % although the bot's own gate is 3 % (the gate blocks new entries only).
* The deepest drawdown episode is the entire window (2014-07 to 2021-04); by exit its trades are SL -2,185 R, TP +1,485 R, BE +88 R, FLAT +69 R; by pair GBPUSD -120 R, USDCAD -145 R, USDJPY -108 R, XAUUSD -68 R, AUDUSD -67 R, EURUSD -36 R.
* Positions are open only 18 % of the time; 2 or more 2.96 %, 3 or more 0.19 %. **The cap of 3 was exceeded once: 4 positions open for 0.0014 % of the time (1.0 % risk)**, the live stale-snapshot quirk. Total exposure is low, so the max-open and correlation gates rarely bind.
* Ten worst trades sum to about -32 R (6 % of the total loss): a GBPUSD BUY breakeven stop gapped through at the 21:00 rollover for **-12.7 R** (2017-06-08, UK election night; 3.2 % of the account at the time), then -2.8, -2.5 (XAUUSD fill-bar stop), -2.3 ... mostly STOP-entry fill-bar stops and rollover-hour bars. The tail is modest on DEV with the Friday flatten on; the 21:00 rollover is the remaining gap window (not covered by a flatten).

### 1.7 Gates: what each saves or costs (`quant_g1`; null = reflect, 16 seeds paired; real = one run; DEV; total R over 89 months)
| gate removed | null trades | null total R | change in total R (paired SE) | R per extra trade | real total R change |
|---|---|---|---|---|---|
| none (base) | 4,128 | -573 | | | |
| relative spread cap (10 % of stop) | +1,439 | -1,417 | **-843 (15)** | -0.59 | -730 |
| all spread filtering (abs and rel) | +1,834 | -1,556 | **-983 (16)** | -0.54 | -890 |
| Friday flatten | +30 | -625 | **-52 (5)** | -1.75 | -67 |
| regime filter (candidates rebuilt) | +585 | -647 | -74 (31, unpaired) | -0.13 | -80 |
| correlation filter | +62 | -581 | -7 (4) | -0.12 | -18 |
| max-open (3) | +4 | -577 | -4 (1) | -0.96 | +3 |
| 3 % daily gate | 0 | -573 | 0 | | 0 |
| +5 % monthly pause | +11 | -574 | -0.2 (1.6) | | -2 |
| same-direction cooldown | +15 | -572 | +1 (3) | | -6 |
| stale snapshot off (in-cycle re-read) | -7 | -573 | +0.2 (1.8) | | +8 |

In a system where every trade has a negative expectation a gate "saves" total R merely by blocking trades; the informative number is the mean R of the extra trades its removal admits. Only the spread cap and the Friday flatten admit trades that are much worse than average (-0.59 R and -1.75 R against -0.139 R): the spread cap blocks trades that are expensive and prone to fill-bar stops (809 of the 843 R is in 2014-2020-04, so it is a genuine cost filter and not a placeholder-era artefact); the Friday flatten removes the weekend-gap losses. Correlation, max-open, daily, monthly, cooldown and the stale-snapshot quirk are all immaterial at the live exposure of 0.25 % and at most 3 positions. The regime filter cuts trades at about the average loss. News blocking was not simulated.

## 2. Structural and method ideas that do not predict direction (ranked)

All measured on the random-direction `reflect` null (40 seeds, same flips per arm, base = live settings, default conventions, spread placeholder as is) and, as a second reading, on the real DEV signals. Method: paired week-block bootstrap (2,000 resamples). Effect labels follow the pre-registered rule: REDUCES needs D_null >= +0.02 R and D_null - 2.61 SE > 0 (Bonferroni over K = 11); minimum detectable null change 2.8 SE = 0.03 to 0.045 R; the real-signal SE is 0.017 to 0.026 so **the real-signal read is not powered for effects of this size** and is shown for consistency only.
Why the null can measure these: on a driftless price every stop, target, breakeven or time-exit rule has zero expectation before costs, so only cost per R, simulator conventions and trade count can move. The negative control M6 (a 24 h time stop) came out at -0.002 +/- 0.002 R, as it should.

### 2.1 Measured arms (`quant_m1`)
| arm | rule (grid fixed in advance) | null R/trade | D_null (SE) | yrs same sign | no-gold / clean D | null trades vs B0 | null R/month | real R/trade (D_act, SE) | verdict |
|---|---|---|---|---|---|---|---|---|---|
| B0 | live settings | -0.1386 | | | | 4,133 | -6.43 | -0.1308 | control |
| **M1_k20** | stop >= 2.0 x ATR(H1,14) | -0.0739 | **+0.0647 (0.016)** | 7/8 | +0.084 / +0.062 | 0.80 | **-2.74** | -0.0649 (+0.066, 0.026) | REDUCES; real not separable |
| **M1_k15** | stop >= 1.5 x ATR | -0.0888 | **+0.0498 (0.014)** | 7/8 | +0.059 / +0.049 | 0.91 | -3.77 | -0.1077 (+0.023, 0.022) | REDUCES; real not separable |
| **M1_k10** | stop >= 1.0 x ATR | -0.0985 | **+0.0401 (0.012)** | 7/8 | +0.045 / +0.039 | **1.03** | -4.70 | -0.1105 (+0.020, 0.017) | REDUCES; real not separable |
| M2_c7 | skip if spread > 7 % of stop | -0.1174 | +0.0212 (0.008) | 7/8 | +0.020 / +0.023 | 0.71 | -3.90 | -0.1235 (+0.007, 0.016) | NONE at the bound (z 2.55 < 2.61) |
| M2_c5 | > 5 % | -0.1128 | +0.0259 (0.011) | 5/8 | +0.026 / +0.027 | 0.57 | -2.97 | -0.1046 (+0.026, 0.019) | NONE |
| M2_c3 | > 3 % | -0.1215 | +0.0171 (0.016) | 4/8 | +0.012 / +0.021 | 0.42 | -2.39 | -0.0952 (+0.036, 0.024) | NONE |
| M3_h20 | no entries from 20:00 UTC | -0.1374 | +0.0012 (0.004) | 5/8 | 0.000 / 0.001 | 0.92 | -5.85 | -0.1427 (-0.012, 0.008) | NONE |
| M3_h21 | from 21:00 UTC | -0.1390 | -0.0004 (0.003) | 4/8 | -0.002 / -0.001 | 0.95 | -6.16 | -0.1384 (-0.008, 0.006) | NONE |
| M5_rr2 | target 2R | -0.1352 | +0.0034 (0.007) | 5/8 | +0.008 / +0.003 | 1.03 | -6.44 | -0.1409 (-0.010, 0.010) | NONE |
| M5_rr4 | target 4R | -0.1450 | -0.0063 (0.008) | 5/8 | -0.004 / -0.006 | 0.98 | -6.62 | -0.1349 (-0.004, 0.010) | NONE |
| M4_o2 (exposure) | max 2 open | -0.1379 | +0.0008 (0.002) | | | 0.98 | -6.26 | -0.1376 | no per-trade effect |
| M4_o1 (exposure) | max 1 open | -0.1339 | +0.0048 (0.006) | | | 0.80 | **-4.96** | -0.1255 | exposure reducer: -23 % R/month for -20 % trades |
| M6_t24 (negative control) | close after 24 h | -0.1402 | -0.0016 (0.002) | | | 1.00 | -6.54 | -0.1344 | 0 as expected |

Mechanism of M1 (null, pooled): commission 0.044 -> 0.030 / 0.021 / 0.016 R, entry spread over stop 0.047 -> 0.039 / 0.031 / 0.025, fill-bar stop-outs 3.8 % -> 1.7 / 0.9 / 0.6 %, share of trades pinned at the 10-pip floor 51 % -> 7 / 1 / 0 %, average hold 3.6 h -> 5.8 / 10.1 / 15.4 h, Friday-flatten exits 5 % -> 8 / 12 / 18 %, max equity drawdown on the null -77 % -> -66 / -58 / -47 %, worst Prague-day low -2.9 % -> -2.2 / -1.8 / -1.5 %, worst trade -7.7 R -> -5.2 / -4.1 / -2.4 R. The ATR floor beats the scalar floors of `stop_floor.md` in one respect: k = 1.0 keeps the trade count (spread-relative blocks fall as stops widen, offsetting longer holds), and it binds for gold (whose pip floor never did). D_null by year, k = 2.0: 2014 -0.049, then +0.077, +0.058, +0.082, +0.049, +0.126, +0.105, +0.113: 2014 is the only negative year for all three k; 2019 to 2021 gains are largest, which overlaps the placeholder-spread era and is being checked by the sentinel-missing run [SENT]. Without gold the effect is larger (+0.045 / +0.059 / +0.084).
Revision 1 (k = 2.5 and 3.0, logged in the prereg before it was run; `quant_m2`, 40 seeds, same flips): null R per trade -0.0544 / -0.0382, **D_null +0.084 (SE 0.016) / +0.100 (0.017)**, 8/8 and 7/8 DEV years, no-gold +0.109 / +0.123, null trades 69 % / 61 % of control, R per month -1.73 / -1.08, real R per trade -0.0511 / -0.0631 (D_act +0.080 (0.028) / +0.068 (0.028); k = 2.5 clears the HELPS bound on the real DEV path but fails the trade-count guard of 80 % and is not nominated; family not re-corrected). The gain per trade keeps growing with the stop, so the optimum is set by what you are willing to give up (trade count, holding days, weekend exposure), not found by this grid; beyond k = 2 most trades end by the Friday flatten.
Sentinel-missing robustness (`quant_s1`, 20 seeds, candidates and bars with the 50-point placeholder imputed; control null -0.1205, real -0.1125, 4,203 trades): M1_k10 +0.035 (0.012), M1_k15 +0.045 (0.014), M1_k20 +0.055 (0.016), all 7/8 years, real D_act +0.010 / +0.008 / +0.045 (SE 0.017 to 0.026): the ATR floor does not depend on the placeholder. The spread caps become clearer once the placeholder is imputed: M2_c7 +0.030 (0.009, 8/8 years, trades 70 %), M2_c5 +0.033 (0.012, 6/8, trades 54 %), both REDUCES; in the primary data they sat at the bound (+0.021, +0.026). A 7 % cap is therefore a modest cost lever (about +0.02 to +0.03 R at 30 % fewer trades) rather than nothing.

### 2.2 Ranking by expected benefit on a skill-less signal (measured where tested, expected from theory otherwise)
| rank | idea | evidence | expected / measured effect | cost |
|---|---|---|---|---|
| 1 | ATR-scaled minimum stop, k 1.0 to 2.0 (k 2.5 to 3.0 give more per trade, +0.084 / +0.100 R, at 69 % / 61 % of the trades) | measured, DEV, null, 7/8 years, robust to the placeholder spread | +0.04 to +0.065 R per trade, R per month -6.4 -> -4.7 to -2.7; trades 103 % to 80 % | needs one pre-registered holdout; live change is `MIN_SL_PIPS` -> max(floor, k x ATR(H1)) |
| 2 | Keep the relative spread cap at 10 % (do not loosen) | measured | worth +843 R over the window | none (already live) |
| 3 | Fewer simultaneous positions (max 1 to 2) | measured | per trade 0; R per month -23 % at max 1 | trades -20 % |
| 4 | Tighter spread cap, 7 % | measured; borderline in the primary data, REDUCES with the placeholder imputed | +0.021 R (z 2.55) primary, +0.030 R sentinel-missing; trades -29 % | modest; not nominated |
| 5 | Friday flatten (live) | measured here and in `rule_changes.md` | +0.011 R per trade, cuts the -12.7 R gap tail | none |
| 6 | Cost-aware sizing: risk weight min(1, c*/cost-per-R) | specified, not run | at most a few hundredths of an R-equivalent; cost per R is 0.07 to 0.10 for all pairs so there is little to weight | needs new sizing code |
| 7 | Volatility-scaled risk (risk ~ 1/ATR percentile) | specified, not run | mean R per trade unchanged (martingale); smooths the drawdown path | needs sizing code and a drawdown test |
| 8 | Entry cut-off 20:00 / 21:00 UTC, target 2R / 4R, time stop 24 h, breakeven variants | measured (M3, M5, M6; breakeven in `rule_changes.md`) | 0 within noise | none |
| 9 | Regime filter | measured by removal | blocks at the average loss, not selective | none |
| 10 | Rollover (21:00 UTC) flatten or no-hold window | specified, not run | removes the -12.7 R type of tail; effect on the mean unmeasurable (one event) | small |

### 2.3 Pre-registered specs of the untested items (to run only if the owner wants them)
* **Cost-aware sizing.** Rule: risk fraction = 0.25 % x min(1, c* / (spread/stop + commission/risk)), c* in {0.08, 0.10} fixed in advance; success: null D_null >= +0.02 R per unit of risk (R-weighted) with 95 % bound above 0; power as above (minimum detectable 0.03 to 0.045 R); a weighting that only trades less is labelled an exposure effect.
* **Volatility-scaled risk.** Rule: risk = 0.25 % x clip(median ATR / ATR, 0.5, 1.5) with a 250-day rolling median (causal, own no-look-ahead test); success criterion is not expectancy but the 95th percentile of max drawdown and the worst Prague-day low at equal average risk; the expected mean-R effect is zero.
* **Rollover no-hold.** Rule: close all positions at 20:55 UTC every day (or no entries 20:00 to 22:00 and flatten at 20:55); grid {20:55}; success: worst-trade and worst-day improvement with null D_null >= 0; one extreme event, so unpowered for the mean.
* **ATR floor variants beyond this grid** (ATR(H4), different periods) are not proposed: the family would grow without new information.

## 3. Robust risk profile

### 3.1 What was simulated
`quant_risk_days`: full portfolio simulations of DEV at each profile's static settings (real signals + 20 reflect null seeds), reduced to Prague-day sources; `quant_risk`: block bootstrap (5-day blocks, 20,000 paths, seed 21) of those days with the path-dependent rules the simulator lacks, FTMO-style limits (daily 5 % of the initial balance incl. floating, static 10 %, target 10 %). FTMO terms are from third-party summaries (search on 2026-10-31); the 5 % and 10 % figures agree across sources, how the daily 5 % is anchored does not; **verify against FTMO's own rule text before relying on it**. The engine reduces to `montecarlo.ftmo_mc` when no rule is active (test: breach and pass probabilities agree within 0.02 on 20,000 paths).
Static settings: P0 = live (0.25 %, max 3, gate 3 %); P1 = P0 trading plus guards; P2 = 0.15 %, max 2, gate 2 %; P3 = 0.075 %, max 2, gate 1.5 %. Dynamic rules as in the prereg (P1: halt -8 %, day stop -2 %, 8-loss pause; P2: ladder -3 % x0.5 / -5 % x0.25, halt -8 %, day stop -1.5 %, 6-loss pause; P3: ladder -2 % / -4 %, halt -6 %, day stop -1 %, 6-loss pause).
Sim facts per static config (DEV, 89 months): C25 real -73.1 % return, max DD -74.7 %; null (20 seeds) -76.2 % +/- 5.2, DD -77.1 %; C15 real -55.7 %, DD -57.5 %; C075 real -33.2 %, DD -34.6 %. Mean R -0.131 to -0.138 whatever the risk fraction.

### 3.2 Profile comparison at the simulated drag (mean R about -0.13; real DEV path / null pool, 12-month and 24-month horizons)
| profile | horizon | P(5 % daily breach) | P(10 % max-loss breach) | P(own halt) | max DD median / 95 % / 99 % | mean final return | median months to breach | median trades taken |
|---|---|---|---|---|---|---|---|---|
| P0 current | 6 m | 0.00 % / 0.00 % | 45.3 % / 52.0 % | n/a | -10.1 / -12.4 / -13.7 % | -7.0 % / -7.6 % | 4.2 | |
| P0 current | 12 m | 0.00 % / 0.00 % | **86.0 % / 91.3 %** | n/a | -10.9 / -14.1 / -15.9 % | -9.4 % / -9.8 % | 5.8 / 5.5 | |
| P0 current | 24 m | 0.00 % / 0.00 % | **98.5 % / 99.4 %** | n/a | -11.2 / -15.0 / -17.0 % | -10.1 % / -10.2 % | 6.3 / 5.9 | 268 / 260 |
| P1 current + guards | 12 m | 0.00 % | 1.1 % / 0.3 % | 89.8 % / 94.6 % | -9.0 / -12.4 / -14.2 % | -7.6 % / -7.9 % | 4.9 / 4.0 | |
| P1 current + guards | 24 m | 0.00 % | 1.2 % / 0.4 % | 97.6 % / 99.2 % | -9.2 / -13.1 / -15.1 % | -8.1 % / -8.1 % | 5.3 / 5.1 | 214 / 205 |
| P2 reduced + ladder | 12 m | 0.00 % | 0.0 % / 0.0 % | 1.5 % / 2.6 % | -6.7 / -8.4 / -9.2 % | -5.5 % / -5.8 % | | |
| P2 reduced + ladder | 24 m | 0.00 % | 0.0 % / 0.0 % | 54.9 % / 65.0 % | -8.3 / -10.2 / -11.4 % | -7.4 % / -7.6 % | halt at 19.3 / 18.7 | 904 / 880 |
| P3 small + ladder | 12 m | 0.00 % | 0.0 % / 0.0 % | 0.0 % | -4.0 / -5.1 / -5.5 % | -3.3 % / -3.5 % | | |
| P3 small + ladder | 24 m | 0.00 % | 0.0 % / 0.0 % | 7.3 % / 12.3 % | -5.4 / -6.5 / -7.0 % | -4.8 % / -5.0 % | halt at 22.1 / 21.9 | 963 / 983 |

Reading: (a) the daily 5 % limit is not the problem in this resample (0.00 % in every profile at this drag, but a block bootstrap of DEV days cannot produce a day worse than DEV's worst, -4.58 %, which is within 0.4 points of 5 %, so read 0.00 % as 'not observed', not as a bound; the live 3 % gate fires 3 times in 89 months); the static 10 % limit is. (b) The current profile is certain to breach it within two years at this drag; the only profiles that do not are those that cut the risk (P2, P3) and, at the price of stopping, P1's halt. (c) P1 stops itself at -8 % in 90 to 99 % of paths within 12 to 24 months, i.e. it ends the account early but bounds the loss (its 1 % residual breach comes from gaps and open positions after the halt). (d) Drawdown is bounded by construction in P2 and P3 because risk shrinks as the loss grows; the 99th percentile worst drawdown at 12 months falls from -15.9 % (P0) to -9.2 % (P2) and -5.5 % (P3).

### 3.3 Risk management cannot create profit: the same profiles with the mean R moved (real-path source, 12 months)
| mean R per trade | metric | P0 | P1 | P2 | P3 |
|---|---|---|---|---|---|
| as simulated (-0.13) | P(pass 10 %) / P(max-loss breach) | 0.7 % / 86.0 % | 0.7 % / 1.1 % | 0.03 % / 0.0 % | 0.0 % / 0.0 % |
| 0.00 | P(pass 10 %) / P(max-loss breach) | 22.1 % / 19.2 % | 22.2 % / 0.6 % | 5.0 % / 0.0 % | 0.02 % / 0.0 % |
| +0.05 | P(pass 10 %) / P(max-loss breach) | 48.0 % / 5.5 % | 47.7 % / 0.2 % | 17.7 % / 0.0 % | 0.2 % / 0.0 % |
| +0.10 | P(pass 10 %) / P(max-loss breach) | 75.6 % / 1.1 % | 75.2 % / 0.1 % | 42.6 % / 0.0 % | 1.4 % / 0.0 % |
| mean final return, 12 m: -0.13 / 0 / +0.05 / +0.10 | | -9.4 / +0.1 / +5.2 / +8.6 % | -7.6 / +0.3 / +5.1 / +8.5 % | -5.5 / +0.6 / +4.1 / +7.2 % | -3.3 / +0.3 / +2.2 / +4.1 % |

Quantified: at the simulated drag no profile has a pass probability above 0.7 % (the 22 % at mean zero is the symmetric random-walk chance of touching +10 % before -10 % in a year, which is luck at 0.25 % risk, not skill; the null-pool numbers agree within 2 points). Smaller risk cuts the loss speed roughly in proportion to the risk fraction and cuts the winnings by the same factor: at +0.10 R, P2 passes 42.6 % of the time against P0's 75.6 %. A zero-mean stream stays zero-mean under every rule (tested), the ladder and the halt only change the shape of the distribution.

### 3.4 Time to ruin versus risk fraction (no overlays; real DEV path / null pool; mean final return and breach probability; 10 % static limit, 5 % daily limit)
| f (% risk per trade) | expected loss per month (mean path) | mean-path months to -10 % | P(any breach) 6 m | 12 m | 24 m | P(daily) 12 m | median months to breach (12 m horizon) | 95 / 99 % worst DD, 12 m |
|---|---|---|---|---|---|---|---|---|
| 0.05 | -0.29 % | 34 | 0.0 / 0.0 % | 0.0 / 0.0 % | 6.0 / 10.4 % | 0.00 % | | -6.2 / -7.2 % |
| 0.075 | -0.44 % | 23 | 0.0 / 0.0 % | 1.8 / 2.6 % | 51 / 64 % | 0.00 % | 11.1 | -9.2 / -10.2 % |
| 0.10 | -0.58 % | 17 | 0.1 / 0.2 % | 15.6 / 20.5 % | 80 / 88 % | 0.00 % | 10.3 | -10.5 / -11.1 % |
| 0.15 | -0.87 % | 11.5 | 7.7 / 9.5 % | 55 / 64 % | 95 / 98 % | 0.00 % | 8.7 | -11.9 / -12.9 % |
| **0.25 (live)** | **-1.45 %** | **6.9** | **45 / 52 %** | **86 / 91 %** | **98.5 / 99.5 %** | 0.00 % | 5.8 | -14.1 / -15.9 % |
| 0.35 | -2.0 % | 4.9 | 70 / 75 % | 93 / 96 % | 97 / 98 % | 5.5 % | 4.0 | -15.7 / -17.6 % |
| 0.50 | -2.9 % | 3.4 | 83 / 87 % | 92 / 95 % | 93 / 95 % | 3.5 % | 2.6 | -17.0 / -18.4 % |
| 1.00 | -5.8 % | 1.7 | 83 / 86 % | 83 / 86 % | 83 / 87 % | 25 % | 0.8 | -17.6 / -20.4 % |

Expected loss per month is the arithmetic monthly mean of the DEV real path (-1.45 % at f = 0.25) scaled linearly in f; "mean-path months to -10 %" is 10 % divided by it. The loss speed is proportional to f (1/f time to ruin) and the daily limit only becomes active above f = 0.35. With a mean R of zero the same curve gives: 12-month breach 0.0 / 0.1 / 2.8 / 19 / 37 / 47 % at f = 0.05 / 0.10 / 0.15 / 0.25 / 0.35 / 0.50 (real source), i.e. a risk of ruin of about one in five at the live setting even with no drag at all, which is the cost of the 0.25 % risk against a 10 % limit and a trade standard deviation of 1.57 R.

### 3.5 Recommended forward-demo profile (rule fixed in `quant_risk_prereg.md` section 3, then applied)
Rule: the largest f among the four profiles and the grid with P(max-loss breach within 12 months) <= 1 %, P(daily breach within 12 months) <= 0.5 % and a median of at least 500 trades taken in 24 months, under both the real DEV path and the null pool at the simulated drag. Result (`research/output/quant_risk_demo/demo_rule.csv`): P0 fails (86 %), P1 fails (1.1 % real), **P2 passes** (0.0 %, 0.0 %, median 904 / 880 trades), P3 passes, grid f = 0.05 passes, f >= 0.075 without overlays fails. **Recommended: P2.**
Profile P2: risk 0.15 % of balance per trade; at most 2 open positions counting pending orders; daily gate 2 % (equity vs Prague-midnight balance); loss-from-initial ladder (>= 3 % x0.5, >= 5 % x0.25); halt new entries at -8 % loss from initial; no new entries for the rest of the day once the day's realised loss reaches 1.5 %; skip the rest of the day and the next day after 6 consecutive losing trades; Friday flatten at 20:00 UTC kept.
What it buys: no breach of either FTMO limit in 24 months in 20,000 simulated paths at the simulated drag (the paths resample one 7.4-year history and are not independent, so 0 of 20,000 does not bound the true probability; the profile halts itself in 55-65 % of paths by 24 months), a loss of about 0.5 % per month over 12 months (mean final return -5.5 %; the 24-month summary figure is -0.31 % per month) instead of 1.45 %, about 900 trades observed (against about 270 for P0 before it breaches), halted by its own rule after a median 19 months. What it costs: if an edge of +0.10 R existed, its mean 12-month return would be +7.2 % against +8.6 % for P0 and its probability of reaching the 10 % target 43 % against 76 %. It is a capital-preservation and evidence-gathering profile, not a profit profile. P3 (0.075 %) is the same idea at half the speed (halted in 7 to 12 % of paths in 24 months).
If the owner would rather not halt at all on a demo: P3 runs two years with a median -4.8 % result.

**Live-code spec for P2 (a later reviewed change; nothing was changed here; each item needs a test that fails without it, the full suite, and the `reviewer`):**
1. `utils/risk_manager.py`: `FIXED_RISK_PCT` 0.25 -> 0.15; add a ladder multiplier in `calculate_position` from the loss relative to a persisted initial balance (>= 3 % x0.5, >= 5 % x0.25).
2. `config.py` `max_open_trades` 3 -> 2, and count **pending orders as open risk** and re-count inside the cycle after each order placed (the live snapshot is read once per cycle, so up to 4 positions were seen on DEV and pending orders are not counted; `main.py` around the max-open check and `_analyse_pair`).
3. Daily limit: 3 % -> 2 %, measured on **equity** against the **Prague-midnight** day-start balance (REVIEW item 6), baseline persisted per login (already persisted; the day key must change from the UTC date).
4. New static max-loss halt: no new entries when equity <= initial x 0.92; flatten when equity <= initial x 0.90 + a margin (FTMO's 10 % must never be touched); initial balance stored in the baselines file; manual reset only.
5. Day stop at 1.5 % realised loss, consecutive-loss pause (6 losses: rest of day plus next day): needs closed-trade outcomes from `history_deals_get` filtered by `BOT_MAGIC` (REVIEW: `record_trade_outcome` is never called), persisted so a restart does not reset the counter.
6. XAUUSD: its stop buffers, floor and breakeven buffer treat it like a 5-digit pair (11 % fill-bar stop-outs, -0.213 R): either exclude it from the demo or set its risk to half until a dollar/ATR floor exists.
7. Keep the Friday flatten and the relative spread cap exactly as they are; log the ladder level, halt and pause state in `candidates.jsonl` so the demo can be compared with this simulation.
8. Not recommended as risk limits: a consecutive-loss limit below 6 (ordinary variance trips it every few weeks), and the +5 % monthly profit pause as a protection (it only stops after gains; a monthly loss pause was not simulated here).

## 4. Honest limits
* **No edge, no profit.** The strategy's expectancy on DEV is -0.13 R and its signal skill is zero within noise (stage 0). Everything in sections 2 and 3 reduces the speed of loss or the probability of a limit breach; none of it creates expectancy. The best cost lever still leaves the null at about -0.065 to -0.074 R, and the frictionless residual of the simulator null is -0.025 to -0.07 R, so "break-even for a skill-less signal" is not reachable by cost work alone; a real skill of at least about +0.07 R per trade would be needed.
* What cannot be known without an edge: whether the DEV drag persists (spreads after 2021 are much smaller than the 2014-2020 level: recorded EURUSD spread about 7 % of a 10-pip stop on DEV against 2 to 3 points later), whether the ATR-floor gain survives a different cost environment (it is a cost-per-R effect, so it should shrink when spreads shrink, which is why it must go through a holdout), and whether any real signal exists.
* **Simulation assumptions:** reflect null is not a perfect zero-skill model (frictionless residual -0.025 to -0.07 R, shared by real and null, so only differences are read); default conventions (SL wins ties, targets never pay gapped prices, fill-bar rule) are conservative; the placeholder 50-point spread (2020-05 to 2021-09) is used as is in the primary runs (the sentinel-missing run changes levels by about 0.02 R and no sign); zero recorded spreads are imputed from same-hour medians; no swaps, slippage, requotes, news blocking or LLM; weekend and rollover gaps are those of the broker's history only.
* **Monte Carlo assumptions:** 5-day blocks of 1,860 days from one 7.4-year price history; the 20 null seeds share candidates and prices (they are not independent histories, so the null pool is a check on direction luck, not on market regimes); risk scaling is linear in the multiplier; the day's floating low is scaled at the multiplier at the start of the day (rules that change the multiplier intraday do not change the low); the daily gate of the static config is simulated but the other overlays act on realised trade returns; the mean-R scenarios shift trade returns by a constant (an approximation of the intraday path); FTMO rules are assumed (see 3.1); the static initial balance is taken as the reference for the ladder and halt.
* Statistical: 40 null seeds give a paired SE of 0.008 to 0.016 R, so a null change smaller than 0.03 to 0.045 R is not detectable; the real-signal SE is 0.017 to 0.026 R; one deterministic real path per arm. D_null is conditional on this price history. Family size 11 (z = 2.61) plus two more arms (ext) and the revision log are in the prereg.
* Data flags: results are given with and without XAUUSD and without GBPUSD 2019 (all signs unchanged); the sentinel-missing re-run (section 2.1); 2014 is the one DEV year where the ATR floor does not help (descriptive).
* Not done: news-filter effect (no calendar), cost-aware sizing, volatility-scaled sizing and rollover flatten (specified only), a monthly-loss pause, any FINAL or 2025-05+ read.

## 5. Prioritised next steps
| # | Step | Cost / effort | Needs the owner's locked-holdout run? |
|---|---|---|---|
| 1 | Restart the forward demo on profile P2 (live-code spec 3.5 through the reviewer) | 1 to 2 days of live code and tests, reviewer rounds | No (demo is plumbing, never a gate) |
| 2 | Pre-register the ATR floor (k in {1.0, 1.5}; the grid from this review, nothing re-tuned) with a stressed-cost criterion and run it once on FINAL | half a day to write, one run (hours of compute for the null seeds) | **Yes** (alpha 0.025, stressed costs, trades per month >= 80 %) |
| 3 | Resolve the 50-point placeholder spread before any FINAL run (flag exists: `--sentinel-missing`), apply it identically to actual and null | done in tooling; decide the treatment | No |
| 4 | XAUUSD: dollar/ATR stop floor and buffers (11 % fill-bar stops, -0.213 R, 2.3x the average loss) or exclude from the demo | small once decided | The floor does (it is a rule change) |
| 5 | Live hygiene from the open lists: equity-based daily limit with Prague reset, pending orders counted as risk, in-cycle position re-count, `record_trade_outcome` wiring, 10 % max-loss check | folded into the P2 change | No |
| 6 | Rollover (21:00) flatten test and a monthly-loss-pause test as pre-registered DEV studies | 1 day each | Only if they pass DEV |
| 7 | Historical news calendar (data task) so the news gate can be measured | data purchase / import | No |
| 8 | Real-signal confirmation of any M1 effect (the null measures cost, not the signal) can only come from FINAL, where the minimum detectable change is about 0.05 to 0.07 R | one run | Yes |

## 6. Verification and deviations
* Reproduction: the anatomy run gives 3,970 trades and -0.1308 R, identical to `stage0_R2`; the methods and gates controls (B0) give -0.1308 on the real path; the C25 risk source reproduces the same trade count.
* Hand checks (sampled from the trade log): three trades re-derived from price, volume and contract size: gross P/L, risk, commission and R agree to the cent (trade 0 USDCAD breakeven: gross 49.90, risk 249.20, commission 13.25, R 0.1471); two EURUSD stop-outs re-derived from raw M5 bars: MAE 1.03 and 1.06 R and MFE 0.05 R agree with the module; the weekly/years tables and the FTMO-style engine are covered by hand-built tests (halt after 3 trades, breach on day 5, ladder, streak pause, day stop, pass needs 4 days, weekend close moved to Monday).
* Deviations (logged in the preregs): gate counterfactuals use 16 null seeds instead of 20 (shared machine at 100 % load); the demo rule's trade-count metric was made operational after the main report (the thresholds and candidates are unchanged); one parameter revision (ATR floor 2.5 and 3.0); the exposure table was recomputed from the saved trades after a sort-order bug (zero-length fill-bar trades produced negative counts), fixed with a test; an episode-start off-by-one in the drawdown anatomy fixed with a test.
* Tests: 40 new tests (12 loss, 11 methods, 17 risk); full suite run once at the end: 625 passed (includes `test_fidelity.py::test_no_lookahead`); pytest temp `research/.pytest_tmp_quant` deleted.

## 7. For the orchestrator to merge (I did not edit `progress.md` or `RESULTS.md`)
* `progress.md` entry: quant loss/risk review done on DEV (no FINAL read). Anatomy: cost 0.091 R of the -0.131 R (spread 0.047, commission 0.044), pre-cost -0.040 R; spread cap worth +843 R, Friday flatten +52 R, other gates about 0. Methods: ATR-scaled stop floor reduces the null drag monotonically (k 1.0 / 1.5 / 2.0 / 2.5 / 3.0: +0.040 / +0.050 / +0.065 / +0.084 / +0.100 R, trades 103 / 91 / 80 / 69 / 61 %), robust to the sentinel spread, real signals not separable (k 2.5 nominally HELPS but fails the trade-count guard); nothing nominated for FINAL. Risk: at 0.25 % the 10 % limit is breached within 12 months in 86 % of paths, the 5 % daily limit never; demo rule selects P2 (0.15 %, max 2, ladder, halt -8 %). Open: owner decision on an ATR-floor holdout; live-code spec 3.5 not implemented.
* `RESULTS.md`: sections 1.7, 2.1, 3.2 to 3.4 of this report are the table-ready results; all negatives (M3, M4 per trade, M5, M6 control, gates other than spread/flatten) are in 2.1 and 1.7.
* `research/reports/session_pair_weekend_analysis.md` (the data analyst's report) exists; I did not duplicate it and did not reconcile it with the per-pair figures in 1.1. The headline numbers differ by data treatment, not by error: this report uses the broker's 50-point placeholder spread as recorded (3,970 trades, -0.131 R); the analyst's report treats it as missing (4,173 trades, -0.100 R); the sentinel-missing re-run here (section 2.1) gives -0.1125 R on 4,203 trades.
