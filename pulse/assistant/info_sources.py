"""External information sources for news, weather, and sports."""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

import httpx

try:
    from openlocationcode import openlocationcode as olc  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover - optional dependency
    olc = None

from .config import InfoConfig, NewsConfig, SportsConfig, WeatherConfig

LOGGER = logging.getLogger("pulse.info_sources")

LAT_LON_PATTERN = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$")
WHAT3WORDS_PATTERN = re.compile(r"^[a-z]+(?:\.[a-z]+){2}$")
POSTAL_CODE_PATTERN = re.compile(r"^\s*(\d{5})(?:-\d{4})?\s*$")
STATE_CODE_PATTERN = re.compile(r"^[A-Za-z]{2}$")

LEAGUE_PATHS: dict[str, tuple[str, str]] = {
    "nfl": ("football", "nfl"),
    "college-football": ("football", "college-football"),
    "ncaaf": ("football", "college-football"),
    "nba": ("basketball", "nba"),
    "wnba": ("basketball", "wnba"),
    "mlb": ("baseball", "mlb"),
    "nhl": ("hockey", "nhl"),
    "ncaam": ("basketball", "mens-college-basketball"),
    "ncaaw": ("basketball", "womens-college-basketball"),
    "nascar": ("racing", "nascar"),
    "f1": ("racing", "f1"),
    "soccer": ("soccer", "usa.1"),
}


class TTLCache:
    """Simple in-memory cache with per-key TTL."""

    def __init__(self, ttl_seconds: int = 300) -> None:
        self.ttl = ttl_seconds
        self._values: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> Any | None:
        now = time.monotonic()
        entry = self._values.get(key)
        if not entry:
            return None
        expires, value = entry
        if expires < now:
            self._values.pop(key, None)
            return None
        return value

    def set(self, key: str, value: Any) -> None:
        self._values[key] = (time.monotonic() + self.ttl, value)


async def _get_json(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> dict | None:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url, params=params, headers=headers)
            response.raise_for_status()
            return response.json()
    except httpx.HTTPError:
        return None


@dataclass(slots=True)
class NewsHeadline:
    title: str
    description: str | None
    source: str | None


class NewsClient:
    def __init__(self, config: NewsConfig) -> None:
        self.config = config
        self._cache = TTLCache(ttl_seconds=300)

    async def latest(self, topic: str | None = None) -> list[NewsHeadline]:
        if not self.config.api_key:
            return []
        cache_key = topic or "__default__"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        params = {
            "country": self.config.country,
            "category": topic or self.config.category,
            "language": self.config.language,
            "pageSize": self.config.max_articles,
        }
        headers = {"X-Api-Key": self.config.api_key}
        payload = await _get_json(f"{self.config.base_url}/top-headlines", params=params, headers=headers)
        results: list[NewsHeadline] = []
        if payload:
            for article in (payload.get("articles") or [])[: self.config.max_articles]:
                title = (article.get("title") or "").strip()
                if not title:
                    continue
                results.append(
                    NewsHeadline(
                        title=title,
                        description=(article.get("description") or "").strip() or None,
                        source=(article.get("source") or {}).get("name"),
                    )
                )
        self._cache.set(cache_key, results)
        return results


@dataclass(slots=True)
class WeatherDay:
    date: str
    temp_high: float | None
    temp_low: float | None
    precipitation_chance: float | None
    weather_code: int | None


@dataclass(slots=True)
class WeatherCurrent:
    temperature: float | None
    weather_code: int | None
    windspeed: float | None
    time: str | None


@dataclass(slots=True)
class WeatherForecast:
    location_name: str
    latitude: float
    longitude: float
    days: list[WeatherDay]
    current: WeatherCurrent | None = None


