"""Intent detector + formatter for real-time information answers."""

from __future__ import annotations

import logging
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from .config import InfoConfig
from .info_sources import InfoSources, NewsHeadline, TeamSnapshot

STOP_WORDS = {
    "what",
    "whats",
    "is",
    "when",
    "are",
    "the",
    "a",
    "an",
    "next",
    "game",
    "games",
    "news",
    "headlines",
    "with",
    "about",
    "in",
    "on",
    "today",
    "tonight",
    "this",
    "week",
    "latest",
    "play",
    "playing",
    "do",
    "does",
    "happening",
    "happens",
    "up",
    "for",
    "forecast",
    "weather",
    "sports",
}


LEAGUE_ALIASES = {
    "nfl": "nfl",
    "football": "nfl",
    "college football": "college-football",
    "ncaa football": "college-football",
    "college": "college-football",
    "nba": "nba",
    "basketball": "nba",
    "wnba": "wnba",
    "mlb": "mlb",
    "baseball": "mlb",
    "nhl": "nhl",
    "hockey": "nhl",
    "nascar": "nascar",
    "f1": "f1",
    "formula 1": "f1",
    "march madness": "ncaam",
    "ncaa basketball": "ncaam",
    "college basketball": "ncaam",
}


WEATHER_KEYWORDS = {"weather", "forecast", "temperature", "rain", "snow"}
NEWS_KEYWORDS = {"news", "headlines", "headline", "top stories"}
SPORTS_KEYWORDS = {
    "sports",
    "score",
    "scores",
    "headlines",
    "standings",
    "schedule",
    "game",
    "games",
    "season",
}


@dataclass(slots=True)
class InfoResponse:
    category: str
    text: str
    display: str | None = None


class InfoService:
    def __init__(
        self,
        config: InfoConfig,
        logger: logging.Logger | None = None,
        sources: InfoSources | None = None,
    ) -> None:
        self.config = config
        self.sources = sources or InfoSources(config)
        self.logger = logger or logging.getLogger(__name__)

    async def maybe_answer(self, transcript: str) -> InfoResponse | None:
        normalized = transcript.strip()
        if not normalized:
            return None
        simple = _normalize_text(normalized)

        if self._is_weather(simple):
            result = await self._handle_weather()
            if result:
                spoken, display = result
                return InfoResponse("weather", spoken, display)

        if self._is_news(simple):
            topic = self._extract_news_topic(simple)
            result = await self._handle_news(topic)
            if result:
                spoken, display = result
                return InfoResponse("news", spoken, display)

        sport_intent = await self._handle_sports(simple)
        if sport_intent:
            spoken, display = sport_intent
            return InfoResponse("sports", spoken, display)
        return None

    def _is_weather(self, text: str) -> bool:
        return any(keyword in text for keyword in WEATHER_KEYWORDS)

    def _is_news(self, text: str) -> bool:
        return any(keyword in text for keyword in NEWS_KEYWORDS)

    def _extract_news_topic(self, text: str) -> str | None:
        for alias in ("sports", "business", "technology", "politics", "world"):
            if f"{alias} news" in text or alias in text.split():
                return alias
        return None

    async def _handle_weather(self) -> tuple[str, str] | None:
        forecast = await self.sources.weather.forecast()
        if not forecast or not forecast.days:
            return None
        units = self.config.weather.units
        unit_label = "°F" if units in {"imperial", "auto"} else "°C"
        phrases: list[str] = []
        for idx, day in enumerate(forecast.days[: self.config.weather.forecast_days]):
            label = _describe_day(day.date, idx)
            high = _format_temp(day.temp_high)
            low = _format_temp(day.temp_low)
            rain = f"{int(day.precipitation_chance)}% chance of precip" if day.precipitation_chance is not None else ""
            if high and low:
                sentence = f"{label} tops out near {high}{unit_label} with lows around {low}{unit_label}"
            elif high:
                sentence = f"{label} reaches roughly {high}{unit_label}"
            elif low:
                sentence = f"{label} dips to about {low}{unit_label}"
            else:
                continue
            if rain:
                sentence = f"{sentence} and a {rain}."
            else:
                sentence = f"{sentence}."
            phrases.append(sentence)
        if not phrases:
            return None
        location_name = forecast.location_name
        intro = f"In {location_name}, " if location_name else ""
        spoken = intro + " ".join(phrases)
        display_parts: list[str] = []
        if location_name:
            display_parts.append(f"In {location_name}")
        display_parts.extend(phrases)
        display = "\n\n".join(display_parts)
        return spoken, display

    async def _handle_news(self, topic: str | None) -> tuple[str, str] | None:
        headlines = await self.sources.news.latest(topic)
        if not headlines:
            return None
        snippets = [_summarize_headline(item) for item in headlines[: self.config.news.max_articles]]
        snippets = [s for s in snippets if s]
        if not snippets:
            return None
        if topic:
            intro = f"Here are the latest {topic} headlines: "
        else:
            intro = "Here are the latest headlines: "
        spoken = intro + " ".join(snippets)
        display = "\n\n".join(f"• {snippet}" for snippet in snippets)
        return spoken, display

    async def _handle_sports(self, text: str) -> tuple[str, str] | None:
        league = _extract_league(text)
        wants_standings = "standing" in text or "standings" in text
        wants_headlines = "headline" in text or "news" in text or "happening" in text
        wants_next_game = "next" in text and "game" in text
        wants_schedule = "schedule" in text or "play next" in text

        if league and wants_standings:
            standings = await self.sources.sports.league_standings(league, limit=5)
            if standings:
                lines = [f"{item['name']} ({item.get('record') or 'record pending'})" for item in standings[:5]]
                spoken = f"In {league.upper()}, the top teams are: {', '.join(lines)}."
                display_lines = "\n".join(f"• {line}" for line in lines)
                display = f"{league.upper()} standings:\n{display_lines}"
                return spoken, display

        if league and wants_headlines:
            headlines = await self.sources.sports.league_headlines(league)
            if headlines:
                return _summarize_sports_headlines(headlines, league)

        if "sports" in text or (league and not wants_headlines and not wants_standings):
            league_list = [league] if league else None
            snapshot = await self._find_team_snapshot(text, league_list)
            if snapshot:
                return self._format_team_snapshot(snapshot, wants_next_game or wants_schedule)

        if not league and ("sports" in text or "happening" in text or "headlines" in text):
            headlines = await self.sources.sports.general_headlines()
            if headlines:
                return _summarize_sports_headlines(headlines, None)

        snapshot = await self._find_team_snapshot(text, None)
        if snapshot:
            return self._format_team_snapshot(snapshot, wants_next_game or wants_schedule)
        return None

    async def _find_team_snapshot(self, text: str, leagues: Sequence[str] | None) -> TeamSnapshot | None:
        words = [word for word in re.split(r"\s+", text) if word and word not in STOP_WORDS]
        max_length = min(3, len(words))
        for size in range(max_length, 0, -1):
            for idx in range(len(words) - size + 1):
                phrase = " ".join(words[idx : idx + size])
                if len(phrase) < 3:
                    continue
                snapshot = await self.sources.sports.team_snapshot(phrase, leagues=leagues)
                if snapshot:
                    return snapshot
        return None

    def _format_team_snapshot(self, snapshot: TeamSnapshot, emphasize_next: bool) -> tuple[str, str] | None:
        parts: list[str] = []
        record = snapshot.record or "record unavailable"
        parts.append(f"The {snapshot.name} are {record}.")

        if snapshot.next_event:
            next_text = _describe_event("Next up", snapshot.next_event)
            if next_text:
                parts.append(next_text)
        elif emphasize_next:
            parts.append("I couldn't find the next matchup yet.")

        if snapshot.previous_event and not emphasize_next:
            prev_text = _describe_event("Last game", snapshot.previous_event)
            if prev_text:
                parts.append(prev_text)
        spoken = " ".join(parts)
        display = "\n\n".join(parts)
        return spoken, display


