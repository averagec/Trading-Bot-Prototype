# Backtest + Monte Carlo report — `v1/spread_x2`

**Replays the live bot's logic on historical MT5 bars. Treat results as an optimistic upper bound** (bar data cannot reproduce real fills; no news filter, swaps or LLM).

## Setup
```
signals : {'sweep_mode': 'faithful', 'window': 500, 'session_start_hour': 7, 'session_end_hour': 22, 'min_checks': 4, 'min_score': 50.0, 'use_regime': True}
sim     : {'initial_balance': 100000.0, 'risk_per_trade_pct': 0.5, 'rr_ratio': 3.0, 'min_sl_pips': 10, 'daily_dd_pct': 3.0, 'monthly_target_pct': 5.0, 'max_open_trades': 3, 'pending_expiry_min': 30, 'cooldown_min': 25, 'breakeven': True, 'be_trigger_rr': 1.0, 'be_buffer_pips': 2.0, 'commission_per_lot_side': 2.5, 'spread_mult': 2.0, 'stop_slippage_points': 0.0, 'leverage': 100.0, 'max_margin_usage_pct': 80.0, 'max_spread_to_stop_pct': 10.0, 'use_spread_filter': True, 'stale_snapshot': True, 'intrabar': 'pessimistic'}
M5 span 2025-06-19 -> 2026-10-26 | OOS from 2026-05-31 | symbols ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'XAUUSD']
```

## Headline (full period)
```
start                   : 2025-05-23 08:35:00+00:00
end                     : 2026-10-26 06:45:00+00:00
trades                  : 682
trades_per_month        : 39.870769230769234
win_rate_pct            : 36.21700879765396
tp_rate_pct             : 13.48973607038123
exit_mix_pct            : {'SL': 59.8, 'BE': 26.7, 'TP': 13.5}
expectancy_r            : -0.2528831074994125
expectancy_r_ci95       : {'lo': -0.3732158731682109, 'hi': -0.1399234782046425, 'p_le_0': 1.0}
median_r                : -1.0310570987654322
std_r                   : 1.566635604228358
profit_factor           : 0.5843954623754102
avg_win_r               : 1.1747631141441204
avg_loss_r              : -1.0635236057659703
net_pnl                 : -41739.436277415516
return_pct              : -41.73943627741548
max_dd_equity_pct       : -43.413810365015124
sharpe_daily_ann        : -2.6853299974474627
sortino_daily_ann       : -2.596924224677242
longest_losing_streak   : 13
avg_hold_min            : 433.90029325513194
commission_in_r         : 0.04689722256559487
open_at_end             : 0
```

**In-sample:** trades=514 win%=36.0 expR=-0.265 PF=0.56 maxDD=-36.6%

**Out-of-sample (held back):** trades=168 win%=36.9 expR=-0.216 PF=0.70 maxDD=-12.0%

## Order funnel (why candidate signals did or did not become trades)
```
candidates_reached          : 3714
orders_placed               : 1353
guard_open_position         : 1152
filled                      : 682
expired_unfilled            : 671
validator_spread_rel        : 456
guard_pending               : 352
validator_spread_abs        : 210
correlation                 : 187
blocked_max_open            : 21
guard_cooldown              : 3
sizing_rejected             : 1
```
