# Backtest + Monte Carlo report — `v1/fresh_snapshot`

**Replays the live bot's logic on historical MT5 bars. Treat results as an optimistic upper bound** (bar data cannot reproduce real fills; no news filter, swaps or LLM).

## Setup
```
signals : {'sweep_mode': 'faithful', 'window': 500, 'session_start_hour': 7, 'session_end_hour': 22, 'min_checks': 4, 'min_score': 50.0, 'use_regime': True}
sim     : {'initial_balance': 100000.0, 'risk_per_trade_pct': 0.5, 'rr_ratio': 3.0, 'min_sl_pips': 10, 'daily_dd_pct': 3.0, 'monthly_target_pct': 5.0, 'max_open_trades': 3, 'pending_expiry_min': 30, 'cooldown_min': 25, 'breakeven': True, 'be_trigger_rr': 1.0, 'be_buffer_pips': 2.0, 'commission_per_lot_side': 2.5, 'spread_mult': 1.0, 'stop_slippage_points': 0.0, 'leverage': 100.0, 'max_margin_usage_pct': 80.0, 'max_spread_to_stop_pct': 10.0, 'use_spread_filter': True, 'stale_snapshot': False, 'intrabar': 'pessimistic'}
M5 span 2025-06-19 -> 2026-10-26 | OOS from 2026-05-31 | symbols ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'XAUUSD']
```

## Headline (full period)
```
start                   : 2025-05-23 08:35:00+00:00
end                     : 2026-10-26 06:45:00+00:00
trades                  : 821
trades_per_month        : 47.99692307692308
win_rate_pct            : 36.4190012180268
tp_rate_pct             : 14.494518879415347
exit_mix_pct            : {'SL': 58.6, 'BE': 26.9, 'TP': 14.5}
expectancy_r            : -0.20209794503759093
expectancy_r_ci95       : {'lo': -0.3076417599401127, 'hi': -0.0946743564511906, 'p_le_0': 0.9999}
median_r                : -1.0115074798619095
std_r                   : 1.5707048582167198
profit_factor           : 0.6317736344499186
avg_win_r               : 1.250731099466973
avg_loss_r              : -1.03427396861396
net_pnl                 : -41479.83707829672
return_pct              : -41.479837078296654
max_dd_equity_pct       : -43.90016125938288
sharpe_daily_ann        : -2.4524161526019586
sortino_daily_ann       : -2.5148043850839366
longest_losing_streak   : 13
avg_hold_min            : 410.40194884287456
commission_in_r         : 0.044980899709861834
open_at_end             : 0
```

**In-sample:** trades=625 win%=35.7 expR=-0.254 PF=0.57 maxDD=-41.8%

**Out-of-sample (held back):** trades=196 win%=38.8 expR=-0.036 PF=0.93 maxDD=-8.4%

## Order funnel (why candidate signals did or did not become trades)
```
candidates_reached          : 3704
orders_placed               : 1593
guard_open_position         : 1245
filled                      : 821
expired_unfilled            : 772
guard_pending               : 424
correlation                 : 232
validator_spread_rel        : 140
validator_spread_abs        : 61
blocked_max_open            : 31
guard_cooldown              : 8
sizing_rejected             : 1
```
