"""ICS/WebCal polling service that turns upcoming events into ephemeral reminders."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta

import httpx
from icalendar import Calendar

from .config import CalendarConfig

LOGGER = logging.getLogger("pulse.calendar_sync")


def _now() -> datetime:
    return datetime.now().astimezone()


@dataclass(slots=True, frozen=True)
class CalendarReminder:
    """Normalized reminder payload derived from an ICS event."""

    uid: str
    summary: str
    description: str | None
    location: str | None
    start: datetime
    end: datetime | None
    all_day: bool
    trigger_time: datetime
    calendar_name: str | None
    source_url: str
    url: str | None = None
    sequence: int | None = None


@dataclass(slots=True)
class _FeedState:
    url: str
    etag: str | None = None
    last_modified: str | None = None
    calendar_name: str | None = None
    active_keys: set[str] = field(default_factory=set)


class CalendarSyncService:
    """Poll ICS/WebCal feeds and trigger reminders before each event."""

    def __init__(
        self,
        *,
        config: CalendarConfig,
        trigger_callback: Callable[[CalendarReminder], Awaitable[None]],
        logger: logging.Logger | None = None,
    ) -> None:
        self._config = config
        self._trigger_callback = trigger_callback
        self._logger = logger or LOGGER
        self._client: httpx.AsyncClient | None = None
        self._runner: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._feed_states = {url: _FeedState(url=url) for url in config.feeds}
        self._scheduled: dict[str, asyncio.Task] = {}
        self._scheduled_reminders: dict[str, CalendarReminder] = {}
        self._key_to_feed: dict[str, str] = {}
        self._triggered: dict[str, datetime] = {}

    async def start(self) -> None:
        if not self._config.feeds or self._runner:
            return
        self._stop_event.clear()
        self._client = httpx.AsyncClient(follow_redirects=True, timeout=20.0)
        self._runner = asyncio.create_task(self._run_loop())
        self._logger.info("Calendar sync service started for %d feed(s)", len(self._feed_states))

    async def stop(self) -> None:
        self._stop_event.set()
        runner = self._runner
        self._runner = None
        if runner:
            runner.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await runner
        for task in list(self._scheduled.values()):
            task.cancel()
        self._scheduled.clear()
        self._scheduled_reminders.clear()
        self._key_to_feed.clear()
        if self._client:
            await self._client.aclose()
            self._client = None
        self._logger.info("Calendar sync service stopped")

    async def _run_loop(self) -> None:
        refresh_seconds = max(1, self._config.refresh_minutes) * 60
        while not self._stop_event.is_set():
            await self._sync_once()
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=refresh_seconds)
            except TimeoutError:
                continue

    async def _sync_once(self) -> None:
        now = _now()
        self._prune_triggered(now)
        for state in self._feed_states.values():
            await self._sync_feed(state, now)

    async def _sync_feed(self, state: _FeedState, now: datetime) -> None:
        if not self._client:
            return
        headers: dict[str, str] = {}
        if state.etag:
            headers["If-None-Match"] = state.etag
        if state.last_modified:
            headers["If-Modified-Since"] = state.last_modified
        try:
            response = await self._client.get(state.url, headers=headers)
        except httpx.HTTPError as exc:
            self._logger.warning("Calendar fetch failed for %s: %s", state.url, exc)
            return
        if response.status_code == 304:
            return
        if response.status_code >= 400:
            self._logger.warning("Calendar fetch returned %s for %s", response.status_code, state.url)
            return
        state.etag = response.headers.get("etag") or state.etag
        state.last_modified = response.headers.get("last-modified") or state.last_modified
        try:
            calendar = Calendar.from_ical(response.content)
        except Exception as exc:  # pylint: disable=broad-except
            self._logger.warning("Calendar parse failed for %s: %s", state.url, exc)
            return
        calendar_name = calendar.get("X-WR-CALNAME")
        if calendar_name:
            state.calendar_name = str(calendar_name)
        reminders = self._collect_reminders(calendar, state, now)
        await self._schedule_reminders(state, reminders, now)

    def _collect_reminders(
        self,
        calendar: Calendar,
        state: _FeedState,
        now: datetime,
    ) -> list[CalendarReminder]:
        reminders: list[CalendarReminder] = []
        for component in calendar.walk("VEVENT"):
            uid = component.get("UID")
            if not uid:
                continue
            try:
                start_value = component.decoded("DTSTART")
            except Exception:  # pylint: disable=broad-except
                continue
            start_dt, all_day = self._coerce_datetime(start_value, now.tzinfo)
            if not start_dt:
                continue
            end_dt = None
            try:
                end_value = component.decoded("DTEND")
            except Exception:  # pylint: disable=broad-except
                end_value = None
            if end_value:
                end_dt, _ = self._coerce_datetime(end_value, start_dt.tzinfo or now.tzinfo)
            summary = str(component.get("SUMMARY") or "Calendar event").strip() or "Calendar event"
            description = str(component.get("DESCRIPTION")).strip() if component.get("DESCRIPTION") else None
            location = str(component.get("LOCATION")).strip() if component.get("LOCATION") else None
            url = str(component.get("URL")).strip() if component.get("URL") else None
            sequence = component.get("SEQUENCE")
            triggers = self._extract_alarm_triggers(component, start_dt, now.tzinfo or UTC)
            if not triggers:
                triggers = [self._default_trigger(start_dt, all_day)]
            for trigger_time in triggers:
                if not trigger_time:
                    continue
                reminder = CalendarReminder(
                    uid=str(uid),
                    summary=summary,
                    description=description,
                    location=location,
                    start=start_dt,
                    end=end_dt,
                    all_day=all_day,
                    trigger_time=trigger_time,
                    calendar_name=state.calendar_name,
                    source_url=state.url,
                    url=url,
                    sequence=int(sequence) if sequence is not None else None,
                )
                reminders.append(reminder)
        return reminders

    def _extract_alarm_triggers(
        self,
        component,
        start_dt: datetime,
        local_tz,
    ) -> list[datetime]:
        triggers: list[datetime] = []
        for alarm in getattr(component, "subcomponents", []):
            if getattr(alarm, "name", "").upper() != "VALARM":
                continue
            action = str(alarm.get("ACTION") or "").strip().upper()
            if action and action != "DISPLAY":
                continue
            trigger_raw = alarm.get("TRIGGER")
            if trigger_raw is None:
                continue
            try:
                decoded = alarm.decoded("TRIGGER")
            except Exception:  # pylint: disable=broad-except
                continue
            trigger_dt: datetime | None = None
            if isinstance(decoded, timedelta):
                trigger_dt = start_dt + decoded
            elif isinstance(decoded, datetime):
                trigger_dt = decoded if decoded.tzinfo else decoded.replace(tzinfo=local_tz)
                trigger_dt = trigger_dt.astimezone(local_tz)
            if not trigger_dt:
                continue
            triggers.append(trigger_dt)
        return triggers

    def _default_trigger(self, start_dt: datetime, all_day: bool) -> datetime:
        if all_day:
            previous_day = (start_dt - timedelta(days=1)).date()
            return datetime.combine(previous_day, time(hour=12), tzinfo=start_dt.tzinfo)
        return start_dt - timedelta(minutes=5)

    def _coerce_datetime(
        self,
        value,
        tzinfo,
    ) -> tuple[datetime | None, bool]:
        if isinstance(value, datetime):
            if value.tzinfo is None:
                value = value.replace(tzinfo=tzinfo)
            return value.astimezone(tzinfo), False
        if isinstance(value, date):
            return datetime.combine(value, time.min, tzinfo=tzinfo), True
        return None, False

    async def _schedule_reminders(
        self,
        state: _FeedState,
        reminders: list[CalendarReminder],
        now: datetime,
    ) -> None:
        lookahead_end = now + timedelta(hours=self._config.lookahead_hours)
        valid_keys: set[str] = set()
        for reminder in reminders:
            trigger_time = reminder.trigger_time
            if trigger_time < now - timedelta(minutes=1):
                continue
            if trigger_time > lookahead_end:
                continue
            key = self._reminder_key(reminder)
            valid_keys.add(key)
            if key in self._triggered or key in self._scheduled:
                continue
            task = asyncio.create_task(self._await_and_fire(key, reminder))
            self._scheduled[key] = task
            self._scheduled_reminders[key] = reminder
            self._key_to_feed[key] = state.url
            state.active_keys.add(key)
            self._logger.debug(
                "Scheduled calendar reminder %s (%s) for %s",
                reminder.uid,
                reminder.summary,
                reminder.trigger_time.isoformat(),
            )
        stale_keys = state.active_keys - valid_keys
        for key in stale_keys:
            task = self._scheduled.pop(key, None)
            if task:
                task.cancel()
            self._scheduled_reminders.pop(key, None)
            self._key_to_feed.pop(key, None)
        state.active_keys = {key for key in valid_keys if key in self._scheduled}

    async def _await_and_fire(self, key: str, reminder: CalendarReminder) -> None:
        delay = (reminder.trigger_time - _now()).total_seconds()
        if delay > 0:
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=delay)
                return
            except TimeoutError:
                pass
        if self._stop_event.is_set():
            return
        try:
            await self._trigger_callback(reminder)
            self._logger.info("Fired calendar reminder %s (%s)", reminder.uid, reminder.summary)
        except Exception:  # pylint: disable=broad-except
            self._logger.exception(
                "Failed to trigger calendar reminder %s (%s)",
                reminder.uid,
                reminder.summary,
            )
        finally:
            self._scheduled.pop(key, None)
            self._scheduled_reminders.pop(key, None)
            feed_url = self._key_to_feed.pop(key, None)
            if feed_url:
                state = self._feed_states.get(feed_url)
                if state:
                    state.active_keys.discard(key)
            self._triggered[key] = reminder.start

    def _prune_triggered(self, now: datetime) -> None:
        cutoff = now - timedelta(days=7)
        for key, event_start in list(self._triggered.items()):
            if event_start < cutoff:
                self._triggered.pop(key, None)

    def _reminder_key(self, reminder: CalendarReminder) -> str:
        return f"{reminder.source_url}|{reminder.uid}|{reminder.trigger_time.isoformat()}"
