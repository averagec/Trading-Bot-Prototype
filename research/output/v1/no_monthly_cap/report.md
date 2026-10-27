# Backtest + Monte Carlo report — `v1/no_monthly_cap`

**Replays the live bot's logic on historical MT5 bars. Treat results as an optimistic upper bound** (bar data cannot reproduce real fills; no news filter, swaps or LLM).

## Setup
```
signals : {'sweep_mode': 'faithful', 'window': 500, 'session_start_hour': 7, 'session_end_hour': 22, 'min_checks': 4, 'min_score': 50.0, 'use_regime': True}
sim     : {'initial_balance': 100000.0, 'risk_per_trade_pct': 0.5, 'rr_ratio': 3.0, 'min_sl_pips': 10, 'daily_dd_pct': 3.0, 'monthly_target_pct': None, 'max_open_trades': 3, 'pending_expiry_min': 30, 'cooldown_min': 25, 'breakeven': True, 'be_trigger_rr': 1.0, 'be_buffer_pips': 2.0, 'commission_per_lot_side': 2.5, 'spread_mult': 1.0, 'stop_slippage_points': 0.0, 'leverage': 100.0, 'max_margin_usage_pct': 80.0, 'max_spread_to_stop_pct': 10.0, 'use_spread_filter': True, 'stale_snapshot': True, 'intrabar': 'pessimistic'}
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
