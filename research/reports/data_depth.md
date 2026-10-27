# Data depth: why M5 stops at 2025-06-19, how to fix it, and the tooling built (2026-10-26)

Nothing in this work edited a terminal setting, restarted MT5, placed an order, read credentials or downloaded external data.
All terminal calls were read-only API calls. **One side effect to know about is in section 1.4.**

## 1. What was found

### 1.1 The limit (confirmed three ways)
| Source | Value |
|---|---|
| Terminal runtime `terminal_info().maxbars` | **100000** |
| `%APPDATA%\MetaQuotes\Terminal\D0E8209F77C8CF37AD8BF550E51FF075\config\common.ini`, `[Charts]` | `MaxBars=100000` (also `PreloadCharts=1`); terminal build 6230 |
| Behaviour | `copy_rates_from_pos(..., 99999)` works, `100000` and `120000` fail with `Invalid params` |

The cap is **per timeframe**, counted from the newest bar:
* **M1 is capped too**: 99,999 M1 bars is only ~100 days (EURUSD from 2026-07-20). So M1 cannot be used to rebuild older M5 through the API.
* M5 99,999 bars -> 2025-06-19 (XAUUSD 2025-05-23). `copy_rates_from(date_in_the_past)` does not escape the cap: it clamps to the oldest capped bar (returns 1 bar), or fails.
  `copy_rates_range` on 2025-01 returns 1 bar, i.e. it is also clamped.
* The cap is *not* a data shortage. H1 at 99,999 bars reaches **2010** for every pair, so H1 depth is available now. The current `BARS_WANTED` asks for only 12,000 H1 (to 2024-11).
  M15 at 99,999 bars would reach ~2022. Both are free to deepen without touching MT5.

### 1.2 What the broker really holds (evidence)
* **Daily bars from the server:** EURUSD and GBPUSD D1 from 2000-03-29; USDJPY/AUDUSD/USDCAD D1 requests of 6,000 bars reach 2003-09 (their weekly/monthly bars start 2000-03); XAUUSD D1 from 2004-06-11.
* **The terminal's local M1 store** (`bases\FTMO-Demo\history\<SYM>\<year>.hcc`; M1 base, ~21-23 MB per full year, consistent year to year) now contains **2000-2026 for EURUSD, GBPUSD, USDJPY, AUDUSD, USDCAD and 2004-2026 for XAUUSD**. That is ~26 years of M1 on disk, already synced.
  Files before my probes covered only 2023-2026 for the non-EUR/GBP pairs.
* So: **the server evidently holds M1 back to 2000 (FX) / 2004 (gold).** What I could NOT verify is the *content* quality of those old years (the `.hcc` format is proprietary and I did not reverse-engineer it) and whether MT5 serves them as M5 bars once the limit is raised. That is standard MT5 behaviour (M5 is built from the M1 base) but I cannot test it without changing the setting.
  Practical consequence: raising the limit should be near-instant (data is already local); no multi-hour download is expected.

### 1.3 Other facts the investigation produced
* The live bot calls `mt5.initialize(...)` once (`mt5_connector.connect`, called once from `main.py:66`) and I found no reconnect loop. **After an MT5 restart the bot probably needs to be restarted too.** Not verified at runtime; treat as likely.
* Broker clock behaves as documented: weekly open at UTC 21/22 (FX) and 22/23 (gold); H4 bars start on server 00/04/08 h, i.e. at UTC 21/01/05... in US summer and 22/02/06... in winter.
* `resample_bars` (new) rebuilds M15/H1/H4 from the cached **M5** and matches MT5's own M15/H1/H4 bars **exactly** (0 differences in 33,575 M15 / 8,395 H1 / 2,098 H4 bars for EURUSD; same for USDJPY and XAUUSD). This validates the broker-time bucketing used by the importer on real data.
* A real append-fetch into a scratch copy of the cache: 99,912 overlapping bars re-fetched, **0 changed** (history is stable), +87 new, cache grew to 100,086 bars (> the 99,999 cap) with no duplicates.

