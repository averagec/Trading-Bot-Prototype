# Backtest + Monte Carlo report — `v1/base_faithful`

**Replays the live bot's logic on historical MT5 bars. Treat results as an optimistic upper bound** (bar data cannot reproduce real fills; no news filter, swaps or LLM).

## Setup
```
signals : {'sweep_mode': 'faithful', 'window': 500, 'session_start_hour': 7, 'session_end_hour': 22, 'min_checks': 4, 'min_score': 50.0, 'use_regime': True}
sim     : {'initial_balance': 100000.0, 'risk_per_trade_pct': 0.5, 'rr_ratio': 3.0, 'min_sl_pips': 10, 'daily_dd_pct': 3.0, 'monthly_target_pct': 5.0, 'max_open_trades': 3, 'pending_expiry_min': 30, 'cooldown_min': 25, 'breakeven': True, 'be_trigger_rr': 1.0, 'be_buffer_pips': 2.0, 'commission_per_lot_side': 2.5, 'spread_mult': 1.0, 'stop_slippage_points': 0.0, 'leverage': 100.0, 'max_margin_usage_pct': 80.0, 'max_spread_to_stop_pct': 10.0, 'use_spread_filter': True, 'stale_snapshot': True, 'intrabar': 'pessimistic'}
M5 span 2025-06-19 -> 2026-10-26 | OOS from 2026-05-31 | symbols ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'XAUUSD']
```

## Headline (full period)
```
start                   : 2025-05-23 08:35:00+00:00
end                     : 2026-10-26 06:45:00+00:00
trades                  : 825
trades_per_month        : 48.23076923076923
win_rate_pct            : 36.36363636363637
tp_rate_pct             : 14.424242424242426
exit_mix_pct            : {'SL': 58.7, 'BE': 26.9, 'TP': 14.4}
expectancy_r            : -0.2049710252491474
expectancy_r_ci95       : {'lo': -0.31045549434709435, 'hi': -0.0987784550016788, 'p_le_0': 1.0}
median_r                : -1.011933174224345
std_r                   : 1.5678096088752618
profit_factor           : 0.6302750904979322
avg_win_r               : 1.2468239471840996
avg_loss_r              : -1.0345681523538601
net_pnl                 : -41740.324917143786
return_pct              : -41.7403249171436
max_dd_equity_pct       : -44.15243641894358
sharpe_daily_ann        : -2.4704285532399437
sortino_daily_ann       : -2.5244502114762852
longest_losing_streak   : 13
avg_hold_min            : 411.56969696969696
commission_in_r         : 0.04486094041077817
open_at_end             : 0
```

**In-sample:** trades=629 win%=35.6 expR=-0.258 PF=0.57 maxDD=-42.0%

**Out-of-sample (held back):** trades=196 win%=38.8 expR=-0.036 PF=0.93 maxDD=-8.4%

## Order funnel (why candidate signals did or did not become trades)
```
candidates_reached          : 3698
orders_placed               : 1604
guard_open_position         : 1248
filled                      : 825
expired_unfilled            : 779
guard_pending               : 423
correlation                 : 214
validator_spread_rel        : 139
validator_spread_abs        : 61
blocked_max_open            : 37
guard_cooldown              : 8
sizing_rejected             : 1
```

### by_symbol
```
            n  win%  exp_R  sum_R    PF
symbol                                 
AUDUSD   99.0  41.4 -0.244  -24.1  0.54
EURUSD  128.0  43.8 -0.024   -3.0  0.92
GBPUSD  161.0  33.5 -0.332  -53.4  0.52
USDCAD  115.0  40.9 -0.285  -32.7  0.56
USDJPY  165.0  41.2 -0.409  -67.5  0.37
XAUUSD  157.0  21.7  0.075   11.7  1.08
```

### by_direction
```
               n  win%  exp_R  sum_R    PF
direction                                 
BUY        473.0  35.7 -0.185  -87.3  0.68
SELL       352.0  37.2 -0.232  -81.8  0.57
```

