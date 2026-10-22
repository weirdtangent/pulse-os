"""Tests for pulse.outdoor_weather (Open-Meteo parsing, icons, stale fallback)."""

from __future__ import annotations

import httpx
import pulse.outdoor_weather as ow
import pytest


def _payload(temp=43.9, code=0, is_day=1) -> dict:
    return {"current": {"time": "2026-10-22T08:45", "temperature_2m": temp, "weather_code": code, "is_day": is_day}}


class _Feed:
    """Stateful MockTransport handler: swap the payload between fetches, or force an error."""

    def __init__(self) -> None:
        self.payload: dict = _payload()
        self.fail = False
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
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
