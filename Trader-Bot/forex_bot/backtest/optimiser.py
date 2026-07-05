"""
Optimiser
Takes backtest results and finds optimal config settings.
"""

import sys
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

logger = logging.getLogger(__name__)


class Optimiser:

    def __init__(self, backtest_results: dict):
        self.results      = backtest_results
        self.best_per_sym = backtest_results.get("best_per_symbol", {})
        self.all_results  = backtest_results.get("all_results", [])

    def get_optimal_weights(self) -> dict:
        strategy_scores = {
            "liquidity_sweep": [], "quarterly_theory": [],
            "candle_pattern":  [], "trend": [], "fvg": [],
        }

        top_results = self.all_results[:max(1, len(self.all_results) // 5)]

        for result in top_results:
            wr = result.get("win_rate", 0)
            pf = result.get("profit_factor", 0)
            for strategy in result.get("strategies", []):
                if strategy in strategy_scores:
                    strategy_scores[strategy].append(wr * 0.5 + min(pf, 3) * 16.67 * 0.5)

        avg_scores = {k: sum(v) / len(v) if v else 0 for k, v in strategy_scores.items()}
        total      = sum(avg_scores.values())

        if total > 0:
            return {k: round((v / total) * 100) for k, v in avg_scores.items()}
        return {
            "liquidity_sweep": 25, "quarterly_theory": 20,
            "candle_pattern":  20, "trend": 20, "fvg": 15,
        }

    def get_optimal_threshold(self) -> int:
        if not self.all_results:
            return 65

        best_threshold = 65
        best_pf        = 0

        for threshold in range(50, 85, 5):
            filtered  = [r for r in self.all_results if r.get("win_rate", 0) >= threshold * 0.7]
            if not filtered:
                continue
            avg_pf     = sum(r["profit_factor"] for r in filtered) / len(filtered)
            avg_trades = sum(r["total_trades"] for r in filtered) / len(filtered)
            if avg_trades >= 10 and avg_pf > best_pf:
                best_pf        = avg_pf
                best_threshold = threshold

        return best_threshold

    def get_best_combo_per_pair(self) -> dict:
        return {
            sym: {
                "strategies":    data["strategies"],
                "win_rate":      data["win_rate"],
                "profit_factor": data["profit_factor"],
                "total_trades":  data["total_trades"],
                "expectancy":    data["expectancy"],
            }
            for sym, data in self.best_per_sym.items()
        }

    def generate_summary(self) -> dict:
        weights   = self.get_optimal_weights()
        threshold = self.get_optimal_threshold()
        combos    = self.get_best_combo_per_pair()

        valid     = [r for r in self.all_results if r["valid"]]
        avg_wr    = sum(r["win_rate"] for r in valid) / len(valid) if valid else 0
        avg_pf    = sum(r["profit_factor"] for r in valid) / len(valid) if valid else 0

        lines = [f"Optimal threshold: {threshold}%", "", "Score weights:"]
        for s, w in sorted(weights.items(), key=lambda x: x[1], reverse=True):
            lines.append(f"  {s}: {w} pts")
        lines.append("")
        lines.append("Best combo per pair:")
        for sym, data in combos.items():
            lines.append(f"  {sym}: {' + '.join(data['strategies'])} | WR: {data['win_rate']}% | PF: {data['profit_factor']}")

        return {
            "optimal_weights":   weights,
            "optimal_threshold": threshold,
            "best_per_pair":     combos,
            "overall_stats": {
                "avg_win_rate":       round(avg_wr, 1),
                "avg_profit_factor":  round(avg_pf, 2),
                "total_valid_combos": len(valid),
            },
            "recommendation": "\n".join(lines),
        }

    def apply_to_config(self, config_path: str = "config.py", dry_run: bool = True) -> bool:
        summary   = self.generate_summary()
        threshold = summary["optimal_threshold"]

        if dry_run:
            logger.info(f"DRY RUN - would set MIN_SCORE_TO_TRADE = {threshold}")
            return True

        try:
            import re
            with open(config_path, "r") as f:
                content = f.read()
            content = re.sub(r"MIN_SCORE_TO_TRADE\s*=\s*\d+", f"MIN_SCORE_TO_TRADE = {threshold}", content)
            with open(config_path, "w") as f:
                f.write(content)
            logger.info(f"Config updated: threshold = {threshold}")
            return True
        except Exception as e:
            logger.error(f"Config update failed: {e}")
            return False