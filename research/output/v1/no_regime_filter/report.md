# Backtest + Monte Carlo report — `v1/no_regime_filter`

**Replays the live bot's logic on historical MT5 bars. Treat results as an optimistic upper bound** (bar data cannot reproduce real fills; no news filter, swaps or LLM).

## Setup
```
signals : {'sweep_mode': 'faithful', 'window': 500, 'session_start_hour': 7, 'session_end_hour': 22, 'min_checks': 4, 'min_score': 50.0, 'use_regime': False}
sim     : {'initial_balance': 100000.0, 'risk_per_trade_pct': 0.5, 'rr_ratio': 3.0, 'min_sl_pips': 10, 'daily_dd_pct': 3.0, 'monthly_target_pct': 5.0, 'max_open_trades': 3, 'pending_expiry_min': 30, 'cooldown_min': 25, 'breakeven': True, 'be_trigger_rr': 1.0, 'be_buffer_pips': 2.0, 'commission_per_lot_side': 2.5, 'spread_mult': 1.0, 'stop_slippage_points': 0.0, 'leverage': 100.0, 'max_margin_usage_pct': 80.0, 'max_spread_to_stop_pct': 10.0, 'use_spread_filter': True, 'stale_snapshot': True, 'intrabar': 'pessimistic'}
M5 span 2025-06-19 -> 2026-10-26 | OOS from 2026-05-31 | symbols ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'XAUUSD']
```

## Headline (full period)
```
start                   : 2025-05-23 08:35:00+00:00
end                     : 2026-10-26 06:45:00+00:00
trades                  : 953
trades_per_month        : 55.713846153846156
win_rate_pct            : 36.41133263378804
tp_rate_pct             : 13.955928646379853
exit_mix_pct            : {'SL': 59.2, 'BE': 26.9, 'TP': 14.0}
expectancy_r            : -0.21942871537696595
expectancy_r_ci95       : {'lo': -0.3177701979765882, 'hi': -0.12342636467755576, 'p_le_0': 1.0}
median_r                : -1.012690355329949
std_r                   : 1.5299911718028387
profit_factor           : 0.6008939944610326
avg_win_r               : 1.20890688338754
avg_loss_r              : -1.0373040499830446
net_pnl                 : -67160.90282945144
return_pct              : -67.16090282945137
max_dd_equity_pct       : -68.49332151051743
sharpe_daily_ann        : -3.5848252461963983
sortino_daily_ann       : -4.519033373275904
longest_losing_streak   : 13
avg_hold_min            : 405.2256033578174
commission_in_r         : 0.04540229084547632
open_at_end             : 0
```

**In-sample:** trades=710 win%=34.9 expR=-0.275 PF=0.56 maxDD=-65.3%

**Out-of-sample (held back):** trades=243 win%=40.7 expR=-0.058 PF=0.85 maxDD=-15.8%

## Order funnel (why candidate signals did or did not become trades)
```
candidates_reached          : 4318
orders_placed               : 1847
guard_open_position         : 1439
filled                      : 953
expired_unfilled            : 894
guard_pending               : 492
correlation                 : 308
validator_spread_rel        : 151
validator_spread_abs        : 69
blocked_max_open            : 56
guard_cooldown              : 11
blocked_daily_drawdown      : 5
blocked_monthly_target      : 5
sizing_rejected             : 1
```