### by_month
```
            n  win%  exp_R  sum_R    PF
month                                  
2025-06  36.0  27.8 -0.264   -9.5  0.59
2025-07  56.0  32.1 -0.493  -27.6  0.24
2025-08  50.0  34.0 -0.218  -10.9  0.56
2025-09  46.0  34.8 -0.108   -5.0  0.84
2025-10  58.0  37.9 -0.400  -23.2  0.37
2025-11  46.0  37.0 -0.274  -12.6  0.62
2025-12  67.0  38.8 -0.024   -1.6  1.00
2026-01  52.0  38.5 -0.097   -5.1  0.88
2026-02  61.0  24.6 -0.504  -30.8  0.23
2026-03  73.0  42.5 -0.163  -11.9  0.88
2026-04  47.0  42.6 -0.390  -18.3  0.28
2026-05  37.0  32.4 -0.152   -5.6  0.78
2026-06  42.0  42.9  0.158    6.6  1.51
2026-07  39.0  25.6 -0.390  -15.2  0.45
2026-08  48.0  47.9  0.271   13.0  1.40
2026-09  35.0  37.1 -0.256   -9.0  0.56
2026-10  32.0  37.5 -0.078   -2.5  0.97
```

### by_hour
```
             n  win%  exp_R  sum_R    PF
hour_utc                                
9         76.0  36.8 -0.178  -13.5  0.69
10        69.0  31.9 -0.355  -24.5  0.45
11        82.0  50.0  0.071    5.8  1.29
12        64.0  37.5 -0.118   -7.5  0.73
13        83.0  33.7  0.001    0.1  0.93
14        78.0  39.7  0.021    1.6  0.77
15        80.0  30.0 -0.274  -21.9  0.58
16        61.0  49.2 -0.108   -6.6  0.70
17        62.0  27.4 -0.402  -24.9  0.47
18        57.0  36.8 -0.310  -17.7  0.42
19        41.0  41.5 -0.467  -19.1  0.37
20        52.0  30.8 -0.378  -19.7  0.57
21        20.0   5.0 -1.063  -21.3  0.20
```

### by_exit
```
                 n   win%  exp_R  sum_R            PF
exit_reason                                          
BE           222.0   81.5  0.089   19.8  1.165000e+01
SL           484.0    0.0 -1.118 -541.1  0.000000e+00
TP           119.0  100.0  2.960  352.2  6.681393e+13
```

### by_pattern
```
                       n  win%  exp_R  sum_R    PF
pattern                                           
Bearish Engulfing  255.0  39.6 -0.180  -46.0  0.62
Bearish Pin Bar     18.0  27.8 -0.406   -7.3  0.45
Bullish Engulfing  330.0  37.0 -0.131  -43.1  0.73
Bullish Pin Bar     39.0  28.2 -0.297  -11.6  0.52
Evening Star        79.0  31.6 -0.361  -28.5  0.41
Morning Star       104.0  34.6 -0.314  -32.6  0.59
```

### by_subtype
```
             n  win%  exp_R  sum_R    PF
subtype                                 
LIMIT    443.0  34.3 -0.233 -103.2  0.59
STOP     382.0  38.7 -0.172  -65.9  0.68
```

