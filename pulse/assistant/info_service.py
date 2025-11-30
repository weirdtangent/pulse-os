"""Intent detector + formatter for real-time information answers."""

from __future__ import annotations

import logging
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .config import InfoConfig
from .info_sources import InfoSources, NewsHeadline, TeamSnapshot, WeatherForecast
from .shopping_list import ShoppingListError, ShoppingListService, ShoppingListView

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
    card: dict[str, Any] | None = None


class InfoService:
    def __init__(
        self,
        config: InfoConfig,
        logger: logging.Logger | None = None,
        sources: InfoSources | None = None,
        shopping: ShoppingListService | None = None,
    ) -> None:
        self.config = config
        self.sources = sources or InfoSources(config)
        self.logger = logger or logging.getLogger(__name__)
        self.shopping = shopping

    async def maybe_answer(self, transcript: str) -> InfoResponse | None:
        normalized = transcript.strip()
        if not normalized:
            return None
        simple = _normalize_text(normalized)

        shopping_result = await self._maybe_handle_shopping(transcript)
        if shopping_result:
            return shopping_result

        if self._is_weather(simple):
            result = await self._handle_weather()
            if result:
                spoken, display, card = result
                return InfoResponse("weather", spoken, display, card)

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

    async def _handle_weather(self) -> tuple[str, str, dict[str, Any]] | None:
        forecast = await self.sources.weather.forecast()
        if not forecast or not forecast.days:
            return None
        units = self.config.weather.units
        display_label = "°F" if units in {"imperial", "auto"} else "°C"
        phrases: list[str] = []
        card_days: list[dict[str, Any]] = []
        current_sentence = _build_current_weather_phrase(forecast)
        if current_sentence:
            phrases.append(current_sentence)
        for idx, day in enumerate(forecast.days[: self.config.weather.forecast_days]):
            label = _describe_day(day.date, idx)
            high = _format_temp(day.temp_high)
            low = _format_temp(day.temp_low)
            rain = f"{int(day.precipitation_chance)}% chance of precip" if day.precipitation_chance is not None else ""
            if high and low:
                sentence = f"{label} tops out near {high}° with lows around {low}°"
            elif high:
                sentence = f"{label} reaches roughly {high}°"
            elif low:
                sentence = f"{label} dips to about {low}°"
            else:
                continue
            if rain:
                sentence = f"{sentence} and a {rain}."
            else:
                sentence = f"{sentence}."
            phrases.append(sentence)
            card_days.append(
                {
                    "label": label,
                    "high": high,
                    "low": low,
                    "precip": int(day.precipitation_chance) if day.precipitation_chance is not None else None,
                    "icon": _weather_icon_key(day.weather_code),
                }
            )
        if not phrases:
            return None
        location_name = forecast.location_name
        intro = f"In {location_name}, " if location_name else ""
        spoken = intro + " ".join(phrases)
        display_parts: list[str] = []
        if location_name:
            display_parts.append(f"In {location_name}")
        for idx, day in enumerate(forecast.days[: self.config.weather.forecast_days]):
            label = _describe_day(day.date, idx)
            high = _format_temp(day.temp_high)
            low = _format_temp(day.temp_low)
            rain = day.precipitation_chance
            line_parts = []
            if high is not None:
                line_parts.append(f"High {high}{display_label}")
            if low is not None:
                line_parts.append(f"Low {low}{display_label}")
            if rain is not None:
                line_parts.append(f"Precip {int(rain)}%")
            display_line = f"{label}: " + ", ".join(line_parts)
            display_parts.append(display_line)
        display = "\n\n".join(display_parts)
        title = location_name or "Weather"
        day_count = len(card_days)
        outlook_text = f"Next {day_count} day{'s' if day_count != 1 else ''}"
        current_block = _format_current_weather_entry(forecast, display_label)
        card_payload = {
            "type": "weather",
            "title": title,
            "subtitle": outlook_text,
            "units": display_label,
            "days": card_days,
            "current": current_block,
        }
        return spoken, display, card_payload

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

    async def _maybe_handle_shopping(self, transcript: str) -> InfoResponse | None:
        if not self.shopping or not self.shopping.enabled:
            return None
        command = self.shopping.parser.parse(transcript)
        if not command:
            return None
        try:
            if command.action == "add":
                result = await self.shopping.add_items(command.items)
                spoken = self._format_addition_response(result.added, result.reactivated, result.duplicates)
                return InfoResponse("shopping", spoken, spoken)
            if command.action == "remove":
                result = await self.shopping.remove_items(command.items)
                spoken = self._format_removal_response(result.removed, result.missing)
                return InfoResponse("shopping", spoken, spoken)
            if command.action == "clear":
                result = await self.shopping.clear()
                spoken = (
                    "Your shopping list is already empty." if result.cleared == 0 else "Cleared your shopping list."
                )
                return InfoResponse("shopping", spoken, spoken)
            if command.action == "show":
                view = await self.shopping.list_items()
                spoken, display, card = self._format_shopping_summary(view)
                return InfoResponse("shopping", spoken, display, card)
        except ShoppingListError as exc:
            self.logger.warning("shopping: unable to handle request: %s", exc)
            apology = "I couldn't reach your shopping list right now."
            return InfoResponse("shopping", apology, apology)
        return None

    def _format_addition_response(
        self,
        added: Sequence[str],
        reactivated: Sequence[str],
        duplicates: Sequence[str],
    ) -> str:
        parts: list[str] = []
        if added:
            parts.append(f"Added {self._format_nice_list(added)} to your shopping list.")
        if reactivated:
            parts.append(f"I unchecked {self._format_nice_list(reactivated)}.")
        if duplicates and not reactivated:
            verb = "is" if len(duplicates) == 1 else "are"
            parts.append(f"{self._format_nice_list(duplicates)} {verb} already on your shopping list.")
        if not parts:
            parts.append("I didn't catch any new items for your shopping list.")
        return " ".join(parts)

    def _format_removal_response(self, removed: Sequence[str], missing: Sequence[str]) -> str:
        parts: list[str] = []
        if removed:
            parts.append(f"Removed {self._format_nice_list(removed)}.")
        if missing:
            parts.append(f"I couldn't find {self._format_nice_list(missing)} on your shopping list.")
        if not parts:
            parts.append("I couldn't find those items on your shopping list.")
        return " ".join(parts)

    def _format_shopping_summary(self, view: ShoppingListView) -> tuple[str, str, dict[str, Any]]:
        if not view.items:
            card = self.shopping.build_card(view, subtitle="You're all caught up.")
            spoken = "Your shopping list is empty."
            return spoken, spoken, card
        unchecked = [entry.label for entry in view.items if not entry.checked]
        checked = [entry.label for entry in view.items if entry.checked]
        total = len(view.items)
        spoken_parts = [f"You have {total} item{'s' if total != 1 else ''} on your shopping list."]
        if unchecked:
            spoken_parts.append(f"You still need {self._format_nice_list(unchecked)}.")
        if checked:
            verb = "is" if len(checked) == 1 else "are"
            spoken_parts.append(f"{self._format_nice_list(checked)} {verb} already checked off.")
        display_lines = []
        for entry in view.items:
            prefix = "☑︎" if entry.checked else "•"
            display_lines.append(f"{prefix} {entry.label}")
        subtitle = f"{len(unchecked)} to buy" if unchecked else "All items checked off"
        card = self.shopping.build_card(view, subtitle=subtitle)
        return " ".join(spoken_parts), "\n".join(display_lines), card

    @staticmethod
    def _format_nice_list(items: Sequence[str]) -> str:
        cleaned = [item.strip() for item in items if item and item.strip()]
        if not cleaned:
            return ""
        if len(cleaned) == 1:
            return cleaned[0]
        return ", ".join(cleaned[:-1]) + f", and {cleaned[-1]}"

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


