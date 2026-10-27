# Backtest + Monte Carlo report — `v1/neutral_intrabar`

**Replays the live bot's logic on historical MT5 bars. Treat results as an optimistic upper bound** (bar data cannot reproduce real fills; no news filter, swaps or LLM).

## Setup
```
signals : {'sweep_mode': 'faithful', 'window': 500, 'session_start_hour': 7, 'session_end_hour': 22, 'min_checks': 4, 'min_score': 50.0, 'use_regime': True}
sim     : {'initial_balance': 100000.0, 'risk_per_trade_pct': 0.5, 'rr_ratio': 3.0, 'min_sl_pips': 10, 'daily_dd_pct': 3.0, 'monthly_target_pct': 5.0, 'max_open_trades': 3, 'pending_expiry_min': 30, 'cooldown_min': 25, 'breakeven': True, 'be_trigger_rr': 1.0, 'be_buffer_pips': 2.0, 'commission_per_lot_side': 2.5, 'spread_mult': 1.0, 'stop_slippage_points': 0.0, 'leverage': 100.0, 'max_margin_usage_pct': 80.0, 'max_spread_to_stop_pct': 10.0, 'use_spread_filter': True, 'stale_snapshot': True, 'intrabar': 'neutral'}
M5 span 2025-06-19 -> 2026-10-26 | OOS from 2026-05-31 | symbols ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'XAUUSD']
```

## Headline (full period)
```
start                   : 2025-05-23 08:35:00+00:00
end                     : 2026-10-26 06:45:00+00:00
trades                  : 825
trades_per_month        : 48.23076923076923
win_rate_pct            : 36.72727272727273
tp_rate_pct             : 14.424242424242426
exit_mix_pct            : {'SL': 58.3, 'BE': 27.3, 'TP': 14.4}
expectancy_r            : -0.2139060167705799
expectancy_r_ci95       : {'lo': -0.3219773702034548, 'hi': -0.10800478290289821, 'p_le_0': 1.0}
median_r                : -1.0115074798619097
std_r                   : 1.5897261130393658
profit_factor           : 0.6204549097217809
avg_win_r               : 1.235561728496483
avg_loss_r              : -1.0552637309773234
net_pnl                 : -42995.19156912192
return_pct              : -42.995191569121914
max_dd_equity_pct       : -45.372398628000255
sharpe_daily_ann        : -2.540849968416037
sortino_daily_ann       : -2.5886380092458574
longest_losing_streak   : 13
avg_hold_min            : 411.8545454545455
commission_in_r         : 0.0448934313974556
open_at_end             : 0
```

**In-sample:** trades=629 win%=36.1 expR=-0.265 PF=0.56 maxDD=-42.7%

**Out-of-sample (held back):** trades=196 win%=38.8 expR=-0.049 PF=0.90 maxDD=-8.6%

## Order funnel (why candidate signals did or did not become trades)
```
candidates_reached          : 3698
orders_placed               : 1602
guard_open_position         : 1250
filled                      : 825
expired_unfilled            : 777
guard_pending               : 423
correlation                 : 214
validator_spread_rel        : 139
validator_spread_abs        : 61
blocked_max_open            : 37
guard_cooldown              : 8
sizing_rejected             : 1
```
