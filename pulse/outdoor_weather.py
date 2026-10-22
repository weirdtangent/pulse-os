"""Current outdoor conditions for the clock card's title row ("Office · ☀️ 44°F · Clear").

Two sources, because neither is good enough alone:

- The TEMPERATURE comes from the nearest NWS observation station when there is one (US
  only, api.weather.gov, the same keyless API as pulse/weather_alerts.py). That is a real
  thermometer reading. Open-Meteo's number is model output for a grid cell, and on a clear
  morning with a valley inversion it ran ~8°F warm against the airport three miles away:
  the cell for a ZIP centroid on a hillside sat 70 m higher than the valley floor.
- The SKY (icon + condition) and the fallback temperature come from Open-Meteo's
  `current` block, the keyless service behind the assistant's forecast in
  pulse/assistant/info_sources.py. Sky cover doesn't swing over a few miles the way an
  inversion swings temperature, and Open-Meteo works everywhere.

The assistant's client is async and lives in another process; the overlay runs in the
kiosk listener, so this is a small synchronous client polled from a daemon thread there,
mirroring pulse/weather_alerts.py. Both upstreams refresh at most every 15 minutes or so.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any

import httpx

LOGGER = logging.getLogger("pulse.outdoor_weather")

DEFAULT_URL = "https://api.open-meteo.com/v1/forecast"
NWS_URL = "https://api.weather.gov"
# Same identification as the alerts client; NWS asks every caller for one.
DEFAULT_CONTACT = "https://github.com/weirdtangent/pulse-os"
# Stations report hourly (more often in changing weather). Past two hours the station is
# probably down, and the model is the better guess.
MAX_OBSERVATION_AGE_SECONDS = 7200
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
        use_nws: bool = False,
        contact: str | None = None,
        timeout: float = 10.0,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self.latitude = float(latitude)
        self.longitude = float(longitude)
        self.unit = temperature_unit(units)
        self.base_url = (base_url or DEFAULT_URL).rstrip("/")
        self.use_nws = use_nws
        self.contact = (contact or "").strip() or DEFAULT_CONTACT
        self.timeout = timeout
        # Nearest NWS station id, looked up once; "" until found. A failed lookup is
        # retried on the next poll rather than giving up on observations for good.
        self._station = ""
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
            self._client = httpx.Client(
                timeout=self.timeout,
                headers={"User-Agent": f"pulse-os ({self.contact})"},
                follow_redirects=True,
            )
        return self._client

    def fetch(self) -> dict[str, Any] | None:
        """Return the latest reading, never raising.

        The station's measured temperature wins over the model's when there is a fresh
        one. With both sources down, the previous reading is reused until it is
        MAX_STALE_SECONDS old, so a network blip doesn't blink the temperature off and on.
        """
        try:
            reading = self._request()
        except (httpx.HTTPError, ValueError) as exc:
            self._log(f"outdoor-weather: open-meteo fetch failed: {exc}")
            reading = None
        observed = self._observed_temperature() if self.use_nws else None
        if observed is not None:
            reading = reading or {"icon": "", "condition": ""}
            reading.update(
                temp=round(observed),
                unit="C" if self.unit == "celsius" else "F",
                source=self._station,
                fetched_at=time.time(),
            )
        if reading is None:
            return self._stale_fallback()
        self._cache = reading
        return reading

    def _observed_temperature(self) -> float | None:
        try:
            if not self._station:
                self._station = self._nearest_station()
            if not self._station:
                return None
            response = self._get_client().get(f"{NWS_URL}/stations/{self._station}/observations/latest")
            response.raise_for_status()
            return parse_observation(response.json(), unit=self.unit)
        except (httpx.HTTPError, ValueError) as exc:
            self._log(f"outdoor-weather: nws observation failed: {exc}")
            return None

    def _nearest_station(self) -> str:
        client = self._get_client()
        point = client.get(f"{NWS_URL}/points/{self.latitude:.4f},{self.longitude:.4f}")
        point.raise_for_status()
        stations_url = ((point.json() or {}).get("properties") or {}).get("observationStations")
        if not isinstance(stations_url, str) or not stations_url.startswith(NWS_URL):
            return ""
        # NWS returns the grid's stations nearest first.
        stations = client.get(stations_url, params={"limit": 1})
        stations.raise_for_status()
        features = (stations.json() or {}).get("features") or []
        props = (features[0].get("properties") or {}) if features and isinstance(features[0], dict) else {}
        station = str(props.get("stationIdentifier") or "")
        if station:
            self._log(f"outdoor-weather: using NWS station {station} ({props.get('name', '')})")
        return station

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


def parse_observation(payload: Any, *, unit: str = "fahrenheit", now: float | None = None) -> float | None:
    """The temperature from an NWS latest-observation response, in `unit`; None when the
    station sent no temperature (they often omit it), flagged it as bad, or is too old."""
    props = payload.get("properties") if isinstance(payload, dict) else None
    if not isinstance(props, dict):
        return None
    temperature = props.get("temperature")
    if not isinstance(temperature, dict):
        return None
    value = temperature.get("value")
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    # NWS quality control: "X" failed, "Q" questioned. Anything else has passed or is
    # still pending, and pending is the normal state of the newest observation.
    if temperature.get("qualityControl") in {"X", "Q"}:
        return None
    try:
        observed_at = datetime.fromisoformat(str(props.get("timestamp"))).timestamp()
    except ValueError:
        return None
    if (now if now is not None else time.time()) - observed_at > MAX_OBSERVATION_AGE_SECONDS:
        return None
    unit_code = str(temperature.get("unitCode") or "")
    celsius = float(value) if unit_code.endswith("degC") else None
    if celsius is None:
        return None
    return celsius if unit == "celsius" else celsius * 9 / 5 + 32


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
