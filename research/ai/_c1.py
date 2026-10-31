"""Pre-registered C1 check (Holm-level one-sided lower bound of D1) for each model setting."""
import json

from research.ai.learnability import LEARN, holm_lower_bound

out = [holm_lower_bound(m) for m in ("LR", "HGB_S", "HGB_M")]
(LEARN / "c1.json").write_text(json.dumps(out, indent=1))
for r in out:
    print(r)
