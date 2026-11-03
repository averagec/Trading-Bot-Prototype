"""
Independent hand check of treated trades of an ATR-floor DEV run against the raw bars (pre-registration section 8).

For a random sample of the K10 real-signal trades (30 drawn with numpy seed 20261031, plus 10 XAUUSD trades) it recomputes, without the simulator's
code: the ATR(H1,14) at the decision time from the raw H1 bars (plain loop), the floored stop distance (= max of the matched control trade's stop distance
and k x ATR), the lot size from the 0.25 % risk rule, the 3R target, and replays the trade bar by bar on the raw M5 bars (stop / target / breakeven /
Friday flatten) to compare exit reason, exit bar and exit price. DEV only (it reads the run's own trades and bars before 2021-06-01).

    python -m research.atr_floor_handcheck --tag atr_floor_dev
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from research.data import load_history, load_specs, prepare_bars
from research.run import OUT

K, N_ATR, SEED = 1.0, 14, 20261031
DEV_START, DEV_END = "2014-01-01", "2021-06-01"


def atr_loop(h1: pd.DataFrame, T: pd.Timestamp, n: int = N_ATR) -> float:
    """Mean true range of the last n H1 bars closed at or before T (bar open + 1 h <= T), written as a plain loop on raw bars."""
    closed = h1[h1["time"] + pd.Timedelta(hours=1) <= T].tail(n + 1)
    hi, lo, cl = closed["high"].tolist(), closed["low"].tolist(), closed["close"].tolist()
    if len(hi) < n + 1:
        return float("nan")
    tr = [max(hi[i] - lo[i], abs(hi[i] - cl[i - 1]), abs(lo[i] - cl[i - 1])) for i in range(1, n + 1)]
    return sum(tr) / n


def replay(tr: pd.Series, m5: pd.DataFrame, point: float, digits: int) -> dict:
    """Replay one trade on raw M5 bars with the simulator's documented rules (not its code). Step time = bar open + 5 min."""
    step = m5["time"] + pd.Timedelta(minutes=5)
    j0 = int(step.searchsorted(tr["open_T"]))
    d, entry, sl, tp = tr["direction"], float(tr["entry"]), float(tr["sl0"]), float(tr["tp"])
    dist = abs(entry - sl)
    pip = 0.01 if "JPY" in tr["symbol"] else 0.0001
    be_moved = False
    pending_fill = tr["subtype"] != "MARKET"
    O, H, L, C = (m5[c].to_numpy(float) for c in ("open", "high", "low", "close"))
    S = m5["spread_pts"].to_numpy(float) * point
    for j in range(j0, min(j0 + 20000, len(m5))):
        T = step.iloc[j]
        fill_bar = j == j0
        if fill_bar and not pending_fill:
            pass                                                              # market fill: exits start on the next bar
        else:
            op, hi, lo, sp = O[j], H[j], L[j], S[j]
            if d == "BUY":
                sl_hit, tp_hit = lo <= sl, hi >= tp
                sl_px = op if op <= sl else sl
            else:
                sl_hit, tp_hit = hi + sp >= sl, lo + sp <= tp
                sl_px = (op + sp) if (op + sp) >= sl else sl
            if fill_bar:
                tp_hit = False                                                # pessimistic fill-bar rule: only the stop counts
            if sl_hit:
                return {"reason": "BE" if be_moved else "SL", "close_T": T, "exit": sl_px}
            if tp_hit:
                return {"reason": "TP", "close_T": T, "exit": tp}
        # Friday flatten after the bar's own stop / target check
        if T.dayofweek == 4 and (T.hour + T.minute / 60.0) >= 20.0:
            return {"reason": "FLAT", "close_T": T, "exit": C[j] if d == "BUY" else C[j] + S[j]}
        # breakeven at the cycle (close-based trigger, buffer 2 pips)
        up = (C[j] - entry) if d == "BUY" else (entry - (C[j] + S[j]))
        already = sl >= entry if d == "BUY" else sl <= entry
        if up >= dist and not already:
            sl = round(entry + 2 * pip, 5) if d == "BUY" else round(entry - 2 * pip, 5)
            be_moved = True
    return {"reason": "NOEXIT", "close_T": None, "exit": float("nan")}


