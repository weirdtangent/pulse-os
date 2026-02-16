"""Calendar event management for Pulse Assistant."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from pulse.assistant.calendar_sync import CalendarReminder
from pulse.assistant.schedule_service import ScheduleService

CALENDAR_EVENT_INFO_LIMIT = 25

LOGGER = logging.getLogger(__name__)


class CalendarEventManager:
    """Manages calendar event state, snapshots, and reminder triggering."""

    def __init__(
        self,
        *,
        schedule_service: ScheduleService,
        ooo_summary_marker: str = "OOO",
        calendar_enabled: bool = False,
        calendar_has_feeds: bool = False,
        logger: logging.Logger | None = None,
    ) -> None:
        self._schedule_service = schedule_service
        self._ooo_marker = (ooo_summary_marker or "OOO").lower()
        self._calendar_enabled = calendar_enabled
        self._calendar_has_feeds = calendar_has_feeds
        self._logger = logger or LOGGER
        self._calendar_events: list[dict[str, Any]] = []
        self._calendar_updated_at: float | None = None
        self._on_events_changed: Callable[[list[dict[str, Any]], float | None], None] | None = None

    @property
    def calendar_events(self) -> list[dict[str, Any]]:
        return self._calendar_events

    @property
    def calendar_updated_at(self) -> float | None:
        return self._calendar_updated_at

    def set_events_changed_callback(
        self,
        callback: Callable[[list[dict[str, Any]], float | None], None],
    ) -> None:
        """Register a callback invoked whenever calendar events change.

        The callback receives (events, updated_at).
        """
        self._on_events_changed = callback

    def _notify_events_changed(self) -> None:
        if self._on_events_changed is not None:
            self._on_events_changed(self._calendar_events, self._calendar_updated_at)

    async def trigger_calendar_reminder(self, reminder: CalendarReminder) -> None:
        """Dispatch a calendar reminder as an ephemeral schedule event."""
        label = reminder.summary or "Calendar event"
        local_start = reminder.start.astimezone()
        metadata = {
            "reminder": {"message": label},
            "calendar": {
                "allow_delay": False,
                "calendar_name": reminder.calendar_name,
                "source": reminder.source_url,
                "start": reminder.start.isoformat(),
                "start_local": local_start.isoformat(),
                "end": reminder.end.isoformat() if reminder.end else None,
                "all_day": reminder.all_day,
                "description": reminder.description,
                "location": reminder.location,
                "trigger": reminder.trigger_time.isoformat(),
                "url": reminder.url,
                "uid": reminder.uid,
            },
        }
        try:
            await self._schedule_service.trigger_ephemeral_reminder(
                label=label,
                message=label,
                metadata=metadata,
                auto_clear_seconds=900,
            )
        except Exception as exc:
            self._logger.exception("[calendar] Calendar reminder dispatch failed for %s: %s", label, exc)

    async def handle_calendar_snapshot(self, reminders: list[CalendarReminder]) -> None:
        """Process a full calendar snapshot: deduplicate, extract OOO dates, serialize."""
        unique_reminders = self.deduplicate_calendar_reminders(reminders)
        now = datetime.now().astimezone()
        future_reminders = [r for r in unique_reminders if (r.end or r.start) > now]

        ooo_dates: set[str] = set()
        for reminder in future_reminders:
            if reminder.all_day and self._ooo_marker and self._ooo_marker in (reminder.summary or "").lower():
                start_date = reminder.start.date()
                if reminder.end:
                    try:
                        last = reminder.end.date() - timedelta(days=1)
                    except Exception:
                        last = start_date
                else:
                    last = start_date
                if last < start_date:
                    last = start_date
                current = start_date
                while current <= last:
                    ooo_dates.add(current.isoformat())
                    current += timedelta(days=1)

        await self._schedule_service.set_ooo_skip_dates(ooo_dates)

        events = [self.serialize_calendar_event(r) for r in future_reminders[:CALENDAR_EVENT_INFO_LIMIT]]
        if self._calendar_enabled and self._calendar_has_feeds and not events:
            self._logger.warning(
                "[calendar] Calendar snapshot contained no upcoming events within the lookahead window (now=%s)",
                now.isoformat(),
            )
        self._calendar_events = events
        self._calendar_updated_at = time.time()
        self._notify_events_changed()

    def filter_past_calendar_events(self) -> None:
        """Remove events whose end (or start) has passed."""
        now = datetime.now().astimezone()
        filtered: list[dict[str, Any]] = []
        for event in self._calendar_events:
            start_str = event.get("start")
            end_str = event.get("end")
            if not start_str:
                continue
            try:
                start_dt = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
                if start_dt.tzinfo is None:
                    start_dt = start_dt.replace(tzinfo=now.tzinfo)
                else:
                    start_dt = start_dt.astimezone(now.tzinfo)
                event_end = start_dt
                if end_str:
                    try:
                        end_dt = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
                        if end_dt.tzinfo is None:
                            end_dt = end_dt.replace(tzinfo=now.tzinfo)
                        else:
                            end_dt = end_dt.astimezone(now.tzinfo)
                        event_end = end_dt
                    except (ValueError, AttributeError):
                        pass
                if event_end > now:
                    filtered.append(event)
            except (ValueError, AttributeError):
                filtered.append(event)
        if len(filtered) != len(self._calendar_events):
            self._calendar_events = filtered
            self._notify_events_changed()

    @staticmethod
    def deduplicate_calendar_reminders(reminders: Sequence[CalendarReminder]) -> list[CalendarReminder]:
        """Collapse duplicate events that arise from multiple VALARMs."""
        unique: list[CalendarReminder] = []
        seen: set[tuple[str, str, str]] = set()
        for reminder in reminders:
            key = (
                reminder.source_url or "",
                reminder.uid,
                reminder.start.isoformat(),
            )
            if key in seen:
                continue
            seen.add(key)
            unique.append(reminder)
        return unique

    @staticmethod
    def serialize_calendar_event(reminder: CalendarReminder) -> dict[str, Any]:
        """Convert a CalendarReminder to a serializable dict."""
        local_start = reminder.start.astimezone()
        start_utc = reminder.start.astimezone(UTC)
        payload: dict[str, Any] = {
            "uid": reminder.uid,
            "summary": reminder.summary,
            "description": reminder.description,
            "location": reminder.location,
            "calendar_name": reminder.calendar_name,
            "all_day": reminder.all_day,
            "start": start_utc.isoformat(),
            "start_local": local_start.isoformat(),
            "trigger": reminder.trigger_time.astimezone().isoformat(),
            "source": reminder.source_url,
            "url": reminder.url,
            "declined": reminder.declined,
        }
        if reminder.end:
            payload["end"] = reminder.end.astimezone().isoformat()
        return payload
