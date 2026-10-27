# Backtest + Monte Carlo report — `v1/rr_2`

**Replays the live bot's logic on historical MT5 bars. Treat results as an optimistic upper bound** (bar data cannot reproduce real fills; no news filter, swaps or LLM).

## Setup
```
signals : {'sweep_mode': 'faithful', 'window': 500, 'session_start_hour': 7, 'session_end_hour': 22, 'min_checks': 4, 'min_score': 50.0, 'use_regime': True}
sim     : {'initial_balance': 100000.0, 'risk_per_trade_pct': 0.5, 'rr_ratio': 2.0, 'min_sl_pips': 10, 'daily_dd_pct': 3.0, 'monthly_target_pct': 5.0, 'max_open_trades': 3, 'pending_expiry_min': 30, 'cooldown_min': 25, 'breakeven': True, 'be_trigger_rr': 1.0, 'be_buffer_pips': 2.0, 'commission_per_lot_side': 2.5, 'spread_mult': 1.0, 'stop_slippage_points': 0.0, 'leverage': 100.0, 'max_margin_usage_pct': 80.0, 'max_spread_to_stop_pct': 10.0, 'use_spread_filter': True, 'stale_snapshot': True, 'intrabar': 'pessimistic'}
M5 span 2025-06-19 -> 2026-10-26 | OOS from 2026-05-31 | symbols ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'XAUUSD']
```

## Headline (full period)
```
start                   : 2025-05-23 08:35:00+00:00
end                     : 2026-10-26 06:45:00+00:00
trades                  : 844
trades_per_month        : 49.34153846153846
win_rate_pct            : 37.203791469194314
tp_rate_pct             : 21.563981042654028
exit_mix_pct            : {'SL': 58.9, 'TP': 21.6, 'BE': 19.5}
expectancy_r            : -0.21754402620106075
expectancy_r_ci95       : {'lo': -0.31392090190349003, 'hi': -0.1250305716445499, 'p_le_0': 1.0}
median_r                : -1.0119689880725566
std_r                   : 1.415458325084862
profit_factor           : 0.6141949968134832
avg_win_r               : 1.1877812325172705
avg_loss_r              : -1.050132953064374
net_pnl                 : -44540.403355753
return_pct              : -44.54040335575298
max_dd_equity_pct       : -45.7863439835338
sharpe_daily_ann        : -2.9485548918171887
sortino_daily_ann       : -2.750133173080951
longest_losing_streak   : 13
avg_hold_min            : 354.3483412322275
commission_in_r         : 0.044804555397319645
open_at_end             : 0
```

**In-sample:** trades=648 win%=36.7 expR=-0.241 PF=0.59 maxDD=-40.6%

**Out-of-sample (held back):** trades=196 win%=38.8 expR=-0.140 PF=0.75 maxDD=-10.0%

## Order funnel (why candidate signals did or did not become trades)
```
candidates_reached          : 3714
orders_placed               : 1642
guard_open_position         : 1253
filled                      : 844
expired_unfilled            : 798
guard_pending               : 430
correlation                 : 174
validator_spread_rel        : 143
validator_spread_abs        : 63
blocked_max_open            : 21
guard_cooldown              : 8
sizing_rejected             : 1
```
