"""Shared datetime parsing and manipulation utilities."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

# Constants used by datetime parsing
_RELATIVE_DAY_RULES: tuple[tuple[str, int, int], ...] = (
    ("day after tomorrow", 2, 9),
    ("tomorrow", 1, 9),
    ("today", 0, 9),
    ("tonight", 0, 21),
)

_WEEKDAY_NAMES = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}

_WEEKDAY_PATTERN = re.compile(
    r"\b(?:(next|this|every|each|on|upcoming)\s+)?(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
    re.IGNORECASE,
)

_TIME_PHRASE_STOP_WORDS = (
    ",",
    " every ",
    " each ",
    " repeating ",
    " repeat ",
    " starting ",
    " start ",
    " beginning ",
    " for ",
    " during ",
    " over ",
    " to ",
    " so ",
    " then ",
    " that ",
)

_TIME_KEYWORDS = {
    "noon": (12, 0),
    "midday": (12, 0),
    "midnight": (0, 0),
    "morning": (9, 0),
    "afternoon": (15, 0),
    "evening": (18, 0),
}


def utc_now() -> datetime:
    """Get current datetime in UTC."""
    return datetime.now(UTC)


def local_now() -> datetime:
    """Get current datetime in local timezone."""
    return datetime.now().astimezone()


def ensure_utc(dt: datetime) -> datetime:
    """Ensure datetime is in UTC timezone."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def ensure_local(dt: datetime) -> datetime:
    """Ensure datetime is in local timezone."""
    if dt.tzinfo is None:
        return dt.astimezone()
    return dt.astimezone()


def parse_iso_duration(value: str) -> float:
    """Parse ISO8601 duration string (PT#H#M#S format) into seconds."""
    value = value.lstrip("pP")
    if not value.startswith("T") and "T" not in value:
        raise ValueError("Invalid ISO duration")
    value = value.lstrip("tT")
    hours = minutes = seconds = 0.0
    number = ""
    for char in value:
        if char.isdigit() or char == ".":
            number += char
            continue
        if not number:
            continue
        if char in ("h", "H"):
            hours = float(number)
        elif char in ("m", "M"):
            minutes = float(number)
        elif char in ("s", "S"):
            seconds = float(number)
        number = ""
    if number:
        seconds = float(number)
    return hours * 3600 + minutes * 60 + seconds


def parse_duration_seconds(value: str) -> float:
    """Parse duration string into seconds. Supports various formats like '5m', '10s', 'PT5M', etc."""
    text = value.strip().lower()
    if not text:
        return 0.0
    multipliers = {
        "ms": 0.001,
        "s": 1,
        "sec": 1,
        "secs": 1,
        "m": 60,
        "min": 60,
        "mins": 60,
        "h": 3600,
        "hr": 3600,
        "hrs": 3600,
    }
    for suffix, multiplier in multipliers.items():
        if text.endswith(suffix):
            try:
                number = float(text[: -len(suffix)])
            except ValueError:
                return 0.0
            return number * multiplier
    if text.startswith("pt") or text.startswith("p"):
        # ISO8601 duration limited parsing (PT#H#M#S)
        try:
            return parse_iso_duration(text)
        except ValueError:
            return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def parse_time_of_day(phrase: str | None) -> tuple[int, int] | None:
    """Parse time of day phrase into (hour, minute) tuple. Returns None if invalid."""
    if not phrase:
        return None
    cleaned = phrase.strip().lower()
    if not cleaned:
        return None
    keyword = _TIME_KEYWORDS.get(cleaned)
    if keyword:
        return keyword
    if cleaned.endswith(" o'clock"):
        cleaned = cleaned[: -len(" o'clock")].strip()
    match = re.match(r"^(\d{1,2})(?::(\d{2}))?\s*(am|pm)?$", cleaned)
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    suffix = match.group(3)
    if suffix:
        if hour == 12:
            hour = 0
        if suffix == "pm":
            hour += 12
    if hour >= 24 or minute >= 60:
        return None
    return hour, minute


