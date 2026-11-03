# Data-quality report: broker D1 history for the multi-asset trend study (written 2026-11-03, before any rule was run on returns)

Scope: `research/data_multi/<SYMBOL>_D1.parquet` and `<SYMBOL>_spec.json` (read-only pull from the terminal by `research/fetch_multi.py`; this session never called MetaTrader5). **Only bars with label before 2021-06-01 were read** (file-level filter; FINAL and later were not brought into memory, so nothing about 2021-06 onward is in this report). The screen is `python -m research.multi_trend_data screen`; its outputs are `research/output/multi_trend_dev/{dq_table.csv, dq_flags.json, dq_report_tables.md, universe.csv, universe_sha256.txt}`. No strategy return of any kind was computed to write this report; the rules were written first (constants in `multi_trend_data.py`) and the screen applied mechanically. The only per-instrument numbers looked at are prices, dates, spreads, volumes and metadata.

## 1. Status of the fetch (what is and is not in the data)

<!--STATUS-->
Fetch state: **complete** (the last log line starts with `done:`). D1 files on disk: 77. Log lines: 77 fetched, 0 FAILED (timeout, `rc=-9`).

Fetched: GBPUSD, USDJPY, USDCAD, USDCHF, NZDUSD, USDNOK, USDSEK, US500.cash, US30.cash, US100.cash, GER40.cash, UK100.cash, JP225.cash, USOIL.cash, UKOIL.cash, NATGAS.cash, XAUUSD, XAGUSD, XCUUSD, XPTUSD, XPDUSD, AUDCAD, AUDCHF, AUDJPY, AUDNZD, AUDUSD, AUS200.cash, CADCHF, CADJPY, CHFJPY, COCOA.c, COFFEE.c, CORN.c, COTTON.c, DXY.cash, EU50.cash, EURAUD, EURCAD, EURCHF, EURCZK, EURGBP, EURHUF, EURJPY, EURNOK, EURNZD, EURPLN, EURUSD, FRA40.cash, GBPAUD, GBPCAD, GBPCHF, GBPJPY, GBPNZD, HEATOIL.c, HK50.cash, N25.cash, NZDCAD, NZDCHF, NZDJPY, SOYBEAN.c, SPN35.cash, SUGAR.c, US2000.cash, USDCNH, USDCZK, USDHKD, USDHUF, USDILS, USDMXN, USDPLN, USDSGD, USDZAR, WHEAT.c, XAGAUD, XAGEUR, XAUAUD, XAUEUR.

