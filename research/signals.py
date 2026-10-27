"""
Signal replay ("Phase A").

Replays the live bot's per-cycle decision logic (main.py `_analyse_pair` + smc_strategies.run_confluence)
on historical bars, as of each M5 cycle time T, using ONLY bars that had closed by T.

Re-used UNMODIFIED from the live code base:
    detect_trend_h4, detect_fvg_h1, detect_entry_candle_m5, detect_sweep_m15 (faithful mode),
    regime_detector.get_summary, OrderManager.get_entry_type / get_limit_price,
    RiskManager.find_structure_sl / calculate_atr_risk_factor.

Ported (they read the wall clock or are buggy in live):
    qt_asof()                 - quarterly theory with an explicit hour instead of datetime.now()
    detect_sweep_m15_fixed()  - optional variant; the live sweep can never fire (window includes `prev`)

Output: one row per (cycle, symbol) that passes every STATELESS entry condition. Stateful gates
(open positions, cooldown, correlation, daily/monthly risk, sizing, spread) are applied in sim.py.
"""

from __future__ import annotations

import hashlib
import logging
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from strategies.smc_strategies import (          # noqa: E402  (live code, untouched)
    detect_trend_h4, detect_fvg_h1, detect_entry_candle_m5, detect_sweep_m15,
)
from utils.regime_detector import regime_detector   # noqa: E402
from utils.order_manager import OrderManager        # noqa: E402
from utils.risk_manager import RiskManager          # noqa: E402

CACHE_DIR = Path(__file__).resolve().parent / "cache"
OHLC = ["time", "open", "high", "low", "close", "volume"]


def quiet_live_logs() -> None:
    for name in ("utils.risk_manager", "utils.order_manager", "utils.regime_detector",
                 "strategies.smc_strategies", "mt5_connector"):
        logging.getLogger(name).setLevel(logging.WARNING)


@dataclass(frozen=True)
class SignalConfig:
    sweep_mode: str = "faithful"      # "faithful" = live code (never fires) | "fixed"
    window: int = 500                 # CANDLE_COUNT in config.py
    session_start_hour: int = 7       # main.py: 7 <= hour < 22
    session_end_hour: int = 22
    min_checks: int = 4               # main.py: checks_passed >= 4
    min_score: float = 50.0           # config.MIN_SCORE_TO_TRADE
    use_regime: bool = True

    def signature(self) -> str:
        return hashlib.md5(repr(sorted(asdict(self).items())).encode()).hexdigest()[:10]


# ── Ports ────────────────────────────────────────────────────────────────────────

def qt_asof(df: pd.DataFrame, hour: int) -> dict:
    """Verbatim port of detect_quarterly_theory_h1 with `hour` supplied instead of datetime.now()."""
    if 0 <= hour < 6:
        return {"signal": "NONE", "score": 0, "quarter": 1, "phase": "Accumulation",
                "reason": "H1: Q1 Asian session — no trade", "block": True}
    if 6 <= hour < 9:
        return {"signal": "WAIT", "score": 0, "quarter": 2, "phase": "Manipulation",
                "reason": "H1: Q2 manipulation window (06-09 UTC) — wait for sweep", "block": True}
    if 9 <= hour < 12:
        if "time" not in df.columns:
            return {"signal": "NONE", "score": 10, "quarter": 2, "phase": "London",
                    "reason": "H1: London session active", "block": False}
        q2_early = df[df["time"].dt.hour.between(6, 8)]
        if not q2_early.empty:
            q2_high = q2_early["high"].max(); q2_low = q2_early["low"].min()
            last_close = df.iloc[-1]["close"]
            if last_close > q2_high:
                return {"signal": "BUY", "score": 20, "quarter": 2, "phase": "London", "block": False,
                        "reason": f"H1: London expanding above Q2 high ({q2_high:.5f})"}
            if last_close < q2_low:
                return {"signal": "SELL", "score": 20, "quarter": 2, "phase": "London", "block": False,
                        "reason": f"H1: London expanding below Q2 low ({q2_low:.5f})"}
        return {"signal": "NONE", "score": 10, "quarter": 2, "phase": "London", "block": False,
                "reason": "H1: London session — no Q2 sweep confirmed yet"}
    if 12 <= hour < 18:
        if "time" not in df.columns:
            return {"signal": "NONE", "score": 15, "quarter": 3, "phase": "New York",
                    "reason": "H1: NY session active", "block": False}
        q2_candles = df[df["time"].dt.hour.between(6, 11)]
        if not q2_candles.empty:
            q2_high = q2_candles["high"].max(); q2_low = q2_candles["low"].min()
            last_close = df.iloc[-1]["close"]
            if last_close > q2_high:
                return {"signal": "BUY", "score": 25, "quarter": 3, "phase": "New York", "block": False,
                        "reason": f"H1: NY Q3 above London high ({q2_high:.5f})"}
            if last_close < q2_low:
                return {"signal": "SELL", "score": 25, "quarter": 3, "phase": "New York", "block": False,
                        "reason": f"H1: NY Q3 below London low ({q2_low:.5f})"}
        return {"signal": "NONE", "score": 15, "quarter": 3, "phase": "New York", "block": False,
                "reason": "H1: NY session — awaiting directional confirmation"}
    return {"signal": "NONE", "score": 10, "quarter": 4, "phase": "Late", "block": False,
            "reason": "H1: Q4 late session — follow established direction"}


def detect_sweep_m15_fixed(df: pd.DataFrame, trend_direction: str,
                           h1_level: Optional[float] = None, lookback: int = 20) -> dict:
    """Same as the live detector but the swing window EXCLUDES `prev`, so a sweep can actually be detected."""
    if len(df) < lookback + 3:
        return {"signal": "NONE", "score": 0, "reason": "Insufficient M15 data"}
    recent = df.iloc[-lookback - 2:-2]
    prev, last = df.iloc[-2], df.iloc[-1]
    swing_high, swing_low = recent["high"].max(), recent["low"].min()
    swept_high = (prev["high"] > swing_high) and (last["close"] < swing_high)
    swept_low  = (prev["low"]  < swing_low)  and (last["close"] > swing_low)
    pip = 0.01 if last["close"] > 50 and last["close"] <= 500 else (0.1 if last["close"] > 500 else 0.0001)

    if swept_high and trend_direction == "SELL":
        confirmed  = prev["close"] < prev["open"]
        near_level = bool(h1_level) and abs(swing_high - h1_level) < 20 * pip
        score = 30 if (confirmed and near_level) else (20 if confirmed else 10)
        return {"signal": "SELL", "score": score, "level": swing_high, "near_h1": near_level,
                "confirmed": confirmed, "reason": "M15: swept highs (fixed window)"}
    if swept_low and trend_direction == "BUY":
        confirmed  = prev["close"] > prev["open"]
        near_level = bool(h1_level) and abs(swing_low - h1_level) < 20 * pip
        score = 30 if (confirmed and near_level) else (20 if confirmed else 10)
        return {"signal": "BUY", "score": score, "level": swing_low, "near_h1": near_level,
                "confirmed": confirmed, "reason": "M15: swept lows (fixed window)"}
    return {"signal": "NONE", "score": 0, "reason": "M15: No sweep in trend direction"}


def compose_confluence(trend: dict, fvg: dict, qt: dict, sweep: dict, candle: dict) -> dict:
    """Mirror of the tail of run_confluence() (checklist + score)."""
    direction = trend["signal"]
    checklist = {
        "h4_trend_exists":    trend["signal"] in ("BUY", "SELL"),
        "h1_level_present":   fvg["signal"] in ("BUY", "SELL"),
        "qt_not_blocking":    not qt.get("block", False),
        "m15_sweep_present":  sweep["signal"] == direction,
        "m5_entry_confirmed": candle["signal"] == direction,
    }
    signals = {"trend": trend, "fvg": fvg, "quarterly_theory": qt,
               "liquidity_sweep": sweep, "candle_pattern": candle}
    total = sum(s["score"] for s in signals.values() if s.get("signal") == direction)
    return {
        "direction": direction, "probability": round(min(total, 100), 1), "raw_score": total,
        "breakdown": signals, "checklist": checklist,
        "checks_passed": sum(checklist.values()),
        "h1_level": fvg.get("fvg_mid"),
    }


