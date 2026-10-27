# Backtest + Monte Carlo report — `v1/fixed_sweep`

**Replays the live bot's logic on historical MT5 bars. Treat results as an optimistic upper bound** (bar data cannot reproduce real fills; no news filter, swaps or LLM).

## Setup
```
signals : {'sweep_mode': 'fixed', 'window': 500, 'session_start_hour': 7, 'session_end_hour': 22, 'min_checks': 4, 'min_score': 50.0, 'use_regime': True}
sim     : {'initial_balance': 100000.0, 'risk_per_trade_pct': 0.5, 'rr_ratio': 3.0, 'min_sl_pips': 10, 'daily_dd_pct': 3.0, 'monthly_target_pct': 5.0, 'max_open_trades': 3, 'pending_expiry_min': 30, 'cooldown_min': 25, 'breakeven': True, 'be_trigger_rr': 1.0, 'be_buffer_pips': 2.0, 'commission_per_lot_side': 2.5, 'spread_mult': 1.0, 'stop_slippage_points': 0.0, 'leverage': 100.0, 'max_margin_usage_pct': 80.0, 'max_spread_to_stop_pct': 10.0, 'use_spread_filter': True, 'stale_snapshot': True, 'intrabar': 'pessimistic'}
M5 span 2025-06-19 -> 2026-10-26 | OOS from 2026-05-31 | symbols ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'XAUUSD']
```

## Headline (full period)
```
start                   : 2025-05-23 08:35:00+00:00
end                     : 2026-10-26 06:45:00+00:00
trades                  : 1127
trades_per_month        : 65.88615384615385
win_rate_pct            : 37.79946761313221
tp_rate_pct             : 14.81810115350488
exit_mix_pct            : {'SL': 57.3, 'BE': 27.9, 'TP': 14.8}
expectancy_r            : -0.17285658776777205
expectancy_r_ci95       : {'lo': -0.26444807908272056, 'hi': -0.08507753084330379, 'p_le_0': 1.0}
median_r                : -1.0102880658436213
std_r                   : 1.5355151175424733
profit_factor           : 0.687486434021128
avg_win_r               : 1.2345078613394935
avg_loss_r              : -1.02811658109116
net_pnl                 : -42659.19022089038
return_pct              : -42.65919022089043
max_dd_equity_pct       : -46.80760548448572
sharpe_daily_ann        : -2.2037542303554383
sortino_daily_ann       : -2.4650932191698645
longest_losing_streak   : 17
avg_hold_min            : 415.9006211180124
commission_in_r         : 0.04536997425241234
open_at_end             : 0
```

**In-sample:** trades=846 win%=37.4 expR=-0.220 PF=0.62 maxDD=-44.3%

**Out-of-sample (held back):** trades=281 win%=39.1 expR=-0.032 PF=1.02 maxDD=-7.5%

## Order funnel (why candidate signals did or did not become trades)
```
candidates_reached          : 7141
guard_open_position         : 2494
orders_placed               : 2130
guard_pending               : 1432
filled                      : 1127
expired_unfilled            : 1003
correlation                 : 699
validator_spread_rel        : 222
blocked_max_open            : 192
validator_spread_abs        : 131
guard_cooldown              : 30
sizing_rejected             : 3
```
