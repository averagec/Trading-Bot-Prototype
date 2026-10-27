# Backtest + Monte Carlo report — `v1/spread_x1.5`

**Replays the live bot's logic on historical MT5 bars. Treat results as an optimistic upper bound** (bar data cannot reproduce real fills; no news filter, swaps or LLM).

## Setup
```
signals : {'sweep_mode': 'faithful', 'window': 500, 'session_start_hour': 7, 'session_end_hour': 22, 'min_checks': 4, 'min_score': 50.0, 'use_regime': True}
sim     : {'initial_balance': 100000.0, 'risk_per_trade_pct': 0.5, 'rr_ratio': 3.0, 'min_sl_pips': 10, 'daily_dd_pct': 3.0, 'monthly_target_pct': 5.0, 'max_open_trades': 3, 'pending_expiry_min': 30, 'cooldown_min': 25, 'breakeven': True, 'be_trigger_rr': 1.0, 'be_buffer_pips': 2.0, 'commission_per_lot_side': 2.5, 'spread_mult': 1.5, 'stop_slippage_points': 0.0, 'leverage': 100.0, 'max_margin_usage_pct': 80.0, 'max_spread_to_stop_pct': 10.0, 'use_spread_filter': True, 'stale_snapshot': True, 'intrabar': 'pessimistic'}
M5 span 2025-06-19 -> 2026-10-26 | OOS from 2026-05-31 | symbols ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'XAUUSD']
```

## Headline (full period)
```
start                   : 2025-05-23 08:35:00+00:00
end                     : 2026-10-26 06:45:00+00:00
trades                  : 770
trades_per_month        : 45.01538461538462
win_rate_pct            : 36.493506493506494
tp_rate_pct             : 13.636363636363635
exit_mix_pct            : {'SL': 58.8, 'BE': 27.5, 'TP': 13.6}
expectancy_r            : -0.23476328662759022
expectancy_r_ci95       : {'lo': -0.347514974828469, 'hi': -0.12783914248228437, 'p_le_0': 1.0}
median_r                : -1.02371891312349
std_r                   : 1.5613997010296725
profit_factor           : 0.5954799904299082
avg_win_r               : 1.1805805310562305
avg_loss_r              : -1.0480794681596015
net_pnl                 : -43718.020808117086
return_pct              : -43.71802080811717
max_dd_equity_pct       : -45.73086543688209
sharpe_daily_ann        : -2.6561274304476523
sortino_daily_ann       : -2.6213864345786875
longest_losing_streak   : 15
avg_hold_min            : 428.3766233766234
commission_in_r         : 0.04674770448903702
open_at_end             : 0
```

**In-sample:** trades=588 win%=36.1 expR=-0.268 PF=0.55 maxDD=-42.1%

**Out-of-sample (held back):** trades=182 win%=37.9 expR=-0.126 PF=0.84 maxDD=-10.0%

## Order funnel (why candidate signals did or did not become trades)
```
candidates_reached          : 3702
orders_placed               : 1512
guard_open_position         : 1223
filled                      : 770
expired_unfilled            : 742
guard_pending               : 405
validator_spread_rel        : 232
correlation                 : 208
validator_spread_abs        : 116
blocked_max_open            : 33
guard_cooldown              : 5
sizing_rejected             : 1
```
