"""Read-only D1 history pull for the multi-asset universe (trend and carry studies).

  python -m research.fetch_multi            pull D1 for every non-crypto, non-single-stock symbol the terminal offers

Writes research/data_multi/<SYMBOL>_D1.parquet (time = broker server day, UTC-labelled like the rest of the cache; open, high, low,
close, tick_volume, spread, real_volume) and research/data_multi/specs_multi.json (path, digits, point, contract size, swap
long/short, swap mode as reported NOW, first/last bar). Never places orders, never reads credentials. The only terminal state it
changes is Market Watch: invisible symbols are added for the pull and removed again at the end.
Each symbol is fetched in its own subprocess with a timeout, because a symbol that has to sync from the server can block the
MetaTrader5 call indefinitely.
Single-stock CFDs are excluded on purpose (today's survivors: selection bias); crypto is excluded (history starts about 2017).
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

OUT = Path(__file__).resolve().parent / "data_multi"
SKIP_PREFIX = ("Crypto", "Equities")
N_BARS = 20000
TIMEOUT_S = 240
WAIT_S = 200
PRIORITY = ["GBPUSD", "USDJPY", "USDCAD", "USDCHF", "NZDUSD", "USDNOK", "USDSEK", "US500.cash", "US30.cash", "US100.cash",
            "GER40.cash", "UK100.cash", "JP225.cash", "USOIL.cash", "UKOIL.cash", "NATGAS.cash", "XAUUSD", "XAGUSD", "XCUUSD",
            "XPTUSD", "XPDUSD"]       # pulled first: the G10-vs-USD pairs, the main indices, energy and metals


def fetch_one(name: str) -> int:
    """Worker: pull one symbol, write its parquet and a one-line json spec. Exit code 0 ok, 2 no data."""
    import MetaTrader5 as mt5
    import pandas as pd
    if not mt5.initialize():
        return 1
    try:
        info = mt5.symbol_info(name)
        if info is None:
            return 2
        if not info.visible:
            mt5.symbol_select(name, True)
        rates = None
        t0 = time.time()
        while time.time() - t0 < WAIT_S:
            rates = mt5.copy_rates_from_pos(name, mt5.TIMEFRAME_D1, 0, N_BARS)
            if rates is not None and len(rates) > 100:
                break
            time.sleep(1.0)
        if rates is None or len(rates) == 0:
            return 2
        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
        df = df.sort_values("time").drop_duplicates("time").reset_index(drop=True)
        df.to_parquet(OUT / f"{name}_D1.parquet", index=False)
        spec = {"path": info.path, "bars": int(len(df)), "first": str(df["time"].iloc[0]), "last": str(df["time"].iloc[-1]),
                "digits": info.digits, "point": info.point, "contract_size": info.trade_contract_size,
                "swap_long": info.swap_long, "swap_short": info.swap_short, "swap_mode": info.swap_mode,
                "currency_base": info.currency_base, "currency_profit": info.currency_profit}
        (OUT / f"{name}_spec.json").write_text(json.dumps(spec))
        return 0
    finally:
        mt5.shutdown()


def main() -> int:
    import MetaTrader5 as mt5
    if len(sys.argv) > 2 and sys.argv[1] == "--one":
        OUT.mkdir(parents=True, exist_ok=True)
        return fetch_one(sys.argv[2])
    if not mt5.initialize():
        print(f"MT5 initialize failed: {mt5.last_error()} (open the terminal and log in first)")
        return 1
    OUT.mkdir(parents=True, exist_ok=True)
    syms = [s for s in (mt5.symbols_get() or []) if not s.path.startswith(SKIP_PREFIX)]
    hidden_before = [s.name for s in syms if not s.visible]
    mt5.shutdown()
    specs, failed = {}, []
    try:
        order = {n: i for i, n in enumerate(PRIORITY)}
        for s in sorted(syms, key=lambda x: (order.get(x.name, len(PRIORITY)), x.name)):
            f = OUT / f"{s.name}_spec.json"
            if not f.exists():
                try:
                    rc = subprocess.run([sys.executable, "-m", "research.fetch_multi", "--one", s.name], cwd=str(Path(__file__).resolve().parent.parent),
                                        timeout=TIMEOUT_S, capture_output=True).returncode
                except subprocess.TimeoutExpired:
                    rc = -9
                if rc != 0:
                    failed.append((s.name, rc))
                    print(f"{s.name:12s} FAILED rc={rc}", flush=True)
                    continue
            spec = json.loads(f.read_text())
            specs[s.name] = spec
            print(f"{s.name:12s} {s.path.split(chr(92))[0]:14s} {spec['bars']:6d} bars  {spec['first'][:10]} -> {spec['last'][:10]}", flush=True)
    finally:
        if mt5.initialize():
            for n in hidden_before:
                mt5.symbol_select(n, False)
            mt5.shutdown()
    (OUT / "specs_multi.json").write_text(json.dumps(specs, indent=1))
    print(f"done: {len(specs)} symbols, {len(failed)} failed: {failed}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
