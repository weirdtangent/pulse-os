"""Current outdoor conditions for the clock card's title row ("Office · ☀️ 44°F · Sunny").

Source: Open-Meteo's `current` block (https://api.open-meteo.com/v1/forecast), the same
keyless service behind the assistant's forecast in pulse/assistant/info_sources.py. That
one is async and lives in the assistant process; the overlay runs in the kiosk listener,
so this is a small synchronous client polled from a daemon thread there, mirroring
pulse/weather_alerts.py.

Open-Meteo refreshes `current` every 15 minutes, so polling faster than that only
re-reads the same number.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

import httpx

LOGGER = logging.getLogger("pulse.outdoor_weather")

DEFAULT_URL = "https://api.open-meteo.com/v1/forecast"
# A reading older than this is dropped rather than shown. A wall display quietly sitting
# on a morning temperature all afternoon is worse than showing no temperature at all.
MAX_STALE_SECONDS = 3600

# WMO weather code -> (day icon, night icon, label). Grouped the same way as
# _weather_icon_key in pulse/assistant/info_service.py so the spoken forecast and the
# clock never describe the same sky differently. Labels are kept short: they share one
# line with the room name above a clock that is already as wide as its cell.
_CONDITIONS: tuple[tuple[frozenset[int], str, str, str], ...] = (
    (frozenset({0}), "☀️", "\U0001f319", "Clear"),
    (frozenset({1}), "\U0001f324️", "\U0001f319", "Mostly clear"),
    (frozenset({2}), "⛅", "☁️", "Partly cloudy"),
    (frozenset({3}), "☁️", "☁️", "Cloudy"),
    (frozenset({45, 48}), "\U0001f32b️", "\U0001f32b️", "Fog"),
    (frozenset({51, 53, 55}), "\U0001f326️", "\U0001f327️", "Drizzle"),
    (frozenset({56, 57, 66, 67}), "\U0001f328️", "\U0001f328️", "Sleet"),
    (frozenset({61, 63, 80, 81}), "\U0001f327️", "\U0001f327️", "Rain"),
    (frozenset({65, 82}), "\U0001f327️", "\U0001f327️", "Heavy rain"),
    (frozenset({71, 73, 75, 77, 85, 86}), "❄️", "❄️", "Snow"),
    (frozenset({95, 96, 99}), "⛈️", "⛈️", "Storms"),
)


def describe(code: int | None, *, is_day: bool = True) -> tuple[str, str]:
    """Return (icon, label) for a WMO weather code; ("", "") when it is unknown."""
    if code is None:
        return "", ""
    for codes, day_icon, night_icon, label in _CONDITIONS:
        if code in codes:
            return (day_icon if is_day else night_icon), label
    return "", ""


def temperature_unit(units: str | None) -> str:
    """Map PULSE_WEATHER_UNITS onto Open-Meteo's unit. `auto` means Fahrenheit, as it
    does for the assistant's forecast (pulse/assistant/info_sources.py)."""
    return "celsius" if (units or "").strip().lower() == "metric" else "fahrenheit"


class OutdoorWeatherClient:
    """Fetches the current temperature and sky for one point."""

    def __init__(
        self,
        latitude: float,
        longitude: float,
        *,
        units: str | None = "auto",
        base_url: str = DEFAULT_URL,
        timeout: float = 10.0,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self.latitude = float(latitude)
        self.longitude = float(longitude)
        self.unit = temperature_unit(units)
        self.base_url = (base_url or DEFAULT_URL).rstrip("/")
        self.timeout = timeout
        self._log = log or LOGGER.info
        self._client: httpx.Client | None = None
        self._cache: dict[str, Any] | None = None

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def _get_client(self) -> httpx.Client:
        # Only the polling thread calls fetch(), so one reused client is safe.
        if self._client is None:
            self._client = httpx.Client(timeout=self.timeout, follow_redirects=True)
        return self._client

    def fetch(self) -> dict[str, Any] | None:
        """Return the latest reading, never raising.

        On a fetch error the previous reading is reused until it is MAX_STALE_SECONDS
        old, so a network blip doesn't blink the temperature off and back on.
        """
        try:
            reading = self._request()
        except (httpx.HTTPError, ValueError) as exc:
            self._log(f"outdoor-weather: fetch failed: {exc}")
            reading = None
        if reading is None:
            return self._stale_fallback()
        self._cache = reading
        return reading

    def _stale_fallback(self) -> dict[str, Any] | None:
        if self._cache and time.time() - float(self._cache.get("fetched_at") or 0) < MAX_STALE_SECONDS:
            return self._cache
        self._cache = None
        return None

    def _request(self) -> dict[str, Any] | None:
        response = self._get_client().get(
            self.base_url,
            params={
                "latitude": f"{self.latitude:.4f}",
                "longitude": f"{self.longitude:.4f}",
                "current": "temperature_2m,weather_code,is_day",
                "temperature_unit": self.unit,
                "timezone": "auto",
            },
        )
        response.raise_for_status()
        return parse_current(response.json(), unit=self.unit)


def parse_current(payload: Any, *, unit: str = "fahrenheit") -> dict[str, Any] | None:
    """Turn an Open-Meteo response into the overlay's reading, or None if unusable."""
    if not isinstance(payload, dict):
        return None
    current = payload.get("current")
    if not isinstance(current, dict):
        return None
    temperature = current.get("temperature_2m")
    if not isinstance(temperature, (int, float)) or isinstance(temperature, bool):
        return None
    code = current.get("weather_code")
    code = int(code) if isinstance(code, (int, float)) and not isinstance(code, bool) else None
    # Missing is_day means daytime: a sun at night is a smaller error than no icon.
    is_day = current.get("is_day") != 0
    icon, condition = describe(code, is_day=is_day)
    return {
        "temp": round(float(temperature)),
        "unit": "C" if unit == "celsius" else "F",
        "icon": icon,
        "condition": condition,
        "fetched_at": time.time(),
    }