def _normalize_text(value: str) -> str:
    value = value.lower()
    value = re.sub(r"[^\w\s+\.]", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def _describe_day(date_str: str, idx: int) -> str:
    try:
        date_obj = datetime.fromisoformat(date_str)
        day_name = date_obj.strftime("%A")
    except ValueError:
        day_name = date_str
    if idx == 0:
        return "Today"
    if idx == 1:
        return "Tomorrow"
    return day_name


def _format_temp(value: float | None) -> str | None:
    if value is None or math.isnan(value):
        return None
    return f"{round(value):d}"


def _summarize_headline(headline: NewsHeadline) -> str:
    if headline.description:
        return f"{headline.title}: {headline.description}"
    if headline.source:
        return f"{headline.title} ({headline.source})"
    return headline.title


def _summarize_sports_headlines(headlines, league: str | None) -> tuple[str, str]:
    spoken_parts = []
    prefix = f"In {league.upper()}, " if league else ""
    display_lines: list[str] = []
    for idx, item in enumerate(headlines[:3]):
        if idx == 0:
            spoken_parts.append(f"{prefix}{item.headline}")
        else:
            spoken_parts.append(item.headline)
        display_lines.append(f"• {item.headline}")
    return " ".join(spoken_parts), "\n".join(display_lines)


def _extract_league(text: str) -> str | None:
    for alias, league in LEAGUE_ALIASES.items():
        if alias in text:
            return league
    return None


def _describe_event(prefix: str, event: dict) -> str | None:
    matchup = event.get("matchup") or event.get("name") or event.get("shortName")
    date_text = _friendly_date(event.get("date"))
    status = event.get("status")
    pieces = [prefix]
    if matchup:
        pieces.append(matchup)
    if date_text:
        pieces.append(f"on {date_text}")
    if status:
        pieces.append(f"({status})")
    sentence = " ".join(pieces).strip()
    if not sentence.endswith("."):
        sentence = f"{sentence}."
    return sentence


def _friendly_date(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError:
        return value
    return dt.astimezone().strftime("%A at %-I:%M %p")
