"""Live bot: the news agent never touches the shared MT5 session, caches the calendar on disk, and fails closed.
HTTP and MT5 are mocked throughout; nothing here uses the network, a terminal, or the project's own cache file."""

import ast
import json
import logging
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import agents.news_agent as na          # noqa: E402
from config import NEWS                 # noqa: E402

T0 = datetime(2026, 10, 28, 10, 0, tzinfo=timezone.utc)      # a Wednesday


class Clock:
    def __init__(self, now=T0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, **kw):
        self.now += timedelta(**kw)


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(na, "_utcnow", c)
    return c


@pytest.fixture(autouse=True)
def news_settings(monkeypatch):
    """Pin the settings these tests rely on, whatever config.py currently says."""
    for key, value in {"fail_closed": True, "cache_max_age_hours": 24, "refresh_mins": 60, "retry_mins": 10,
                       "rate_limit_backoff_mins": 60, "no_trade_before_mins": 15, "no_trade_after_mins": 15,
                       "impact_filter": ["High"]}.items():
        monkeypatch.setitem(NEWS, key, value)


@pytest.fixture
def cache_file(tmp_path):
    return tmp_path / "news_cache.json"


@pytest.fixture
def agent(cache_file):
    return na.NewsAgent(cache_file)


def resp(status=200, payload=None, text="", headers=None):
    r = mock.Mock()
    r.status_code = status
    r.json.return_value = payload
    r.text = text
    r.headers = headers or {}
    return r


def ff_event(when_utc, title="US CPI", country="USD", impact="High"):
    local = when_utc.astimezone(timezone(timedelta(hours=-4)))       # the live feed carries a UTC offset
    return {"title": title, "country": country, "date": local.isoformat(), "impact": impact,
            "forecast": "", "previous": ""}


def ff_ok(*events):
    return resp(200, payload=list(events))


def route(table):
    """requests.get stand-in: table maps a URL substring to a response, an exception, or a callable."""
    calls = []

    def fake_get(url, **kw):
        calls.append(url)
        for key, value in table.items():
            if key in url:
                if isinstance(value, Exception):
                    raise value
                return value() if isinstance(value, types.FunctionType) else value
        raise AssertionError(f"unexpected URL {url}")
    fake_get.calls = calls
    return fake_get


def all_fail():
    import requests
    return route({"faireconomy": requests.ConnectionError("down"), "fxstreet": resp(404), "dailyfx": resp(500)})


def write_cache(path, fetched_at, events, source="ForexFactory"):
    path.write_text(json.dumps({
        "fetched_at": fetched_at.isoformat(), "source": source,
        "events": [{"title": t, "country": c, "impact": "High", "time_utc": w.isoformat(), "forecast": "", "previous": ""}
                   for t, c, w in events]}), encoding="utf-8")


# ── the shared MT5 session is never touched ────────────────────────────────────

def test_never_touches_the_mt5_session(agent, clock):
    fake_mt5 = mock.MagicMock()
    ev = ff_event(T0 + timedelta(minutes=5))
    with mock.patch.dict(sys.modules, {"MetaTrader5": fake_mt5}), \
         mock.patch.object(na.requests, "get", route({"faireconomy": ff_ok(ev)})):
        agent.is_news_window("EURUSD")
        clock.advance(hours=2)                                   # cache expiry: the old code re-initialised MT5 here
        agent.is_news_window("EURUSD")
        agent.summary("EURUSD")
    fake_mt5.initialize.assert_not_called()
    fake_mt5.shutdown.assert_not_called()
    assert fake_mt5.mock_calls == []


