"""Live bot: LLM shadow mode, candidate/outcome log, pre-order account re-check, and the 'positions still open' halt note.
MT5, the LLM and Excel are mocked throughout; nothing here touches a terminal or places an order."""

import importlib
import importlib.util
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from utils import candidate_log   # noqa: E402

FAKE_KEY = "sk-ant-fake-not-a-real-key"


@pytest.fixture(scope="module")
def live_main(tmp_path_factory):
    cwd = os.getcwd()
    os.chdir(tmp_path_factory.mktemp("livecwd_candlog"))
    try:
        sys.modules.pop("main", None)
        mod = importlib.import_module("main")
    finally:
        os.chdir(cwd)
    return mod


def _rows():
    p = candidate_log.LOG_PATH
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []


# ── the log itself ─────────────────────────────────────────────────────────────

def test_log_event_appends_one_json_line_per_call(tmp_path):
    p = tmp_path / "sub" / "c.jsonl"
    candidate_log.log_event("candidate", {"symbol": "EURUSD", "x": 1}, path=p)
    candidate_log.log_event("order", {"ticket": 5}, path=p)
    rows = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines()]
    assert [r["kind"] for r in rows] == ["candidate", "order"]
    assert rows[0]["symbol"] == "EURUSD" and rows[1]["ticket"] == 5 and rows[0]["ts_utc"].endswith("+00:00")


def test_log_event_never_raises(tmp_path):
    with mock.patch("builtins.open", side_effect=PermissionError("locked")):
        candidate_log.log_event("candidate", {"a": 1}, path=tmp_path / "c.jsonl")      # must not raise
    candidate_log.log_event("candidate", {"bad": object()}, path=tmp_path / "d.jsonl")  # unserialisable -> str(), no raise


def test_tests_cannot_write_to_the_live_log():
    assert "logs" not in candidate_log.LOG_PATH.parts[-2:]            # conftest redirected it to a tmp dir


# ── config default ─────────────────────────────────────────────────────────────

