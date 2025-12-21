from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import httpx

try:  # Optional dependency
    from openlocationcode import openlocationcode as olc
except ImportError:  # pragma: no cover - optional dependency
    olc = None

LAT_LON_PATTERN = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$")
WHAT3WORDS_PATTERN = re.compile(r"^[a-z]+(?:\.[a-z]+){2}$")
POSTAL_CODE_PATTERN = re.compile(r"^\s*(\d{5})(?:-\d{4})?\s*$")


@dataclass(slots=True)
class ResolvedLocation:
    latitude: float
    longitude: float
    display_name: str
    country_code: str | None = None
    timezone: str | None = None


_CACHE: dict[str, ResolvedLocation] = {}


def _decode_plus_code(raw: str) -> ResolvedLocation | None:
    if not olc:
        return None
    try:
        code = olc.recoverNearest(raw.upper(), 0, 0)
        decoded = olc.decode(code)
    except Exception:  # noqa: BLE001
        return None
    return ResolvedLocation(
        latitude=decoded.latitudeCenter,
        longitude=decoded.longitudeCenter,
        display_name=raw,
        country_code=None,
        timezone=None,
    )


def _http_get_json(url: str, params: dict[str, Any]) -> dict[str, Any] | None:
    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.get(url, params=params)
            response.raise_for_status()
            return response.json()
    except httpx.HTTPError:
        return None


def resolve_location(
    raw: str | None,
    *,
    language: str = "en",
    what3words_api_key: str | None = None,
) -> ResolvedLocation | None:
    """Resolve a user-friendly location string to coordinates.

    Supports:
    - "lat,lon"
    - ZIP/postal codes (US)
    - City names ("City, ST")
    - Google Plus Codes
    - what3words (with WHAT3WORDS_API_KEY)
    """
    if not raw:
        return None
    normalized = raw.strip()
    if not normalized:
        return None
    cached = _CACHE.get(normalized)
    if cached:
        return cached

    match = LAT_LON_PATTERN.match(normalized)
    if match:
        lat = float(match.group(1))
        lon = float(match.group(2))
        result = ResolvedLocation(
            latitude=lat,
            longitude=lon,
            display_name=f"{lat:.2f}, {lon:.2f}",
            country_code=None,
            timezone=None,
        )
        _CACHE[normalized] = result
        return result

    if WHAT3WORDS_PATTERN.match(normalized.lower()) and what3words_api_key:
        payload = _http_get_json(
            "https://api.what3words.com/v3/convert-to-coordinates",
            params={"words": normalized, "key": what3words_api_key},
        )
        if payload:
            coords = payload.get("coordinates") or {}
            if "lat" in coords and "lng" in coords:
                result = ResolvedLocation(
                    latitude=float(coords["lat"]),
                    longitude=float(coords["lng"]),
                    display_name=normalized,
                    country_code=(payload.get("country") or None),
                    timezone=None,
                )
                _CACHE[normalized] = result
                return result

    postal_match = POSTAL_CODE_PATTERN.match(normalized)
    if postal_match:
        payload = _http_get_json(f"https://api.zippopotam.us/us/{postal_match.group(1)}", params={})
        if payload:
            places = payload.get("places") or []
            if places:
                place = places[0]
                try:
                    lat = float(place["latitude"])
                    lon = float(place["longitude"])
                except (KeyError, TypeError, ValueError):
                    pass
                else:
                    city = place.get("place name") or postal_match.group(1)
                    state = place.get("state abbreviation")
                    display = f"{city}, {state}" if state else city
                    result = ResolvedLocation(
                        latitude=lat,
                        longitude=lon,
                        display_name=display,
                        country_code=(payload.get("country abbreviation") or payload.get("country")) or None,
                        timezone=None,
                    )
                    _CACHE[normalized] = result
                    return result

    if "+" in normalized and not normalized.startswith("http"):
        decoded = _decode_plus_code(normalized)
        if decoded:
            _CACHE[normalized] = decoded
            return decoded

    payload = _http_get_json(
        "https://geocoding-api.open-meteo.com/v1/search",
        params={"name": normalized, "count": 1, "language": language},
    )
    if payload:
        results = payload.get("results") or []
        if results:
            entry = results[0]
            result = ResolvedLocation(
                latitude=float(entry.get("latitude")),
                longitude=float(entry.get("longitude")),
                display_name=entry.get("name") or normalized,
                country_code=(entry.get("country_code") or entry.get("country") or "").lower() or None,
                timezone=entry.get("timezone") or None,
            )
            _CACHE[normalized] = result
            return result

    return None


def resolve_location_defaults(
    raw: str | None,
    *,
    language: str = "en",
    what3words_api_key: str | None = None,
) -> ResolvedLocation | None:
    """Resolve location with sane defaults for downstream consumers."""
    return resolve_location(raw, language=language, what3words_api_key=what3words_api_key)
