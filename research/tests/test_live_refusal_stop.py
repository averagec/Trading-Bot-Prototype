"""Live bot: a refused (non-demo) account stops the bot, and the account type is re-checked every cycle.
MT5 is mocked throughout; nothing here touches a terminal or places an order."""

import importlib
import os
import sys
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

DEMO, CONTEST, REAL = 0, 1, 2      # MetaTrader5 ACCOUNT_TRADE_MODE_* values


@pytest.fixture(scope="module")
def live_main(tmp_path_factory):
    """Import main.py from a scratch cwd so its FileHandler('forex_bot.log') does not touch the live log."""
    cwd = os.getcwd()
    os.chdir(tmp_path_factory.mktemp("livecwd_refusal"))
    try:
        sys.modules.pop("main", None)
        mod = importlib.import_module("main")
    finally:
        os.chdir(cwd)
    return mod


def _fake_mt5(trade_mode, login=42, server="FTMO-Demo"):
    fake = mock.MagicMock()
    fake.ACCOUNT_TRADE_MODE_DEMO, fake.ACCOUNT_TRADE_MODE_CONTEST, fake.ACCOUNT_TRADE_MODE_REAL = DEMO, CONTEST, REAL
    fake.initialize.return_value = True
    fake.account_info.return_value = mock.Mock(login=login, balance=100000.0, currency="USD", server=server,
                                               trade_mode=trade_mode)
    return fake


def _connector(monkeypatch, trade_mode, allow_live=False, connected=False):
    import mt5_connector as mc
    fake = _fake_mt5(trade_mode)
    monkeypatch.setattr(mc, "mt5", fake, raising=False)
    monkeypatch.setattr(mc, "MT5_AVAILABLE", True)
    monkeypatch.setattr(mc, "MT5_CONFIG", {"login": None, "password": "", "server": "S", "timeout": 1})
    monkeypatch.setattr(mc, "ALLOW_LIVE_ACCOUNT", allow_live)
    monkeypatch.setattr(mc, "logger", mock.Mock())
    conn = mc.MT5Connector()
    conn.connected = connected
    monkeypatch.setattr(conn, "detect_server_utc_offset", mock.Mock(return_value=0))
    return conn, fake


# ── connector: the `refused` flag ──────────────────────────────────────────────

def test_refused_starts_false_and_stays_false_for_a_demo(monkeypatch):
    conn, _ = _connector(monkeypatch, DEMO)
    assert conn.refused is False
    assert conn.connect() is True and conn.refused is False


@pytest.mark.parametrize("mode", [REAL, CONTEST, 7])
def test_non_demo_connect_sets_refused(monkeypatch, mode):
    conn, _ = _connector(monkeypatch, mode)
    assert conn.connect() is False and conn.refused is True and conn.connected is False


def test_override_does_not_set_refused(monkeypatch):
    conn, _ = _connector(monkeypatch, REAL, allow_live=True)
    assert conn.connect() is True and conn.refused is False


def test_other_connect_failures_are_not_a_refusal(monkeypatch):
    """Init/auth failure keeps the old behaviour (simulation mode), it is not a refusal."""
    conn, fake = _connector(monkeypatch, DEMO)
    fake.initialize.return_value = False
    assert conn.connect() is False and conn.refused is False


def test_refused_resets_on_the_next_connect(monkeypatch):
    conn, fake = _connector(monkeypatch, REAL)
    conn.connect()
    assert conn.refused is True
    fake.account_info.return_value.trade_mode = DEMO
    assert conn.connect() is True and conn.refused is False


# ── connector: per-cycle recheck ───────────────────────────────────────────────

@pytest.mark.parametrize("mode,allow,expected", [
    (DEMO, False, True),
    (REAL, False, False),
    (CONTEST, False, False),
    (7, False, False),
    (REAL, True, True),
])
def test_account_still_allowed(monkeypatch, mode, allow, expected):
    conn, _ = _connector(monkeypatch, mode, allow_live=allow, connected=True)
    assert conn.account_still_allowed() is expected


def test_account_still_allowed_logs_the_account_when_it_fails(monkeypatch):
    import mt5_connector as mc
    conn, _ = _connector(monkeypatch, REAL, connected=True)
    conn.account_still_allowed()
    err = " ".join(c.args[0] for c in mc.logger.error.call_args_list)
    assert "REAL" in err and "Account: 42" in err


def test_account_still_allowed_true_when_account_info_is_unreadable(monkeypatch):
    """A transient read failure must not kill the bot; the account did not change."""
    conn, fake = _connector(monkeypatch, REAL, connected=True)
    fake.account_info.return_value = None
    assert conn.account_still_allowed() is True


def test_account_still_allowed_true_when_not_connected(monkeypatch):
    conn, fake = _connector(monkeypatch, REAL, connected=False)
    assert conn.account_still_allowed() is True
    fake.account_info.assert_not_called()


# ── main: startup / cycle / run ────────────────────────────────────────────────

def test_startup_exits_when_the_account_is_refused(live_main):
    bot = live_main.ForexBot()
    conn = mock.Mock(refused=True)
    conn.connect.return_value = False
    xl = mock.Mock()
    with mock.patch.object(live_main, "connector", conn), mock.patch.object(live_main, "excel_manager", xl):
        with pytest.raises(SystemExit) as exc:
            bot.startup()
    assert exc.value.code == 2
    conn.get_account_info.assert_not_called()
    xl.update_dashboard.assert_called_once_with({}, bot_status="STOPPED - ACCOUNT REFUSED")   # never "STARTING"


