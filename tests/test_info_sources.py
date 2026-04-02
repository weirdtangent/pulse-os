"""Tests for pulse.assistant.info_sources."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pulse.assistant.config import InfoConfig, NewsConfig, SportsConfig, WeatherConfig
from pulse.assistant.info_sources import (
    InfoSources,
    NewsClient,
    NewsHeadline,
    SportsClient,
    SportsHeadline,
    TTLCache,
    WeatherClient,
    WeatherForecast,
    _decode_plus_code,
    _expand_geocode_queries,
    _extract_record,
    _safe_list_float,
    _simplify_event,
    _team_name_tokens,
    _team_record,
)

pytestmark = pytest.mark.anyio


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _news_config(**overrides) -> NewsConfig:  # type: ignore[no-untyped-def]
    defaults: dict = dict(
        api_key="test-key",
        base_url="https://newsapi.org/v2",
        country="us",
        category="general",
        language="en",
        max_articles=5,
    )
    defaults.update(overrides)
    return NewsConfig(**defaults)  # type: ignore[arg-type]


def _weather_config(**overrides) -> WeatherConfig:  # type: ignore[no-untyped-def]
    defaults: dict = dict(
        location="40.7128,-74.0060",
        units="imperial",
        language="en",
        forecast_days=3,
        base_url="https://api.open-meteo.com/v1/forecast",
    )
    defaults.update(overrides)
    return WeatherConfig(**defaults)  # type: ignore[arg-type]


def _sports_config(**overrides) -> SportsConfig:  # type: ignore[no-untyped-def]
    defaults: dict = dict(
        default_country="us",
        headline_country="us",
        favorite_teams=("eagles",),
        default_leagues=("nfl", "nba"),
        base_url="https://site.api.espn.com/apis",
    )
    defaults.update(overrides)
    return SportsConfig(**defaults)  # type: ignore[arg-type]


def _info_config(**overrides) -> InfoConfig:  # type: ignore[no-untyped-def]
    defaults: dict = dict(
        news=_news_config(),
        weather=_weather_config(),
        sports=_sports_config(),
        what3words_api_key=None,
    )
    defaults.update(overrides)
    return InfoConfig(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# TTLCache
# ---------------------------------------------------------------------------


class TestTTLCache:
    def test_get_returns_none_for_missing_key(self):
        cache = TTLCache(ttl_seconds=60)
        assert cache.get("missing") is None

    def test_set_and_get(self):
        cache = TTLCache(ttl_seconds=60)
        cache.set("key", "value")
        assert cache.get("key") == "value"

    def test_expired_entry_returns_none(self):
        cache = TTLCache(ttl_seconds=10)
        with patch("pulse.assistant.info_sources.time") as mock_time:
            mock_time.monotonic.return_value = 100.0
            cache.set("key", "value")
            # Advance past TTL
            mock_time.monotonic.return_value = 111.0
            assert cache.get("key") is None

    def test_not_yet_expired_entry_returned(self):
        cache = TTLCache(ttl_seconds=10)
        with patch("pulse.assistant.info_sources.time") as mock_time:
            mock_time.monotonic.return_value = 100.0
            cache.set("key", "value")
            mock_time.monotonic.return_value = 109.0
            assert cache.get("key") == "value"

    def test_expired_entry_is_removed(self):
        cache = TTLCache(ttl_seconds=10)
        with patch("pulse.assistant.info_sources.time") as mock_time:
            mock_time.monotonic.return_value = 100.0
            cache.set("key", "value")
            mock_time.monotonic.return_value = 111.0
            cache.get("key")
            assert "key" not in cache._values


# ---------------------------------------------------------------------------
# _get_json
# ---------------------------------------------------------------------------


async def test_get_json_success():
    from pulse.assistant.info_sources import _get_json

    mock_response = MagicMock()
    mock_response.json.return_value = {"ok": True}
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.get.return_value = mock_response
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("pulse.assistant.info_sources.httpx.AsyncClient", return_value=mock_client):
        result = await _get_json("https://example.com/api", params={"q": "test"})
    assert result == {"ok": True}


async def test_get_json_http_error_returns_none():
    import httpx
    from pulse.assistant.info_sources import _get_json

    mock_client = AsyncMock()
    mock_client.get.side_effect = httpx.HTTPError("fail")
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("pulse.assistant.info_sources.httpx.AsyncClient", return_value=mock_client):
        result = await _get_json("https://example.com/api")
    assert result is None


# ---------------------------------------------------------------------------
# NewsClient
# ---------------------------------------------------------------------------


class TestNewsClient:
    async def test_no_api_key_returns_empty(self):
        client = NewsClient(_news_config(api_key=None))
        assert await client.latest() == []

    async def test_cached_result_returned(self):
        client = NewsClient(_news_config())
        expected = [NewsHeadline(title="Test", description=None, source=None)]
        client._cache.set("__default__", expected)
        assert await client.latest() is expected

    async def test_cached_with_topic(self):
        client = NewsClient(_news_config())
        expected = [NewsHeadline(title="Tech", description=None, source=None)]
        client._cache.set("technology", expected)
        assert await client.latest(topic="technology") is expected

    @pytest.mark.anyio
    @patch("pulse.assistant.info_sources._get_json", new_callable=AsyncMock)
    async def test_successful_fetch(self, mock_get):
        mock_get.return_value = {
            "articles": [
                {"title": "Breaking News", "description": "Desc", "source": {"name": "CNN"}},
                {"title": "  ", "description": "skip empty title", "source": {"name": "X"}},
                {"title": "Story 2", "description": None, "source": {}},
            ]
        }
        client = NewsClient(_news_config())
        results = await client.latest()
        assert len(results) == 2
        assert results[0].title == "Breaking News"
        assert results[0].description == "Desc"
        assert results[0].source == "CNN"
        assert results[1].title == "Story 2"
        assert results[1].description is None
        assert results[1].source is None

    @pytest.mark.anyio
    @patch("pulse.assistant.info_sources._get_json", new_callable=AsyncMock)
    async def test_empty_articles(self, mock_get):
        mock_get.return_value = {"articles": []}
        client = NewsClient(_news_config())
        results = await client.latest()
        assert results == []

    @pytest.mark.anyio
    @patch("pulse.assistant.info_sources._get_json", new_callable=AsyncMock)
    async def test_none_payload(self, mock_get):
        mock_get.return_value = None
        client = NewsClient(_news_config())
        results = await client.latest()
        assert results == []

    @pytest.mark.anyio
    @patch("pulse.assistant.info_sources._get_json", new_callable=AsyncMock)
    async def test_results_are_cached(self, mock_get):
        mock_get.return_value = {"articles": [{"title": "Cached", "description": "", "source": {}}]}
        client = NewsClient(_news_config())
        await client.latest()
        assert client._cache.get("__default__") is not None


# ---------------------------------------------------------------------------
# WeatherClient
# ---------------------------------------------------------------------------


class TestWeatherClient:
    async def test_no_location_returns_none(self):
        client = WeatherClient(_weather_config(location=None), what3words_api_key=None)
        assert await client.forecast() is None

    async def test_cached_forecast(self):
        config = _weather_config(location="40.7128,-74.0060", units="imperial")
        client = WeatherClient(config, what3words_api_key=None)
        expected = WeatherForecast("NYC", 40.7128, -74.006, [])
        cache_key = "40.7128,-74.0060:imperial:en:3"
        client._forecast_cache.set(cache_key, expected)
        assert await client.forecast() is expected

    @pytest.mark.anyio
    @patch("pulse.assistant.info_sources._get_json", new_callable=AsyncMock)
    async def test_successful_forecast_imperial(self, mock_get):
        mock_get.return_value = {
            "daily": {
                "time": ["2026-04-01", "2026-04-02"],
                "temperature_2m_max": [75.0, 78.0],
                "temperature_2m_min": [55.0, 58.0],
                "precipitation_probability_max": [10, 20],
                "weathercode": [0, 1],
            },
            "current_weather": {
                "temperature": 72.0,
                "weathercode": 0,
                "windspeed": 5.5,
                "time": "2026-04-01T12:00",
            },
        }
        config = _weather_config(units="imperial")
        client = WeatherClient(config, what3words_api_key=None)
        result = await client.forecast()
        assert result is not None
        assert len(result.days) == 2
        assert result.days[0].temp_high == 75.0
        assert result.current is not None
        assert result.current.temperature == 72.0
        assert result.current.windspeed == 5.5
        assert result.current.time == "2026-04-01T12:00"

    @pytest.mark.anyio
    @patch("pulse.assistant.info_sources._get_json", new_callable=AsyncMock)
    async def test_successful_forecast_metric(self, mock_get):
        mock_get.return_value = {
            "daily": {
                "time": ["2026-04-01"],
                "temperature_2m_max": [24.0],
                "temperature_2m_min": [13.0],
                "precipitation_probability_max": [30],
                "weathercode": [2],
            },
            "current_weather": {},
        }
        config = _weather_config(units="metric")
        client = WeatherClient(config, what3words_api_key=None)
        result = await client.forecast()
        assert result is not None
        assert result.current.temperature is None
        assert result.current.weather_code is None

    @pytest.mark.anyio
    @patch("pulse.assistant.info_sources._get_json", new_callable=AsyncMock)
    async def test_forecast_auto_units(self, mock_get):
        mock_get.return_value = {
            "daily": {
                "time": [],
                "temperature_2m_max": [],
                "temperature_2m_min": [],
                "precipitation_probability_max": [],
                "weathercode": [],
            },
            "current_weather": {},
        }
        config = _weather_config(units="auto")
        client = WeatherClient(config, what3words_api_key=None)
        result = await client.forecast()
        assert result is not None
        # auto should use imperial params
        call_kwargs = mock_get.call_args
        assert call_kwargs[1]["params"]["temperature_unit"] == "fahrenheit"

    @pytest.mark.anyio
    @patch("pulse.assistant.info_sources._get_json", new_callable=AsyncMock)
    async def test_forecast_none_payload(self, mock_get):
        mock_get.return_value = None
        config = _weather_config()
        client = WeatherClient(config, what3words_api_key=None)
        result = await client.forecast()
        assert result is None


# ---------------------------------------------------------------------------
# WeatherClient._resolve_location
# ---------------------------------------------------------------------------


class TestResolveLocation:
    async def test_lat_lon(self):
        config = _weather_config()
        client = WeatherClient(config, what3words_api_key=None)
        loc = await client._resolve_location("40.7128, -74.0060")
        assert loc is not None
        assert loc.latitude == 40.7128
        assert loc.longitude == -74.006

    async def test_cached_location(self):
        config = _weather_config()
        client = WeatherClient(config, what3words_api_key=None)
        from pulse.assistant.info_sources import _Location

        expected = _Location(1.0, 2.0, "cached")
        client._location_cache.set("somewhere", expected)
        result = await client._resolve_location("somewhere")
        assert result is expected

    @pytest.mark.anyio
    @patch("pulse.assistant.info_sources._get_json", new_callable=AsyncMock)
    async def test_what3words(self, mock_get):
        mock_get.return_value = {"coordinates": {"lat": 51.5, "lng": -0.1}}
        config = _weather_config()
        client = WeatherClient(config, what3words_api_key="w3w-key")
        loc = await client._resolve_location("filled.count.soap")
        assert loc is not None
        assert loc.latitude == 51.5
        assert loc.longitude == -0.1

    @pytest.mark.anyio
    @patch("pulse.assistant.info_sources._get_json", new_callable=AsyncMock)
    async def test_postal_code(self, mock_get):
        mock_get.return_value = {
            "places": [
                {
                    "latitude": "37.7749",
                    "longitude": "-122.4194",
                    "place name": "San Francisco",
                    "state abbreviation": "CA",
                }
            ]
        }
        config = _weather_config()
        client = WeatherClient(config, what3words_api_key=None)
        loc = await client._resolve_location("94102")
        assert loc is not None
        assert loc.display_name == "San Francisco, CA"

    @pytest.mark.anyio
    @patch("pulse.assistant.info_sources._get_json", new_callable=AsyncMock)
    async def test_plus_code(self, mock_get):
        # Mock olc module
        mock_decoded = MagicMock()
        mock_decoded.latitudeCenter = 37.7749
        mock_decoded.longitudeCenter = -122.4194
        mock_olc = MagicMock()
        mock_olc.decode.return_value = mock_decoded

        with patch("pulse.assistant.info_sources.olc", mock_olc):
            config = _weather_config()
            client = WeatherClient(config, what3words_api_key=None)
            loc = await client._resolve_location("849VCWC8+R9")
        assert loc is not None
        assert loc.latitude == 37.7749

    @pytest.mark.anyio
    @patch("pulse.assistant.info_sources._get_json", new_callable=AsyncMock)
    async def test_geocode_text_fallback(self, mock_get):
        mock_get.return_value = {"results": [{"latitude": 48.8566, "longitude": 2.3522, "name": "Paris"}]}
        config = _weather_config()
        client = WeatherClient(config, what3words_api_key=None)
        loc = await client._resolve_location("Paris, France")
        assert loc is not None
        assert loc.display_name == "Paris"

    @pytest.mark.anyio
    @patch("pulse.assistant.info_sources._get_json", new_callable=AsyncMock)
    async def test_resolve_location_returns_none_when_nothing_matches(self, mock_get):
        mock_get.return_value = None
        config = _weather_config()
        client = WeatherClient(config, what3words_api_key=None)
        loc = await client._resolve_location("xyznotaplace")
        assert loc is None


# ---------------------------------------------------------------------------
# WeatherClient._geocode_text
# ---------------------------------------------------------------------------


class TestGeocodeText:
    @pytest.mark.anyio
    @patch("pulse.assistant.info_sources._get_json", new_callable=AsyncMock)
    async def test_success(self, mock_get):
        mock_get.return_value = {"results": [{"latitude": 40.0, "longitude": -74.0, "name": "NYC"}]}
        config = _weather_config()
        client = WeatherClient(config, what3words_api_key=None)
        result = await client._geocode_text("New York")
        assert result is not None
        assert result.display_name == "NYC"

    @pytest.mark.anyio
    @patch("pulse.assistant.info_sources._get_json", new_callable=AsyncMock)
    async def test_no_results(self, mock_get):
        mock_get.return_value = {"results": []}
        config = _weather_config()
        client = WeatherClient(config, what3words_api_key=None)
        result = await client._geocode_text("xyznotreal")
        assert result is None

    @pytest.mark.anyio
    @patch("pulse.assistant.info_sources._get_json", new_callable=AsyncMock)
    async def test_none_payload(self, mock_get):
        mock_get.return_value = None
        config = _weather_config()
        client = WeatherClient(config, what3words_api_key=None)
        result = await client._geocode_text("nowhere")
        assert result is None


# ---------------------------------------------------------------------------
# WeatherClient._resolve_what3words
# ---------------------------------------------------------------------------


class TestResolveWhat3Words:
    @pytest.mark.anyio
    @patch("pulse.assistant.info_sources._get_json", new_callable=AsyncMock)
    async def test_success(self, mock_get):
        mock_get.return_value = {"coordinates": {"lat": 51.5, "lng": -0.1}}
        config = _weather_config()
        client = WeatherClient(config, what3words_api_key="key")
        result = await client._resolve_what3words("filled.count.soap")
        assert result is not None
        assert result.latitude == 51.5

    @pytest.mark.anyio
    @patch("pulse.assistant.info_sources._get_json", new_callable=AsyncMock)
    async def test_no_coords(self, mock_get):
        mock_get.return_value = {"coordinates": {}}
        config = _weather_config()
        client = WeatherClient(config, what3words_api_key="key")
        result = await client._resolve_what3words("bad.bad.bad")
        assert result is None

    @pytest.mark.anyio
    @patch("pulse.assistant.info_sources._get_json", new_callable=AsyncMock)
    async def test_none_payload(self, mock_get):
        mock_get.return_value = None
        config = _weather_config()
        client = WeatherClient(config, what3words_api_key="key")
        result = await client._resolve_what3words("bad.bad.bad")
        assert result is None


# ---------------------------------------------------------------------------
# WeatherClient._resolve_postal_code
# ---------------------------------------------------------------------------


class TestResolvePostalCode:
    @pytest.mark.anyio
    @patch("pulse.assistant.info_sources._get_json", new_callable=AsyncMock)
    async def test_success(self, mock_get):
        mock_get.return_value = {
            "places": [{"latitude": "37.7", "longitude": "-122.4", "place name": "SF", "state abbreviation": "CA"}]
        }
        config = _weather_config()
        client = WeatherClient(config, what3words_api_key=None)
        result = await client._resolve_postal_code("94102")
        assert result is not None
        assert result.display_name == "SF, CA"

    @pytest.mark.anyio
    @patch("pulse.assistant.info_sources._get_json", new_callable=AsyncMock)
    async def test_no_places(self, mock_get):
        mock_get.return_value = {"places": []}
        config = _weather_config()
        client = WeatherClient(config, what3words_api_key=None)
        result = await client._resolve_postal_code("00000")
        assert result is None

    @pytest.mark.anyio
    @patch("pulse.assistant.info_sources._get_json", new_callable=AsyncMock)
    async def test_invalid_coords(self, mock_get):
        mock_get.return_value = {"places": [{"latitude": "bad", "longitude": "bad"}]}
        config = _weather_config()
        client = WeatherClient(config, what3words_api_key=None)
        result = await client._resolve_postal_code("99999")
        assert result is None

    @pytest.mark.anyio
    @patch("pulse.assistant.info_sources._get_json", new_callable=AsyncMock)
    async def test_no_state(self, mock_get):
        mock_get.return_value = {"places": [{"latitude": "37.7", "longitude": "-122.4", "place name": "SF"}]}
        config = _weather_config()
        client = WeatherClient(config, what3words_api_key=None)
        result = await client._resolve_postal_code("94102")
        assert result is not None
        assert result.display_name == "SF"

    @pytest.mark.anyio
    @patch("pulse.assistant.info_sources._get_json", new_callable=AsyncMock)
    async def test_none_payload(self, mock_get):
        mock_get.return_value = None
        config = _weather_config()
        client = WeatherClient(config, what3words_api_key=None)
        result = await client._resolve_postal_code("11111")
        assert result is None


# ---------------------------------------------------------------------------
# SportsClient
# ---------------------------------------------------------------------------


class TestSportsClientGeneralHeadlines:
    async def test_cached(self):
        client = SportsClient(_sports_config())
        expected = [SportsHeadline(headline="Big game", description=None, league=None)]
        client._headline_cache.set("general:5:us", expected)
        result = await client.general_headlines()
        assert result is expected

    @pytest.mark.anyio
    @patch("pulse.assistant.info_sources._get_json", new_callable=AsyncMock)
    async def test_success(self, mock_get):
        mock_get.return_value = {
            "articles": [
                {"headline": "NFL Draft", "description": "Top picks", "categories": [{"sport": "football"}]},
            ]
        }
        client = SportsClient(_sports_config())
        result = await client.general_headlines()
        assert len(result) == 1
        assert result[0].headline == "NFL Draft"
        assert result[0].description == "Top picks"


class TestSportsClientLeagueHeadlines:
    async def test_cached(self):
        client = SportsClient(_sports_config())
        expected = [SportsHeadline(headline="Trade", description=None, league="nfl")]
        client._league_cache.set("league:nfl:5", expected)
        result = await client.league_headlines("nfl")
        assert result is expected

    async def test_unknown_league(self):
        client = SportsClient(_sports_config())
        result = await client.league_headlines("curling")
        assert result == []

    @pytest.mark.anyio
    @patch("pulse.assistant.info_sources._get_json", new_callable=AsyncMock)
    async def test_success(self, mock_get):
        mock_get.return_value = {
            "articles": [
                {"headline": "Mahomes shines", "description": "Great game"},
            ]
        }
        client = SportsClient(_sports_config())
        result = await client.league_headlines("nfl")
        assert len(result) == 1
        assert result[0].league == "nfl"


class TestSportsClientLeagueStandings:
    @pytest.mark.anyio
    @patch("pulse.assistant.info_sources._get_json", new_callable=AsyncMock)
    async def test_success_with_entries(self, mock_get):
        mock_get.return_value = {
            "children": [
                {
                    "name": "AFC",
                    "standings": {
                        "entries": [
                            {
                                "team": {"displayName": "Eagles", "name": "Eagles"},
                                "stats": [{"name": "overall", "displayValue": "14-3"}],
                            }
                        ]
                    },
                }
            ]
        }
        client = SportsClient(_sports_config())
        result = await client.league_standings("nfl")
        assert len(result) == 1
        assert result[0]["name"] == "Eagles"
        assert result[0]["record"] == "14-3"

    @pytest.mark.anyio
    @patch("pulse.assistant.info_sources._get_json", new_callable=AsyncMock)
    async def test_no_payload(self, mock_get):
        mock_get.return_value = None
        client = SportsClient(_sports_config())
        result = await client.league_standings("nfl")
        assert result == []

    async def test_unknown_league(self):
        client = SportsClient(_sports_config())
        result = await client.league_standings("curling")
        assert result == []


class TestSportsClientTeamSnapshot:
    @pytest.mark.anyio
    @patch("pulse.assistant.info_sources._get_json", new_callable=AsyncMock)
    async def test_found(self, mock_get):
        mock_get.return_value = {
            "sports": [
                {
                    "leagues": [
                        {
                            "teams": [
                                {
                                    "team": {
                                        "displayName": "Philadelphia Eagles",
                                        "nickname": "Eagles",
                                        "name": "Eagles",
                                        "shortDisplayName": "Eagles",
                                        "abbreviation": "PHI",
                                        "slug": "philadelphia-eagles",
                                        "record": {"items": [{"type": "total", "summary": "14-3"}]},
                                        "nextEvent": [
                                            {
                                                "name": "Eagles vs Cowboys",
                                                "shortName": "PHI vs DAL",
                                                "date": "2026-04-10",
                                                "status": {"type": {"completed": False, "description": "Scheduled"}},
                                                "competitions": [
                                                    {
                                                        "competitors": [
                                                            {"team": {"shortDisplayName": "Eagles"}},
                                                            {"team": {"shortDisplayName": "Cowboys"}},
                                                        ]
                                                    }
                                                ],
                                            }
                                        ],
                                        "previousEvent": [
                                            {
                                                "name": "Eagles vs Giants",
                                                "shortName": "PHI vs NYG",
                                                "date": "2026-03-28",
                                                "status": {"type": {"completed": True, "description": "Final"}},
                                                "competitions": [
                                                    {
                                                        "competitors": [
                                                            {"team": {"shortDisplayName": "Eagles"}},
                                                            {"team": {"shortDisplayName": "Giants"}},
                                                        ]
                                                    }
                                                ],
                                            }
                                        ],
                                    }
                                }
                            ]
                        }
                    ]
                }
            ]
        }
        client = SportsClient(_sports_config())
        result = await client.team_snapshot("eagles")
        assert result is not None
        assert result.name == "Philadelphia Eagles"
        assert result.record == "14-3"
        assert result.next_event is not None
        assert result.previous_event is not None

    @pytest.mark.anyio
    @patch("pulse.assistant.info_sources._get_json", new_callable=AsyncMock)
    async def test_not_found(self, mock_get):
        mock_get.return_value = {"sports": [{"leagues": [{"teams": []}]}]}
        client = SportsClient(_sports_config())
        result = await client.team_snapshot("nonexistent_team_xyz")
        assert result is None


class TestSportsClientLeagueTeams:
    async def test_cached(self):
        client = SportsClient(_sports_config())
        expected = [{"team": {"displayName": "Eagles"}}]
        client._team_cache.set("nfl", expected)
        result = await client._league_teams("nfl")
        assert result is expected

    @pytest.mark.anyio
    @patch("pulse.assistant.info_sources._get_json", new_callable=AsyncMock)
    async def test_success(self, mock_get):
        mock_get.return_value = {"sports": [{"leagues": [{"teams": [{"team": {"displayName": "Eagles"}}]}]}]}
        client = SportsClient(_sports_config())
        result = await client._league_teams("nfl")
        assert len(result) == 1

    async def test_unknown_league(self):
        client = SportsClient(_sports_config())
        result = await client._league_teams("curling")
        assert result == []


class TestSportsClientLeaguePath:
    def test_known(self):
        client = SportsClient(_sports_config())
        assert client._league_path("nfl") == ("football", "nfl")
        assert client._league_path("NBA") == ("basketball", "nba")

    def test_unknown(self):
        client = SportsClient(_sports_config())
        assert client._league_path("curling") is None


class TestSportsClientExtractHeadlines:
    def test_none_payload(self):
        client = SportsClient(_sports_config())
        assert client._extract_headlines(None, limit=5, league="nfl") == []

    def test_with_articles(self):
        client = SportsClient(_sports_config())
        payload = {
            "articles": [
                {"headline": "Big Win", "description": "Details"},
                {"headline": "", "description": "skip"},
                {"headline": "Another", "description": ""},
            ]
        }
        result = client._extract_headlines(payload, limit=5, league="nfl")
        assert len(result) == 2
        assert result[0].headline == "Big Win"
        assert result[1].description is None

    def test_with_results_key(self):
        client = SportsClient(_sports_config())
        payload = {
            "results": [
                {"title": "Using title key", "description": "Desc", "categories": [{"sport": "football"}]},
            ]
        }
        result = client._extract_headlines(payload, limit=5, league=None)
        assert len(result) == 1
        assert result[0].headline == "Using title key"
        assert result[0].league == "football"

    def test_limit_respected(self):
        client = SportsClient(_sports_config())
        payload = {"articles": [{"headline": f"H{i}"} for i in range(10)]}
        result = client._extract_headlines(payload, limit=3, league="nfl")
        assert len(result) == 3


class TestPickNextEvent:
    def test_with_next_event(self):
        team = {
            "nextEvent": [
                {
                    "name": "Game 1",
                    "shortName": "G1",
                    "date": "2026-04-10",
                    "status": {"type": {"completed": False, "description": "Scheduled"}},
                    "competitions": [{"competitors": []}],
                }
            ]
        }
        result = SportsClient._pick_next_event(team)
        assert result is not None
        assert result["name"] == "Game 1"

    def test_from_schedule(self):
        team = {
            "events": [
                {"name": "Past", "status": {"type": {"completed": True}}, "competitions": [{"competitors": []}]},
                {"name": "Future", "status": {"type": {"completed": False}}, "competitions": [{"competitors": []}]},
            ]
        }
        result = SportsClient._pick_next_event(team)
        assert result is not None
        assert result["name"] == "Future"

    def test_no_events(self):
        assert SportsClient._pick_next_event({}) is None

    def test_all_completed(self):
        team = {
            "events": [
                {"name": "Done", "status": {"type": {"completed": True}}, "competitions": [{"competitors": []}]},
            ]
        }
        assert SportsClient._pick_next_event(team) is None


class TestPickPreviousEvent:
    def test_with_previous_event(self):
        team = {
            "previousEvent": [
                {
                    "name": "Last Game",
                    "shortName": "LG",
                    "date": "2026-03-28",
                    "status": {"type": {"completed": True, "description": "Final"}},
                    "competitions": [{"competitors": []}],
                }
            ]
        }
        result = SportsClient._pick_previous_event(team)
        assert result is not None
        assert result["name"] == "Last Game"

    def test_from_schedule(self):
        team = {
            "events": [
                {"name": "First", "status": {"type": {"completed": True}}, "competitions": [{"competitors": []}]},
                {"name": "Second", "status": {"type": {"completed": True}}, "competitions": [{"competitors": []}]},
                {"name": "Upcoming", "status": {"type": {"completed": False}}, "competitions": [{"competitors": []}]},
            ]
        }
        result = SportsClient._pick_previous_event(team)
        assert result is not None
        assert result["name"] == "Second"

    def test_no_events(self):
        assert SportsClient._pick_previous_event({}) is None

    def test_none_completed_in_schedule(self):
        team = {
            "events": [
                {"name": "Upcoming", "status": {"type": {"completed": False}}, "competitions": [{"competitors": []}]},
            ]
        }
        assert SportsClient._pick_previous_event(team) is None


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


class TestSafeListFloat:
    def test_in_range_int(self):
        assert _safe_list_float([10, 20, 30], 1) == 20.0

    def test_in_range_float(self):
        assert _safe_list_float([1.5], 0) == 1.5

    def test_out_of_range(self):
        assert _safe_list_float([1, 2], 5) is None

    def test_string_convertible(self):
        assert _safe_list_float(["3.14"], 0) == 3.14

    def test_non_convertible(self):
        assert _safe_list_float(["abc"], 0) is None

    def test_none_value(self):
        assert _safe_list_float([None], 0) is None


class TestExpandGeocodeQueries:
    def test_empty(self):
        assert _expand_geocode_queries("") == []
        assert _expand_geocode_queries("   ") == []

    def test_simple_city(self):
        result = _expand_geocode_queries("Berlin")
        assert result == ["Berlin"]

    def test_city_state(self):
        result = _expand_geocode_queries("Roanoke, VA")
        assert "Roanoke, VA" in result
        assert "Roanoke" in result
        assert "Roanoke VA" in result
        assert "Roanoke, VA USA" in result
        assert "Roanoke, VA, USA" in result

    def test_city_state_country(self):
        result = _expand_geocode_queries("Portland, OR, US")
        assert "Portland, OR, US" in result
        assert "Portland" in result

    def test_deduplication(self):
        result = _expand_geocode_queries("Berlin")
        assert len(result) == len(set(r.lower() for r in result))


class TestDecodePlusCode:
    def test_olc_none(self):
        with patch("pulse.assistant.info_sources.olc", None):
            assert _decode_plus_code("849VCWC8+R9") is None

    def test_invalid_code(self):
        mock_olc = MagicMock()
        mock_olc.decode.side_effect = ValueError("invalid")
        with patch("pulse.assistant.info_sources.olc", mock_olc):
            assert _decode_plus_code("INVALID") is None

    def test_success(self):
        mock_decoded = MagicMock()
        mock_decoded.latitudeCenter = 37.7749
        mock_decoded.longitudeCenter = -122.4194
        mock_olc = MagicMock()
        mock_olc.decode.return_value = mock_decoded
        with patch("pulse.assistant.info_sources.olc", mock_olc):
            result = _decode_plus_code("849VCWC8+R9")
        assert result is not None
        assert result.latitude == 37.7749
        assert result.display_name == "849VCWC8+R9"

    def test_none_center(self):
        mock_decoded = MagicMock()
        mock_decoded.latitudeCenter = None
        mock_decoded.longitudeCenter = None
        mock_olc = MagicMock()
        mock_olc.decode.return_value = mock_decoded
        with patch("pulse.assistant.info_sources.olc", mock_olc):
            assert _decode_plus_code("849VCWC8+R9") is None


class TestExtractRecord:
    def test_overall_stat(self):
        entry = {"stats": [{"name": "overall", "displayValue": "10-5"}]}
        assert _extract_record(entry) == "10-5"

    def test_record_stat(self):
        entry = {"stats": [{"name": "record", "displayValue": "8-2"}]}
        assert _extract_record(entry) == "8-2"

    def test_display_name_overall(self):
        entry = {"stats": [{"name": "something", "displayName": "Overall", "displayValue": "7-3"}]}
        assert _extract_record(entry) == "7-3"

    def test_summary_fallback(self):
        entry = {"summary": "12-4"}
        assert _extract_record(entry) == "12-4"

    def test_no_record(self):
        entry = {"stats": [{"name": "points", "displayValue": "100"}]}
        assert _extract_record(entry) is None

    def test_empty_entry(self):
        assert _extract_record({}) is None

    def test_vs_overall(self):
        entry = {"stats": [{"name": "vsOverall", "displayValue": "5-1"}]}
        assert _extract_record(entry) == "5-1"


class TestTeamNameTokens:
    def test_basic(self):
        team = {
            "displayName": "Philadelphia Eagles",
            "nickname": "Eagles",
            "name": "Eagles",
            "shortDisplayName": "Eagles",
            "abbreviation": "PHI",
            "slug": "philadelphia-eagles",
        }
        tokens = _team_name_tokens(team)
        assert "philadelphia eagles" in tokens
        assert "eagles" in tokens
        assert "phi" in tokens
        assert "philadelphia-eagles" in tokens

    def test_empty_values_skipped(self):
        team = {"displayName": "", "name": "  "}
        tokens = _team_name_tokens(team)
        assert tokens == set()

    def test_non_string_ignored(self):
        team = {"displayName": 123}
        tokens = _team_name_tokens(team)
        assert tokens == set()


class TestTeamRecord:
    def test_total_item(self):
        team = {"record": {"items": [{"type": "total", "summary": "14-3"}]}}
        assert _team_record(team) == "14-3"

    def test_overall_fallback(self):
        team = {"record": {"overall": {"summary": "10-6"}}}
        assert _team_record(team) == "10-6"

    def test_no_record(self):
        assert _team_record({}) is None

    def test_no_total_item(self):
        team = {"record": {"items": [{"type": "away", "summary": "5-3"}]}}
        assert _team_record(team) is None


class TestSimplifyEvent:
    def test_basic(self):
        event = {
            "name": "Eagles vs Cowboys",
            "shortName": "PHI vs DAL",
            "date": "2026-04-10",
            "status": {"type": {"completed": False, "description": "Scheduled"}},
            "competitions": [
                {
                    "competitors": [
                        {"team": {"shortDisplayName": "Eagles"}},
                        {"team": {"shortDisplayName": "Cowboys"}},
                    ]
                }
            ],
        }
        result = _simplify_event(event)
        assert result["name"] == "Eagles vs Cowboys"
        assert result["matchup"] == "Eagles vs Cowboys"
        assert result["completed"] is False
        assert result["status"] == "Scheduled"

    def test_no_competitions(self):
        event = {"name": "Game", "status": {"type": {}}, "competitions": [{}]}
        result = _simplify_event(event)
        assert result["matchup"] == "Game"

    def test_empty_event(self):
        result = _simplify_event({})
        assert result["name"] is None
        assert result["completed"] is False


# ---------------------------------------------------------------------------
# InfoSources.__init__
# ---------------------------------------------------------------------------


class TestInfoSources:
    def test_init(self):
        config = _info_config()
        sources = InfoSources(config)
        assert isinstance(sources.news, NewsClient)
        assert isinstance(sources.weather, WeatherClient)
        assert isinstance(sources.sports, SportsClient)
        assert sources.config is config

    def test_what3words_key_passed_to_weather(self):
        config = _info_config(what3words_api_key="w3w-key")
        sources = InfoSources(config)
        assert sources.weather.what3words_api_key == "w3w-key"
