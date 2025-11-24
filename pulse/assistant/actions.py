"""Action parsing and execution helpers."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .schedule_service import PlaybackConfig, parse_day_tokens

if TYPE_CHECKING:  # pragma: no cover
    from .home_assistant import HomeAssistantClient
    from .schedule_service import ScheduleService
    from .scheduler import AssistantScheduler


@dataclass(frozen=True)
class ActionDefinition:
    slug: str
    description: str
    type: str
    topic: str
    payload: str
    retain: bool = False
    qos: int = 0

    def to_prompt_dict(self) -> dict[str, str]:
        return {
            "slug": self.slug,
            "description": self.description,
        }


def load_action_definitions(action_file: Path | None, inline_json: str | None) -> list[ActionDefinition]:
    """Load action definitions from a JSON file or inline JSON string."""
    candidates: list[dict] = []
    if action_file and action_file.exists():
        try:
            candidates.extend(_ensure_list(json.loads(action_file.read_text(encoding="utf-8"))))
        except Exception:  # pylint: disable=broad-except
            pass

    if inline_json:
        try:
            candidates.extend(_ensure_list(json.loads(inline_json)))
        except Exception:  # pylint: disable=broad-except
            pass

    definitions: list[ActionDefinition] = []
    for candidate in candidates:
        slug = str(candidate.get("slug") or "").strip()
        topic = str(candidate.get("topic") or "").strip()
        payload = candidate.get("payload")
        if not slug or not topic or payload is None:
            continue

        description = str(candidate.get("description") or slug)
        action_type = (candidate.get("type") or "mqtt").lower()
        if action_type != "mqtt":
            # Only MQTT actions are supported for now
            continue

        definitions.append(
            ActionDefinition(
                slug=slug,
                description=description,
                type=action_type,
                topic=topic,
                payload=json.dumps(payload) if isinstance(payload, (dict, list)) else str(payload),
                retain=bool(candidate.get("retain", False)),
                qos=int(candidate.get("qos", 0)),
            )
        )
    return definitions


def _ensure_list(value) -> list[dict]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        return [value]
    return []


class ActionEngine:
    """Execute assistant actions (MQTT + Home Assistant)."""

    def __init__(self, definitions: Iterable[ActionDefinition]) -> None:
        self._definitions = {definition.slug: definition for definition in definitions}

    def describe_for_prompt(self) -> list[dict[str, str]]:
        return [definition.to_prompt_dict() for definition in self._definitions.values()]

    async def execute(
        self,
        tokens: Iterable[str],
        mqtt_client,
        ha_client: HomeAssistantClient | None = None,
        scheduler: AssistantScheduler | None = None,
        schedule_service: ScheduleService | None = None,
    ) -> list[str]:
        executed: list[str] = []

        seen: set[str] = set()
        for token in tokens:
            slug, arg_string = _split_action_token(token)
            if not slug or slug in seen:
                continue
            seen.add(slug)
            definition = self._definitions.get(slug)
            if not definition:
                handled = await _maybe_execute_home_assistant_action(slug, arg_string, ha_client)
                if handled:
                    executed.append(slug)
                continue
            if definition.type == "mqtt" and mqtt_client:
                mqtt_client.publish(
                    definition.topic,
                    definition.payload,
                    retain=definition.retain,
                    qos=definition.qos,
                )
                executed.append(slug)
            elif definition.type == "ha":
                handled = await _maybe_execute_home_assistant_action(slug, arg_string, ha_client)
                if handled:
                    executed.append(slug)
            else:
                handled = await _maybe_execute_scheduler_action(slug, arg_string, scheduler, schedule_service)
                if handled:
                    executed.append(slug)
        return executed


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _local_now() -> datetime:
    return datetime.now().astimezone()


def _split_action_token(token: str) -> tuple[str, str]:
    token = token.strip()
    if not token:
        return "", ""
    if ":" not in token:
        return token, ""
    slug, arg_string = token.split(":", 1)
    return slug.strip(), arg_string.strip()


def _parse_action_args(arg_string: str) -> dict[str, str]:
    if not arg_string:
        return {}
    args: dict[str, str] = {}
    for segment in arg_string.split(","):
        piece = segment.strip()
        if not piece:
            continue
        if "=" in piece:
            key, value = piece.split("=", 1)
            args[key.strip()] = value.strip()
        elif " " not in piece:
            args.setdefault("entity_id", piece)
    return args


async def _maybe_execute_home_assistant_action(
    slug: str,
    arg_string: str,
    ha_client: HomeAssistantClient | None,
) -> bool:
    if ha_client is None:
        return False
    args = _parse_action_args(arg_string)
    if slug == "ha.turn_on":
        entity_id = args.get("entity_id")
        if not entity_id:
            return False
        await ha_client.call_service("homeassistant", "turn_on", {"entity_id": entity_id})
        return True
    if slug == "ha.turn_off":
        entity_id = args.get("entity_id")
        if not entity_id:
            return False
        await ha_client.call_service("homeassistant", "turn_off", {"entity_id": entity_id})
        return True
    return False


async def _maybe_execute_scheduler_action(
    slug: str,
    arg_string: str,
    scheduler: AssistantScheduler | None,
    schedule_service: ScheduleService | None,
) -> bool:
    args = _parse_action_args(arg_string)
    if slug == "reminder.create":
        message = args.get("message") or args.get("text") or arg_string
        when_text = args.get("when") or args.get("time")
        if not message:
            return False
        target_time = _parse_datetime(when_text) if when_text else datetime.now(UTC)
        if target_time is None:
            return False
        repeat_rule = _reminder_repeat_from_args(args, target_time)
        if schedule_service is not None:
            await schedule_service.create_reminder(fire_time=target_time, message=message, repeat=repeat_rule)
            return True
        if scheduler is not None:
            await scheduler.schedule_reminder(target_time, message)
            return True
        return False
    if schedule_service is not None:
        if slug == "timer.start":
            duration = _duration_from_args(args, arg_string)
            if duration <= 0:
                return False
            playback = _playback_from_args(args)
            await schedule_service.create_timer(duration_seconds=duration, label=args.get("label"), playback=playback)
            return True
        if slug in {"timer.add", "timer.extend"}:
            duration = _duration_from_args(args, "")
            if duration <= 0:
                return False
            event_id = _resolve_schedule_event_id(schedule_service, "timer", args)
            if not event_id:
                return False
            await schedule_service.extend_timer(event_id, int(duration))
            return True
        if slug in {"timer.stop", "timer.cancel"}:
            event_id = _resolve_schedule_event_id(schedule_service, "timer", args)
            if not event_id:
                return False
            await schedule_service.stop_event(event_id, reason="action_stop")
            return True
        if slug == "timer.cancel_all":
            await schedule_service.cancel_all_timers()
            return True
        if slug == "alarm.set":
            time_text = args.get("time") or args.get("at") or arg_string
            if not time_text:
                return False
            days = parse_day_tokens(args.get("days") or args.get("repeat"))
            playback = _playback_from_args(args)
            single_flag = args.get("single") or args.get("once")
            single_shot = bool(single_flag) if single_flag is not None else None
            await schedule_service.create_alarm(
                time_of_day=time_text,
                label=args.get("label") or args.get("name"),
                days=days,
                playback=playback,
                single_shot=single_shot,
            )
            return True
        if slug == "alarm.update":
            event_id = _resolve_schedule_event_id(schedule_service, "alarm", args)
            if not event_id:
                return False
            days = (
                parse_day_tokens(args.get("days") or args.get("repeat"))
                if ("days" in args or "repeat" in args)
                else None
            )
            playback = _playback_from_args(args) if "type" in args or "mode" in args or "source" in args else None
            await schedule_service.update_alarm(
                event_id,
                time_of_day=args.get("time") or args.get("at"),
                days=days,
                label=args.get("label") or args.get("name"),
                playback=playback,
            )
            return True
        if slug == "alarm.delete":
            event_id = _resolve_schedule_event_id(schedule_service, "alarm", args)
            if not event_id:
                return False
            await schedule_service.delete_event(event_id)
            return True
        if slug == "alarm.stop":
            event_id = _resolve_schedule_event_id(schedule_service, "alarm", args)
            if not event_id:
                return False
            await schedule_service.stop_event(event_id, reason="action_stop")
            return True
        if slug == "alarm.snooze":
            event_id = _resolve_schedule_event_id(schedule_service, "alarm", args)
            if not event_id:
                return False
            minutes = int(float(args.get("minutes") or 5))
            await schedule_service.snooze_alarm(event_id, minutes=minutes)
            return True
    if scheduler is not None and slug == "timer.start":
        duration_text = args.get("duration") or args.get("seconds") or arg_string
        if not duration_text:
            return False
        duration = _parse_duration_seconds(duration_text)
        if duration <= 0:
            return False
        label = args.get("label")
        await scheduler.start_timer(duration, label)
        return True
    return False


def _duration_from_args(args: dict[str, str], fallback: str) -> float:
    duration_text = args.get("duration") or args.get("seconds") or fallback
    if not duration_text:
        return 0.0
    return _parse_duration_seconds(duration_text)


def _playback_from_args(args: dict[str, str]) -> PlaybackConfig:
    mode = (args.get("type") or args.get("mode") or "beep").lower()
    if mode != "music":
        return PlaybackConfig()
    return PlaybackConfig(
        mode="music",
        music_source=args.get("source") or args.get("playlist") or args.get("media"),
        music_entity=args.get("entity") or args.get("player"),
        media_content_type=args.get("content_type"),
        provider=args.get("provider"),
        description=args.get("description") or args.get("label"),
    )


def _resolve_schedule_event_id(schedule_service: ScheduleService, event_type: str, args: dict[str, str]) -> str | None:
    candidate = args.get("id") or args.get("event_id")
    if candidate:
        return candidate
    label = args.get("label") or args.get("name")
    if not label:
        return None
    lowered = label.strip().lower()
    for event in schedule_service.list_events(event_type):
        event_label = (event.get("label") or "").lower()
        if event_label and lowered in event_label:
            return event["id"]
    return None


def _parse_duration_seconds(value: str) -> float:
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
            return _parse_iso_duration(text)
        except ValueError:
            return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def _parse_iso_duration(value: str) -> float:
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


def _parse_datetime(text: str) -> datetime | None:
    text = text.strip()
    if not text:
        return None
    lowered = text.lower()
    relative = _parse_relative_datetime(text, lowered)
    if relative is not None:
        return relative
    if lowered.startswith("in "):
        duration = _parse_duration_seconds(lowered[3:])
        if duration <= 0:
            return None
        return _utc_now() + timedelta(seconds=duration)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        duration = _parse_duration_seconds(text)
        if duration > 0:
            return _utc_now() + timedelta(seconds=duration)
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


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


def _parse_relative_datetime(original: str, lowered: str) -> datetime | None:
    for keyword, day_offset, default_hour in _RELATIVE_DAY_RULES:
        if not lowered.startswith(keyword):
            continue
        remainder = original[len(keyword) :].strip()
        if remainder.lower().startswith("at "):
            remainder = remainder[3:].strip()
        time_phrase = _extract_time_phrase(remainder)
        base = _local_now() + timedelta(days=day_offset)
        combined = _apply_time_phrase(base, time_phrase, default_hour)
        return combined.astimezone(UTC)
    weekday_candidate = _parse_weekday_reference(original, lowered)
    if weekday_candidate is not None:
        return weekday_candidate
    return None


def _parse_weekday_reference(original: str, lowered: str) -> datetime | None:
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
    base = _local_now()
    days_ahead = (target_weekday - base.weekday()) % 7
    if prefix in {"next", "upcoming"} and days_ahead == 0:
        days_ahead = 7
    target_date = base + timedelta(days=days_ahead)
    combined = _apply_time_phrase(target_date, time_phrase, 9)
    if combined <= base:
        combined = combined + timedelta(days=7)
    return combined.astimezone(UTC)


def _extract_time_phrase(text: str) -> str:
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
    time_match = _parse_time_of_day(phrase)
    if time_match is None:
        hour, minute = default_hour, 0
    else:
        hour, minute = time_match
    return reference.replace(hour=hour, minute=minute, second=0, microsecond=0)


def _parse_time_of_day(phrase: str | None) -> tuple[int, int] | None:
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


def _reminder_repeat_from_args(args: dict[str, str], default: datetime) -> dict[str, Any] | None:
    repeat_value = (args.get("repeat") or args.get("frequency") or "").strip().lower()
    if repeat_value in {"", "none"}:
        repeat_value = ""
    days_arg = args.get("days") or args.get("repeat_days")
    if repeat_value in {"weekly", "week"} or days_arg:
        days = parse_day_tokens(days_arg or repeat_value or "")
        return {"type": "weekly", "days": days} if days else {"type": "weekly", "days": list(range(7))}
    if repeat_value in {"daily", "day"}:
        return {"type": "weekly", "days": list(range(7))}
    if repeat_value in {"weekdays", "weekday"}:
        return {"type": "weekly", "days": [0, 1, 2, 3, 4]}
    if repeat_value in {"monthly", "month"}:
        day_value = args.get("day") or args.get("day_of_month")
        try:
            day_number = int(day_value) if day_value else default.day
        except (TypeError, ValueError):
            day_number = default.day
        return {"type": "monthly", "day": max(1, min(31, day_number))}
    interval_days = args.get("interval_days")
    interval_months = args.get("interval_months")
    if interval_months:
        try:
            months = int(interval_months)
        except ValueError:
            months = 0
        if months > 0:
            return {"type": "interval", "interval_months": months}
    if interval_days:
        try:
            days = int(interval_days)
        except ValueError:
            days = 0
        if days > 0:
            return {"type": "interval", "interval_days": days}
    numeric_match = re.search(r"(\d+)\s+(months?|month)", repeat_value)
    if numeric_match:
        months = int(numeric_match.group(1))
        return {"type": "interval", "interval_months": months}
    numeric_match = re.search(r"(\d+)\s+(weeks?|week)", repeat_value)
    if numeric_match:
        weeks = int(numeric_match.group(1))
        return {"type": "interval", "interval_days": weeks * 7}
    numeric_match = re.search(r"(\d+)\s+(days?|day)", repeat_value)
    if numeric_match:
        days = int(numeric_match.group(1))
        return {"type": "interval", "interval_days": days}
    return None
