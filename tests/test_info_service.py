"""Tests for info_service module."""

from __future__ import annotations

import math
from unittest.mock import AsyncMock, Mock

import pytest
from pulse.assistant.config import InfoConfig, NewsConfig, SportsConfig, WeatherConfig
from pulse.assistant.info_service import (
    InfoService,
    _build_current_weather_phrase,
    _describe_day,
    _describe_event,
    _extract_league,
    _format_current_weather_entry,
    _format_temp,
    _friendly_date,
    _normalize_text,
    _summarize_headline,
    _summarize_sports_headlines,
    _weather_description,
    _weather_icon_key,
)
from pulse.assistant.info_sources import (
    NewsHeadline,
    SportsHeadline,
    TeamSnapshot,
    WeatherCurrent,
    WeatherDay,
    WeatherForecast,
)

pytestmark = pytest.mark.anyio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(
    news=None,
    weather=None,
    sports=None,
    what3words_api_key=None,
) -> InfoConfig:
    return InfoConfig(
        news=news
        or NewsConfig(
            api_key="dummy",
            base_url="https://newsapi.example",
            country="us",
            category="general",
            language="en",
            max_articles=3,
        ),
        weather=weather
        or WeatherConfig(
            location="40.0,-80.0",
            units="imperial",
            language="en",
            forecast_days=3,
            base_url="https://weather.example",
        ),
        sports=sports
        or SportsConfig(
            default_country="us",
            headline_country="us",
            favorite_teams=("nfl:steelers",),
            default_leagues=("nfl",),
            base_url="https://sports.example",
        ),
        what3words_api_key=what3words_api_key,
    )


def _make_sources(weather=None, news=None, sports=None):
    sources = Mock()
    sources.weather = weather or AsyncMock()
    sources.news = news or AsyncMock()
    sources.sports = sports or AsyncMock()
    return sources


def _make_service(sources=None, config=None):
    return InfoService(config or _make_config(), sources=sources or _make_sources())


def _make_forecast(
    days=None,
    current=None,
    location_name="Testville",
) -> WeatherForecast:
    if days is None:
        days = [
            WeatherDay("2025-01-01", 72, 58, 20, 0),
            WeatherDay("2025-01-02", 68, 55, 10, 1),
        ]
    return WeatherForecast(location_name, 0.0, 0.0, days, current=current)


# ---------------------------------------------------------------------------
# _normalize_text
# ---------------------------------------------------------------------------


class TestNormalizeText:
    def test_lowercases(self):
        assert _normalize_text("Hello World") == "hello world"

    def test_strips_punctuation(self):
        # Apostrophe becomes space, then collapsed
        assert _normalize_text("what's the weather?") == "what s the weather"

    def test_collapses_whitespace(self):
        assert _normalize_text("too   many   spaces") == "too many spaces"

    def test_strips_leading_trailing(self):
        assert _normalize_text("  hi  ") == "hi"

    def test_preserves_plus_and_dot(self):
        assert _normalize_text("f1 3.5+") == "f1 3.5+"


# ---------------------------------------------------------------------------
# _describe_day
# ---------------------------------------------------------------------------


class TestDescribeDay:
    def test_today(self):
        assert _describe_day("2025-01-01", 0) == "Today"

    def test_tomorrow(self):
        assert _describe_day("2025-01-02", 1) == "Tomorrow"

    def test_weekday_name(self):
        result = _describe_day("2025-01-03", 2)
        assert result == "Friday"

    def test_invalid_date_returns_raw(self):
        assert _describe_day("not-a-date", 2) == "not-a-date"


# ---------------------------------------------------------------------------
# _format_temp
# ---------------------------------------------------------------------------


class TestFormatTemp:
    def test_none(self):
        assert _format_temp(None) is None

    def test_nan(self):
        assert _format_temp(math.nan) is None

    def test_rounds_integer(self):
        assert _format_temp(72.6) == "73"

    def test_zero(self):
        assert _format_temp(0.0) == "0"

    def test_negative(self):
        assert _format_temp(-5.3) == "-5"


# ---------------------------------------------------------------------------
# _weather_icon_key
# ---------------------------------------------------------------------------


