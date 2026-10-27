"""Live bot hygiene: kill switch (STOP file), Ctrl+C during the sleep, and the panic-close script.
MT5 is mocked throughout; panic_close.py is only ever called with a mocked connector or a fake mt5 module."""

import importlib
import sys
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import mt5_connector as mc   # noqa: E402
from config import BOT_MAGIC   # noqa: E402
from utils.flatten import flatten_bot, bot_positions   # noqa: E402

DEMO, CONTEST, REAL = 0, 1, 2


def pos(ticket, symbol="EURUSD", magic=BOT_MAGIC):
    return {"ticket": ticket, "symbol": symbol, "magic": magic}


# ── STOP file ──────────────────────────────────────────────────────────────────

@pytest.fixture
def stop_file(tmp_path, live_main):
    f = tmp_path / "STOP"
    with mock.patch.object(live_main, "STOP_FILE", f):
        yield f


def _stopped_cycle(live_main, xl=None, conn=None, due=False):
    conn = conn or mock.Mock()
    xl = xl or mock.Mock()
    bot = live_main.ForexBot()
    with mock.patch.object(live_main, "connector", conn), mock.patch.object(live_main, "excel_manager", xl), \
            mock.patch.object(live_main.ForexBot, "_friday_flatten_due", return_value=due), \
            mock.patch.object(live_main.ForexBot, "_friday_flatten") as flat, \
            mock.patch.object(live_main.ForexBot, "_analyse_pair") as analyse:
        with pytest.raises(SystemExit) as exc:
            bot._run_cycle()
    return bot, conn, xl, flat, analyse, exc.value


def test_stop_file_exits_cleanly_without_touching_anything(live_main, stop_file):
    stop_file.write_text("")
    bot, conn, xl, flat, analyse, exit_ = _stopped_cycle(live_main)
    assert exit_.code == 0 and bot.running is False
    for name in ("account_still_allowed", "get_account_info", "get_open_positions", "get_pending_orders",
                 "close_position", "cancel_pending_order", "place_order", "place_pending_order"):
        getattr(conn, name).assert_not_called()
    flat.assert_not_called()
    analyse.assert_not_called()
    conn.disconnect.assert_called_once()
    xl.update_dashboard.assert_called_once_with({}, bot_status="STOPPED - STOP FILE")


def test_stop_file_logs_critical(live_main, stop_file):
    stop_file.write_text("")
    log = mock.Mock()
    with mock.patch.object(live_main, "logger", log):
        _stopped_cycle(live_main)
    assert log.critical.call_count == 1 and "STOP file" in log.critical.call_args.args[0]


def test_stop_file_beats_the_friday_flatten(live_main, stop_file):
    """STOP means hands off: it does not even run the weekend flatten (use panic_close.py to close things)."""
    stop_file.write_text("")
    _, conn, _, flat, _, _ = _stopped_cycle(live_main, due=True)
    flat.assert_not_called()
    conn.close_position.assert_not_called()


def test_stop_still_exits_when_the_dashboard_write_fails(live_main, stop_file):
    stop_file.write_text("")
    xl = mock.Mock()
    xl.update_dashboard.side_effect = PermissionError("workbook open in Excel")
    bot, _, _, _, _, exit_ = _stopped_cycle(live_main, xl=xl)
    assert exit_.code == 0 and bot.running is False


def test_without_a_stop_file_the_cycle_runs(live_main, stop_file):
    conn = mock.Mock()
    conn.account_still_allowed.return_value = True
    conn.get_account_info.return_value = {"balance": 1000, "equity": 1000}
    conn.get_open_positions.return_value = []
    conn.get_pending_orders.return_value = []
    rm = mock.Mock()
    rm.can_trade.return_value = {"allowed": True, "reason": "ok"}
    with mock.patch.object(live_main, "connector", conn), mock.patch.object(live_main, "risk_manager", rm), \
            mock.patch.object(live_main, "excel_manager", mock.Mock()), \
            mock.patch.object(live_main, "news_agent", mock.Mock(get_upcoming_events=lambda hours_ahead: [])), \
            mock.patch.object(live_main, "order_manager", mock.Mock(get_expired_pending=lambda p: [])), \
            mock.patch.object(live_main.ForexBot, "_friday_flatten_due", return_value=False), \
            mock.patch.object(live_main.ForexBot, "_analyse_pair") as analyse:
        live_main.ForexBot()._run_cycle()
    assert analyse.call_count == len(live_main.PAIRS)


def test_the_stop_file_lives_in_the_project_folder(live_main):
    assert live_main.STOP_FILE == ROOT / "STOP"


def test_stop_file_exits_run_without_sleeping(live_main, stop_file):
    stop_file.write_text("")
    with mock.patch.object(live_main, "connector", mock.Mock()), mock.patch.object(live_main, "excel_manager", mock.Mock()), \
            mock.patch.object(live_main.ForexBot, "startup"), \
            mock.patch.object(live_main.time, "sleep", side_effect=AssertionError("slept after STOP")):
        with pytest.raises(SystemExit) as exc:
            live_main.ForexBot().run()
    assert exc.value.code == 0


