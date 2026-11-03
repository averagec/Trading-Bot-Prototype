# Data notes for the FX carry study (written 2026-11-03, before any run on real returns)

Companion of `research/reports/carry_prereg.md`. Everything here is a structure screen (counts, gaps, levels of the rate inputs, spread-field statistics): **no return of any rule, and no price return of any pair, was computed.**
Screen code: `research/carry_screens.py` (`rates`, `window`, `prices`, `specs`); raw outputs in `research/output/fx_carry_dev/screen_*.txt`. Rates: `research/data_multi/rates/` (FRED/OECD, see `SOURCE.txt`). Prices: `research/data_multi/<SYMBOL>_D1.parquet` + `_spec.json` (read-only, from the owner's background pull).

## 0. Status of the price pull (updated 2026-11-03, after the lead's report that all nine USD pairs are on disk)

All nine D1 files and specs are on disk. The pull log ends with `done: 62 symbols, 15 failed`; the four pairs that had failed (USDCHF, NZDUSD, USDNOK, USDSEK) were retried and are present, which I verified by loading them. The screens below are those of the lead's run of `python -m research.carry_screens prices` (raw: `research/output/scratch/carry_prices_screen.txt`) and of `specs`, plus a structure-only load of the real DEV files through `research.fx_carry.load_dev_world` and `build_panel` (counts and flags printed; no return, weight or P&L was printed or looked at).

| Currency | Pair (orientation) | Bars to 2021-05-31 | First bar | Weekend bars dropped |
|---|---|---|---|---|
| EUR | EURUSD (USD-quoted) | 5,504 | 2000-03-29 | 0 |
| GBP | GBPUSD (USD-quoted) | 5,503 | 2000-03-29 | 0 |
| JPY | USDJPY (USD-base) | 5,503 | 2000-03-29 | 0 |
| AUD | AUDUSD (USD-quoted) | 5,503 | 2000-03-29 | 0 |
| CAD | USDCAD (USD-base) | 5,504 | 2000-03-29 | 1 |
| CHF | USDCHF (USD-base) | 5,502 | 2000-03-29 | 1 |
| NZD | NZDUSD (USD-quoted) | 5,503 | 2000-03-29 | 0 |
| **NOK** | USDNOK (USD-base) | **3,960** | **2006-02-06** | 0 |
| SEK | USDSEK (USD-base) | 5,500 | 2000-03-30 | 0 |

**USDNOK starts 2006-02-06 (the lead's 5,369 bars include the post-DEV years).** With 252 bars of momentum history and 80 bars of covariance required, NOK is first live at the **2007-01-31 decision** (first holding period February 2007); the panel has nine live currencies for the first 49 of 221 periods and ten afterwards. Handling is in the pre-registration (section 2 and pre-run note 1 in its Log): the currency is neither back-filled nor dropped for the whole sample; the study starts with nine currencies and NOK joins when it has history, as the code does by the live flag, and the null relabels only live currencies.

## 1. Interest-rate series (FRED, OECD Main Economic Indicators, 3-month interbank, monthly average, percent per year)

Series: USA IR3TIB01USM156N, EUR ...EZM156N, GBR ...GBM156N, JPN ...JPM156N, AUS ...AUM156N, CAN ...CAM156N, CHE ...CHM156N, NZL ...NZM156N, NOR ...NOM156N, SWE ...SEM156N. `observation_date` is the first day of the month the average belongs to.

| Series | First obs | Last obs | Missing months inside the series | Negative values (first) | Largest monthly move in the DEV window |
|---|---|---|---|---|---|
| USA | 1964-06 | 2026-09 | **1: 2020-04 (the FRED value is "."**) | none | 1.65 pp (2008-11) |
| EUR | 1994-01 | 2026-01 | 0 | 86 months (2015-05) | 0.95 pp (2008-12) |
| GBR | 1957-01 | 2026-01 | 0 | none | 1.69 pp (2008-11) |
| JPN | **2002-04** | 2026-08 | 0 | 40 months (2020-04, tiny, min -0.07) | below 0.75 pp throughout |
| AUS | 1968-01 | 2026-08 | 0 | none (min 0.00) | 1.27 pp (2008-11) |
| CAN | 1956-01 | 2026-09 | 0 | none | 1.08 pp (2008-03) |
| CHE | 1999-07 | 2026-09 | 0 | 135 months (2011-08) | below 0.75 pp throughout |
| NZL | 1973-12 | 2026-09 | 0 | none | 1.18 pp (2008-11) |
| NOR | 1979-01 | 2026-09 | 0 | none | 1.54 pp (2008-12) |
| SWE | 1982-01 | 2026-09 | 0 | 87 months (2015-02) | 1.38 pp (2008-12) |

Findings, each with its handling:

1. **Coverage of DEV.** All ten series cover every month from 2002-04 to 2021-05 except the USA gap above. JPN starts 2002-04, so with the one-month lag the first usable JPY rate is 2002-05; the first decision of the study is 2002-12-31, which reads November 2002. EUR is a euro-area series (pre-1999 values are a synthetic composite, never used). The EUR series ends 2026-01, irrelevant for DEV.
2. **The one gap, USA 2020-04.** Handling (fixed): a missing month is carried forward at most 3 months (causal; a month that is still missing after 3 months would make the currency not live and the accrual raise an error). The April-2020 value is therefore the March-2020 value. Effect: the USD leg of every accrual in April 2020 is mis-stated by the difference between the March and April averages (the USA series moves by 1.18 pp between 2020-04 and 2020-05, so the March-to-April error is of that order or smaller) on a net foreign weight of order 1 for one month, i.e. at most a few basis points of one monthly return; and the USD slot of the ranking at the end of May 2020 (which reads April) uses the March value. I have not quantified the effect on any rule's returns. One month in 221; stated, not corrected.
3. **Negative rates** (EUR from 2015-05, CHE from 2011-08, SWE from 2015-02, JPN from 2020-04 at -0.07 at the most). The accrual formula is signed and symmetric (a long position in a negative-rate currency earns less than the USD leg pays), which is what the economics say for interbank funding; whether the *broker* passes a negative rate on is unknown and is covered by the mark-up stress. Rankings are unaffected.
4. **Flat runs.** JPN has repeated runs of identical values (up to 10 months: 2015-05 for 9 months, 2016-09 for 10, plus 5 to 6 months in 2003-2005 and 2020): the series is at 0.0x percent and rounds to two decimals; not treated as an error (consistent with the zero-rate policy). USA 4 months in 2006-10, AUS 4 months in 2005-10 and 2020-05, NZL 4 months in 2013-05. No series has an unexplained jump: all monthly moves above 0.75 pp fall in 2008-2009 or 2020, except one 0.88 pp move in NOR 2003-06 and one 1.06 pp move in USA 2008-01 (both consistent with policy-rate cuts of those months, which I know only from general knowledge).
5. **No source mixing check is possible from this download.** OECD MEI may have spliced different underlying benchmarks over time (IBOR, TIBOR, NIBOR, STIBOR, a euro-area average); the series is a convenience proxy. A better overnight or policy-rate series would need another download, which was not made (open question 1).
6. **Cross-sectional dispersion by year (what carry has to work with, rate data only).** Range (max minus min of the ten rates) averaged over the months of each year: 2003 5.4, 2004 6.1, 2005 7.0, 2006 7.2, 2007 7.6, 2008 7.2 percentage points; 2009 3.4, 2010 4.5, 2011 4.8, 2012 3.8, 2013 2.9, 2014 3.4, 2015 4.0, 2016 3.1, 2017 2.8, 2018 3.0, 2019 3.0; **2020 1.4 and 2021 (to May) 1.1**. The mean of the ten rates fell from 4.6 (2007) to 0.5 (2017) and 0.1 (2020). Consequence: most of the interest income any carry rule can earn in DEV is in 2003-2008, and almost none in 2020-21; the volatility targeting levers the position up when the book is quiet (cap 4), which raises the weight of the later, low-income years in the Sharpe ratio. Stated in the power section of the pre-registration.

### 1.1 Monthly averages and the lag (decision, fixed)

A monthly average for month m is not known until month m has ended, and the OECD/FRED value is published weeks later. Two separate points:

* **Staleness of an average.** The average of month m-1 is centred in the middle of m-1, so at the end of month m it is on average 1.5 months old. Carry rankings are very persistent (the same currencies are at the top and the bottom for years), so the cost of this staleness is small; the accrual (what is *earned*) uses the average of the month actually held, which is the best available daily-rate proxy and is not a decision input.
* **Lag decided: the decision at the end of calendar month m uses the rate of month m-1 (lag = 1 month), and `build_panel` refuses a lag below 1.** It is the shortest lag the instructions allow and the one with no ambiguity: the month-m average is only complete at the instant of the decision and its publication comes later. A two-month lag is run as a descriptive sensitivity (`rate_lag_2_months`) and cannot change eligibility. A lag of 0 is not a legal setting. Caveat that remains (and review S1): the *publication* delay of the FRED file (the 2026-11-03 fetch had USA to 2026-09 and JPN to 2026-08, i.e. about five weeks or more for some series) (and revisions of the OECD data) means that a decision at the end of month m cannot be reproduced from this source with lag 1; **the lag-2 result is therefore reported next to every lag-1 result** (descriptive, not a gate; pre-registration section 2); a live implementation would read interbank fixings directly, which are public on the day.
* **Monthly average versus point in time.** The average smooths the true path: when policy rates are changing quickly (2008-09, 2020-03) the monthly average leads or lags the end-of-month rate by up to half the monthly change (max moves 1 to 1.7 pp), which blurs both the ranking and the accrual in those months. Over 2003-2021 this is a second-order error for rankings (ranks of the ten currencies change slowly) and a small error in the accrual (monthly averages integrate exactly the rate paid over a month if the rate were the overnight rate, so the *accrued amount* over a holding month is not biased by averaging, only by using a 3-month rather than an overnight rate).

## 2. Accrual convention (fixed; the carry the strategy earns)

* A position in currency c against USD of size w (a fraction of equity, positive = long c) earns per calendar day `w x (r_c / B_c - r_USD / B_USD)` where r is the **monthly-average 3-month interbank rate of the calendar month the day belongs to** (percent / 100), and the day-count basis B is 365 for GBP, AUD, CAD, NZD and 360 for USD, EUR, JPY, CHF, NOK, SEK (money-market convention of each currency). Spot P&L is added separately; total return = spot + accrual. A short position earns the negative.
* **Holding period and weekends.** A trade decided at the close of the last weekday of month m is executed at the open of the next D1 bar (the first bar with a later date) and held to the next month's execution. The accrual of the period is the sum over the calendar days in `[ED_j, ED_(j+1))` where ED is the next *business day* after the decision date, i.e. **every calendar day is accrued once, weekends included, so the weekly triple-swap (Wednesday rollover) is just a different allocation of the same days and the sum over a year is 365 days**; value-date holiday shifts are ignored. The telescoping is tested (`panel.days.sum()` equals the calendar span). A hand-computed two-month example is in the test file.
* **3-month rate as the overnight proxy.** What an FX position accrues is the overnight (tom-next) differential, which for most of the sample sits within a few tens of basis points of the 3-month interbank differential but departs from it in 2007-2012 (bank credit spreads, which differ by country and are not earned by holding a currency) and whenever the market expects a rate change. This is the main weakness of the input and is why the policy-rate series would be a better download (open question 1). It does not make the accrual look-ahead: the accrual of a holding month uses that month's published rate, but no decision uses it.
* **What is not credited:** interest on the collateral (excess-return convention, USD cash is the benchmark), the interest on margin, and any rebate. **Compounding** of the accrual into the notional is ignored (equity base constant, additive monthly returns, as in R3-1).
* **The broker's own swap** (`swap_long`, `swap_short` in the spec files) is a snapshot as of the pull date, in points per lot per day, and includes a mark-up; it is **not used for accrual**. It informs the cost stress only. From the AUDUSD spec (the only one present): long -3.59, short -5.62 points per lot per day, so the implied mark-up (the part common to both directions, `-(long+short)/2`) is 4.61 points per lot per day, about **2.2 % a year of notional at the 2021-05-31 price (0.773) and about 2.6 % at a price near 0.65**; the implied rate differential (`(long-short)/2`) is only +1.0 point, i.e. the broker's side of the trade is 4 to 5 times larger than the interest differential on that pair. (The price conversion uses the last DEV-window close only; no later price was read.) This is the reason the mark-up stress levels in the pre-registration are 1.5 % (S1, gate level, raised from 1.0 % once all nine tables were read) and 2.5 % (S2, close to the top of this snapshot) a year of gross notional. Only the owner can read the other eight swap tables; `python -m research.carry_screens specs` prints the table for every pair once its files exist.

## 3. Calendar and execution conventions that touch the data

* D1 `time` is the broker server day labelled as UTC (as everywhere in this project). Decision date = the last weekday (Monday to Friday) of each calendar month: a pure calendar rule that needs no data. First decision 2002-12-31, last 2021-05-31: 222 decisions, 221 holding periods (the 2021-05-31 decision has no period). The period of the last decision before it is marked at the **close of the last DEV bar** (2021-05-31), because the next open belongs to FINAL and is not loaded.
* The signal uses bars with date <= the decision date. Execution is the open of the first bar after it. No fill at the signal bar's own close.
* Weekend bars (Saturday/Sunday dates) are dropped on load and counted. AUDUSD has none; AUDCAD and AUDCHF (not used) have 10 each.
* A calendar month in which a used pair has a gap of more than 5 weekdays, or a pair that is stale at a decision, is handled by the live flag (stale data: the currency is not live at that decision) and by `OFF_MONTHS` (empty now; to be filled only by a pre-run note, from data screens alone, before the run).

## 4. Price screens (structure only, DEV cut at 2021-06-01)

| Pair | Bars | Weekend bars | Max gap (days) | Gaps over 4 days | Duplicates | Non-positive prices | High/low violations |
|---|---|---|---|---|---|---|---|
| EURUSD | 5,504 | 0 | 4 | 0 | 0 | 0 | 0 |
| GBPUSD | 5,503 | 0 | 4 | 0 | 0 | 0 | 0 |
| USDJPY | 5,503 | 0 | 4 | 0 | 0 | 0 | 0 |
| AUDUSD | 5,503 | 0 | 4 | 0 | 0 | 0 | 0 |
| USDCAD | 5,505 | 1 | 4 | 0 | 0 | 0 | 0 |
| USDCHF | 5,503 | 1 | 4 | 0 | 0 | 0 | 0 |
| NZDUSD | 5,503 | 0 | 4 | 0 | 0 | 0 | 0 |
| USDNOK | 3,960 | 0 | 4 | 0 | 0 | 0 | 0 |
| USDSEK | 5,500 | 0 | 4 | 0 | 0 | 0 | 0 |

(Bars are as the screen counts them before dropping the weekend bar; the loader leaves 5,504 for USDCAD and 5,502 for USDCHF.) **All nine pairs have no gap longer than 4 days (a long weekend), no duplicate, no impossible price: no pair needs an `OFF_MONTHS` entry and the set is empty.** The crosses carry gaps of 19 to 136 days (AUDCAD, AUDCHF, AUDJPY, AUDNZD, CADCHF, CHFJPY, EURCAD, EURJPY, EURNZD) and are not used.

### 4.1 The D1 `spread` field is not a usable cost record on its own (AUDUSD shown; the other eight below)

| Year | AUDUSD median spread (points) | AUDUSD share of zero spreads | AUDUSD share equal to 50 |
|---|---|---|---|
| 2000-2004 | 50 constant | 0 | 1.00 (a nominal constant; not the placeholder, which starts 2018 in `dataset.sentinel_mask_era`) |
| 2005-2009 | 40, 40, 30, 30, 30 | 0 | 0 |
| 2010-2012 | 15, 15, 10 | 0 | 0 |
| 2013-2014 | 1, 1 | 0.12 in 2014 | 0 |
| 2015-2017 | 7, 9, 9 | 0.10, 0.66, 0.55 | 0 |
| 2018-2021 | 1, 4, 1, 1 | 0.01 | 0 |

Readings. (a) Before 2010 the field is a nominal constant per year (5 pips to 3 pips); used as recorded it is the right order of magnitude for retail spreads of the time. (b) From 2013 the field is unreliable: a median of 1 point in 2013-14, 2018, 2020 and 2021 is a placeholder, not a quote, and in 2016-17 the zero share is 55 to 68 % of the bars. The 50-point sentinel logic (`dataset.add_clean_spread`: zero and sentinel values replaced by the median of clean recorded values of the same pair and year) fixes the zeros but cannot fix a year whose clean median is itself a 1-point placeholder. (c) AUDCHF records exactly 50 on 70 to 90 % of bars in 2010-2012 (before the sentinel era of the helper), a second kind of constant. **Decision (pre-registration section 5): the cost spread of a trade is the larger of (i) the trailing 20-bar median of the clean recorded D1 spread and (ii) a floor of 1.0 bp of notional for the seven large currencies and 4.0 bp for NOK and SEK**, with ×2 and ×4 stress. At a monthly turnover of about 1 to 2 of gross weight the floor costs about 0.1 to 0.3 % a year, small against the mark-up question; the floor values are assumptions, not data.

### 4.2 The other eight pairs' spread fields (screen of the lead's run)

Same pattern as AUDUSD: constant nominal spreads before about 2010 (EURUSD 50 points to 2003 then 40, 30, 20; GBPUSD 50 to 2004; USDJPY 50 to 2003, 30 in 2005-09; USDCAD 50 to 2004; USDCHF 50 from 2002 to 2006, 40 in 2007-09), medians of 1 point in 2013-14, 2018 and 2020-21 (placeholder), and zero-spread shares of 56 to 74 % of bars in 2016-17 in every pair. Specific findings:
* **NZDUSD**: zero spread on 95 to 100 % of bars 2000-2005 and about half in 2006-08 (then 80 points in 2000-01, 70 in 2002, 40 from 2006). The year-median repair has no clean value for 2003-2005, so it falls back to the whole-window median of clean values (hs median 3.0 bp, maximum 20 bp in the loaded series).
* **USDSEK**: zero spread on 100 % of bars 2000-2009, then **750, 749 and 674 points in 2010-12** (about 9 bp of notional), then 12 and 8 points in 2013-14 and 129 in 2015-17. **USDNOK**: zero spread on 100 % of bars 2006-09 and 82 % in 2010, then 40, 40, 68 points, **395 and 394 points in 2013-14**, 125 in 2015-17. These swings are not market spreads (a 60-fold change in one year); they are the recorded field of a feed that changed. The study does not use these values as costs on their own: the floor (4 bp for NOK and SEK) is applied, and in the loaded series the cost spread of both currencies sits at the 4.0 bp floor in the median, with a maximum of 12.5 bp (SEK) and 7.8 bp (NOK).
* **GBPUSD** has 9 % of its 2019 bars equal to 50 points: the known 2019-01..03 sentinel, caught by `add_clean_spread`. **USDCAD** has 35 % zero spreads in 2019.
* Effective cost model, as loaded: the median cost spread of the loaded series is the 1.0 bp floor for EURUSD and GBPUSD, 1.2 bp for USDJPY and USDCAD, 1.4 for USDCHF, 1.6 for AUDUSD, 3.0 for NZDUSD and the 4.0 bp floor for NOK and SEK; that is, **the cost used is mostly the pre-registered floor, not data**. At the study's turnover this is a cost of about 0.1 to 0.3 % a year; the x2 and x4 stresses are reported.
* **Disclosure:** the cleaning repairs a zero spread from same-year (or, failing that, whole-window) medians of clean values, as in A2'/R3-1. A repaired cost therefore depends on later bars of the same year; this affects costs only, never a signal or a weight, and is not part of the no-look-ahead test (which is about decisions).

### 4.3 Broker swap snapshot, all nine pairs (the lead's `specs` run; today's table, not history)

| Pair | swap_long | swap_short | implied mark-up (points per lot per day) | implied differential | mark-up % a year of notional (2021-05-31 price) |
|---|---|---|---|---|---|
| EURUSD | -10.09 | 0.52 | 4.79 | -5.30 | 1.43 |
| GBPUSD | -6.40 | -3.67 | 5.04 | -1.37 | 1.29 |
| USDJPY | 4.24 | -20.53 | 8.14 | 12.39 | 2.71 |
| AUDUSD | -3.59 | -5.62 | 4.61 | 1.02 | 2.17 |
| USDCAD | 0.94 | -12.82 | 5.94 | 6.88 | 1.80 |
| USDCHF | 1.96 | -13.54 | 5.79 | 7.75 | 2.35 |
| NZDUSD | -4.84 | 0.10 | 2.37 | -2.47 | 1.19 |
| USDNOK | -39.90 | -3.85 | 21.88 | -18.02 | 0.96 |
| USDSEK | 12.05 | -84.63 | 36.29 | 48.34 | 1.60 |

The implied mark-up is `-(long + short)/2`; across the nine it is **0.96 to 2.71 % a year of notional, mean 1.72, median 1.60**. (The conversion uses the last DEV-window price, so today's percentages differ by the move since then; NOK and SEK convert from the quote currency at 2021 prices and are the least reliable.) The pre-registered stress levels are S1 = 1.5 % (below five of the nine pairs; raised from 1.0 % by pre-run revision 1) and S2 = 2.5 % (above all but JPY); a per-pair snapshot stress using these nine numbers is reported as a descriptive sensitivity (`markup_snapshot_per_pair`).

## 5. Why the universe is the ten G10 currencies through nine USD pairs

The 28 FX pairs of the pull plus exotics offer far more pairs than currencies; a currency-level portfolio (weights on the ten currencies, USD as the numeraire, summing to zero) has the same exposure whether it is implemented through USD pairs or crosses, and the crosses cost a spread each way on a single instrument while the USD legs cost two. The crosses available have long data holes (above); the USD pairs are the most liquid and best recorded. Exotic currencies (MXN, ZAR, TRY, PLN, ...) have no rate series in the download and are outside this study; "without exotics" in the gates is therefore **without NOK and SEK**, the two least liquid G10 currencies and the two with the widest spreads. The fixed universe: USD (numeraire), EUR, GBP, JPY, AUD, CAD, CHF, NZD, NOK, SEK.

## 6. Open data questions (nothing downloaded; each needs the owner's yes)

1. **A better short-rate series (declined by the lead on 2026-11-03; kept here as a limitation, not a dependency).** The 3-month interbank rate contains term and bank-credit components that an FX position does not earn (2007-2012 especially). The cleaner series would be the central-bank policy rate or the overnight call-money rate of each country (on FRED the OECD "immediate rates" family, IRSTCB01xx / IRSTCI01xx style identifiers, monthly; I have not checked that the identifiers exist, I cannot browse without being allowed to download). Would change the accrual and, slightly, the ranking; same lag rule.
2. **JPN before 2002-04** is not needed (DEV starts 2003-01). Not requested.
3. **Rates for other currencies** (exotics) would extend the universe; they are outside this study and would need their own pre-registration.
4. (closed for the DEV design) the nine swap tables were read by the lead (section 4.3); a re-read just before any FINAL decision would refresh the numbers.
5. (closed) The failed D1 symbols among the nine pairs were retried and are present.

## 7. Freeze manifest (revision 2)

`python -m research.fx_carry freeze` hashes, in `research/reports/carry_prereg.sha256`, the pre-registration, `research/fx_carry.py`, `research/carry_screens.py`, the nine `<PAIR>_D1.parquet` files and the ten `rates/<CTRY>.csv` files (bytes only; the parquets also contain post-DEV bars that the loaders never load, and hashing them reads no value). `dev` refuses unless every hash matches and the manifest lists exactly these files. Replacing a data file (for example by a re-pull of the terminal history, which appends and can change old bars) therefore needs a logged re-freeze before the run.
