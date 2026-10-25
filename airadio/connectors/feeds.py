"""Outside data feeds. Each connector returns PLAIN FACTS (numbers, names, headlines) — never
finished sentences. Only the brain writes words (Part 0).

To plug in a station's own source later, write a class with the same `get()` method and
return it from `get_feeds()`.
"""
import logging
import re
import threading
import time

import requests

from .. import config

log = logging.getLogger("airadio.feeds")

AED_PER_USD = 3.6725        # UAE dirham is pegged to the US dollar
GRAMS_PER_TROY_OUNCE = 31.1035


class FeedError(RuntimeError):
    pass


class _Cached:
    """Small time-based cache so WhatsApp commands and renders don't hammer the APIs."""

    def __init__(self):
        self._lock = threading.Lock()
        self._ts = 0.0
        self._val = None

    def get(self) -> dict:
        with self._lock:
            if self._val is not None and time.time() - self._ts < config.FEED_CACHE_SEC:
                return self._val
        try:
            val = self.fetch()
        except FeedError as first:
            if "Unauthorized" in str(first) or "not set" in str(first):
                raise  # a bad key won't fix itself
            time.sleep(1.0)
            log.info("%s feed retry after: %s", getattr(self, "name", "feed"), first)
            val = self.fetch()
        with self._lock:
            self._val, self._ts = val, time.time()
        return val

    def fetch(self) -> dict:
        raise NotImplementedError


class OpenMeteoWeather(_Cached):
    name = "weather"
    URL = "https://api.open-meteo.com/v1/forecast"

    def fetch(self) -> dict:
        params = {
            "latitude": config.WEATHER_LAT, "longitude": config.WEATHER_LON,
            "current": "temperature_2m,precipitation,rain",
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max",
            "forecast_days": 2, "timezone": "auto",
        }
        try:
            data = requests.get(self.URL, params=params, timeout=config.FEED_TIMEOUT_SEC).json()
            cur, daily = data["current"], data.get("daily", {})
            highs, lows = daily.get("temperature_2m_max", []), daily.get("temperature_2m_min", [])
            rain_prob = daily.get("precipitation_probability_max", [])
            return {
                "city": "Dubai",
                "temp_c": cur.get("temperature_2m"),
                "raining": bool(cur.get("rain") or cur.get("precipitation")),
                "today_max_c": highs[0] if highs else None,
                "today_min_c": lows[0] if lows else None,
                "tomorrow_max_c": highs[1] if len(highs) > 1 else None,
                "tomorrow_min_c": lows[1] if len(lows) > 1 else None,
                "tomorrow_rain_chance_pct": rain_prob[1] if len(rain_prob) > 1 else None,
            }
        except Exception as e:
            raise FeedError(f"weather: {e}") from e


class NewsAPIHeadlines(_Cached):
    name = "news"
    URL = "https://newsapi.org/v2/top-headlines"

    def fetch(self) -> dict:
        if not config.NEWS_API_KEY:
            raise FeedError("news: NEWS_API_KEY not set")
        try:
            data = requests.get(self.URL, params={"language": "en", "pageSize": 10, "apiKey": config.NEWS_API_KEY},
                                timeout=config.FEED_TIMEOUT_SEC).json()
            if data.get("status") != "ok":
                raise FeedError(f"news: {data.get('message', data.get('status'))}")
            titles = []
            for a in data.get("articles", []):
                t = (a.get("title") or "").strip()
                t = re.sub(r"\s+-\s+[^-]+$", "", t)  # drop " - Source Name" suffix
                if t and t not in titles:
                    titles.append(t)
            if not titles:
                raise FeedError("news: no headlines")
            return {"headlines": titles[:8]}
        except FeedError:
            raise
        except Exception as e:
            raise FeedError(f"news: {e}") from e


_INCIDENT_KIND_TA = {1: "accident", 6: "traffic jam", 7: "lane closure", 8: "road closed", 9: "road works"}


class TomTomTraffic(_Cached):
    name = "traffic"
    URL = "https://api.tomtom.com/traffic/services/5/incidentDetails"

    @staticmethod
    def _score(p: dict) -> float:
        return (p.get("delay") or 0) / 60 * 1.5 + int(p.get("magnitudeOfDelay") or 0) * 5

    def fetch(self) -> dict:
        if not config.TOMTOM_API_KEY:
            raise FeedError("traffic: TOMTOM_API_KEY not set")
        params = {
            "bbox": config.TRAFFIC_BBOX,
            "fields": "{incidents{properties{delay,magnitudeOfDelay,iconCategory,from,to,roadNumbers}}}",
            "language": "en-GB", "timeValidityFilter": "present", "key": config.TOMTOM_API_KEY,
        }
        try:
            data = requests.get(self.URL, params=params, timeout=config.FEED_TIMEOUT_SEC).json()
            if "incidents" not in data:
                raise FeedError(f"traffic: unexpected reply {str(data)[:120]}")
            props = sorted((i.get("properties") or {} for i in data["incidents"]), key=self._score, reverse=True)
            incidents = []
            for p in props[:3]:
                road = (p.get("roadNumbers") or [None])[0]
                frm = (p.get("from") or "").strip()
                where = f"{road} near {frm}" if road and frm else (road or frm or "Dubai")
                delay = p.get("delay") or 0
                incidents.append({
                    "where": re.sub(r"[^\x00-\x7F]+", "", where).strip(),
                    "kind": _INCIDENT_KIND_TA.get(p.get("iconCategory"), "incident"),
                    "delay_min": max(1, round(delay / 60)) if delay else None,
                })
            return {"incidents": incidents}
        except FeedError:
            raise
        except Exception as e:
            raise FeedError(f"traffic: {e}") from e


class MetalPrice(_Cached):
    name = "metals"
    URL = "https://api.metalpriceapi.com/v1/latest"

    def fetch(self) -> dict:
        if not config.METALPRICE_API_KEY:
            raise FeedError("metals: METALPRICE_API_KEY not set")
        try:
            data = requests.get(self.URL, params={"api_key": config.METALPRICE_API_KEY, "base": "USD",
                                                  "currencies": "XAU,XAG"},
                                timeout=config.FEED_TIMEOUT_SEC).json()
            rates = data.get("rates") or {}
            # Rates are ounces per 1 USD; invert to get USD per ounce.
            gold = 1 / rates["XAU"] if rates.get("XAU") else None
            silver = 1 / rates["XAG"] if rates.get("XAG") else None
            if not gold:
                raise FeedError(f"metals: no gold rate {str(data)[:120]}")
            per_gram = lambda usd_oz: round(usd_oz * AED_PER_USD / GRAMS_PER_TROY_OUNCE, 2) if usd_oz else None
            return {
                "gold_usd_per_ounce": round(gold, 2),
                "silver_usd_per_ounce": round(silver, 2) if silver else None,
                "gold_aed_per_gram_24k": per_gram(gold),
                "silver_aed_per_gram": per_gram(silver),
            }
        except FeedError:
            raise
        except Exception as e:
            raise FeedError(f"metals: {e}") from e


_FEEDS: dict | None = None


def get_feeds() -> dict:
    """Feed registry. Swap a default for the station's own source here."""
    global _FEEDS
    if _FEEDS is None:
        _FEEDS = {"weather": OpenMeteoWeather(), "news": NewsAPIHeadlines(),
                  "traffic": TomTomTraffic(), "metals": MetalPrice()}
    return _FEEDS
