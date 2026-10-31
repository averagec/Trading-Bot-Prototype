# AI approach to finding an edge: data feasibility, design, and the one learnability test (2026-10-29)

DEV only (decision bars 2014-03 to 2021-05, out-of-fold 2016-01 to 2021-05). FINAL (2021-06 to 2025-04) was not read: the loader refuses any end after
2021-06-01. Pre-registration: `research/reports/ai_edge_prereg.md` (written before any model was fitted). Code: `research/ai/`, tests `research/tests/test_ai_*.py` (39 pass).
Classical ML only; no LLM. Intervals are 95 % week-block bootstrap (calendar weeks shared by all pairs) unless stated.

## 1. Executive verdict

1. **No learnable directional structure is established net of costs.** On the pre-registered test (net-R sign of a stateless triple-barrier trade, H1 bars, five FX pairs, purged
   walk-forward, three pre-declared model settings) the criteria were not met: the improvement of the directional features over a non-directional model (D1, log-loss) has a
   Holm-level lower bound of -5.8e-4 (LR), -5.2e-4 (HGB_S) and -1.9e-6 (HGB_M, a near miss). HGB_M and LR do beat the block-permutation null (z 4.8 and 3.2, above all 40 and 100 replicates),
   so the association is not noise, **but it comes from one half-year**: 2019H2 has directional AUC 0.61 to 0.70 and D1 +0.005 to +0.019; every other block is between 0.40 and 0.57, and without 2019H2 the
   pooled directional AUC is 0.497 / 0.495 / 0.501 and D1 is negative for all three settings (post hoc, flagged as such). I could not explain 2019H2 (a regime, or a feature of that data period; not verified).
2. **Cost, not model class, is the binding constraint at the current style.** The label's cost is 0.047 R at H1 (22-pip stops), 0.086 to 0.10 R at M15 (stop floor binds on 49 % of bars),
   0.021 R at H4 and 0.0085 R at D1. A directional edge visible at the 1 to 4 hour horizon is 0.5 to 1.4 pips gross per trade against about 1.0 pip of spread plus commission.
3. **What does exist and is large is non-directional**: volatility and its time-of-day pattern are highly forecastable (Spearman IC 0.4 to 0.5 for forward range against ATR state, hour sin/cos), and
   short-horizon returns mean-revert slightly (IC -0.03 at 1 h, variance ratio 0.925 at 24 h). Neither is an edge by itself; they are inputs for cost control (when and how wide to trade).
4. **Recommendation**: do not spend the next weeks on richer models of H1/M15 direction, on SMC meta-labelling (stage 0, corrected null: skill +0.006 R, SE 0.020, upper bound 0.044, KILL), on sequence models or on LLMs.
   Spend them on (a) re-running exactly this protocol at H4 and D1 decision horizons, where cost is 2 to 10 times smaller in R and the cache has 2000-onward history (needs a quality screen of the old H1/H4 years), (b) a volatility/time-of-day cost-control layer,
   (c) a second data source for the 2019H2 question (GAMEPLAN D3). The honest prior after this work is that a net edge in OHLC + spread + time features is unlikely at intraday horizons and small at best at slow ones.

## 2. Data inventory (DEV, descriptive; tables in `research/output/ai/inventory/`)

