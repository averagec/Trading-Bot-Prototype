"""Week-block bootstrap on sufficient statistics, ranks, AUC, and calendar-week utilities (vectorised)."""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import rankdata


def week_index(times) -> np.ndarray:
    """Monday-aligned integer week number of tz-aware or naive UTC timestamps (shared across pairs)."""
    t = pd.DatetimeIndex(times)
    if t.tz is not None:
        t = t.tz_convert("UTC").tz_localize(None)
    days = t.values.astype("datetime64[D]").astype(np.int64)
    return (days + 3) // 7


def group_sums(keys: np.ndarray, cols: np.ndarray):
    """Sum rows of `cols` (N x k) by integer `keys`. Returns (unique_keys, sums W x k)."""
    u, inv = np.unique(keys, return_inverse=True)
    if cols.ndim == 1:
        cols = cols[:, None]
    out = np.zeros((len(u), cols.shape[1]))
    for j in range(cols.shape[1]):
        out[:, j] = np.bincount(inv, weights=cols[:, j], minlength=len(u))
    return u, out


def block_bootstrap(stats: np.ndarray, fn, n_boot: int = 1000, seed: int = 0) -> dict:
    """stats: W x k per-block sufficient statistics; fn maps a (k,) or (k, B) array of totals to the statistic.
    Whole blocks (weeks) are resampled with replacement."""
    stats = np.asarray(stats, float)
    W = stats.shape[0]
    point = float(fn(stats.sum(axis=0)))
    if W < 2:
        return {"est": point, "se": np.nan, "lo": np.nan, "hi": np.nan, "blocks": int(W)}
    rng = np.random.default_rng(seed)
    mult = rng.multinomial(W, np.full(W, 1.0 / W), size=n_boot).astype(float)
    with np.errstate(invalid="ignore", divide="ignore"):
        boots = np.asarray(fn((mult @ stats).T), float)
    return {"est": point, "se": float(np.nanstd(boots, ddof=1)),
            "lo": float(np.nanquantile(boots, 0.025)), "hi": float(np.nanquantile(boots, 0.975)), "blocks": int(W)}


def pearson_from_sums(t):
    """t rows: n, sx, sy, sxx, syy, sxy  -> Pearson correlation (works on (6,) or (6, B) arrays)."""
    n, sx, sy, sxx, syy, sxy = t
    cov = sxy / n - (sx / n) * (sy / n)
    vx = sxx / n - (sx / n) ** 2
    vy = syy / n - (sy / n) ** 2
    return cov / np.sqrt(vx * vy)


def corr_stats(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Per-row contributions for pearson_from_sums; rows with NaN in x or y contribute zero."""
    ok = np.isfinite(x) & np.isfinite(y)
    x = np.where(ok, x, 0.0)
    y = np.where(ok, y, 0.0)
    return np.column_stack([ok.astype(float), x, y, x * x, y * y, x * y])


def rank_unit(x: np.ndarray) -> np.ndarray:
    """Average ranks scaled to (0,1) among finite values (NaN stays NaN): Pearson on these = Spearman."""
    x = np.asarray(x, float)
    out = np.full(len(x), np.nan)
    ok = np.isfinite(x)
    if ok.any():
        out[ok] = (rankdata(x[ok]) - 0.5) / ok.sum()
    return out


def auc_score(y: np.ndarray, s: np.ndarray) -> float:
    """Mann-Whitney AUC with average ranks for ties. NaN if one class is missing."""
    y = np.asarray(y).astype(bool)
    n1 = int(y.sum())
    n0 = len(y) - n1
    if n1 == 0 or n0 == 0:
        return float("nan")
    r = rankdata(s)
    return float((r[y].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def logloss(y: np.ndarray, p: np.ndarray, eps: float = 1e-12) -> np.ndarray:
    p = np.clip(p, eps, 1 - eps)
    return -(y * np.log(p) + (1 - y) * np.log(1 - p))


def block_resample_index(keys: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Row indices of one block-bootstrap draw (blocks = unique keys, drawn with replacement)."""
    order = np.argsort(keys, kind="stable")
    ks = keys[order]
    u, start, cnt = np.unique(ks, return_index=True, return_counts=True)
    pick = rng.integers(0, len(u), len(u))
    return np.concatenate([order[start[i]:start[i] + cnt[i]] for i in pick])


def bootstrap_auc(y, s, keys, n_boot: int = 200, seed: int = 0) -> dict:
    rng = np.random.default_rng(seed)
    vals = np.empty(n_boot)
    for b in range(n_boot):
        ix = block_resample_index(keys, rng)
        vals[b] = auc_score(y[ix], s[ix])
    return {"est": auc_score(y, s), "se": float(np.nanstd(vals, ddof=1)),
            "lo": float(np.nanquantile(vals, 0.025)), "hi": float(np.nanquantile(vals, 0.975))}
