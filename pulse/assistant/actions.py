"""Action parsing and execution helpers."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

from pulse.audio import set_volume
from pulse.datetime_utils import parse_datetime, parse_duration_seconds, utc_now

from .home_assistant import HomeAssistantError, kelvin_to_mired
from .schedule_service import PlaybackConfig, parse_day_tokens

LOGGER = logging.getLogger("pulse-assistant")

if TYPE_CHECKING:  # pragma: no cover
    from .home_assistant import HomeAssistantClient
    from .media_controller import MediaController
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
        except Exception:
            pass

    if inline_json:
        try:
            candidates.extend(_ensure_list(json.loads(inline_json)))
        except Exception:
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


def _utc_now() -> datetime:
    return utc_now()


def _local_now() -> datetime:
    return _utc_now().astimezone()


def _parse_datetime(text: str) -> datetime | None:
    with patch("pulse.datetime_utils.utc_now", _utc_now):
        return parse_datetime(text)


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
        media_controller: MediaController | None = None,
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
                if not handled:
                    handled = await _maybe_execute_media_action(slug, arg_string, media_controller)
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


def _entity_domain(entity_id: str | None) -> str:
    if not entity_id or "." not in entity_id:
        return ""
    return entity_id.split(".", 1)[0].lower()


def _log_resolution(slug: str, targets: list[str], domains: list[str | None]) -> None:
    LOGGER.debug("Resolved %s to %s (domain_order=%s)", slug, targets, domains)


def _parse_brightness_pct(args: dict[str, str]) -> float | None:
    value = args.get("brightness") or args.get("level")
    if not value:
        return None
    cleaned = value.strip().lower().rstrip("%")
    try:
        brightness = float(cleaned)
    except ValueError:
        return None
    return max(0.0, min(100.0, brightness))


def _parse_color_temp_mired(args: dict[str, str]) -> int | None:
    raw = args.get("color_temp") or args.get("color_temperature") or args.get("kelvin")
    if raw is None:
        return None
    text = str(raw).strip().lower()
    if not text:
        return None
    try:
        value = float(text.rstrip("k"))
    except ValueError:
        return None
    # Heuristic: values over 1000 are likely Kelvin
    if value > 1000:
        return kelvin_to_mired(value)
    return int(round(value))


def _parse_rgb_color(args: dict[str, str]) -> tuple[int, int, int] | None:
    """Parse RGB color from action args. Supports names, hex, and RGB tuples."""
    # Check for explicit RGB values
    rgb_str = args.get("rgb") or args.get("rgb_color")
    if rgb_str:
        try:
            # Support formats like "255,0,0" or "[255,0,0]"
            cleaned = rgb_str.strip().strip("[]")
            parts = [int(x.strip()) for x in cleaned.split(",")]
            if len(parts) == 3:
                return tuple(max(0, min(255, p)) for p in parts)
        except (ValueError, TypeError):
            pass

    # Check for hex color (e.g., #ff0000 or ff0000)
    hex_str = args.get("hex") or args.get("color_hex") or args.get("color")
    if isinstance(hex_str, str) and hex_str.strip().startswith("#"):
        hex_str = hex_str.strip()[1:]
    if isinstance(hex_str, str) and len(hex_str.strip()) in {6, 8}:
        try:
            cleaned = hex_str.strip()
            # Ignore alpha if present
            if len(cleaned) == 8:
                cleaned = cleaned[2:]
            value = int(cleaned, 16)
            r = (value >> 16) & 0xFF
            g = (value >> 8) & 0xFF
            b = value & 0xFF
            return (r, g, b)
        except (ValueError, TypeError):
            pass

    # Check for color name
    color_name = args.get("color") or args.get("colour")
    if color_name:
        rgb = _color_name_to_rgb(color_name.strip().lower())
        if rgb:
            return rgb

    return None


def _color_name_to_rgb(color_name: str) -> tuple[int, int, int] | None:
    """Convert common color names to RGB values."""
    color_map: dict[str, tuple[int, int, int]] = {
        "red": (255, 0, 0),
        "green": (0, 255, 0),
        "blue": (0, 0, 255),
        "white": (255, 255, 255),
        "yellow": (255, 255, 0),
        "orange": (255, 165, 0),
        "purple": (128, 0, 128),
        "pink": (255, 192, 203),
        "cyan": (0, 255, 255),
        "magenta": (255, 0, 255),
        "lime": (0, 255, 0),
        "navy": (0, 0, 128),
        "teal": (0, 128, 128),
        "maroon": (128, 0, 0),
        "olive": (128, 128, 0),
        "silver": (192, 192, 192),
        "gray": (128, 128, 128),
        "grey": (128, 128, 128),
        "black": (0, 0, 0),
        "warm": (255, 147, 41),  # Warm white
        "cool": (148, 191, 255),  # Cool white
    }
    return color_map.get(color_name)


def _parse_percentage(args: dict[str, str]) -> int | None:
    """Parse percentage value (0-100) from action args."""
    raw = args.get("percentage") or args.get("speed") or args.get("percent")
    if not raw:
        return None
    try:
        value = int(float(raw))
        return max(0, min(100, value))
    except (ValueError, TypeError):
        return None


def _parse_transition_seconds(args: dict[str, str]) -> float | None:
    raw = args.get("transition") or args.get("fade")
    if raw is None:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return max(0.0, value)


def _preferred_domains(args: dict[str, str]) -> list[str | None]:
    """Choose domain resolution order based on hints in the args."""
    percent_hint = args.get("percentage") or args.get("speed") or args.get("percent")
    name_hint = (args.get("name") or "").lower()
    color_hint = args.get("color") or args.get("colour") or args.get("rgb") or args.get("rgb_color")
    if percent_hint or "fan" in name_hint:
        return ["fan", "light", "switch", None]
    if color_hint:
        return ["light", "fan", "switch", None]
    return ["light", "fan", "switch", None]


async def _resolve_entities(
    args: dict[str, str], ha_client: HomeAssistantClient, domain: str | None = None
) -> list[str]:
    """Resolve entity names to entity IDs for any domain (lights, fans, switches, etc.)."""
    entity_id = args.get("entity_id")
    if entity_id:
        return [entity_id]
    room_hint = args.get("room") or args.get("area") or args.get("group")
    name_hint = args.get("name")
    scope_all = str(args.get("all") or "").lower() in {"true", "1", "yes", "on"}
    entities = await ha_client.list_entities(domain)
    if scope_all:
        return [e["entity_id"] for e in entities if e.get("entity_id")]
    if not room_hint and not name_hint:
        return []

    def _tokenize(text: str) -> list[str]:
        return [t for t in re.split(r"[^a-z0-9]+", text.lower()) if t]

    tokens: list[str] = []
    if room_hint:
        tokens.extend(_tokenize(room_hint))
    if name_hint:
        tokens.extend(_tokenize(name_hint))

    def _score_entity(state: dict[str, Any]) -> int:
        ent = state.get("entity_id") or ""
        attrs = state.get("attributes") or {}
        friendly = str(attrs.get("friendly_name") or ent).lower()
        area = str(attrs.get("area_id") or "").lower()
        score = 0
        # Strong boost for exact name_hint substring in friendly name
        if name_hint and name_hint.lower() in friendly:
            score += 5
        # Token matches across friendly, area, and entity_id
        for token in tokens:
            if token in friendly:
                score += 3
            elif token in area:
                score += 2
            elif token in ent.lower():
                score += 1
        return score

    scored: list[tuple[int, str]] = []
    for state in entities:
        ent = state.get("entity_id")
        if not ent:
            continue
        score = _score_entity(state)
        if score > 0:
            scored.append((score, ent))

    if scored:
        # Pick the top-scoring entity (or entities tied for top)
        scored.sort(key=lambda pair: pair[0], reverse=True)
        top_score = scored[0][0]
        top_entities = [ent for score, ent in scored if score == top_score]
        return [top_entities[0]] if top_entities else []

    # Fallback: substring match on friendly/entity/area if scoring produced nothing
    fallback_matches: list[str] = []
    name_lower = name_hint.lower() if name_hint else ""
    for state in entities:
        ent = state.get("entity_id") or ""
        attrs = state.get("attributes") or {}
        friendly = str(attrs.get("friendly_name") or ent).lower()
        area = str(attrs.get("area_id") or "").lower()
        if name_lower and (name_lower in friendly or name_lower in ent.lower() or name_lower in area):
            fallback_matches.append(ent)
            continue
        if tokens and any(token in ent.lower() for token in tokens):
            fallback_matches.append(ent)
    if fallback_matches:
        return [fallback_matches[0]]

    return []


async def _resolve_light_entities(args: dict[str, str], ha_client: HomeAssistantClient) -> list[str]:
    """Resolve light entity names to entity IDs (backward compatibility wrapper)."""
    return await _resolve_entities(args, ha_client, "light")


async def _maybe_execute_home_assistant_action(
    slug: str,
    arg_string: str,
    ha_client: HomeAssistantClient | None,
) -> bool:
    if ha_client is None:
        return False
    args = _parse_action_args(arg_string)

    if slug in {"ha.light", "ha.light_on", "ha.light_off"}:
        return await _execute_light_action(slug, args, ha_client)
    if slug == "ha.turn_on":
        entity_id = args.get("entity_id")
        if not entity_id:
            # Try to resolve entity by name, trying multiple common domains
            targets: list[str] = []
            for domain in _preferred_domains(args):
                targets = await _resolve_entities(args, ha_client, domain)
                if targets:
                    break
            if not targets:
                return False
            _log_resolution(slug, targets, _preferred_domains(args))
            # Check if resolved entities are lights (for brightness/color support)
            if all(_entity_domain(t) == "light" for t in targets):
                brightness = _parse_brightness_pct(args)
                rgb_color = _parse_rgb_color(args)
                color_temp = None if rgb_color is not None else _parse_color_temp_mired(args)
                transition = _parse_transition_seconds(args)
                await ha_client.set_light_state(
                    targets,
                    on=True,
                    brightness_pct=brightness,
                    color_temp_mired=color_temp,
                    rgb_color=rgb_color,
                    transition=transition,
                )
                return True
            # For non-light entities, use generic turn_on
            for target in targets:
                domain = _entity_domain(target)
                if domain == "fan":
                    percentage = _parse_percentage(args)
                    if percentage is not None:
                        try:
                            await ha_client.call_service(
                                "fan", "set_percentage", {"entity_id": target, "percentage": percentage}
                            )
                        except HomeAssistantError:
                            try:
                                await ha_client.call_service(
                                    "fan", "set_speed", {"entity_id": target, "speed": str(percentage)}
                                )
                            except HomeAssistantError:
                                await ha_client.call_service(
                                    "homeassistant", "turn_on", {"entity_id": target, "percentage": percentage}
                                )
                    else:
                        await ha_client.call_service("homeassistant", "turn_on", {"entity_id": target})
                else:
                    await ha_client.call_service("homeassistant", "turn_on", {"entity_id": target})
            return True
        if _entity_domain(entity_id) == "light":
            brightness = _parse_brightness_pct(args)
            rgb_color = _parse_rgb_color(args)
            color_temp = None if rgb_color is not None else _parse_color_temp_mired(args)
            transition = _parse_transition_seconds(args)
            await ha_client.set_light_state(
                [entity_id],
                on=True,
                brightness_pct=brightness,
                color_temp_mired=color_temp,
                rgb_color=rgb_color,
                transition=transition,
            )
            return True
        if _entity_domain(entity_id) == "fan":
            # Handle fan speed/percentage
            percentage = _parse_percentage(args)
            if percentage is not None:
                try:
                    await ha_client.call_service(
                        "fan", "set_percentage", {"entity_id": entity_id, "percentage": percentage}
                    )
                except HomeAssistantError:
                    try:
                        await ha_client.call_service(
                            "fan", "set_speed", {"entity_id": entity_id, "speed": str(percentage)}
                        )
                    except HomeAssistantError:
                        await ha_client.call_service(
                            "homeassistant", "turn_on", {"entity_id": entity_id, "percentage": percentage}
                        )
            else:
                await ha_client.call_service("homeassistant", "turn_on", {"entity_id": entity_id})
            return True
        await ha_client.call_service("homeassistant", "turn_on", {"entity_id": entity_id})
        return True
    if slug == "ha.turn_off":
        entity_id = args.get("entity_id")
        if not entity_id:
            # Try to resolve entity by name, trying multiple common domains
            targets: list[str] = []
            for domain in _preferred_domains(args):
                targets = await _resolve_entities(args, ha_client, domain)
                if targets:
                    break
            if not targets:
                return False
            _log_resolution(slug, targets, _preferred_domains(args))
            # Check if resolved entities are lights (for transition support)
            if all(_entity_domain(t) == "light" for t in targets):
                transition = _parse_transition_seconds(args)
                await ha_client.set_light_state(targets, on=False, transition=transition)
            else:
                # For non-light entities, use generic turn_off
                for target in targets:
                    domain = _entity_domain(target)
                    if domain == "fan":
                        percentage = _parse_percentage(args)
                        if percentage is not None:
                            try:
                                await ha_client.call_service(
                                    "fan", "set_percentage", {"entity_id": target, "percentage": percentage}
                                )
                            except HomeAssistantError:
                                try:
                                    await ha_client.call_service(
                                        "fan", "set_speed", {"entity_id": target, "speed": str(percentage)}
                                    )
                                except HomeAssistantError:
                                    await ha_client.call_service(
                                        "homeassistant", "turn_off", {"entity_id": target, "percentage": percentage}
                                    )
                        else:
                            await ha_client.call_service("homeassistant", "turn_off", {"entity_id": target})
                    else:
                        await ha_client.call_service("homeassistant", "turn_off", {"entity_id": target})
            return True
        if _entity_domain(entity_id) == "light":
            transition = _parse_transition_seconds(args)
            await ha_client.set_light_state([entity_id], on=False, transition=transition)
            return True
        if _entity_domain(entity_id) == "fan":
            percentage = _parse_percentage(args)
            if percentage is not None:
                try:
                    await ha_client.call_service(
                        "fan", "set_percentage", {"entity_id": entity_id, "percentage": percentage}
                    )
                except HomeAssistantError:
                    try:
                        await ha_client.call_service(
                            "fan", "set_speed", {"entity_id": entity_id, "speed": str(percentage)}
                        )
                    except HomeAssistantError:
                        await ha_client.call_service(
                            "homeassistant", "turn_off", {"entity_id": entity_id, "percentage": percentage}
                        )
            else:
                await ha_client.call_service("homeassistant", "turn_off", {"entity_id": entity_id})
            return True
        await ha_client.call_service("homeassistant", "turn_off", {"entity_id": entity_id})
        return True
    if slug == "ha.scene":
        scene_id = args.get("entity_id") or args.get("scene") or args.get("name")
        if not scene_id:
            return False
        scene_entity = scene_id if scene_id.startswith("scene.") else f"scene.{scene_id}"
        await ha_client.activate_scene(scene_entity)
        return True
    return False


async def _execute_light_action(
    slug: str,
    args: dict[str, str],
    ha_client: HomeAssistantClient,
) -> bool:
    targets = await _resolve_light_entities(args, ha_client)
    if not targets:
        return False
    turn_on = slug in {"ha.light", "ha.light_on"} or args.get("state", "").lower() in {"on", "true", "1"}
    brightness = _parse_brightness_pct(args)
    color_temp = _parse_color_temp_mired(args)
    transition = _parse_transition_seconds(args)
    await ha_client.set_light_state(
        targets,
        on=turn_on,
        brightness_pct=brightness,
        color_temp_mired=color_temp,
        transition=transition,
    )
    return True


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
        target_time = parse_datetime(when_text) if when_text else utc_now()
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
        duration = parse_duration_seconds(duration_text)
        if duration <= 0:
            return False
        label = args.get("label")
        await scheduler.start_timer(duration, label)
        return True
    return False


async def _maybe_execute_media_action(
    slug: str,
    arg_string: str,
    media_controller: MediaController | None,
) -> bool:
    if media_controller is None:
        return False
    if slug in {"media.pause", "media.mute"}:
        await media_controller.pause_all()
        return True
    if slug in {"media.resume", "media.play"}:
        await media_controller.resume_all()
        return True
    if slug in {"media.stop", "media.halt"}:
        await media_controller.stop_all()
        return True
    if slug in {"volume.set", "volume"}:
        args = _parse_action_args(arg_string)
        percent = _parse_percentage(args)
        if percent is None:
            raw = args.get("volume") or args.get("value")
            if raw:
                try:
                    percent = int(float(raw))
                except (ValueError, TypeError):
                    percent = None
        if percent is None:
            return False
        sink = args.get("sink")
        LOGGER.debug('Setting volume to %s%% (sink="%s")', percent, sink or "default")
        set_volume(percent, sink)
        return True
    return False


def _duration_from_args(args: dict[str, str], fallback: str) -> float:
    duration_text = args.get("duration") or args.get("seconds") or fallback
    if not duration_text:
        return 0.0
    return parse_duration_seconds(duration_text)


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
