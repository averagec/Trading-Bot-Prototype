"""Live bot hygiene: fail closed on missing data (account info, order_send results). MT5 is mocked throughout."""

import logging
import sys
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import mt5_connector as mc   # noqa: E402
from config import BOT_MAGIC   # noqa: E402


def _connector(monkeypatch, connected=True, available=True):
    fake = mock.MagicMock()
    fake.TRADE_RETCODE_DONE = 10009
    monkeypatch.setattr(mc, "mt5", fake, raising=False)
    monkeypatch.setattr(mc, "MT5_AVAILABLE", available)
    conn = mc.MT5Connector()
    conn.connected = connected
    return conn, fake


# ── (a) get_account_info ───────────────────────────────────────────────────────

def test_session_down_with_the_library_present_gives_no_account(monkeypatch):
    """Used to fabricate a $10,000 account, which let the bot size and place trades with no real data."""
    conn, _ = _connector(monkeypatch, connected=False)
    assert conn.get_account_info() is None


def test_unreadable_account_info_gives_no_account(monkeypatch):
    conn, fake = _connector(monkeypatch)
    fake.account_info.return_value = None
    assert conn.get_account_info() is None


def test_library_missing_keeps_the_synthetic_simulation_account(monkeypatch):
    conn, _ = _connector(monkeypatch, connected=False, available=False)
    acct = conn.get_account_info()
    assert acct["balance"] == 10000.0 and acct["equity"] == 10000.0


def test_connected_account_reports_login_and_balances(monkeypatch):
    conn, fake = _connector(monkeypatch)
    fake.account_info.return_value = mock.Mock(login=42, balance=5.0, equity=6.0, margin_free=7.0, currency="USD", profit=1.0)
    assert conn.get_account_info() == {"login": 42, "balance": 5.0, "equity": 6.0, "margin_free": 7.0,
                                       "currency": "USD", "profit": 1.0}


# ── (b) _run_cycle with no account ─────────────────────────────────────────────

def _cycle(live_main, account, positions=(), due=False, caplog=None):
    conn = mock.Mock()
    conn.account_still_allowed.return_value = True
    conn.get_account_info.return_value = account
    conn.get_open_positions.return_value = list(positions)
    conn.get_pending_orders.return_value = []
    rm = mock.Mock()
    rm.can_trade.return_value = {"allowed": True, "reason": "ok"}
    bot = live_main.ForexBot()
    with mock.patch.object(live_main, "connector", conn), mock.patch.object(live_main, "risk_manager", rm), \
            mock.patch.object(live_main, "excel_manager", mock.Mock()), \
            mock.patch.object(live_main, "news_agent", mock.Mock(get_upcoming_events=lambda hours_ahead: [])), \
            mock.patch.object(live_main, "order_manager", mock.Mock(get_expired_pending=lambda p: [])), \
            mock.patch.object(live_main.ForexBot, "_friday_flatten_due", return_value=due), \
            mock.patch.object(live_main.ForexBot, "_friday_flatten") as flat, \
            mock.patch.object(live_main.ForexBot, "_manage_breakeven") as be, \
            mock.patch.object(live_main.ForexBot, "_analyse_pair") as analyse:
        bot._run_cycle()
    return conn, rm, flat, be, analyse


def test_no_account_means_no_entries_one_warning_and_breakeven_still_runs(live_main, caplog):
    pos = [{"ticket": 5, "symbol": "EURUSD", "magic": BOT_MAGIC}]
    with caplog.at_level(logging.WARNING):
        conn, rm, flat, be, analyse = _cycle(live_main, None, pos)
    analyse.assert_not_called()
    rm.can_trade.assert_not_called()
    be.assert_called_once_with(pos)
    conn.account_still_allowed.assert_called_once()                  # the account-type guard still runs
    assert [r.getMessage() for r in caplog.records if "Account info unavailable" in r.getMessage()] != []
    assert len([r for r in caplog.records if r.levelno == logging.WARNING and "Account info" in r.getMessage()]) == 1


def test_no_account_still_runs_the_friday_flatten(live_main):
    pos = [{"ticket": 5, "symbol": "EURUSD", "magic": BOT_MAGIC}]
    _, _, flat, _, analyse = _cycle(live_main, None, pos, due=True)
    flat.assert_called_once()
    analyse.assert_not_called()


