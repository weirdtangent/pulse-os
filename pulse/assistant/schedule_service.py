"""Alarm and timer scheduling with local playback + MQTT hooks."""

from __future__ import annotations

import asyncio
import calendar
import contextlib
import json
import logging
import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pulse import audio as pulse_audio
from pulse.datetime_utils import combine_time, parse_time_string
from pulse.sound_library import SoundKind, SoundLibrary, SoundSettings
from pulse.utils import sanitize_hostname_for_entity_id

from .home_assistant import HomeAssistantClient

EventType = Literal["alarm", "timer", "reminder"]
PlaybackMode = Literal["beep", "music"]

StateCallback = Callable[[dict[str, Any]], None]
ActiveCallback = Callable[[EventType, dict[str, Any] | None], None]

LOGGER = logging.getLogger("pulse.schedule_service")


def _now() -> datetime:
    return datetime.now().astimezone()


def _serialize_dt(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.astimezone()
    return value.isoformat()


def _deserialize_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed


def _default_media_player_entity(hostname: str) -> str:
    sanitized = sanitize_hostname_for_entity_id(hostname)
    return f"media_player.{sanitized}"


def _clamp_volume(value: int) -> int:
    return max(0, min(100, value))


def _add_months(dt: datetime, months: int) -> datetime:
    total = dt.month - 1 + months
    year = dt.year + total // 12
    month = total % 12 + 1
    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


def _next_weekly_occurrence(anchor: datetime, days: list[int], time_str: str, after: datetime) -> datetime:
    tz_after = after.astimezone(anchor.tzinfo or after.tzinfo)
    tz_anchor = anchor.tzinfo or tz_after.tzinfo
    normalized_days = sorted({day % 7 for day in days}) or [anchor.weekday()]
    start_search = tz_after.replace(hour=0, minute=0, second=0, microsecond=0)
    for offset in range(0, 14):
        candidate_date = start_search + timedelta(days=offset)
        if candidate_date.weekday() not in normalized_days:
            continue
        candidate = combine_time(candidate_date, time_str)
        if tz_anchor:
            candidate = candidate.astimezone(tz_anchor)
        if candidate <= after:
            continue
        if candidate >= anchor:
            return candidate
    fallback = anchor if anchor > after else anchor + timedelta(days=7)
    return combine_time(fallback, time_str)


def _next_monthly_occurrence(anchor: datetime, day: int, time_str: str, after: datetime) -> datetime:
    tzinfo = anchor.tzinfo or after.tzinfo
    current_year = after.year
    current_month = after.month
    desired_day = max(1, min(31, day))
    for _ in range(0, 24):
        days_in_month = calendar.monthrange(current_year, current_month)[1]
        actual_day = min(desired_day, days_in_month)
        candidate = datetime(
            current_year,
            current_month,
            actual_day,
            tzinfo=tzinfo,
        )
        candidate = combine_time(candidate, time_str)
        if candidate <= after:
            current_month += 1
            if current_month > 12:
                current_month = 1
                current_year += 1
            continue
        if candidate >= anchor:
            return candidate
        current_month += 1
        if current_month > 12:
            current_month = 1
            current_year += 1
    fallback = _add_months(anchor, 1)
    return combine_time(fallback, time_str)


def _next_interval_occurrence(
    anchor: datetime,
    *,
    interval_days: int | None = None,
    interval_months: int | None = None,
    after: datetime | None = None,
) -> datetime:
    reference = after or _now()
    if interval_months:
        months = max(1, interval_months)
        candidate = anchor
        while candidate <= reference:
            candidate = _add_months(candidate, months)
        return candidate
    days = max(1, interval_days or 1)
    if anchor > reference:
        return anchor
    delta = reference - anchor
    steps = int(delta.total_seconds() // (days * 86400)) + 1
    return anchor + timedelta(days=steps * days)


def _ensure_reminder_meta(event: ScheduledEvent) -> dict[str, Any]:
    if not isinstance(event.metadata, dict):
        event.metadata = {}
    reminder = event.metadata.get("reminder")
    if not isinstance(reminder, dict):
        reminder = {}
        event.metadata["reminder"] = reminder
    return reminder


def _reminder_meta(event: ScheduledEvent) -> dict[str, Any]:
    reminder = event.metadata.get("reminder") if isinstance(event.metadata, dict) else None
    if isinstance(reminder, dict):
        return reminder
    return {}


def _reminder_repeat_rule(event: ScheduledEvent) -> dict[str, Any] | None:
    rule = _reminder_meta(event).get("repeat")
    if isinstance(rule, dict):
        return rule
    return None


def _reminder_delay(event: ScheduledEvent) -> datetime | None:
    meta = _reminder_meta(event)
    return _deserialize_dt(meta.get("delay_until"))


def _set_reminder_delay(event: ScheduledEvent, target: datetime | None) -> None:
    meta = _ensure_reminder_meta(event)
    if target is None:
        meta.pop("delay_until", None)
    else:
        meta["delay_until"] = _serialize_dt(target)


def _reminder_start(event: ScheduledEvent) -> datetime:
    meta = _reminder_meta(event)
    start = _deserialize_dt(meta.get("start"))
    if start:
        return start
    return event.next_fire_dt()


def _reminder_message(event: ScheduledEvent) -> str:
    meta = _reminder_meta(event)
    message = meta.get("message")
    if isinstance(message, str) and message.strip():
        return message.strip()
    return event.label or "Reminder"


def _reminder_repeats(event: ScheduledEvent) -> bool:
    rule = _reminder_repeat_rule(event)
    return bool(rule)


def _compute_next_reminder_fire(event: ScheduledEvent, *, after: datetime | None = None) -> datetime | None:
    rule = _reminder_repeat_rule(event)
    if not rule:
        return None
    reference = after or _now()
    start = _reminder_start(event)
    reference = max(reference, start - timedelta(seconds=1))
    repeat_type = (rule.get("type") or "").lower()
    time_str = rule.get("time") or start.strftime("%H:%M")
    if repeat_type == "weekly":
        days = rule.get("days") or [start.weekday()]
        base = _next_weekly_occurrence(start, days, time_str, reference)
    elif repeat_type == "monthly":
        day = int(rule.get("day") or start.day)
        base = _next_monthly_occurrence(start, day, time_str, reference)
    elif repeat_type == "interval":
        interval_days = rule.get("interval_days")
        interval_months = rule.get("interval_months")
        base = _next_interval_occurrence(
            start,
            interval_days=interval_days,
            interval_months=interval_months,
            after=reference,
        )
    else:
        # Fallback to daily cadence using provided days/time
        days = rule.get("days") or list(range(7))
        base = _next_weekly_occurrence(start, days, time_str, reference)
    delay = _reminder_delay(event)
    if delay and (not base or delay <= base):
        return delay
    if delay and base and delay > base:
        _set_reminder_delay(event, None)
    return base


def _normalize_repeat_rule(rule: dict[str, Any] | None, fallback: datetime) -> dict[str, Any] | None:
    if not isinstance(rule, dict):
        return None
    repeat_type = (rule.get("type") or "").lower()
    if repeat_type not in {"weekly", "monthly", "interval"}:
        return None
    normalized: dict[str, Any] = {"type": repeat_type}
    if repeat_type == "weekly":
        raw_days = rule.get("days")
        if isinstance(raw_days, list):
            days = sorted({int(day) % 7 for day in raw_days if isinstance(day, (int, float))})
        else:
            days = [fallback.weekday()]
        normalized["days"] = days
        normalized["time"] = rule.get("time") or fallback.strftime("%H:%M")
    elif repeat_type == "monthly":
        day = rule.get("day")
        if isinstance(day, (int, float)):
            normalized["day"] = max(1, min(31, int(day)))
        else:
            normalized["day"] = fallback.day
        normalized["time"] = rule.get("time") or fallback.strftime("%H:%M")
    elif repeat_type == "interval":
        months = rule.get("interval_months")
        days = rule.get("interval_days")
        if isinstance(months, (int, float)) and int(months) > 0:
            normalized["interval_months"] = int(months)
        else:
            normalized["interval_days"] = max(1, int(days or 1))
        normalized["time"] = rule.get("time") or fallback.strftime("%H:%M")
    return normalized


def _format_duration_label(duration_seconds: float) -> str:
    """Format a duration in seconds into a human-readable timer label.

    Examples:
        - 180 seconds -> "3 MIN TIMER"
        - 3600 seconds -> "60 MIN TIMER"
        - 90 seconds -> "90 SEC TIMER"
    """
    total_seconds = int(duration_seconds)
    if total_seconds < 60:
        return f"{total_seconds} SEC TIMER"
    total_minutes = total_seconds // 60
    if total_minutes < 60:
        return f"{total_minutes} MIN TIMER"
    hours = total_minutes // 60
    minutes = total_minutes % 60
    if minutes == 0:
        return f"{hours} HR TIMER"
    return f"{hours} HR {minutes} MIN TIMER"


DAY_NAME_MAP = {
    "mon": 0,
    "monday": 0,
    "tue": 1,
    "tues": 1,
    "tuesday": 1,
    "wed": 2,
    "wednesday": 2,
    "thu": 3,
    "thur": 3,
    "thurs": 3,
    "thursday": 3,
    "fri": 4,
    "friday": 4,
    "sat": 5,
    "saturday": 5,
    "sun": 6,
    "sunday": 6,
}

WEEKDAY_SET = {0, 1, 2, 3, 4}
WEEKEND_SET = {5, 6}


def parse_day_tokens(value: str | None) -> list[int] | None:
    if not value:
        return None
    lowered = value.strip().lower()
    condensed = lowered.replace(" ", "")
    if lowered in {"single", "once", "next"}:
        return None
    if lowered in {"weekdays", "weekday"}:
        return sorted(WEEKDAY_SET)
    if lowered in {"weekend", "weekends"}:
        return sorted(WEEKEND_SET)
    if condensed in {"everyday", "alldays"} or lowered in {"daily", "all"}:
        return list(range(7))
    days: set[int] = set()
    for chunk in re.split(r"[,\s]+", lowered):
        chunk = chunk.strip()
        if not chunk:
            continue
        idx = DAY_NAME_MAP.get(chunk[:3], DAY_NAME_MAP.get(chunk))
        if idx is None:
            continue
        days.add(idx)
    if not days:
        return None
    return sorted(days)


def day_indexes_to_names(indexes: list[int] | None) -> list[str]:
    names = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    if not indexes:
        return []
    return [names[i % 7] for i in indexes]


def _compute_next_alarm_fire(
    time_str: str,
    repeat_days: list[int] | None,
    *,
    after: datetime | None = None,
    skip_dates: set[str] | None = None,
    skip_weekdays: set[int] | None = None,
) -> datetime:
    """Compute the next alarm fire time honoring skip dates/weekdays for recurring alarms."""
    hour, minute = parse_time_string(time_str)
    now = after or _now()
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)

    def _is_skipped(dt: datetime) -> bool:
        date_str = dt.date().isoformat()
        if skip_dates and date_str in skip_dates:
            return True
        if skip_weekdays and dt.weekday() in skip_weekdays:
            return True
        return False

    if not repeat_days:
        # For one-time alarms, do not apply skip lists; just move to the next day if already past.
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate

    day_set = {d % 7 for d in repeat_days}
    for offset in range(0, 21):
        attempt = candidate + timedelta(days=offset)
        if attempt <= now:
            continue
        if attempt.weekday() not in day_set:
            continue
        if _is_skipped(attempt):
            continue
        return attempt
    # Fallback: advance until we find a matching repeat day that is not skipped.
    attempt = candidate + timedelta(days=1)
    for _ in range(60):
        if attempt <= now:
            attempt += timedelta(days=1)
            continue
        if attempt.weekday() not in day_set:
            attempt += timedelta(days=1)
            continue
        if _is_skipped(attempt):
            attempt += timedelta(days=1)
            continue
        return attempt
    # If we somehow didn't find a valid day, return the last attempt.
    return attempt


@dataclass(slots=True)
class PlaybackConfig:
    mode: PlaybackMode = "beep"
    music_entity: str | None = None
    music_source: str | None = None
    media_content_type: str | None = None
    provider: str | None = None
    description: str | None = None
    sound_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "music_entity": self.music_entity,
            "music_source": self.music_source,
            "media_content_type": self.media_content_type,
            "provider": self.provider,
            "description": self.description,
            "sound_id": self.sound_id,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> PlaybackConfig:
        if not payload:
            return cls()
        return cls(
            mode=(payload.get("mode") or "beep"),
            music_entity=payload.get("music_entity"),
            music_source=payload.get("music_source"),
            media_content_type=payload.get("media_content_type"),
            provider=payload.get("provider"),
            description=payload.get("description"),
            sound_id=payload.get("sound_id"),
        )


@dataclass
class ScheduledEvent:
    event_id: str
    event_type: EventType
    label: str | None
    time_of_day: str | None
    repeat_days: list[int] | None
    single_shot: bool
    duration_seconds: float | None
    target_time: str | None
    next_fire: str
    playback: PlaybackConfig
    created_at: str
    metadata: dict[str, Any] = field(default_factory=dict)
    paused: bool = False

    def next_fire_dt(self) -> datetime:
        dt = _deserialize_dt(self.next_fire)
        if dt is None:
            return _now()
        return dt

    def set_next_fire(self, dt: datetime) -> None:
        self.next_fire = _serialize_dt(dt)

    def target_dt(self) -> datetime | None:
        return _deserialize_dt(self.target_time)

    def set_target(self, dt: datetime | None) -> None:
        self.target_time = _serialize_dt(dt) if dt else None

    def to_public_dict(self, status: str = "scheduled") -> dict[str, Any]:
        is_repeating = bool(self.repeat_days)
        if not is_repeating and self.event_type == "reminder":
            reminder_meta = self.metadata.get("reminder") if isinstance(self.metadata, dict) else None
            if isinstance(reminder_meta, dict):
                is_repeating = bool(reminder_meta.get("repeat"))
        data = {
            "id": self.event_id,
            "type": self.event_type,
            "label": self.label,
            "time": self.time_of_day,
            "days": day_indexes_to_names(self.repeat_days),
            "is_repeating": is_repeating,
            "single_shot": self.single_shot,
            "duration_seconds": self.duration_seconds,
            "target": self.target_time,
            "next_fire": self.next_fire,
            "playback": self.playback.to_dict(),
            "created_at": self.created_at,
            "metadata": self.metadata,
            "status": status,
            "paused": self.paused,
        }
        return data

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> ScheduledEvent:
        return cls(
            event_id=payload["event_id"],
            event_type=payload["event_type"],
            label=payload.get("label"),
            time_of_day=payload.get("time_of_day"),
            repeat_days=payload.get("repeat_days"),
            single_shot=bool(payload.get("single_shot", False)),
            duration_seconds=payload.get("duration_seconds"),
            target_time=payload.get("target_time"),
            next_fire=payload.get("next_fire") or _serialize_dt(_now()),
            playback=PlaybackConfig.from_dict(payload.get("playback")),
            created_at=payload.get("created_at") or _serialize_dt(_now()),
            metadata=payload.get("metadata") or {},
            paused=bool(payload.get("paused", False)),
        )

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "label": self.label,
            "time_of_day": self.time_of_day,
            "repeat_days": self.repeat_days,
            "single_shot": self.single_shot,
            "duration_seconds": self.duration_seconds,
            "target_time": self.target_time,
            "next_fire": self.next_fire,
            "playback": self.playback.to_dict(),
            "created_at": self.created_at,
            "metadata": self.metadata,
            "paused": self.paused,
        }