class TestWeatherIconKey:
    @pytest.mark.parametrize(
        "code,expected",
        [
            (None, "cloudy"),
            (0, "sunny"),
            (1, "partly_cloudy"),
            (2, "mostly_cloudy"),
            (3, "cloudy"),
            (45, "fog"),
            (48, "fog"),
            (51, "drizzle"),
            (53, "drizzle"),
            (55, "drizzle"),
            (56, "sleet"),
            (57, "sleet"),
            (61, "rain"),
            (63, "rain"),
            (65, "downpour"),
            (71, "snow"),
            (77, "snow"),
            (80, "rain"),
            (81, "rain"),
            (82, "downpour"),
            (85, "snow"),
            (86, "snow"),
            (95, "thunder"),
            (96, "thunder"),
            (99, "thunder"),
            (999, "cloudy"),  # unmapped code
        ],
    )
    def test_icon_mapping(self, code, expected):
        assert _weather_icon_key(code) == expected


# ---------------------------------------------------------------------------
# _weather_description
# ---------------------------------------------------------------------------


class TestWeatherDescription:
    def test_sunny(self):
        assert _weather_description(0) == "clear skies"

    def test_rain(self):
        assert _weather_description(61) == "rainy"

    def test_none_code(self):
        assert _weather_description(None) == "overcast"

    def test_unmapped_code(self):
        assert _weather_description(999) == "overcast"


# ---------------------------------------------------------------------------
# _build_current_weather_phrase
# ---------------------------------------------------------------------------


class TestBuildCurrentWeatherPhrase:
    def test_with_current(self):
        current = WeatherCurrent(temperature=70, weather_code=0, windspeed=5, time="2025-01-01T12:00:00Z")
        forecast = _make_forecast(current=current)
        result = _build_current_weather_phrase(forecast)
        assert result is not None
        assert "70" in result
        assert "clear skies" in result

    def test_no_current(self):
        forecast = _make_forecast(current=None)
        assert _build_current_weather_phrase(forecast) is None

    def test_none_temperature(self):
        current = WeatherCurrent(temperature=None, weather_code=0, windspeed=0, time=None)
        forecast = _make_forecast(current=current)
        assert _build_current_weather_phrase(forecast) is None

    def test_nan_temperature(self):
        current = WeatherCurrent(temperature=math.nan, weather_code=0, windspeed=0, time=None)
        forecast = _make_forecast(current=current)
        assert _build_current_weather_phrase(forecast) is None


# ---------------------------------------------------------------------------
# _format_current_weather_entry
# ---------------------------------------------------------------------------


class TestFormatCurrentWeatherEntry:
    def test_with_current(self):
        current = WeatherCurrent(temperature=70, weather_code=0, windspeed=5, time=None)
        forecast = _make_forecast(current=current)
        result = _format_current_weather_entry(forecast, "°F")
        assert result is not None
        assert result["label"] == "Now"
        assert result["temp"] == "70"
        assert result["units"] == "°F"
        assert result["icon"] == "sunny"

    def test_no_current(self):
        forecast = _make_forecast(current=None)
        assert _format_current_weather_entry(forecast, "°F") is None

    def test_none_temperature(self):
        current = WeatherCurrent(temperature=None, weather_code=0, windspeed=0, time=None)
        forecast = _make_forecast(current=current)
        assert _format_current_weather_entry(forecast, "°C") is None


# ---------------------------------------------------------------------------
# _summarize_headline
# ---------------------------------------------------------------------------


class TestSummarizeHeadline:
    def test_with_description(self):
        h = NewsHeadline("Title", "Details here", "Source")
        assert _summarize_headline(h) == "Title: Details here"

    def test_no_description_with_source(self):
        h = NewsHeadline("Title", None, "CNN")
        assert _summarize_headline(h) == "Title (CNN)"

    def test_no_description_no_source(self):
        h = NewsHeadline("Title", None, None)
        assert _summarize_headline(h) == "Title"


# ---------------------------------------------------------------------------
# _summarize_sports_headlines
# ---------------------------------------------------------------------------


class TestSummarizeSportsHeadlines:
    def test_with_league(self):
        headlines = [SportsHeadline("Big trade", None, "nfl"), SportsHeadline("Injury update", None, "nfl")]
        spoken, display = _summarize_sports_headlines(headlines, "nfl")
        assert "In NFL" in spoken
        assert "Big trade" in spoken
        assert "Injury update" in spoken
        assert display.count("•") == 2

    def test_without_league(self):
        headlines = [SportsHeadline("Story one", None, None)]
        spoken, display = _summarize_sports_headlines(headlines, None)
        assert "Story one" in spoken
        assert not spoken.startswith("In")

    def test_max_three_headlines(self):
        headlines = [SportsHeadline(f"Story {i}", None, None) for i in range(5)]
        spoken, display = _summarize_sports_headlines(headlines, None)
        assert display.count("•") == 3


