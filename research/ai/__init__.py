"""AI-edge research package (classical ML on price data; DEV window only).

Importing the package caps BLAS/OpenMP threads at 4 before numpy is loaded by any submodule.
Nothing here is imported by, or modifies, the live bot.
"""
from research.ai import _env

_env.cap_threads()