@dataclass
class ActiveEvent:
    event: ScheduledEvent
    started_at: datetime
    handle: PlaybackHandle
    playback_task: asyncio.Task | None = None
    auto_stop_task: asyncio.Task | None = None


class PlaybackHandle:
    def __init__(
        self,
        playback: PlaybackConfig,
        hostname: str,
        ha_client: HomeAssistantClient | None,
        event_type: EventType,
        sound_library: SoundLibrary | None,
        sound_settings: SoundSettings,
    ) -> None:
        self.playback = playback
        self.hostname = hostname
        self.ha_client = ha_client
        self.event_type = event_type
        self._sound_library = sound_library
        self._sound_settings = sound_settings
        self._loop = asyncio.get_running_loop()
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._sink = None
        self._orig_volume: int | None = None
        self._pause_flag = False
        self._pause_condition = asyncio.Event()
        self._pause_condition.set()
        self._music_paused = False

    def _sound_path(self, kind: SoundKind, sound_id: str | None) -> Path | None:
        if not self._sound_library:
            return None
        return self._sound_library.resolve_with_default(sound_id, kind=kind, settings=self._sound_settings)

    async def start(self) -> None:
        if self.event_type == "reminder":
            await self._play_reminder_tone()
            return
        if self.playback.mode == "music" and self.ha_client:
            await self._start_music()
        else:
            self._task = asyncio.create_task(self._beep_loop())

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        if self.playback.mode == "music" and self.ha_client:
            await self._stop_music()
        await self._restore_volume()
        self._pause_condition.set()

    async def pause(self) -> None:
        if self.playback.mode == "music" and self.ha_client:
            await self._pause_music()
            return
        if self._pause_flag:
            return
        self._pause_flag = True
        self._pause_condition.clear()

    async def resume(self) -> None:
        if self.playback.mode == "music" and self.ha_client:
            await self._resume_music()
            return
        if not self._pause_flag:
            return
        self._pause_flag = False
        self._pause_condition.set()

    async def _start_music(self) -> None:
        entity = (
            self.playback.music_entity
            or os.environ.get("PULSE_MEDIA_PLAYER_ENTITY")
            or _default_media_player_entity(self.hostname)
        )
        if not entity:
            LOGGER.warning("Music alarm requested but no media_player entity available; falling back to beep")
            self.playback = PlaybackConfig()
            self._task = asyncio.create_task(self._beep_loop())
            return
        payload = {
            "entity_id": entity,
            "media_content_id": self.playback.music_source or self.playback.description or "",
            "media_content_type": self.playback.media_content_type or "music",
        }
        try:
            await self.ha_client.call_service("media_player", "play_media", payload)
        except Exception as exc:
            LOGGER.warning("Failed to start music alarm via Home Assistant: %s", exc)
            self.playback = PlaybackConfig()
            self._task = asyncio.create_task(self._beep_loop())

    async def _stop_music(self) -> None:
        entity = (
            self.playback.music_entity
            or os.environ.get("PULSE_MEDIA_PLAYER_ENTITY")
            or _default_media_player_entity(self.hostname)
        )
        if not entity:
            return
        try:
            await self.ha_client.call_service("media_player", "media_stop", {"entity_id": entity})
        except Exception:
            LOGGER.debug("Failed to stop media_player for alarm", exc_info=True)
        self._music_paused = False

    async def _pause_music(self) -> None:
        entity = (
            self.playback.music_entity
            or os.environ.get("PULSE_MEDIA_PLAYER_ENTITY")
            or _default_media_player_entity(self.hostname)
        )
        if not entity or self._music_paused:
            return
        try:
            await self.ha_client.call_service("media_player", "media_pause", {"entity_id": entity})
            self._music_paused = True
        except Exception:
            LOGGER.debug("Failed to pause media_player for alarm", exc_info=True)

    async def _resume_music(self) -> None:
        entity = (
            self.playback.music_entity
            or os.environ.get("PULSE_MEDIA_PLAYER_ENTITY")
            or _default_media_player_entity(self.hostname)
        )
        if not entity or not self._music_paused:
            return
        try:
            await self.ha_client.call_service("media_player", "media_play", {"entity_id": entity})
            self._music_paused = False
        except Exception:
            LOGGER.debug("Failed to resume media_player for alarm", exc_info=True)

    async def _beep_loop(self) -> None:
        self._sink = pulse_audio.find_audio_sink()
        if self._sink:
            current = pulse_audio.get_current_volume(self._sink)
            # Never save 0 as original volume - use minimum of 20% if volume is 0 or None
            # This prevents accidentally restoring to 0% later
            self._orig_volume = current if current and current > 0 else 20
        force_full_volume = self.event_type == "timer"
        start_volume = 100 if force_full_volume else _clamp_volume((self._orig_volume or 50) // 2)
        ramp_duration = 0.0 if force_full_volume else 30.0  # Ramp volume over 30 seconds
        ramp_end = self._loop.time() + ramp_duration
        stop_at = self._loop.time() + 60.0
        sound_kind: SoundKind = "timer" if self.event_type == "timer" else "alarm"
        resolved_sound = self._sound_path(sound_kind, self.playback.sound_id)
        try:
            while not self._stop_event.is_set() and self._loop.time() < stop_at:
                await self._wait_if_paused()
                if self._stop_event.is_set():
                    break
                now = self._loop.time()
                if self._sink:
                    if force_full_volume:
                        target = 100
                    elif now < ramp_end:
                        progress = (now - (ramp_end - ramp_duration)) / ramp_duration
                        target = start_volume + int(progress * (100 - start_volume))
                    else:
                        target = 100
                    pulse_audio.set_volume(target, self._sink)
                await asyncio.to_thread(pulse_audio.play_sound, resolved_sound, pulse_audio.play_alarm_sound)
                await asyncio.sleep(0.8)
        except asyncio.CancelledError:
            raise
        finally:
            await self._restore_volume()

    async def _play_reminder_tone(self) -> None:
        # Play reminder sound twice for better noticeability
        sound_path = self._sound_path("reminder", self.playback.sound_id)
        await asyncio.to_thread(pulse_audio.play_sound, sound_path, pulse_audio.play_reminder_sound)
        await asyncio.sleep(0.2)
        await asyncio.to_thread(pulse_audio.play_sound, sound_path, pulse_audio.play_reminder_sound)

    async def _wait_if_paused(self) -> None:
        while self._pause_flag and not self._stop_event.is_set():
            await asyncio.sleep(0.05)

    async def _restore_volume(self) -> None:
        if self._sink is None or self._orig_volume is None:
            return
        # Never restore to 0% - use minimum of 20% to prevent silent audio
        restore_volume = max(20, self._orig_volume) if self._orig_volume > 0 else 20
        await asyncio.to_thread(pulse_audio.set_volume, restore_volume, self._sink)


class ScheduleService:
    """Manage alarm & timer scheduling."""

    def __init__(
        self,
        *,
        storage_path: Path,
        hostname: str,
        on_state_changed: StateCallback | None = None,
        on_active_event: ActiveCallback | None = None,
        ha_client: HomeAssistantClient | None = None,
        sound_settings: SoundSettings | None = None,
        skip_dates: set[str] | None = None,
        skip_weekdays: set[int] | None = None,
    ) -> None:
        self._storage_path = storage_path
        self._hostname = hostname
        self._state_cb = on_state_changed
        self._active_cb = on_active_event
        self._ha_client = ha_client
        self._sound_settings = sound_settings or SoundSettings.with_defaults()
        self._sound_library = SoundLibrary(custom_dir=self._sound_settings.custom_dir)
        self._sound_library.ensure_custom_dir()
        self._events: dict[str, ScheduledEvent] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._active: dict[str, ActiveEvent] = {}
        self._lock = asyncio.Lock()
        self._started = False
        self._manual_skip_dates: set[str] = set(skip_dates or set())
        self._skip_weekdays: set[int] = {d % 7 for d in (skip_weekdays or set())}
        self._ooo_skip_dates: set[str] = set()
        self._ui_pause_dates: set[str] = set()

    def _effective_skip_dates(self) -> set[str]:
        return set(self._manual_skip_dates) | set(self._ooo_skip_dates) | set(self._ui_pause_dates)

    async def set_manual_skip_dates(self, dates: set[str]) -> None:
        async with self._lock:
            self._manual_skip_dates = set(dates)
            await self._reschedule_all_alarms_locked()
        await self._persist_events()
        await self._publish_state()

    async def set_ooo_skip_dates(self, dates: set[str]) -> None:
        async with self._lock:
            self._ooo_skip_dates = set(dates)
            await self._reschedule_all_alarms_locked()
        await self._persist_events()
        await self._publish_state()

    async def set_ui_pause_date(self, date_str: str, paused: bool) -> None:
        date_str = date_str.strip()
        if not date_str:
            return
        async with self._lock:
            if paused:
                self._ui_pause_dates.add(date_str)
            else:
                self._ui_pause_dates.discard(date_str)
            await self._reschedule_all_alarms_locked()
        await self._persist_events()
        await self._publish_state()

    async def start(self) -> None:
        if self._started:
            return
        await self._load_events()
        for event in self._events.values():
            self._schedule_event(event)
        await self._publish_state()
        self._started = True

    async def stop(self) -> None:
        for task in self._tasks.values():
            task.cancel()
        self._tasks.clear()
        active = list(self._active.keys())
        for event_id in active:
            await self.stop_event(event_id, reason="shutdown")
        self._started = False

    def update_sound_settings(self, settings: SoundSettings) -> None:
        """Update sound settings for future playback."""
        self._sound_settings = settings

    async def pause_active_audio(self) -> None:
        async with self._lock:
            handles = [active.handle for active in self._active.values()]
        await asyncio.gather(
            *(handle.pause() for handle in handles if handle),
            return_exceptions=True,
        )

    async def resume_active_audio(self) -> None:
        async with self._lock:
            handles = [active.handle for active in self._active.values()]
        await asyncio.gather(
            *(handle.resume() for handle in handles if handle),
            return_exceptions=True,
        )

    def _compute_alarm_fire(self, time_str: str, days: list[int] | None, after: datetime | None = None) -> datetime:
        return _compute_next_alarm_fire(
            time_str,
            days,
            after=after or _now(),
            skip_dates=self._effective_skip_dates(),
            skip_weekdays=self._skip_weekdays,
        )

    async def _reschedule_all_alarms_locked(self) -> None:
        for event in self._events.values():
            if event.event_type != "alarm":
                continue
            if event.time_of_day:
                event.set_next_fire(self._compute_alarm_fire(event.time_of_day, event.repeat_days))
                self._reschedule_event(event)

    async def create_alarm(
        self,
        *,
        time_of_day: str,
        label: str | None = None,
        days: list[int] | None = None,
        playback: PlaybackConfig | None = None,
        single_shot: bool | None = None,
    ) -> ScheduledEvent:
        next_fire = self._compute_alarm_fire(time_of_day, days)
        event = ScheduledEvent(
            event_id=uuid4().hex,
            event_type="alarm",
            label=label,
            time_of_day=time_of_day,
            repeat_days=days,
            single_shot=bool(single_shot) if single_shot is not None else not bool(days),
            duration_seconds=None,
            target_time=None,
            next_fire=_serialize_dt(next_fire),
            playback=playback or PlaybackConfig(),
            created_at=_serialize_dt(_now()),
        )
        async with self._lock:
            self._events[event.event_id] = event
            self._schedule_event(event)
            await self._persist_events()
            await self._publish_state()
        return event

    async def create_timer(
        self,
        *,
        duration_seconds: float,
        label: str | None = None,
        playback: PlaybackConfig | None = None,
    ) -> ScheduledEvent:
        duration_seconds = max(1.0, duration_seconds)
        fire_time = _now() + timedelta(seconds=duration_seconds)
        # Use duration-based label if no label provided
        if label and label.strip():
            timer_label = label.strip()
        else:
            timer_label = _format_duration_label(duration_seconds)
        event = ScheduledEvent(
            event_id=uuid4().hex,
            event_type="timer",
            label=timer_label,
            time_of_day=None,
            repeat_days=None,
            single_shot=True,
            duration_seconds=duration_seconds,
            target_time=_serialize_dt(fire_time),
            next_fire=_serialize_dt(fire_time),
            playback=playback or PlaybackConfig(),
            created_at=_serialize_dt(_now()),
        )
        async with self._lock:
            self._events[event.event_id] = event
            self._schedule_event(event)
            await self._persist_events()
            await self._publish_state()
        return event

    async def create_reminder(
        self,
        *,
        fire_time: datetime,
        message: str,
        repeat: dict[str, Any] | None = None,
    ) -> ScheduledEvent:
        fire_time = fire_time.astimezone()
        repeat_rule = _normalize_repeat_rule(repeat, fire_time)
        reminder_meta = {
            "message": message,
            "repeat": repeat_rule,
            "start": _serialize_dt(fire_time),
        }
        repeat_days = None
        if repeat_rule and repeat_rule.get("type") == "weekly":
            normalized_days = sorted({int(day) % 7 for day in repeat_rule.get("days") or []})
            repeat_days = normalized_days or None
        event = ScheduledEvent(
            event_id=uuid4().hex,
            event_type="reminder",
            label=message,
            time_of_day=fire_time.strftime("%H:%M"),
            repeat_days=repeat_days,
            single_shot=not bool(repeat_rule),
            duration_seconds=None,
            target_time=None,
            next_fire=_serialize_dt(fire_time),
            playback=PlaybackConfig(),
            created_at=_serialize_dt(_now()),
            metadata={"reminder": reminder_meta},
        )
        async with self._lock:
            self._events[event.event_id] = event
            self._schedule_event(event)
            await self._persist_events()
            await self._publish_state()
        return event

    async def update_alarm(
        self,
        event_id: str,
        *,
        time_of_day: str | None = None,
        days: list[int] | None = None,
        label: str | None = None,
        playback: PlaybackConfig | None = None,
    ) -> bool:
        async with self._lock:
            event = self._events.get(event_id)
            if not event or event.event_type != "alarm":
                return False
            if time_of_day:
                event.time_of_day = time_of_day
            if days is not None:
                event.repeat_days = days
                event.single_shot = not bool(days)
            if label is not None:
                event.label = label
            if playback:
                event.playback = playback
            if event.time_of_day:
                event.set_next_fire(self._compute_alarm_fire(event.time_of_day, event.repeat_days))
            self._reschedule_event(event)
            await self._persist_events()
            await self._publish_state()
            return True

    async def pause_alarm(self, event_id: str) -> bool:
        return await self._set_alarm_pause_state(event_id, True)

    async def resume_alarm(self, event_id: str) -> bool:
        return await self._set_alarm_pause_state(event_id, False)

    async def _set_alarm_pause_state(self, event_id: str, paused: bool) -> bool:
        async with self._lock:
            event = self._events.get(event_id)
            if not event or event.event_type != "alarm":
                return False
            if event.paused == paused:
                return True
            event.paused = paused
            task = self._tasks.pop(event_id, None)
            if task:
                task.cancel()
            if not paused and event.time_of_day:
                event.set_next_fire(_compute_next_alarm_fire(event.time_of_day, event.repeat_days))
                self._schedule_event(event)
            await self._persist_events()
            await self._publish_state()
            return True

    async def delete_event(self, event_id: str) -> bool:
        await self.stop_event(event_id, reason="deleted")
        async with self._lock:
            event = self._events.pop(event_id, None)
            if not event:
                return False
            task = self._tasks.pop(event_id, None)
            if task:
                task.cancel()
            await self._persist_events()
            await self._publish_state()
            return True

    async def stop_event(self, event_id: str, *, reason: str = "stopped") -> bool:
        handle: PlaybackHandle | None = None
        event_payload: dict[str, Any] | None = None
        event_type: EventType | None = None
        async with self._lock:
            active = self._active.pop(event_id, None)
            stored_event = self._events.get(event_id)
            if active:
                if active.auto_stop_task:
                    active.auto_stop_task.cancel()
                if active.playback_task:
                    active.playback_task.cancel()
                handle = active.handle
                if stored_event is None:
                    stored_event = active.event
            if stored_event is None:
                return False
            event_type = stored_event.event_type
            event_payload = stored_event.to_public_dict()
            if stored_event.event_type == "alarm":
                if stored_event.paused:
                    task = self._tasks.pop(event_id, None)
                    if task:
                        task.cancel()
                elif stored_event.repeat_days:
                    stored_event.set_next_fire(
                        _compute_next_alarm_fire(stored_event.time_of_day or "08:00", stored_event.repeat_days)
                    )
                    self._reschedule_event(stored_event)
                else:
                    self._events.pop(event_id, None)
                    task = self._tasks.pop(event_id, None)
                    if task:
                        task.cancel()
            elif stored_event.event_type == "reminder" and _reminder_repeats(stored_event):
                _set_reminder_delay(stored_event, None)
                next_fire = _compute_next_reminder_fire(stored_event, after=_now())
                if next_fire:
                    stored_event.set_next_fire(next_fire)
                    self._reschedule_event(stored_event)
                else:
                    self._events.pop(event_id, None)
                    task = self._tasks.pop(event_id, None)
                    if task:
                        task.cancel()
            else:
                self._events.pop(event_id, None)
                task = self._tasks.pop(event_id, None)
                if task:
                    task.cancel()
            await self._persist_events()
            await self._publish_state()
        if handle:
            with contextlib.suppress(Exception):
                await handle.stop()
        if event_type and event_payload:
            self._notify_active(event_type, {"state": "stopped", "reason": reason, "event": event_payload})
            self._notify_active(event_type, None)
            return True
        return False

    async def snooze_alarm(self, event_id: str, minutes: int = 5) -> bool:
        minutes = max(1, minutes)
        async with self._lock:
            existing = self._events.get(event_id)
            if not existing or existing.event_type != "alarm":
                return False
            snapshot = replace(existing)
        await self.stop_event(event_id, reason="snoozed")
        async with self._lock:
            event = self._events.get(event_id) or snapshot
            event.set_next_fire(_now() + timedelta(minutes=minutes))
            self._events[event.event_id] = event
            self._reschedule_event(event)
            await self._persist_events()
            await self._publish_state()
        self._notify_active("alarm", None)
        return True

    async def delay_reminder(self, event_id: str, seconds: int) -> bool:
        seconds = max(1, seconds)
        async with self._lock:
            event = self._events.get(event_id)
            if not event or event.event_type != "reminder":
                return False
            target = _now() + timedelta(seconds=seconds)
            if _reminder_repeats(event):
                _set_reminder_delay(event, target)
                next_fire = _compute_next_reminder_fire(event, after=_now())
                event.set_next_fire(next_fire or target)
            else:
                _set_reminder_delay(event, None)
                event.set_next_fire(target)
                meta = _ensure_reminder_meta(event)
                meta["start"] = _serialize_dt(target)
            self._reschedule_event(event)
            await self._persist_events()
            await self._publish_state()
            return True

    async def extend_timer(self, event_id: str, seconds: int) -> bool:
        async with self._lock:
            event = self._events.get(event_id)
            if not event or event.event_type != "timer":
                return False
            target = event.target_dt() or _now()
            target += timedelta(seconds=seconds)
            event.set_target(target)
            event.set_next_fire(target)
            self._reschedule_event(event)
            await self._persist_events()
            await self._publish_state()
            return True

    async def cancel_all_timers(self) -> int:
        async with self._lock:
            timers = [event_id for event_id, event in self._events.items() if event.event_type == "timer"]
        count = 0
        for event_id in timers:
            if await self.stop_event(event_id, reason="cancel_all"):
                count += 1
        return count

    def list_events(self, event_type: EventType | None = None) -> list[dict[str, Any]]:
        events = []
        for event in self._events.values():
            if event_type and event.event_type != event_type:
                continue
            if event.paused:
                status = "paused"
            elif event.event_id in self._active:
                status = "active"
            else:
                status = "scheduled"
            events.append(event.to_public_dict(status=status))
        events.sort(key=lambda item: item.get("next_fire") or "")
        return events

    async def trigger_ephemeral_reminder(
        self,
        *,
        label: str,
        message: str,
        metadata: dict[str, Any] | None = None,
        auto_clear_seconds: int = 900,
    ) -> str:
        """Play a reminder tone and publish an active reminder without persisting it."""
        event_metadata: dict[str, Any] = {}
        if isinstance(metadata, dict):
            event_metadata = dict(metadata)
        event = ScheduledEvent(
            event_id=f"calendar-{uuid4().hex}",
            event_type="reminder",
            label=label or message or "Reminder",
            time_of_day=None,
            repeat_days=None,
            single_shot=True,
            duration_seconds=None,
            target_time=None,
            next_fire=_serialize_dt(_now()),
            playback=PlaybackConfig(),
            created_at=_serialize_dt(_now()),
            metadata=event_metadata,
        )
        reminder_meta = _ensure_reminder_meta(event)
        reminder_meta.setdefault("message", message or label or "Reminder")
        async with self._lock:
            handle = PlaybackHandle(
                event.playback,
                self._hostname,
                self._ha_client,
                event.event_type,
                self._sound_library,
                self._sound_settings,
            )
            playback_task = asyncio.create_task(handle.start())
            active = ActiveEvent(
                event=event,
                started_at=_now(),
                handle=handle,
                playback_task=playback_task,
            )
            self._active[event.event_id] = active
            auto_stop = asyncio.create_task(self._auto_stop(event.event_id, timeout=float(max(1, auto_clear_seconds))))
            active.auto_stop_task = auto_stop
            self._notify_active("reminder", {"state": "ringing", "event": event.to_public_dict(status="active")})
        return event.event_id

    def get_next_alarm(self) -> dict[str, Any] | None:
        alarms = [event for event in self._events.values() if event.event_type == "alarm" and not event.paused]
        if not alarms:
            return None
        alarms.sort(key=lambda ev: ev.next_fire_dt())
        return alarms[0].to_public_dict()

    def active_event(self, event_type: EventType) -> dict[str, Any] | None:
        for active in self._active.values():
            if active.event.event_type == event_type:
                return active.event.to_public_dict(status="active")
        return None

    def _schedule_event(self, event: ScheduledEvent) -> None:
        task = self._tasks.pop(event.event_id, None)
        if task:
            task.cancel()
        if event.event_type == "alarm" and event.paused:
            return
        self._tasks[event.event_id] = asyncio.create_task(self._wait_for_event(event.event_id))

    def _reschedule_event(self, event: ScheduledEvent) -> None:
        task = self._tasks.pop(event.event_id, None)
        if task:
            task.cancel()
        self._schedule_event(event)

    async def _wait_for_event(self, event_id: str) -> None:
        while True:
            async with self._lock:
                event = self._events.get(event_id)
                if not event:
                    return
                if event.event_type == "alarm" and event.paused:
                    return
                delay = (event.next_fire_dt() - _now()).total_seconds()
            if delay > 0:
                try:
                    await asyncio.sleep(delay)
                except asyncio.CancelledError:
                    return
            await self._activate_event(event_id)
            return

    async def _activate_event(self, event_id: str) -> None:
        async with self._lock:
            event = self._events.get(event_id)
            if not event:
                return
            if event.event_type == "alarm" and event.paused:
                return
            if event.event_type == "reminder":
                _set_reminder_delay(event, None)
            handle = PlaybackHandle(
                event.playback,
                self._hostname,
                self._ha_client,
                event.event_type,
                self._sound_library,
                self._sound_settings,
            )
            playback_task = asyncio.create_task(handle.start())
            active = ActiveEvent(
                event=event,
                started_at=_now(),
                handle=handle,
                playback_task=playback_task,
            )
            self._active[event_id] = active
            auto_stop = asyncio.create_task(self._auto_stop(event_id))
            active.auto_stop_task = auto_stop
            self._notify_active(event.event_type, {"state": "ringing", "event": event.to_public_dict(status="active")})

    async def _auto_stop(self, event_id: str, *, timeout: float = 60.0) -> None:
        try:
            await asyncio.sleep(max(1.0, timeout))
        except asyncio.CancelledError:
            return
        await self.stop_event(event_id, reason="auto_timeout")

    async def _persist_events(self) -> None:
        payload = {
            "events": [event.to_json_dict() for event in self._events.values()],
            "paused_dates": sorted(self._ui_pause_dates),
        }
        self._storage_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self._storage_path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp_path.replace(self._storage_path)

    async def _load_events(self) -> None:
        if not self._storage_path.exists():
            return
        try:
            data = json.loads(self._storage_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            LOGGER.warning("Failed to load schedules file %s: %s", self._storage_path, exc)
            return
        paused_dates = data.get("paused_dates") or []
        if isinstance(paused_dates, list):
            self._ui_pause_dates = {item for item in paused_dates if isinstance(item, str)}
        for item in data.get("events", []):
            try:
                event = ScheduledEvent.from_dict(item)
            except Exception:
                LOGGER.debug("Skipping invalid schedule entry: %s", item, exc_info=True)
                continue
            if event.event_type == "alarm" and event.time_of_day:
                event.set_next_fire(self._compute_alarm_fire(event.time_of_day, event.repeat_days))
            elif event.event_type == "timer":
                target = event.target_dt()
                if not target or target <= _now():
                    continue
            self._events[event.event_id] = event

    async def _publish_state(self) -> None:
        if not self._state_cb:
            return
        snapshot = {
            "alarms": [],
            "timers": [],
            "reminders": [],
            "paused_dates": sorted(self._ui_pause_dates),
            "effective_skip_dates": sorted(self._effective_skip_dates()),
            "skip_weekdays": sorted(self._skip_weekdays),
            "updated_at": _serialize_dt(_now()),
        }
        for event in self._events.values():
            if event.paused:
                status = "paused"
            elif event.event_id in self._active:
                status = "active"
            else:
                status = "scheduled"
            if event.event_type == "alarm":
                snapshot_key = "alarms"
            elif event.event_type == "timer":
                snapshot_key = "timers"
            else:
                snapshot_key = "reminders"
            snapshot[snapshot_key].append(event.to_public_dict(status=status))
        self._state_cb(snapshot)

    def _notify_active(self, event_type: EventType, payload: dict[str, Any] | None) -> None:
        if self._active_cb:
            self._active_cb(event_type, payload)