class WeatherClient:
    def __init__(self, config: WeatherConfig, what3words_api_key: str | None) -> None:
        self.config = config
        self.what3words_api_key = what3words_api_key
        self._location_cache = TTLCache(ttl_seconds=3600)
        self._forecast_cache = TTLCache(ttl_seconds=600)

    async def forecast(self) -> WeatherForecast | None:
        if not self.config.location:
            return None
        location = await self._resolve_location(self.config.location)
        if not location:
            return None
        cache_key = (
            f"{location.latitude:.4f},{location.longitude:.4f}:"
            f"{self.config.units}:{self.config.language}:{self.config.forecast_days}"
        )
        cached = self._forecast_cache.get(cache_key)
        if cached:
            return cached
        params = {
            "latitude": location.latitude,
            "longitude": location.longitude,
            "daily": ["temperature_2m_max", "temperature_2m_min", "precipitation_probability_max", "weathercode"],
            "timezone": "auto",
            "forecast_days": self.config.forecast_days,
            "language": self.config.language,
            "current_weather": True,
        }
        if self.config.units in {"imperial", "auto"}:
            params["temperature_unit"] = "fahrenheit"
            params["windspeed_unit"] = "mph"
            params["precipitation_unit"] = "inch"
        elif self.config.units == "metric":
            params["temperature_unit"] = "celsius"
            params["windspeed_unit"] = "kmh"
            params["precipitation_unit"] = "mm"

        payload = await _get_json(self.config.base_url, params=params)
        if not payload:
            return None
        daily = payload.get("daily") or {}
        dates = daily.get("time") or []
        highs = daily.get("temperature_2m_max") or []
        lows = daily.get("temperature_2m_min") or []
        precip = daily.get("precipitation_probability_max") or []
        codes = daily.get("weathercode") or []
        days: list[WeatherDay] = []
        for idx, day in enumerate(dates[: self.config.forecast_days]):
            days.append(
                WeatherDay(
                    date=day,
                    temp_high=_safe_list_float(highs, idx),
                    temp_low=_safe_list_float(lows, idx),
                    precipitation_chance=_safe_list_float(precip, idx),
                    weather_code=int(codes[idx]) if idx < len(codes) and isinstance(codes[idx], (int, float)) else None,
                )
            )
        current_payload = payload.get("current_weather") or {}
        current = WeatherCurrent(
            temperature=float(current_payload["temperature"]) if "temperature" in current_payload else None,
            weather_code=int(current_payload["weathercode"]) if "weathercode" in current_payload else None,
            windspeed=float(current_payload["windspeed"]) if "windspeed" in current_payload else None,
            time=str(current_payload.get("time")) if current_payload.get("time") else None,
        )
        forecast = WeatherForecast(location.display_name, location.latitude, location.longitude, days, current=current)
        self._forecast_cache.set(cache_key, forecast)
        return forecast

    async def _resolve_location(self, raw: str):
        normalized = raw.strip()
        cached = self._location_cache.get(normalized)
        if cached:
            return cached
        match = LAT_LON_PATTERN.match(normalized)
        if match:
            lat = float(match.group(1))
            lon = float(match.group(2))
            result = _Location(latitude=lat, longitude=lon, display_name=f"{lat:.2f}, {lon:.2f}")
            self._location_cache.set(normalized, result)
            return result
        if WHAT3WORDS_PATTERN.match(normalized.lower()) and self.what3words_api_key:
            coords = await self._resolve_what3words(normalized)
            if coords:
                self._location_cache.set(normalized, coords)
                return coords
        postal_match = POSTAL_CODE_PATTERN.match(normalized)
        if postal_match:
            coords = await self._resolve_postal_code(postal_match.group(1))
            if coords:
                self._location_cache.set(normalized, coords)
                return coords
        if "+" in normalized and not normalized.startswith("http"):
            coords = _decode_plus_code(normalized)
            if coords:
                self._location_cache.set(normalized, coords)
                return coords
        geocoded = await self._geocode_text(normalized)
        if geocoded:
            self._location_cache.set(normalized, geocoded)
            return geocoded
        return None

    async def _geocode_text(self, query: str):
        for candidate in _expand_geocode_queries(query):
            payload = await _get_json(
                "https://geocoding-api.open-meteo.com/v1/search",
                params={"name": candidate, "count": 1, "language": self.config.language},
            )
            if not payload:
                continue
            results = payload.get("results") or []
            if not results:
                continue
            entry = results[0]
            return _Location(
                latitude=float(entry.get("latitude")),
                longitude=float(entry.get("longitude")),
                display_name=entry.get("name") or candidate,
            )
        return None

    async def _resolve_what3words(self, words: str):
        payload = await _get_json(
            "https://api.what3words.com/v3/convert-to-coordinates",
            params={"words": words, "key": self.what3words_api_key},
        )
        if not payload:
            return None
        coords = payload.get("coordinates") or {}
        if "lat" not in coords or "lng" not in coords:
            return None
        return _Location(
            latitude=float(coords["lat"]),
            longitude=float(coords["lng"]),
            display_name=words,
        )

    async def _resolve_postal_code(self, postal_code: str):
        payload = await _get_json(f"https://api.zippopotam.us/us/{postal_code}")
        if not payload:
            return None
        places = payload.get("places") or []
        if not places:
            return None
        place = places[0]
        try:
            lat = float(place["latitude"])
            lon = float(place["longitude"])
        except (KeyError, TypeError, ValueError):
            return None
        city = place.get("place name") or postal_code
        state = place.get("state abbreviation")
        display = f"{city}, {state}" if state else city
        return _Location(latitude=lat, longitude=lon, display_name=display)


@dataclass(slots=True)
class SportsHeadline:
    headline: str
    description: str | None
    league: str | None