def _load_config():
    spec = importlib.util.spec_from_file_location("config_under_test", ROOT / "config.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("value,expected", [(None, False), ("", False), ("0", False), ("true", False), ("1", True)])
def test_llm_veto_is_off_unless_the_env_var_is_exactly_1(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv("FOREX_LLM_VETO", raising=False)
    else:
        monkeypatch.setenv("FOREX_LLM_VETO", value)
    assert _load_config().LLM_VETO_ACTIVE is expected


# ── _analyse_pair: shadow mode and candidate rows ──────────────────────────────

class _FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 10, 27, 10, 0, tzinfo=timezone.utc)      # a Tuesday inside the London session


def _bars():
    return pd.DataFrame({"time": pd.date_range("2026-10-27", periods=3, freq="5min", tz="UTC"),
                         "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1})


def _analyse(live_main, monkeypatch, *, llm_says="BUY", key=FAKE_KEY, direction="BUY", veto=False,
             guard2_allowed=True, account_allowed=True, llm_raises=False):
    if key:
        monkeypatch.setenv("ANTHROPIC_API_KEY", key)
    else:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    confluence = {"direction": direction, "probability": 80, "checklist_met": True, "checks_passed": 5,
                  "checks_failed": [], "breakdown": {}}
    conn = mock.Mock()
    conn.get_ohlcv.return_value = _bars()
    conn.get_tick.return_value = {"bid": 1.0, "ask": 1.0}
    conn.account_still_allowed.return_value = account_allowed
    om = mock.Mock()
    om.can_trade_symbol.side_effect = [{"allowed": True, "reason": ""},
                                       {"allowed": guard2_allowed, "reason": "cooldown"}]
    om.get_entry_type.return_value = "MARKET"
    news = mock.Mock()
    news.summary.return_value = {"news_window": {"is_blocked": False, "reason": ""}}
    observer = mock.Mock()
    observer.get_confidence_boost.return_value = 0
    regime = mock.Mock()
    regime.get_summary.return_value = {"h4_regime": "trend", "h1_regime": "trend", "reason": "", "can_trade": True,
                                       "size_factor": 1.0}
    corr = mock.Mock()
    corr.check.return_value = {"allowed": True, "reason": ""}
    llm = mock.Mock()
    llm.make_trade_decision.return_value = {"decision": llm_says, "confidence": 77, "reasoning": "r"}
    if llm_raises:
        llm.make_trade_decision.side_effect = RuntimeError("api exploded")
    bot = live_main.ForexBot()
    with mock.patch.object(live_main, "datetime", _FixedDatetime), mock.patch.object(live_main, "connector", conn), \
            mock.patch.object(live_main, "order_manager", om), mock.patch.object(live_main, "news_agent", news), \
            mock.patch.object(live_main, "pair_observer", observer), mock.patch.object(live_main, "regime_detector", regime), \
            mock.patch.object(live_main, "correlation_filter", corr), mock.patch.object(live_main, "excel_manager", mock.Mock()), \
            mock.patch.object(live_main, "llm_agent", llm), mock.patch.object(live_main, "LLM_VETO_ACTIVE", veto), \
            mock.patch.object(live_main, "run_confluence", return_value=dict(confluence)), \
            mock.patch.object(live_main.ForexBot, "_place_market_order") as market:
        bot._analyse_pair("EURUSD", {"balance": 1000, "equity": 1000}, [], [])
    return market, llm, conn


def test_shadow_mode_asks_the_llm_but_does_not_let_it_veto(live_main, monkeypatch):
    market, llm, _ = _analyse(live_main, monkeypatch, llm_says="HOLD", veto=False)
    llm.make_trade_decision.assert_called_once()
    assert market.call_args.args[1] == "BUY"                          # the checklist alone decided
    (row,) = [r for r in _rows() if r["kind"] == "candidate"]
    assert row["llm"]["decision"] == "HOLD" and row["llm_veto_active"] is False
    assert row["final_dir"] == "BUY" and row["should_trade"] is True and row["symbol"] == "EURUSD"
    assert row["direction"] == "BUY" and row["checks_passed"] == 5 and row["probability"] == 80


def test_veto_active_lets_the_llm_stop_the_trade_and_logs_it(live_main, monkeypatch):
    market, _, _ = _analyse(live_main, monkeypatch, llm_says="HOLD", veto=True)
    market.assert_not_called()
    (row,) = [r for r in _rows() if r["kind"] == "candidate"]
    assert row["final_dir"] == "HOLD" and row["should_trade"] is False and row["llm_veto_active"] is True


def test_no_key_means_no_llm_call_and_a_null_llm_field(live_main, monkeypatch):
    market, llm, _ = _analyse(live_main, monkeypatch, key=None)
    llm.make_trade_decision.assert_not_called()
    assert market.call_args.args[1] == "BUY"
    (row,) = [r for r in _rows() if r["kind"] == "candidate"]
    assert row["llm"] is None


def test_pairs_without_a_proposed_direction_are_not_logged(live_main, monkeypatch):
    _analyse(live_main, monkeypatch, direction="NONE")
    assert _rows() == []


def test_a_trade_stopped_after_the_entry_conditions_is_logged_as_blocked(live_main, monkeypatch):
    market, _, _ = _analyse(live_main, monkeypatch, guard2_allowed=False)
    market.assert_not_called()
    (row,) = [r for r in _rows() if r["kind"] == "blocked"]
    assert row["symbol"] == "EURUSD" and row["direction"] == "BUY" and "cooldown" in row["reason"]


def test_a_failing_log_write_does_not_stop_the_trade(live_main, monkeypatch):
    with mock.patch("builtins.open", side_effect=OSError("disk full")):
        market, _, _ = _analyse(live_main, monkeypatch)
    assert market.call_args.args[1] == "BUY"


def test_an_llm_exception_never_stops_a_trade_in_shadow_mode(live_main, monkeypatch):
    market, _, _ = _analyse(live_main, monkeypatch, veto=False, llm_raises=True)
    assert market.call_args.args[1] == "BUY"
    (row,) = [r for r in _rows() if r["kind"] == "candidate"]
    assert row["llm"]["decision"] == "HOLD" and "RuntimeError" in row["llm"]["reasoning"]


def test_an_llm_exception_is_a_hold_when_the_veto_is_active(live_main, monkeypatch):
    market, _, _ = _analyse(live_main, monkeypatch, veto=True, llm_raises=True)
    market.assert_not_called()                       # fail closed, as llm_agent documents: any failure means HOLD


@pytest.mark.parametrize("key,veto,expect", [(None, False, "LLM review: off"), (FAKE_KEY, False, "shadow"),
                                             (FAKE_KEY, True, "veto ACTIVE")])
def test_startup_says_which_llm_mode_is_on(live_main, monkeypatch, key, veto, expect):
    if key:
        monkeypatch.setenv("ANTHROPIC_API_KEY", key)
    else:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    conn = mock.Mock(refused=False)
    conn.connect.return_value = True
    conn.get_account_info.return_value = None
    log = mock.Mock()
    with mock.patch.object(live_main, "connector", conn), mock.patch.object(live_main, "excel_manager", mock.Mock()), \
            mock.patch.object(live_main, "LLM_VETO_ACTIVE", veto), mock.patch.object(live_main, "logger", log):
        live_main.ForexBot().startup()
    assert any(expect in c.args[0] for c in log.info.call_args_list)


# ── pre-order account re-check ─────────────────────────────────────────────────

def test_account_is_rechecked_right_before_an_order_and_the_bot_stops_if_it_changed(live_main, monkeypatch):
    with pytest.raises(SystemExit) as exc:
        _analyse(live_main, monkeypatch, account_allowed=False)
    assert exc.value.code == 2


def test_order_placement_is_not_reached_when_the_account_is_not_allowed(live_main, monkeypatch):
    placed = mock.Mock()
    with mock.patch.object(live_main.ForexBot, "_place_market_order", placed):
        with pytest.raises(SystemExit):
            _analyse(live_main, monkeypatch, account_allowed=False)
    placed.assert_not_called()


# ── order rows ─────────────────────────────────────────────────────────────────

def _place(live_main, which, result):
    conn = mock.Mock()
    conn.get_tick.return_value = {"bid": 1.0, "ask": 1.0}
    conn.place_order.return_value = result
    conn.place_pending_order.return_value = result
    rm = mock.Mock()
    rm.find_structure_sl.return_value = 0.99
    rm.calculate_position.return_value = {"volume": 0.1, "sl": 0.99, "tp": 1.03, "allowed_risk": 25, "sl_pips": 10,
                                          "tp_pips": 30, "rr_ratio": 3, "risk_amount": 25}
    ptv = mock.Mock()
    ptv.validate.return_value = {"allowed": True, "reason": ""}
    bot = live_main.ForexBot()
    with mock.patch.object(live_main, "connector", conn), mock.patch.object(live_main, "risk_manager", rm), \
            mock.patch.object(live_main, "pre_trade_validator", ptv), mock.patch.object(live_main, "order_manager", mock.Mock()), \
            mock.patch.object(live_main, "excel_manager", mock.Mock()):
        if which == "market":
            bot._place_market_order("EURUSD", "BUY", {"probability": 80, "breakdown": {}}, {"balance": 1000}, _bars())
        else:
            bot._place_limit_order("EURUSD", "BUY", 0.995, {"probability": 80, "breakdown": {}}, {"balance": 1000}, _bars())


@pytest.mark.parametrize("which,kind_name", [("market", "MARKET"), ("limit", "PENDING_LIMIT")])
def test_accepted_orders_are_logged_with_their_ticket(live_main, which, kind_name):
    _place(live_main, which, {"order": 4242, "deal": 77})
    (row,) = [r for r in _rows() if r["kind"] == "order"]
    assert row["ticket"] == 4242 and row["symbol"] == "EURUSD" and row["direction"] == "BUY"
    assert row["order_type"] == kind_name and row["volume"] == 0.1 and row["sl"] == 0.99 and row["tp"] == 1.03


@pytest.mark.parametrize("which", ["market", "limit"])
def test_rejected_orders_are_not_logged_as_orders(live_main, which):
    _place(live_main, which, None)
    assert [r for r in _rows() if r["kind"] == "order"] == []


# ── halt note: bot positions still open ────────────────────────────────────────

def _halt(live_main, positions):
    bot = live_main.ForexBot()
    conn = mock.Mock()
    if isinstance(positions, Exception):
        conn.get_open_positions.side_effect = positions
    else:
        conn.get_open_positions.return_value = positions
    xl, log = mock.Mock(), mock.Mock()
    with mock.patch.object(live_main, "connector", conn), mock.patch.object(live_main, "excel_manager", xl), \
            mock.patch.object(live_main, "logger", log):
        with pytest.raises(SystemExit):
            bot._halt("because", "STOPPED - TEST", 2)
    return xl, log


def test_halt_reports_bot_positions_still_open_and_ignores_others(live_main):
    pos = [{"ticket": 11, "magic": live_main.BOT_MAGIC}, {"ticket": 12, "magic": live_main.BOT_MAGIC},
           {"ticket": 13, "magic": 0}]
    xl, log = _halt(live_main, pos)
    msg = log.critical.call_args.args[0]
    assert "2 BOT POSITION" in msg and "11" in msg and "12" in msg and "13" not in msg and "NOT running" in msg
    assert xl.update_dashboard.call_args.kwargs["bot_status"] == "STOPPED - TEST (2 BOT POSITIONS OPEN)"


def test_halt_without_open_bot_positions_is_unchanged(live_main):
    xl, log = _halt(live_main, [{"ticket": 13, "magic": 0}])
    assert "BOT POSITION" not in log.critical.call_args.args[0]
    assert xl.update_dashboard.call_args.kwargs["bot_status"] == "STOPPED - TEST"


def test_halt_still_exits_when_the_position_read_fails(live_main):
    xl, _ = _halt(live_main, RuntimeError("terminal gone"))
    assert xl.update_dashboard.call_args.kwargs["bot_status"] == "STOPPED - TEST"
