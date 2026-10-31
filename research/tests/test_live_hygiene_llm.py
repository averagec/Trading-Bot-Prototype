"""Live bot hygiene: the optional LLM review is auth'd, deterministic, fails to HOLD and is veto-only.
requests and the environment are mocked; nothing here contacts the network."""

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import pandas as pd
import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agents import llm_agent as la   # noqa: E402
from config import LLM               # noqa: E402

FAKE_KEY = "sk-ant-test-0000000000"


def _response(status=200, body=None, text="", json_error=False):
    r = mock.Mock(status_code=status, text=text)
    if json_error:
        r.json.side_effect = ValueError("not json")
    else:
        r.json.return_value = body
    return r


def _ok(answer):
    return _response(200, {"content": [{"type": "text", "text": answer}]})


def _decide(monkeypatch, response=None, post_side_effect=None, key=FAKE_KEY):
    if key:
        monkeypatch.setenv("ANTHROPIC_API_KEY", key)
    else:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with mock.patch.object(la.requests, "post", return_value=response, side_effect=post_side_effect) as post:
        out = la.LLMAgent().make_trade_decision("EURUSD", {"direction": "BUY", "probability": 80}, {}, {}, [])
    return out, post


# ── request shape ──────────────────────────────────────────────────────────────

def test_request_carries_auth_headers_temperature_zero_and_the_configured_model(monkeypatch):
    out, post = _decide(monkeypatch, _ok('{"decision": "BUY", "confidence": 70, "reasoning": "ok"}'))
    kwargs = post.call_args.kwargs
    assert kwargs["headers"]["x-api-key"] == FAKE_KEY
    assert kwargs["headers"]["anthropic-version"] == la.ANTHROPIC_VERSION
    assert kwargs["json"]["temperature"] == 0
    assert kwargs["json"]["model"] == LLM["model"] and kwargs["json"]["max_tokens"] == LLM["max_tokens"]
    assert out["decision"] == "BUY" and out["confidence"] == 70


def test_no_api_key_means_no_call_and_hold(monkeypatch):
    out, post = _decide(monkeypatch, _ok("{}"), key=None)
    post.assert_not_called()
    assert out["decision"] == "HOLD"


def test_the_key_is_never_logged(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    _decide(monkeypatch, _response(401, text='{"error": "invalid x-api-key"}'))
    _decide(monkeypatch, post_side_effect=requests.ConnectionError(f"boom {FAKE_KEY}"))
    assert FAKE_KEY not in caplog.text


# ── failures are explicit and end in HOLD ──────────────────────────────────────

@pytest.mark.parametrize("status", [400, 401, 429, 500, 529])
def test_non_200_is_logged_with_the_status_and_gives_hold(monkeypatch, caplog, status):
    out, _ = _decide(monkeypatch, _response(status, text="nope"))
    assert out["decision"] == "HOLD" and out["confidence"] == 0
    assert f"HTTP {status}" in caplog.text


def test_network_error_gives_hold(monkeypatch):
    out, _ = _decide(monkeypatch, post_side_effect=requests.Timeout())
    assert out["decision"] == "HOLD"


def test_body_that_is_not_json_gives_hold(monkeypatch, caplog):
    out, _ = _decide(monkeypatch, _response(200, json_error=True))
    assert out["decision"] == "HOLD" and "not the expected JSON" in caplog.text


@pytest.mark.parametrize("body", [{}, {"content": []}, {"content": [{"type": "tool_use"}]}, [], None])
def test_unexpected_body_shape_gives_hold(monkeypatch, body):
    out, _ = _decide(monkeypatch, _response(200, body))
    assert out["decision"] == "HOLD"


@pytest.mark.parametrize("answer", ["I think BUY", "{bad json", "[1, 2]", '{"decision": "LONG"}', '{"confidence": 90}',
                                    '{"decision": null}'])
def test_malformed_model_answer_gives_hold(monkeypatch, answer):
    out, _ = _decide(monkeypatch, _ok(answer))
    assert out["decision"] == "HOLD" and out["confidence"] == 0


def test_markdown_fenced_answer_is_accepted(monkeypatch):
    out, _ = _decide(monkeypatch, _ok('```json\n{"decision": "HOLD", "confidence": 55, "reasoning": "x"}\n```'))
    assert out["decision"] == "HOLD" and out["confidence"] == 55


@pytest.mark.parametrize("raw,expected", [("250", 100), ("-5", 0), ('"abc"', 0), ("null", 0)])
def test_confidence_is_clamped_to_0_100(monkeypatch, raw, expected):
    out, _ = _decide(monkeypatch, _ok('{"decision": "BUY", "confidence": %s}' % raw))
    assert out["confidence"] == expected


def test_every_call_outcome_is_logged(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    _decide(monkeypatch, _ok('{"decision": "BUY", "confidence": 70}'))
    _decide(monkeypatch, _response(500, text="x"))
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("LLM review")]
    assert len(lines) == 2 and "call: ok" in lines[0] and "call: failed" in lines[1]


# ── veto-only ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("checklist,llm,final", [
    ("BUY", "BUY", "BUY"), ("SELL", "SELL", "SELL"),
    ("BUY", "HOLD", "HOLD"), ("SELL", "HOLD", "HOLD"),
    ("BUY", "SELL", "HOLD"), ("SELL", "BUY", "HOLD"),       # a flip is a disagreement -> HOLD
    ("NONE", "BUY", "HOLD"), ("NONE", "SELL", "HOLD"), ("HOLD", "BUY", "HOLD"),     # the LLM cannot create a trade
])
def test_apply_veto(checklist, llm, final):
    assert la.apply_veto(checklist, {"decision": llm}) == final


