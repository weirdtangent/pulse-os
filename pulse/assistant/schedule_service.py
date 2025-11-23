"""Alarm and timer scheduling with local playback + MQTT hooks."""

from __future__ import annotations

import asyncio
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

from .home_assistant import HomeAssistantClient

EventType = Literal["alarm", "timer"]
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
    sanitized = hostname.lower().replace("-", "_").replace(".", "_")
    return f"media_player.{sanitized}_2"


def _clamp_volume(value: int) -> int:
    return max(0, min(100, value))


def _parse_time_string(value: str) -> tuple[int, int]:
    cleaned = value.strip().lower()
    match = re.match(r"^(\d{1,2})(?::(\d{2}))?\s*(am|pm)?$", cleaned)
    if not match:
        raise ValueError("Invalid time format")
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    suffix = match.group(3)
    if suffix:
        if hour == 12:
            hour = 0
        if suffix == "pm":
            hour += 12
    if hour >= 24 or minute >= 60:
        raise ValueError("Time outside 24h range")
    return hour, minute


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
    if lowered in {"single", "once", "next"}:
        return None
    if lowered in {"weekdays", "weekday"}:
        return sorted(WEEKDAY_SET)
    if lowered in {"weekend", "weekends"}:
        return sorted(WEEKEND_SET)
    if lowered in {"everyday", "daily", "all"}:
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


def _compute_next_alarm_fire(time_str: str, repeat_days: list[int] | None) -> datetime:
    hour, minute = _parse_time_string(time_str)
    now = _now()
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if not repeat_days:
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate
    day_set = [d % 7 for d in repeat_days]
    for offset in range(0, 8):
        attempt = candidate + timedelta(days=offset)
        if attempt.weekday() in day_set and attempt > now:
            return attempt
        if attempt.weekday() in day_set and offset == 0 and attempt > now:
            return attempt
    return candidate + timedelta(days=1)