def test_startup_still_falls_back_to_simulation_for_other_failures(live_main):
    bot = live_main.ForexBot()
    conn = mock.Mock(refused=False)
    conn.connect.return_value = False
    conn.get_account_info.return_value = None
    xl = mock.Mock()
    with mock.patch.object(live_main, "connector", conn), mock.patch.object(live_main, "excel_manager", xl):
        bot.startup()                                # no SystemExit
    xl.update_dashboard.assert_called_once_with({}, bot_status="STARTING")


def test_cycle_stops_before_touching_anything_when_the_account_is_no_longer_allowed(live_main):
    bot = live_main.ForexBot()
    conn = mock.Mock()
    conn.account_still_allowed.return_value = False
    xl = mock.Mock()
    with mock.patch.object(live_main, "connector", conn), mock.patch.object(live_main, "excel_manager", xl), \
            mock.patch.object(live_main.ForexBot, "_friday_flatten") as flat, \
            mock.patch.object(live_main.ForexBot, "_analyse_pair") as analyse:
        with pytest.raises(SystemExit) as exc:
            bot._run_cycle()
    assert exc.value.code == 2
    # the halt may READ the open positions (to report how many are still open) but must not act on anything
    for name in ("get_account_info", "get_pending_orders", "close_position", "cancel_pending_order", "place_order",
                 "place_pending_order"):
        getattr(conn, name).assert_not_called()      # positions on the refused account are left alone
    flat.assert_not_called()
    analyse.assert_not_called()
    conn.disconnect.assert_called_once()
    (acct, ), kwargs = xl.update_dashboard.call_args
    assert acct == {} and "REFUSED" in kwargs["bot_status"]     # no fake/real balance written


@pytest.mark.parametrize("failing", ["update_dashboard", "disconnect"])
def test_stop_still_exits_with_code_2_when_cleanup_raises(live_main, failing):
    """A dashboard write failing (workbook open in Excel -> PermissionError) must not replace the exit."""
    bot = live_main.ForexBot()
    conn = mock.Mock()
    conn.account_still_allowed.return_value = False
    xl = mock.Mock()
    (xl if failing == "update_dashboard" else conn).__getattr__(failing).side_effect = PermissionError("file is open")
    with mock.patch.object(live_main, "connector", conn), mock.patch.object(live_main, "excel_manager", xl):
        with pytest.raises(SystemExit) as exc:
            bot._run_cycle()
    assert exc.value.code == 2 and bot.running is False


def test_cycle_runs_normally_when_the_account_is_allowed(live_main):
    bot = live_main.ForexBot()
    conn = mock.Mock()
    conn.account_still_allowed.return_value = True
    conn.get_account_info.return_value = {"balance": 100000, "equity": 100000}
    conn.get_open_positions.return_value = []
    conn.get_pending_orders.return_value = []
    rm = mock.Mock()
    rm.can_trade.return_value = {"allowed": True, "reason": "ok"}
    with mock.patch.object(live_main, "connector", conn), mock.patch.object(live_main, "risk_manager", rm),             mock.patch.object(live_main, "excel_manager", mock.Mock()), \
            mock.patch.object(live_main.ForexBot, "_friday_flatten_due", return_value=False), \
            mock.patch.object(live_main.ForexBot, "_manage_breakeven"), \
            mock.patch.object(live_main.ForexBot, "_analyse_pair") as analyse, \
            mock.patch.object(live_main, "news_agent", mock.Mock(get_upcoming_events=lambda hours_ahead: [])), \
            mock.patch.object(live_main, "order_manager", mock.Mock(get_expired_pending=lambda p: [])):
        bot._run_cycle()
    assert analyse.call_count == len(live_main.PAIRS)


def test_run_loop_does_not_swallow_the_stop_or_sleep(live_main):
    """run()'s `except Exception` must not catch the SystemExit, and the bot must not go to sleep afterwards."""
    bot = live_main.ForexBot()
    conn = mock.Mock()
    with mock.patch.object(live_main, "connector", conn), \
            mock.patch.object(live_main.ForexBot, "startup"), \
            mock.patch.object(live_main.ForexBot, "_run_cycle", side_effect=SystemExit(2)), \
            mock.patch.object(live_main.time, "sleep", side_effect=AssertionError("slept after a refusal")):
        with pytest.raises(SystemExit) as exc:
            bot.run()
    assert exc.value.code == 2


# ── main: _modify_sl respects `connected` ──────────────────────────────────────

@pytest.mark.parametrize("connected,sent", [(False, False), (True, True)])
def test_modify_sl_only_sends_when_connected(live_main, connected, sent):
    bot = live_main.ForexBot()
    fake = mock.MagicMock()
    fake.order_send.return_value = mock.Mock(retcode=fake.TRADE_RETCODE_DONE)
    conn = mock.Mock(connected=connected)
    with mock.patch.object(live_main, "mt5", fake, create=True), mock.patch.object(live_main, "MT5_AVAILABLE", True), \
            mock.patch.object(live_main, "connector", conn):
        bot._modify_sl(1, "EURUSD", 1.1, 1.2)
    assert fake.order_send.called is sent
