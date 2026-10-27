"""Live bot hygiene: pending-order expiry works from the broker's setup time, so it survives a restart.
MT5 is mocked; nothing here touches a terminal."""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import mt5_connector as mc   # noqa: E402
from config import BOT_MAGIC   # noqa: E402
from utils.order_manager import OrderManager, PENDING_EXPIRY_MINS   # noqa: E402


def ago(minutes):
    return datetime.now(timezone.utc) - timedelta(minutes=minutes)


def pend(ticket, symbol, minutes_ago):
    return {"ticket": ticket, "symbol": symbol, "time_setup": ago(minutes_ago)}


def test_order_placed_before_a_restart_expires():
    """A fresh OrderManager has no record of the order; the broker's setup time alone must expire it."""
    om = OrderManager()
    assert om.get_expired_pending([pend(7, "EURUSD", PENDING_EXPIRY_MINS + 5)]) == [{"symbol": "EURUSD", "ticket": 7}]


def test_young_order_is_kept():
    assert OrderManager().get_expired_pending([pend(7, "EURUSD", PENDING_EXPIRY_MINS - 5)]) == []


def test_only_the_old_orders_are_returned():
    om = OrderManager()
    out = om.get_expired_pending([pend(1, "EURUSD", 90), pend(2, "GBPUSD", 5), pend(3, "USDJPY", 31)])
    assert [o["ticket"] for o in out] == [1, 3]


def test_broker_time_wins_over_our_own_record():
    """Our record says 'just placed' but the broker says it is old (e.g. a re-used symbol): trust the broker."""
    om = OrderManager()
    om.track_pending("EURUSD", {"ticket": 7})
    assert om.get_expired_pending([pend(7, "EURUSD", 120)]) == [{"symbol": "EURUSD", "ticket": 7}]


def test_fallback_to_our_record_when_the_order_has_no_setup_time():
    om = OrderManager()
    om.track_pending("EURUSD", {"ticket": 7})
    om.pending_orders["EURUSD"]["placed_at"] = ago(PENDING_EXPIRY_MINS + 1)
    assert om.get_expired_pending([{"ticket": 7, "symbol": "EURUSD"}]) == [{"symbol": "EURUSD", "ticket": 7}]
    assert om.get_expired_pending([{"ticket": 8, "symbol": "EURUSD"}]) == []          # a different order is not covered


def test_order_without_any_time_information_is_not_expired():
    assert OrderManager().get_expired_pending([{"ticket": 7, "symbol": "EURUSD"}]) == []


def test_records_of_old_orders_gone_from_the_broker_are_forgotten():
    om = OrderManager()
    om.track_pending("EURUSD", {"ticket": 7})
    om.pending_orders["EURUSD"]["placed_at"] = ago(60)
    om.track_pending("GBPUSD", {"ticket": 8})                                           # young and not listed yet: kept
    assert om.get_expired_pending([]) == []
    assert list(om.pending_orders) == ["GBPUSD"]


def test_connector_exposes_the_setup_time_in_utc_with_the_server_offset(monkeypatch):
    fake = mock.MagicMock()
    monkeypatch.setattr(mc, "mt5", fake, raising=False)
    monkeypatch.setattr(mc, "MT5_AVAILABLE", True)
    conn = mc.MT5Connector()
    conn.connected = True
    conn.server_utc_offset_hours = 3                      # broker clock is UTC+3
    raw = int(datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc).timestamp())      # broker shows 12:00 -> 09:00 UTC
    fake.orders_get.return_value = [mock.Mock(ticket=1, symbol="EURUSD", type=2, price_open=1.0, sl=0.9, tp=1.3,
                                              volume_initial=0.1, magic=BOT_MAGIC, time_setup=raw)]
    out = conn.get_pending_orders()
    assert out[0]["time_setup"] == datetime(2026, 10, 27, 9, 0, tzinfo=timezone.utc)


def test_restart_flow_cancels_the_orphan_through_the_cycle(live_main):
    """End to end through _run_cycle with a brand-new OrderManager: the old broker order is cancelled."""
    conn = mock.Mock()
    conn.account_still_allowed.return_value = True
    conn.get_account_info.return_value = {"balance": 1000, "equity": 1000}
    conn.get_open_positions.return_value = []
    conn.get_pending_orders.return_value = [pend(7, "EURUSD", 45)]
    rm = mock.Mock()
    rm.can_trade.return_value = {"allowed": True, "reason": "ok"}
    with mock.patch.object(live_main, "connector", conn), mock.patch.object(live_main, "risk_manager", rm), \
            mock.patch.object(live_main, "excel_manager", mock.Mock()), \
            mock.patch.object(live_main, "order_manager", OrderManager()), \
            mock.patch.object(live_main, "news_agent", mock.Mock(get_upcoming_events=lambda hours_ahead: [])), \
            mock.patch.object(live_main.ForexBot, "_friday_flatten_due", return_value=False), \
            mock.patch.object(live_main.ForexBot, "_analyse_pair"):
        live_main.ForexBot()._run_cycle()
    conn.cancel_pending_order.assert_called_once_with(7)