def test_apply_veto_tolerates_a_decision_without_the_key():
    assert la.apply_veto("BUY", {}) == "HOLD"


def test_unused_llm_functions_are_gone():
    agent = la.LLMAgent()
    assert not hasattr(agent, "analyse_news_impact") and not hasattr(agent, "explain_signal")


# ── the call site in main._analyse_pair ────────────────────────────────────────

class _FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 10, 27, 10, 0, tzinfo=timezone.utc)      # a Tuesday inside the London session


def _bars():
    return pd.DataFrame({"time": pd.date_range("2026-10-27", periods=3, freq="5min", tz="UTC"),
                         "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1})


def _analyse(live_main, monkeypatch, llm_says, key=FAKE_KEY, direction="BUY"):
    """Run _analyse_pair with everything mocked; returns (excel_manager mock, market-order mock, llm mock)."""
    if key:
        monkeypatch.setenv("ANTHROPIC_API_KEY", key)
    else:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    confluence = {"direction": direction, "probability": 80, "checklist_met": True, "checks_passed": 5,
                  "checks_failed": [], "breakdown": {}}
    conn = mock.Mock()
    conn.get_ohlcv.return_value = _bars()
    conn.get_tick.return_value = {"bid": 1.0, "ask": 1.0}
    om = mock.Mock()
    om.can_trade_symbol.return_value = {"allowed": True, "reason": ""}
    om.get_entry_type.return_value = "MARKET"
    news = mock.Mock()
    news.summary.return_value = {"news_window": {"is_blocked": False, "reason": ""}}
    observer = mock.Mock()
    observer.get_confidence_boost.return_value = 0
    regime = mock.Mock()
    regime.get_summary.return_value = {"h4_regime": "t", "h1_regime": "t", "reason": "", "can_trade": True}
    corr = mock.Mock()
    corr.check.return_value = {"allowed": True, "reason": ""}
    xl = mock.Mock()
    llm = mock.Mock()
    llm.make_trade_decision.return_value = {"decision": llm_says, "confidence": 77, "reasoning": "r"}
    bot = live_main.ForexBot()
    with mock.patch.object(live_main, "datetime", _FixedDatetime), mock.patch.object(live_main, "connector", conn), \
            mock.patch.object(live_main, "order_manager", om), mock.patch.object(live_main, "news_agent", news), \
            mock.patch.object(live_main, "pair_observer", observer), mock.patch.object(live_main, "regime_detector", regime), \
            mock.patch.object(live_main, "correlation_filter", corr), mock.patch.object(live_main, "excel_manager", xl), \
            mock.patch.object(live_main, "llm_agent", llm), mock.patch.object(live_main, "LLM_VETO_ACTIVE", True), \
            mock.patch.object(live_main, "run_confluence", return_value=dict(confluence)), \
            mock.patch.object(live_main.ForexBot, "_place_market_order") as market:
        bot._analyse_pair("EURUSD", {"balance": 1000, "equity": 1000}, [], [])
    return xl, market, llm


def test_llm_confirming_the_direction_lets_the_trade_through(live_main, monkeypatch):
    xl, market, llm = _analyse(live_main, monkeypatch, "BUY")
    assert xl.write_signal.call_args.args[0]["llm_decision"] == "BUY"
    assert market.call_args.args[1] == "BUY"


@pytest.mark.parametrize("llm_says", ["SELL", "HOLD"])
def test_llm_cannot_flip_or_keep_the_trade_against_the_checklist(live_main, monkeypatch, llm_says):
    xl, market, _ = _analyse(live_main, monkeypatch, llm_says)
    assert xl.write_signal.call_args.args[0]["llm_decision"] == "HOLD"
    market.assert_not_called()


def test_llm_is_off_without_the_env_var_and_not_asked_when_there_is_no_direction(live_main, monkeypatch):
    _, market, llm = _analyse(live_main, monkeypatch, "SELL", key=None)
    llm.make_trade_decision.assert_not_called()
    assert market.call_args.args[1] == "BUY"                 # the checklist alone decides, as before
    _, market, llm = _analyse(live_main, monkeypatch, "BUY", direction="NONE")
    llm.make_trade_decision.assert_not_called()
    market.assert_not_called()