* Five FX pairs + XAUUSD. M5 about 598,000 bars per pair from 2013-05 (about 548,000 from 2014-01 to 2021-05), M15/H1/H4 from 2000 (XAUUSD 2004). Bar times are OPEN times, UTC. No M1. The week opens 21:00 UTC (250 of 387 weeks) or 22:00 (135), closes 19:00 to 21:00; 24 hours are covered evenly (4.0 to 4.2 % of bars per hour); Christmas reopenings at 04:00 (2 weeks).
* Gaps (M5, outside weekends): EURUSD 442 (48 unexplained), USDJPY 426 (45), AUDUSD 437 (34), USDCAD 449 (33), **GBPUSD 1,063 (460 unexplained, about 10,900 missing bars, mostly the 2019 hole)**, XAUUSD 4,071.
* **Spread field, new finding.** From 2020-05 the FX `spread` value is exactly 50 points on 14 to 93 % of M5 bars per month (EURUSD 58 % of 2020 and 86 % of 2021, GBPUSD 33 % / 69 %, USDJPY 26 % / 81 %, AUDUSD 26 % / 64 %, USDCAD 19 % / 0.6 %), and 1 point on most of the rest; GBPUSD also 2019-01..03. `spread_regimes.csv`.
  A 5-pip EURUSD spread is not credible; I treat 50 as a sentinel for costs (`dataset.sentinel_mask`) but **could not verify** it without the terminal. The simulator uses this field as is (it only imputes zeros), so any simulator result in 2020-05..2021-05 (including stage 0's 2020 and 2021 years) paid a 5-pip spread or was filtered by the spread gate on a large share of bars. Not checked here; for the lead.
  Recorded zeros (2017: 9 to 21 % per pair) and the typical level (7 to 11 points in 2015-2017, 2 to 15 in 2018-2019) are the other regimes; costs by era are in `cost_per_r_summary.csv`.
* **Returns**: lag-1 autocorrelation (pooled five pairs): M5 -0.042 [-0.053, -0.033], M15 -0.021 [-0.028, -0.016], H1 -0.005 [-0.019, +0.011], H4 -0.008 [-0.038, +0.014], D1 0.000 [-0.043, +0.041]. Variance ratio (1 = random walk), H1 base: 4 h 0.995 [0.971, 1.020], 12 h 0.956 [0.933, 0.980], 24 h 0.925 [0.895, 0.957], 48 h 0.881 [0.802, 0.951]; clipped-return version 0.934 and 0.928. M5-based ratios go lower (0.84 at 24 h) but mix in bar-level noise (bad ticks, rollover); bar data cannot separate noise from true mean reversion. Mild mean reversion at every intraday horizon; none measurable at D1.
* **Volatility**: clustering is strong (autocorrelation of hour-of-week-deseasonalised |H1 return| 0.23 [0.19, 0.27] at 1 h, 0.14 [0.11, 0.17] at 24 h, 0.13 at 48 h). Seasonality is large: EURUSD 2017-2020 mean H1 range is 7 pips at 04:00 UTC and 23 pips at 14:00 (3.2 times); weekdays vary less (13.8 Monday to 16.6 Thursday). The recorded spread medians are nearly flat across the day in this broker's history, so **cost per R varies with the hour through the stop** (EURUSD 0.035 R at 20-23 UTC to 0.056 R at 06 UTC), not through the spread.
* **Cost in R by decision horizon** (stop = max(10 pips, 1.5 x ATR14 of that timeframe), spread + 2 x $2.50 commission; `cost_by_horizon.csv`): M15 0.086 R (stop 10 pips, floor binds 49 %), H1 0.047 R (22 pips), H4 0.021 R (48 pips), D1 0.0085 R (126 pips). The live bot sits in the M15-like regime (pinned 10-pip stops), which is why its random-direction version loses about 0.12 to 0.15 R.
* **What a learner can exploit (cheap labels, next h H1 bars, out-of-fold, LR / HGB_S)**: direction AUC 0.530 [0.526, 0.534] at 1 h, 0.527 [0.520, 0.535] at 4 h, 0.514 [0.503, 0.526] at 24 h (adding the non-directional features changes nothing). Strongest single ICs: distance below the 20-bar high +0.033 (z 11 at 1 h), 1-bar return -0.032 (z -11), i.e. short-term reversal toward the recent range. Family size 39, Bonferroni |z| > 3.2: 12 direction tests pass. Forward range: ICs -0.51 (vol_ratio), -0.44 (ATR percentile), -0.43 (hour_cos), sign partly a normalisation effect (range divided by the current ATR); the information is the seasonal and state forecast of volatility.
* **Does it survive the spread** (top decile long, bottom decile short of the OOF score; 0.5 pip commission + recorded spread, about 1.0 pip): 1 h gross +0.51 pips [0.30, 0.71], net -0.56 [-0.77, -0.34]; 4 h gross +1.37 [0.70, 2.04], net +0.41 [-0.28, +1.08] (HGB_S: net -0.17); 24 h gross +3.56 [0.96, 6.33], net +2.56 [-0.04, +5.38] pips = +0.11 R [-0.01, +0.23] against a 1.5 ATR stop. The mean absolute move is 7 times (1 h), 15 times (4 h), 39 times (24 h) the cost. **By year the 4 h and 24 h gross comes from 2019** (4 h: +8.3 pips in 2019 against -0.1 to +0.8 in other years; 24 h: +18.0 against -1.6 to +5.3), the same 2019H2 as above; broad inside it (all six months, 4 of 5 pairs, GBPUSD -1.5, strongest at 12-14 UTC, top 1 % of trades = 11 % of the pips), so it is not an outlier artefact.

## 3. Label design

* **Cheap**: sign of the next-N-bar return. Costless, symmetric, high-powered; it measures predictability, not profit.
* **Realistic (the one that matters)**: net R of a stateless triple-barrier trade at each decision bar and for both sides: entry at the last M5 close (long pays the spread), stop = max(10 pips, 1.5 ATR14), take-profit 3R, stop wins ties, gap-through fills at the open, 48 h time barrier, Friday 20:00 flatten (decisions from Friday 20:00 invalid), commission as R, spread from `spread_clean_pts`.
  It matters because the win probability needed to break even is (1 + cost)/4: base win rate here is 29.0 %, and a model can easily raise AUC on a label geometry (wider stop relative to volatility lowers P(TP)) without raising expected R. That is why the evaluation is the gain **over a non-directional model with the same geometry features**.
  `labels.py`; tests compare R and exit reason with `research/sim.py` for 30+ random trades (tolerance 0.03 R), plus hand-computed barriers, tie rule, gap fill, Friday flatten, censoring, and a check that bars after the exit cannot change the label.
* Label statistics (pooled long+short, no direction favoured): 430,172 labels, mean net R -0.059, sd 1.57, win 29.0 %; exits TP 18.1 %, SL 67.1 %, time 3.4 %, Friday flatten 11.5 %; average uniqueness 0.085 (sum of weights 36,700). The pooled mean of -0.059 R is close to the first-order cost (0.051 R): a direction-neutral trade at this geometry has almost no structural drag besides cost.
* Uniqueness weights (Lopez de Prado) per label per pair; training uses them, evaluation does not, and every interval is a week-block bootstrap, which absorbs the overlap and the shared USD factor.
* Not in the label: breakeven (the H6 framework removes it), lot rounding, JPY P&L conversion (second order), validator rejections. The label spec (k = 1.5, 48 h) was fixed before any result and is one of many; k, the target multiple and the horizon are the obvious axes of a later study.

## 4. Features (`features.py`; all causal)

N (non-directional): hour and weekday of T (sin/cos), hours to Friday flatten, ATR percentile (1000 bars), log(ATR14/ATR100), H4 ATR percentile, spread relative to its own trailing median (recorded values; the sentinel handled by a trailing rule), missing-spread flag, stop in ATR units, first-order cost over stop.
S (directional): returns over 1, 4, 12, 24, 120 bars in ATR units, distance to the 20/120-bar high and low, H4 trend and 6-bar return from closed H4 bars, USD factor over 4 and 24 bars (other four pairs, oriented to USD). Short rows carry mirrored features, so one symmetric model serves both sides.
The live SMC signals are not used. No-look-ahead tests: truncation (cut each timeframe at a time inside a bar; features before the cut identical), scrambling every unclosed bar in H1 and H4, an H4 forming-bar test, mirror involution, price-inversion and USD-orientation checks. A deliberately leaky H4 timing makes four of those tests fail (hand-checked once).

## 5. Candidate approaches, ranked

| Rank | Approach | What it can find / cannot | Sample, compute | Overfitting risk | Plugs into the simulator and null | Cheapest falsifier |
|---|---|---|---|---|---|---|
| 1 | **Same protocol at H4 and D1 decision horizons** (stop 48 to 126 pips, cost 0.021 and 0.0085 R), regularised logistic first | slow reversal/momentum in a 5-pair USD factor; cannot see intraday structure | H4: about 11,000 bars per pair in DEV 2014-21; with 2000-2013 history about 2.9 times more, if the old years pass a quality screen (not done); minutes of compute | few features, long labels; effective sample about 1,000 to 3,000 independent labels, so only AUC edges above about 0.02 to 0.03 are detectable | stateless labels at the same bar; simulator via a candidate list with a time-stop, null = `reflect` geometry or label-block permutation | D1/H4 version of the section 6 test: if directional AUC is not above 0.5 by about 0.02 in more than one era, stop |
| 2 | **Volatility / time-of-day layer** (forecast range, choose when to trade and how wide to put the stop) | cost per R (0.035 to 0.056 across hours), position-size/stop-width scaling; no direction | existing features; minutes | low | applies as a gate in the simulator; judged by cost per R, not by a null | forecast R^2 of forward range from (hour, ATR state): already IC 0.4 to 0.5; the test is whether gating on it changes net R of a *random-direction* run by more than its SE |
| 3 | Gradient-boosted trees on N+S at H1/M15 (done: HGB_S, HGB_M) | interactions (HGB_M beat the permutation null) | 430k rows, 2 to 4 s per fit | high; kept small | stateless label, OOF | done: not established; 2019H2 only |
| 4 | Regularised logistic / elastic net (done: LR) | linear reversal factors | seconds | low | same | done: not established; z 3.2 vs null, 2019H2 only |
| 5 | Regime/state models (HMM, vol regimes, rolling variance-ratio gates) | would target exactly the 2019H2 phenomenon (the reversal factor is on in some periods) | only about 15 half-year regimes in DEV: far too few to learn switching; trailing-VR gates can be tested but not powered | very high | a feature in the model above | does trailing 3-month VR(24 h) predict the next month's reversal gross? with about 80 independent months it can only exclude large effects |
| 6 | Meta-labelling on SMC candidates | would filter a population whose skill is +0.006 R (SE 0.020) | about 4,000 DEV trades (3,970 in R2); trade-level sd 1.57; 1,260 trades just to see 0.10 R | high | direct | **de-prioritised**: nothing to filter; if wanted later, use the corrected `reflect` null |
| 7 | Sequence models (LSTM/transformers), reinforcement learning | non-linear temporal patterns | needs at least 10^5 to 10^6 effectively independent samples; here 10^4 and nearly one regime | extreme | none | not worth testing: a linear model and a depth-4 tree cannot beat the non-directional baseline robustly, so a larger one has nothing to add except fit noise |

## 6. Evaluation protocol for a campaign

* Purged expanding walk-forward by half-year, 7-day embargo on label end (longest label 48 h; a slower study must lengthen both), all pairs cut at the same time. Week-block bootstrap for every interval; whole-pipeline null by shifting the directional features by a random whole number of weeks (>= 26, same shift for all pairs, same hour of the week) and refitting; both implemented and tested with planted signals.
* Primary statistic: gain of directional over non-directional features (D1, log-loss) and the within-bar directional AUC; economic reading only after those pass: top-decile gain over the unchosen side (simulator-free) with costs x 1.5. Do not use "real minus mirrored null" (the mirrored null in `nulltest.py` was biased; the lead's `reflect` is the corrected simulator null).
* Power (this label, 312,730 out-of-fold rows, 283 weeks): week-block SE of the pooled mean net R is 0.0075 (optimistic: long and short rows cancel the USD factor), 0.0116 for a random 10 % of bars; the realistic SE for a directional rule is larger (observed SE of the top-decile gain: 0.022 R, of the directional AUC: 0.0077, of D1: 0.0005 to 0.0006). Minimum detectable at 80 % power: top-decile gain about 0.06 R, directional AUC about 0.022 above 0.5. A true 0.05 R edge is detected about 60 % of the time. Edges below that are "not detectable", not "rejected".
* Hypotheses and correction: this study's family is 3 settings (Holm, alpha 0.05), plus the descriptive feasibility family (39 + 14 IC tests Bonferroni, 6 AUC tests). A campaign of K label/horizon variants should declare K up front and use Holm over the primary statistic (K <= 6 per GAMEPLAN section 3).
* Stop rule: all three settings fail C1 and C2 -> stop that label (done, here). Two label revisions without movement -> stop the family. Any candidate must be stable: D1 positive in at least 70 % of half-year blocks and not carried by one block (here it fails: LR is positive in 4 of 11 blocks and one block carries the sum).
* FINAL confirmation, specified not run: freeze one setting, one threshold rule, one label spec; train on all DEV rows with label end before 2021-05-25; evaluate once on 2021-06-01..2025-04-30 (about 3.9 years, 28 % less than the 5.4 out-of-fold years here, so less power) with the same bootstrap; success = lower bound of the top-decile gain under costs x 1.5, spread x 1.5 and a one-bar delay above 0 at alpha 0.025, plus a positive D1 in at least 70 % of half-years; one run, no `--force`. Also report with and without XAUUSD/EURUSD-2025 per GAMEPLAN section 4.

## 7. The prototype (pre-registered; `learnability.py`)

Rows 422,720 stacked (long + mirrored short), 312,730 out of fold; base rate 29.0 %. Log-loss: base 0.6018, B1 (non-directional) 0.6001 to 0.6005, F (all features) 0.5991 to 0.5999. Non-directional state explains a little (D0 = +0.0014 [+0.00006, +0.0027] for LR): time of day, volatility and spread geometry. Pooled AUC: B1 0.558 (LR) and F 0.560 [0.555, 0.566] — most of the ranking power is non-directional.

| Setting | D1 (B1 minus F) [95 % CI] | C1: Holm lower bound | Null mean (sd), z; above all B | Directional AUC [95 %] | Top-decile gain vs unchosen side (R) | Chosen-side net R, costs x1 / x1.5 | Verdict |
|---|---|---|---|---|---|---|---|
| LR | +4.9e-4 [-4.7e-4, +1.4e-3] | -5.8e-4 | -3.9e-4 (2.8e-4), z 3.2; yes, B=100 | 0.5187 [0.505, 0.536] | +0.041 [-0.000, +0.085] | -0.005 [-0.057, +0.050] / -0.030 [-0.082, +0.024] | C1 fails |
| HGB_S | +2.4e-4 [-5.1e-4, +9.9e-4] | -5.2e-4 | -7.6e-5 (2.1e-4), z 1.5; no, B=40 | 0.5046 | +0.010 [-0.034, +0.050] | -0.035 / | fails both |
| HGB_M | +1.33e-3 [+9.6e-5, +2.66e-3] | **-1.9e-6** | -6.1e-4 (4.0e-4), z 4.8; yes, B=40 | 0.5183 | +0.059 [+0.017, +0.105] | +0.029 [-0.023, +0.086] / +0.006 [-0.046, +0.063] | C1 fails by a hair, C2 passes |

By half-year (D1 x 1e3, LR): 2016H1 +2.6, 2016H2 -1.2, 2017H1 -1.8, 2017H2 +0.2, 2018H1 +1.1, 2018H2 -3.5, 2019H1 -2.3, **2019H2 +14.8**, 2020H1 -0.7, 2020H2 -1.1, 2021H1 -4.0; directional AUC in 2018H2 to 2019H1 and 2021H1 is 0.40 to 0.46 (the model, trained on the earlier years, is wrong-way). Excluding 2019H2 (post hoc): directional AUC 0.497 (LR), 0.495 (HGB_S), 0.501 (HGB_M), D1 -1.0e-3, -2.4e-4, -5.2e-4. By pair (LR): directional AUC EURUSD 0.533, USDJPY 0.532, AUDUSD 0.524, USDCAD 0.518, GBPUSD 0.488 (the pair with the 2019 data hole).
Two notes on the machinery: the null mean of D1 is negative (-4e-4 to -6e-4): extra features cost log-loss when they carry no information, so comparing D1 with zero is stricter than comparing it with the null; and the LR result, where the null is passed but C1 is not, is the typical profile of a real but small and unstable effect.
**Reading**: there is something in H1 price history that the models use in 2019H2, there is nothing stable enough to call learnable, and the net-of-cost top-decile level (HGB_M +0.029 R [-0.023, +0.086]) is not distinguishable from zero. Under the pre-registered stop rule this label and horizon stop here.

## 8. Risks and what I could not verify

* The 50-point spread sentinel: assumed placeholder, not confirmed. If 50 is real, 2020-05..2021-05 costs are 5 pips and everything there is untradable; if it is not, the simulator overstates cost for that year.
* 2019H2: unexplained. Candidate causes (range-bound regime with strong reversal; an unrecognised data feature of that period) were not separated. The lag-1 autocorrelation of 2019H2 is not unusual (H1 -0.022 against -0.036 in 2019H1).
* Effective sample: the realised SE of the directional AUC (0.0077) corresponds to about 5,000 to 6,000 independent bars, far fewer than 430,000 rows; five correlated pairs, 48 h labels.
* Bar data hide intrabar order and quote noise; ties between stop and target are conservative (stop wins; the stage-0 audit found they decided 0 of 861 trades at M5).
* The prototype fixes one label geometry (k 1.5, 48 h, 3R), one decision horizon, three model settings and one fold design. A different label could behave differently; this was not searched.
* Old (2000-2013) H1/H4 history was only used as indicator warm-up; its quality (Saturday bars, shifted clock, zero spreads) is why recommendation 1 needs a screen first.
* The permutation null keeps hour-of-week alignment and cross-pair structure but assumes a shift of 26+ weeks decouples directional features from labels; with a regime like 2019H2 inside the window a shifted copy of that regime lands on other labels, which makes the null conservative for a regime effect.

## 9. Effort and time plan (against GAMEPLAN.md)

* Week of Nov 2: merge the sentinel finding into `data_quality_by_era.md`, decide the cost treatment for 2020-05..2021-05 (affects stage 0 and the FINAL window); screen 2000-2013 H1/H4 quality (2 days).
* Nov 9 to 20: run the section 6 protocol on H4 and D1 decision bars (code reuse: `LabelSpec(k_atr, horizon_min)` needs a decision-timeframe argument for H4/D1, about 1 day; features at H4/D1, about 2 days) with K = 4 pre-registered variants (H4 and D1, two stop multiples). Replaces GAMEPLAN arm A2 (meta-labelling), which stage 0 removes.
* Dec: volatility/time-of-day gate evaluated on cost per R in the simulator with the `reflect` null (1 week); optional vendor cross-check of 2019 (D3).
* Cut: sequence models, LLM arms, M5/M15 direction models, hyperparameter search. Total about 3 to 4 person-weeks, inside the Dec 14 to Jan 8 AI window, with slack.

## 10. Things for the lead to merge

* GAMEPLAN section 7: arm A2 reworded (no meta-labelling on SMC; slow-horizon learnability test instead); section 7b: add the sentinel investigation.
* `data_quality_by_era.md` / `data_quality.py`: a 50-point sentinel check (share of bars exactly equal to the pair's modal wide value) is missing; `dataset.sentinel_mask` is a candidate. `prepare_bars` would impute it as a normal value.
* `requirements.txt`: scikit-learn is not listed; I installed 1.7.2 only into `research/output/ai/pkgs` (disk: 230 MB; delete the folder to undo).
* Housekeeping disclosure: I started `python -` with empty input and `python -c` with trivial no-op code about ten times by reflex (nothing computed, nothing written); every real run used a script file.

## Files

Package `research/ai/` (`dataset.py labels.py features.py build.py cv.py models.py learnability.py predictability.py inventory.py cost_horizon.py power.py stability*.py acf_by_period.py`, scratch viewers `_show.py _c1.py _probe_spread.py`), tests `research/tests/test_ai_{dataset,labels,features,learnability,predictability}.py`, outputs `research/output/ai/` (inventory, predictability, learn, dataset parquet), pre-registration `research/reports/ai_edge_prereg.md`.