## Monte Carlo
```json
// edge
{
 "n": 825,
 "mean_r": -0.2049710252491474,
 "std_r": 1.5678096088752618,
 "std_error_r": 0.054584124339565006,
 "t_stat": -3.7551399372834724,
 "trades_needed_for_80pct_power_at_observed_edge": null
}
// mc_iid
{
 "mode": "iid",
 "paths": 20000,
 "trades_per_path": 825,
 "final_return_pct": {
  "p05": -56.010221212366915,
  "p25": -47.60452009138594,
  "p50": -41.392408628904505,
  "p75": -34.97173488861034,
  "p95": -25.00192873885873
 },
 "max_dd_pct": {
  "median": -43.09086470816334,
  "p95": -57.035834751086874,
  "p99": -62.184492277733156
 },
 "p_profit": 0.00015,
 "p_dd_worse_than": {
  "5%": 1.0,
  "10%": 1.0,
  "15%": 0.9988,
  "20%": 0.9939,
  "30%": 0.92665
 }
}
// mc_shuffle
{
 "mode": "shuffle",
 "paths": 20000,
 "trades_per_path": 825,
 "final_return_pct": {
  "p05": -41.784104506447036,
  "p25": -41.784104506446965,
  "p50": -41.784104506446916,
  "p75": -41.78410450644686,
  "p95": -41.784104506446795
 },
 "max_dd_pct": {
  "median": -43.122045092054876,
  "p95": -45.79972677657587,
  "p99": -47.14250978525287
 },
 "p_profit": 0.0,
 "p_dd_worse_than": {
  "5%": 1.0,
  "10%": 1.0,
  "15%": 1.0,
  "20%": 1.0,
  "30%": 1.0
 }
}
// stress
{
 "extra_cost_0.00R": {
  "mean_r": -0.2049710252491474,
  "p_profit": 0.0001,
  "median_return_pct": -41.55468107099266,
  "p95_dd_pct": -56.65238585699339
 },
 "extra_cost_0.05R": {
  "mean_r": -0.25497102524914733,
  "p_profit": 0.0,
  "median_return_pct": -47.79489698502067,
  "p95_dd_pct": -61.15292150343122
 },
 "extra_cost_0.10R": {
  "mean_r": -0.3049710252491475,
  "p_profit": 0.0,
  "median_return_pct": -53.38820496214812,
  "p95_dd_pct": -65.218226562095
 },
 "extra_cost_0.20R": {
  "mean_r": -0.40497102524914735,
  "p_profit": 0.0,
  "median_return_pct": -62.84190296078359,
  "p95_dd_pct": -72.19880911791542
 },
 "extra_cost_0.30R": {
  "mean_r": -0.5049710252491474,
  "p_profit": 0.0,
  "median_return_pct": -70.35352526165741,
  "p95_dd_pct": -77.80818172663511
 },
 "missed_10pct": {
  "trades": 733,
  "p_profit": 0.0006,
  "median_return_pct": -37.361540912148996
 },
 "missed_25pct": {
  "trades": 626,
  "p_profit": 0.0009,
  "median_return_pct": -34.53462068513718
 },
 "missed_50pct": {
  "trades": 414,
  "p_profit": 0.0027,
  "median_return_pct": -25.782691015182547
 }
}
// ftmo_1step_like_10pct
{
 "rules": {
  "initial": 100000.0,
  "profit_target_pct": 10.0,
  "max_daily_loss_pct": 5.0,
  "max_loss_pct": 10.0,
  "min_trading_days": 4,
  "max_days": 90,
  "block_len": 5
 },
 "paths": 20000,
 "p_pass": 0.00195,
 "p_fail_daily_loss": 0.0,
 "p_fail_max_loss": 0.51945,
 "p_unresolved_at_horizon": 0.4786,
 "median_days_to_pass": 64.0,
 "p90_days_to_pass": 83.2,
 "median_final_balance": 90980.37602695751
}
// ftmo_phase2_5pct
{
 "rules": {
  "initial": 100000.0,
  "profit_target_pct": 5.0,
  "max_daily_loss_pct": 5.0,
  "max_loss_pct": 10.0,
  "min_trading_days": 4,
  "max_days": 90,
  "block_len": 5
 },
 "paths": 20000,
 "p_pass": 0.0466,
 "p_fail_daily_loss": 0.0,
 "p_fail_max_loss": 0.5175,
 "p_unresolved_at_horizon": 0.4359,
 "median_days_to_pass": 31.0,
 "p90_days_to_pass": 69.0,
 "median_final_balance": 90990.38633295191
}
```
