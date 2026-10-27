"""Live bot hygiene: Excel is a best-effort sink (a locked workbook never aborts a cycle), and config/Excel/README
agree with the code. Workbooks are created in tmp_path."""

import logging
import re
import sys
from pathlib import Path
from unittest import mock

import openpyxl
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import config   # noqa: E402
from utils import risk_manager as rm_mod   # noqa: E402
from utils.excel_manager import ExcelManager   # noqa: E402


@pytest.fixture
def xl(tmp_path):
    m = ExcelManager(tmp_path / "dash.xlsx")
    m.create_workbook()
    return m


def _locked():
    """Every workbook open fails the way Windows reports a workbook that is open in Excel."""
    return mock.patch("utils.excel_manager.openpyxl.load_workbook", side_effect=PermissionError("open in Excel"))


SIGNAL = {"symbol": "EURUSD", "direction": "BUY", "probability": 80, "breakdown": {}}
TRADE  = {"symbol": "EURUSD", "direction": "BUY", "volume": 0.1}
CALLS = [("write_signal", (SIGNAL,)), ("write_trade", (TRADE,)), ("update_dashboard", ({"balance": 1}, "RUNNING")),
         ("write_news", ([],))]


# ── best effort ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name,args", CALLS)
def test_a_locked_workbook_does_not_raise(xl, name, args):
    with _locked():
        assert getattr(xl, name)(*args) is False


def test_a_failed_create_does_not_raise(tmp_path):
    m = ExcelManager(tmp_path / "x.xlsx")
    with mock.patch("utils.excel_manager.openpyxl.Workbook", side_effect=PermissionError("nope")):
        assert m.create_workbook() is False


@pytest.mark.parametrize("name,args", CALLS)
def test_working_writes_still_work_and_return_true(xl, name, args):
    assert getattr(xl, name)(*args) is True


def test_signal_row_is_really_written(xl):
    xl.write_signal(SIGNAL)
    ws = openpyxl.load_workbook(xl.path)[xl.sheets["signals"]]
    assert ws.cell(row=3, column=2).value == "EURUSD" and ws.cell(row=3, column=3).value == "BUY"


def test_one_error_per_failure_episode_not_one_per_cycle(xl, caplog):
    caplog.set_level(logging.INFO)
    with _locked():
        for _ in range(5):
            xl.update_dashboard({}, "RUNNING")
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1 and "update_dashboard" in errors[0].getMessage()
    xl.update_dashboard({}, "RUNNING")                                            # recovery is logged once
    assert [r.getMessage() for r in caplog.records if r.getMessage().endswith("works again") and r.levelno == logging.INFO] ==["Excel update_dashboard works again"]
    caplog.clear()
    with _locked():                                                                # a new episode logs again
        xl.update_dashboard({}, "RUNNING")
    assert len([r for r in caplog.records if r.levelno == logging.ERROR]) == 1


def test_each_write_kind_has_its_own_episode(xl, caplog):
    with _locked():
        xl.update_dashboard({}, "RUNNING")
        xl.write_signal(SIGNAL)
        xl.write_signal(SIGNAL)
    assert len([r for r in caplog.records if r.levelno == logging.ERROR]) == 2


def test_a_locked_workbook_does_not_abort_the_cycle_or_skip_the_risk_check(live_main, xl):
    """Used to raise PermissionError out of _run_cycle at the first dashboard write, before can_trade()."""
    conn = mock.Mock()
    conn.account_still_allowed.return_value = True
    conn.get_account_info.return_value = {"balance": 1000, "equity": 1000}
    conn.get_open_positions.return_value = []
    conn.get_pending_orders.return_value = []
    rm = mock.Mock()
    rm.can_trade.return_value = {"allowed": True, "reason": "ok"}
    news = mock.Mock(get_upcoming_events=lambda hours_ahead: [{"title": "x"}])
    with mock.patch.object(live_main, "connector", conn), mock.patch.object(live_main, "risk_manager", rm), \
            mock.patch.object(live_main, "excel_manager", xl), mock.patch.object(live_main, "news_agent", news), \
            mock.patch.object(live_main, "order_manager", mock.Mock(get_expired_pending=lambda p: [])), \
            mock.patch.object(live_main.ForexBot, "_friday_flatten_due", return_value=False), \
            mock.patch.object(live_main.ForexBot, "_analyse_pair") as analyse, _locked():
        live_main.ForexBot()._run_cycle()
    rm.can_trade.assert_called_once()
    assert analyse.call_count == len(live_main.PAIRS)