def _weather_icon_key(code: int | None) -> str:
    if code is None:
        return "cloudy"
    if code == 0:
        return "sunny"
    if code == 1:
        return "partly_cloudy"
    if code == 2:
        return "mostly_cloudy"
    if code == 3:
        return "cloudy"
    if code in {45, 48}:
        return "fog"
    if code in {51, 53, 55}:
        return "drizzle"
    if code in {56, 57, 66, 67}:
        return "sleet"
    if code in {61, 63, 80, 81}:
        return "rain"
    if code in {65, 82}:
        return "downpour"
    if code in {71, 73, 75, 77, 85, 86}:
        return "snow"
    if code in {95, 96, 99}:
        return "thunder"
    return "cloudy"


def _weather_description(code: int | None) -> str:
    mapping = {
        "sunny": "clear skies",
        "partly_cloudy": "partly cloudy",
        "mostly_cloudy": "mostly cloudy",
        "cloudy": "overcast",
        "fog": "foggy",
        "drizzle": "light drizzle",
        "rain": "rainy",
        "downpour": "heavy rain",
        "sleet": "sleet",
        "snow": "snowy",
        "thunder": "stormy",
    }
    icon = _weather_icon_key(code)
    return mapping.get(icon, "cloudy")


def _build_current_weather_phrase(forecast: WeatherForecast) -> str | None:
    current = forecast.current
    if not current or current.temperature is None:
        return None
    temp = _format_temp(current.temperature)
    description = _weather_description(current.weather_code)
    if not temp:
        return None
    return f"Right now it's around {temp} degrees and {description}."


def _format_current_weather_entry(forecast: WeatherForecast, units_label: str) -> dict[str, Any] | None:
    current = forecast.current
    if not current:
        return None
    temp = _format_temp(current.temperature)
    if not temp:
        return None
    return {
        "label": "Now",
        "temp": temp,
        "units": units_label,
        "description": _weather_description(current.weather_code).title(),
        "icon": _weather_icon_key(current.weather_code),
    }


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
