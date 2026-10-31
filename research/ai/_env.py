"""Environment helpers for the AI-edge study: thread cap and the private scikit-learn install.

scikit-learn is NOT in requirements.txt. It was installed once, on 2026-10-29, into a private directory on A:
(`pip install --target research/output/ai/pkgs scikit-learn`, PIP_USER=0, TMP/TEMP on A:), so the project's own
site-packages are untouched. `enable_sklearn()` appends that directory to sys.path (after the normal packages).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

MAX_THREADS = 4          # the lead's cap while other jobs run
PKGS = Path(__file__).resolve().parents[1] / "output" / "ai" / "pkgs"


def cap_threads(n: int = MAX_THREADS) -> None:
    """Cap BLAS / OpenMP threads. Only effective if called before numpy / sklearn are first imported."""
    n = min(int(n), MAX_THREADS)
    for k in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ[k] = str(min(n, int(os.environ.get(k, n))))


def enable_sklearn():
    """Make `import sklearn` work (private install) and return the module."""
    cap_threads()
    if PKGS.exists() and str(PKGS) not in sys.path:
        sys.path.append(str(PKGS))
    try:
        import sklearn
    except ImportError as e:                                   # pragma: no cover
        raise ImportError("scikit-learn not available: pip install --target research/output/ai/pkgs scikit-learn "
                          "(with PIP_USER=0 and TMP/TEMP on A:)") from e
    return sklearn
