"""
News Agent - blocks new entries around high-impact economic news.

Structure: sources -> cache -> decision
  sources   ForexFactory JSON, then FXStreet/DailyFX RSS (tried in that order). The MetaTrader5 Python package has
            no economic-calendar API, so MT5 is not a source and this module never touches the MT5 session.
  cache     the last good calendar, in memory and on disk (NEWS["cache_file"]); refetched at most every
            NEWS["refresh_mins"]; a source that answers HTTP 429 is left alone for a back-off period; if every
            source fails the disk cache is used while it is younger than NEWS["cache_max_age_hours"].
  decision  is_news_window() blocks inside an event window; with no usable calendar it blocks every new entry
            when NEWS["fail_closed"] is True (the default).
"""

import json
import logging
import os
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Callable, Optional

from config import NEWS

logger = logging.getLogger(__name__)

CACHE_PATH = Path(__file__).resolve().parents[1] / NEWS["cache_file"]

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

FF_URLS = [
    "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
    "https://cdn-nfs.faireconomy.media/ff_calendar_thisweek.json",
]
RSS_URLS = [
    "https://www.fxstreet.com/rss/economic-calendar",
    "https://www.dailyfx.com/feeds/forex-economic-calendar",
]

CURRENCY_COUNTRY_MAP = {
    "USD": "US", "EUR": "EU", "GBP": "GB", "JPY": "JP",
    "AUD": "AU", "CAD": "CA", "CHF": "CH", "NZD": "NZ",
    "XAU": "US",
}

HIGH_IMPACT_KEYWORDS = [
    "non-farm", "nfp", "fomc", "federal reserve", "interest rate",
    "cpi", "inflation", "gdp", "unemployment", "retail sales",
    "ecb", "boe", "rba", "boc", "rbnz", "pmi", "ism",
    "trade balance", "current account", "monetary policy"
]


def _utcnow() -> datetime:
    """Current UTC time (one place to patch in tests)."""
    return datetime.now(timezone.utc)