@dataclass(slots=True)
class TeamSnapshot:
    name: str
    record: str | None
    next_event: dict[str, Any] | None
    previous_event: dict[str, Any] | None
    league: str


class SportsClient:
    def __init__(self, config: SportsConfig) -> None:
        self.config = config
        self._headline_cache = TTLCache(ttl_seconds=300)
        self._league_cache = TTLCache(ttl_seconds=300)
        self._team_cache = TTLCache(ttl_seconds=3600)

    async def general_headlines(self, limit: int = 5) -> list[SportsHeadline]:
        cache_key = f"general:{limit}:{self.config.headline_country}"
        cached = self._headline_cache.get(cache_key)
        if cached is not None:
            return cached
        url = f"{self.config.base_url}/site/v2/sports/news"
        payload = await _get_json(url, params={"region": self.config.headline_country})
        headlines = self._extract_headlines(payload, limit=limit, league=None)
        self._headline_cache.set(cache_key, headlines)
        return headlines

    async def league_headlines(self, league: str, limit: int = 5) -> list[SportsHeadline]:
        league = league.lower()
        cache_key = f"league:{league}:{limit}"
        cached = self._league_cache.get(cache_key)
        if cached is not None:
            return cached
        path = self._league_path(league)
        if not path:
            return []
        sport, league_slug = path
        url = f"{self.config.base_url}/site/v2/sports/{sport}/{league_slug}/news"
        payload = await _get_json(url)
        headlines = self._extract_headlines(payload, limit=limit, league=league)
        self._league_cache.set(cache_key, headlines)
        return headlines

    async def league_standings(self, league: str, limit: int = 5) -> list[dict[str, Any]]:
        league = league.lower()
        path = self._league_path(league)
        if not path:
            return []
        sport, league_slug = path
        url = f"{self.config.base_url}/site/v2/sports/{sport}/{league_slug}/standings"
        payload = await _get_json(url, params={"region": self.config.default_country})
        standings: list[dict[str, Any]] = []
        if not payload:
            return standings
        children = payload.get("children") or []
        for conference in children:
            groups = conference.get("standings", {}).get("entries") or conference.get("standings", {}).get("entries")
            if not groups:
                groups = conference.get("standings", {}).get("entries")
            entries = conference.get("standings", {}).get("entries")
            if entries:
                for entry in entries:
                    team = entry.get("team") or {}
                    standings.append(
                        {
                            "name": team.get("displayName") or team.get("name"),
                            "record": _extract_record(entry),
                            "conference": conference.get("name"),
                            "league": league,
                        }
                    )
            elif groups:
                for entry in groups:
                    team = entry.get("team") or {}
                    standings.append(
                        {
                            "name": team.get("displayName") or team.get("name"),
                            "record": _extract_record(entry),
                            "conference": conference.get("name"),
                            "league": league,
                        }
                    )
            if len(standings) >= limit:
                break
        return standings[:limit]

    async def team_snapshot(self, query: str, leagues: Sequence[str] | None = None) -> TeamSnapshot | None:
        leagues_to_search: Iterable[str] = leagues or self.config.default_leagues
        normalized_query = query.strip().lower()
        for league in leagues_to_search:
            teams = await self._league_teams(league)
            for team in teams:
                team_obj = team.get("team") or {}
                if not team_obj:
                    continue
                values = _team_name_tokens(team_obj)
                if normalized_query in values:
                    record = _team_record(team_obj)
                    next_event = self._pick_next_event(team_obj)
                    previous_event = self._pick_previous_event(team_obj)
                    return TeamSnapshot(
                        name=team_obj.get("displayName") or team_obj.get("name") or query,
                        record=record,
                        next_event=next_event,
                        previous_event=previous_event,
                        league=league,
                    )
        return None

    async def _league_teams(self, league: str):
        league = league.lower()
        cached = self._team_cache.get(league)
        if cached is not None:
            return cached
        path = self._league_path(league)
        if not path:
            return []
        sport, league_slug = path
        url = f"{self.config.base_url}/site/v2/sports/{sport}/{league_slug}/teams"
        payload = await _get_json(url, params={"limit": 400})
        teams: list[dict[str, str]] = []
        if payload:
            sports_block = payload.get("sports") or []
            for sport_entry in sports_block:
                for league_entry in sport_entry.get("leagues") or []:
                    teams.extend(league_entry.get("teams") or [])
        self._team_cache.set(league, teams)
        return teams

    def _league_path(self, league: str) -> tuple[str, str] | None:
        league = league.lower()
        if league in LEAGUE_PATHS:
            return LEAGUE_PATHS[league]
        return None

    def _extract_headlines(self, payload: dict | None, *, limit: int, league: str | None) -> list[SportsHeadline]:
        results: list[SportsHeadline] = []
        if not payload:
            return results
        for item in payload.get("articles") or payload.get("results") or []:
            headline = (item.get("headline") or item.get("title") or "").strip()
            if not headline:
                continue
            results.append(
                SportsHeadline(
                    headline=headline,
                    description=(item.get("description") or "").strip() or None,
                    league=league or (item.get("categories") or [{}])[0].get("sport"),
                )
            )
            if len(results) >= limit:
                break
        return results

    @staticmethod
    def _pick_next_event(team: dict[str, Any]) -> dict[str, Any] | None:
        events = team.get("nextEvent") or []
        if events:
            return _simplify_event(events[0])
        schedule = team.get("events") or []
        for event in schedule:
            if event.get("status", {}).get("type", {}).get("completed"):
                continue
            return _simplify_event(event)
        return None

    @staticmethod
    def _pick_previous_event(team: dict[str, Any]) -> dict[str, Any] | None:
        events = team.get("previousEvent") or []
        if events:
            return _simplify_event(events[0])
        schedule = team.get("events") or []
        for event in reversed(schedule):
            if event.get("status", {}).get("type", {}).get("completed"):
                return _simplify_event(event)
        return None