def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="atr_floor_dev")
    a = ap.parse_args(argv)
    outdir = OUT / a.tag
    tr = pd.read_parquet(outdir / "trades.parquet")
    for c in ("placed_T", "open_T", "close_T"):
        tr[c] = pd.to_datetime(tr[c], utc=True)
    assert tr["open_T"].max() < pd.Timestamp(DEV_END, tz="UTC"), "trades beyond the DEV window"
    real = tr[(tr.seed == -1) & (tr.exit_reason != "END")]
    tre, ctl = real[real.arm == "K10"].reset_index(drop=True), real[real.arm == "B0"].set_index(["symbol", "placed_T"])
    rng = np.random.default_rng(SEED)
    pick = list(rng.choice(len(tre), 30, replace=False))
    gold = [i for i in np.flatnonzero((tre.symbol == "XAUUSD").to_numpy()) if i not in pick]
    pick += list(rng.choice(gold, 10, replace=False))
    specs = load_specs()
    bars, h1s = {}, {}
    for s in sorted(set(tre.loc[pick, "symbol"])):
        bars[s] = prepare_bars(s, start=DEV_START, end=DEV_END, sentinel_missing=True)["M5"]
        h1s[s] = load_history(s, "H1", end=DEV_END)
    done = tre.sort_values("close_T")
    lines, ok_atr, ok_dist, ok_lot, ok_tp, ok_exit, n_match = [], 0, 0, 0, 0, 0, 0
    for n, i in enumerate(pick):
        r = tre.loc[i]
        sym, spec = r["symbol"], specs[r["symbol"]]
        point, digits = spec["point"], spec["digits"]
        T = r["placed_T"]
        atr = atr_loop(h1s[sym], T)
        dist = abs(r["entry"] - r["sl0"])
        tol = 1.01 * point * 10 ** (0 if digits <= 2 else 0)                 # prices are rounded to the symbol's digits
        key = (sym, T)
        if key in ctl.index:
            cd = abs(ctl.loc[key]["entry"] - ctl.loc[key]["sl0"])
            expect = max(cd, K * atr)
            n_match += 1
            dist_ok = abs(dist - expect) <= 2 * point + 1e-9
        else:
            cd, expect, dist_ok = float("nan"), max(K * atr, 0.0), dist >= K * atr - 2 * point
        atr_ok = bool(np.isfinite(atr)) and dist >= K * atr - 2 * point
        # lot size from the risk rule: balance = 100,000 + net P/L of treated trades closed at or before the decision time
        bal = 100_000.0 + float(done.loc[done.close_T <= T, "net_pnl"].sum())
        allowed = 0.0025 * bal
        px = float(r["sl0"])
        lpl = dist * spec["contract_size"] / (px if (sym.startswith("USD")) else 1.0)
        vol_exp = math.floor(allowed / lpl / spec["volume_step"] + 1e-9) * spec["volume_step"]
        lot_ok = abs(vol_exp - r["volume"]) <= spec["volume_step"] + 1e-9
        tp_ok = abs(abs(r["tp"] - r["entry"]) - 3 * dist) <= 3 * point + 1e-9
        rp = replay(r, bars[sym], point, digits)
        exit_ok = rp["reason"] == r["exit_reason"] and rp["close_T"] == r["close_T"] and abs(rp["exit"] - r["exit"]) <= 2 * point + 1e-9
        ok_atr += atr_ok; ok_dist += dist_ok; ok_lot += lot_ok; ok_tp += tp_ok; ok_exit += exit_ok
        lines.append(f"{n:2d} {sym:7s} {r['direction']:4s} {r['subtype']:6s} T={T:%Y-%m-%d %H:%M} atr={atr:.5f} stop={dist:.5f} ctl_stop={cd:.5f} "
                     f"expect={expect:.5f} dist_ok={dist_ok} lot sim/exp={r['volume']:.2f}/{vol_exp:.2f} tp_ok={tp_ok} "
                     f"exit sim={r['exit_reason']}@{r['close_T']:%m-%d %H:%M} replay={rp['reason']}@{rp['close_T']:%m-%d %H:%M} px_diff={abs(rp['exit'] - r['exit']):.5f} ok={exit_ok}"
                     if rp["close_T"] is not None else f"{n:2d} {sym} replay found no exit")
    summary = (f"trades checked {len(pick)} (30 random + 10 gold, seed {SEED}); stop >= k x ATR: {ok_atr}; stop == max(control stop, k x ATR) "
               f"(matched {n_match}): {ok_dist}; lot size within one step of the 0.25 % rule: {ok_lot}; target = 3 x stop: {ok_tp}; "
               f"independent replay reproduces exit reason, bar and price: {ok_exit}")
    text = "\n".join(lines + ["", summary])
    (outdir / "handcheck.txt").write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