class NewsAgent:

    def __init__(self, cache_path: Optional[Path] = None):
        self.cache_path      = Path(cache_path) if cache_path else CACHE_PATH
        self.calendar_cache: list               = []     # last good calendar (also on disk)
        self.last_fetch:     Optional[datetime] = None   # when calendar_cache was fetched
        self.source_used:    str                = "none"
        self._next_attempt:  Optional[datetime] = None   # no source is tried before this time
        self._last_round_ok: bool               = True   # did the latest fetch round succeed?
        self._backoff_until: dict               = {}     # url -> time it may be requested again (HTTP 429)
        self._disk_loaded:   bool               = False
        self._state:         Optional[str]      = None   # "live" | "cached" | "lost", for change-only logging

    # ── Sources ────────────────────────────────────────────────────────────────

    def _http_get(self, url: str, now: datetime) -> Optional[requests.Response]:
        """GET a calendar URL; None on any failure. HTTP 429 puts the URL on back-off (no hammering)."""
        until = self._backoff_until.get(url)
        if until and now < until:
            return None
        try:
            r = requests.get(url, headers=HEADERS, timeout=8)
        except requests.RequestException as e:
            logger.debug(f"Calendar request failed ({url}): {e}")
            return None
        if r.status_code == 429:
            wait = NEWS["rate_limit_backoff_mins"]
            try:
                wait = max(wait, float(r.headers.get("Retry-After", 0)) / 60)
            except (TypeError, ValueError, AttributeError):
                pass
            self._backoff_until[url] = now + timedelta(minutes=wait)
            logger.warning(f"Calendar source rate-limited (HTTP 429) - backing off {wait:.0f} min: {url}")
            return None
        if r.status_code != 200:
            logger.debug(f"Calendar request returned HTTP {r.status_code}: {url}")
            return None
        return r

    def _fetch_first(self, urls: list, parse: Callable, label: Callable, now: datetime) -> tuple:
        """First URL whose response parses to at least one event -> (events, source label); else ([], "")."""
        for url in urls:
            r = self._http_get(url, now)
            if r is None:
                continue
            try:
                events = parse(r)
            except Exception as e:
                logger.debug(f"Calendar parse failed ({url}): {e}")
                continue
            if events:
                return events, label(url)
        return [], ""

    def _fetch_forexfactory(self, now: datetime) -> tuple:
        return self._fetch_first(FF_URLS, lambda r: self._parse_ff_events(r.json()),
                                 lambda url: "ForexFactory", now)

    def _fetch_rss(self, now: datetime) -> tuple:
        return self._fetch_first(RSS_URLS, lambda r: self._parse_rss(r.text),
                                 lambda url: f"RSS ({url.split('/')[2]})", now)

    @staticmethod
    def _parse_ff_time(event: dict) -> Optional[datetime]:
        """Event time in UTC, or None for all-day / tentative events.

        The current feed gives ISO-8601 with a UTC offset in "date"; the older feed gave "date" + "time" (read as UTC).
        """
        date_str = event.get("date", "")
        if "T" in date_str:
            dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        time_str = event.get("time", "")
        if not date_str or time_str in ("", "All Day", "Tentative"):
            return None
        return datetime.strptime(f"{date_str} {time_str}", "%m-%d-%Y %I:%M%p").replace(tzinfo=timezone.utc)

    def _parse_ff_events(self, raw: list) -> list:
        parsed = []
        for event in raw:
            try:
                impact = event.get("impact", "").strip()
                if impact not in ("High", "Medium"):
                    continue
                dt = self._parse_ff_time(event)
                if dt is None:
                    continue

                parsed.append({
                    "title":    event.get("title", "Unknown"),
                    "country":  event.get("country", "").upper(),
                    "impact":   impact,
                    "time_utc": dt,
                    "forecast": event.get("forecast", ""),
                    "previous": event.get("previous", ""),
                })
            except Exception:
                continue

        return sorted(parsed, key=lambda x: x["time_utc"])

    def _parse_rss(self, xml_text: str) -> list:
        parsed = []
        try:
            root  = ET.fromstring(xml_text)
            items = root.findall(".//item")

            for item in items:
                try:
                    title    = item.findtext("title", "")
                    pub_date = item.findtext("pubDate", "")

                    if not title or not pub_date:
                        continue

                    is_high = any(kw in title.lower() for kw in HIGH_IMPACT_KEYWORDS)
                    if not is_high:
                        continue

                    dt = datetime.strptime(pub_date, "%a, %d %b %Y %H:%M:%S %z")
                    dt = dt.astimezone(timezone.utc)

                    country = "USD"
                    for ccy in ["EUR", "GBP", "JPY", "AUD", "CAD", "CHF", "NZD"]:
                        if ccy in title.upper():
                            country = ccy
                            break

                    parsed.append({
                        "title":    title,
                        "country":  country,
                        "impact":   "High",
                        "time_utc": dt,
                        "forecast": "",
                        "previous": "",
                    })
                except Exception:
                    continue

        except Exception as e:
            logger.debug(f"RSS parse error: {e}")

        return sorted(parsed, key=lambda x: x["time_utc"])

    # ── Cache (memory + disk) ──────────────────────────────────────────────────

    def _save_disk(self) -> None:
        """Write the calendar to the cache file (atomic replace). A failure is logged; memory still holds it."""
        data = {
            "fetched_at": self.last_fetch.isoformat(),
            "source":     self.source_used,
            "events":     [{**e, "time_utc": e["time_utc"].isoformat()} for e in self.calendar_cache],
        }
        tmp = self.cache_path.with_name(self.cache_path.name + ".tmp")
        try:
            tmp.write_text(json.dumps(data), encoding="utf-8")
            os.replace(tmp, self.cache_path)
        except OSError as e:
            logger.warning(f"Could not write the news cache {self.cache_path}: {e}")

    def _load_disk(self, now: datetime) -> None:
        """Load the cache file once, at the first use. A missing, unreadable or future-dated file is ignored."""
        self._disk_loaded = True
        try:
            data    = json.loads(self.cache_path.read_text(encoding="utf-8"))
            fetched = datetime.fromisoformat(data["fetched_at"])
            events  = [{**e, "time_utc": datetime.fromisoformat(e["time_utc"])} for e in data["events"]]
        except FileNotFoundError:
            return
        except (OSError, ValueError, KeyError, TypeError) as e:
            logger.warning(f"Ignoring unreadable news cache {self.cache_path}: {e}")
            return
        if fetched > now or not events:
            return
        self.calendar_cache = events
        self.last_fetch     = fetched
        self.source_used    = f"{data.get('source', 'unknown')} (disk cache)"
        self._next_attempt  = fetched + timedelta(minutes=NEWS["refresh_mins"])

    def _refresh_if_due(self, now: datetime) -> None:
        """Run a fetch round when due: good result -> memory + disk, hourly; failure -> retry after retry_mins."""
        if not self._disk_loaded:
            self._load_disk(now)
        if self._next_attempt and now < self._next_attempt:
            return

        events, source = self._fetch_round(now)
        self._last_round_ok = bool(events)
        if events:
            self.calendar_cache = events
            self.last_fetch     = now
            self.source_used    = source
            self._next_attempt  = now + timedelta(minutes=NEWS["refresh_mins"])
            self._save_disk()
        else:
            self._next_attempt  = now + timedelta(minutes=NEWS["retry_mins"])

    def _fetch_round(self, now: datetime) -> tuple:
        """Try the sources in order; the first one that returns events wins -> (events, source label)."""
        for fetch in (self._fetch_forexfactory, self._fetch_rss):
            events, source = fetch(now)
            if events:
                return events, source
        return [], ""

    def _usable(self, now: datetime) -> bool:
        """True while the held calendar is no older than NEWS["cache_max_age_hours"]."""
        return bool(self.calendar_cache and self.last_fetch and
                    now - self.last_fetch <= timedelta(hours=NEWS["cache_max_age_hours"]))

    def _log_state_change(self, now: datetime, usable: bool) -> None:
        """One log line when the calendar goes live / cached / lost, never one per cycle."""
        state = "lost" if not usable else ("live" if self._last_round_ok else "cached")
        if state == self._state:
            return
        previous, self._state = self._state, state
        if state == "live":
            msg = f"Calendar: {len(self.calendar_cache)} events from {self.source_used}"
            if previous is None:
                logger.info(msg)
            else:
                logger.warning(f"NEWS CALENDAR RESTORED - {msg}")
        elif state == "cached":
            age = (now - self.last_fetch).total_seconds() / 3600
            logger.warning(f"NEWS CALENDAR DEGRADED - all sources failed, using the cached calendar "
                           f"({self.source_used}, {age:.1f} h old, trusted up to {NEWS['cache_max_age_hours']} h)")
        else:
            effect = ("new entries are BLOCKED (NEWS fail_closed)" if NEWS["fail_closed"]
                      else "news filter is OFF (NEWS fail_closed=False)")
            logger.warning(f"NEWS CALENDAR LOST - all sources failed and no cached calendar newer than "
                           f"{NEWS['cache_max_age_hours']} h; {effect}")

    # ── Decision ───────────────────────────────────────────────────────────────

    def _calendar(self, now: datetime) -> Optional[list]:
        """The usable calendar (refreshed first when due), or None when there is none."""
        self._refresh_if_due(now)
        usable = self._usable(now)
        self._log_state_change(now, usable)
        return self.calendar_cache if usable else None

    def fetch_calendar(self) -> list:
        """The usable calendar events, or [] when there is none."""
        return self._calendar(_utcnow()) or []

    def is_news_window(self, symbol: Optional[str] = None) -> dict:
        now    = _utcnow()
        events = self._calendar(now)
        if events is None:
            if NEWS["fail_closed"]:
                return {"is_blocked": True, "reason": "Calendar unavailable - new entries blocked (fail closed)"}
            return {"is_blocked": False, "reason": "Calendar unavailable - news filter off (fail_closed=False)"}

        before = timedelta(minutes=NEWS["no_trade_before_mins"])
        after  = timedelta(minutes=NEWS["no_trade_after_mins"])

        pair_currencies = set()
        if symbol and len(symbol) >= 6:
            base  = symbol[:3].upper()
            quote = symbol[3:6].upper()
            pair_currencies = {
                CURRENCY_COUNTRY_MAP.get(base, base),
                CURRENCY_COUNTRY_MAP.get(quote, quote),
                base, quote
            }

        for event in events:
            if event["impact"] not in NEWS["impact_filter"]:
                continue

            if pair_currencies and event["country"] not in pair_currencies:
                continue

            window_start = event["time_utc"] - before
            window_end   = event["time_utc"] + after

            if window_start <= now <= window_end:
                mins_to = (event["time_utc"] - now).total_seconds() / 60
                return {
                    "is_blocked":    True,
                    "event":         event["title"],
                    "impact":        event["impact"],
                    "event_time":    event["time_utc"].strftime("%H:%M UTC"),
                    "mins_to_event": round(mins_to, 1),
                    "reason":        f"{event['impact']} news: {event['title']} at {event['time_utc'].strftime('%H:%M UTC')}",
                }

        return {"is_blocked": False, "reason": "No high-impact news in window"}

    def get_upcoming_events(self, hours_ahead: int = 4) -> list:
        events  = self.fetch_calendar()
        now     = _utcnow()
        cutoff  = now + timedelta(hours=hours_ahead)
        impacts = set(NEWS["impact_filter"])
        return [e for e in events if e["impact"] in impacts and now < e["time_utc"] <= cutoff]

    def summary(self, symbol: Optional[str] = None) -> dict:
        window   = self.is_news_window(symbol)
        upcoming = self.get_upcoming_events()
        return {
            "news_window":    window,
            "upcoming_count": len(upcoming),
            "upcoming":       upcoming[:3],
            "source":         self.source_used,
        }


news_agent = NewsAgent()