# ---------------------------------------------------------------------------
# _extract_league
# ---------------------------------------------------------------------------


class TestExtractLeague:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("nfl standings", "nfl"),
            ("what are the nba scores", "nba"),
            ("hockey news", "nhl"),
            ("baseball schedule", "mlb"),
            ("formula 1 results", "f1"),
            ("march madness bracket", "ncaam"),
            ("march madness bracket", "ncaam"),
            ("no league here", None),
        ],
    )
    def test_league_extraction(self, text, expected):
        assert _extract_league(text) == expected


# ---------------------------------------------------------------------------
# _describe_event
# ---------------------------------------------------------------------------


class TestDescribeEvent:
    def test_with_matchup_and_date(self):
        event = {"matchup": "Team A vs Team B", "date": "2025-06-15T18:00:00Z"}
        result = _describe_event("Next up", event)
        assert result is not None
        assert "Next up" in result
        assert "Team A vs Team B" in result
        assert result.endswith(".")

    def test_with_status(self):
        event = {"matchup": "A vs B", "status": "Won"}
        result = _describe_event("Last game", event)
        assert result is not None
        assert "(Won)" in result

    def test_fallback_to_name(self):
        event = {"name": "Championship Game"}
        result = _describe_event("Next up", event)
        assert result is not None
        assert "Championship Game" in result

    def test_fallback_to_short_name(self):
        event = {"shortName": "CG"}
        result = _describe_event("Next", event)
        assert result is not None
        assert "CG" in result

    def test_empty_event(self):
        result = _describe_event("Next up", {})
        assert result is not None
        assert result.endswith(".")


# ---------------------------------------------------------------------------
# _friendly_date
# ---------------------------------------------------------------------------


class TestFriendlyDate:
    def test_none(self):
        assert _friendly_date(None) is None

    def test_valid_iso(self):
        result = _friendly_date("2025-06-15T18:00:00Z")
        assert result is not None
        assert "Sunday" in result

    def test_invalid_format(self):
        assert _friendly_date("not-a-date") == "not-a-date"


# ---------------------------------------------------------------------------
# InfoService intent detection
# ---------------------------------------------------------------------------


class TestIntentDetection:
    def test_is_weather(self):
        svc = _make_service()
        assert svc._is_weather("weather today") is True
        assert svc._is_weather("forecast for tomorrow") is True
        assert svc._is_weather("will it rain") is True
        assert svc._is_weather("hello there") is False

    def test_is_news(self):
        svc = _make_service()
        assert svc._is_news("top headlines") is True
        assert svc._is_news("latest news") is True
        assert svc._is_news("hello there") is False

    def test_extract_news_topic(self):
        svc = _make_service()
        assert svc._extract_news_topic("sports news") == "sports"
        assert svc._extract_news_topic("technology headlines") == "technology"
        assert svc._extract_news_topic("latest news") is None


# ---------------------------------------------------------------------------
# InfoService.maybe_answer — weather
# ---------------------------------------------------------------------------


