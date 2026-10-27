"""
Portfolio simulator ("Phase B").

Steps through every M5 cycle on one shared account and applies the STATEFUL parts of the live bot to the
candidate signals produced by signals.py:

  cycle order (main.py::_run_cycle):  risk gate -> max-open gate -> expire pendings -> breakeven -> per-pair analysis
  per-pair gates:  symbol guard + cooldown, correlation filter, sizing (calculate_position), pre-trade validator
  fills/exits:     bid/ask from per-bar spread, pending LIMIT/STOP orders, SL/TP/breakeven, pessimistic intrabar rule

Known quirk reproduced on purpose (`stale_snapshot=True`): live reads positions/pending ONCE per cycle, so several
pairs can open in the same cycle and the max-open / correlation checks do not see them.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field, replace
from typing import Optional

import numpy as np
import pandas as pd

from research.signals import ROOT  # noqa: F401  (ensures project root on sys.path)
from utils import risk_manager as live_rm                       # constants only
from utils import order_manager as live_om                      # constants only
from utils.correlation_filter import correlation_filter         # pure
from utils.pre_trade_validator import PreTradeValidator         # constants only


# ── Configuration ───────────────────────────────────────────────────────────────

@dataclass
class SimConfig:
    initial_balance: float = 100_000.0
    # sizing / risk (defaults pulled from the live modules so they cannot drift)
    risk_per_trade_pct: float = live_rm.RISK_PER_TRADE_PCT      # 0.5
    rr_ratio: float = live_rm.RR_RATIO                          # 3.0
    min_sl_pips: float = live_rm.MIN_SL_PIPS                    # 10
    daily_dd_pct: float = live_rm.DAILY_DRAWDOWN_PCT            # 3.0 (equity vs first balance of the UTC day)
    monthly_target_pct: Optional[float] = live_rm.MONTHLY_TARGET_PCT   # 5.0 -> pauses trading for the month; None=off
    max_open_trades: int = 3
    # orders
    pending_expiry_min: int = live_om.PENDING_EXPIRY_MINS       # 30
    cooldown_min: int = live_om.COOLDOWN_CANDLES * 5            # 25
    # breakeven (config.BREAKEVEN)
    breakeven: bool = True
    be_trigger_rr: float = 1.0
    be_buffer_pips: float = 2.0
    # execution costs
    commission_per_lot_side: float = 2.5                        # FTMO forex: $2.50 / lot / side
    spread_mult: float = 1.0
    stop_slippage_points: float = 0.0
    leverage: float = 100.0
    max_margin_usage_pct: float = PreTradeValidator.MAX_MARGIN_USAGE_PCT
    max_spread_to_stop_pct: float = PreTradeValidator.MAX_SPREAD_TO_STOP_PCT
    use_spread_filter: bool = True
    # fidelity switches
    stale_snapshot: bool = True          # reproduce live "positions/pending read once per cycle"
    intrabar: str = "pessimistic"        # pessimistic | neutral | optimistic (see _bar_exit)
    # ── rule / risk-framework experiments (research/reports/rule_changes*.md). ALL default to off = live behaviour.
    friday_flatten_utc: Optional[float] = None       # e.g. 20.5: at the first cycle >= Fri 20:30 UTC close all, cancel pendings, no entries
    friday_no_entry_from_utc: Optional[float] = None # e.g. 15.0: no new orders/fills on Fridays from this UTC hour
    no_entry_from_utc: Optional[float] = None        # e.g. 20.0: no new orders/fills on any day from this UTC hour
    skip_stop_entries: bool = False                  # LIMIT-only: drop candidates whose LIMIT was converted to a STOP
    fixed_risk_pct: Optional[float] = None           # e.g. 0.25: ignore ATR/regime scaling, risk exactly this % of balance
    reopen_spread_zero: bool = False                 # sensitivity: ignore the (very wide) first-bar-after-weekend spread when testing SL/TP exits
                                                     # (the data's reopen bar carries its opening-tick spread, which is applied to the whole bar's high for shorts)
    tp_gap_fill: bool = False                        # TP is a limit order: if a bar OPENS beyond TP (weekend gap) fill at the open, not at TP.
                                                     # Default False keeps the original convention (TP capped at +RR), which is asymmetric
                                                     # vs. SL gap-through (SL already fills at the gapped open).


# ── Helpers ─────────────────────────────────────────────────────────────────────

def profit_usd(spec: dict, symbol: str, direction: str, volume: float,
               entry: float, exit_price: float) -> float:
    """P/L in USD for the pairs this bot trades (USD-quoted, USD-based, gold)."""
    sign = 1.0 if direction == "BUY" else -1.0
    raw = (exit_price - entry) * sign * spec["contract_size"] * volume
    if spec["currency_profit"] == "USD":
        return raw
    if symbol.startswith("USD"):
        return raw / exit_price
    raise NotImplementedError(f"{symbol}: quote currency {spec['currency_profit']} needs a conversion rate")


def notional_usd(spec: dict, symbol: str, volume: float, price: float) -> float:
    base_units = spec["contract_size"] * volume
    return base_units if symbol.startswith("USD") else base_units * price


def size_position(spec: dict, symbol: str, direction: str, entry: float, sl: float, balance: float,
                  atr_factor: float, size_factor: float, cfg: SimConfig) -> dict:
    """Port of RiskManager.calculate_position with analytic P/L instead of MT5 order_calc_profit.
    Raises ValueError exactly where the live code does."""
    if balance <= 0:
        raise ValueError("invalid balance")
    if entry <= 0 or sl <= 0:
        raise ValueError("invalid entry/sl")
    if direction == "BUY" and sl >= entry:
        raise ValueError("BUY sl above entry")
    if direction == "SELL" and sl <= entry:
        raise ValueError("SELL sl below entry")

    point, digits = spec["point"], spec["digits"]
    vmin, vmax, vstep = spec["volume_min"], spec["volume_max"], spec["volume_step"]
    stops_level = spec.get("stops_level", 0)

    sl_distance = abs(entry - sl)
    sl_points = sl_distance / point
    pip_size = point * (10 if digits in (3, 5) else 1)
    sl_pips = sl_distance / pip_size

    if stops_level > 0 and sl_points < stops_level:
        raise ValueError("below broker stops level")

    if sl_pips < cfg.min_sl_pips:
        sl_pips = cfg.min_sl_pips
        sl_distance = sl_pips * pip_size
        sl = round(entry - sl_distance, digits) if direction == "BUY" else round(entry + sl_distance, digits)
    else:
        sl = round(sl, digits)

    tp_distance = sl_distance * cfg.rr_ratio
    tp = round(entry + tp_distance, digits) if direction == "BUY" else round(entry - tp_distance, digits)

    size_factor = max(0.0, min(size_factor, 1.0))
    risk_pct = cfg.risk_per_trade_pct * atr_factor * size_factor
    allowed_risk = balance * risk_pct / 100
    if allowed_risk <= 0:
        raise ValueError("allowed risk <= 0")

    loss_per_lot = abs(profit_usd(spec, symbol, direction, 1.0, entry, sl))
    if loss_per_lot <= 0:
        raise ValueError("loss per lot <= 0")

    raw_volume = allowed_risk / loss_per_lot
    volume = math.floor((raw_volume + 1e-12) / vstep) * vstep
    volume = round(min(volume, vmax), 8)
    if volume < vmin:
        raise ValueError("volume below broker minimum")

    actual_risk = abs(profit_usd(spec, symbol, direction, volume, entry, sl))
    if actual_risk > allowed_risk * 1.01:
        raise ValueError("actual risk exceeds allowed")

    return {"volume": volume, "sl": sl, "tp": tp, "sl_pips": sl_pips, "tp_pips": sl_pips * cfg.rr_ratio,
            "risk_amount": actual_risk, "allowed_risk": allowed_risk, "risk_pct": risk_pct}


class SimRisk:
    """Daily-drawdown / monthly-target gate (port of RiskManager.can_trade using bar time, not wall clock)."""

    def __init__(self, cfg: SimConfig):
        self.cfg = cfg
        self.daily_bal = self.monthly_bal = None
        self.daily_key = self.monthly_key = None

    def can_trade(self, ts: pd.Timestamp, balance: float, equity: float) -> tuple[bool, str]:
        day, month = ts.strftime("%Y-%m-%d"), ts.strftime("%Y-%m")
        if self.daily_key != day:
            self.daily_bal, self.daily_key = balance, day
        if self.monthly_key != month:
            self.monthly_bal, self.monthly_key = balance, month
        dd_pct = (equity - self.daily_bal) / self.daily_bal * 100
        if dd_pct <= -self.cfg.daily_dd_pct:
            return False, "daily_drawdown"
        if self.cfg.monthly_target_pct is not None:
            m_pct = (balance - self.monthly_bal) / self.monthly_bal * 100
            if m_pct >= self.cfg.monthly_target_pct:
                return False, "monthly_target"
        return True, "ok"


@dataclass
class Order:
    oid: int
    sym: str
    dir: str
    subtype: str            # LIMIT | STOP
    price: float
    sl: float
    tp: float
    volume: float
    risk_usd: float
    placed_n: int
    placed_T: pd.Timestamp
    meta: dict


@dataclass
class Pos:
    oid: int
    sym: str
    dir: str
    entry: float
    sl: float
    sl0: float
    tp: float
    volume: float
    risk_usd: float
    open_n: int
    open_T: pd.Timestamp
    placed_T: pd.Timestamp
    balance_at_open: float
    commission: float
    meta: dict
    be_moved: bool = False


# ── Simulator ───────────────────────────────────────────────────────────────────

class Simulator:

    def __init__(self, symbols: list[str], m5: dict, specs: dict, candidates: pd.DataFrame,
                 cfg: SimConfig = SimConfig()):
        self.symbols, self.specs, self.cfg = symbols, specs, cfg
        self.risk = SimRisk(cfg)
        self._time_rules = (cfg.friday_flatten_utc is not None or cfg.friday_no_entry_from_utc is not None
                            or cfg.no_entry_from_utc is not None)
        # sizing config: fixed true risk ignores the ATR/regime scaling factors (see _cycle)
        self._size_cfg = (replace(cfg, risk_per_trade_pct=cfg.fixed_risk_pct)
                          if cfg.fixed_risk_pct is not None else cfg)

        # per-symbol M5 arrays; bid prices; spread in price units
        self.T, self.O, self.H, self.L, self.C, self.S = {}, {}, {}, {}, {}, {}
        for s in symbols:
            df = m5[s]
            self.T[s] = (df["time"].values.astype("datetime64[ns]") + np.timedelta64(5, "m")).astype("int64")
            self.O[s], self.H[s] = df["open"].to_numpy(float), df["high"].to_numpy(float)
            self.L[s], self.C[s] = df["low"].to_numpy(float), df["close"].to_numpy(float)
            self.S[s] = df["spread_pts"].to_numpy(float) * specs[s]["point"] * cfg.spread_mult
        self.reopen = {s: np.concatenate([[False], np.diff(self.T[s]) > np.int64(40 * 3600 * 1e9)]) for s in symbols}
        self.times = np.unique(np.concatenate([self.T[s] for s in symbols]))
        self.idx = {}
        for s in symbols:
            pos = np.searchsorted(self.T[s], self.times)
            pos_c = np.minimum(pos, len(self.T[s]) - 1)
            self.idx[s] = np.where(self.T[s][pos_c] == self.times, pos_c, -1)

        # candidates keyed by cycle time -> {symbol: row}
        self.cand: dict = {}
        if len(candidates):
            for r in candidates.to_dict("records"):
                self.cand.setdefault(r["T"].value, {})[r["symbol"]] = r

        self.balance = cfg.initial_balance
        self.positions: list[Pos] = []
        self.pending: list[Order] = []
        self.last_trade_T: dict = {}
        self.last_trade_dir: dict = {}
        self.last_close: dict = {}
        self.funnel = Counter()
        self.trades: list[dict] = []
        self.orders: list[dict] = []
        self.equity_rows: list[tuple] = []
        self._oid = 0

    # ── bar processing ────────────────────────────────────────────────────────────
    def _bar(self, s: str, j: int):
        return self.O[s][j], self.H[s][j], self.L[s][j], self.C[s][j], self.S[s][j]

    def _try_fill(self, o: Order, bar) -> Optional[float]:
        op, hi, lo, _, sp = bar
        slip = self.cfg.stop_slippage_points * self.specs[o.sym]["point"]
        if o.dir == "BUY":
            if o.subtype == "LIMIT" and lo + sp <= o.price:
                return min(o.price, op + sp)
            if o.subtype == "STOP" and hi + sp >= o.price:
                return max(o.price, op + sp) + slip
        else:
            if o.subtype == "LIMIT" and hi >= o.price:
                return max(o.price, op)
            if o.subtype == "STOP" and lo <= o.price:
                return min(o.price, op) - slip
        return None

    def _bar_exit(self, p: Pos, bar, fill_bar: bool) -> Optional[tuple]:
        """Return (exit_price, reason) if SL/TP is hit on this bar. Pessimistic: SL wins ties; on the fill bar
        only SL counts ('neutral' ignores both, 'optimistic' treats it like any other bar)."""
        op, hi, lo, _, sp = bar
        mode = self.cfg.intrabar
        if fill_bar and mode == "neutral":
            return None
        if p.dir == "BUY":
            sl_hit, tp_hit = lo <= p.sl, hi >= p.tp
            sl_px = op if op <= p.sl else p.sl
        else:
            sl_hit, tp_hit = hi + sp >= p.sl, lo + sp <= p.tp
            sl_px = (op + sp) if (op + sp) >= p.sl else p.sl
        if fill_bar and mode == "pessimistic":
            tp_hit = False
        if sl_hit:
            return sl_px, ("BE" if p.be_moved else "SL")
        if tp_hit:
            if self.cfg.tp_gap_fill:
                tp_px = max(p.tp, op) if p.dir == "BUY" else min(p.tp, op + sp)
                return tp_px, "TP"
            return p.tp, "TP"
        return None

    def _close(self, p: Pos, px: float, reason: str, T: pd.Timestamp):
        spec = self.specs[p.sym]
        gross = profit_usd(spec, p.sym, p.dir, p.volume, p.entry, px)
        comm = p.commission + self.cfg.commission_per_lot_side * p.volume
        net = gross - comm
        self.balance += net
        self.positions.remove(p)
        self.trades.append({
            **p.meta, "symbol": p.sym, "direction": p.dir, "placed_T": p.placed_T, "open_T": p.open_T,
            "close_T": T, "entry": p.entry, "sl0": p.sl0, "tp": p.tp, "exit": px, "volume": p.volume,
            "risk_usd": p.risk_usd, "gross_pnl": gross, "commission": comm, "net_pnl": net,
            "r": net / p.risk_usd if p.risk_usd else np.nan,
            "ret_pct": net / p.balance_at_open * 100, "exit_reason": reason, "be_moved": p.be_moved,
            "hold_min": (T - p.open_T).total_seconds() / 60,
        })

    # ── time-based entry/flatten rules (all inactive unless configured) ────────────
    def _entry_blocked(self, T: pd.Timestamp) -> bool:
        """True when no new order may be placed / no pending may fill at cycle time T."""
        cfg = self.cfg
        if not self._time_rules:
            return False
        h = T.hour + T.minute / 60.0
        if cfg.no_entry_from_utc is not None and h >= cfg.no_entry_from_utc:
            return True
        if T.dayofweek == 4:
            if cfg.friday_no_entry_from_utc is not None and h >= cfg.friday_no_entry_from_utc:
                return True
            if cfg.friday_flatten_utc is not None and h >= cfg.friday_flatten_utc:
                return True
        return False

    def _cancel_pendings(self, why: str):
        for o in list(self.pending):
            self.pending.remove(o)
            self.funnel[why] += 1
            self.orders[o.oid]["outcome"] = why

    def _friday_flatten(self, T: pd.Timestamp):
        """Close every position at the last bar close (bid for BUY, ask for SELL) and cancel pendings."""
        cfg = self.cfg
        if cfg.friday_flatten_utc is None or T.dayofweek != 4:
            return
        if T.hour + T.minute / 60.0 < cfg.friday_flatten_utc:
            return
        self._cancel_pendings("cancelled_flatten")
        for p in list(self.positions):
            c, sp = self.last_close[p.sym]
            self._close(p, c if p.dir == "BUY" else c + sp, "FLAT", T)
            self.funnel["flattened"] += 1

    def _process_step(self, n: int, T: pd.Timestamp):
        bars = {}
        for s in self.symbols:
            j = self.idx[s][n]
            if j >= 0:
                bars[s] = self._bar(s, j)
                if self.cfg.reopen_spread_zero and self.reopen[s][j]:
                    bars[s] = bars[s][:4] + (0.0,)
                self.last_close[s] = (self.C[s][j], self.S[s][j])
        # pending orders -> fills (a time-blocked cycle cancels them instead of filling)
        if self.pending and self._entry_blocked(T):
            self._cancel_pendings("cancelled_time")
        for o in list(self.pending):
            bar = bars.get(o.sym)
            if bar is None or o.placed_n >= n:
                continue
            px = self._try_fill(o, bar)
            if px is None:
                continue
            self.pending.remove(o)
            spec = self.specs[o.sym]
            p = Pos(o.oid, o.sym, o.dir, px, o.sl, o.sl, o.tp, o.volume, o.risk_usd, n, T, o.placed_T,
                    self.balance, self.cfg.commission_per_lot_side * o.volume, o.meta)
            self.positions.append(p)
            self.funnel["filled"] += 1
            self.orders[o.oid]["outcome"] = "filled"
            ex = self._bar_exit(p, bar, fill_bar=True)
            if ex:
                self._close(p, ex[0], ex[1], T)
        # open positions -> exits (skip those opened this very step; handled above)
        for p in list(self.positions):
            if p.open_n == n:
                continue
            bar = bars.get(p.sym)
            if bar is None:
                continue
            ex = self._bar_exit(p, bar, fill_bar=False)
            if ex:
                self._close(p, ex[0], ex[1], T)
        self._friday_flatten(T)             # after SL/TP of this bar (pessimistic), before the decision cycle

    # ── cycle (decision) ──────────────────────────────────────────────────────────
    def _floating(self) -> float:
        tot = 0.0
        for p in self.positions:
            c, sp = self.last_close[p.sym]
            px = c if p.dir == "BUY" else c + sp
            tot += profit_usd(self.specs[p.sym], p.sym, p.dir, p.volume, p.entry, px)
        return tot

    def _used_margin(self) -> float:
        tot = 0.0
        for p in self.positions:
            tot += notional_usd(self.specs[p.sym], p.sym, p.volume, p.entry) / self.cfg.leverage
        return tot

    def _manage_breakeven(self):
        cfg = self.cfg
        if not cfg.breakeven:
            return
        for p in self.positions:
            if p.sym not in self.last_close or p.sl == 0:
                continue
            c, sp = self.last_close[p.sym]
            pip = 0.01 if "JPY" in p.sym else 0.0001          # live quirk: same rule for gold
            buffer = cfg.be_buffer_pips * pip
            sl_dist = abs(p.entry - p.sl)
            if p.dir == "BUY":
                up, already = c - p.entry, p.sl >= p.entry
                new_sl = round(p.entry + buffer, 5)
            else:
                up, already = p.entry - (c + sp), p.sl <= p.entry
                new_sl = round(p.entry - buffer, 5)
            if up >= sl_dist * cfg.be_trigger_rr and not already:
                p.sl, p.be_moved = new_sl, True

    def _validate(self, sym, direction, volume, entry, sl, tp, allowed_risk, spread_pts, equity) -> Optional[str]:
        spec, cfg = self.specs[sym], self.cfg
        point, vmin, vmax, vstep = spec["point"], spec["volume_min"], spec["volume_max"], spec["volume_step"]
        if cfg.use_spread_filter:
            mx = PreTradeValidator.MAX_SPREAD_POINTS.get(sym)
            if mx is not None and spread_pts > mx:
                return "spread_abs"
            stop_pts = abs(entry - sl) / point
            if stop_pts <= 0 or spread_pts / stop_pts * 100 > cfg.max_spread_to_stop_pct:
                return "spread_rel"
        ok = (sl < entry < tp) if direction == "BUY" else (tp < entry < sl)
        if not ok:
            return "structure"
        stop_pts = abs(entry - sl) / point
        if spec.get("stops_level", 0) > 0 and stop_pts < spec["stops_level"]:
            return "stop_level"
        if not (vmin <= volume <= vmax) or not math.isclose(volume / vstep, round(volume / vstep), abs_tol=1e-8):
            return "volume"
        if abs(profit_usd(spec, sym, direction, volume, entry, sl)) > allowed_risk * 1.01:
            return "risk"
        margin = notional_usd(spec, sym, volume, entry) / cfg.leverage
        free = equity - self._used_margin()
        if margin > free * cfg.max_margin_usage_pct / 100:
            return "margin"
        return None

    def _guard(self, sym, direction, T, positions, pending) -> Optional[str]:
        if sym in [p["symbol"] for p in positions]:
            return "guard_open_position"
        if sym in [p["symbol"] for p in pending]:
            return "guard_pending"
        lt = self.last_trade_T.get(sym)
        if lt is not None and T < lt + pd.Timedelta(minutes=self.cfg.cooldown_min) \
                and self.last_trade_dir.get(sym) == direction:
            return "guard_cooldown"
        return None

    def _cycle(self, n: int, T: pd.Timestamp):
        cfg = self.cfg
        equity = self.balance + self._floating()
        allowed, why = self.risk.can_trade(T, self.balance, equity)
        positions = [{"symbol": p.sym, "type": p.dir} for p in self.positions]
        pending = [{"symbol": o.sym, "direction": o.dir} for o in self.pending]
        cands = self.cand.get(T.value, {})

        if not allowed:
            self.funnel[f"blocked_{why}"] += len(cands)
            self._manage_breakeven()
            return equity
        if len(positions) >= cfg.max_open_trades:
            self.funnel["blocked_max_open"] += len(cands)
            self._manage_breakeven()
            return equity

        if self._time_rules and self._entry_blocked(T):       # research option: no new entries at this time
            self.funnel["blocked_time"] += len(cands)
            self._manage_breakeven()
            return equity

        for o in list(self.pending):                         # expire (client-side, strict ">")
            if (T - o.placed_T) > pd.Timedelta(minutes=cfg.pending_expiry_min):
                self.pending.remove(o)
                self.funnel["expired_unfilled"] += 1
                self.orders[o.oid]["outcome"] = "expired"
        self._manage_breakeven()

        for sym in self.symbols:                              # config.PAIRS order
            c = cands.get(sym)
            if c is None:
                continue
            self.funnel["candidates_reached"] += 1
            d = c["direction"]
            if cfg.skip_stop_entries and c["subtype"] == "STOP":
                self.funnel["skipped_stop"] += 1
                continue
            r = self._guard(sym, "BUY", T, positions, pending) or self._guard(sym, d, T, positions, pending)
            if r:
                self.funnel[r] += 1
                continue
            if not correlation_filter.check(sym, d, positions, pending)["allowed"]:
                self.funnel["correlation"] += 1
                continue
            spec = self.specs[sym]
            try:
                if cfg.fixed_risk_pct is None:
                    pos = size_position(spec, sym, d, c["price"], c["sl_raw"], self.balance,
                                        c["atr_factor"], c["size_factor"], cfg)
                else:
                    pos = size_position(spec, sym, d, c["price"], c["sl_raw"], self.balance,
                                        1.0, 1.0, self._size_cfg)
            except ValueError:
                self.funnel["sizing_rejected"] += 1
                continue
            spread_pts = c["spread_pts"] * cfg.spread_mult
            bad = self._validate(sym, d, pos["volume"], c["price"], pos["sl"], pos["tp"],
                                 pos["allowed_risk"], spread_pts, equity)
            if bad:
                self.funnel[f"validator_{bad}"] += 1
                continue

            meta = {k: c[k] for k in ("probability", "checks_passed", "atr_factor", "size_factor", "subtype",
                                      "entry_type", "pattern", "fvg_sig", "sweep_sig", "qt_phase",
                                      "trend_strong", "spread_pts")}
            meta["risk_pct"] = pos["risk_pct"]
            self.last_trade_T[sym], self.last_trade_dir[sym] = T, d      # record_trade at placement
            oid = self._oid; self._oid += 1
            self.orders.append({"oid": oid, "symbol": sym, "T": T, "direction": d, "subtype": c["subtype"],
                                "outcome": "pending", **{k: meta[k] for k in ("probability", "pattern")}})
            self.funnel["orders_placed"] += 1

            if c["entry_type"] == "MARKET":
                slip = cfg.stop_slippage_points * spec["point"]
                px = c["ask"] + slip if d == "BUY" else c["bid"] - slip
                p = Pos(oid, sym, d, px, pos["sl"], pos["sl"], pos["tp"], pos["volume"], pos["risk_amount"],
                        n, T, T, self.balance, cfg.commission_per_lot_side * pos["volume"], meta)
                self.positions.append(p)
                self.funnel["filled"] += 1
                self.orders[oid]["outcome"] = "filled"
                if not cfg.stale_snapshot:
                    positions.append({"symbol": sym, "type": d})
            else:
                self.pending.append(Order(oid, sym, d, c["subtype"], c["price"], pos["sl"], pos["tp"],
                                          pos["volume"], pos["risk_amount"], n, T, meta))
                if not cfg.stale_snapshot:
                    pending.append({"symbol": sym, "direction": d})
        return equity

    # ── main loop ─────────────────────────────────────────────────────────────────
    def run(self) -> dict:
        for n, tns in enumerate(self.times):
            T = pd.Timestamp(int(tns), tz="UTC")
            self._process_step(n, T)
            eq = self._cycle(n, T)
            self.equity_rows.append((T, self.balance, eq))

        # mark leftovers
        for p in list(self.positions):
            c, sp = self.last_close[p.sym]
            self._close(p, c if p.dir == "BUY" else c + sp, "END", pd.Timestamp(int(self.times[-1]), tz="UTC"))

        equity = pd.DataFrame(self.equity_rows, columns=["T", "balance", "equity"])
        return {"trades": pd.DataFrame(self.trades), "orders": pd.DataFrame(self.orders),
                "equity": equity, "funnel": dict(self.funnel)}
