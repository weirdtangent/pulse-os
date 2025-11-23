from __future__ import annotations

import asyncio
import unittest

from pulse.assistant.config import InfoConfig, NewsConfig, SportsConfig, WeatherConfig
from pulse.assistant.info_service import InfoResponse, InfoService
from pulse.assistant.info_sources import NewsHeadline, TeamSnapshot, WeatherDay, WeatherForecast


class FakeNewsClient:
    def __init__(self) -> None:
        self.last_topic: str | None = None

    async def latest(self, topic: str | None = None) -> list[NewsHeadline]:
        self.last_topic = topic
        return [
            NewsHeadline("Demo headline", "Detailed context here.", "Example News"),
            NewsHeadline("Second headline", None, None),
        ]


class FakeWeatherClient:
    async def forecast(self) -> WeatherForecast:
        days = [
            WeatherDay(date="2025-01-01", temp_high=72, temp_low=58, precipitation_chance=20, weather_code=0),
            WeatherDay(date="2025-01-02", temp_high=68, temp_low=55, precipitation_chance=10, weather_code=0),
        ]
        return WeatherForecast("Testville", 0.0, 0.0, days)


class FakeSportsClient:
    def __init__(self) -> None:
        self.team_snapshot_result = TeamSnapshot(
            name="Pittsburgh Steelers",
            record="6-3",
            next_event={"matchup": "Steelers vs Browns", "date": "2025-01-05T18:00:00Z"},
            previous_event={"matchup": "Steelers vs Ravens", "date": "2024-12-29T18:00:00Z", "status": "Won"},
            league="nfl",
        )

    async def general_headlines(self, limit: int = 5):
        return []

    async def league_headlines(self, league: str, limit: int = 5):
        return []

    async def league_standings(self, league: str, limit: int = 5):
        return [{"name": "Steelers", "record": "6-3"}]

    async def team_snapshot(self, query: str, leagues=None):
        if "steelers" in query:
            return self.team_snapshot_result
        return None


class InfoServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        config = InfoConfig(
            news=NewsConfig(
                api_key="dummy",
                base_url="https://newsapi.example",
                country="us",
                category="general",
                language="en",
                max_articles=3,
            ),
            weather=WeatherConfig(
                location="40.0,-80.0",
                units="imperial",
                language="en",
                forecast_days=3,
                base_url="https://weather.example",
            ),
            sports=SportsConfig(
                default_country="us",
                headline_country="us",
                favorite_teams=("nfl:steelers",),
                default_leagues=("nfl",),
                base_url="https://sports.example",
            ),
            what3words_api_key=None,
        )
        fake_sources = type(
            "Sources",
            (),
            {
                "news": FakeNewsClient(),
                "weather": FakeWeatherClient(),
                "sports": FakeSportsClient(),
            },
        )()
        self.service = InfoService(config, sources=fake_sources)
        self.fake_news: FakeNewsClient = fake_sources.news

    def test_weather_intent_triggers_forecast(self) -> None:
        response = asyncio.run(self.service.maybe_answer("What's the weather today?"))
        self.assertIsInstance(response, InfoResponse)
        self.assertEqual(response.category, "weather")
        self.assertIn("Today", response.text)

    def test_news_intent_tracks_topic(self) -> None:
        asyncio.run(self.service.maybe_answer("What are the sports news headlines?"))
        self.assertEqual(self.fake_news.last_topic, "sports")

    def test_team_query_reports_next_game(self) -> None:
        response = asyncio.run(self.service.maybe_answer("When is the next Steelers game?"))
        self.assertIsNotNone(response)
        assert response is not None
        self.assertEqual(response.category, "sports")
        self.assertIn("Next up", response.text)


if __name__ == "__main__":
    unittest.main()
