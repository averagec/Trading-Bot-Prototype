"""PostToolUse hook (Edit|Write): after a change to live bot code, compile it and run the live-change tests.

Reads the hook payload on stdin. Anything outside the live files is ignored. MT5 is never contacted: the tests mock it.
Exit 0 = fine/ignored, exit 2 = failure (stderr is fed back to Claude).
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LIVE_FILES = {"main.py", "config.py", "mt5_connector.py"}
LIVE_DIRS = {"utils", "strategies", "agents"}
TESTS = [
    "research/tests/test_live_friday_flatten.py",
    "research/tests/test_fidelity.py",
]


def is_live(path: Path) -> bool:
    try:
        rel = path.resolve().relative_to(ROOT)
    except ValueError:
        return False
    if path.suffix != ".py":
        return False
    return rel.as_posix() in LIVE_FILES or (len(rel.parts) > 1 and rel.parts[0] in LIVE_DIRS)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    tool_input = payload.get("tool_input") or {}
    raw = tool_input.get("file_path") or (payload.get("tool_response") or {}).get("filePath")
    if not raw:
        return 0
    path = Path(raw)
    if not is_live(path):
        return 0

    comp = subprocess.run([sys.executable, "-m", "py_compile", str(path)], cwd=ROOT, capture_output=True, text=True)
    if comp.returncode != 0:
        print(f"LIVE-CODE HOOK: py_compile failed for {path.name}\n{comp.stderr}", file=sys.stderr)
        return 2

    # own temp dir per run (on A:) so parallel sessions / test runs never fight over research/.pytest_tmp
    basetemp = ROOT / "research" / f".pytest_tmp_hook_{os.getpid()}"
    try:
        tests = subprocess.run(
            [sys.executable, "-m", "pytest", *TESTS, "-q", "-x", "-p", "no:cacheprovider", f"--basetemp={basetemp}"],
            cwd=ROOT, capture_output=True, text=True, timeout=300,
        )
    finally:
        shutil.rmtree(basetemp, ignore_errors=True)
    if tests.returncode != 0:
        tail = "\n".join((tests.stdout + tests.stderr).splitlines()[-25:])
        print(f"LIVE-CODE HOOK: tests failed after editing {path.name}\n{tail}", file=sys.stderr)
        return 2

    print(f"live-code hook: {path.name} compiles, live-change tests pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