class TestMaybeAnswerWeather:
    async def test_weather_response(self):
        weather = AsyncMock()
        current = WeatherCurrent(temperature=70, weather_code=0, windspeed=5, time="2025-01-01T12:00:00Z")
        weather.forecast = AsyncMock(return_value=_make_forecast(current=current))
        sources = _make_sources(weather=weather)
        svc = _make_service(sources=sources)

        response = await svc.maybe_answer("What's the weather today?")
        assert response is not None
        assert response.category == "weather"
        assert "Today" in response.text
        assert response.card is not None
        assert response.card["type"] == "weather"
        assert response.card["title"] == "Testville"
        assert len(response.card["days"]) == 2

    async def test_weather_with_current_conditions(self):
        weather = AsyncMock()
        current = WeatherCurrent(temperature=72, weather_code=0, windspeed=0, time=None)
        weather.forecast = AsyncMock(return_value=_make_forecast(current=current))
        sources = _make_sources(weather=weather)
        svc = _make_service(sources=sources)

        response = await svc.maybe_answer("Weather forecast")
        assert response is not None
        assert "72" in response.text
        assert response.card["current"] is not None

    async def test_weather_no_forecast(self):
        weather = AsyncMock()
        weather.forecast = AsyncMock(return_value=None)
        sources = _make_sources(weather=weather)
        svc = _make_service(sources=sources)

        response = await svc.maybe_answer("What's the weather?")
        # Falls through to sports handler if no weather data
        assert response is None or response.category != "weather"

    async def test_weather_empty_days(self):
        weather = AsyncMock()
        weather.forecast = AsyncMock(return_value=_make_forecast(days=[]))
        sources = _make_sources(weather=weather)
        svc = _make_service(sources=sources)

        response = await svc.maybe_answer("Weather forecast")
        assert response is None or response.category != "weather"

    async def test_weather_no_location_name(self):
        weather = AsyncMock()
        current = WeatherCurrent(temperature=70, weather_code=0, windspeed=0, time=None)
        forecast = WeatherForecast("", 0.0, 0.0, [WeatherDay("2025-01-01", 72, 58, 20, 0)], current=current)
        weather.forecast = AsyncMock(return_value=forecast)
        sources = _make_sources(weather=weather)
        svc = _make_service(sources=sources)

        response = await svc.maybe_answer("Weather?")
        assert response is not None
        assert response.card["title"] == "Weather"

    async def test_weather_metric_units(self):
        config = _make_config(
            weather=WeatherConfig(
                location="40.0,-80.0",
                units="metric",
                language="en",
                forecast_days=1,
                base_url="https://weather.example",
            )
        )
        weather = AsyncMock()
        current = WeatherCurrent(temperature=21, weather_code=0, windspeed=0, time=None)
        weather.forecast = AsyncMock(
            return_value=_make_forecast(days=[WeatherDay("2025-01-01", 22, 15, None, 0)], current=current)
        )
        sources = _make_sources(weather=weather)
        svc = InfoService(config, sources=sources)

        response = await svc.maybe_answer("Weather?")
        assert response is not None
        assert response.card["units"] == "°C"

    async def test_weather_day_missing_temps(self):
        weather = AsyncMock()
        weather.forecast = AsyncMock(
            return_value=_make_forecast(days=[WeatherDay("2025-01-01", None, None, None, None)])
        )
        sources = _make_sources(weather=weather)
        svc = _make_service(sources=sources)

        response = await svc.maybe_answer("Weather?")
        # Day with no temps is skipped, no phrases → None
        assert response is None or response.category != "weather"

    async def test_weather_no_precipitation(self):
        weather = AsyncMock()
        current = WeatherCurrent(temperature=70, weather_code=0, windspeed=0, time=None)
        weather.forecast = AsyncMock(
            return_value=_make_forecast(days=[WeatherDay("2025-01-01", 72, 58, None, 0)], current=current)
        )
        sources = _make_sources(weather=weather)
        svc = _make_service(sources=sources)

        response = await svc.maybe_answer("Weather?")
        assert response is not None
        assert "precip" not in response.text.lower()


# ---------------------------------------------------------------------------
# InfoService.maybe_answer — news
# ---------------------------------------------------------------------------


class TestMaybeAnswerNews:
    async def test_news_response(self):
        news = AsyncMock()
        news.latest = AsyncMock(
            return_value=[
                NewsHeadline("Big Story", "Something happened", "Reuters"),
                NewsHeadline("Other Story", None, "AP"),
            ]
        )
        sources = _make_sources(news=news)
        svc = _make_service(sources=sources)

        response = await svc.maybe_answer("What are the latest headlines?")
        assert response is not None
        assert response.category == "news"
        assert "headlines" in response.text.lower()
        assert "Big Story" in response.text

    async def test_news_with_topic(self):
        news = AsyncMock()
        news.latest = AsyncMock(return_value=[NewsHeadline("Tech News", "Details", None)])
        sources = _make_sources(news=news)
        svc = _make_service(sources=sources)

        response = await svc.maybe_answer("Give me the technology news")
        assert response is not None
        news.latest.assert_awaited_once_with("technology")

    async def test_news_empty_headlines(self):
        news = AsyncMock()
        news.latest = AsyncMock(return_value=[])
        weather = AsyncMock()
        weather.forecast = AsyncMock(return_value=None)
        sources = _make_sources(news=news, weather=weather)
        svc = _make_service(sources=sources)

        response = await svc.maybe_answer("What's the news?")
        assert response is None or response.category != "news"

    async def test_news_display_has_bullets(self):
        news = AsyncMock()
        news.latest = AsyncMock(
            return_value=[
                NewsHeadline("Story One", "Desc one", "Source"),
                NewsHeadline("Story Two", "Desc two", "Source"),
            ]
        )
        sources = _make_sources(news=news)
        svc = _make_service(sources=sources)

        response = await svc.maybe_answer("Top headlines")
        assert response is not None
        assert response.display is not None
        assert response.display.count("•") == 2


