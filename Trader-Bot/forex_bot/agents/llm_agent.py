"""
LLM Decision Agent
Uses Claude to synthesise strategy confluence + news data into a final trade decision.
Provides reasoning, probability assessment, and trade parameters.
"""

import json
import logging
import requests
from datetime import datetime, timezone
from typing import Optional

from config import LLM

logger = logging.getLogger(__name__)

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"


class LLMAgent:

    def __init__(self):
        self.model      = LLM["model"]
        self.max_tokens = LLM["max_tokens"]

    def _call_claude(self, system: str, user: str) -> Optional[str]:
        """Make a call to Claude API."""
        try:
            response = requests.post(
                ANTHROPIC_API_URL,
                headers={"Content-Type": "application/json"},
                json={
                    "model":      self.model,
                    "max_tokens": self.max_tokens,
                    "system":     system,
                    "messages":   [{"role": "user", "content": user}],
                },
                timeout=30,
            )
            response.raise_for_status()
            data = response.json()
            return data["content"][0]["text"]

        except Exception as e:
            logger.error(f"Claude API error: {e}")
            return None

    # ── Primary Decision ───────────────────────────────────────────────────────

    def make_trade_decision(self, symbol: str, confluence: dict, news_summary: dict,
                            account_info: dict, open_positions: list) -> dict:
        """
        Given all market context, ask Claude to make the final trade decision.
        Returns structured decision dict.
        """
        system = """You are a professional forex trading analyst and risk manager.
You analyse Smart Money Concepts (SMC) signals, news data, and account conditions to make precise trading decisions.

Your output must be a valid JSON object with this exact structure:
{
  "decision": "BUY" | "SELL" | "HOLD",
  "confidence": <number 0-100>,
  "reasoning": "<brief explanation>",
  "entry_note": "<specific entry advice>",
  "risk_note": "<risk management note>",
  "sl_pips": <stop loss in pips as number>,
  "tp_pips": <take profit in pips as number>
}

Rules:
- Only recommend BUY or SELL if probability >= 65 and news window is not blocked
- Be conservative — capital preservation is priority #1
- Always respect FTMO daily loss limits
- Output ONLY the JSON object, no preamble or markdown"""

        user = f"""
Analyse this trading opportunity and make a decision:

SYMBOL: {symbol}
TIMESTAMP: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}

STRATEGY CONFLUENCE:
- Overall Direction: {confluence.get('direction', 'NONE')}
- Probability Score: {confluence.get('probability', 0)}%
- Raw Score: {confluence.get('raw_score', 0)}/100

SIGNAL BREAKDOWN:
- Liquidity Sweep: {confluence.get('breakdown', {}).get('liquidity_sweep', {}).get('signal', 'NONE')} | {confluence.get('breakdown', {}).get('liquidity_sweep', {}).get('reason', '')}
- Quarterly Theory: {confluence.get('breakdown', {}).get('quarterly_theory', {}).get('signal', 'NONE')} | {confluence.get('breakdown', {}).get('quarterly_theory', {}).get('reason', '')}
- Candle Pattern: {confluence.get('breakdown', {}).get('candle_pattern', {}).get('pattern', 'None')} | {confluence.get('breakdown', {}).get('candle_pattern', {}).get('reason', '')}
- Trend: {confluence.get('breakdown', {}).get('trend', {}).get('trend', 'Unknown')} | {confluence.get('breakdown', {}).get('trend', {}).get('reason', '')}
- FVG: {confluence.get('breakdown', {}).get('fvg', {}).get('fvg_type', 'None')} | {confluence.get('breakdown', {}).get('fvg', {}).get('reason', '')}

NEWS STATUS:
- Trading Blocked: {news_summary.get('news_window', {}).get('is_blocked', False)}
- Block Reason: {news_summary.get('news_window', {}).get('reason', 'N/A')}
- Pair News Bias: {news_summary.get('pair_bias', {}).get('bias', 'NEUTRAL')} | {news_summary.get('pair_bias', {}).get('reason', '')}
- Upcoming High-Impact Events: {news_summary.get('upcoming_count', 0)} in next 4 hours

ACCOUNT STATUS:
- Balance: {account_info.get('balance', 0)} {account_info.get('currency', 'USD')}
- Equity: {account_info.get('equity', 0)}
- Open Positions: {len(open_positions)}
- Daily P&L: {account_info.get('profit', 0)}

Make your decision:"""

        response_text = self._call_claude(system, user)

        if response_text:
            try:
                # Strip any accidental markdown
                clean = response_text.strip()
                if clean.startswith("```"):
                    clean = clean.split("```")[1]
                    if clean.startswith("json"):
                        clean = clean[4:]
                decision = json.loads(clean)
                decision["symbol"]    = symbol
                decision["timestamp"] = datetime.now(timezone.utc).isoformat()
                return decision
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse Claude response: {e}\nRaw: {response_text}")

        # Fallback
        return {
            "decision":    "HOLD",
            "confidence":  0,
            "reasoning":   "LLM unavailable — defaulting to HOLD",
            "sl_pips":     0,
            "tp_pips":     0,
            "symbol":      symbol,
            "timestamp":   datetime.now(timezone.utc).isoformat(),
        }

    # ── News Reader ────────────────────────────────────────────────────────────

    def analyse_news_impact(self, symbol: str, news_events: list) -> dict:
        """Ask Claude to interpret how upcoming news might affect the pair."""
        if not news_events:
            return {"impact": "NONE", "bias": "NEUTRAL", "summary": "No major news expected"}

        system = """You are a forex fundamental analyst. Analyse upcoming economic events and their likely impact on currency pairs.
Output only a JSON object:
{
  "impact": "HIGH" | "MEDIUM" | "LOW",
  "bias": "BUY" | "SELL" | "NEUTRAL",
  "summary": "<one sentence explanation>",
  "caution_level": <1-5>
}"""

        events_text = "\n".join([
            f"- {e['time_utc'].strftime('%H:%M UTC')} [{e['impact']}] {e['country']}: {e['title']} (Forecast: {e.get('forecast', 'N/A')})"
            for e in news_events[:5]
        ])

        user = f"Symbol: {symbol}\nUpcoming events:\n{events_text}\n\nAssess impact on {symbol}:"

        response_text = self._call_claude(system, user)
        if response_text:
            try:
                clean = response_text.strip().lstrip("```json").rstrip("```")
                return json.loads(clean)
            except Exception:
                pass

        return {"impact": "UNKNOWN", "bias": "NEUTRAL", "summary": "Analysis unavailable", "caution_level": 3}

    # ── Strategy Explainer ─────────────────────────────────────────────────────

    def explain_signal(self, confluence: dict, symbol: str) -> str:
        """Generate a human-readable explanation of the current signal for the Excel sheet."""
        system = "You are a forex trading assistant. Write a clear, concise 2-3 sentence explanation of the trading signal for a trader's log. Be specific about the SMC concepts at play."

        user = f"""
Symbol: {symbol}
Direction: {confluence.get('direction')}
Probability: {confluence.get('probability')}%
Signals: {json.dumps({k: v.get('reason', '') for k, v in confluence.get('breakdown', {}).items()}, indent=2)}

Write the explanation:"""

        return self._call_claude(system, user) or "Signal explanation unavailable."


# Singleton
llm_agent = LLMAgent()
