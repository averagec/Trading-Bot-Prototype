"""
LLM Veto Agent (optional - only runs when ANTHROPIC_API_KEY is set)
Asks Claude to review a trade the rule-based checklist has already proposed.
The LLM is VETO-ONLY: it can turn a trade into HOLD, it can never flip a direction or create one.
Any failure (no key, network, HTTP error, malformed answer) means HOLD.
"""

import json
import logging
import os
import requests
from datetime import datetime, timezone
from typing import Optional

from config import LLM, MIN_SCORE_TO_TRADE

logger = logging.getLogger(__name__)

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
API_KEY_ENV       = "ANTHROPIC_API_KEY"
VALID_DECISIONS   = ("BUY", "SELL", "HOLD")


def apply_veto(checklist_direction: str, llm_decision: dict) -> str:
    """Final direction after the LLM review: the checklist's direction only if the LLM confirms exactly that
    direction, otherwise HOLD. An opposite direction is a disagreement, not a flip."""
    if checklist_direction in ("BUY", "SELL") and llm_decision.get("decision") == checklist_direction:
        return checklist_direction
    return "HOLD"


class LLMAgent:

    def __init__(self):
        self.model      = LLM["model"]
        self.max_tokens = LLM["max_tokens"]

    def _call_claude(self, system: str, user: str) -> Optional[str]:
        """Call the Claude API. Returns the answer text, or None after logging why the call failed.
        The API key is read from the environment per call and never logged."""
        api_key = os.environ.get(API_KEY_ENV)
        if not api_key:
            logger.error(f"Claude API call skipped: {API_KEY_ENV} is not set")
            return None
        try:
            response = requests.post(
                ANTHROPIC_API_URL,
                headers={
                    "content-type":      "application/json",
                    "x-api-key":         api_key,
                    "anthropic-version": ANTHROPIC_VERSION,
                },
                json={
                    "model":       self.model,
                    "max_tokens":  self.max_tokens,
                    "temperature": 0,
                    "system":      system,
                    "messages":    [{"role": "user", "content": user}],
                },
                timeout=30,
            )
        except requests.RequestException as e:
            logger.error(f"Claude API request failed: {type(e).__name__}")
            return None

        if response.status_code != 200:
            logger.error(f"Claude API returned HTTP {response.status_code}: {response.text[:200]}")
            return None
        try:
            return response.json()["content"][0]["text"]
        except (ValueError, KeyError, IndexError, TypeError) as e:
            logger.error(f"Claude API response was not the expected JSON ({type(e).__name__})")
            return None

    @staticmethod
    def _parse_decision(text: str) -> dict:
        """Parse the model's answer into {"decision": BUY|SELL|HOLD, "confidence": 0-100, "reasoning": str}.
        Raises ValueError for anything that is not a JSON object with a valid decision."""
        clean = text.strip()
        if clean.startswith("```"):                 # tolerate an accidental markdown fence
            clean = clean.split("```")[1]
            if clean.startswith("json"):
                clean = clean[4:]
        data = json.loads(clean)                    # JSONDecodeError is a ValueError
        if not isinstance(data, dict) or data.get("decision") not in VALID_DECISIONS:
            raise ValueError(f"missing or invalid 'decision': {str(data)[:100]}")
        try:
            confidence = max(0.0, min(100.0, float(data.get("confidence", 0))))
        except (TypeError, ValueError):
            confidence = 0.0
        return {"decision": data["decision"], "confidence": confidence, "reasoning": str(data.get("reasoning", ""))}

    def make_trade_decision(self, symbol: str, confluence: dict, news_summary: dict,
                            account_info: dict, open_positions: list) -> dict:
        """
        Ask Claude to review the checklist's proposed trade (confluence["direction"]).
        Returns {"decision", "confidence", "reasoning", "symbol", "timestamp"}. Decision is HOLD on any failure.
        Use apply_veto() to combine it with the checklist direction; never use "decision" on its own.
        """
        system = f"""You are a risk reviewer for a rule-based forex bot using Smart Money Concepts (SMC).
The rule-based checklist has already proposed a trade direction. You may only CONFIRM it or VETO it.

Your output must be a valid JSON object with this exact structure:
{{
  "decision": "BUY" | "SELL" | "HOLD",
  "confidence": <number 0-100>,
  "reasoning": "<brief explanation>"
}}

Rules:
- Answer with the proposed direction to confirm it, or "HOLD" to veto it. Never answer with the opposite direction.
- Veto if the setup score is below {MIN_SCORE_TO_TRADE} or the news window is blocked
- Be conservative - capital preservation is priority #1
- Output ONLY the JSON object, no preamble or markdown"""

        breakdown = confluence.get("breakdown", {})
        news      = news_summary.get("news_window", {})
        user = f"""
Review this proposed trade:

SYMBOL: {symbol}
TIMESTAMP: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}

STRATEGY CONFLUENCE:
- Proposed Direction: {confluence.get('direction', 'NONE')}
- Probability Score: {confluence.get('probability', 0)}%
- Raw Score: {confluence.get('raw_score', 0)}

SIGNAL BREAKDOWN:
- Liquidity Sweep: {breakdown.get('liquidity_sweep', {}).get('signal', 'NONE')} | {breakdown.get('liquidity_sweep', {}).get('reason', '')}
- Quarterly Theory: {breakdown.get('quarterly_theory', {}).get('signal', 'NONE')} | {breakdown.get('quarterly_theory', {}).get('reason', '')}
- Candle Pattern: {breakdown.get('candle_pattern', {}).get('pattern', 'None')} | {breakdown.get('candle_pattern', {}).get('reason', '')}
- Trend: {breakdown.get('trend', {}).get('trend', 'Unknown')} | {breakdown.get('trend', {}).get('reason', '')}
- FVG: {breakdown.get('fvg', {}).get('fvg_type', 'None')} | {breakdown.get('fvg', {}).get('reason', '')}

NEWS STATUS:
- Trading Blocked: {news.get('is_blocked', False)}
- Block Reason: {news.get('reason', 'N/A')}
- Upcoming High-Impact Events: {news_summary.get('upcoming_count', 0)} in next 4 hours

ACCOUNT STATUS:
- Balance: {account_info.get('balance', 0)} {account_info.get('currency', 'USD')}
- Equity: {account_info.get('equity', 0)}
- Open Positions: {len(open_positions)}
- Open P&L: {account_info.get('profit', 0)}

Confirm or veto:"""

        decision = {"decision": "HOLD", "confidence": 0.0, "reasoning": "LLM unavailable - defaulting to HOLD"}
        outcome  = "failed"
        response_text = self._call_claude(system, user)
        if response_text:
            try:
                decision = self._parse_decision(response_text)
                outcome  = "ok"
            except ValueError as e:
                logger.error(f"Claude answer rejected, defaulting to HOLD: {e}")
                outcome = "malformed"

        logger.info(f"LLM review | {symbol} | checklist: {confluence.get('direction', 'NONE')} | "
                    f"llm: {decision['decision']} ({decision['confidence']:.0f}) | call: {outcome}")
        return {**decision, "symbol": symbol, "timestamp": datetime.now(timezone.utc).isoformat()}


# Singleton
llm_agent = LLMAgent()
