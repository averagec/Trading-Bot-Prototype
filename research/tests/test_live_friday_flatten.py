"""Live bot: Friday flatten (main.py). MT5 is mocked; nothing here touches a terminal or places an order."""

import importlib
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="module")
def live_main(tmp_path_factory):
    """Import main.py from a scratch cwd so its FileHandler('forex_bot.log') does not touch the live log."""
    import os
    cwd = os.getcwd()
    os.chdir(tmp_path_factory.mktemp("livecwd"))
    try:
        sys.modules.pop("main", None)
        mod = importlib.import_module("main")
    finally:
        os.chdir(cwd)
    return mod


def utc(y, m, d, hh, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=timezone.utc)


# 2026-10-23 is a Friday
@pytest.mark.parametrize("when,expected", [
    (utc(2026, 10, 23, 19, 59), False),
    (utc(2026, 10, 23, 20, 0), True),
    (utc(2026, 10, 23, 20, 30), True),
    (utc(2026, 10, 23, 22, 55), True),
    (utc(2026, 10, 22, 21, 0), False),     # Thursday evening
    (utc(2026, 10, 24, 0, 0), True),       # Saturday: keep retrying anything the Friday attempts left open
    (utc(2026, 10, 24, 21, 0), True),
    (utc(2026, 10, 25, 23, 59), True),     # Sunday, last minute
    (utc(2026, 10, 26, 0, 0), False),      # Monday back to normal
    (utc(2026, 10, 26, 9, 0), False),
])
def test_flatten_window(live_main, when, expected):
    assert live_main.ForexBot._friday_flatten_due(when) is expected


def test_flatten_disabled_when_none(live_main):
    with mock.patch.object(live_main, "FRIDAY_FLATTEN_UTC", None):
        assert live_main.ForexBot._friday_flatten_due(utc(2026, 10, 23, 21, 0)) is False


def bot_pos(ticket, symbol):
    return {"ticket": ticket, "symbol": symbol, "magic": 20250101}


def test_bot_magic_matches_connector_magic(live_main):
    """The flatten's BOT_MAGIC must equal the magic the connector stamps on every order: the connector must take it
    from config.BOT_MAGIC everywhere (no numeric literal that could drift)."""
    import re
    import mt5_connector as mc
    src = (ROOT / "mt5_connector.py").read_text(encoding="utf-8")
    assert set(re.findall(r'"magic":\s*(\w+)\s*,', src)) == {"BOT_MAGIC"}
    assert not re.search(r"\b\d{7,}\b", src)                      # no magic-number-sized literal left in the connector
    assert mc.BOT_MAGIC == live_main.BOT_MAGIC


def test_flatten_closes_positions_and_cancels_pendings(live_main):
    bot = live_main.ForexBot()
    conn = mock.Mock()
    conn.cancel_pending_order.return_value = True
    conn.close_position.return_value = True
    om = mock.Mock()
    with mock.patch.object(live_main, "connector", conn), mock.patch.object(live_main, "order_manager", om):
        bot._friday_flatten([bot_pos(1, "EURUSD"), bot_pos(2, "USDJPY")], [{"ticket": 9, "symbol": "GBPUSD"}])
    assert [c.args[0] for c in conn.close_position.call_args_list] == [1, 2]
    conn.cancel_pending_order.assert_called_once_with(9)
    om.clear_pending.assert_called_once_with("GBPUSD")


def test_flatten_leaves_non_bot_positions_alone(live_main):
    """Manual trades (magic 0) and another EA's trades must not be closed."""
    bot = live_main.ForexBot()
    conn = mock.Mock()
    conn.close_position.return_value = True
    positions = [bot_pos(1, "EURUSD"),
                 {"ticket": 2, "symbol": "GBPUSD", "magic": 0},             # manual
                 {"ticket": 3, "symbol": "USDJPY", "magic": 777},           # other EA
                 {"ticket": 4, "symbol": "XAUUSD"}]                         # no magic info -> not ours
    with mock.patch.object(live_main, "connector", conn), mock.patch.object(live_main, "order_manager", mock.Mock()):
        bot._friday_flatten(positions, [])
    assert [c.args[0] for c in conn.close_position.call_args_list] == [1]


