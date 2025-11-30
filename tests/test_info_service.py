from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace

from pulse.assistant.config import InfoConfig, NewsConfig, ShoppingListConfig, SportsConfig, WeatherConfig
from pulse.assistant.info_service import InfoResponse, InfoService
from pulse.assistant.info_sources import NewsHeadline, TeamSnapshot, WeatherCurrent, WeatherDay, WeatherForecast
from pulse.assistant.shopping_list import ShoppingListEntry, ShoppingListView


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
        current = WeatherCurrent(temperature=70, weather_code=0, windspeed=0, time="2025-01-01T12:00:00Z")
        return WeatherForecast("Testville", 0.0, 0.0, days, current=current)


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


class FakeShoppingParser:
    def __init__(self) -> None:
        self.command: SimpleNamespace | None = None

    def parse(self, transcript: str):
        return self.command


class FakeShoppingService:
    def __init__(self) -> None:
        self.enabled = True
        self.parser = FakeShoppingParser()
        self.last_action: tuple[str, list[str] | None] | None = None

    async def add_items(self, items: list[str]):
        self.last_action = ("add", items)
        return SimpleNamespace(added=items, reactivated=[], duplicates=[])

    async def remove_items(self, items: list[str]):
        self.last_action = ("remove", items)
        return SimpleNamespace(removed=items, missing=[])

    async def clear(self):
        self.last_action = ("clear", None)
        return SimpleNamespace(cleared=2)

    async def list_items(self):
        self.last_action = ("show", None)
        return ShoppingListView(items=[ShoppingListEntry("Eggs", False), ShoppingListEntry("Syrup", True)])

    def build_card(self, view: ShoppingListView, subtitle: str | None = None):
        return {
            "type": "shopping_list",
            "title": "Shopping List",
            "text": subtitle or "2 items",
            "items": [
                {"label": entry.label, "checked": entry.checked, "index": idx} for idx, entry in enumerate(view.items)
            ],
        }


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
            shopping=ShoppingListConfig(
                enabled=False,
                list_title="Shopping list",
                keep_client_id=None,
                keep_client_secret=None,
                keep_refresh_token=None,
                note_id=None,
                compound_items=(),
            ),
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
        self.fake_shopping = FakeShoppingService()
        self.service = InfoService(config, sources=fake_sources, shopping=self.fake_shopping)
        self.fake_news: FakeNewsClient = fake_sources.news

    def test_weather_intent_triggers_forecast(self) -> None:
        response = asyncio.run(self.service.maybe_answer("What's the weather today?"))
        self.assertIsInstance(response, InfoResponse)
        self.assertEqual(response.category, "weather")
        self.assertIn("Today", response.text)
        self.assertIsNotNone(response.display)
        assert response.display is not None
        self.assertIn("Today", response.display)
        self.assertIsNotNone(response.card)
        assert response.card is not None
        self.assertEqual(response.card.get("type"), "weather")
        days = response.card.get("days") or []
        self.assertGreaterEqual(len(days), 2)
        first = days[0]
        self.assertEqual(first.get("icon"), "sunny")
        current = response.card.get("current")
        assert isinstance(current, dict)
        self.assertEqual(current.get("icon"), "sunny")

    def test_news_intent_tracks_topic(self) -> None:
        response = asyncio.run(self.service.maybe_answer("What are the sports news headlines?"))
        self.assertEqual(self.fake_news.last_topic, "sports")
        self.assertIsNotNone(response)
        assert response is not None
        self.assertIn("headlines", response.text.lower())
        self.assertIn("•", response.display or "")

    def test_team_query_reports_next_game(self) -> None:
        response = asyncio.run(self.service.maybe_answer("When is the next Steelers game?"))
        self.assertIsNotNone(response)
        assert response is not None
        self.assertEqual(response.category, "sports")
        self.assertIn("Next up", response.text)
        self.assertIn("Next up", response.display or "")

    def test_shopping_add_command(self) -> None:
        self.fake_shopping.parser.command = SimpleNamespace(action="add", items=["eggs"])
        response = asyncio.run(self.service.maybe_answer("add eggs to my shopping list"))
        self.assertIsNotNone(response)
        assert response is not None
        self.assertEqual(response.category, "shopping")
        self.assertIn("Added", response.text)
        self.assertEqual(self.fake_shopping.last_action, ("add", ["eggs"]))

    def test_shopping_show_command_returns_card(self) -> None:
        self.fake_shopping.parser.command = SimpleNamespace(action="show", items=[])
        response = asyncio.run(self.service.maybe_answer("show me my shopping list"))
        self.assertIsNotNone(response)
        assert response is not None
        self.assertEqual(response.category, "shopping")
        self.assertIsNotNone(response.card)
        assert response.card is not None
        self.assertEqual(response.card.get("type"), "shopping_list")


if __name__ == "__main__":
    unittest.main()