def test_with_an_account_pairs_are_analysed_as_before(live_main):
    _, rm, _, _, analyse = _cycle(live_main, {"balance": 1000, "equity": 1000})
    rm.can_trade.assert_called_once()
    assert analyse.call_count == len(live_main.PAIRS)


def test_order_placement_without_a_balance_is_rejected_not_defaulted_to_10000(live_main):
    bot = live_main.ForexBot()
    conn = mock.Mock()
    conn.get_tick.return_value = {"bid": 1.1, "ask": 1.1}
    with mock.patch.object(live_main, "connector", conn), \
            mock.patch.object(live_main.risk_manager, "find_structure_sl", return_value=1.09):
        bot._place_market_order("EURUSD", "BUY", {}, {}, None)
        bot._place_limit_order("EURUSD", "BUY", 1.1, {}, {}, None)
    conn.place_order.assert_not_called()
    conn.place_pending_order.assert_not_called()


# ── (c) order_send returning None ──────────────────────────────────────────────

def test_market_order_with_none_result_returns_none(monkeypatch):
    conn, fake = _connector(monkeypatch)
    fake.symbol_info_tick.return_value = mock.Mock(ask=1.1, bid=1.0)
    fake.order_send.return_value = None
    assert conn.place_order("EURUSD", "BUY", 0.1, 1.0, 1.2) is None


def test_pending_order_with_none_result_returns_none(monkeypatch):
    conn, fake = _connector(monkeypatch)
    fake.order_send.return_value = None
    assert conn.place_pending_order("EURUSD", "BUY", "LIMIT", 1.05, 0.1, 1.0, 1.2) is None


def test_cancel_with_none_result_returns_false(monkeypatch):
    conn, fake = _connector(monkeypatch)
    fake.order_send.return_value = None
    assert conn.cancel_pending_order(7) is False


def test_close_with_none_result_returns_false(monkeypatch):
    conn, fake = _connector(monkeypatch)
    fake.positions_get.return_value = [mock.Mock(type=0, symbol="EURUSD", volume=0.1)]
    fake.symbol_info_tick.return_value = mock.Mock(bid=1.0, ask=1.1)
    fake.order_send.return_value = None
    assert conn.close_position(7) is False


def test_modify_sl_with_none_result_does_not_raise(live_main):
    fake = mock.MagicMock()
    fake.order_send.return_value = None
    log = mock.Mock()
    with mock.patch.object(live_main, "mt5", fake, create=True), mock.patch.object(live_main, "MT5_AVAILABLE", True), \
            mock.patch.object(live_main, "connector", mock.Mock(connected=True)), mock.patch.object(live_main, "logger", log):
        live_main.ForexBot()._modify_sl(1, "EURUSD", 1.1, 1.2)
    assert "None" in log.error.call_args.args[0]


@pytest.mark.parametrize("call", ["market", "pending", "close"])
def test_orders_are_stamped_with_the_configured_magic(monkeypatch, call):
    conn, fake = _connector(monkeypatch)
    fake.symbol_info_tick.return_value = mock.Mock(ask=1.1, bid=1.0)
    fake.positions_get.return_value = [mock.Mock(type=0, symbol="EURUSD", volume=0.1)]
    fake.order_send.return_value = mock.Mock(retcode=fake.TRADE_RETCODE_DONE, order=1, price=1.1, volume=0.1)
    if call == "market":
        conn.place_order("EURUSD", "BUY", 0.1, 1.0, 1.2)
    elif call == "pending":
        conn.place_pending_order("EURUSD", "BUY", "LIMIT", 1.05, 0.1, 1.0, 1.2)
    else:
        conn.close_position(1)
    assert fake.order_send.call_args.args[0]["magic"] == BOT_MAGIC


def test_pending_orders_of_other_magic_numbers_are_ignored(monkeypatch):
    conn, fake = _connector(monkeypatch)
    mine  = mock.Mock(ticket=1, symbol="EURUSD", type=2, price_open=1.0, sl=0.9, tp=1.3, volume_initial=0.1,
                      magic=BOT_MAGIC, time_setup=1_700_000_000)
    other = mock.Mock(ticket=2, symbol="GBPUSD", type=2, price_open=1.0, sl=0.9, tp=1.3, volume_initial=0.1,
                      magic=BOT_MAGIC + 1, time_setup=1_700_000_000)
    fake.orders_get.return_value = [mine, other]
    assert [o["ticket"] for o in conn.get_pending_orders()] == [1]