@dataclass(slots=True)
class PlaybackConfig:
    mode: PlaybackMode = "beep"
    music_entity: str | None = None
    music_source: str | None = None
    media_content_type: str | None = None
    provider: str | None = None
    description: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "music_entity": self.music_entity,
            "music_source": self.music_source,
            "media_content_type": self.media_content_type,
            "provider": self.provider,
            "description": self.description,
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
        data = {
            "id": self.event_id,
            "type": self.event_type,
            "label": self.label,
            "time": self.time_of_day,
            "days": day_indexes_to_names(self.repeat_days),
            "is_repeating": bool(self.repeat_days),
            "single_shot": self.single_shot,
            "duration_seconds": self.duration_seconds,
            "target": self.target_time,
            "next_fire": self.next_fire,
            "playback": self.playback.to_dict(),
            "created_at": self.created_at,
            "metadata": self.metadata,
            "status": status,
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
    ) -> None:
        self.playback = playback
        self.hostname = hostname
        self.ha_client = ha_client
        self._loop = asyncio.get_running_loop()
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._sink = None
        self._orig_volume: int | None = None
        self._pause_flag = False
        self._pause_condition = asyncio.Event()
        self._pause_condition.set()
        self._music_paused = False

    async def start(self) -> None:
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
        except Exception as exc:  # pylint: disable=broad-except
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
        except Exception:  # pylint: disable=broad-except
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
        except Exception:  # pylint: disable=broad-except
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
        except Exception:  # pylint: disable=broad-except
            LOGGER.debug("Failed to resume media_player for alarm", exc_info=True)

    async def _beep_loop(self) -> None:
        self._sink = pulse_audio.find_audio_sink()
        if self._sink:
            self._orig_volume = pulse_audio.get_current_volume(self._sink)
        start_volume = _clamp_volume((self._orig_volume or 50) // 2)
        ramp_end = self._loop.time() + 15.0
        stop_at = self._loop.time() + 60.0
        try:
            while not self._stop_event.is_set() and self._loop.time() < stop_at:
                await self._wait_if_paused()
                if self._stop_event.is_set():
                    break
                now = self._loop.time()
                if self._sink:
                    if now < ramp_end:
                        progress = (now - (ramp_end - 15.0)) / 15.0
                        target = start_volume + int(progress * (100 - start_volume))
                    else:
                        target = 100
                    pulse_audio.set_volume(target, self._sink)
                await asyncio.to_thread(pulse_audio.play_volume_feedback)
                await asyncio.sleep(0.8)
        except asyncio.CancelledError:
            raise
        finally:
            await self._restore_volume()

    async def _wait_if_paused(self) -> None:
        while self._pause_flag and not self._stop_event.is_set():
            await asyncio.sleep(0.05)

    async def _restore_volume(self) -> None:
        if self._sink is None or self._orig_volume is None:
            return
        await asyncio.to_thread(pulse_audio.set_volume, self._orig_volume, self._sink)


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
    ) -> None:
        self._storage_path = storage_path
        self._hostname = hostname
        self._state_cb = on_state_changed
        self._active_cb = on_active_event
        self._ha_client = ha_client
        self._events: dict[str, ScheduledEvent] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._active: dict[str, ActiveEvent] = {}
        self._lock = asyncio.Lock()
        self._started = False

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

    async def create_alarm(
        self,
        *,
        time_of_day: str,
        label: str | None = None,
        days: list[int] | None = None,
        playback: PlaybackConfig | None = None,
        single_shot: bool | None = None,
    ) -> ScheduledEvent:
        next_fire = _compute_next_alarm_fire(time_of_day, days)
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
                event.set_next_fire(_compute_next_alarm_fire(event.time_of_day, event.repeat_days))
            self._reschedule_event(event)
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
            if stored_event.event_type == "alarm" and stored_event.repeat_days:
                stored_event.set_next_fire(
                    _compute_next_alarm_fire(stored_event.time_of_day or "08:00", stored_event.repeat_days)
                )
                self._reschedule_event(stored_event)
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
            status = "active" if event.event_id in self._active else "scheduled"
            events.append(event.to_public_dict(status=status))
        events.sort(key=lambda item: item.get("next_fire") or "")
        return events

    def get_next_alarm(self) -> dict[str, Any] | None:
        alarms = [event for event in self._events.values() if event.event_type == "alarm"]
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
        if event.event_id in self._tasks:
            self._tasks[event.event_id].cancel()
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
            LOGGER.info("Schedule firing %s (%s)", event.event_type, event.label or event.event_id)
            handle = PlaybackHandle(event.playback, self._hostname, self._ha_client)
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

    async def _auto_stop(self, event_id: str) -> None:
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            return
        await self.stop_event(event_id, reason="auto_timeout")

    async def _persist_events(self) -> None:
        payload = {"events": [event.to_json_dict() for event in self._events.values()]}
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
        for item in data.get("events", []):
            try:
                event = ScheduledEvent.from_dict(item)
            except Exception:  # pylint: disable=broad-except
                LOGGER.debug("Skipping invalid schedule entry: %s", item, exc_info=True)
                continue
            if event.event_type == "alarm" and event.time_of_day:
                event.set_next_fire(_compute_next_alarm_fire(event.time_of_day, event.repeat_days))
            elif event.event_type == "timer":
                target = event.target_dt()
                if not target or target <= _now():
                    continue
            self._events[event.event_id] = event

    async def _publish_state(self) -> None:
        if not self._state_cb:
            return
        snapshot = {"alarms": [], "timers": [], "updated_at": _serialize_dt(_now())}
        for event in self._events.values():
            status = "active" if event.event_id in self._active else "scheduled"
            snapshot_key = "alarms" if event.event_type == "alarm" else "timers"
            snapshot[snapshot_key].append(event.to_public_dict(status=status))
        self._state_cb(snapshot)

    def _notify_active(self, event_type: EventType, payload: dict[str, Any] | None) -> None:
        if self._active_cb:
            self._active_cb(event_type, payload)