# ---------------------------------------------------------------------------
# InfoService.maybe_answer — sports
# ---------------------------------------------------------------------------


class TestMaybeAnswerSports:
    async def test_team_snapshot(self):
        sports = AsyncMock()
        sports.team_snapshot = AsyncMock(
            return_value=TeamSnapshot(
                name="Pittsburgh Steelers",
                record="6-3",
                next_event={"matchup": "Steelers vs Browns", "date": "2025-01-05T18:00:00Z"},
                previous_event={"matchup": "Steelers vs Ravens", "status": "Won"},
                league="nfl",
            )
        )
        sports.league_standings = AsyncMock(return_value=[])
        sports.league_headlines = AsyncMock(return_value=[])
        sports.general_headlines = AsyncMock(return_value=[])
        sources = _make_sources(sports=sports)
        svc = _make_service(sources=sources)

        response = await svc.maybe_answer("When is the next Steelers game?")
        assert response is not None
        assert response.category == "sports"
        assert "Next up" in response.text

    async def test_standings_query(self):
        sports = AsyncMock()
        sports.league_standings = AsyncMock(
            return_value=[
                {"name": "Chiefs", "record": "10-1"},
                {"name": "Steelers", "record": "8-3"},
            ]
        )
        sports.team_snapshot = AsyncMock(return_value=None)
        sports.general_headlines = AsyncMock(return_value=[])
        sources = _make_sources(sports=sports)
        svc = _make_service(sources=sources)

        response = await svc.maybe_answer("NFL standings")
        assert response is not None
        assert "Chiefs" in response.text
        assert "NFL" in response.text

    async def test_league_headlines(self):
        sports = AsyncMock()
        sports.league_headlines = AsyncMock(
            return_value=[
                SportsHeadline("Trade deadline news", None, "nba"),
            ]
        )
        sports.league_standings = AsyncMock(return_value=[])
        sports.team_snapshot = AsyncMock(return_value=None)
        sports.general_headlines = AsyncMock(return_value=[])
        sources = _make_sources(sports=sports)
        svc = _make_service(sources=sources)

        response = await svc.maybe_answer("NBA headlines")
        assert response is not None
        assert "Trade deadline news" in response.text

    async def test_general_sports_headlines(self):
        sports = AsyncMock()
        sports.general_headlines = AsyncMock(
            return_value=[
                SportsHeadline("Big game tonight", None, None),
            ]
        )
        sports.team_snapshot = AsyncMock(return_value=None)
        sports.league_standings = AsyncMock(return_value=[])
        sports.league_headlines = AsyncMock(return_value=[])
        sources = _make_sources(sports=sports)
        svc = _make_service(sources=sources)

        response = await svc.maybe_answer("What's happening in sports?")
        assert response is not None
        assert "Big game tonight" in response.text

    async def test_team_not_found(self):
        sports = AsyncMock()
        sports.team_snapshot = AsyncMock(return_value=None)
        sports.general_headlines = AsyncMock(return_value=[])
        sports.league_standings = AsyncMock(return_value=[])
        sports.league_headlines = AsyncMock(return_value=[])
        sources = _make_sources(sports=sports)
        svc = _make_service(sources=sources)

        response = await svc.maybe_answer("Next FakeTeam game")
        assert response is None

    async def test_team_no_next_event_emphasized(self):
        sports = AsyncMock()
        sports.team_snapshot = AsyncMock(
            return_value=TeamSnapshot(
                name="Steelers",
                record="6-3",
                next_event=None,
                previous_event=None,
                league="nfl",
            )
        )
        sports.league_standings = AsyncMock(return_value=[])
        sports.league_headlines = AsyncMock(return_value=[])
        sports.general_headlines = AsyncMock(return_value=[])
        sources = _make_sources(sports=sports)
        svc = _make_service(sources=sources)

        response = await svc.maybe_answer("When is the next Steelers game?")
        assert response is not None
        assert "couldn't find" in response.text.lower()

    async def test_team_no_record(self):
        sports = AsyncMock()
        sports.team_snapshot = AsyncMock(
            return_value=TeamSnapshot(
                name="Steelers",
                record=None,
                next_event=None,
                previous_event=None,
                league="nfl",
            )
        )
        sports.league_standings = AsyncMock(return_value=[])
        sports.league_headlines = AsyncMock(return_value=[])
        sports.general_headlines = AsyncMock(return_value=[])
        sources = _make_sources(sports=sports)
        svc = _make_service(sources=sources)

        response = await svc.maybe_answer("Steelers sports")
        assert response is not None
        assert "record unavailable" in response.text


