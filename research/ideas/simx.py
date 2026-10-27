"""
TimedSimulator: research.sim.Simulator + a per-trade time stop (candidate column `expire_ns`) + optional switch-off of
the live bot's correlation filter.  The base class is not modified; this only overrides two hooks.

Time stop semantics: a position whose expire time is <= the end of the current M5 bar (T) is closed at that bar's close
(BUY at bid=close, SELL at ask=close+spread), after the bar's SL/TP check (SL/TP still win on the expiry bar).
Reason code "TIME".
"""

from __future__ import annotations

from dataclasses import replace
from typing import Optional

import pandas as pd

import research.sim as _sim
from research.sim import Simulator, SimConfig


class _AllowAll:
    def check(self, *a, **k):
        return {"allowed": True, "reason": "correlation filter disabled for edge measurement"}


def edge_sim_config(rr: float, **over) -> SimConfig:
    """The fixed execution config from the pre-registration (section 0.3)."""
    cfg = SimConfig(rr_ratio=rr, breakeven=False, monthly_target_pct=None, daily_dd_pct=1e9, max_open_trades=50)
    return replace(cfg, **over) if over else cfg


def gross_sim_config(cfg: SimConfig) -> SimConfig:
    return replace(cfg, spread_mult=0.0, commission_per_lot_side=0.0, use_spread_filter=False)


def to_gross(cands: pd.DataFrame) -> pd.DataFrame:
    g = cands.copy()
    g["ask"] = g["bid"]
    g["spread_pts"] = 0.0
    g["price"] = g["bid"]            # entry at the mid/bid for both sides; stop distance mirrored accordingly
    shift = cands["price"] - g["price"]          # original price (ask for BUY) minus new price
    g["sl_raw"] = cands["sl_raw"] - shift
    return g


class TimedSimulator(Simulator):

    def __init__(self, *a, disable_correlation: bool = True, **k):
        super().__init__(*a, **k)
        self.disable_correlation = disable_correlation

    def run(self) -> dict:
        orig = _sim.correlation_filter
        if self.disable_correlation:
            _sim.correlation_filter = _AllowAll()
        try:
            return super().run()
        finally:
            _sim.correlation_filter = orig

    def _cycle(self, n: int, T: pd.Timestamp):
        eq = super()._cycle(n, T)
        cands = self.cand.get(T.value)
        if cands:
            for p in self.positions:
                if p.open_n == n and "expire_ns" not in p.meta:
                    c = cands.get(p.sym)
                    if c is not None and c.get("expire_ns") is not None:
                        p.meta["expire_ns"] = int(c["expire_ns"])
        return eq

    def _process_step(self, n: int, T: pd.Timestamp):
        super()._process_step(n, T)
        tn = T.value
        for p in list(self.positions):
            ex: Optional[int] = p.meta.get("expire_ns")
            if ex is None or p.open_n == n or tn < ex:
                continue
            j = self.idx[p.sym][n]
            if j < 0:
                continue
            c, sp = self.C[p.sym][j], self.S[p.sym][j]
            self._close(p, c if p.dir == "BUY" else c + sp, "TIME", T)
