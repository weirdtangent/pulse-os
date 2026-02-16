"""Tests for CalendarEventManager."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from unittest.mock import AsyncMock, Mock

import pytest
from pulse.assistant.calendar_manager import CALENDAR_EVENT_INFO_LIMIT, CalendarEventManager
from pulse.assistant.calendar_sync import CalendarReminder


def _make_reminder(
    uid: str = "uid-1",
    summary: str = "Test Event",
    start: datetime | None = None,
    end: datetime | None = None,
    all_day: bool = False,
    calendar_name: str = "Work",
    source_url: str = "https://example.com/cal.ics",
    trigger_time: datetime | None = None,
    description: str | None = None,
    location: str | None = None,
    url: str | None = None,
    declined: bool = False,
) -> CalendarReminder:
    now = datetime.now(UTC)
    return CalendarReminder(
        uid=uid,
        summary=summary,
        description=description,
        location=location,
        start=start or (now + timedelta(hours=1)),
        end=end,
        all_day=all_day,
        trigger_time=trigger_time or (now - timedelta(minutes=10)),
        calendar_name=calendar_name,
        source_url=source_url,
        url=url,
        declined=declined,
    )


@pytest.fixture
def mock_schedule_service():
    service = Mock()
    service.trigger_ephemeral_reminder = AsyncMock()
    service.set_ooo_skip_dates = AsyncMock()
    return service


@pytest.fixture
def manager(mock_schedule_service):
    return CalendarEventManager(
        schedule_service=mock_schedule_service,
        ooo_summary_marker="OOO",
        calendar_enabled=True,
        calendar_has_feeds=True,
    )


class TestDeduplicateCalendarReminders:
    def test_no_duplicates(self) -> None:
        r1 = _make_reminder(uid="a", source_url="https://a.ics")
        r2 = _make_reminder(uid="b", source_url="https://b.ics")
        result = CalendarEventManager.deduplicate_calendar_reminders([r1, r2])
        assert len(result) == 2

    def test_removes_duplicates_by_source_uid_start(self) -> None:
        start = datetime(2025, 6, 1, 10, 0, tzinfo=UTC)
        r1 = _make_reminder(uid="x", source_url="https://cal.ics", start=start)
        r2 = _make_reminder(uid="x", source_url="https://cal.ics", start=start)
        result = CalendarEventManager.deduplicate_calendar_reminders([r1, r2])
        assert len(result) == 1
        assert result[0] is r1

    def test_different_source_urls_not_deduped(self) -> None:
        start = datetime(2025, 6, 1, 10, 0, tzinfo=UTC)
        r1 = _make_reminder(uid="x", source_url="https://a.ics", start=start)
        r2 = _make_reminder(uid="x", source_url="https://b.ics", start=start)
        result = CalendarEventManager.deduplicate_calendar_reminders([r1, r2])
        assert len(result) == 2

    def test_different_start_times_not_deduped(self) -> None:
        r1 = _make_reminder(uid="x", start=datetime(2025, 6, 1, 10, 0, tzinfo=UTC))
        r2 = _make_reminder(uid="x", start=datetime(2025, 6, 2, 10, 0, tzinfo=UTC))
        result = CalendarEventManager.deduplicate_calendar_reminders([r1, r2])
        assert len(result) == 2

    def test_empty_list(self) -> None:
        result = CalendarEventManager.deduplicate_calendar_reminders([])
        assert result == []

    def test_three_duplicates_keeps_first(self) -> None:
        start = datetime(2025, 6, 1, 10, 0, tzinfo=UTC)
        reminders = [
            _make_reminder(
                uid="x", source_url="https://cal.ics", start=start, trigger_time=start - timedelta(minutes=i)
            )
            for i in range(3)
        ]
        result = CalendarEventManager.deduplicate_calendar_reminders(reminders)
        assert len(result) == 1
        assert result[0] is reminders[0]


class TestSerializeCalendarEvent:
    def test_basic_serialization(self) -> None:
        start = datetime(2025, 6, 15, 14, 0, tzinfo=UTC)
        trigger = datetime(2025, 6, 15, 13, 50, tzinfo=UTC)
        reminder = _make_reminder(
            uid="ev-1",
            summary="Team Standup",
            start=start,
            trigger_time=trigger,
            calendar_name="Work",
            source_url="https://cal.ics",
            description="Daily sync",
            location="Room A",
        )
        result = CalendarEventManager.serialize_calendar_event(reminder)
        assert result["uid"] == "ev-1"
        assert result["summary"] == "Team Standup"
        assert result["description"] == "Daily sync"
        assert result["location"] == "Room A"
        assert result["calendar_name"] == "Work"
        assert result["all_day"] is False
        assert result["source"] == "https://cal.ics"
        assert result["declined"] is False
        assert "start" in result
        assert "start_local" in result
        assert "trigger" in result
        assert "end" not in result

    def test_serialization_with_end_time(self) -> None:
        start = datetime(2025, 6, 15, 14, 0, tzinfo=UTC)
        end = datetime(2025, 6, 15, 15, 0, tzinfo=UTC)
        reminder = _make_reminder(uid="ev-2", start=start, end=end)
        result = CalendarEventManager.serialize_calendar_event(reminder)
        assert "end" in result

    def test_start_is_utc_iso(self) -> None:
        eastern = timezone(timedelta(hours=-5))
        start = datetime(2025, 6, 15, 14, 0, tzinfo=eastern)
        reminder = _make_reminder(start=start)
        result = CalendarEventManager.serialize_calendar_event(reminder)
        # start should be in UTC
        parsed = datetime.fromisoformat(result["start"])
        assert parsed.utcoffset() == timedelta(0)

    def test_declined_flag(self) -> None:
        reminder = _make_reminder(declined=True)
        result = CalendarEventManager.serialize_calendar_event(reminder)
        assert result["declined"] is True

    def test_url_included(self) -> None:
        reminder = _make_reminder(url="https://meet.google.com/abc")
        result = CalendarEventManager.serialize_calendar_event(reminder)
        assert result["url"] == "https://meet.google.com/abc"


class TestTriggerCalendarReminder:
    @pytest.mark.anyio
    async def test_dispatches_ephemeral_reminder(self, manager, mock_schedule_service) -> None:
        reminder = _make_reminder(summary="Doctor appointment")
        await manager.trigger_calendar_reminder(reminder)
        mock_schedule_service.trigger_ephemeral_reminder.assert_called_once()
        call_kwargs = mock_schedule_service.trigger_ephemeral_reminder.call_args.kwargs
        assert call_kwargs["label"] == "Doctor appointment"
        assert call_kwargs["message"] == "Doctor appointment"
        assert call_kwargs["auto_clear_seconds"] == 900
        metadata = call_kwargs["metadata"]
        assert metadata["reminder"]["message"] == "Doctor appointment"
        assert metadata["calendar"]["uid"] == reminder.uid

    @pytest.mark.anyio
    async def test_uses_fallback_label(self, manager, mock_schedule_service) -> None:
        reminder = _make_reminder(summary="")
        await manager.trigger_calendar_reminder(reminder)
        call_kwargs = mock_schedule_service.trigger_ephemeral_reminder.call_args.kwargs
        assert call_kwargs["label"] == "Calendar event"

    @pytest.mark.anyio
    async def test_exception_is_logged_not_raised(self, manager, mock_schedule_service) -> None:
        mock_schedule_service.trigger_ephemeral_reminder.side_effect = RuntimeError("boom")
        reminder = _make_reminder(summary="Failing event")
        # Should not raise
        await manager.trigger_calendar_reminder(reminder)

    @pytest.mark.anyio
    async def test_metadata_includes_calendar_fields(self, manager, mock_schedule_service) -> None:
        start = datetime(2025, 7, 1, 9, 0, tzinfo=UTC)
        end = datetime(2025, 7, 1, 10, 0, tzinfo=UTC)
        trigger = datetime(2025, 7, 1, 8, 50, tzinfo=UTC)
        reminder = _make_reminder(
            uid="cal-123",
            summary="Meeting",
            start=start,
            end=end,
            trigger_time=trigger,
            calendar_name="Personal",
            source_url="https://cal.ics",
            description="Discuss roadmap",
            location="Zoom",
            url="https://zoom.us/j/123",
        )
        await manager.trigger_calendar_reminder(reminder)
        cal_meta = mock_schedule_service.trigger_ephemeral_reminder.call_args.kwargs["metadata"]["calendar"]
        assert cal_meta["calendar_name"] == "Personal"
        assert cal_meta["uid"] == "cal-123"
        assert cal_meta["location"] == "Zoom"
        assert cal_meta["url"] == "https://zoom.us/j/123"
        assert cal_meta["all_day"] is False


class TestHandleCalendarSnapshot:
    @pytest.mark.anyio
    async def test_basic_snapshot_populates_events(self, manager) -> None:
        reminders = [_make_reminder(uid="a"), _make_reminder(uid="b")]
        await manager.handle_calendar_snapshot(reminders)
        assert len(manager.calendar_events) == 2
        assert manager.calendar_updated_at is not None

    @pytest.mark.anyio
    async def test_filters_past_events(self, manager) -> None:
        past = _make_reminder(
            uid="old",
            start=datetime.now(UTC) - timedelta(hours=2),
            end=datetime.now(UTC) - timedelta(hours=1),
        )
        future = _make_reminder(uid="new", start=datetime.now(UTC) + timedelta(hours=1))
        await manager.handle_calendar_snapshot([past, future])
        assert len(manager.calendar_events) == 1
        assert manager.calendar_events[0]["uid"] == "new"

    @pytest.mark.anyio
    async def test_deduplicates_within_snapshot(self, manager) -> None:
        start = datetime.now(UTC) + timedelta(hours=1)
        r1 = _make_reminder(uid="dup", source_url="https://cal.ics", start=start)
        r2 = _make_reminder(uid="dup", source_url="https://cal.ics", start=start)
        await manager.handle_calendar_snapshot([r1, r2])
        assert len(manager.calendar_events) == 1

    @pytest.mark.anyio
    async def test_ooo_dates_sent_to_schedule_service(self, manager, mock_schedule_service) -> None:
        tomorrow = (datetime.now(UTC) + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        day_after = tomorrow + timedelta(days=1)
        ooo = _make_reminder(
            uid="ooo-1",
            summary="OOO - Vacation",
            start=tomorrow,
            end=day_after,
            all_day=True,
        )
        await manager.handle_calendar_snapshot([ooo])
        mock_schedule_service.set_ooo_skip_dates.assert_called_once()
        ooo_dates = mock_schedule_service.set_ooo_skip_dates.call_args[0][0]
        # All-day ICS end is exclusive, so only the start date should be in the set
        assert tomorrow.date().isoformat() in ooo_dates

    @pytest.mark.anyio
    async def test_non_ooo_events_dont_set_skip_dates(self, manager, mock_schedule_service) -> None:
        reminder = _make_reminder(
            uid="normal",
            summary="Regular meeting",
            start=datetime.now(UTC) + timedelta(hours=1),
        )
        await manager.handle_calendar_snapshot([reminder])
        ooo_dates = mock_schedule_service.set_ooo_skip_dates.call_args[0][0]
        assert len(ooo_dates) == 0

    @pytest.mark.anyio
    async def test_events_changed_callback_fired(self, manager) -> None:
        callback = Mock()
        manager.set_events_changed_callback(callback)
        reminders = [_make_reminder(uid="x")]
        await manager.handle_calendar_snapshot(reminders)
        callback.assert_called_once()
        events, updated_at = callback.call_args[0]
        assert len(events) == 1
        assert updated_at is not None

    @pytest.mark.anyio
    async def test_respects_event_limit(self, manager) -> None:
        reminders = [
            _make_reminder(uid=f"ev-{i}", start=datetime.now(UTC) + timedelta(hours=i + 1))
            for i in range(CALENDAR_EVENT_INFO_LIMIT + 5)
        ]
        await manager.handle_calendar_snapshot(reminders)
        assert len(manager.calendar_events) == CALENDAR_EVENT_INFO_LIMIT

    @pytest.mark.anyio
    async def test_empty_snapshot_clears_events(self, manager) -> None:
        await manager.handle_calendar_snapshot([_make_reminder()])
        assert len(manager.calendar_events) == 1
        await manager.handle_calendar_snapshot([])
        assert len(manager.calendar_events) == 0


class TestFilterPastCalendarEvents:
    def test_removes_ended_events(self, manager) -> None:
        past_start = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
        past_end = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        future_start = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        future_end = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
        manager._calendar_events = [
            {"uid": "past", "summary": "Past", "start": past_start, "end": past_end},
            {"uid": "future", "summary": "Future", "start": future_start, "end": future_end},
        ]
        callback = Mock()
        manager.set_events_changed_callback(callback)
        manager.filter_past_calendar_events()
        assert len(manager.calendar_events) == 1
        assert manager.calendar_events[0]["uid"] == "future"
        callback.assert_called_once()

    def test_no_change_when_all_future(self, manager) -> None:
        future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        manager._calendar_events = [
            {"uid": "a", "summary": "A", "start": future},
        ]
        callback = Mock()
        manager.set_events_changed_callback(callback)
        manager.filter_past_calendar_events()
        assert len(manager.calendar_events) == 1
        callback.assert_not_called()

    def test_keeps_event_with_unparseable_date(self, manager) -> None:
        manager._calendar_events = [
            {"uid": "bad", "summary": "Bad", "start": "not-a-date"},
        ]
        manager.filter_past_calendar_events()
        assert len(manager.calendar_events) == 1

    def test_uses_end_time_when_available(self, manager) -> None:
        # Event started in the past but hasn't ended yet
        past_start = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        future_end = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        manager._calendar_events = [
            {"uid": "ongoing", "summary": "Ongoing", "start": past_start, "end": future_end},
        ]
        manager.filter_past_calendar_events()
        assert len(manager.calendar_events) == 1

    def test_utc_z_suffix_handled(self, manager) -> None:
        future = datetime.now(UTC) + timedelta(hours=1)
        # Use Z suffix instead of +00:00
        z_format = future.strftime("%Y-%m-%dT%H:%M:%SZ")
        manager._calendar_events = [
            {"uid": "z", "summary": "Z-format", "start": z_format},
        ]
        manager.filter_past_calendar_events()
        assert len(manager.calendar_events) == 1

    def test_skips_events_without_start(self, manager) -> None:
        manager._calendar_events = [
            {"uid": "no-start", "summary": "Missing start"},
        ]
        manager.filter_past_calendar_events()
        # Event without start is dropped
        assert len(manager.calendar_events) == 0
