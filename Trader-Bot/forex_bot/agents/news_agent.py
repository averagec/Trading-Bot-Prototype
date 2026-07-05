"""
News Agent
Primary source: MT5 built-in economic calendar (already connected to broker)
Fallback 1:    ForexFactory JSON
Fallback 2:    FXStreet RSS feed
Fallback 3:    Fail gracefully - never crash the bot
"""

import logging
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from typing import Optional

from config import NEWS

logger = logging.getLogger(__name__)

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


class NewsAgent:

    def __init__(self):
        self.calendar_cache: list            = []
        self.last_fetch:     Optional[datetime] = None
        self.cache_ttl_mins: int             = 240
        self.fetch_failed:   bool            = False
        self.source_used:    str             = "none"

    def fetch_calendar(self) -> list:
        now = datetime.now(timezone.utc)

        if (self.last_fetch and self.calendar_cache and
                (now - self.last_fetch).total_seconds() < self.cache_ttl_mins * 60):
            return self.calendar_cache

        events = (
            self._fetch_mt5_calendar() or
            self._fetch_forexfactory() or
            self._fetch_rss() or
            []
        )

        if events:
            self.calendar_cache = events
            self.last_fetch     = now
            self.fetch_failed   = False
            logger.info(f"Calendar: {len(events)} events from {self.source_used}")
        else:
            self.last_fetch   = now
            self.fetch_failed = True
            logger.warning("All calendar sources failed - news blocking disabled")

        return self.calendar_cache

    def _fetch_mt5_calendar(self) -> list:
        try:
            import MetaTrader5 as mt5

            if not mt5.initialize():
                return []

            now       = datetime.now(timezone.utc)
            date_from = now - timedelta(hours=1)
            date_to   = now + timedelta(days=7)

            values = mt5.calendar_value_get(date_from, date_to)
            mt5.shutdown()

            if values is None or len(values) == 0:
                return []

            parsed = []
            for val in values:
                try:
                    event_time = datetime.fromtimestamp(val.time, tz=timezone.utc)
                    impact     = self._map_mt5_impact(val.impact_type)

                    if impact not in ("High", "Medium"):
                        continue

                    parsed.append({
                        "title":    val.event_name if hasattr(val, "event_name") else f"Event {val.event_id}",
                        "country":  self._mt5_country_to_ccy(val.country_id if hasattr(val, "country_id") else 0),
                        "impact":   impact,
                        "time_utc": event_time,
                        "forecast": str(val.forecast) if hasattr(val, "forecast") else "",
                        "previous": str(val.prev_value) if hasattr(val, "prev_value") else "",
                    })
                except Exception:
                    continue

            if parsed:
                self.source_used = "MT5"
                return sorted(parsed, key=lambda x: x["time_utc"])

        except Exception as e:
            logger.debug(f"MT5 calendar failed: {e}")

        return []

    def _map_mt5_impact(self, impact_type: int) -> str:
        return {1: "Low", 2: "Medium", 3: "High"}.get(impact_type, "Low")

    def _mt5_country_to_ccy(self, country_id: int) -> str:
        id_map = {
            1: "USD", 2: "EUR", 3: "GBP", 4: "JPY",
            5: "AUD", 6: "CAD", 7: "CHF", 8: "NZD",
        }
        return id_map.get(country_id, "USD")

    def _fetch_forexfactory(self) -> list:
        urls = [
            "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
            "https://cdn-nfs.faireconomy.media/ff_calendar_thisweek.json",
        ]
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

        for url in urls:
            try:
                r = requests.get(url, headers=headers, timeout=8)
                if r.status_code == 200:
                    parsed = self._parse_ff_events(r.json())
                    if parsed:
                        self.source_used = "ForexFactory"
                        return parsed
            except Exception as e:
                logger.debug(f"FF failed: {e}")
        return []

    def _parse_ff_events(self, raw: list) -> list:
        parsed = []
        for event in raw:
            try:
                date_str = event.get("date", "")
                time_str = event.get("time", "")
                impact   = event.get("impact", "").strip()

                if not date_str or time_str in ("", "All Day", "Tentative"):
                    continue
                if impact not in ("High", "Medium"):
                    continue

                dt = datetime.strptime(f"{date_str} {time_str}", "%m-%d-%Y %I:%M%p")
                dt = dt.replace(tzinfo=timezone.utc)

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

    def _fetch_rss(self) -> list:
        rss_urls = [
            "https://www.fxstreet.com/rss/economic-calendar",
            "https://www.dailyfx.com/feeds/forex-economic-calendar",
        ]
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

        for url in rss_urls:
            try:
                r = requests.get(url, headers=headers, timeout=8)
                if r.status_code == 200:
                    parsed = self._parse_rss(r.text)
                    if parsed:
                        self.source_used = f"RSS ({url.split('/')[2]})"
                        return parsed
            except Exception as e:
                logger.debug(f"RSS failed: {e}")
        return []

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

    def is_news_window(self, symbol: Optional[str] = None) -> dict:
        if self.fetch_failed and not self.calendar_cache:
            return {"is_blocked": False, "reason": "Calendar unavailable - proceeding"}

        events = self.fetch_calendar()
        now    = datetime.now(timezone.utc)
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
        now     = datetime.now(timezone.utc)
        cutoff  = now + timedelta(hours=hours_ahead)
        impacts = set(NEWS["impact_filter"])
        return [e for e in events if e["impact"] in impacts and now < e["time_utc"] <= cutoff]

    def get_pair_news_bias(self, symbol: str) -> dict:
        return {"bias": "NEUTRAL", "reason": "Sentiment analysis requires LLM"}

    def summary(self, symbol: Optional[str] = None) -> dict:
        window   = self.is_news_window(symbol)
        upcoming = self.get_upcoming_events()
        return {
            "news_window":    window,
            "upcoming_count": len(upcoming),
            "upcoming":       upcoming[:3],
            "pair_bias":      self.get_pair_news_bias(symbol) if symbol else {},
            "source":         self.source_used,
        }


news_agent = NewsAgent()