# ---------------------------------------------------------------------------
# InfoService.maybe_answer — edge cases
# ---------------------------------------------------------------------------


class TestMaybeAnswerEdgeCases:
    async def test_empty_transcript(self):
        svc = _make_service()
        response = await svc.maybe_answer("")
        assert response is None

    async def test_whitespace_transcript(self):
        svc = _make_service()
        response = await svc.maybe_answer("   ")
        assert response is None

    async def test_no_match(self):
        sports = AsyncMock()
        sports.team_snapshot = AsyncMock(return_value=None)
        sports.general_headlines = AsyncMock(return_value=[])
        sports.league_standings = AsyncMock(return_value=[])
        sports.league_headlines = AsyncMock(return_value=[])
        sources = _make_sources(sports=sports)
        svc = _make_service(sources=sources)

        response = await svc.maybe_answer("Tell me a joke")
        assert response is None


# ---------------------------------------------------------------------------
# InfoService._find_team_snapshot — phrase window
# ---------------------------------------------------------------------------


class TestFindTeamSnapshot:
    async def test_tries_multi_word_phrases_first(self):
        sports = AsyncMock()
        calls = []

        async def track_snapshot(query, leagues=None):
            calls.append(query)
            if query == "pittsburgh steelers":
                return TeamSnapshot("Steelers", "6-3", None, None, "nfl")
            return None

        sports.team_snapshot = track_snapshot
        sources = _make_sources(sports=sports)
        svc = _make_service(sources=sources)

        result = await svc._find_team_snapshot("pittsburgh steelers record", None)
        assert result is not None
        # Should try 2-word phrases before 1-word
        assert "pittsburgh steelers" in calls

    async def test_skips_short_phrases(self):
        sports = AsyncMock()
        calls = []

        async def track_snapshot(query, leagues=None):
            calls.append(query)
            return None

        sports.team_snapshot = track_snapshot
        sources = _make_sources(sports=sports)
        svc = _make_service(sources=sources)

        # After stop words removed, only short tokens remain
        await svc._find_team_snapshot("is the a", None)
        # All remaining words are stop words, so no calls
        assert len(calls) == 0


# ---------------------------------------------------------------------------
# InfoService._format_team_snapshot
# ---------------------------------------------------------------------------


class TestFormatTeamSnapshot:
    def test_with_next_and_previous(self):
        svc = _make_service()
        snapshot = TeamSnapshot(
            "Steelers",
            "6-3",
            next_event={"matchup": "A vs B"},
            previous_event={"matchup": "C vs D", "status": "Won"},
            league="nfl",
        )
        result = svc._format_team_snapshot(snapshot, emphasize_next=False)
        assert result is not None
        spoken, display = result
        assert "Next up" in spoken
        assert "Last game" in spoken

    def test_emphasize_next_hides_previous(self):
        svc = _make_service()
        snapshot = TeamSnapshot(
            "Steelers",
            "6-3",
            next_event={"matchup": "A vs B"},
            previous_event={"matchup": "C vs D"},
            league="nfl",
        )
        result = svc._format_team_snapshot(snapshot, emphasize_next=True)
        assert result is not None
        spoken, _ = result
        assert "Next up" in spoken
        assert "Last game" not in spoken

    def test_no_next_event_emphasized(self):
        svc = _make_service()
        snapshot = TeamSnapshot("Steelers", "6-3", next_event=None, previous_event=None, league="nfl")
        result = svc._format_team_snapshot(snapshot, emphasize_next=True)
        assert result is not None
        spoken, _ = result
        assert "couldn't find" in spoken.lower()
