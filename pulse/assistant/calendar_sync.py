"""ICS/WebCal polling service that turns upcoming events into ephemeral reminders."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from urllib.parse import unquote, urlparse

import httpx
from icalendar import Calendar

from .config import CalendarConfig

LOGGER = logging.getLogger("pulse.calendar_sync")


def _now() -> datetime:
    return datetime.now().astimezone()


def _normalize_attendee_identifier(value) -> str:
    """Normalize ATTENDEE values and configured emails for comparison."""

    if value is None:
        return ""
    text = str(value).strip().lower()
    if text.startswith("mailto:"):
        text = text[len("mailto:") :]
    return text


def _guess_google_calendar_email(feed_url: str) -> str | None:
    """Best-effort extraction of the calendar owner email from Google ICS URLs."""

    try:
        parsed = urlparse(feed_url)
    except ValueError:
        return None
    host = (parsed.netloc or "").lower()
    if "calendar.google.com" not in host:
        return None
    segments = [segment for segment in (parsed.path or "").split("/") if segment]
    try:
        idx = segments.index("ical")
    except ValueError:
        return None
    if idx + 1 >= len(segments):
        return None
    calendar_id = unquote(segments[idx + 1])
    calendar_id = calendar_id.strip()
    if not calendar_id:
        return None
    return calendar_id.lower()


def _owner_tokens_for_feed(url: str, config: CalendarConfig) -> set[str]:
    tokens = {_normalize_attendee_identifier(email) for email in config.attendee_emails if email}
    guessed = _guess_google_calendar_email(url)
    if guessed:
        tokens.add(_normalize_attendee_identifier(guessed))
    return {token for token in tokens if token}


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
    declined: bool = False


@dataclass(slots=True)
class _FeedState:
    url: str
    etag: str | None = None
    last_modified: str | None = None
    calendar_name: str | None = None
    active_keys: set[str] = field(default_factory=set)
    owner_tokens: set[str] = field(default_factory=set)


class CalendarSyncService:
    """Poll ICS/WebCal feeds and trigger reminders before each event."""

    def __init__(
        self,
        *,
        config: CalendarConfig,
        trigger_callback: Callable[[CalendarReminder], Awaitable[None]],
        logger: logging.Logger | None = None,
        snapshot_callback: Callable[[list[CalendarReminder]], Awaitable[None]] | None = None,
    ) -> None:
        self._config = config
        self._trigger_callback = trigger_callback
        self._snapshot_callback = snapshot_callback
        self._logger = logger or LOGGER
        self._client: httpx.AsyncClient | None = None
        self._runner: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._feed_states = {
            url: _FeedState(url=url, owner_tokens=_owner_tokens_for_feed(url, config)) for url in config.feeds
        }
        self._scheduled: dict[str, asyncio.Task] = {}
        self._scheduled_reminders: dict[str, CalendarReminder] = {}
        self._key_to_feed: dict[str, str] = {}
        self._triggered: dict[str, datetime] = {}
        self._latest_events: list[CalendarReminder] = []
        self._retry_tasks: dict[str, asyncio.Task] = {}
        self._failed_feeds: set[str] = set()
        self._windowed_events: dict[str, CalendarReminder] = {}

    async def start(self) -> None:
        if not self._config.feeds:
            self._logger.warning("Calendar sync start() called but no feeds configured")
            return
        if self._runner:
            return
        self._stop_event.clear()
        self._client = httpx.AsyncClient(follow_redirects=True, timeout=20.0)
        self._runner = asyncio.create_task(self._run_loop())

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
        for task in list(self._retry_tasks.values()):
            task.cancel()
        self._retry_tasks.clear()
        self._failed_feeds.clear()
        if self._client:
            await self._client.aclose()
            self._client = None

    async def _run_loop(self) -> None:
        refresh_seconds = max(1, self._config.refresh_minutes) * 60
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(self._sync_once(), timeout=30.0)
            except Exception:  # pylint: disable=broad-except
                self._logger.exception("Calendar sync loop failed; continuing")
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=refresh_seconds)
            except TimeoutError:
                continue

    async def _sync_once(self) -> None:
        now = _now()
        self._prune_triggered(now)
        self._windowed_events.clear()
        for state in self._feed_states.values():
            try:
                await asyncio.wait_for(self._sync_feed(state, now), timeout=12.0)
            except TimeoutError:
                self._logger.warning("Calendar sync timed out for feed %s", state.url)
            except Exception:  # pylint: disable=broad-except
                self._logger.exception("Calendar sync failed for feed %s", state.url)
        try:
            await self._emit_event_snapshot()
        except Exception:  # pylint: disable=broad-except
            self._logger.exception("Calendar snapshot emit failed")

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
            self._schedule_retry(state.url)
            return
        if response.status_code == 304:
            # Successful response (not modified) - clear any retry
            self._cancel_retry(state.url)
            return
        if response.status_code >= 400:
            self._logger.warning("Calendar fetch returned %s for %s", response.status_code, state.url)
            self._schedule_retry(state.url)
            return
        # Successful fetch - clear any retry
        self._cancel_retry(state.url)
        state.etag = response.headers.get("etag") or state.etag
        state.last_modified = response.headers.get("last-modified") or state.last_modified
        try:
            calendar = Calendar.from_ical(response.content)
        except Exception as exc:  # pylint: disable=broad-except
            self._logger.warning("Calendar parse failed for %s: %s", state.url, exc)
            self._schedule_retry(state.url)
            return
        calendar_name = calendar.get("X-WR-CALNAME")
        if calendar_name:
            state.calendar_name = str(calendar_name)
        reminders = self._collect_reminders(calendar, state, now)
        if not reminders:
            self._logger.warning(
                "Calendar feed %s (%s) produced no reminders at %s",
                state.url,
                state.calendar_name or "unknown",
                now.isoformat(),
            )
        await self._schedule_reminders(state, reminders, now)

    def _collect_reminders(
        self,
        calendar: Calendar,
        state: _FeedState,
        now: datetime,
    ) -> list[CalendarReminder]:
        reminders: list[CalendarReminder] = []
        # Process VEVENT components (calendar events)
        for component in calendar.walk("VEVENT"):
            reminder = self._process_vevent(component, state, now)
            if reminder:
                # Filter out declined events if configured to hide them
                if self._config.hide_declined_events:
                    reminder = [r for r in reminder if not r.declined]
                if reminder:
                    reminders.extend(reminder)
        # Process VTODO components (tasks)
        for component in calendar.walk("VTODO"):
            reminder = self._process_vtodo(component, state, now)
            if reminder:
                # Filter out declined events if configured to hide them
                if self._config.hide_declined_events:
                    reminder = [r for r in reminder if not r.declined]
                if reminder:
                    reminders.extend(reminder)
        return reminders

    def _process_vevent(
        self,
        component,
        state: _FeedState,
        now: datetime,
    ) -> list[CalendarReminder]:
        """Process a VEVENT component and return list of reminders."""
        uid = component.get("UID")
        if not uid:
            return []
        declined = self._event_declined(component, state.owner_tokens)
        summary = str(component.get("SUMMARY") or "Calendar event").strip() or "Calendar event"
        try:
            start_value = component.decoded("DTSTART")
        except Exception:  # pylint: disable=broad-except
            return []
        start_dt, all_day = self._coerce_datetime(start_value, now.tzinfo)
        if not start_dt:
            return []
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
        # Skip events that have already ended; keep recurring events even if the original DTSTART is in the past
        if end_dt and end_dt <= now:
            return []

        triggers = self._extract_alarm_triggers(component, start_dt, now.tzinfo or UTC)
        # Merge default notifications with VALARM triggers, avoiding duplicates
        trigger_times = {trigger for trigger in triggers if trigger}
        # Add default notifications if configured
        if self._config.default_notifications:
            for minutes_before in self._config.default_notifications:
                default_trigger = start_dt - timedelta(minutes=minutes_before)
                # Only add future triggers not already covered by a VALARM trigger (within 30 seconds)
                if default_trigger > now - timedelta(minutes=1) and not any(
                    abs((default_trigger - existing).total_seconds()) < 30 for existing in trigger_times
                ):
                    trigger_times.add(default_trigger)
        # If no triggers at all (no VALARM and no defaults), use the legacy 5-minute default
        if not trigger_times:
            trigger_times = {self._default_trigger(start_dt, all_day)}
        reminders: list[CalendarReminder] = []
        for trigger_time in sorted(trigger_times):
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
                declined=declined,
            )
            reminders.append(reminder)
        return reminders

    def _process_vtodo(
        self,
        component,
        state: _FeedState,
        now: datetime,
    ) -> list[CalendarReminder]:
        """Process a VTODO component and return list of reminders."""
        uid = component.get("UID")
        if not uid:
            return []
        # Skip completed or cancelled tasks
        status = str(component.get("STATUS") or "").strip().upper()
        if status in ("COMPLETED", "CANCELLED"):
            return []
        # VTODO can have DUE or DTSTART (or both)
        # Prefer DUE if available, otherwise use DTSTART
        start_value = None
        try:
            due_value = component.decoded("DUE")
            if due_value:
                start_value = due_value
        except Exception:  # pylint: disable=broad-except
            pass
        if not start_value:
            try:
                start_value = component.decoded("DTSTART")
            except Exception:  # pylint: disable=broad-except
                return []
        start_dt, all_day = self._coerce_datetime(start_value, now.tzinfo)
        if not start_dt:
            return []
        # VTODO typically doesn't have DTEND, but might have DURATION
        end_dt = None
        try:
            duration_value = component.decoded("DURATION")
            if duration_value and isinstance(duration_value, timedelta):
                end_dt = start_dt + duration_value
        except Exception:  # pylint: disable=broad-except
            pass
        # If no DURATION, try DTEND (some implementations use it)
        if not end_dt:
            try:
                end_value = component.decoded("DTEND")
                if end_value:
                    end_dt, _ = self._coerce_datetime(end_value, start_dt.tzinfo or now.tzinfo)
            except Exception:  # pylint: disable=broad-except
                pass
        summary = str(component.get("SUMMARY") or "Task").strip() or "Task"
        description = str(component.get("DESCRIPTION")).strip() if component.get("DESCRIPTION") else None
        location = str(component.get("LOCATION")).strip() if component.get("LOCATION") else None
        url = str(component.get("URL")).strip() if component.get("URL") else None
        sequence = component.get("SEQUENCE")
        # VTODO might not have attendees, but check anyway
        declined = self._event_declined(component, state.owner_tokens)
        triggers = self._extract_alarm_triggers(component, start_dt, now.tzinfo or UTC)
        # Merge default notifications with VALARM triggers, avoiding duplicates
        trigger_times = {trigger for trigger in triggers if trigger}
        # Add default notifications if configured
        if self._config.default_notifications:
            for minutes_before in self._config.default_notifications:
                default_trigger = start_dt - timedelta(minutes=minutes_before)
                # Only add future triggers not already covered by a VALARM trigger (within 30 seconds)
                if default_trigger > now - timedelta(minutes=1) and not any(
                    abs((default_trigger - existing).total_seconds()) < 30 for existing in trigger_times
                ):
                    trigger_times.add(default_trigger)
        # If no triggers at all (no VALARM and no defaults), use the legacy 5-minute default
        if not trigger_times:
            trigger_times = {self._default_trigger(start_dt, all_day)}
        reminders: list[CalendarReminder] = []
        for trigger_time in sorted(trigger_times):
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
                declined=declined,
            )
            reminders.append(reminder)
        return reminders

    def _event_declined(self, component, owner_tokens: set[str]) -> bool:
        if not owner_tokens:
            return False
        attendees = component.get("ATTENDEE")
        if not attendees:
            return False
        if not isinstance(attendees, list):
            attendees = [attendees]
        for attendee in attendees:
            params = getattr(attendee, "params", {}) or {}
            email_param = params.get("EMAIL")
            identifier = _normalize_attendee_identifier(email_param) or _normalize_attendee_identifier(attendee)
            if not identifier or identifier not in owner_tokens:
                continue
            partstat = params.get("PARTSTAT")
            if isinstance(partstat, bytes):
                partstat = partstat.decode("utf-8", errors="ignore")
            partstat = str(partstat or "").strip().upper()
            if partstat == "DECLINED":
                return True
        return False

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
        # First pass: collect all valid reminders and their trigger times per UID
        valid_reminders: list[CalendarReminder] = []
        uids_to_schedule: dict[str, dict[str, set[datetime]]] = {}  # uid -> {source_url: {trigger_times}}
        skipped_past_events = 0
        skipped_past_triggers = 0
        skipped_beyond_lookahead = 0
        for reminder in reminders:
            # Filter out events that have already ended (or started if no end time)
            event_end = reminder.end or reminder.start
            if event_end <= now:
                skipped_past_events += 1
                continue
            trigger_time = reminder.trigger_time
            if trigger_time < now - timedelta(minutes=1):
                skipped_past_triggers += 1
                continue
            # If trigger is beyond lookahead, only schedule if the event itself is within lookahead
            # This ensures we don't miss long advance notifications (e.g., 30-day birthday reminders)
            if trigger_time > lookahead_end:
                if reminder.start > lookahead_end:
                    skipped_beyond_lookahead += 1
                    continue
            key = self._reminder_key(reminder)
            valid_keys.add(key)
            # Track this UID and its trigger time
            if reminder.uid not in uids_to_schedule:
                uids_to_schedule[reminder.uid] = {}
            if reminder.source_url not in uids_to_schedule[reminder.uid]:
                uids_to_schedule[reminder.uid][reminder.source_url] = set()
            uids_to_schedule[reminder.uid][reminder.source_url].add(trigger_time)
            if key in self._triggered:
                continue
            if key in self._scheduled:
                continue
            valid_reminders.append(reminder)
        # Cancel old reminders for UIDs we're about to schedule
        # This handles the case where an event time changed
        for uid, url_to_times in uids_to_schedule.items():
            for source_url, trigger_times in url_to_times.items():
                self._cancel_old_reminders_for_uid(uid, source_url, trigger_times, state)
        # Now schedule all valid reminders
        for reminder in valid_reminders:
            key = self._reminder_key(reminder)
            task = asyncio.create_task(self._await_and_fire(key, reminder))
            self._scheduled[key] = task
            self._scheduled_reminders[key] = reminder
            self._key_to_feed[key] = state.url
            state.active_keys.add(key)
        stale_keys = state.active_keys - valid_keys
        for key in stale_keys:
            task = self._scheduled.pop(key, None)
            if task:
                task.cancel()
            self._scheduled_reminders.pop(key, None)
            self._key_to_feed.pop(key, None)
        state.active_keys = {key for key in valid_keys if key in self._scheduled}
        if reminders and not valid_reminders:
            self._logger.warning(
                "Calendar feed %s (%s) had %d reminder(s) but none were scheduled "
                "(past events=%d, past triggers=%d, beyond lookahead=%d)",
                state.url,
                state.calendar_name or "unknown",
                len(reminders),
                skipped_past_events,
                skipped_past_triggers,
                skipped_beyond_lookahead,
            )

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
            if not reminder.declined:
                await self._trigger_callback(reminder)
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

    def _cancel_old_reminders_for_uid(
        self,
        uid: str,
        source_url: str,
        new_trigger_times: set[datetime],
        state: _FeedState,
    ) -> None:
        """Cancel any existing scheduled reminders for the same UID but different trigger times.

        This handles the case where an event time changed - we need to cancel the old reminder(s)
        and schedule new one(s) with the updated time.
        """
        keys_to_cancel: list[str] = []
        for key, scheduled_reminder in self._scheduled_reminders.items():
            # Only cancel reminders from the same source URL and UID
            if scheduled_reminder.source_url != source_url or scheduled_reminder.uid != uid:
                continue
            # Cancel if the trigger time is different from any of the new trigger times
            if scheduled_reminder.trigger_time not in new_trigger_times:
                keys_to_cancel.append(key)
        for key in keys_to_cancel:
            scheduled_reminder = self._scheduled_reminders.get(key)
            task = self._scheduled.pop(key, None)
            if task:
                task.cancel()
            self._scheduled_reminders.pop(key, None)
            self._key_to_feed.pop(key, None)
            state.active_keys.discard(key)

    async def _emit_event_snapshot(self) -> None:
        ordered = sorted(self._windowed_events.values(), key=lambda reminder: (reminder.start, reminder.trigger_time))
        if not ordered:
            self._logger.warning(
                "No upcoming calendar events found within the next %d hour(s); check calendar feed configuration",
                self._config.lookahead_hours,
            )
        self._latest_events = list(ordered)
        if self._snapshot_callback:
            try:
                await self._snapshot_callback(list(ordered))
            except Exception:  # pylint: disable=broad-except
                self._logger.exception("Calendar snapshot callback failed")

    def cached_events(self) -> list[CalendarReminder]:
        return list(self._latest_events)

    def _schedule_retry(self, feed_url: str) -> None:
        """Schedule a retry for a failed feed after a short delay."""
        if feed_url in self._retry_tasks:
            # Already has a retry scheduled
            return
        if feed_url not in self._feed_states:
            # Feed no longer exists
            return
        self._failed_feeds.add(feed_url)
        retry_delay = 120  # 2 minutes for retry
        self._logger.warning("Scheduling retry for failed calendar feed %s in %d seconds", feed_url, retry_delay)
        task = asyncio.create_task(self._retry_feed_after_delay(feed_url, retry_delay))
        self._retry_tasks[feed_url] = task

    def _cancel_retry(self, feed_url: str) -> None:
        """Cancel any scheduled retry for a feed that just succeeded."""
        if feed_url in self._retry_tasks:
            task = self._retry_tasks.pop(feed_url)
            task.cancel()
        self._failed_feeds.discard(feed_url)

    async def _retry_feed_after_delay(self, feed_url: str, delay_seconds: float) -> None:
        """Wait for delay then retry a failed feed."""
        try:
            await asyncio.sleep(delay_seconds)
            if self._stop_event.is_set():
                return
            state = self._feed_states.get(feed_url)
            if not state:
                return
            self._logger.warning("Retrying calendar fetch for %s after failure", feed_url)
            now = _now()
            await self._sync_feed(state, now)
            # Emit snapshot after retry to update any changes
            await self._emit_event_snapshot()
        except asyncio.CancelledError:
            pass
        except Exception:  # pylint: disable=broad-except
            self._logger.exception("Error in calendar feed retry for %s", feed_url)
        finally:
            self._retry_tasks.pop(feed_url, None)
