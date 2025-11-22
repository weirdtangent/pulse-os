"""Action parsing and execution helpers."""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

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
    if slug == "reminder.create" and scheduler is not None:
        message = args.get("message") or args.get("text") or arg_string
        when_text = args.get("when") or args.get("time")
        if not message:
            return False
        if not when_text:
            when = datetime.now(UTC)
        else:
            when = _parse_datetime(when_text)
            if when is None:
                return False
        await scheduler.schedule_reminder(when, message)
        return True
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
    if lowered.startswith("in "):
        duration = _parse_duration_seconds(lowered[3:])
        if duration <= 0:
            return None
        return datetime.now(UTC) + timedelta(seconds=duration)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        duration = _parse_duration_seconds(text)
        if duration > 0:
            return datetime.now(UTC) + timedelta(seconds=duration)
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed
