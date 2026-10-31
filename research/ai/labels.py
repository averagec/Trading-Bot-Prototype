"""Triple-barrier labels in R with the project's real costs; stateless per decision bar.

Why this label (see research/reports/ai_edge_approach.md section 3)
    cheap label      sign of the next-N-bar return: ignores spread, commission, stop geometry. A model can "win" on it and
                     still lose money; it is only useful as a feasibility measure of predictability.
    realistic label  NET R of a trade opened at the decision bar's close under the live framework: market entry at ask
                     (long) / bid (short), stop = max(floor, k x ATR), take-profit = rr x stop, pessimistic tie rule
                     (stop wins), time barrier, Friday flatten at 20:00 UTC, spread paid through bid/ask, commission
                     2 x $2.50 per lot expressed in R. This is the quantity the bot's P&L is made of, so a score that
                     does not predict THIS cannot be traded. Each bar is labelled independently of any open position
                     (stateless), for both directions.

Bar-level mechanics deliberately mirror research/sim.py (`_bar_exit`, `_close`, `_friday_flatten`):
    * decision cycle time T = close of the decision bar = open of the next M5 bar; entry at the last M5 close (+spread
      for longs, as signals.build_candidates does);
    * the path starts with the M5 bar that opens at T; stop gap-through fills at the bar open; take-profit fills at the
      target; a stop and a target in the same M5 bar -> stop;
    * Friday: the position is closed at the close of the M5 bar that ends at 20:00 UTC (after its stop / target check);
      decisions at or after Friday 20:00 are invalid (the simulator blocks entries from then on);
    * time barrier: closed at the close of the M5 bar that ends at T + horizon.
Not modelled (stated, not hidden): breakeven (the "H6" framework removes it), lot rounding, the JPY P&L conversion
(second order: exit price vs stop price), pre-trade validator rejections, margin.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from research.ai import dataset as ds
from research.ai.indicators import atr

REASONS = {0: "TP", 1: "SL", 2: "TIME", 3: "FLAT", -1: "CENSORED"}
_FIVE_MIN = np.timedelta64(5, "m")


@dataclass(frozen=True)
class LabelSpec:
    k_atr: float = 1.5              # stop = max(floor_pips, k_atr x ATR(atr_n) of the decision timeframe)
    atr_n: int = 14
    floor_pips: float = 10.0        # live MIN_SL_PIPS
    rr: float = 3.0                 # live RR_RATIO
    horizon_min: int = 2880         # time barrier: 48 hours
    flatten_hour_utc: float = 20.0  # Friday flatten (live FRIDAY_FLATTEN_UTC); None disables
    comm_per_lot_side: float = 2.5
    spread_col: str = "spread_clean_pts"       # or "spread_pts" for the simulator-faithful (sentinel kept) cost
    spread_mult: float = 1.0
    min_unit_prob_edge: float = 0.0            # reserved


def _friday_flatten_open(T: np.ndarray, hour: float, bar_min: int = 5) -> tuple[np.ndarray, np.ndarray]:
    """For decision times T (datetime64[ns]): (valid, open-time of the path bar (bar_min minutes) that ENDS at the Friday flatten).
    Invalid: Saturday/Sunday decisions and Friday decisions at or after the flatten hour."""
    day = T.astype("datetime64[D]")
    dow = ((day.astype(np.int64) + 3) % 7)               # Monday = 0 (1970-01-01 was a Thursday)
    since_midnight = (T - day.astype("datetime64[ns]"))
    flat_td = np.timedelta64(int(round(hour * 60)), "m")
    valid = (dow <= 4) & ~((dow == 4) & (since_midnight >= flat_td))
    friday = day + (4 - dow).astype("timedelta64[D]")
    flat_end = friday.astype("datetime64[ns]") + flat_td
    return valid, flat_end - np.timedelta64(int(bar_min), "m")


def barrier_scan(t: np.ndarray, O: np.ndarray, H: np.ndarray, L: np.ndarray, C: np.ndarray, S: np.ndarray,
                 entry_row: np.ndarray, stop_dist: np.ndarray, side: int, rr: float, horizon_min: int,
                 flatten_hour: float | None, bar_min: int = 5) -> dict:
    """Triple barrier for one side (+1 long, -1 short) on the M5 arrays. `entry_row[i]` is the index of the last M5 bar
    of decision i (its close is the entry reference). Returns arrays: entry, exit, exit_row, reason (code), valid."""
    n = len(entry_row)
    m = entry_row.astype(np.int64)
    bar = np.timedelta64(int(bar_min), "m")
    T = t[m] + bar
    bid0 = C[m]
    ask0 = bid0 + S[m]
    entry = ask0 if side > 0 else bid0
    sl = entry - side * stop_dist
    tp = entry + side * rr * stop_dist

    lim_h = T + np.timedelta64(int(horizon_min), "m") - bar
    if flatten_hour is not None:
        fvalid, flat_open = _friday_flatten_open(T, flatten_hour, bar_min)
        is_flat = fvalid & (flat_open <= lim_h)
        limit = np.where(is_flat, flat_open, lim_h)
    else:
        fvalid = np.ones(n, bool)
        is_flat = np.zeros(n, bool)
        limit = lim_h
    end_row = np.searchsorted(t, limit, side="left")             # first bar opening at/after the limit
    valid = fvalid & (end_row > m)                                  # unresolved rows at the data end stay reason -1

    exit_px = np.full(n, np.nan)
    exit_row = np.full(n, -1, np.int64)
    reason = np.full(n, -1, np.int64)
    act = np.flatnonzero(valid)
    k = 1
    while len(act):
        j = m[act] + k
        over = (j > end_row[act]) | (j >= len(t))                 # data ended before the label resolved
        if over.any():
            act = act[~over]
            j = j[~over]
            if not len(act):
                break
        o, h, l, c, s = O[j], H[j], L[j], C[j], S[j]
        if side > 0:
            sl_hit = l <= sl[act]
            tp_hit = h >= tp[act]
            sl_px = np.where(o <= sl[act], o, sl[act])
            close_px = c
        else:
            sl_hit = h + s >= sl[act]
            tp_hit = l + s <= tp[act]
            sl_px = np.where(o + s >= sl[act], o + s, sl[act])
            close_px = c + s
        hit_sl = sl_hit
        hit_tp = tp_hit & ~sl_hit                                 # stop wins ties
        at_end = (j == end_row[act]) & ~hit_sl & ~hit_tp

        rows = act[hit_sl]
        exit_px[rows], exit_row[rows], reason[rows] = sl_px[hit_sl], j[hit_sl], 1
        rows = act[hit_tp]
        exit_px[rows], exit_row[rows], reason[rows] = tp[rows], j[hit_tp], 0
        rows = act[at_end]
        exit_px[rows], exit_row[rows] = close_px[at_end], j[at_end]
        reason[rows] = np.where(is_flat[rows], 3, 2)
        act = act[~(hit_sl | hit_tp | at_end)]
        k += 1
    return {"entry": entry, "exit": exit_px, "exit_row": exit_row, "reason": reason, "valid": valid & (reason >= 0),
            "sl": sl, "tp": tp}


def commission_r(symbol: str, spec: dict, price: np.ndarray, stop_dist: np.ndarray, comm_side: float) -> np.ndarray:
    """Round-trip commission as a fraction of the stop distance (= commission in R)."""
    return ds.commission_pips(symbol, spec, price, comm_side) * ds.pip_size(spec) / stop_dist


def decision_rows(dec: pd.DataFrame, m5_time: np.ndarray, tf_min: int, bar_min: int = 5) -> np.ndarray:
    """Index of the last path bar of each decision bar (open = decision close T - bar_min); -1 if that bar is missing.
    T is the frame's `T` column if present (D1 bars have a variable length), else open + tf_min."""
    if "T" in dec.columns:
        want = dec["T"].values.astype("datetime64[ns]") - np.timedelta64(int(bar_min), "m")
    else:
        want = dec["time"].values.astype("datetime64[ns]") + np.timedelta64(tf_min - bar_min, "m")
    pos = np.searchsorted(m5_time, want)
    pos_c = np.minimum(pos, len(m5_time) - 1)
    return np.where(m5_time[pos_c] == want, pos_c, -1)


def average_uniqueness(start: np.ndarray, end: np.ndarray, n_grid: int) -> np.ndarray:
    """Lopez de Prado average uniqueness of labels spanning grid positions [start_i, end_i] (inclusive):
    mean over the span of 1 / (number of labels alive at that position). In (0, 1]; 1 = no overlap."""
    start = np.asarray(start, np.int64)
    end = np.asarray(end, np.int64)
    diff = np.zeros(n_grid + 1)
    np.add.at(diff, start, 1.0)
    np.add.at(diff, end + 1, -1.0)
    conc = np.cumsum(diff)[:n_grid]
    inv = np.where(conc > 0, 1.0 / np.maximum(conc, 1.0), 0.0)
    pref = np.concatenate([[0.0], np.cumsum(inv)])
    return (pref[end + 1] - pref[start]) / (end - start + 1)


def label_pair(symbol: str, dec: pd.DataFrame, m5: pd.DataFrame, specs: dict, ls: LabelSpec = LabelSpec(),
               tf_min: int = 60, bar_min: int = 5) -> pd.DataFrame:
    """Labels for every decision bar of `dec` (H1 or M15 frame from dataset.load_bars). One row per decision bar:

        time (decision bar OPEN), T (decision cycle time), stop_pips, atr_pips, floor_binds,
        {r,rg,reason,exit_T,w}_{L,S}: net R, gross R, exit reason code, exit time, uniqueness weight  (long / short),
        valid_L / valid_S, cost_r_lab (first-order spread+commission over stop).
    Rows without a valid label (censored by the end of data, weekend, after Friday flatten, missing M5 bar) are kept
    with valid_* False so callers can see how many were dropped."""
    spec = specs[symbol]
    pip = ds.pip_size(spec)
    t5 = m5["time"].values.astype("datetime64[ns]")
    O, H, L, C = (m5[c].to_numpy(float) for c in ("open", "high", "low", "close"))
    S = m5[ls.spread_col].to_numpy(float) * spec["point"] * ls.spread_mult
    erow = decision_rows(dec, t5, tf_min, bar_min)
    ok_row = erow >= 0
    a = atr(dec["high"].to_numpy(), dec["low"].to_numpy(), dec["close"].to_numpy(), ls.atr_n)
    stop = np.maximum(ls.floor_pips * pip, ls.k_atr * a)
    ok_row &= np.isfinite(stop)
    erow_f = np.where(ok_row, erow, 0)
    out = pd.DataFrame({"time": dec["time"].to_numpy(), "stop_pips": stop / pip, "atr_pips": a / pip,
                        "floor_binds": stop <= ls.floor_pips * pip * (1 + 1e-9)})
    out["T"] = dec["T"].to_numpy() if "T" in dec.columns else out["time"] + pd.Timedelta(minutes=tf_min)
    stop_f = np.where(ok_row, stop, 1.0)
    comm = commission_r(symbol, spec, C[erow_f], stop_f, ls.comm_per_lot_side)
    out["cost_r_lab"] = (S[erow_f] + comm * stop_f) / stop_f
    grid_t = dec["time"].values.astype("datetime64[ns]")
    for tag, side in (("L", 1), ("S", -1)):
        res = barrier_scan(t5, O, H, L, C, S, erow_f, stop_f, side, ls.rr, ls.horizon_min, ls.flatten_hour_utc, bar_min)
        valid = res["valid"] & ok_row
        gross = side * (res["exit"] - res["entry"]) / stop_f
        out[f"rg_{tag}"] = np.where(valid, gross, np.nan)
        out[f"r_{tag}"] = np.where(valid, gross - comm, np.nan)
        out[f"reason_{tag}"] = np.where(valid, res["reason"], -1)
        exit_t = np.where(valid, t5[np.where(valid, res["exit_row"], 0)], np.datetime64("NaT"))
        out[f"exit_T_{tag}"] = pd.to_datetime(exit_t, utc=True) + pd.Timedelta(minutes=bar_min)
        out[f"valid_{tag}"] = valid
        w = np.full(len(out), np.nan)
        vi = np.flatnonzero(valid)
        if len(vi):
            e_pos = np.searchsorted(grid_t, np.asarray(exit_t[vi], dtype="datetime64[ns]"), side="right") - 1
            e_pos = np.maximum(e_pos, vi)
            w[vi] = average_uniqueness(vi, e_pos, len(out))
        out[f"w_{tag}"] = w
    return out
