"""Live bot hygiene: the daily/monthly risk baselines survive a restart, per account login. Files live in tmp_path."""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from utils import risk_manager as rm_mod   # noqa: E402
from utils.risk_manager import RiskManager   # noqa: E402


class _Clock(datetime):
    """datetime whose now() is set by the test."""
    current = datetime(2026, 10, 27, 10, 0, tzinfo=timezone.utc)

    @classmethod
    def now(cls, tz=None):
        return cls.current


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    _Clock.current = datetime(2026, 10, 27, 10, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(rm_mod, "datetime", _Clock)
    return _Clock


def acct(login, balance, equity=None):
    return {"login": login, "balance": balance, "equity": balance if equity is None else equity}


def test_daily_baseline_survives_a_restart(tmp_path):
    f = tmp_path / "b.json"
    RiskManager(f).can_trade(acct(1, 100000))                         # first balance of the day
    restarted = RiskManager(f)                                        # bot restarted after losses
    out = restarted.can_trade(acct(1, 96000, equity=96000))
    assert restarted.daily_start_balance == 100000
    assert out["allowed"] is False and out["type"] == "daily_drawdown"


def test_without_persistence_a_restart_would_forget_the_loss(tmp_path):
    """Control: a fresh manager with no file takes the current (already reduced) balance as its reference."""
    out = RiskManager(tmp_path / "none.json").can_trade(acct(1, 96000))
    assert out["allowed"] is True


def test_a_different_login_does_not_inherit_the_baseline(tmp_path):
    f = tmp_path / "b.json"
    RiskManager(f).can_trade(acct(1, 100000))
    other = RiskManager(f)
    out = other.can_trade(acct(2, 50000))
    assert other.daily_start_balance == 50000 and out["allowed"] is True
    saved = json.loads(f.read_text())
    assert set(saved) == {"1", "2"} and saved["1"]["daily_balance"] == 100000      # account 1's entry is kept


def test_switching_login_inside_one_process_swaps_the_baselines(tmp_path):
    rm = RiskManager(tmp_path / "b.json")
    rm.can_trade(acct(1, 100000))
    rm.can_trade(acct(2, 50000))
    assert rm.daily_start_balance == 50000
    rm.can_trade(acct(1, 99000))
    assert rm.daily_start_balance == 100000


def test_baseline_from_an_earlier_day_is_replaced_but_the_month_is_kept(tmp_path, clock):
    f = tmp_path / "b.json"
    RiskManager(f).can_trade(acct(1, 100000))
    clock.current = datetime(2026, 10, 28, 10, 0, tzinfo=timezone.utc)
    rm = RiskManager(f)
    rm.can_trade(acct(1, 98000))
    assert (rm.daily_start_date, rm.daily_start_balance) == ("2026-10-28", 98000)
    assert (rm.monthly_start_date, rm.monthly_start_balance) == ("2026-10", 100000)


def test_a_new_month_resets_the_monthly_baseline(tmp_path, clock):
    f = tmp_path / "b.json"
    RiskManager(f).can_trade(acct(1, 100000))
    clock.current = datetime(2026, 11, 2, 10, 0, tzinfo=timezone.utc)
    rm = RiskManager(f)
    rm.can_trade(acct(1, 104000))
    assert (rm.monthly_start_date, rm.monthly_start_balance) == ("2026-11", 104000)


def test_monthly_target_is_still_reached_after_a_restart(tmp_path):
    f = tmp_path / "b.json"
    RiskManager(f).can_trade(acct(1, 100000))
    out = RiskManager(f).can_trade(acct(1, 105500))
    assert out["allowed"] is False and out["type"] == "monthly_target"


def test_no_login_means_in_memory_only_and_no_file(tmp_path):
    f = tmp_path / "b.json"
    rm = RiskManager(f)
    rm.can_trade({"balance": 100000, "equity": 100000})
    assert rm.daily_start_balance == 100000 and not f.exists()


def test_file_is_only_written_when_a_baseline_changes(tmp_path):
    rm = RiskManager(tmp_path / "b.json")
    rm.can_trade(acct(1, 100000))
    with mock.patch.object(rm_mod.os, "replace") as replace:
        rm.can_trade(acct(1, 99000))
        rm.can_trade(acct(1, 98000))
    replace.assert_not_called()


@pytest.mark.parametrize("content", ["not json", "[]", "{\"1\": 5}", "{\"1\": {\"daily_date\": \"2026-10-27\", \"daily_balance\": \"x\"}}",
                                     "{\"1\": {\"daily_date\": \"2026-10-27\", \"daily_balance\": -5}}"])
def test_corrupt_or_malformed_file_is_ignored(tmp_path, content):
    f = tmp_path / "b.json"
    f.write_text(content)
    rm = RiskManager(f)
    out = rm.can_trade(acct(1, 100000))
    assert out["allowed"] is True and rm.daily_start_balance == 100000
    assert json.loads(f.read_text())["1"]["daily_balance"] == 100000      # and the file is healed


def test_a_save_failure_never_breaks_the_risk_check(tmp_path):
    rm = RiskManager(tmp_path / "missing_dir" / "b.json")                 # parent folder does not exist -> OSError
    out = rm.can_trade(acct(1, 100000))
    assert out["allowed"] is True and rm.daily_start_balance == 100000


def test_the_singleton_saves_next_to_the_project_not_in_the_cwd():
    assert rm_mod.BASELINES_FILE == Path(rm_mod.__file__).resolve().parent.parent / "risk_baselines.json"
    assert rm_mod.risk_manager.baselines_file == rm_mod.BASELINES_FILE


def test_baseline_definitions_and_limits_are_unchanged():
    assert (rm_mod.DAILY_DRAWDOWN_PCT, rm_mod.MONTHLY_TARGET_PCT, rm_mod.FIXED_RISK_PCT, rm_mod.RR_RATIO) == (3.0, 5.0, 0.25, 3.0)
