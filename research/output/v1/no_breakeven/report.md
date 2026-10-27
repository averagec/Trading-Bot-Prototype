# Backtest + Monte Carlo report — `v1/no_breakeven`

**Replays the live bot's logic on historical MT5 bars. Treat results as an optimistic upper bound** (bar data cannot reproduce real fills; no news filter, swaps or LLM).

## Setup
```
signals : {'sweep_mode': 'faithful', 'window': 500, 'session_start_hour': 7, 'session_end_hour': 22, 'min_checks': 4, 'min_score': 50.0, 'use_regime': True}
sim     : {'initial_balance': 100000.0, 'risk_per_trade_pct': 0.5, 'rr_ratio': 3.0, 'min_sl_pips': 10, 'daily_dd_pct': 3.0, 'monthly_target_pct': 5.0, 'max_open_trades': 3, 'pending_expiry_min': 30, 'cooldown_min': 25, 'breakeven': False, 'be_trigger_rr': 1.0, 'be_buffer_pips': 2.0, 'commission_per_lot_side': 2.5, 'spread_mult': 1.0, 'stop_slippage_points': 0.0, 'leverage': 100.0, 'max_margin_usage_pct': 80.0, 'max_spread_to_stop_pct': 10.0, 'use_spread_filter': True, 'stale_snapshot': True, 'intrabar': 'pessimistic'}
M5 span 2025-06-19 -> 2026-10-26 | OOS from 2026-05-31 | symbols ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'XAUUSD']
```

## Headline (full period)
```
start                   : 2025-05-23 08:35:00+00:00
end                     : 2026-10-26 06:45:00+00:00
trades                  : 732
trades_per_month        : 42.793846153846154
win_rate_pct            : 23.770491803278688
tp_rate_pct             : 23.770491803278688
exit_mix_pct            : {'SL': 76.2, 'TP': 23.8}
expectancy_r            : -0.1415423660487516
expectancy_r_ci95       : {'lo': -0.2767131093073207, 'hi': -0.00383820249824613, 'p_le_0': 0.9774}
median_r                : -1.0463747628704536
std_r                   : 1.8868983895999434
profit_factor           : 0.7521016288710205
avg_win_r               : 2.9586291565550535
avg_loss_r              : -1.1082625182585404
net_pnl                 : -33516.44336675573
return_pct              : -33.51644336675557
max_dd_equity_pct       : -39.52961621302529
sharpe_daily_ann        : -1.655566686220797
sortino_daily_ann       : -1.7783328643914498
longest_losing_streak   : 21
avg_hold_min            : 599.474043715847
commission_in_r         : 0.04447681225238111
open_at_end             : 0
```

**In-sample:** trades=555 win%=22.2 expR=-0.215 PF=0.67 maxDD=-38.6%

**Out-of-sample (held back):** trades=177 win%=28.8 expR=0.089 PF=1.14 maxDD=-6.9%

## Order funnel (why candidate signals did or did not become trades)
```
candidates_reached          : 3640
orders_placed               : 1432
guard_open_position         : 1386
filled                      : 732
expired_unfilled            : 700
guard_pending               : 369
correlation                 : 258
validator_spread_rel        : 132
blocked_max_open            : 75
validator_spread_abs        : 55
blocked_monthly_target      : 20
guard_cooldown              : 7
sizing_rejected             : 1
```