@dataclass(slots=True)
class _Location:
    latitude: float
    longitude: float
    display_name: str


def _safe_list_float(values: Sequence[Any], idx: int) -> float | None:
    if idx >= len(values):
        return None
    value = values[idx]
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _expand_geocode_queries(query: str) -> list[str]:
    cleaned = query.strip()
    if not cleaned:
        return []
    candidates: list[str] = []
    seen: set[str] = set()

    def add(value: str) -> None:
        normalized = " ".join(value.split())
        if not normalized:
            return
        key = normalized.lower()
        if key in seen:
            return
        seen.add(key)
        candidates.append(normalized)

    add(cleaned)

    if "," in cleaned:
        parts = [part.strip() for part in cleaned.split(",") if part.strip()]
        if parts:
            city = parts[0]
            remainder = parts[1:]
            add(" ".join(parts))
            add(city)
            if remainder:
                state = remainder[0].split()[0]
                if STATE_CODE_PATTERN.match(state):
                    add(f"{city} {state}")
                    add(f"{city}, {state}")
                    add(f"{city}, {state} USA")
                    add(f"{city}, {state}, USA")
    add(cleaned.replace(",", " "))
    return candidates


def _decode_plus_code(code: str) -> _Location | None:
    if olc is None:
        LOGGER.debug("[info] Plus Code support unavailable (openlocationcode not installed)")
        return None
    try:
        decoded = olc.decode(code.strip().upper())
    except ValueError:
        return None
    lat = decoded.latitudeCenter
    lon = decoded.longitudeCenter
    if lat is None or lon is None:
        return None
    return _Location(latitude=lat, longitude=lon, display_name=code.upper())


def _extract_record(entry: dict[str, Any]) -> str | None:
    records = entry.get("stats") or entry.get("records") or []
    if isinstance(records, list):
        for stat in records:
            if stat.get("name") in {"overall", "vsOverall", "record"} and stat.get("displayValue"):
                return stat["displayValue"]
            if stat.get("displayName", "").lower() == "overall":
                return stat.get("displayValue")
    summary = entry.get("summary")
    if isinstance(summary, str) and summary:
        return summary
    return None


def _team_name_tokens(team: dict[str, Any]) -> set[str]:
    tokens: set[str] = set()
    for key in ("displayName", "nickname", "name", "shortDisplayName", "abbreviation"):
        value = team.get(key)
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized:
                tokens.add(normalized)
    slug = team.get("slug")
    if isinstance(slug, str):
        tokens.add(slug.strip().lower())
    return tokens


def _team_record(team: dict[str, Any]) -> str | None:
    record_info = team.get("record") or {}
    items = record_info.get("items") or []
    for item in items:
        if item.get("type") == "total" and item.get("summary"):
            return item["summary"]
    return record_info.get("overall", {}).get("summary")


def _simplify_event(event: dict[str, Any]) -> dict[str, Any]:
    competitors = event.get("competitions", [{}])[0].get("competitors") or []
    matchup = " vs ".join(comp.get("team", {}).get("shortDisplayName", "") for comp in competitors if comp.get("team"))
    status = event.get("status", {}).get("type", {})
    completed = bool(status.get("completed"))
    return {
        "name": event.get("name"),
        "shortName": event.get("shortName"),
        "date": event.get("date"),
        "status": status.get("description"),
        "completed": completed,
        "matchup": matchup or event.get("name"),
    }


class InfoSources:
    """Aggregates the individual data clients into a single facade."""

    def __init__(self, config: InfoConfig) -> None:
        self.config = config
        self.news = NewsClient(config.news)
        self.weather = WeatherClient(config.weather, config.what3words_api_key)
        self.sports = SportsClient(config.sports)
