"""Tests for pulse.outdoor_weather (Open-Meteo parsing, icons, stale fallback)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pulse.outdoor_weather as ow
import pytest


def _payload(temp=43.9, code=0, is_day=1) -> dict:
    return {"current": {"time": "2026-10-22T08:45", "temperature_2m": temp, "weather_code": code, "is_day": is_day}}


def _observation(celsius=8.0, *, age_minutes=20, qc="V", unit="wmoUnit:degC") -> dict:
    stamp = (datetime.now(UTC) - timedelta(minutes=age_minutes)).isoformat()
    return {
        "properties": {"timestamp": stamp, "temperature": {"unitCode": unit, "value": celsius, "qualityControl": qc}}
    }


class _Feed:
    """Stateful MockTransport handler routing Open-Meteo and NWS URLs; swap payloads or fail."""

    def __init__(self) -> None:
        self.payload: dict = _payload()
        self.observation: dict = _observation()
        self.fail = False
        self.nws_fail = False
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.host == "api.weather.gov":
            if self.nws_fail:
                return httpx.Response(503)
            path = request.url.path
            if path.startswith("/points/"):
                return httpx.Response(
                    200,
                    json={"properties": {"observationStations": "https://api.weather.gov/gridpoints/XXX/1,1/stations"}},
                )
            if path.endswith("/stations"):
                return httpx.Response(
                    200, json={"features": [{"properties": {"stationIdentifier": "KXYZ", "name": "Test"}}]}
                )
            if path == "/stations/KXYZ/observations/latest":
                return httpx.Response(200, json=self.observation)
            return httpx.Response(404)
        if self.fail:
            return httpx.Response(503)
        return httpx.Response(200, json=self.payload)


@pytest.fixture
def feed(monkeypatch) -> _Feed:
    handler = _Feed()
    real_client = httpx.Client  # capture before patching to avoid self-recursion
    monkeypatch.setattr(ow.httpx, "Client", lambda *a, **k: real_client(transport=httpx.MockTransport(handler), **k))
    return handler


def test_fetch_rounds_temperature_and_describes_the_sky(feed: _Feed) -> None:
    reading = ow.OutdoorWeatherClient(40.71, -74.01, log=lambda _: None).fetch()
    assert reading is not None
    assert (reading["temp"], reading["unit"], reading["icon"], reading["condition"]) == (44, "F", "☀️", "Clear")


def test_fetch_asks_for_the_configured_unit(feed: _Feed) -> None:
    ow.OutdoorWeatherClient(40.71, -74.01, units="metric", log=lambda _: None).fetch()
    params = feed.requests[0].url.params
    assert params["temperature_unit"] == "celsius"
    assert params["current"] == "temperature_2m,weather_code,is_day"


@pytest.mark.parametrize(
    ("units", "expected"),
    [("auto", "fahrenheit"), ("imperial", "fahrenheit"), ("metric", "celsius"), (None, "fahrenheit")],
)
def test_temperature_unit_matches_the_assistant_forecast(units, expected) -> None:
    # `auto` is Fahrenheit for the spoken forecast too; the two must never disagree.
    assert ow.temperature_unit(units) == expected


def test_clear_night_shows_a_moon_not_a_sun() -> None:
    assert ow.describe(0, is_day=False) == ("\U0001f319", "Clear")
    assert ow.describe(0, is_day=True)[0] == "☀️"


def test_unknown_code_has_no_icon_or_label() -> None:
    assert ow.describe(None) == ("", "")
    assert ow.describe(42) == ("", "")


@pytest.mark.parametrize(
    "payload",
    [None, [], {}, {"current": {}}, {"current": {"temperature_2m": "warm"}}, {"current": {"temperature_2m": True}}],
)
def test_unusable_payloads_parse_to_none(payload) -> None:
    assert ow.parse_current(payload) is None


def test_missing_weather_code_still_shows_the_temperature() -> None:
    reading = ow.parse_current({"current": {"temperature_2m": 10.4}}, unit="celsius")
    assert reading is not None
    assert (reading["temp"], reading["unit"], reading["icon"], reading["condition"]) == (10, "C", "", "")


def test_fetch_error_reuses_a_recent_reading(feed: _Feed) -> None:
    client = ow.OutdoorWeatherClient(40.71, -74.01, log=lambda _: None)
    first = client.fetch()
    feed.fail = True
    assert client.fetch() == first


def test_fetch_error_drops_a_stale_reading(feed: _Feed, monkeypatch) -> None:
    # A morning temperature still on the wall in the afternoon is worse than none at all.
    client = ow.OutdoorWeatherClient(40.71, -74.01, log=lambda _: None)
    client.fetch()
    feed.fail = True
    real_time = ow.time.time
    monkeypatch.setattr(ow.time, "time", lambda: real_time() + ow.MAX_STALE_SECONDS + 1)
    assert client.fetch() is None


def _nws_client(**kwargs) -> ow.OutdoorWeatherClient:
    return ow.OutdoorWeatherClient(40.71, -74.01, use_nws=True, log=lambda _: None, **kwargs)


def test_station_temperature_wins_over_the_model(feed: _Feed) -> None:
    # The model ran ~8F warm on an inversion morning; a thermometer reading beats it.
    feed.payload = _payload(temp=54.3)
    feed.observation = _observation(8.0)  # 46.4F
    reading = _nws_client().fetch()
    assert reading is not None
    assert (reading["temp"], reading["source"]) == (46, "KXYZ")
    # The sky still comes from Open-Meteo.
    assert (reading["icon"], reading["condition"]) == ("\u2600\ufe0f", "Clear")


def test_station_temperature_in_celsius(feed: _Feed) -> None:
    feed.observation = _observation(8.4)
    reading = _nws_client(units="metric").fetch()
    assert reading is not None
    assert (reading["temp"], reading["unit"]) == (8, "C")


def test_station_is_looked_up_once(feed: _Feed) -> None:
    client = _nws_client()
    client.fetch()
    client.fetch()
    assert sum(r.url.path.startswith("/points/") for r in feed.requests) == 1


@pytest.mark.parametrize(
    "observation",
    [
        _observation(None),
        _observation(8.0, qc="X"),
        _observation(8.0, qc="Q"),
        _observation(8.0, age_minutes=180),
        _observation(46.4, unit="wmoUnit:degF"),
        {"properties": {}},
    ],
)
def test_unusable_observation_falls_back_to_the_model(feed: _Feed, observation: dict) -> None:
    feed.payload = _payload(temp=54.3)
    feed.observation = observation
    reading = _nws_client().fetch()
    assert reading is not None
    assert reading["temp"] == 54
    assert "source" not in reading


def test_nws_outage_falls_back_to_the_model_and_retries_the_lookup(feed: _Feed) -> None:
    feed.nws_fail = True
    client = _nws_client()
    reading = client.fetch()
    assert reading is not None and reading["temp"] == 44
    feed.nws_fail = False
    reading = client.fetch()
    assert reading is not None and reading["source"] == "KXYZ"


def test_station_alone_still_shows_a_temperature(feed: _Feed) -> None:
    feed.fail = True
    reading = _nws_client().fetch()
    assert reading is not None
    assert (reading["temp"], reading["icon"], reading["condition"]) == (46, "", "")


def test_nws_is_not_called_outside_the_us(feed: _Feed) -> None:
    ow.OutdoorWeatherClient(40.71, -74.01, log=lambda _: None).fetch()
    assert all(r.url.host != "api.weather.gov" for r in feed.requests)
