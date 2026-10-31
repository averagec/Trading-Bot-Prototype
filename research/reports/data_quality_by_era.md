# Data quality by era (2026-10-29)

Source: `python -m research.data_tools quality --tfs M5 H1 H4 --by-year 2013 2026` (full table in
`research/output/quality_by_year.txt`). Cache after `fetch --max-depth --tfs M5`: M5 about 1,000,000 bars per pair from
2013-05 (XAUUSD 2012-07), H1/H4/M15 from 2000 (XAUUSD 2004). Columns "ok" = no FAIL-level issue in that year.

## What the per-year table shows

| Era | FX pairs (EURUSD, GBPUSD, USDJPY, AUDUSD, USDCAD) | XAUUSD |
|---|---|---|
| 2013 (from May) | clean | FAIL: gaps and 4 Saturday bars |
| 2014-2015 | clean (H4 spread field 2 to 12 % zero) | clean |
| 2016 | all five FAIL on one check: weekly open at UTC hour 4 (expected 21-22); no Saturday bars, no duplicates, few gaps | clean |
| 2017 | all five FAIL on the same check; spread field 8 to 31 % zero (H4 up to 32 %) | clean |
| 2018 | clean | clean |
| 2019 | GBPUSD FAIL: 428 unexplained M5 gaps (about 7,900 bars missing, roughly 1.5 months); the other four clean but show 85 to 104 flagged single-bar spikes each on M5 | clean |
| 2020 | clean | clean |
| 2021 | clean | zero spreads 18.5 % (M5), 69.5 % (H1), 70.5 % (H4) |
| 2022-2024 | clean | clean |
| 2025 | EURUSD spread field 25.8 % zero (the others clean) | clean |
| 2026 (to Oct) | EURUSD spread field 65.5 % zero (M5), 75.8 % (H4); the others clean | clean |

## What I could and could not establish

* The 2016 and 2017 FAIL is one check ("weekly open at UTC hour 4") on otherwise clean bars. The detail view shows
  weekly-open hours `[4, 21, 22]`, so at least one week opened at 04:00 UTC; it does not show how many, and I have not shown that the
  broker clock was shifted. I treat these years as flagged, not broken.
* The 2019 GBPUSD hole is real missing data (M5 bar count 66,442 against about 74,300 for the other pairs).
* The 2019 spike counts (85 to 104 on M5 for four pairs) are not explained here. They could be bad ticks or a real event
  and need a look before 2019 is used.
* Zero recorded spreads are imputed by `prepare_bars` with a same-hour non-zero median, so every cost in those periods is an
  estimate. This matters for the current window too: **EURUSD in 2026 has 65 % zero recorded spreads**, so the EURUSD costs in
  the 825-trade base backtest and the demo-period comparisons are largely imputed.
* I did not check quality for M15 by year, nor M1.

## Decision for the split (proposal; goes into the stage-0 pre-registration)

| Window | Use | Handling |
|---|---|---|
| 2014-01 to 2021-05 | DEV | exclude XAUUSD 2013; report with and without 2016-2017 (flagged), without GBPUSD 2019 (hole), and without XAUUSD (zero-spread years) |
| 2021-06 to 2025-04 | FINAL (unburnt) | report with and without XAUUSD (2021 zero spreads) and with and without EURUSD 2025 (imputed spread) |
| 2025-05 to 2026-10 | descriptive only | EURUSD costs largely imputed |
| 2013-05 to 2013-12 | not used | short, XAUUSD fails |

Remaining work before the stage-0 run: look at the 2019 spikes, and decide whether the imputation rule should become
more conservative (for example the 90th percentile of the same-hour non-zero spread) for the periods with mostly zero
recorded spreads. That is a simulator change, so it needs a pre-registered decision rule and the reviewer.

## H1/H4 screen for the years 2000 to 2013 (2026-10-30; data only, no P/L; `research/output/quality_h1h4_2000_2013.txt`)

Purpose: the slow-horizon study (GAMEPLAN section 13, A2') wants the 2000-2013 H1/H4 history as extra DEV data. Five FX pairs, H1 and H4, per calendar year.

* **Bars are complete.** About 6,200 H1 and 1,550 H4 bars a year (a full 24 x 5 x 52 week); no duplicates, no Saturday bars (one in USDCAD 2009), unexplained gaps 0 to 6 hours a year per pair.
* **Clock is consistent.** The week reopens at 21:00 or 22:00 UTC every year (the DST pattern), plus 1 to 2 holiday reopenings at other hours in some years (the same Christmas / New Year effect as in the M5 years). The "weekly open hour" FAIL flags in 2002, 2006, 2007, 2009 and 2011 come from those single reopenings, not from a clock shift.
* **The spread field before 2010 is a constant, not a record.** EURUSD H1 `spread` is exactly 50 points in 2000-2003, 40 in 2004, 30 in 2005, 20 in 2006-2009 (median equals the 90th percentile); USDJPY 50 / 40 / 30 / 30; USDCAD 50 to 60 / 40 / 30. From 2010 it varies (EURUSD 11, 10, 9 points in 2010-2012, p90 12 to 14) and in 2013 it is 1 (p90 6). The pre-2010 values look like a nominal retail spread for the era, not tick-recorded; they are not zeros, so `prepare_bars` would use them as is. For a slow-horizon study the cost is 0.02 R (H4) or 0.01 R (D1) and a constant 2 to 5 pips is a defensible, conservative level, but it is an assumption: report results by era (2003-2009 constant spread, 2010-2013, 2014-2021) and stress the cost x1.5 and x2.
* **Verdict: usable** for H4 and D1 decision bars from 2003-01-01 (2000-2002 carry 5-pip nominal spreads and the 2002 H1 flag; used only as indicator warm-up). Not used for M5/M15 questions. No row needs exclusion; the 50-point placeholder of 2020-2021 is a different thing and still needs `sentinel_missing`.