# ── Replay ───────────────────────────────────────────────────────────────────────

def _prep(df: pd.DataFrame) -> pd.DataFrame:
    return df[OHLC].reset_index(drop=True)


def build_candidates(symbol: str, bars: dict, cfg: SignalConfig, point: float,
                     diag_times: Optional[np.ndarray] = None) -> pd.DataFrame:
    """
    bars: {"M5","M15","H1","H4"} -> DataFrame[time, open, high, low, close, volume, spread_pts]
    Returns one row per M5 cycle that satisfies every stateless entry condition.

    diag_times (validation only): datetime64[ns] cycle times. When given, EVERY listed cycle is evaluated through the
    same code path (session gate ignored) and one diagnostic row is returned per cycle with a `status`
    (regime_skip / no_bias / qt_block / below_gate / candidate) plus whatever was computed.
    """
    quiet_live_logs()
    W = cfg.window
    m5, m15, h1, h4 = (_prep(bars[t]) for t in ("M5", "M15", "H1", "H4"))
    spread5 = bars["M5"]["spread_pts"].to_numpy()

    # `.values` (not to_numpy) so tz-aware columns come back as datetime64[ns] in UTC, not object arrays
    t5  = m5["time"].values.astype("datetime64[ns]")
    t15 = m15["time"].values.astype("datetime64[ns]"); t1 = h1["time"].values.astype("datetime64[ns]")
    t4  = h4["time"].values.astype("datetime64[ns]")
    T   = t5 + np.timedelta64(5, "m")                       # cycle time = close of the M5 bar
    i5  = np.arange(len(m5))
    i15 = np.searchsorted(t15 + np.timedelta64(15, "m"), T, side="right") - 1
    i1  = np.searchsorted(t1  + np.timedelta64(60, "m"), T, side="right") - 1
    i4  = np.searchsorted(t4  + np.timedelta64(240, "m"), T, side="right") - 1
    hour = (T.astype("datetime64[h]") - T.astype("datetime64[D]")).astype(int)

    warm = (i5 >= W - 1) & (i15 >= W - 1) & (i1 >= W - 1) & (i4 >= W - 1)
    diag = diag_times is not None
    if diag:
        ok = warm & np.isin(T, np.asarray(diag_times, dtype="datetime64[ns]"))
    else:
        ok = warm & (hour >= cfg.session_start_hour) & (hour < cfg.session_end_hour)

    def note(k, status, **kw):
        if diag:
            rows.append({"symbol": symbol, "T": pd.Timestamp(T[k], tz="UTC"), "status": status, **kw})

    om, rm = OrderManager(), RiskManager()
    trend_c, fvg_c, qt_c, sw_c, reg_c = {}, {}, {}, {}, {}
    rows = []

    for k in np.flatnonzero(ok):
        a4, a1, a15 = int(i4[k]), int(i1[k]), int(i15[k])
        h4w = h4.iloc[a4 - W + 1:a4 + 1]
        h1w = h1.iloc[a1 - W + 1:a1 + 1]

        if cfg.use_regime:
            rk = (a4, a1)
            if rk not in reg_c:
                reg_c[rk] = regime_detector.get_summary(symbol, h4w, h1w)
            regime = reg_c[rk]
            if not regime["can_trade"]:
                note(k, "regime_skip")
                continue
            size_factor = regime.get("size_factor", 1.0)
        else:
            size_factor = 1.0

        if a4 not in trend_c:
            trend_c[a4] = detect_trend_h4(h4w)
        trend = trend_c[a4]
        direction = trend["signal"]
        if direction == "NONE":
            note(k, "no_bias", trend_label=trend.get("trend"))
            continue

        fk = (a1, direction)
        if fk not in fvg_c:
            fvg_c[fk] = detect_fvg_h1(h1w, direction)
        fvg = fvg_c[fk]
        h1_level = fvg.get("fvg_mid")

        qk = (a1, int(hour[k]))
        if qk not in qt_c:
            qt_c[qk] = qt_asof(h1w, int(hour[k]))
        qt = qt_c[qk]
        if qt.get("block", False):
            note(k, "qt_block", direction=direction, trend_label=trend.get("trend"))
            continue

        sk = (a15, direction, h1_level)
        if sk not in sw_c:
            m15w = m15.iloc[a15 - 24:a15 + 1]              # detectors only read the last ~22 rows
            sw_c[sk] = (detect_sweep_m15(m15w, direction, h1_level) if cfg.sweep_mode == "faithful"
                        else detect_sweep_m15_fixed(m15w, direction, h1_level))
        sweep = sw_c[sk]

        m5w3 = m5.iloc[k - 2:k + 1]                         # detector only reads the last 3 bars
        candle = detect_entry_candle_m5(m5w3, direction)

        conf = compose_confluence(trend, fvg, qt, sweep, candle)
        if conf["checks_passed"] < cfg.min_checks or conf["probability"] < cfg.min_score:
            bd0 = conf["breakdown"]
            note(k, "below_gate", direction=direction, probability=conf["probability"],
                 checks_passed=conf["checks_passed"], trend_label=trend.get("trend"),
                 fvg_type=fvg.get("fvg_type"), pattern=candle.get("pattern", "None"),
                 sweep_sig=sweep["signal"], qt_sig=qt["signal"])
            continue

        # ── order construction (stateless part of main.py) ───────────────────────
        spr_pts = float(spread5[k])
        bid = float(m5["close"].iat[k]); ask = bid + spr_pts * point
        cur = ask if direction == "BUY" else bid
        entry_type = om.get_entry_type(conf)
        if entry_type == "MARKET":
            price, subtype = cur, "MARKET"
        else:
            price = om.get_limit_price(symbol, direction, conf, cur)
            if direction == "BUY":
                subtype = "LIMIT" if price < cur else "STOP"
            else:
                subtype = "LIMIT" if price > cur else "STOP"

        m5w60 = m5.iloc[k - 59:k + 1]
        sl = rm.find_structure_sl(m5w60, direction, symbol)
        if sl is None:
            continue
        if (direction == "BUY" and sl >= price) or (direction == "SELL" and sl <= price):
            continue
        atr_factor = rm.calculate_atr_risk_factor(m5w60)

        bd = conf["breakdown"]
        rows.append({
            "symbol": symbol, "T": pd.Timestamp(T[k], tz="UTC"), "bar_idx": int(k),
            "direction": direction, "probability": conf["probability"], "raw_score": conf["raw_score"],
            "checks_passed": conf["checks_passed"],
            "entry_type": entry_type, "subtype": subtype, "price": float(price), "sl_raw": float(sl),
            "atr_factor": float(atr_factor), "size_factor": float(size_factor),
            "bid": bid, "ask": ask, "spread_pts": spr_pts,
            "trend_score": bd["trend"]["score"], "trend_strong": bool(trend.get("strong", False)),
            "fvg_sig": bd["fvg"]["signal"], "fvg_score": bd["fvg"]["score"],
            "qt_sig": qt["signal"], "qt_score": qt["score"], "qt_phase": qt.get("phase"),
            "sweep_sig": sweep["signal"], "sweep_score": sweep["score"],
            "candle_sig": candle["signal"], "candle_score": candle["score"],
            "pattern": candle.get("pattern", "None"),
            **({"status": "candidate", "trend_label": trend.get("trend"), "fvg_type": fvg.get("fvg_type")}
               if diag else {}),
        })

    return pd.DataFrame(rows)


def build_candidates_cached(symbol: str, bars: dict, cfg: SignalConfig, point: float,
                            refresh: bool = False) -> pd.DataFrame:
    CACHE_DIR.mkdir(exist_ok=True)
    fp = hashlib.md5(
        f"{symbol}|{len(bars['M5'])}|{bars['M5']['time'].iat[0]}|{bars['M5']['time'].iat[-1]}|{cfg.signature()}".encode()
    ).hexdigest()[:10]
    path = CACHE_DIR / f"cand_{symbol}_{cfg.sweep_mode}_{fp}.parquet"
    if path.exists() and not refresh:
        return pd.read_parquet(path)
    df = build_candidates(symbol, bars, cfg, point)
    df.to_parquet(path, index=False)
    return df