def test_module_does_not_import_metatrader5():
    tree = ast.parse(Path(na.__file__).read_text(encoding="utf-8"))
    imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    imported |= {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert "MetaTrader5" not in imported
    names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert not names & {"initialize", "shutdown", "calendar_value_get"}


# ── sources ────────────────────────────────────────────────────────────────────

def test_current_forexfactory_feed_is_parsed_with_its_utc_offset(agent, clock):
    ev = ff_event(T0 + timedelta(minutes=5))
    with mock.patch.object(na.requests, "get", route({"faireconomy": ff_ok(ev)})):
        events = agent.fetch_calendar()
    assert len(events) == 1
    assert events[0]["time_utc"] == T0 + timedelta(minutes=5)
    assert events[0]["time_utc"].utcoffset() == timedelta(0)


def test_legacy_forexfactory_feed_is_still_parsed(agent, clock):
    legacy = {"title": "FOMC", "country": "usd", "date": "10-28-2026", "time": "2:00pm", "impact": "High"}
    with mock.patch.object(na.requests, "get", route({"faireconomy": ff_ok(legacy)})):
        events = agent.fetch_calendar()
    assert events[0]["time_utc"] == datetime(2026, 10, 28, 14, 0, tzinfo=timezone.utc)
    assert events[0]["country"] == "USD"


def test_low_impact_and_untimed_events_are_dropped(agent, clock):
    raw = [ff_event(T0, impact="Low"), {"title": "x", "country": "USD", "date": "10-28-2026", "time": "All Day",
                                        "impact": "High"}, ff_event(T0 + timedelta(hours=1), impact="Medium")]
    with mock.patch.object(na.requests, "get", route({"faireconomy": ff_ok(*raw)})):
        events = agent.fetch_calendar()
    assert [e["impact"] for e in events] == ["Medium"]


def test_sources_are_tried_in_order_and_rss_is_the_fallback(agent, clock):
    rss = ("<rss><channel><item><title>EUR ECB interest rate decision</title>"
           "<pubDate>Wed, 28 Oct 2026 12:15:00 +0000</pubDate></item></channel></rss>")
    getter = route({"faireconomy": resp(500), "fxstreet": resp(200, text=rss)})
    with mock.patch.object(na.requests, "get", getter):
        events = agent.fetch_calendar()
    assert [e["country"] for e in events] == ["EUR"]
    assert agent.source_used.startswith("RSS")
    assert any("faireconomy" in u for u in getter.calls) and not any("dailyfx" in u for u in getter.calls)


def test_forexfactory_is_preferred_when_it_works(agent, clock):
    getter = route({"faireconomy": ff_ok(ff_event(T0 + timedelta(hours=1)))})
    with mock.patch.object(na.requests, "get", getter):
        agent.fetch_calendar()
    assert agent.source_used == "ForexFactory"
    assert not any("fxstreet" in u for u in getter.calls)


# ── cache: on disk, hourly, 429 back-off ───────────────────────────────────────

def test_good_result_is_cached_on_disk_with_a_timestamp(agent, clock, cache_file):
    with mock.patch.object(na.requests, "get", route({"faireconomy": ff_ok(ff_event(T0 + timedelta(hours=1)))})):
        agent.fetch_calendar()
    data = json.loads(cache_file.read_text(encoding="utf-8"))
    assert datetime.fromisoformat(data["fetched_at"]) == T0
    assert data["source"] == "ForexFactory" and len(data["events"]) == 1
    assert not list(cache_file.parent.glob("*.tmp"))


def test_default_cache_file_is_in_the_project_folder_on_a():
    assert na.CACHE_PATH == ROOT / NEWS["cache_file"]
    assert na.CACHE_PATH.parent == Path(na.__file__).resolve().parents[1]
    assert na.CACHE_PATH.name == "news_cache.json"
    assert na.NewsAgent().cache_path == na.CACHE_PATH


def test_a_good_calendar_is_refetched_at_most_hourly(agent, clock):
    getter = route({"faireconomy": ff_ok(ff_event(T0 + timedelta(hours=3)))})
    with mock.patch.object(na.requests, "get", getter):
        for _ in range(20):
            agent.is_news_window("EURUSD")
            agent.get_upcoming_events()
            clock.advance(minutes=2)                              # 40 min in total
        assert len(getter.calls) == 1
        clock.advance(minutes=30)                                 # past the hour
        agent.fetch_calendar()
    assert len(getter.calls) == 2


def test_a_fresh_disk_cache_is_used_at_startup_without_any_request(agent, clock, cache_file):
    write_cache(cache_file, T0 - timedelta(minutes=20), [("US CPI", "USD", T0 + timedelta(minutes=5))])
    getter = route({})                                            # any request would raise
    with mock.patch.object(na.requests, "get", getter):
        res = agent.is_news_window("EURUSD")
    assert getter.calls == []
    assert res["is_blocked"] is True and "US CPI" in res["reason"]


def test_http_429_backs_off_instead_of_hammering(agent, clock):
    getter = route({"faireconomy": resp(429, headers={}), "fxstreet": resp(404), "dailyfx": resp(404)})
    with mock.patch.object(na.requests, "get", getter):
        agent.fetch_calendar()
        first = [u for u in getter.calls if "faireconomy" in u]
        assert len(first) == 2                                    # both mirrors were tried once
        for _ in range(5):                                        # a retry round every retry_mins, inside the back-off
            clock.advance(minutes=11)
            agent.fetch_calendar()
        assert len([u for u in getter.calls if "faireconomy" in u]) == 2
        clock.advance(minutes=30)                                 # 66 min after the 429: allowed again
        agent.fetch_calendar()
    assert len([u for u in getter.calls if "faireconomy" in u]) == 4


def test_http_429_honours_a_longer_retry_after(agent, clock):
    getter = route({"faireconomy": resp(429, headers={"Retry-After": "7200"}), "fxstreet": resp(404),
                    "dailyfx": resp(404)})
    with mock.patch.object(na.requests, "get", getter):
        agent.fetch_calendar()
        clock.advance(minutes=100)
        agent.fetch_calendar()
        assert len([u for u in getter.calls if "faireconomy" in u]) == 2
        clock.advance(minutes=30)                                 # past 2 h
        agent.fetch_calendar()
    assert len([u for u in getter.calls if "faireconomy" in u]) == 4


def test_a_failed_round_is_not_retried_every_cycle(agent, clock):
    getter = all_fail()
    with mock.patch.object(na.requests, "get", getter):
        for _ in range(9):
            agent.is_news_window("EURUSD")
            clock.advance(minutes=1)
        n = len(getter.calls)
        clock.advance(minutes=5)
        agent.is_news_window("EURUSD")
    assert n == 4                                                 # FF x2 mirrors + fxstreet + dailyfx, once
    assert len(getter.calls) == 8                                 # one more round after retry_mins


# ── fail closed ────────────────────────────────────────────────────────────────

def test_all_sources_failing_with_no_cache_blocks_new_entries(agent, clock):
    with mock.patch.object(na.requests, "get", all_fail()):
        res = agent.is_news_window("EURUSD")
        summ = agent.summary("EURUSD")
    assert res["is_blocked"] is True and "fail closed" in res["reason"]
    assert summ["news_window"]["is_blocked"] is True
    assert agent.get_upcoming_events() == []


def test_fail_closed_can_be_switched_off(agent, clock, monkeypatch):
    monkeypatch.setitem(NEWS, "fail_closed", False)
    with mock.patch.object(na.requests, "get", all_fail()):
        res = agent.is_news_window("EURUSD")
    assert res["is_blocked"] is False and "off" in res["reason"]


def test_fail_closed_is_the_default_in_config():
    """Read config.py as shipped (the autouse fixture pins values on the live dict)."""
    tree = ast.parse((ROOT / "config.py").read_text(encoding="utf-8"))
    node = next(n for n in tree.body if isinstance(n, ast.Assign) and n.targets[0].id == "NEWS")
    shipped = ast.literal_eval(node.value)
    assert shipped["fail_closed"] is True
    assert shipped["cache_max_age_hours"] == 24 and shipped["refresh_mins"] == 60
    assert shipped["cache_file"] == "news_cache.json"


def test_stale_but_valid_disk_cache_is_used_when_every_source_fails(agent, clock, cache_file):
    write_cache(cache_file, T0 - timedelta(hours=5), [("US CPI", "USD", T0 + timedelta(minutes=5))])
    with mock.patch.object(na.requests, "get", all_fail()):
        res = agent.is_news_window("EURUSD")
        other = agent.is_news_window("AUDJPY")
    assert res["is_blocked"] is True and "US CPI" in res["reason"]      # the cached event still blocks
    assert other["is_blocked"] is False and "fail closed" not in other["reason"]   # calendar is available, no AUD/JPY news


def test_cache_older_than_the_maximum_age_is_not_trusted(agent, clock, cache_file):
    write_cache(cache_file, T0 - timedelta(hours=25), [("US CPI", "USD", T0 + timedelta(hours=3))])
    with mock.patch.object(na.requests, "get", all_fail()):
        res = agent.is_news_window("AUDJPY")
    assert res["is_blocked"] is True and "fail closed" in res["reason"]


def test_a_cached_calendar_expires_while_the_sources_stay_down(agent, clock, cache_file):
    write_cache(cache_file, T0 - timedelta(hours=20), [("US CPI", "USD", T0 + timedelta(hours=3))])
    with mock.patch.object(na.requests, "get", all_fail()):
        assert "fail closed" not in agent.is_news_window("AUDJPY")["reason"]
        clock.advance(hours=5)                                    # now 25 h old
        assert "fail closed" in agent.is_news_window("AUDJPY")["reason"]


@pytest.mark.parametrize("content", ["not json", "{}", json.dumps({"fetched_at": "x", "events": []})])
def test_an_unreadable_cache_file_is_ignored(agent, clock, cache_file, content):
    cache_file.write_text(content, encoding="utf-8")
    with mock.patch.object(na.requests, "get", all_fail()):
        assert agent.is_news_window("EURUSD")["is_blocked"] is True


def test_a_future_dated_cache_is_ignored(agent, clock, cache_file):
    write_cache(cache_file, T0 + timedelta(days=30), [("US CPI", "USD", T0 + timedelta(hours=3))])
    with mock.patch.object(na.requests, "get", all_fail()):
        assert "fail closed" in agent.is_news_window("AUDJPY")["reason"]


def test_a_working_source_replaces_a_stale_cache_and_unblocks(agent, clock, cache_file):
    write_cache(cache_file, T0 - timedelta(hours=30), [("old", "USD", T0 - timedelta(hours=29))])
    with mock.patch.object(na.requests, "get", route({"faireconomy": ff_ok(ff_event(T0 + timedelta(hours=3)))})):
        res = agent.is_news_window("EURUSD")
    assert res["is_blocked"] is False and "fail closed" not in res["reason"]
    assert json.loads(cache_file.read_text(encoding="utf-8"))["events"][0]["title"] == "US CPI"


# ── logging: one line per state change ─────────────────────────────────────────

def test_state_changes_are_logged_once_not_every_cycle(agent, clock, caplog):
    import requests
    ok = {"on": False}
    ev = ff_event(T0 + timedelta(hours=40))

    def ff():
        if not ok["on"]:
            raise requests.ConnectionError("down")
        return ff_ok(ev)
    fake = route({"faireconomy": ff, "fxstreet": resp(404), "dailyfx": resp(404)})
    with caplog.at_level(logging.INFO, logger=na.logger.name), mock.patch.object(na.requests, "get", fake):
        for _ in range(100):
            agent.is_news_window("EURUSD")
            clock.advance(minutes=1)
        lost = [r for r in caplog.records if "CALENDAR LOST" in r.getMessage()]
        assert len(lost) == 1 and lost[0].levelno == logging.WARNING
        assert "BLOCKED" in lost[0].getMessage()
        ok["on"] = True
        clock.advance(minutes=15)
        for _ in range(100):
            agent.is_news_window("EURUSD")
            clock.advance(minutes=1)
    restored = [r for r in caplog.records if "CALENDAR RESTORED" in r.getMessage()]
    assert len(restored) == 1 and restored[0].levelno == logging.WARNING
    assert len([r for r in caplog.records if r.levelno >= logging.WARNING and "CALENDAR" in r.getMessage()]) == 2


def test_falling_back_to_the_cache_is_logged_once(agent, clock, cache_file, caplog):
    write_cache(cache_file, T0 - timedelta(hours=3), [("US CPI", "USD", T0 + timedelta(hours=3))])
    with caplog.at_level(logging.INFO, logger=na.logger.name), mock.patch.object(na.requests, "get", all_fail()):
        for _ in range(30):
            agent.is_news_window("EURUSD")
            clock.advance(minutes=1)
    degraded = [r for r in caplog.records if "CALENDAR DEGRADED" in r.getMessage()]
    assert len(degraded) == 1 and degraded[0].levelno == logging.WARNING


# ── behaviour that must stay the same ──────────────────────────────────────────

def test_blocks_only_inside_the_window_and_only_for_the_events_currencies(agent, clock):
    ev = ff_event(T0 + timedelta(minutes=10), country="USD")
    with mock.patch.object(na.requests, "get", route({"faireconomy": ff_ok(ev)})):
        assert agent.is_news_window("EURUSD")["is_blocked"] is True       # 10 min before
        assert agent.is_news_window("AUDJPY")["is_blocked"] is False      # no USD leg
        clock.advance(minutes=24)                                          # 14 min after
        assert agent.is_news_window("EURUSD")["is_blocked"] is True
        clock.advance(minutes=2)                                           # 16 min after
        assert agent.is_news_window("EURUSD")["is_blocked"] is False


def test_summary_and_upcoming_keep_the_shape_main_and_the_llm_agent_read(agent, clock):
    ev = ff_event(T0 + timedelta(hours=2))
    with mock.patch.object(na.requests, "get", route({"faireconomy": ff_ok(ev)})):
        summ = agent.summary("EURUSD")
        upcoming = agent.get_upcoming_events(hours_ahead=4)
    assert {"news_window", "upcoming_count", "upcoming", "source"} <= set(summ)
    assert {"is_blocked", "reason"} <= set(summ["news_window"])
    assert summ["upcoming_count"] == 1 and len(upcoming) == 1
    assert not hasattr(agent, "get_pair_news_bias")
