#!/usr/bin/env python3
"""Adjust Pulse kiosk screen brightness and audio volume based on sunrise/sunset."""

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
from pulse import audio, display

CONF_PATH = Path("/etc/pulse-backlight.conf")
DEFAULT_CONF: dict[str, str] = {
    "LAT": "0",
    "LON": "0",
    "DAY_BRIGHTNESS": "85",
    "NIGHT_BRIGHTNESS": "25",
    "DAY_VOLUME": "70",
    "NIGHT_VOLUME": "30",
    "TWILIGHT": "OFFICIAL",
    "BACKLIGHT": "/sys/class/backlight/11-0045",
}
VALID_TWILIGHT = {"OFFICIAL", "CIVIL", "NAUTICAL", "ASTRONOMICAL"}


def read_conf(path: Path) -> tuple[float, float, int, int, int, int, str, str]:
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
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"Backlight config not found at {path}") from exc

    lat = float(cfg["LAT"])
    lon = float(cfg["LON"])
    # Support legacy DAY/NIGHT for backward compatibility
    day_brightness = max(0, min(100, int(cfg.get("DAY_BRIGHTNESS", cfg.get("DAY", "85")))))
    night_brightness = max(0, min(100, int(cfg.get("NIGHT_BRIGHTNESS", cfg.get("NIGHT", "25")))))
    day_volume = max(0, min(100, int(cfg.get("DAY_VOLUME", "70"))))
    night_volume = max(0, min(100, int(cfg.get("NIGHT_VOLUME", "30"))))
    twilight = cfg["TWILIGHT"].upper()
    if twilight not in VALID_TWILIGHT:
        twilight = "OFFICIAL"
    backlight = cfg["BACKLIGHT"]
    return lat, lon, day_brightness, night_brightness, day_volume, night_volume, twilight, backlight


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


def set_volume(percent: int) -> None:
    """Set audio volume using pactl."""
    audio.set_volume(percent)  # Fails silently if audio not available


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
    lat, lon, day_brightness, night_brightness, day_volume, night_volume, twilight, backlight_device = read_conf(
        CONF_PATH
    )
    tzinfo = detect_tz()
    is_daytime: bool | None = None

    while True:
        now = datetime.now(tzinfo)
        currently_daylight, next_transition = next_events(lat, lon, tzinfo, twilight, now)
        target_brightness = day_brightness if currently_daylight else night_brightness
        target_volume = day_volume if currently_daylight else night_volume
        if is_daytime != currently_daylight:
            try:
                set_backlight(backlight_device, target_brightness)
            except OSError:
                # Backlight not ready; retry soon.
                pass
            # Set volume (fails silently if audio not available)
            set_volume(target_volume)
            is_daytime = currently_daylight
        sleep_seconds = max(30, min(24 * 3600, int((next_transition - now).total_seconds()) + 2))
        time.sleep(sleep_seconds)


if __name__ == "__main__":
    main()