def test_flatten_with_only_non_bot_positions_does_nothing(live_main):
    bot = live_main.ForexBot()
    conn = mock.Mock()
    with mock.patch.object(live_main, "connector", conn), mock.patch.object(live_main, "order_manager", mock.Mock()):
        bot._friday_flatten([{"ticket": 2, "symbol": "GBPUSD", "magic": 0}], [])
    conn.close_position.assert_not_called()
    conn.cancel_pending_order.assert_not_called()


def test_flatten_failure_is_not_fatal_and_does_not_clear_pending(live_main):
    bot = live_main.ForexBot()
    conn = mock.Mock()
    conn.cancel_pending_order.return_value = False
    conn.close_position.side_effect = [False, True]
    om = mock.Mock()
    with mock.patch.object(live_main, "connector", conn), mock.patch.object(live_main, "order_manager", om):
        bot._friday_flatten([bot_pos(1, "EURUSD"), bot_pos(2, "USDJPY")], [{"ticket": 9, "symbol": "GBPUSD"}])
    assert conn.close_position.call_count == 2          # a failed close does not stop the others
    om.clear_pending.assert_not_called()                # still pending at the broker -> keep tracking it


def test_flatten_survives_exceptions_and_still_closes_the_rest(live_main):
    """A raising close/cancel (MT5 returned None, disconnect) must not skip the remaining tickets."""
    bot = live_main.ForexBot()
    conn = mock.Mock()
    conn.cancel_pending_order.side_effect = [RuntimeError("boom"), True]
    conn.close_position.side_effect = [AttributeError("'NoneType' object has no attribute 'bid'"), True, True]
    om = mock.Mock()
    with mock.patch.object(live_main, "connector", conn), mock.patch.object(live_main, "order_manager", om):
        bot._friday_flatten([bot_pos(1, "EURUSD"), bot_pos(2, "USDJPY"), bot_pos(3, "GBPUSD")],
                            [{"ticket": 8, "symbol": "AUDUSD"}, {"ticket": 9, "symbol": "USDCAD"}])
    assert [c.args[0] for c in conn.close_position.call_args_list] == [1, 2, 3]
    assert [c.args[0] for c in conn.cancel_pending_order.call_args_list] == [8, 9]
    om.clear_pending.assert_called_once_with("USDCAD")


def test_flatten_logs_error_summary_when_positions_remain_open(live_main):
    """A failed or raising close must leave one ERROR line with the count still open."""
    bot = live_main.ForexBot()
    conn = mock.Mock()
    conn.close_position.side_effect = [False, RuntimeError("boom"), True]
    log = mock.Mock()
    with mock.patch.object(live_main, "connector", conn), mock.patch.object(live_main, "order_manager", mock.Mock()), \
            mock.patch.object(live_main, "logger", log):
        bot._friday_flatten([bot_pos(1, "EURUSD"), bot_pos(2, "USDJPY"), bot_pos(3, "GBPUSD")], [])
    summaries = [c.args[0] for c in log.error.call_args_list if "still open" in c.args[0]]
    assert len(summaries) == 1 and "2 bot position(s) still open" in summaries[0]


def test_flatten_no_error_summary_when_everything_closed(live_main):
    bot = live_main.ForexBot()
    conn = mock.Mock()
    conn.close_position.return_value = True
    log = mock.Mock()
    with mock.patch.object(live_main, "connector", conn), mock.patch.object(live_main, "order_manager", mock.Mock()), \
            mock.patch.object(live_main, "logger", log):
        bot._friday_flatten([bot_pos(1, "EURUSD")], [])
    assert not [c for c in log.error.call_args_list if "still open" in c.args[0]]