# ── Ctrl+C / loop ──────────────────────────────────────────────────────────────

def _run_with(live_main, cycle=None, sleep=None):
    bot = live_main.ForexBot()
    with mock.patch.object(live_main, "connector", mock.Mock()), mock.patch.object(live_main.ForexBot, "startup"), \
            mock.patch.object(live_main.ForexBot, "_run_cycle", side_effect=cycle), \
            mock.patch.object(live_main.ForexBot, "shutdown") as shutdown, \
            mock.patch.object(live_main.time, "sleep", side_effect=sleep):
        bot.run()
    return bot, shutdown


def test_ctrl_c_during_the_sleep_runs_shutdown(live_main):
    """The sleep used to be outside the try block, so Ctrl+C there skipped shutdown() and dumped a traceback."""
    _, shutdown = _run_with(live_main, sleep=KeyboardInterrupt())
    shutdown.assert_called_once()


def test_ctrl_c_during_a_cycle_still_runs_shutdown(live_main):
    _, shutdown = _run_with(live_main, cycle=KeyboardInterrupt(), sleep=AssertionError("slept"))
    shutdown.assert_called_once()


def test_an_ordinary_cycle_error_does_not_end_the_loop(live_main):
    bot = live_main.ForexBot()
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 2:
            raise KeyboardInterrupt

    with mock.patch.object(live_main, "connector", mock.Mock()), mock.patch.object(live_main.ForexBot, "startup"), \
            mock.patch.object(live_main.ForexBot, "_run_cycle", side_effect=[RuntimeError("boom"), None]), \
            mock.patch.object(live_main.ForexBot, "shutdown") as shutdown, \
            mock.patch.object(live_main.time, "sleep", side_effect=sleep):
        bot.run()
    assert bot.cycle_count == 2 and len(sleeps) == 2
    shutdown.assert_called_once()
    assert 0 < sleeps[0] <= 300


def test_shutdown_reads_the_account_before_disconnecting(live_main):
    order = []
    conn = mock.Mock()
    conn.get_account_info.side_effect = lambda: order.append("account") or {"balance": 5}
    conn.disconnect.side_effect = lambda: order.append("disconnect")
    xl = mock.Mock()
    with mock.patch.object(live_main, "connector", conn), mock.patch.object(live_main, "excel_manager", xl):
        live_main.ForexBot().shutdown()
    assert order == ["account", "disconnect"]
    xl.update_dashboard.assert_called_once_with({"balance": 5}, bot_status="STOPPED")


# ── shared flatten helper ──────────────────────────────────────────────────────

def test_bot_positions_filter():
    assert [p["ticket"] for p in bot_positions([pos(1), pos(2, magic=0), pos(3, magic=7), {"ticket": 4}], BOT_MAGIC)] == [1]


def test_flatten_bot_only_touches_the_bots_orders_and_isolates_failures():
    conn = mock.Mock()
    conn.cancel_pending_order.side_effect = [RuntimeError("x"), True, False]
    conn.close_position.side_effect = [False, RuntimeError("y"), True]
    pending = [{"ticket": 8, "symbol": "A"}, {"ticket": 9, "symbol": "B"}, {"ticket": 10, "symbol": "C"}]
    res = flatten_bot(conn, [pos(1), pos(2), pos(3), pos(4, magic=0), pos(5, magic=99)], pending, BOT_MAGIC, "T")
    assert [c.args[0] for c in conn.close_position.call_args_list] == [1, 2, 3]
    assert [o["ticket"] for o in res.cancelled] == [9]
    assert res.failed_cancels == 2 and res.still_open == 2 and res.nothing_to_do is False


def test_flatten_bot_with_nothing_to_do_does_nothing():
    conn = mock.Mock()
    res = flatten_bot(conn, [pos(4, magic=0)], [], BOT_MAGIC, "T")
    assert res.nothing_to_do is True
    conn.close_position.assert_not_called()
    conn.cancel_pending_order.assert_not_called()


# ── panic_close.py ─────────────────────────────────────────────────────────────

@pytest.fixture
def panic():
    sys.modules.pop("panic_close", None)
    return importlib.import_module("panic_close")


def _panic_conn(connect=True, refused=False):
    conn = mock.Mock(refused=refused)
    conn.connect.return_value = connect
    conn.get_open_positions.return_value = [pos(1), pos(2, "GBPUSD", magic=0), pos(3, "USDJPY", magic=555)]
    conn.get_pending_orders.return_value = [{"ticket": 9, "symbol": "AUDUSD"}]
    conn.cancel_pending_order.return_value = True
    conn.close_position.return_value = True
    return conn


def test_panic_closes_only_the_bots_positions_and_cancels_its_pendings(panic):
    conn = _panic_conn()
    with mock.patch.object(panic, "connector", conn):
        assert panic.panic_close() == 0
    assert [c.args[0] for c in conn.close_position.call_args_list] == [1]
    conn.cancel_pending_order.assert_called_once_with(9)
    conn.disconnect.assert_called_once()


