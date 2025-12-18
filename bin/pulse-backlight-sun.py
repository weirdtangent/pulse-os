#!/usr/bin/env python3
"""Adjust Pulse kiosk screen brightness based on sunrise/sunset."""

from __future__ import annotations

import os
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - fallback for Python < 3.9
    ZoneInfo = None  # type: ignore[assignment]

from astral import LocationInfo
from astral.sun import dawn, dusk, sun
from pulse import display
from pulse.location_resolver import ResolvedLocation, resolve_location

CONF_PATH = Path("/etc/pulse-backlight.conf")
DEFAULT_CONF: dict[str, str] = {
    "LAT": "0",
    "LON": "0",
    "DAY_BRIGHTNESS": "85",
    "NIGHT_BRIGHTNESS": "25",
    "TWILIGHT": "OFFICIAL",
    "BACKLIGHT": "/sys/class/backlight/11-0045",
}
VALID_TWILIGHT = {"OFFICIAL", "CIVIL", "NAUTICAL", "ASTRONOMICAL"}


_LOCATION_CACHE: ResolvedLocation | None = None


def read_conf(path: Path) -> tuple[float, float, int, int, str, str]:
    """Read the config file and return parsed values."""
    cfg = DEFAULT_CONF.copy()
    try:
        with path.open(encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                cfg[key.strip().upper()] = value.strip()
    except FileNotFoundError:
        # Will fall back to defaults and any env overrides.
        pass

    def _percent(raw: str | None, default: int) -> int:
        try:
            return max(0, min(100, int(float(raw if raw is not None else default))))
        except (ValueError, TypeError):
            return default

    global _LOCATION_CACHE
    if _LOCATION_CACHE is None:
        _LOCATION_CACHE = resolve_location(
            os.environ.get("PULSE_LOCATION"),
            language=os.environ.get("PULSE_WEATHER_LANGUAGE", "en"),
            what3words_api_key=os.environ.get("WHAT3WORDS_API_KEY"),
        )

    if _LOCATION_CACHE:
        lat = _LOCATION_CACHE.latitude
        lon = _LOCATION_CACHE.longitude
    else:
        lat = float(cfg["LAT"])
        lon = float(cfg["LON"])
    # Support legacy DAY/NIGHT for backward compatibility
    day_brightness = _percent(os.environ.get("PULSE_DAY_BRIGHTNESS") or cfg.get("DAY_BRIGHTNESS") or cfg.get("DAY"), 85)
    night_brightness = _percent(
        os.environ.get("PULSE_NIGHT_BRIGHTNESS") or cfg.get("NIGHT_BRIGHTNESS") or cfg.get("NIGHT"), 25
    )
    twilight = (os.environ.get("PULSE_TWILIGHT_MODE") or cfg["TWILIGHT"]).upper()
    if twilight not in VALID_TWILIGHT:
        twilight = "OFFICIAL"
    backlight = os.environ.get("PULSE_BACKLIGHT_DEVICE") or cfg["BACKLIGHT"]
    return lat, lon, day_brightness, night_brightness, twilight, backlight


def detect_tz() -> timezone | ZoneInfo:
    """Detect the best timezone for the host device."""
    tzname: str | None = None
    tz_env = os.environ.get("TZ")
    candidates = [tz_env] if tz_env else []
    candidates.append("/etc/timezone")

    for candidate in candidates:
        if not candidate:
            continue
        candidate_path = Path(candidate)
        try:
            if candidate_path.is_file():
                tzname = candidate_path.read_text(encoding="utf-8").strip()
                break
            tzname = candidate
            break
        except OSError:
            continue

    if tzname and ZoneInfo:
        try:
            return ZoneInfo(tzname)
        except (LookupError, ValueError):
            pass

    try:
        return datetime.now().astimezone().tzinfo or datetime.UTC
    except OSError:
        return datetime.UTC


def set_backlight(device_dir: str, percent: int) -> None:
    """Write the scaled brightness value to the backlight device."""
    display.set_brightness(percent, device_path=device_dir)


def _twilight_boundaries(
    location: LocationInfo,
    tzinfo: timezone | ZoneInfo,
    twilight_mode: str,
    date_obj: date,
) -> tuple[datetime, datetime]:
    if twilight_mode == "OFFICIAL":
        sun_data = sun(location.observer, date=date_obj, tzinfo=tzinfo)
        return sun_data["sunrise"], sun_data["sunset"]
    depression = {"CIVIL": 6, "NAUTICAL": 12, "ASTRONOMICAL": 18}[twilight_mode]
    start = dawn(location.observer, date=date_obj, tzinfo=tzinfo, depression=depression)
    end = dusk(location.observer, date=date_obj, tzinfo=tzinfo, depression=depression)
    return start, end


def next_events(
    lat: float,
    lon: float,
    tzinfo: timezone | ZoneInfo,
    twilight_mode: str,
    now: datetime | None = None,
) -> tuple[bool, datetime]:
    """Return whether it is daytime and the datetime of the next transition."""
    current = now or datetime.now(tzinfo)
    location = LocationInfo(latitude=lat, longitude=lon)
    try:
        sunrise, sunset = _twilight_boundaries(location, tzinfo, twilight_mode, current.date())
    except Exception:  # noqa: BLE001 - Astral raises generic Exception on polar nights
        morning = current.replace(hour=8, minute=0, second=0, microsecond=0)
        evening = current.replace(hour=18, minute=0, second=0, microsecond=0)
        next_transition = evening if current < evening else morning + timedelta(days=1)
        return morning <= current < evening, next_transition

    if sunrise <= current < sunset:
        return True, sunset
    if current < sunrise:
        return False, sunrise

    next_sunrise, _ = _twilight_boundaries(location, tzinfo, twilight_mode, current.date() + timedelta(days=1))
    return False, next_sunrise


def main() -> None:
    tzinfo = detect_tz()
    is_daytime: bool | None = None
    last_target: int | None = None
    last_device: str | None = None

    while True:
        lat, lon, day_brightness, night_brightness, twilight, backlight_device = read_conf(CONF_PATH)
        now = datetime.now(tzinfo)
        currently_daylight, next_transition = next_events(lat, lon, tzinfo, twilight, now)

        target_brightness = day_brightness if currently_daylight else night_brightness

        # Apply brightness on state change or when target/device changes
        if is_daytime != currently_daylight or target_brightness != last_target or backlight_device != last_device:
            try:
                set_backlight(backlight_device, target_brightness)
            except OSError:
                # Backlight not ready; retry soon.
                pass
            is_daytime = currently_daylight
            last_target = target_brightness
            last_device = backlight_device
        sleep_seconds = max(30, min(24 * 3600, int((next_transition - now).total_seconds()) + 2))
        time.sleep(sleep_seconds)


if __name__ == "__main__":
    main()