@pytest.mark.parametrize("tick,order_result", [(None, "unused"), ("tick", None)])
def test_close_position_returns_false_when_mt5_gives_none(tick, order_result):
    """symbol_info_tick() / order_send() returning None used to raise AttributeError."""
    import mt5_connector as mc
    fake = mock.MagicMock()
    pos = mock.Mock(type=0, symbol="EURUSD", volume=0.1)
    fake.positions_get.return_value = [pos]
    fake.symbol_info_tick.return_value = None if tick is None else mock.Mock(bid=1.1, ask=1.1001)
    fake.order_send.return_value = order_result
    conn = mc.connector
    with mock.patch.object(mc, "mt5", fake), mock.patch.object(mc, "MT5_AVAILABLE", True), \
            mock.patch.object(conn, "connected", True):
        assert conn.close_position(123) is False
    if tick is None:
        fake.order_send.assert_not_called()


def test_get_open_positions_exposes_magic():
    import mt5_connector as mc
    fake = mock.MagicMock()
    p = mock.Mock(ticket=1, symbol="EURUSD", type=0, volume=0.1, price_open=1.1, sl=1.09, tp=1.13, profit=0.0,
                  time=1_700_000_000, magic=20250101)
    fake.positions_get.return_value = [p]
    with mock.patch.object(mc, "mt5", fake), mock.patch.object(mc, "MT5_AVAILABLE", True), \
            mock.patch.object(mc.connector, "connected", True):
        out = mc.connector.get_open_positions()
    assert out[0]["magic"] == 20250101


def test_cycle_flattens_first_and_analyses_nothing(live_main):
    """Even with the daily-loss gate blocking, the Friday flatten runs and no pair is analysed."""
    bot = live_main.ForexBot()
    conn = mock.Mock()
    conn.get_account_info.return_value = {"balance": 100000, "equity": 100000}
    conn.get_open_positions.return_value = [bot_pos(5, "EURUSD")]
    conn.get_pending_orders.return_value = []
    conn.close_position.return_value = True
    rm = mock.Mock()
    rm.can_trade.return_value = {"allowed": False, "type": "daily", "reason": "x"}
    with mock.patch.object(live_main, "connector", conn), mock.patch.object(live_main, "risk_manager", rm), \
            mock.patch.object(live_main.ForexBot, "_friday_flatten_due", return_value=True), \
            mock.patch.object(live_main.ForexBot, "_analyse_pair") as analyse:
        bot._run_cycle()
    conn.close_position.assert_called_once_with(5)
    analyse.assert_not_called()
    rm.can_trade.assert_not_called()


def test_cycle_outside_window_is_unchanged(live_main):
    bot = live_main.ForexBot()
    conn = mock.Mock()
    conn.get_account_info.return_value = {"balance": 100000, "equity": 100000}
    conn.get_open_positions.return_value = [{"ticket": 5, "symbol": "EURUSD", "type": "BUY", "entry": 1, "sl": 1, "tp": 1}]
    conn.get_pending_orders.return_value = []
    rm = mock.Mock()
    rm.can_trade.return_value = {"allowed": True, "reason": "ok"}
    with mock.patch.object(live_main, "connector", conn), mock.patch.object(live_main, "risk_manager", rm),             mock.patch.object(live_main, "excel_manager", mock.Mock()), \
            mock.patch.object(live_main.ForexBot, "_friday_flatten_due", return_value=False), \
            mock.patch.object(live_main.ForexBot, "_friday_flatten") as flat, \
            mock.patch.object(live_main.ForexBot, "_manage_breakeven"), \
            mock.patch.object(live_main.ForexBot, "_analyse_pair") as analyse, \
            mock.patch.object(live_main, "news_agent", mock.Mock(get_upcoming_events=lambda hours_ahead: [])), \
            mock.patch.object(live_main, "order_manager", mock.Mock(get_expired_pending=lambda p: [])):
        bot._run_cycle()
    flat.assert_not_called()
    assert analyse.call_count == len(live_main.PAIRS)