def test_panic_reports_failure_in_the_exit_code(panic):
    conn = _panic_conn()
    conn.close_position.return_value = False
    with mock.patch.object(panic, "connector", conn):
        assert panic.panic_close() == 1
    conn.disconnect.assert_called_once()


def test_panic_disconnects_even_if_flattening_raises(panic):
    conn = _panic_conn()
    conn.get_open_positions.side_effect = RuntimeError("terminal gone")
    with mock.patch.object(panic, "connector", conn), pytest.raises(RuntimeError):
        panic.panic_close()
    conn.disconnect.assert_called_once()


def test_panic_without_a_connection_touches_nothing(panic):
    conn = _panic_conn(connect=False)
    with mock.patch.object(panic, "connector", conn):
        assert panic.panic_close() == 1
    conn.close_position.assert_not_called()
    conn.cancel_pending_order.assert_not_called()


def test_panic_never_fakes_a_close_in_simulation_mode(panic, monkeypatch):
    """Library missing: the connector's simulated 'close' returns True, so the script must not get that far."""
    monkeypatch.setattr(mc, "MT5_AVAILABLE", False)
    conn = mc.MT5Connector()
    with mock.patch.object(panic, "connector", conn):
        assert panic.panic_close() == 1


@pytest.mark.parametrize("mode", [REAL, CONTEST])
def test_panic_refuses_a_non_demo_account_like_the_bot(panic, monkeypatch, mode):
    fake = mock.MagicMock()
    fake.ACCOUNT_TRADE_MODE_DEMO, fake.ACCOUNT_TRADE_MODE_CONTEST, fake.ACCOUNT_TRADE_MODE_REAL = DEMO, CONTEST, REAL
    fake.initialize.return_value = True
    fake.account_info.return_value = mock.Mock(login=42, balance=1.0, currency="USD", server="S", trade_mode=mode)
    monkeypatch.setattr(mc, "mt5", fake, raising=False)
    monkeypatch.setattr(mc, "MT5_AVAILABLE", True)
    monkeypatch.setattr(mc, "MT5_CONFIG", {"login": None, "password": "", "server": "S", "timeout": 1})
    monkeypatch.setattr(mc, "ALLOW_LIVE_ACCOUNT", False)
    conn = mc.MT5Connector()
    with mock.patch.object(panic, "connector", conn):
        assert panic.panic_close() == 2
    fake.order_send.assert_not_called()
    fake.positions_get.assert_not_called()
    fake.orders_get.assert_not_called()


def test_panic_with_a_fake_demo_terminal_sends_close_requests_only_for_bot_positions(panic, monkeypatch):
    fake = mock.MagicMock()
    fake.ACCOUNT_TRADE_MODE_DEMO, fake.ACCOUNT_TRADE_MODE_CONTEST, fake.ACCOUNT_TRADE_MODE_REAL = DEMO, CONTEST, REAL
    fake.TRADE_RETCODE_DONE = 10009
    fake.initialize.return_value = True
    fake.account_info.return_value = mock.Mock(login=42, balance=1.0, currency="USD", server="S", trade_mode=DEMO)
    mine   = mock.Mock(ticket=1, symbol="EURUSD", type=0, volume=0.1, magic=BOT_MAGIC, price_open=1, sl=0, tp=0, profit=0,
                       time=1_700_000_000)
    manual = mock.Mock(ticket=2, symbol="GBPUSD", type=0, volume=0.1, magic=0, price_open=1, sl=0, tp=0, profit=0,
                       time=1_700_000_000)
    fake.positions_get.side_effect = lambda **kw: [p for p in (mine, manual) if p.ticket == kw["ticket"]] if kw else [mine, manual]
    fake.orders_get.return_value = []
    fake.symbol_info_tick.return_value = mock.Mock(bid=1.0, ask=1.1, time_msc=0)
    fake.order_send.return_value = mock.Mock(retcode=10009)
    monkeypatch.setattr(mc, "mt5", fake, raising=False)
    monkeypatch.setattr(mc, "MT5_AVAILABLE", True)
    monkeypatch.setattr(mc, "MT5_CONFIG", {"login": None, "password": "", "server": "S", "timeout": 1})
    monkeypatch.setattr(mc, "ALLOW_LIVE_ACCOUNT", False)
    conn = mc.MT5Connector()
    monkeypatch.setattr(conn, "detect_server_utc_offset", lambda: 0)
    with mock.patch.object(panic, "connector", conn):
        assert panic.panic_close() == 0
    sent = [c.args[0] for c in fake.order_send.call_args_list]
    assert [r["position"] for r in sent] == [1]
    fake.shutdown.assert_called()


def test_importing_panic_close_does_nothing(panic):
    """The module only defines panic_close(); it acts only when run as a script."""
    assert callable(panic.panic_close)
