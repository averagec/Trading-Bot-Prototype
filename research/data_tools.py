"""
Command-line front end for the data-depth tooling.  Run from the project root (A:\\Trader-Bot\\forex_bot):

  python -m research.data_tools probe                         read-only: terminal Max-bars, ini value, M1/M5 depth per symbol
  python -m research.data_tools fetch                         read-only pull, APPENDS + de-duplicates into research\\data
  python -m research.data_tools fetch --tail                  cheap incremental pull (only bars since the cached end)
  python -m research.data_tools fetch --max-depth             after raising 'Max bars in chart': ask for as many bars as allowed
  python -m research.data_tools quality [--tfs M5 H1] [--detail]    data-quality report of the cache
  python -m research.data_tools import --symbol EURUSD --file F --tz UTC [--side bid]       validate only (dry run)
  python -m research.data_tools import --symbol EURUSD --file F --tz UTC --commit           validate, and merge if it passes
  python -m research.data_tools synth --out DIR               write a SYNTHETIC cache + vendor files to try the importer on

No command places orders, reads credentials, edits terminal settings or downloads anything.
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from research.data import DATA_DIR, TFS, fetch_history, coverage_report, BARS_WANTED
from research.data_quality import quality_report, format_quality, quality_table


def _symbols(args) -> list:
    if getattr(args, "symbols", None):
        return args.symbols
    try:
        from config import PAIRS            # only PAIRS is used; credentials are never read here
        return list(PAIRS)
    except Exception:
        return sorted({p.name.split("_")[0] for p in Path(args.data_dir).glob("*_M5.parquet")})


def read_ini_maxbars(data_path: str) -> object:
    """MaxBars from <terminal data path>\\config\\common.ini. Reads ONLY the [Charts] section (the file also holds
    account details, which are never touched). Returns int, or None if not found."""
    p = Path(data_path) / "config" / "common.ini"
    if not p.exists():
        return None
    raw = p.read_bytes()
    text = raw.decode("utf-16") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else raw.decode("utf-8", "replace")
    m = re.search(r"^\[Charts\][^\[]*?^MaxBars=(\d+)", text, re.M | re.S)
    return int(m.group(1)) if m else None


def cmd_probe(args) -> int:
    import MetaTrader5 as mt5
    if not mt5.initialize():
        print(f"MT5 initialize failed: {mt5.last_error()} (open the terminal and log in first)")
        return 1
    try:
        ti = mt5.terminal_info()
        print(f"terminal build {ti.build}, connected={ti.connected}, terminal Max-bars (runtime) = {ti.maxbars}")
        print(f"common.ini [Charts] MaxBars = {read_ini_maxbars(ti.data_path)}")
        cap = int(ti.maxbars) - 1
        rows = []
        for sym in _symbols(args):
            for tfn in ("M1", "M5", "H1"):
                tf = getattr(mt5, "TIMEFRAME_" + tfn)
                r = mt5.copy_rates_from_pos(sym, tf, 1, cap)
                if r is None:
                    rows.append(dict(symbol=sym, tf=tfn, bars=None, err=str(mt5.last_error())))
                    continue
                t0 = pd.to_datetime(int(r["time"][0]), unit="s")
                rows.append(dict(symbol=sym, tf=tfn, bars=len(r), first_server=t0.date(),
                                 capped=len(r) >= cap, err=""))
        print(pd.DataFrame(rows).to_string(index=False))
        if args.server_depth:
            print("\nserver depth via D1 (WARNING: makes the terminal download M1 history, ~20 MB per symbol-year):")
            for sym in _symbols(args):
                r = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_D1, 1, 9000)
                print(sym, None if r is None else (len(r), pd.to_datetime(int(r["time"][0]), unit="s").date()))
    finally:
        mt5.shutdown()
    return 0


def cmd_fetch(args) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    bars = {"M5": args.m5_bars} if args.m5_bars else None
    fetch_history(_symbols(args), tfs=args.tfs or TFS, bars_wanted=bars, out_dir=Path(args.data_dir),
                  append=not args.overwrite, mode="tail" if args.tail else "full", max_depth=args.max_depth)
    pd.set_option("display.width", 220, "display.max_columns", 30)
    print(coverage_report(_symbols(args), Path(args.data_dir)).to_string(index=False))
    return 0


def cmd_quality(args) -> int:
    pd.set_option("display.width", 220, "display.max_columns", 30)
    syms = _symbols(args)
    print(quality_table(syms, args.tfs or ["M5"], Path(args.data_dir)).to_string(index=False))
    if args.detail:
        for s in syms:
            for tf in args.tfs or ["M5"]:
                p = Path(args.data_dir) / f"{s}_{tf}.parquet"
                if p.exists():
                    rep = quality_report(pd.read_parquet(p), tf=tf, symbol=s)
                    print(format_quality(rep))
                    print("  largest unexplained gaps:", rep["details"].get("largest_unexplained_gaps"))
                    zs = rep["details"].get("zero_spread_pct_by_month")
                    if zs:
                        print("  zero-spread % by month:", zs)
    return 0


def cmd_import(args) -> int:
    from research.data_import import import_external, ImportRefused, Tolerances
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        rep = import_external(args.symbol, args.file, tz=args.tz, price_side=args.side, fmt=args.fmt,
                              ask_path=args.ask_file, name=args.name, data_dir=Path(args.data_dir),
                              spread_scale=args.spread_scale, spread_fixed_pts=args.spread_fixed,
                              commit=args.commit, tol=Tolerances())
    except ImportRefused as e:
        print(e.report.text())
        print("\nREFUSED - nothing was written.")
        return 2
    print(rep.text())
    if not rep.ok:
        print("\nREFUSED (dry run) - fix the reasons above; nothing was written.")
        return 2
    print("\nCOMMITTED to", args.data_dir if rep.committed else "(dry run: nothing written; add --commit to merge)")
    return 0


def cmd_synth(args) -> int:
    from research.synth_vendor import make_truth_m1, build_mt5_cache, write_vendor_file
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    truth = make_truth_m1()
    build_mt5_cache(out / "data", truth, m5_from="2024-09-01", h1_from="2024-08-01", h4_from="2024-07-01")
    for style, tz in (("dukascopy", "UTC"), ("histdata", "EST"), ("mt", "broker")):
        print("wrote", write_vendor_file(truth, out / f"EURUSD_{style}.csv", style, tz))
    print(f"synthetic cache in {out / 'data'} - try:\n  python -m research.data_tools import --symbol EURUSD "
          f"--file {out / 'EURUSD_dukascopy.csv'} --tz UTC --data-dir {out / 'data'}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", default=str(DATA_DIR))
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("probe"); p.add_argument("--symbols", nargs="*")
    p.add_argument("--server-depth", action="store_true", help="also request D1 history (triggers M1 downloads)")
    p.set_defaults(fn=cmd_probe)

    p = sub.add_parser("fetch"); p.add_argument("--symbols", nargs="*"); p.add_argument("--tfs", nargs="*")
    p.add_argument("--tail", action="store_true", help="only bars since the cached end (+overlap)")
    p.add_argument("--max-depth", action="store_true", help="request terminal Max-bars - 1 bars per timeframe")
    p.add_argument("--m5-bars", type=int, help=f"override M5 bars requested (default {BARS_WANTED['M5']})")
    p.add_argument("--overwrite", action="store_true", help="legacy behaviour: overwrite instead of append")
    p.set_defaults(fn=cmd_fetch)

    p = sub.add_parser("quality"); p.add_argument("--symbols", nargs="*"); p.add_argument("--tfs", nargs="*")
    p.add_argument("--detail", action="store_true"); p.set_defaults(fn=cmd_quality)

    p = sub.add_parser("import")
    p.add_argument("--symbol", required=True); p.add_argument("--file", required=True)
    p.add_argument("--tz", required=True, help="timezone of the file's timestamps: UTC | broker | +2 | EST | America/New_York")
    p.add_argument("--side", default="bid", choices=["bid", "ask", "mid"])
    p.add_argument("--ask-file", help="matching ASK file (then --file is the BID file): derives a vendor spread")
    p.add_argument("--fmt", default="auto", choices=["auto", "dukascopy", "histdata", "mt", "generic", "parquet"])
    p.add_argument("--name", help="label stored in the src column (default: file name)")
    p.add_argument("--spread-scale", type=float, default=1.0); p.add_argument("--spread-fixed", type=float)
    p.add_argument("--commit", action="store_true", help="actually merge (default is a validating dry run)")
    p.set_defaults(fn=cmd_import)

    p = sub.add_parser("synth"); p.add_argument("--out", required=True); p.set_defaults(fn=cmd_synth)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