def test_a_locked_workbook_does_not_hide_a_blocked_account(live_main, xl):
    conn = mock.Mock()
    conn.account_still_allowed.return_value = True
    conn.get_account_info.return_value = {"balance": 1000, "equity": 900}
    conn.get_open_positions.return_value = [{"ticket": 1}]
    conn.get_pending_orders.return_value = []
    rm = mock.Mock()
    rm.can_trade.return_value = {"allowed": False, "type": "daily_drawdown", "reason": "limit"}
    with mock.patch.object(live_main, "connector", conn), mock.patch.object(live_main, "risk_manager", rm), \
            mock.patch.object(live_main, "excel_manager", xl), \
            mock.patch.object(live_main.ForexBot, "_friday_flatten_due", return_value=False), \
            mock.patch.object(live_main.ForexBot, "_manage_breakeven") as be, \
            mock.patch.object(live_main.ForexBot, "_analyse_pair") as analyse, _locked():
        live_main.ForexBot()._run_cycle()
    be.assert_called_once()                       # the blocked branch still manages the open position
    analyse.assert_not_called()


def test_a_locked_workbook_after_an_order_does_not_lose_the_trade_bookkeeping(live_main, xl):
    bot = live_main.ForexBot()
    position = {"volume": 0.1, "sl": 1.0, "tp": 1.3, "risk_amount": 2.5, "sl_pips": 10}
    with mock.patch.object(live_main, "excel_manager", xl), _locked():
        bot._log_trade("EURUSD", "BUY", position, 1.1, {"breakdown": {}}, "MARKET")
    assert bot.trades_today == 1


# ── config / Excel / README agree with the code ────────────────────────────────

def test_config_has_no_keys_that_contradict_the_code():
    assert set(config.RISK) == {"max_open_trades"}
    for gone in ("TIMEFRAMES", "SCORE_WEIGHTS"):
        assert not hasattr(config, gone)


def test_config_sheet_shows_the_real_risk_numbers(xl):
    ws = openpyxl.load_workbook(xl.path)[xl.sheets["config"]]
    rows = {r[0]: r[1] for r in ws.iter_rows(min_row=3, values_only=True) if r[0]}
    assert rows["Risk Per Trade (%)"] == rm_mod.FIXED_RISK_PCT == 0.25
    assert rows["Daily Loss Limit (%)"] == rm_mod.DAILY_DRAWDOWN_PCT == 3.0
    assert rows["R:R Ratio"] == rm_mod.RR_RATIO == 3.0
    assert rows["Max Open Trades"] == config.RISK["max_open_trades"]
    assert rows["Min Score to Trade (%)"] == config.MIN_SCORE_TO_TRADE == 50
    assert (rows["Trigger Timeframe"], rows["Entry Timeframe"], rows["Confirmation Timeframe"], rows["Bias Timeframe"]) == \
           (config.TRIGGER_TF, config.PRIMARY_TF, config.CONFIRM_TF, config.BIAS_TF)


def test_every_config_key_is_still_read_somewhere():
    """A config key nothing reads is a lie waiting to happen (this is how 1% risk / 5% limit / R:R 2 got stale)."""
    code = "\n".join(p.read_text(encoding="utf-8") for p in
                     [ROOT / "main.py", ROOT / "mt5_connector.py", ROOT / "utils" / "excel_manager.py",
                      ROOT / "agents" / "llm_agent.py"])
    for name in ("PAIRS", "TRIGGER_TF", "PRIMARY_TF", "CONFIRM_TF", "BIAS_TF", "BREAKEVEN", "FRIDAY_FLATTEN_UTC", "BOT_MAGIC",
                 "MIN_SCORE_TO_TRADE", "CANDLE_COUNT", "ALLOW_LIVE_ACCOUNT", "MT5_CONFIG", "EXCEL", "LLM"):
        assert name in code, name
    for key in ("enabled", "trigger_rr", "buffer_pips"):
        assert f'BREAKEVEN["{key}"]' in code
    assert 'RISK["max_open_trades"]' in code


def test_readme_numbers_match_the_code():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "0.25%" in text and "3% below" in text and "3R" in text and "every 5 minutes" in text
    assert f">= {config.MIN_SCORE_TO_TRADE}" in text and "4 of the 5" in text
    for stale in ("every 15 minutes", "Daily loss limit is 5%", "65%", "R:R ratio ≥ 2.0", "LLM confidence ≥ 60%", "Anthropic API key\n"):
        assert stale not in text, stale
    assert not re.search(r"runs every 15 mins", text)