Missing so far (timed out, not on disk; the owner's session retries them): -.

The universe below is the screen of everything on disk and is final unless the owner adds symbols (its sha256 is in `universe_sha256.txt`). Refresh everything with `python -m research.multi_trend_data screen` followed by `python -m research.multi_trend_data report`.
<!--/STATUS-->

## 2. What the screen checks, and the rule each finding triggers

| Check | Definition (fixed) | Consequence |
|---|---|---|
| Class | `classify`: Forex path with both currencies in G8 = FX; Cash paths split into EN / MT / EQ by name; Metals = MT; Agriculture = AG; Exotics, DXY, crypto, single stocks, unrecognised = excluded labels | Only FX, EQ, EN, MT, AG enter. Exotics: cost and financing cannot be bracketed (pegs, devaluation carry, wide spreads). DXY: linear function of the FX pairs. |
| History / entry | first Wednesday with at least 270 cleaned bars, not before 2003-01-01 | earlier bars are warm-up only |
| Minimum live span | entry to 2021-06-01 at least **8.0 years** (N) | shorter: excluded, listed with its span |
| Weekend bars | Saturday/Sunday labels merged into the next Monday bar | count reported (`weekend_merged`) |
| Non-positive prices, OHLC order | rows with a price <= 0 or NaN dropped; High/Low inconsistent bars counted | count reported |
| One-bar spikes | |r| > 10 robust sigma and > 5 %, reversed by >= 65 % on the next bar: bar dropped (a look-one-bar-ahead data-error repair, not a trading rule) | count reported (`spike_dropped`) |
| Holes | >= 8 consecutive missing weekdays between two bars | the year the hole starts and the year it ends are **masked** (instrument flat) |
| Weekday coverage | share of Mon-Fri weekdays with a bar, per calendar year, below 80 % | year masked |
| Stale data | more than 25 % exactly-zero returns in a year, or a run of 8 identical closes | year masked |
| Masked share | more than 30 % of the instrument's live years masked | instrument excluded |
| Jumps | |r| > 12 robust sigma and > 3 % (descriptive list; real shocks such as SNB 2015-01-15 and March 2020 are not errors) | **not** an exclusion by itself |
| Roll-like recurring gaps | >= 20 days with |z| > 5, >= 50 % of them in one 5-day-of-month bin, and that concentration significant (binomial tail x 6 < 0.001) (futures-style CFD roll calendar). Revised before any run: the first version (12 days, no tail test) flagged the spot pair AUDNZD on 14 random extreme days | instrument excluded |
| Duplicates | weekly return correlation >= 0.98 on >= 100 weeks with an instrument already kept | the later-starting (alphabetically later on ties) is excluded |
| Spread field | zero share and clean median reported; zero and (FX only, 2019-01..03 and 2020-05..2021-09) the 50-point sentinel treated as missing | costs use max(trailing median, class floor), see the preregistration section 4 |
| Clock / DST | (a) FX pairs that exist in the H1 cache: broker D1 open/close versus the H1 server-day open/close; (b) all instruments: median absolute overnight gap in US-DST-mismatch weeks versus other weeks (a median, so that March 2020 does not dominate) | descriptive; no exclusion (the weekly rule is insensitive to a one-hour boundary shift) |
| Quote currency | `currency_profit` from the spec | used for the USD return convention (G8 quote: exact conversion; other: local return, counted) |

## 3. Findings on the instruments fetched

<!--TABLES-->
Screened 77 fetched symbols; 30 enter the universe.

| class | fetched | in universe | excluded |
|---|---|---|---|
| FX | 28 | 28 | 0 |
| EQ | 13 | 0 | 13 |
| EN | 5 | 0 | 5 |
| MT | 9 | 2 | 7 |
| AG | 6 | 0 | 6 |
| EX | 15 | 0 | 15 |
| DXY | 1 | 0 | 1 |

**Per class (screened symbols with DEV bars):** median DST-week overnight-gap ratio (about 1 means no clock effect), instruments with at least one hole, with roll-like pattern, total weekend bars merged, total spikes dropped, median share of zero recorded spreads.

| class | symbols | median DST ratio | with holes | roll-like | weekend bars merged | spikes dropped | median zero-spread share |
|---|---|---|---|---|---|---|---|
| EN | 2 | 0.88 | 0 | 0 | 0 | 0 | 1.00 |
| EQ | 13 | 1.21 | 4 | 0 | 118 | 13 | 1.00 |
| EX | 13 | 0.42 | 8 | 0 | 464 | 1 | 0.13 |
| FX | 28 | 0.85 | 13 | 0 | 60 | 1 | 0.08 |
| MT | 8 | 1.05 | 2 | 0 | 16 | 1 | 0.96 |

**In the universe** (entry = first Wednesday with at least 270 bars, not before 2003-01-01; masked years are flat):

| symbol | class | first bar | entry | live years | masked years | holes | jumps | clean spread median (pts) | half-spread (bp) | quote |
|---|---|---|---|---|---|---|---|---|---|---|
| AUDCAD | FX | 2000-03-30 | 2003-01-01 | 18.41 | 2006,2007 | 1 | 0 | 45.0 | 2.2 | CAD |
| AUDCHF | FX | 2000-03-30 | 2003-01-01 | 18.41 | 2006,2007,2008,2009 | 2 | 2 | 50.0 | 2.7 | CHF |
| AUDJPY | FX | 2000-03-29 | 2003-01-01 | 18.41 | 2006,2008,2009 | 3 | 3 | 29.0 | 1.5 | JPY |
| AUDNZD | FX | 2000-03-30 | 2003-01-01 | 18.41 | 2006,2007,2008,2009 | 2 | 0 | 60.0 | 2.4 | NZD |
| AUDUSD | FX | 2000-03-29 | 2003-01-01 | 18.41 |  | 0 | 0 | 16.0 | 0.8 | USD |
| CADCHF | FX | 2000-03-30 | 2003-01-01 | 18.41 | 2006,2007,2008,2009 | 2 | 3 | 40.0 | 2.5 | CHF |
| CADJPY | FX | 2007-02-12 | 2008-03-05 | 13.24 |  | 0 | 0 | 17.0 | 0.9 | JPY |
| CHFJPY | FX | 2000-03-29 | 2003-01-01 | 18.41 | 2006,2008,2009 | 2 | 2 | 39.0 | 1.6 | JPY |
| EURAUD | FX | 2004-06-17 | 2005-07-06 | 15.9 |  | 0 | 0 | 40.0 | 0.8 | AUD |
| EURCAD | FX | 2000-03-30 | 2003-01-01 | 18.41 | 2003,2006,2007 | 2 | 1 | 60.0 | 1.5 | CAD |
| EURCHF | FX | 2000-03-29 | 2003-01-01 | 18.41 | 2014,2015 | 1 | 3 | 25.5 | 0.8 | CHF |
| EURGBP | FX | 2000-03-29 | 2003-01-01 | 18.41 |  | 0 | 0 | 16.0 | 0.9 | GBP |
| EURJPY | FX | 2000-03-29 | 2003-01-01 | 18.41 | 2000 | 4 | 2 | 20.0 | 0.9 | JPY |
| EURNZD | FX | 2000-03-30 | 2003-01-01 | 18.41 | 2006,2007,2008,2009 | 2 | 1 | 100.0 | 2.5 | NZD |
| EURUSD | FX | 2000-03-29 | 2003-01-01 | 18.41 |  | 0 | 0 | 9.0 | 0.8 | USD |
| GBPAUD | FX | 2007-08-21 | 2008-09-10 | 12.72 |  | 0 | 0 | 31.0 | 0.8 | AUD |
| GBPCAD | FX | 2007-08-21 | 2008-09-10 | 12.72 |  | 0 | 0 | 43.0 | 1.1 | CAD |
| GBPCHF | FX | 2000-03-29 | 2003-01-01 | 18.41 | 2006 | 1 | 1 | 55.0 | 1.6 | CHF |
| GBPJPY | FX | 2000-03-29 | 2003-01-01 | 18.41 |  | 0 | 1 | 57.0 | 1.8 | JPY |
| GBPNZD | FX | 2010-05-10 | 2011-07-06 | 9.91 | 2011,2012 | 3 | 0 | 28.0 | 0.8 | NZD |
| GBPUSD | FX | 2000-03-29 | 2003-01-01 | 18.41 |  | 0 | 1 | 20.0 | 0.8 | USD |
| NZDCAD | FX | 2007-08-20 | 2008-09-03 | 12.74 | 2011,2012 | 2 | 0 | 16.0 | 0.8 | CAD |
| NZDCHF | FX | 2007-08-21 | 2008-09-03 | 12.74 |  | 0 | 1 | 16.0 | 0.8 | CHF |
| NZDJPY | FX | 2007-02-12 | 2008-02-27 | 13.26 |  | 0 | 0 | 17.0 | 1.1 | JPY |
| NZDUSD | FX | 2000-03-29 | 2003-01-01 | 18.41 |  | 0 | 0 | 17.0 | 0.8 | USD |
| USDCAD | FX | 2000-03-29 | 2003-01-01 | 18.41 |  | 0 | 0 | 22.0 | 0.8 | CAD |
| USDCHF | FX | 2000-03-29 | 2003-01-01 | 18.41 |  | 0 | 2 | 17.0 | 0.8 | CHF |
| USDJPY | FX | 2000-03-29 | 2003-01-01 | 18.41 |  | 0 | 0 | 11.0 | 0.8 | JPY |
| XAGUSD | MT | 2008-11-07 | 2009-11-25 | 11.52 |  | 0 | 2 | 40.0 | 2.5 | USD |
| XAUUSD | MT | 2004-06-11 | 2005-07-06 | 15.9 |  | 0 | 1 | 50.0 | 2.5 | USD |

**Excluded** (rule that fired):

| symbol | path | first bar | bars | reasons |
|---|---|---|---|---|
| COCOA.c | Agriculture\COCOA.c |  | 0 | fewer than 50 D1 bars before 2021-06-01 (terminal history starts 2023-01-19) |
| COFFEE.c | Agriculture\COFFEE.c |  | 0 | fewer than 50 D1 bars before 2021-06-01 (terminal history starts 2023-01-19) |
| CORN.c | Agriculture\CORN.c |  | 0 | fewer than 50 D1 bars before 2021-06-01 (terminal history starts 2023-03-30) |
| COTTON.c | Agriculture\COTTON.c |  | 0 | fewer than 50 D1 bars before 2021-06-01 (terminal history starts 2024-11-25) |
| SOYBEAN.c | Agriculture\SOYBEAN.c |  | 0 | fewer than 50 D1 bars before 2021-06-01 (terminal history starts 2023-08-04) |
| SUGAR.c | Agriculture\SUGAR.c |  | 0 | fewer than 50 D1 bars before 2021-06-01 (terminal history starts 2024-11-25) |
| DXY.cash | Cash III CFD\DXY.cash |  | 0 | fewer than 50 D1 bars before 2021-06-01 (terminal history starts 2024-11-26) |
| HEATOIL.c | Commodities\HEATOIL.c |  | 0 | fewer than 50 D1 bars before 2021-06-01 (terminal history starts 2024-12-11) |
| NATGAS.cash | Commodities\NATGAS.cash |  | 0 | fewer than 50 D1 bars before 2021-06-01 (terminal history starts 2024-10-23) |
| UKOIL.cash | Cash II CFD\UKOIL.cash | 2016-04-22 | 1317 | live span 4.1y < 8y |
| USOIL.cash | Cash II CFD\USOIL.cash | 2020-12-31 | 107 | live span 0.0y < 8y |
| WHEAT.c | Agriculture\WHEAT.c |  | 0 | fewer than 50 D1 bars before 2021-06-01 (terminal history starts 2023-03-27) |
| AUS200.cash | Cash II CFD\AUS200.cash | 2019-02-08 | 564 | live span 1.1y < 8y |
| EU50.cash | Cash II CFD\EU50.cash | 2017-12-29 | 868 | live span 2.4y < 8y |
| FRA40.cash | Cash II CFD\FRA40.cash | 2017-12-29 | 872 | live span 2.4y < 8y; duplicate of EU50.cash |
| GER40.cash | Cash CFD\GER40.cash | 2017-12-28 | 713 | live span 1.7y < 8y; masked 33% of live years |
| HK50.cash | Cash II CFD\HK50.cash | 2018-12-17 | 605 | live span 1.4y < 8y |
| JP225.cash | Cash II CFD\JP225.cash | 2017-12-28 | 886 | live span 2.4y < 8y |
| N25.cash | Cash II CFD\N25.cash | 2020-11-12 | 87 | live span 0.0y < 8y |
| SPN35.cash | Cash II CFD\SPN35.cash | 2020-11-09 | 142 | live span 0.0y < 8y |
| UK100.cash | Cash CFD\UK100.cash | 2017-12-28 | 871 | live span 2.4y < 8y |
| US100.cash | Cash CFD\US100.cash | 2017-12-29 | 882 | live span 2.4y < 8y |
| US2000.cash | Cash II CFD\US2000.cash | 2018-01-23 | 863 | live span 2.3y < 8y |
| US30.cash | Cash CFD\US30.cash | 2019-02-08 | 573 | live span 1.2y < 8y |
| US500.cash | Cash CFD\US500.cash | 2017-12-29 | 881 | live span 2.4y < 8y |
| EURCZK | Exotics\EURCZK | 2015-01-02 | 1556 | class EX; live span 5.4y < 8y; masked 33% of live years |
| EURHUF | Exotics\EURHUF | 2015-01-01 | 1610 | class EX; live span 5.4y < 8y |
| EURNOK | Exotics\EURNOK | 2013-04-01 | 2112 | class EX; live span 7.1y < 8y |
| EURPLN | Exotics\EURPLN | 2015-01-01 | 1610 | class EX; live span 5.4y < 8y |
| USDCNH | Exotics\USDCNH |  | 0 | fewer than 50 D1 bars before 2021-06-01 (terminal history starts 2024-01-02) |
| USDCZK | Exotics\USDCZK | 2015-01-02 | 1611 | class EX; live span 5.4y < 8y |
| USDHKD | Exotics\USDHKD | 2007-07-10 | 3452 | class EX |
| USDHUF | Exotics\USDHUF | 2015-01-01 | 1610 | class EX; live span 5.4y < 8y |
| USDILS | Exotics\USDILS | 2021-01-04 | 106 | class EX; live span 0.0y < 8y |
| USDMXN | Exotics\USDMXN | 2007-07-23 | 3423 | class EX |
| USDNOK | Exotics\USDNOK | 2006-02-06 | 3960 | class EX |
| USDPLN | Exotics\USDPLN | 2013-03-19 | 2122 | class EX; live span 7.2y < 8y |
| USDSEK | Exotics\USDSEK | 2000-03-30 | 5500 | class EX |
| USDSGD | Exotics\USDSGD |  | 0 | fewer than 50 D1 bars before 2021-06-01 (terminal history starts 2024-01-02) |
| USDZAR | Exotics\USDZAR | 2006-01-13 | 3810 | class EX |
| XAGAUD | Metals CFD\XAGAUD | 2016-10-24 | 1182 | live span 3.6y < 8y |
| XAGEUR | Metals CFD\XAGEUR | 2017-08-14 | 976 | live span 2.8y < 8y |
| XAUAUD | Metals CFD\XAUAUD | 2016-10-21 | 1183 | live span 3.6y < 8y |
| XAUEUR | Metals CFD\XAUEUR | 2016-10-19 | 1186 | live span 3.6y < 8y |
| XCUUSD | Metals CFD\XCUUSD |  | 0 | fewer than 50 D1 bars before 2021-06-01 (terminal history starts 2024-12-02) |
| XPDUSD | Metals CFD\XPDUSD | 2015-01-07 | 1540 | live span 5.3y < 8y |
| XPTUSD | Metals CFD\XPTUSD | 2015-01-07 | 1553 | live span 5.3y < 8y |
<!--/TABLES-->

## 4. Cross-checks and findings (hand-written 2026-11-03 on the completed fetch; the tables above regenerate, this text does not)

* **Fetch.** Complete: the owner's retry pass (`research/output/scratch/fetch_multi_retry.log`) ends `done: 77 symbols, 0 failed`; 77 D1 files are on disk (62 from the first pass plus the 15 that had timed out). Nothing is missing from the candidate set the fetch was asked for.
* **D1 clock and DST.** For the six symbols that exist in the H1 cache (`python -m research.multi_trend_clockcheck <SYMBOL>`: AUDUSD, EURUSD, GBPUSD, USDJPY, USDCAD, XAUUSD), the broker D1 bar equals the H1-built server-day bar exactly: 5,490 to 5,497 common days for FX (4,344 for gold), median relative difference of open and close 0, no day above 1 bp. The D1 label is the NY-close server day across both DST regimes and 2000-2021, so the clock conventions of R3-1/A2' apply. The DST-week overnight-gap ratio (median |gap| in US/EU DST-mismatch weeks over other weeks) is 0.85 for FX, 1.21 for indices, 1.05 for metals, 0.88 for the two oils: no sign of a clock effect (the exotics' 0.42 is a small-sample artefact and they are excluded).
* **Histories (decisive).** Only FX and two metals have DEV depth. **Equity indices (13): every one starts between 2017-12 and 2020-11** (US500, US100, UK100, GER40, JP225, EU50, FRA40 on 2017-12-28/29, US2000 2018-01, HK50 2018-12, AUS200 and US30 2019-02, SPN35 and N25 2020-11): live spans 0.0 to 2.4 years. **Energy:** UKOIL 2016-04, USOIL 2020-12, NATGAS and HEATOIL 2024. **Metals:** XAUUSD from 2004-06 (15.9 live years) and XAGUSD from 2008-11 (11.5) enter; the gold/silver crosses start 2016-17 (live 2.8 to 3.6 years), platinum and palladium 2015-01 (5.3), copper 2024. **Agriculture (6, `.c` futures-style CFDs): 2023 to 2024, no DEV history at all.** DXY 2024. Exotics (15): USDZAR 2006, USDMXN and USDHKD 2007 are the only deep ones; excluded by rule anyway.
* **Holes in the broker's FX cross history.** 13 of the 28 FX pairs have at least one hole of 8 or more missing weekdays, concentrated in 2006 to 2009 and in the crosses of AUD, CAD, CHF and NZD (for example holes of 86 and 17 weekdays in AUDCHF, AUDNZD, CADCHF and EURNZD in 2007 and 2009, 30 in AUDCAD, three in AUDJPY and GBPNZD). The major USD pairs (EURUSD, GBPUSD, USDJPY, USDCAD, USDCHF, NZDUSD, AUDUSD) have none. The masks remove 1 to 4 years from the affected crosses (largest: 2006-2009 for AUDCHF, AUDNZD, CADCHF and EURNZD, 21 % of their live years); no FX pair crosses the 30 % exclusion line. GER40 is the only symbol excluded for masked share (33 %), on top of its span. EURCHF masks 2014-15 (the SNB floor era). The H1 cache holds only five FX pairs and gold, so the cross holes cannot be cross-checked against another source.
* **Weekend-labelled bars:** 60 in FX, 118 in indices, 16 in metals (merged into the next Monday bar). **One-bar spikes dropped:** 13 in indices (all post-2017), 1 each in FX and metals.
* **Duplicates:** FRA40.cash duplicates EU50.cash (dropped; both fail the span rule anyway). No FX duplicates. **Roll-like pattern:** none flagged after the detector revision; the futures-style `.c` and oil series with enough bars are all post-2016, so a roll test has little to bite on in DEV.
* **Real shocks, not errors.** SNB 2011-09-06 (+12 z) and 2015-01-15 (-41 z) and similar are flagged jumps and kept.
* **The recorded D1 `spread` field is not a usable cost.** AUDUSD: nominal 50 points through 2004, then 40, 30, 15, 10, and 1 to 7 points after 2013, zero in 66 % of 2016 and 55 % of 2017. Indices, oil and metals: zero on (median) 96 to 100 % of bars. Costs rest on the class floors (preregistration section 4) except for the pre-2010 FX nominal constants, used as recorded.
* **Swap snapshot (today, not used in DEV):** AUDUSD long -3.59 and short -5.62 points (both sides pay); XAUUSD long -65.78 and short -4.2 points (a long pays far more than a short). The unsigned S1/S2 charges approximate the first pattern and understate the long side of gold.

