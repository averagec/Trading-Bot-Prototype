"""The three pre-declared model settings (ai_edge_prereg.md section 5). No tuning on results."""
from __future__ import annotations

import numpy as np

from research.ai import _env

SETTINGS = {
    "LR": {"kind": "lr", "C": 0.05},
    "HGB_S": {"kind": "hgb", "max_depth": 2, "max_iter": 100, "learning_rate": 0.05, "min_samples_leaf": 2000,
              "l2_regularization": 10.0},
    "HGB_M": {"kind": "hgb", "max_depth": 4, "max_iter": 200, "learning_rate": 0.03, "min_samples_leaf": 1000,
              "l2_regularization": 10.0},
}


def fit_predict(name: str, Xtr: np.ndarray, ytr: np.ndarray, wtr: np.ndarray, Xte_list: list, seed: int = 0) -> list:
    """Fit on (Xtr, ytr, wtr) and return P(y=1) for every matrix in Xte_list. Weights are normalised to mean 1."""
    _env.enable_sklearn()
    cfg = SETTINGS[name]
    w = np.asarray(wtr, float)
    w = w / w.mean()
    if cfg["kind"] == "lr":
        from sklearn.linear_model import LogisticRegression
        from sklearn.preprocessing import StandardScaler
        sc = StandardScaler().fit(Xtr)
        m = LogisticRegression(C=cfg["C"], max_iter=300, solver="lbfgs")
        m.fit(sc.transform(Xtr), ytr, sample_weight=w)
        return [m.predict_proba(sc.transform(X))[:, 1] for X in Xte_list]
    from sklearn.ensemble import HistGradientBoostingClassifier
    m = HistGradientBoostingClassifier(
        max_depth=cfg["max_depth"], max_iter=cfg["max_iter"], learning_rate=cfg["learning_rate"],
        min_samples_leaf=cfg["min_samples_leaf"], l2_regularization=cfg["l2_regularization"], max_bins=64,
        early_stopping=False, random_state=seed)
    m.fit(Xtr, ytr, sample_weight=w)
    return [m.predict_proba(X)[:, 1] for X in Xte_list]


# Slow-horizon study (ai_slow_prereg.md): same learners, smaller leaves because H4/D1 have far fewer rows.
SETTINGS["HGB_S_slow"] = {"kind": "hgb", "max_depth": 2, "max_iter": 100, "learning_rate": 0.05, "min_samples_leaf": 200,
                          "l2_regularization": 10.0}
SETTINGS["HGB_M_slow"] = {"kind": "hgb", "max_depth": 3, "max_iter": 150, "learning_rate": 0.03, "min_samples_leaf": 100,
                          "l2_regularization": 10.0}