### 1.4 Side effect of my probes (disclosure)
My D1/W1/MN1 depth probe asked the terminal for 6,000+ daily bars. To build them the terminal **downloaded ~2.7 GB of M1 history into its own data folder** (`bases` is now 3.3 GB; `C:` has 46 GB free, 91% used). This is the terminal's normal on-demand sync (the earlier H4 fetch did the same for EURUSD/GBPUSD) and no setting changed, but I did not anticipate the size. I did not delete anything and recommend you do not: those files are what makes a raised limit work immediately. `data_tools probe --server-depth` is therefore opt-in and warns about it.

## 2. Step-by-step: raise the limit safely (you do this; I did not)

Recommended value: **1,000,000** (M5 then covers ~13 years; 400,000 is ~5.4 years). "Unlimited" works but wastes RAM if M1 charts are open (1M M1 bars is only ~2.7 years).

0. **Be flat.** No open positions and no pending orders (a restart interrupts breakeven/expiry management; SL/TP already at the broker stay live). Pick a weekend, e.g. Saturday: markets are closed.
1. **Stop the live bot** (Ctrl+C its console) so it does not try to trade/log in while the terminal is down.
2. Optional backup: copy `research\data\` somewhere, and copy `...\Terminal\D0E8209F77C8CF37AD8BF550E51FF075\config\common.ini` (it contains account details: keep it private).
3. In MT5: **Tools -> Options** (Ctrl+O) -> **Charts** tab -> **Max bars in chart** -> type `1000000` (or choose Unlimited) -> **OK**.
4. **File -> Exit**, then start MT5 again and let it log in (MT5 only applies this option at startup).
5. Verify (read-only): `python -m research.data_tools probe`. Expect `terminal Max-bars (runtime) = 1000000` and `common.ini [Charts] MaxBars = 1000000`. If runtime is still 100000 the change did not apply.
6. Restart the live bot and confirm it logs "Connected to MT5".
7. Back up `research\data` (the old cache is your reproducible baseline for RESULTS.md), then:
   `python -m research.data_tools fetch --m5-bars 400000 --tfs M5 M15 H1 H4`
   (add `--max-depth` for the maximum). Append mode keeps everything already cached. Then `python -m research.data_tools quality --tfs M5 H1 H4 --detail`.
8. Expect: M5 now reaching back ~5 years (EURUSD/GBPUSD more if you ask); parquet files of ~10 MB per pair; backtests ~4x slower than now (use `run.py --start` to trim); the terminal using somewhat more RAM; the first fetch taking a few minutes while M5 is built from the local M1 base. If any pair returns fewer bars than asked, that is the server's real depth for it.

**Without any terminal change** you still get value today: `python -m research.data_tools fetch` (append) accumulates ~one week of M5 per week; a weekly `fetch --tail` (Windows Task Scheduler; I did not create one, it needs your OK) prevents bars from falling off the 99,999 window. Deeper H1/H4/M15 for warm-up: `fetch --tfs H1 M15` with `bars_wanted` raised.

## 3. If you also want third-party M1 (optional, needs your action)

Files to obtain (you download; I did not, and check each vendor's licence): **M1, BID prices, one file per symbol** for EURUSD, GBPUSD, USDJPY, AUDUSD, USDCAD, XAUUSD, covering the longest range you want **through to today** (it must overlap the MT5 window; >= 3 weeks, ideally the whole window, so both DST seasons are compared).
* Dukascopy (e.g. via its historical data export or a downloader like dukascopy-node): CSV `Gmt time,Open,High,Low,Close,Volume`, timezone **UTC**, price side **bid**. Optionally also the **ask** file for a vendor-derived spread.
* HistData.com "ASCII M1": `YYYYMMDD HHMMSS;o;h;l;c;v`, timezone **EST fixed -5 with no DST** (`--tz EST`). Bid-based; per-year zip files must be unzipped and concatenated per symbol (or imported one by one; imports merge).
* Put them in a new folder `A:\Trader-Bot\forex_bot\research\import_raw\` named `<SYMBOL>_M1_bid.csv`.

Then per symbol (dry run first; the default never writes):
```
python -m research.data_tools import --symbol EURUSD --file research\import_raw\EURUSD_M1_bid.csv --tz UTC
python -m research.data_tools import --symbol EURUSD --file research\import_raw\EURUSD_M1_bid.csv --tz UTC --commit
```
Exit code 2 and "REFUSED - nothing was written" if validation fails. Practice on synthetic data: `python -m research.data_tools synth --out <dir>`.

## 4. Tooling built (all new except `data.py`)
| File | Purpose |
|---|---|
| `data.py` (edited, owned) | `fetch_history(append=True default, mode="full"/"tail", max_depth)`, `merge_bars` (dedupe, idempotent, `prefer`, `only_outside`, hole detection), atomic parquet writes, vectorised `server_to_utc` (verified identical to the old one) + `utc_to_server`, `rates_to_frame`, `prepare_bars` now logs a one-time quality verdict per symbol/timeframe and understands synthetic-spread flags. `fetch_data.py` is untouched and now appends because it calls `fetch_history`. |
| `data_quality.py` | `quality_report`, `quality_table`, `format_quality`: duplicates, ordering, grid alignment (broker clock for H4), gaps classified weekend / holiday (incl. Easter) / session break / unexplained, Saturday and off-hours bars, weekly-open hours, OHLC validity, flat runs, reverting spikes / wick spikes / unexplained jumps, zero-spread share overall, by month and last 30 days, synthetic-spread and import share. |
| `data_import.py` | `read_external` (Dukascopy, HistData, MetaTrader CSV, generic CSV/parquet; explicit timezone: UTC, broker, fixed offset, EST, IANA with DST, per-row `GMT+hhmm` suffix), `pair_bid_ask`, `to_bid`, `resample_bars` (broker-clock buckets), `validate_bars`, `import_external`. |
| `data_tools.py` | CLI: `probe`, `fetch`, `quality`, `import`, `synth`. |
| `synth_vendor.py` | Synthetic M1 / MT5 cache / vendor-file generator for tests. |
| `tests/conftest.py`, `test_data_append.py`, `test_data_quality.py`, `test_data_import.py` | 59 new tests. |

Validator behaviour: per timeframe (M5, M15, H1, H4) against real MT5 bars in the overlap, it checks bars missing / extra, price scale, median and p99 close difference and high/low difference (in units of that timeframe's median range), signed bias (ask/mid mislabelled as bid), return correlation, a +-12 h time-shift search (reports "+5h" style hints) and separate summer/winter comparisons (a wrong DST rule fails one season). **Any failure refuses the import and writes nothing.** Imported bars never overwrite MT5 bars and never fill holes inside the MT5 span; they carry `src="import:<name>"` and `spread_synth=True`; their spread is the MT5 hour-of-day median (or a fixed/scaled value, or a vendor-derived one) and is excluded from `prepare_bars` imputation statistics. `import_log.json` records file hash, timezone and counts.

**Tolerances are reasoned, not calibrated** (no real vendor file existed): look at the printed metrics on the first real file; do not loosen limits to make a file pass. The validator only proves agreement where the vendor overlaps MT5 (M5 from 2025-06; H1 from 2024-11; H4 from 2023-08); older years are assumed to share the vendor's clock and quality.

## 5. Tests
`python -m pytest research/tests -q` -> **153 passed** (94 existing + 59 new, ~100 s). New tests include: DST known values and oracle equality, append across a sliding cap using a stubbed MT5, tail and max-depth requests, merge idempotence, every file format x timezone recovering the true UTC clock, refusal of: wrong timezone, DST mishandling, ask-as-bid, wrong scale, noisy feed, no overlap, weekend-filled source, missing bars; merge leaves MT5 rows byte-identical; commit-then-recommit adds nothing; a refused commit leaves cache hashes unchanged; real-cache check that resampling M5 reproduces MT5's H1/H4 exactly.

## 6. Data-quality findings on the current cache (6 pairs, M5, 2025-06-19 -> 2026-10-26)
* No duplicates, no off-grid bars, no Saturday bars, no OHLC inconsistencies, weekly open at UTC 21/22 (gold 22/23), H4 on the broker clock. **Structurally clean.**
* **EURUSD recorded spread is a regime, not noise:** zero-share by month: 0% (Jun-Aug 2025), 48% (Sep), 84-88% (Oct 2025-Feb 2026), 44% (Mar), 0% (Apr), 11% (May), 84-88% (Jun-Oct 2026). Mean recorded spread fell from ~3.3 pts to ~1.3 pts across that shift. The other five pairs are ~0% zero. `prepare_bars` imputes 2.0 pts (hour median) for the zero bars, which is higher than the recorded mean of recent months (~1.3 pts incl. zeros), so EURUSD costs after Sep 2025 are, if anything, modestly overstated; before Sep 2025 recorded spreads (~3.3) exceed the 2.0 imputation applied to few bars. Either way EURUSD cost is a modelled estimate. The 21:00 UTC rollover bar median is 24 pts.
* **A 95-minute hole on 2026-10-20 06:00-07:35 UTC hits EURUSD, GBPUSD, XAUUSD and the others identically**: a terminal/connection/broker outage, not a market event. Smaller unexplained gaps: ~5 per pair (<=29 missing bars in total per pair), mostly 2025-12-02 09:25-10:25.
* Reverting single-bar spikes: EURUSD 1 (2025-07-15), USDJPY 5 (2026-05-04 03:50, 18:55, 05-06 07:55, 06-03, 06-18; moves of ~1.2 yen and back: looks like genuine intervention-style volatility, not bad ticks), XAUUSD 1 wick. 0 for GBPUSD, AUDUSD, USDCAD. Worth knowing because stops placed near those bars are triggered in the sim.
* Gold has daily 1 h breaks (classified as session breaks) and US-holiday early closes; Good Friday 2026 gap recognised.

## 7. What more history can and cannot do (sample size)
From RESULTS.md: 825 trades, expectancy -0.205 R, CI half-width ~0.105 -> per-trade SD ~1.5 R. At ~48 trades/month: 5 years ~ 2,900 trades (CI half-width ~ +-0.055 R); 13 years ~ 7,500 (+-0.035 R). Detecting a true +0.05 R edge at 80% power needs ~7,000 trades; +0.15 R needs ~800. More years also add regimes (2022 hiking cycle, 2023-24 ranges) that 16 months cannot. Third-party bars carry slightly different highs/lows, so same-bar SL/TP ordering and fills will differ somewhat from the broker's; spreads on imported bars are synthetic, so run cost-sensitivity (`spread_mult`) on any long-history result.

## 8. Not verified / needs your permission
* Needs **you**: changing the MT5 option and restarting MT5 (and the bot); downloading vendor files; scheduling a recurring fetch; deleting/keeping the 3.3 GB terminal history.
* Not verified: that MT5 serves M5 before 2025-06 after the limit is raised (needs the setting change); content quality of pre-2023 M1; that the bot cannot reconnect on its own; validator tolerances against a real vendor file; the vendor file formats beyond what is documented (tested on synthetic files written in those layouts); that FTMO spreads in 2023-24 resemble the 2025-26 hour medians used for synthetic spreads.
* I did not use the strategy tester or the proprietary `.hcc` files; both are possible alternatives (an EA run in the tester that writes M1 to CSV, or decoding `.hcc`) but untested and not needed if the limit is raised.
