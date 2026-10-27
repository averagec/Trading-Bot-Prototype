"""Live bot: demo/live guard and account logging in MT5Connector.connect(). MT5 is mocked throughout."""

import importlib.util
import sys
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

DEMO, CONTEST, REAL = 0, 1, 2      # MetaTrader5 ACCOUNT_TRADE_MODE_* values


def _connect(monkeypatch, trade_mode, allow_live=False, server="FTMO-Demo"):
    import mt5_connector as mc
    fake = mock.MagicMock()
    fake.ACCOUNT_TRADE_MODE_DEMO, fake.ACCOUNT_TRADE_MODE_CONTEST, fake.ACCOUNT_TRADE_MODE_REAL = DEMO, CONTEST, REAL
    fake.initialize.return_value = True
    fake.account_info.return_value = mock.Mock(login=42, balance=100000.0, currency="USD", server=server,
                                               trade_mode=trade_mode)
    log = mock.Mock()
    monkeypatch.setattr(mc, "mt5", fake, raising=False)
    monkeypatch.setattr(mc, "MT5_AVAILABLE", True)
    monkeypatch.setattr(mc, "MT5_CONFIG", {"login": None, "password": "", "server": "S", "timeout": 1})
    monkeypatch.setattr(mc, "ALLOW_LIVE_ACCOUNT", allow_live)
    monkeypatch.setattr(mc, "logger", log)
    conn = mc.MT5Connector()
    offset = mock.Mock(return_value=0)
    monkeypatch.setattr(conn, "detect_server_utc_offset", offset)
    return conn, fake, log, offset


def _messages(log_method):
    return [c.args[0] for c in log_method.call_args_list]


def test_demo_account_connects_and_logs_server_and_mode(monkeypatch):
    conn, fake, log, _ = _connect(monkeypatch, DEMO)
    assert conn.connect() is True and conn.connected is True
    line = next(m for m in _messages(log.info) if m.startswith("Connected to MT5"))
    assert "Account: 42" in line and "Server: FTMO-Demo" in line and "DEMO" in line


@pytest.mark.parametrize("mode,name", [(REAL, "REAL"), (CONTEST, "CONTEST")])
def test_non_demo_account_is_refused_by_default(monkeypatch, mode, name):
    conn, fake, log, offset = _connect(monkeypatch, mode, server="FTMO-Server")
    assert conn.connect() is False and conn.connected is False
    fake.shutdown.assert_called_once()                    # release the terminal connection the bot just opened
    offset.assert_not_called()
    err = " ".join(_messages(log.error))
    assert name in err and "Account: 42" in err and "FTMO-Server" in err and "FOREX_ALLOW_LIVE" in err


def test_real_account_is_allowed_with_the_explicit_override(monkeypatch):
    conn, fake, log, _ = _connect(monkeypatch, REAL, allow_live=True)
    assert conn.connect() is True and conn.connected is True
    fake.shutdown.assert_not_called()
    assert any("REAL" in m for m in _messages(log.warning) + _messages(log.info))


def load_config():
    spec = importlib.util.spec_from_file_location("config_under_test", ROOT / "config.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("value,expected", [(None, False), ("", False), ("1", False), ("true", False),
                                            ("yes", False), ("YES", True)])
def test_override_needs_the_exact_word_YES(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv("FOREX_ALLOW_LIVE", raising=False)
    else:
        monkeypatch.setenv("FOREX_ALLOW_LIVE", value)
    assert load_config().ALLOW_LIVE_ACCOUNT is expected