def parse_time_string(value: str) -> tuple[int, int]:
    """Parse time string into (hour, minute) tuple. Raises ValueError if invalid."""
    result = parse_time_of_day(value)
    if result is None:
        raise ValueError("Invalid time format")
    return result


def _extract_time_phrase(text: str) -> str:  # noqa: PLR0911
    """Extract time phrase from text, removing prefixes and stop words."""
    trimmed = text.strip(" ,")
    if not trimmed:
        return ""
    lowered = trimmed.lower()
    for prefix in ("at ", "around ", "by ", "about "):
        if lowered.startswith(prefix):
            trimmed = trimmed[len(prefix) :].lstrip(" ,")
            lowered = trimmed.lower()
            break
    stop_positions = [lowered.find(token) for token in _TIME_PHRASE_STOP_WORDS if lowered.find(token) != -1]
    if stop_positions:
        stop_index = min(stop_positions)
        trimmed = trimmed[:stop_index]
    return trimmed.strip(" ,")


def _apply_time_phrase(reference: datetime, phrase: str, default_hour: int) -> datetime:
    """Apply time phrase to a reference datetime."""
    time_match = parse_time_of_day(phrase)
    if time_match is None:
        hour, minute = default_hour, 0
    else:
        hour, minute = time_match
    return reference.replace(hour=hour, minute=minute, second=0, microsecond=0)


def _parse_relative_datetime(original: str, lowered: str) -> datetime | None:
    """Parse relative datetime phrases like 'tomorrow', 'next Monday', etc."""
    for keyword, day_offset, default_hour in _RELATIVE_DAY_RULES:
        if not lowered.startswith(keyword):
            continue
        remainder = original[len(keyword) :].strip()
        if remainder.lower().startswith("at "):
            remainder = remainder[3:].strip()
        time_phrase = _extract_time_phrase(remainder)
        base = local_now() + timedelta(days=day_offset)
        combined = _apply_time_phrase(base, time_phrase, default_hour)
        return ensure_utc(combined)
    weekday_candidate = _parse_weekday_reference(original, lowered)
    if weekday_candidate is not None:
        return weekday_candidate
    return None


def _parse_weekday_reference(original: str, lowered: str) -> datetime | None:
    """Parse weekday references like 'next Monday', 'this Friday', etc."""
    match = _WEEKDAY_PATTERN.search(lowered)
    if not match:
        return None
    prefix = (match.group(1) or "").lower()
    day_name = match.group(2).lower()
    target_weekday = _WEEKDAY_NAMES.get(day_name)
    if target_weekday is None:
        return None
    remainder = original[match.end(2) :].strip()
    time_phrase = _extract_time_phrase(remainder)
    base = local_now()
    days_ahead = (target_weekday - base.weekday()) % 7
    if prefix in {"next", "upcoming"} and days_ahead == 0:
        days_ahead = 7
    target_date = base + timedelta(days=days_ahead)
    combined = _apply_time_phrase(target_date, time_phrase, 9)
    if combined <= base:
        combined = combined + timedelta(days=7)
    return ensure_utc(combined)


def parse_datetime(text: str) -> datetime | None:
    """Parse datetime from text. Supports ISO format, relative phrases, and duration strings."""
    text = text.strip()
    if not text:
        return None
    lowered = text.lower()
    relative = _parse_relative_datetime(text, lowered)
    if relative is not None:
        return relative
    # Max duration: ~100 years in seconds (avoids timedelta overflow)
    max_duration = 100 * 365 * 24 * 3600
    if lowered.startswith("in "):
        duration = parse_duration_seconds(lowered[3:])
        if duration <= 0 or duration > max_duration:
            return None
        return utc_now() + timedelta(seconds=duration)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        duration = parse_duration_seconds(text)
        if 0 < duration <= max_duration:
            return utc_now() + timedelta(seconds=duration)
        return None
    return ensure_utc(parsed)


def combine_time(reference: datetime, time_str: str) -> datetime:
    """Combine a reference datetime with a time string (HH:MM format)."""
    try:
        hour, minute = parse_time_string(time_str)
    except ValueError:
        return reference.replace(second=0, microsecond=0)
    return reference.replace(hour=hour, minute=minute, second=0, microsecond=0)
