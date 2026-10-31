"""
Append-only JSONL log of what the bot proposed and did, for later offline evaluation (research/GAMEPLAN.md).

One line per event: {"ts_utc", "kind", ...}. Kinds written by main.py:
  candidate  a pair/candle where the checklist proposed BUY or SELL (features, LLM answer, final decision)
  blocked    a proposed trade stopped after the entry conditions passed (duplicate guard, correlation, no tick)
  order      an order or pending order the broker accepted (with its ticket, for joining to MT5 deal history)
The log never raises: a failed write is logged as a warning and trading carries on.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

LOG_PATH = Path(__file__).resolve().parent.parent / "logs" / "candidates.jsonl"


def log_event(kind: str, record: dict, path: Optional[Path] = None) -> None:
    try:
        path = Path(path or LOG_PATH)
        path.parent.mkdir(parents=True, exist_ok=True)
        row = {"ts_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "kind": kind, **record}
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, default=str) + "\n")
    except Exception as e:
        logger.warning(f"candidate log write failed ({type(e).__name__}): {e}")