## 5. What this means for the study

* **Universe from the rule: 30 instruments = 28 FX pairs + XAUUSD + XAGUSD (FX 28, MT 2; equity, energy and agriculture 0).** 47 symbols are excluded (13 equity indices, 5 energy, 7 other metals, 6 agriculture, 15 exotics, DXY), all but the exotics and DXY by the live-span rule. **The viability gate V0 (three classes with at least three instruments each, at least 25 instruments) fails: two classes only, and metals has two instruments.** There is nothing left to retry. The premise of the study, breadth across asset classes on this broker's history, is not available before 2018. On the available history it is "28 FX pairs plus gold and silver", i.e. R3-1 with more FX pairs and one more metal, which found no detectable effect. FX will carry 1 of 2 class budgets; the class-balanced weights give gold and silver together half the risk.
* **Where breadth does exist: after 2017.** Indices, oil, the gold/silver crosses and platinum/palladium have 2.4 to 5.3 years before 2021-06, the agricultural CFDs none; their usable window is the FINAL period and the burnt 2025+ data, which this study may not read. The breadth hypothesis cannot be tested on this broker's data under the DEV/FINAL discipline in force. A test would need external history (futures or index data from another vendor, with its own roll and dividend treatment) or forward data.
* **FX cross holes** reduce the effective FX sample by the masked years; they create no look-ahead (masked years are flat).
* **Costs** rest on assumed class floors (the spread field is empty for non-FX) and nominal pre-2010 FX spreads; every cost level is an assumption, stated in the preregistration and stressed.
* **Survivorship:** the list is today's broker list; the retained CFDs were also chosen by liquidity. Not removable here.
* **FINAL data were not read** (not by the screen, not by the clock check). The FINAL-year masks are computed by the same functions at the owner's FINAL run only.
