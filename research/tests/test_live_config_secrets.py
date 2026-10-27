"""Live bot: no credentials in config.py, and the bot attaches to the terminal's logged-in account by default.
MT5 is mocked throughout."""

import ast
import importlib.util
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
ROOT = Path(__file__).resolve().parents[2]

LOGIN_ENV, PASSWORD_ENV = "FOREX_MT5_LOGIN", "FOREX_MT5_PASSWORD"
DUMMY_LOGIN, DUMMY_PASSWORD = 123456, "dummy-not-a-real-password"


def load_config():
    """Execute config.py as a fresh module so the shared `config` import is left untouched."""
    spec = importlib.util.spec_from_file_location("config_under_test", ROOT / "config.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_credentials_come_from_the_environment(monkeypatch):
    monkeypatch.setenv(LOGIN_ENV, str(DUMMY_LOGIN))
    monkeypatch.setenv(PASSWORD_ENV, DUMMY_PASSWORD)
    cfg = load_config().MT5_CONFIG
    assert cfg["login"] == DUMMY_LOGIN and cfg["password"] == DUMMY_PASSWORD


def test_no_credentials_by_default(monkeypatch):
    monkeypatch.delenv(LOGIN_ENV, raising=False)
    monkeypatch.delenv(PASSWORD_ENV, raising=False)
    cfg = load_config().MT5_CONFIG
    assert cfg["login"] is None and cfg["password"] == ""


def test_config_file_holds_no_login_or_password_literal():
    """`login` and `password` must not be literals in config.py (an account number typed here overrides the terminal)."""
    tree = ast.parse((ROOT / "config.py").read_text(encoding="utf-8"))
    literals = [
        (k.value, v.value)
        for node in ast.walk(tree) if isinstance(node, ast.Dict)
        for k, v in zip(node.keys, node.values)
        if isinstance(k, ast.Constant) and k.value in ("login", "password")
        and isinstance(v, ast.Constant) and v.value not in (None, "")
    ]
    assert literals == []


def _connector(monkeypatch, login, password):
    import mt5_connector as mc
    fake = mock.MagicMock()
    fake.initialize.return_value = True
    fake.ACCOUNT_TRADE_MODE_DEMO = 0
    fake.account_info.return_value = mock.Mock(login=1, balance=1.0, currency="USD", server="S", trade_mode=0)
    cfg = {"login": login, "password": password, "server": "S", "timeout": 1}
    monkeypatch.setattr(mc, "mt5", fake, raising=False)
    monkeypatch.setattr(mc, "MT5_AVAILABLE", True)
    monkeypatch.setattr(mc, "MT5_CONFIG", cfg)
    conn = mc.MT5Connector()
    monkeypatch.setattr(conn, "detect_server_utc_offset", lambda *a, **k: 0)
    return conn, fake


def test_connect_attaches_to_the_terminals_account_without_credentials(monkeypatch):
    """No login/password configured: initialize() must not be given credentials, so the terminal keeps its account."""
    conn, fake = _connector(monkeypatch, None, "")
    assert conn.connect() is True
    kwargs = fake.initialize.call_args.kwargs
    assert not {"login", "password", "server"} & set(kwargs)


def test_connect_with_only_a_password_still_attaches(monkeypatch):
    """A half-configured login (password but no account number) must not trigger a login either."""
    conn, fake = _connector(monkeypatch, None, DUMMY_PASSWORD)
    assert conn.connect() is True
    assert not {"login", "password", "server"} & set(fake.initialize.call_args.kwargs)


def test_connect_logs_in_only_when_login_and_password_are_both_set(monkeypatch):
    conn, fake = _connector(monkeypatch, DUMMY_LOGIN, DUMMY_PASSWORD)
    assert conn.connect() is True
    kwargs = fake.initialize.call_args.kwargs
    assert kwargs["login"] == DUMMY_LOGIN and kwargs["password"] == DUMMY_PASSWORD and kwargs["server"] == "S"
