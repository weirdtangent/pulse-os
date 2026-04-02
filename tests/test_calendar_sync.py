from __future__ import annotations

import asyncio
import unittest
from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from icalendar import Calendar  # type: ignore[import-untyped]
from pulse.assistant.calendar_sync import (
    CalendarReminder,
    CalendarSyncService,
    _FeedState,
    _guess_google_calendar_email,
    _normalize_attendee_identifier,
    _owner_tokens_for_feed,
)
from pulse.assistant.config import CalendarConfig


async def _noop_trigger(_reminder) -> None:
    return None


class CalendarSyncParserTests(unittest.TestCase):
    def setUp(self) -> None:
        config = CalendarConfig(
            enabled=True,
            feeds=("https://example.com/calendar.ics",),
            refresh_minutes=5,
            lookahead_hours=72,
            attendee_emails=("user@example.com",),
            default_notifications=(),
            hide_declined_events=False,
        )
        self.service = CalendarSyncService(config=config, trigger_callback=_noop_trigger)
        self.feed_state = self.service._feed_states[config.feeds[0]]

    def _collect(self, ics_text: str) -> list:
        calendar = Calendar.from_ical(ics_text.encode("utf-8"))
        now = datetime.now(UTC).astimezone()
        return self.service._collect_reminders(calendar, self.feed_state, now)

    def test_valarm_trigger_is_honored(self) -> None:
        event_start = datetime.now(UTC) + timedelta(hours=6)
        dtstart = event_start.strftime("%Y%m%dT%H%M%SZ")
        ics = f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Pulse Test//EN
BEGIN:VEVENT
UID:event-123
DTSTART:{dtstart}
SUMMARY:Project review
BEGIN:VALARM
ACTION:DISPLAY
TRIGGER:-PT30M
DESCRIPTION:Heads up
END:VALARM
END:VEVENT
END:VCALENDAR
"""
        reminders = self._collect(ics)
        self.assertEqual(len(reminders), 1)
        expected_trigger = event_start - timedelta(minutes=30)
        self.assertAlmostEqual(
            reminders[0].trigger_time.astimezone(UTC).timestamp(),
            expected_trigger.timestamp(),
            delta=2,
        )

    def test_all_day_event_fires_day_before_at_noon(self) -> None:
        future_date = (datetime.now(UTC) + timedelta(days=2)).strftime("%Y%m%d")
        ics = f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Pulse Test//EN
BEGIN:VEVENT
UID:event-456
DTSTART;VALUE=DATE:{future_date}
SUMMARY:All day thing
END:VEVENT
END:VCALENDAR
"""
        reminders = self._collect(ics)
        self.assertEqual(len(reminders), 1)
        start = reminders[0].start
        trigger = reminders[0].trigger_time
        self.assertEqual(start.date() - timedelta(days=1), trigger.date())
        self.assertEqual((start - trigger), timedelta(hours=12))

    def test_reminder_key_is_stable(self) -> None:
        event_start = (datetime.now(UTC) + timedelta(hours=6)).strftime("%Y%m%dT%H%M%SZ")
        ics = f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Pulse Test//EN
BEGIN:VEVENT
UID:event-789
DTSTART:{event_start}
SUMMARY:Demo
END:VEVENT
END:VCALENDAR
"""
        reminder_a = self._collect(ics)[0]
        reminder_b = self._collect(ics)[0]
        key_a = self.service._reminder_key(reminder_a)
        key_b = self.service._reminder_key(reminder_b)
        self.assertEqual(key_a, key_b)

    def test_multiple_valarms_emit_multiple_reminders(self) -> None:
        event_start = datetime.now(UTC) + timedelta(hours=6)
        dtstart = event_start.strftime("%Y%m%dT%H%M%SZ")
        ics = f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Pulse Test//EN
BEGIN:VEVENT
UID:event-456
DTSTART:{dtstart}
SUMMARY:Double ping
BEGIN:VALARM
ACTION:DISPLAY
TRIGGER:-PT20M
END:VALARM
BEGIN:VALARM
ACTION:DISPLAY
TRIGGER:-PT5M
END:VALARM
END:VEVENT
END:VCALENDAR
"""
        reminders = self._collect(ics)
        self.assertEqual(len(reminders), 2)
        triggers = sorted(reminder.trigger_time for reminder in reminders)
        expected_first = event_start - timedelta(minutes=20)
        expected_second = event_start - timedelta(minutes=5)
        self.assertAlmostEqual(triggers[0].astimezone(UTC).timestamp(), expected_first.timestamp(), delta=2)
        self.assertAlmostEqual(triggers[1].astimezone(UTC).timestamp(), expected_second.timestamp(), delta=2)

    def test_declined_status_is_detected_for_owner(self) -> None:
        event_start = (datetime.now(UTC) + timedelta(hours=6)).strftime("%Y%m%dT%H%M%SZ")
        ics = f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Pulse Test//EN
BEGIN:VEVENT
UID:event-901
DTSTART:{event_start}
SUMMARY:Skip me
ATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=DECLINED;CN=User;X-NUM-GUESTS=0:mailto:user@example.com
END:VEVENT
END:VCALENDAR
"""
        reminders = self._collect(ics)
        self.assertEqual(len(reminders), 1)
        self.assertTrue(reminders[0].declined)

    def test_other_attendee_decline_is_ignored(self) -> None:
        event_start = (datetime.now(UTC) + timedelta(hours=6)).strftime("%Y%m%dT%H%M%SZ")
        ics = f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Pulse Test//EN
BEGIN:VEVENT
UID:event-902
DTSTART:{event_start}
SUMMARY:Still attending
ATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;CN=User;X-NUM-GUESTS=0:mailto:user@example.com
ATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=DECLINED;CN=Other;X-NUM-GUESTS=0:mailto:other@example.com
END:VEVENT
END:VCALENDAR
"""
        reminders = self._collect(ics)
        self.assertEqual(len(reminders), 1)
        self.assertFalse(reminders[0].declined)

    def test_declined_event_excluded_from_window(self) -> None:
        now = datetime(2025, 1, 1, 9, 0, tzinfo=UTC).astimezone()
        declined_reminder = CalendarReminder(
            uid="event-declined",
            summary="Declined meeting",
            description=None,
            location=None,
            start=now + timedelta(hours=1),
            end=None,
            all_day=False,
            trigger_time=now + timedelta(minutes=50),
            calendar_name="Work",
            source_url=self.feed_state.url,
            declined=True,
        )
        accepted_reminder = CalendarReminder(
            uid="event-accepted",
            summary="Accepted meeting",
            description=None,
            location=None,
            start=now + timedelta(hours=2),
            end=None,
            all_day=False,
            trigger_time=now + timedelta(hours=1, minutes=50),
            calendar_name="Work",
            source_url=self.feed_state.url,
            declined=False,
        )

        async def _run_schedule() -> None:
            await self.service._schedule_reminders(self.feed_state, [declined_reminder, accepted_reminder], now)
            await asyncio.sleep(0)

        asyncio.run(_run_schedule())
        windowed = list(self.service._windowed_events.values())
        self.assertEqual(len(windowed), 1)
        self.assertEqual(windowed[0].uid, "event-accepted")

    def test_rrule_weekly_event_expands(self) -> None:
        """Recurring weekly event should produce instances within the lookahead window."""
        tomorrow = datetime.now(UTC) + timedelta(days=1)
        dtstart = tomorrow.strftime("%Y%m%dT%H%M%SZ")
        ics = f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Pulse Test//EN
BEGIN:VEVENT
UID:recurring-weekly
DTSTART:{dtstart}
DURATION:PT1H
SUMMARY:Weekly standup
RRULE:FREQ=DAILY;COUNT=5
END:VEVENT
END:VCALENDAR
"""
        reminders = self._collect(ics)
        # With 72h lookahead, should get 3 occurrences (tomorrow, +1d, +2d)
        self.assertGreaterEqual(len(reminders), 3)
        starts = sorted({r.start for r in reminders})
        self.assertGreaterEqual(len(starts), 3)
        # Each occurrence should be 1 day apart
        for i in range(1, len(starts)):
            self.assertEqual((starts[i] - starts[i - 1]).days, 1)

    def test_rrule_with_recurrence_id_override(self) -> None:
        """A modified instance (RECURRENCE-ID) should replace the RRULE occurrence."""
        tomorrow = datetime.now(UTC) + timedelta(days=1)
        dtstart = tomorrow.strftime("%Y%m%dT%H%M%SZ")
        day_after = (tomorrow + timedelta(days=1)).strftime("%Y%m%dT%H%M%SZ")
        # The RRULE generates daily, but the second occurrence is overridden with a new summary
        ics = f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Pulse Test//EN
BEGIN:VEVENT
UID:recurring-override
DTSTART:{dtstart}
DURATION:PT1H
SUMMARY:Original title
RRULE:FREQ=DAILY;COUNT=3
END:VEVENT
BEGIN:VEVENT
UID:recurring-override
RECURRENCE-ID:{day_after}
DTSTART:{day_after}
DURATION:PT1H
SUMMARY:Modified title
END:VEVENT
END:VCALENDAR
"""
        reminders = self._collect(ics)
        summaries = {r.summary for r in reminders}
        self.assertIn("Original title", summaries)
        self.assertIn("Modified title", summaries)

    def test_reminder_window_includes_far_event_with_near_trigger(self) -> None:
        now = datetime(2025, 1, 1, 9, 0, tzinfo=UTC).astimezone()
        reminder = CalendarReminder(
            uid="event-far",
            summary="Future trip",
            description=None,
            location=None,
            start=now + timedelta(days=7),
            end=None,
            all_day=False,
            trigger_time=now,
            calendar_name="Personal",
            source_url=self.feed_state.url,
        )

        async def _run_schedule() -> None:
            await self.service._schedule_reminders(self.feed_state, [reminder], now)
            await asyncio.sleep(0)

        asyncio.run(_run_schedule())
        windowed = list(self.service._windowed_events.values())
        self.assertEqual(len(windowed), 1)
        self.assertEqual(windowed[0].uid, reminder.uid)


# ---------------------------------------------------------------------------
# Helper: standalone function tests
# ---------------------------------------------------------------------------


class TestNormalizeAttendeeIdentifier(unittest.TestCase):
    def test_none_returns_empty(self) -> None:
        assert _normalize_attendee_identifier(None) == ""

    def test_strips_mailto_prefix(self) -> None:
        assert _normalize_attendee_identifier("mailto:Bob@Example.COM") == "bob@example.com"

    def test_plain_email_lowered(self) -> None:
        assert _normalize_attendee_identifier("  Alice@Work.ORG  ") == "alice@work.org"

    def test_non_string_converted(self) -> None:
        assert _normalize_attendee_identifier(123) == "123"


class TestGuessGoogleCalendarEmail(unittest.TestCase):
    def test_standard_google_url(self) -> None:
        url = "https://calendar.google.com/calendar/ical/user%40gmail.com/public/basic.ics"
        assert _guess_google_calendar_email(url) == "user@gmail.com"

    def test_non_google_url_returns_none(self) -> None:
        assert _guess_google_calendar_email("https://example.com/cal.ics") is None

    def test_no_ical_segment_returns_none(self) -> None:
        url = "https://calendar.google.com/calendar/something/user%40gmail.com/basic.ics"
        assert _guess_google_calendar_email(url) is None

    def test_ical_at_end_returns_none(self) -> None:
        url = "https://calendar.google.com/calendar/ical"
        assert _guess_google_calendar_email(url) is None

    def test_empty_calendar_id_returns_none(self) -> None:
        url = "https://calendar.google.com/calendar/ical/%20/basic.ics"
        assert _guess_google_calendar_email(url) is None


class TestOwnerTokensForFeed(unittest.TestCase):
    def test_combines_config_emails_and_guessed(self) -> None:
        config = CalendarConfig(
            enabled=True,
            feeds=(),
            refresh_minutes=5,
            lookahead_hours=72,
            attendee_emails=("me@example.com",),
            default_notifications=(),
            hide_declined_events=False,
        )
        url = "https://calendar.google.com/calendar/ical/cal%40gmail.com/public/basic.ics"
        tokens = _owner_tokens_for_feed(url, config)
        assert "me@example.com" in tokens
        assert "cal@gmail.com" in tokens

    def test_empty_emails_only_guessed(self) -> None:
        config = CalendarConfig(
            enabled=True,
            feeds=(),
            refresh_minutes=5,
            lookahead_hours=72,
            attendee_emails=(),
            default_notifications=(),
            hide_declined_events=False,
        )
        url = "https://example.com/cal.ics"
        tokens = _owner_tokens_for_feed(url, config)
        assert tokens == set()


# ---------------------------------------------------------------------------
# _coerce_datetime
# ---------------------------------------------------------------------------


class TestCoerceDatetime(unittest.TestCase):
    def setUp(self) -> None:
        config = CalendarConfig(
            enabled=True,
            feeds=("https://example.com/cal.ics",),
            refresh_minutes=5,
            lookahead_hours=72,
            attendee_emails=(),
            default_notifications=(),
            hide_declined_events=False,
        )
        self.service = CalendarSyncService(config=config, trigger_callback=_noop_trigger)

    def test_naive_datetime_gets_tz(self) -> None:
        naive = datetime(2025, 6, 1, 10, 0)
        result, all_day = self.service._coerce_datetime(naive, UTC)
        assert result is not None
        assert result.tzinfo is not None
        assert not all_day

    def test_aware_datetime_converted(self) -> None:
        aware = datetime(2025, 6, 1, 10, 0, tzinfo=UTC)
        result, all_day = self.service._coerce_datetime(aware, UTC)
        assert result is not None
        assert not all_day

    def test_date_becomes_midnight_all_day(self) -> None:
        d = date(2025, 6, 1)
        result, all_day = self.service._coerce_datetime(d, UTC)
        assert result is not None
        assert all_day
        assert result.hour == 0 and result.minute == 0

    def test_unsupported_type_returns_none(self) -> None:
        result, all_day = self.service._coerce_datetime("not a date", UTC)
        assert result is None
        assert not all_day


# ---------------------------------------------------------------------------
# _default_trigger
# ---------------------------------------------------------------------------


class TestDefaultTrigger(unittest.TestCase):
    def setUp(self) -> None:
        config = CalendarConfig(
            enabled=True,
            feeds=("https://example.com/cal.ics",),
            refresh_minutes=5,
            lookahead_hours=72,
            attendee_emails=(),
            default_notifications=(),
            hide_declined_events=False,
        )
        self.service = CalendarSyncService(config=config, trigger_callback=_noop_trigger)

    def test_timed_event_5_minutes_before(self) -> None:
        start = datetime(2025, 6, 1, 10, 0, tzinfo=UTC)
        trigger = self.service._default_trigger(start, all_day=False)
        assert trigger == start - timedelta(minutes=5)

    def test_all_day_event_noon_day_before(self) -> None:
        start = datetime(2025, 6, 1, 0, 0, tzinfo=UTC)
        trigger = self.service._default_trigger(start, all_day=True)
        assert trigger.date() == date(2025, 5, 31)
        assert trigger.hour == 12


# ---------------------------------------------------------------------------
# _feed_label
# ---------------------------------------------------------------------------


class TestFeedLabel(unittest.TestCase):
    def test_none_state(self) -> None:
        config = CalendarConfig(
            enabled=True,
            feeds=("https://example.com/cal.ics",),
            refresh_minutes=5,
            lookahead_hours=72,
            attendee_emails=(),
            default_notifications=(),
            hide_declined_events=False,
        )
        svc = CalendarSyncService(config=config, trigger_callback=_noop_trigger)
        assert svc._feed_label(None) == "calendar"

    def test_calendar_name_preferred(self) -> None:
        state = _FeedState(url="http://x", calendar_name="My Cal", label="cal 1")
        config = CalendarConfig(
            enabled=True,
            feeds=("http://x",),
            refresh_minutes=5,
            lookahead_hours=72,
            attendee_emails=(),
            default_notifications=(),
            hide_declined_events=False,
        )
        svc = CalendarSyncService(config=config, trigger_callback=_noop_trigger)
        assert svc._feed_label(state) == "My Cal"

    def test_label_fallback(self) -> None:
        state = _FeedState(url="http://x", label="cal 1")
        config = CalendarConfig(
            enabled=True,
            feeds=("http://x",),
            refresh_minutes=5,
            lookahead_hours=72,
            attendee_emails=(),
            default_notifications=(),
            hide_declined_events=False,
        )
        svc = CalendarSyncService(config=config, trigger_callback=_noop_trigger)
        assert svc._feed_label(state) == "cal 1"

    def test_no_name_no_label(self) -> None:
        state = _FeedState(url="http://x")
        config = CalendarConfig(
            enabled=True,
            feeds=("http://x",),
            refresh_minutes=5,
            lookahead_hours=72,
            attendee_emails=(),
            default_notifications=(),
            hide_declined_events=False,
        )
        svc = CalendarSyncService(config=config, trigger_callback=_noop_trigger)
        assert svc._feed_label(state) == "calendar"


# ---------------------------------------------------------------------------
# _prune_triggered
# ---------------------------------------------------------------------------


class TestPruneTriggered(unittest.TestCase):
    def test_removes_old_entries(self) -> None:
        config = CalendarConfig(
            enabled=True,
            feeds=("https://example.com/cal.ics",),
            refresh_minutes=5,
            lookahead_hours=72,
            attendee_emails=(),
            default_notifications=(),
            hide_declined_events=False,
        )
        svc = CalendarSyncService(config=config, trigger_callback=_noop_trigger)
        now = datetime.now(UTC)
        svc._triggered["old"] = now - timedelta(days=10)
        svc._triggered["recent"] = now - timedelta(days=1)
        svc._prune_triggered(now)
        assert "old" not in svc._triggered
        assert "recent" in svc._triggered


# ---------------------------------------------------------------------------
# cached_events
# ---------------------------------------------------------------------------


class TestCachedEvents(unittest.TestCase):
    def test_returns_copy(self) -> None:
        config = CalendarConfig(
            enabled=True,
            feeds=("https://example.com/cal.ics",),
            refresh_minutes=5,
            lookahead_hours=72,
            attendee_emails=(),
            default_notifications=(),
            hide_declined_events=False,
        )
        svc = CalendarSyncService(config=config, trigger_callback=_noop_trigger)
        now = datetime.now(UTC)
        reminder = CalendarReminder(
            uid="e1",
            summary="Test",
            description=None,
            location=None,
            start=now,
            end=None,
            all_day=False,
            trigger_time=now,
            calendar_name=None,
            source_url="http://x",
        )
        svc._latest_events = [reminder]
        result = svc.cached_events()
        assert result == [reminder]
        assert result is not svc._latest_events


# ---------------------------------------------------------------------------
# _extract_alarm_triggers
# ---------------------------------------------------------------------------


class TestExtractAlarmTriggers(unittest.TestCase):
    def setUp(self) -> None:
        config = CalendarConfig(
            enabled=True,
            feeds=("https://example.com/cal.ics",),
            refresh_minutes=5,
            lookahead_hours=72,
            attendee_emails=(),
            default_notifications=(),
            hide_declined_events=False,
        )
        self.service = CalendarSyncService(config=config, trigger_callback=_noop_trigger)

    def test_non_display_action_skipped(self) -> None:
        """VALARM with ACTION:EMAIL should be ignored."""
        event_start = datetime.now(UTC) + timedelta(hours=6)
        dtstart = event_start.strftime("%Y%m%dT%H%M%SZ")
        ics = f"""BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:alarm-email
DTSTART:{dtstart}
SUMMARY:Email alarm
BEGIN:VALARM
ACTION:EMAIL
TRIGGER:-PT10M
END:VALARM
END:VEVENT
END:VCALENDAR
"""
        cal = Calendar.from_ical(ics.encode())
        for comp in cal.walk("VEVENT"):
            triggers = self.service._extract_alarm_triggers(comp, event_start, UTC)
            assert len(triggers) == 0

    def test_absolute_datetime_trigger(self) -> None:
        """VALARM with absolute TRIGGER datetime."""
        event_start = datetime(2025, 7, 1, 14, 0, tzinfo=UTC)
        trigger_dt = datetime(2025, 7, 1, 13, 0, tzinfo=UTC)
        dtstart = event_start.strftime("%Y%m%dT%H%M%SZ")
        trigger_str = trigger_dt.strftime("%Y%m%dT%H%M%SZ")
        ics = f"""BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:alarm-abs
DTSTART:{dtstart}
SUMMARY:Absolute alarm
BEGIN:VALARM
ACTION:DISPLAY
TRIGGER;VALUE=DATE-TIME:{trigger_str}
END:VALARM
END:VEVENT
END:VCALENDAR
"""
        cal = Calendar.from_ical(ics.encode())
        for comp in cal.walk("VEVENT"):
            triggers = self.service._extract_alarm_triggers(comp, event_start, UTC)
            assert len(triggers) == 1
            assert abs((triggers[0] - trigger_dt).total_seconds()) < 2

    def test_no_subcomponents(self) -> None:
        """Component with no alarms returns empty list."""
        event_start = datetime(2025, 7, 1, 14, 0, tzinfo=UTC)
        mock_comp = MagicMock()
        mock_comp.subcomponents = []
        triggers = self.service._extract_alarm_triggers(mock_comp, event_start, UTC)
        assert triggers == []


# ---------------------------------------------------------------------------
# _event_declined - edge cases
# ---------------------------------------------------------------------------


class TestEventDeclinedEdgeCases(unittest.TestCase):
    def setUp(self) -> None:
        config = CalendarConfig(
            enabled=True,
            feeds=("https://example.com/cal.ics",),
            refresh_minutes=5,
            lookahead_hours=72,
            attendee_emails=("user@example.com",),
            default_notifications=(),
            hide_declined_events=False,
        )
        self.service = CalendarSyncService(config=config, trigger_callback=_noop_trigger)
        self.owner_tokens = {"user@example.com"}

    def test_no_attendees_returns_false(self) -> None:
        comp = MagicMock()
        comp.get.return_value = None
        assert self.service._event_declined(comp, self.owner_tokens) is False

    def test_empty_owner_tokens_returns_false(self) -> None:
        comp = MagicMock()
        assert self.service._event_declined(comp, set()) is False

    def test_single_attendee_not_list(self) -> None:
        """When ATTENDEE is a single value (not a list), it should still be processed."""
        attendee = MagicMock(**{"__str__.return_value": "mailto:user@example.com"})
        attendee.params = {"PARTSTAT": "DECLINED"}
        comp = MagicMock()
        comp.get.return_value = attendee
        assert self.service._event_declined(comp, self.owner_tokens) is True

    def test_bytes_partstat(self) -> None:
        """PARTSTAT stored as bytes should be decoded."""
        attendee = MagicMock(**{"__str__.return_value": "mailto:user@example.com"})
        attendee.params = {"PARTSTAT": b"DECLINED"}
        comp = MagicMock()
        comp.get.return_value = attendee
        assert self.service._event_declined(comp, self.owner_tokens) is True

    def test_attendee_with_email_param(self) -> None:
        """Use the EMAIL param for matching when available."""
        attendee = MagicMock(**{"__str__.return_value": "mailto:someone-else@example.com"})
        attendee.params = {"EMAIL": "user@example.com", "PARTSTAT": "DECLINED"}
        comp = MagicMock()
        comp.get.return_value = [attendee]
        assert self.service._event_declined(comp, self.owner_tokens) is True


# ---------------------------------------------------------------------------
# _process_vtodo
# ---------------------------------------------------------------------------


class TestProcessVtodo(unittest.TestCase):
    def setUp(self) -> None:
        self.config = CalendarConfig(
            enabled=True,
            feeds=("https://example.com/cal.ics",),
            refresh_minutes=5,
            lookahead_hours=72,
            attendee_emails=(),
            default_notifications=(),
            hide_declined_events=False,
        )
        self.service = CalendarSyncService(config=self.config, trigger_callback=_noop_trigger)
        self.feed_state = self.service._feed_states[self.config.feeds[0]]
        self.now = datetime.now(UTC).astimezone()

    def _make_ics_with_vtodo(self, vtodo_body: str) -> str:
        return f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//EN
{vtodo_body}
END:VCALENDAR
"""

    def test_basic_vtodo_with_due(self) -> None:
        due = (self.now + timedelta(hours=3)).strftime("%Y%m%dT%H%M%SZ")
        ics = self._make_ics_with_vtodo(f"""BEGIN:VTODO
UID:todo-1
DUE:{due}
SUMMARY:My task
END:VTODO""")
        cal = Calendar.from_ical(ics.encode())
        reminders = self.service._collect_reminders(cal, self.feed_state, self.now)
        assert len(reminders) >= 1
        assert reminders[0].summary == "My task"

    def test_completed_vtodo_skipped(self) -> None:
        due = (self.now + timedelta(hours=3)).strftime("%Y%m%dT%H%M%SZ")
        ics = self._make_ics_with_vtodo(f"""BEGIN:VTODO
UID:todo-done
DUE:{due}
SUMMARY:Done task
STATUS:COMPLETED
END:VTODO""")
        cal = Calendar.from_ical(ics.encode())
        reminders = self.service._collect_reminders(cal, self.feed_state, self.now)
        assert all(r.uid != "todo-done" for r in reminders)

    def test_cancelled_vtodo_skipped(self) -> None:
        due = (self.now + timedelta(hours=3)).strftime("%Y%m%dT%H%M%SZ")
        ics = self._make_ics_with_vtodo(f"""BEGIN:VTODO
UID:todo-cancel
DUE:{due}
SUMMARY:Cancelled task
STATUS:CANCELLED
END:VTODO""")
        cal = Calendar.from_ical(ics.encode())
        reminders = self.service._collect_reminders(cal, self.feed_state, self.now)
        assert all(r.uid != "todo-cancel" for r in reminders)

    def test_vtodo_no_uid_skipped(self) -> None:
        due = (self.now + timedelta(hours=3)).strftime("%Y%m%dT%H%M%SZ")
        ics = self._make_ics_with_vtodo(f"""BEGIN:VTODO
DUE:{due}
SUMMARY:No UID
END:VTODO""")
        cal = Calendar.from_ical(ics.encode())
        for comp in cal.walk("VTODO"):
            result = self.service._process_vtodo(comp, self.feed_state, self.now)
            assert result == []

    def test_vtodo_with_dtstart_fallback(self) -> None:
        start = (self.now + timedelta(hours=2)).strftime("%Y%m%dT%H%M%SZ")
        ics = self._make_ics_with_vtodo(f"""BEGIN:VTODO
UID:todo-start
DTSTART:{start}
SUMMARY:Start-only task
END:VTODO""")
        cal = Calendar.from_ical(ics.encode())
        reminders = self.service._collect_reminders(cal, self.feed_state, self.now)
        assert any(r.uid == "todo-start" for r in reminders)


# ---------------------------------------------------------------------------
# _cancel_old_reminders_for_uid
# ---------------------------------------------------------------------------


class TestCancelOldRemindersForUid(unittest.TestCase):
    def test_cancels_stale_trigger_times(self) -> None:
        config = CalendarConfig(
            enabled=True,
            feeds=("https://example.com/cal.ics",),
            refresh_minutes=5,
            lookahead_hours=72,
            attendee_emails=(),
            default_notifications=(),
            hide_declined_events=False,
        )
        svc = CalendarSyncService(config=config, trigger_callback=_noop_trigger)
        state = svc._feed_states[config.feeds[0]]
        now = datetime.now(UTC)
        old_trigger = now + timedelta(hours=1)
        new_trigger = now + timedelta(hours=2)

        old_reminder = CalendarReminder(
            uid="e1",
            summary="Test",
            description=None,
            location=None,
            start=now + timedelta(hours=3),
            end=None,
            all_day=False,
            trigger_time=old_trigger,
            calendar_name=None,
            source_url="https://example.com/cal.ics",
        )
        old_key = svc._reminder_key(old_reminder)
        mock_task = MagicMock()
        svc._scheduled[old_key] = mock_task
        svc._scheduled_reminders[old_key] = old_reminder
        svc._key_to_feed[old_key] = state.url
        state.active_keys.add(old_key)

        svc._cancel_old_reminders_for_uid("e1", "https://example.com/cal.ics", {new_trigger}, state)
        assert old_key not in svc._scheduled
        assert old_key not in svc._scheduled_reminders
        mock_task.cancel.assert_called_once()


# ---------------------------------------------------------------------------
# hide_declined_events config option
# ---------------------------------------------------------------------------


class TestHideDeclinedEvents(unittest.TestCase):
    def test_declined_events_filtered_when_enabled(self) -> None:
        config = CalendarConfig(
            enabled=True,
            feeds=("https://example.com/cal.ics",),
            refresh_minutes=5,
            lookahead_hours=72,
            attendee_emails=("user@example.com",),
            default_notifications=(),
            hide_declined_events=True,
        )
        svc = CalendarSyncService(config=config, trigger_callback=_noop_trigger)
        feed_state = svc._feed_states[config.feeds[0]]
        event_start = (datetime.now(UTC) + timedelta(hours=6)).strftime("%Y%m%dT%H%M%SZ")
        ics = f"""BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:declined-hide
DTSTART:{event_start}
SUMMARY:Declined meeting
ATTENDEE;PARTSTAT=DECLINED:mailto:user@example.com
END:VEVENT
END:VCALENDAR
"""
        cal = Calendar.from_ical(ics.encode())
        now = datetime.now(UTC).astimezone()
        reminders = svc._collect_reminders(cal, feed_state, now)
        assert len(reminders) == 0


# ---------------------------------------------------------------------------
# default_notifications config option
# ---------------------------------------------------------------------------


class TestDefaultNotifications(unittest.TestCase):
    def test_default_notifications_added(self) -> None:
        config = CalendarConfig(
            enabled=True,
            feeds=("https://example.com/cal.ics",),
            refresh_minutes=5,
            lookahead_hours=72,
            attendee_emails=(),
            default_notifications=(15, 5),
            hide_declined_events=False,
        )
        svc = CalendarSyncService(config=config, trigger_callback=_noop_trigger)
        feed_state = svc._feed_states[config.feeds[0]]
        event_start = datetime.now(UTC) + timedelta(hours=6)
        dtstart = event_start.strftime("%Y%m%dT%H%M%SZ")
        # Event with no VALARM - should get default notifications
        ics = f"""BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:default-notif
DTSTART:{dtstart}
SUMMARY:Meeting with defaults
END:VEVENT
END:VCALENDAR
"""
        cal = Calendar.from_ical(ics.encode())
        now = datetime.now(UTC).astimezone()
        reminders = svc._collect_reminders(cal, feed_state, now)
        # Should have 2 reminders (15 min and 5 min before)
        assert len(reminders) == 2
        triggers_sorted = sorted(r.trigger_time for r in reminders)
        # First trigger ~15 min before, second ~5 min before
        diff_first = (event_start.astimezone() - triggers_sorted[0]).total_seconds()
        diff_second = (event_start.astimezone() - triggers_sorted[1]).total_seconds()
        assert abs(diff_first - 900) < 5  # 15 min = 900s
        assert abs(diff_second - 300) < 5  # 5 min = 300s


# ---------------------------------------------------------------------------
# Async tests for lifecycle and sync
# ---------------------------------------------------------------------------


def _make_config(**overrides) -> CalendarConfig:  # type: ignore[no-untyped-def]
    defaults: dict = dict(
        enabled=True,
        feeds=("https://example.com/cal.ics",),
        refresh_minutes=5,
        lookahead_hours=72,
        attendee_emails=(),
        default_notifications=(),
        hide_declined_events=False,
    )
    defaults.update(overrides)
    return CalendarConfig(**defaults)  # type: ignore[arg-type]


@pytest.mark.anyio
async def test_start_no_feeds_does_nothing() -> None:
    config = _make_config(feeds=())
    svc = CalendarSyncService(config=config, trigger_callback=_noop_trigger)
    await svc.start()
    assert svc._runner is None
    assert svc._client is None


@pytest.mark.anyio
async def test_start_creates_runner_and_client() -> None:
    config = _make_config()
    svc = CalendarSyncService(config=config, trigger_callback=_noop_trigger)
    with patch.object(svc, "_run_loop", new_callable=AsyncMock):
        await svc.start()
        assert svc._runner is not None
        assert svc._client is not None
        # Double start is a no-op
        runner = svc._runner
        await svc.start()
        assert svc._runner is runner
        await svc.stop()


@pytest.mark.anyio
async def test_stop_clears_state() -> None:
    config = _make_config()
    svc = CalendarSyncService(config=config, trigger_callback=_noop_trigger)
    with patch.object(svc, "_run_loop", new_callable=AsyncMock):
        await svc.start()
        # Add some fake scheduled tasks
        fake_task = MagicMock()
        fake_task.cancel = MagicMock()
        svc._scheduled["key1"] = fake_task
        svc._retry_tasks["url1"] = fake_task
        svc._failed_feeds.add("url1")
        await svc.stop()
        assert svc._runner is None
        assert svc._client is None
        assert len(svc._scheduled) == 0
        assert len(svc._retry_tasks) == 0
        assert len(svc._failed_feeds) == 0


# ---------------------------------------------------------------------------
# _sync_feed tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_sync_feed_304_not_modified() -> None:
    config = _make_config()
    svc = CalendarSyncService(config=config, trigger_callback=_noop_trigger)
    state = svc._feed_states[config.feeds[0]]
    state.etag = '"abc"'

    mock_response = MagicMock()
    mock_response.status_code = 304
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    svc._client = mock_client

    now = datetime.now(UTC).astimezone()
    await svc._sync_feed(state, now)
    # Should not raise; etag should be preserved
    assert state.etag == '"abc"'


@pytest.mark.anyio
async def test_sync_feed_http_error_schedules_retry() -> None:
    config = _make_config()
    svc = CalendarSyncService(config=config, trigger_callback=_noop_trigger)
    state = svc._feed_states[config.feeds[0]]
    svc._stop_event = asyncio.Event()

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=httpx.HTTPError("Connection failed"))
    svc._client = mock_client

    now = datetime.now(UTC).astimezone()
    await svc._sync_feed(state, now)
    assert config.feeds[0] in svc._failed_feeds
    # Clean up retry tasks
    for task in svc._retry_tasks.values():
        task.cancel()


@pytest.mark.anyio
async def test_sync_feed_read_timeout_schedules_retry() -> None:
    config = _make_config()
    svc = CalendarSyncService(config=config, trigger_callback=_noop_trigger)
    state = svc._feed_states[config.feeds[0]]
    svc._stop_event = asyncio.Event()

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=httpx.ReadTimeout("Timed out"))
    svc._client = mock_client

    now = datetime.now(UTC).astimezone()
    await svc._sync_feed(state, now)
    assert config.feeds[0] in svc._failed_feeds
    for task in svc._retry_tasks.values():
        task.cancel()


@pytest.mark.anyio
async def test_sync_feed_400_schedules_retry() -> None:
    config = _make_config()
    svc = CalendarSyncService(config=config, trigger_callback=_noop_trigger)
    state = svc._feed_states[config.feeds[0]]
    svc._stop_event = asyncio.Event()

    mock_response = MagicMock()
    mock_response.status_code = 500
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    svc._client = mock_client

    now = datetime.now(UTC).astimezone()
    await svc._sync_feed(state, now)
    assert config.feeds[0] in svc._failed_feeds
    for task in svc._retry_tasks.values():
        task.cancel()


@pytest.mark.anyio
async def test_sync_feed_parse_failure_schedules_retry() -> None:
    config = _make_config()
    svc = CalendarSyncService(config=config, trigger_callback=_noop_trigger)
    state = svc._feed_states[config.feeds[0]]
    svc._stop_event = asyncio.Event()

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.content = b"NOT VALID ICS DATA"
    mock_response.headers = {}
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    svc._client = mock_client

    now = datetime.now(UTC).astimezone()
    with patch("pulse.assistant.calendar_sync.Calendar.from_ical", side_effect=ValueError("bad ical")):
        await svc._sync_feed(state, now)
    assert config.feeds[0] in svc._failed_feeds
    for task in svc._retry_tasks.values():
        task.cancel()


@pytest.mark.anyio
async def test_sync_feed_success_updates_etag_and_calendar_name() -> None:
    config = _make_config()
    svc = CalendarSyncService(config=config, trigger_callback=_noop_trigger)
    state = svc._feed_states[config.feeds[0]]
    svc._stop_event = asyncio.Event()

    event_start = (datetime.now(UTC) + timedelta(hours=2)).strftime("%Y%m%dT%H%M%SZ")
    ics_body = f"""BEGIN:VCALENDAR
VERSION:2.0
X-WR-CALNAME:Work Calendar
BEGIN:VEVENT
UID:sync-test
DTSTART:{event_start}
SUMMARY:Sync test
END:VEVENT
END:VCALENDAR
""".encode()

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.content = ics_body
    mock_response.headers = {"etag": '"new-etag"', "last-modified": "Thu, 01 Jan 2025 00:00:00 GMT"}
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    svc._client = mock_client

    now = datetime.now(UTC).astimezone()
    await svc._sync_feed(state, now)
    assert state.etag == '"new-etag"'
    assert state.last_modified == "Thu, 01 Jan 2025 00:00:00 GMT"
    assert state.calendar_name == "Work Calendar"
    # Clean up
    for task in svc._scheduled.values():
        task.cancel()


@pytest.mark.anyio
async def test_sync_feed_no_client_returns_early() -> None:
    config = _make_config()
    svc = CalendarSyncService(config=config, trigger_callback=_noop_trigger)
    state = svc._feed_states[config.feeds[0]]
    svc._client = None
    now = datetime.now(UTC).astimezone()
    # Should not raise
    await svc._sync_feed(state, now)


# ---------------------------------------------------------------------------
# _emit_event_snapshot
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_emit_event_snapshot_calls_callback() -> None:
    snapshot_cb = AsyncMock()
    config = _make_config()
    svc = CalendarSyncService(config=config, trigger_callback=_noop_trigger, snapshot_callback=snapshot_cb)
    now = datetime.now(UTC)
    reminder = CalendarReminder(
        uid="snap-1",
        summary="Snap test",
        description=None,
        location=None,
        start=now + timedelta(hours=1),
        end=None,
        all_day=False,
        trigger_time=now,
        calendar_name=None,
        source_url="https://example.com/cal.ics",
    )
    svc._windowed_events["key1"] = reminder
    await svc._emit_event_snapshot()
    snapshot_cb.assert_awaited_once()
    args = snapshot_cb.call_args[0][0]
    assert len(args) == 1
    assert args[0].uid == "snap-1"


@pytest.mark.anyio
async def test_emit_event_snapshot_no_callback() -> None:
    config = _make_config()
    svc = CalendarSyncService(config=config, trigger_callback=_noop_trigger, snapshot_callback=None)
    svc._windowed_events.clear()
    # Should not raise
    await svc._emit_event_snapshot()
    assert svc._latest_events == []


@pytest.mark.anyio
async def test_emit_event_snapshot_callback_exception_logged() -> None:
    snapshot_cb = AsyncMock(side_effect=RuntimeError("boom"))
    config = _make_config()
    svc = CalendarSyncService(config=config, trigger_callback=_noop_trigger, snapshot_callback=snapshot_cb)
    now = datetime.now(UTC)
    reminder = CalendarReminder(
        uid="snap-err",
        summary="Error snap",
        description=None,
        location=None,
        start=now + timedelta(hours=1),
        end=None,
        all_day=False,
        trigger_time=now,
        calendar_name=None,
        source_url="https://example.com/cal.ics",
    )
    svc._windowed_events["k"] = reminder
    # Should not raise (exception is logged)
    await svc._emit_event_snapshot()


# ---------------------------------------------------------------------------
# _await_and_fire
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_await_and_fire_immediate_trigger() -> None:
    trigger_cb = AsyncMock()
    config = _make_config()
    svc = CalendarSyncService(config=config, trigger_callback=trigger_cb)
    state = svc._feed_states[config.feeds[0]]
    now = datetime.now(UTC).astimezone()

    reminder = CalendarReminder(
        uid="fire-1",
        summary="Fire now",
        description=None,
        location=None,
        start=now + timedelta(hours=1),
        end=None,
        all_day=False,
        trigger_time=now - timedelta(seconds=1),
        calendar_name=None,
        source_url=config.feeds[0],
    )
    key = svc._reminder_key(reminder)
    svc._scheduled[key] = MagicMock()
    svc._scheduled_reminders[key] = reminder
    svc._key_to_feed[key] = config.feeds[0]
    state.active_keys.add(key)

    await svc._await_and_fire(key, reminder)
    trigger_cb.assert_awaited_once_with(reminder)
    assert key in svc._triggered
    assert key not in svc._scheduled


@pytest.mark.anyio
async def test_await_and_fire_declined_not_triggered() -> None:
    trigger_cb = AsyncMock()
    config = _make_config()
    svc = CalendarSyncService(config=config, trigger_callback=trigger_cb)
    state = svc._feed_states[config.feeds[0]]
    now = datetime.now(UTC).astimezone()

    reminder = CalendarReminder(
        uid="declined-fire",
        summary="Declined event",
        description=None,
        location=None,
        start=now + timedelta(hours=1),
        end=None,
        all_day=False,
        trigger_time=now - timedelta(seconds=1),
        calendar_name=None,
        source_url=config.feeds[0],
        declined=True,
    )
    key = svc._reminder_key(reminder)
    svc._scheduled[key] = MagicMock()
    svc._scheduled_reminders[key] = reminder
    svc._key_to_feed[key] = config.feeds[0]
    state.active_keys.add(key)

    await svc._await_and_fire(key, reminder)
    trigger_cb.assert_not_awaited()
    assert key in svc._triggered


@pytest.mark.anyio
async def test_await_and_fire_stop_event_cancels() -> None:
    trigger_cb = AsyncMock()
    config = _make_config()
    svc = CalendarSyncService(config=config, trigger_callback=trigger_cb)
    now = datetime.now(UTC).astimezone()

    reminder = CalendarReminder(
        uid="stop-fire",
        summary="Stopped",
        description=None,
        location=None,
        start=now + timedelta(hours=1),
        end=None,
        all_day=False,
        trigger_time=now + timedelta(hours=10),
        calendar_name=None,
        source_url=config.feeds[0],
    )
    key = svc._reminder_key(reminder)

    # Set the stop event so the wait returns immediately
    svc._stop_event.set()
    await svc._await_and_fire(key, reminder)
    trigger_cb.assert_not_awaited()


@pytest.mark.anyio
async def test_await_and_fire_callback_exception_logged() -> None:
    trigger_cb = AsyncMock(side_effect=RuntimeError("callback exploded"))
    config = _make_config()
    svc = CalendarSyncService(config=config, trigger_callback=trigger_cb)
    state = svc._feed_states[config.feeds[0]]
    now = datetime.now(UTC).astimezone()

    reminder = CalendarReminder(
        uid="err-fire",
        summary="Error event",
        description=None,
        location=None,
        start=now + timedelta(hours=1),
        end=None,
        all_day=False,
        trigger_time=now - timedelta(seconds=1),
        calendar_name=None,
        source_url=config.feeds[0],
    )
    key = svc._reminder_key(reminder)
    svc._scheduled[key] = MagicMock()
    svc._scheduled_reminders[key] = reminder
    svc._key_to_feed[key] = config.feeds[0]
    state.active_keys.add(key)

    # Should not raise
    await svc._await_and_fire(key, reminder)
    assert key in svc._triggered


# ---------------------------------------------------------------------------
# _schedule_retry / _cancel_retry
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_schedule_retry_creates_task() -> None:
    config = _make_config()
    svc = CalendarSyncService(config=config, trigger_callback=_noop_trigger)
    svc._stop_event = asyncio.Event()
    svc._schedule_retry(config.feeds[0])
    assert config.feeds[0] in svc._retry_tasks
    assert config.feeds[0] in svc._failed_feeds
    # Clean up
    for task in svc._retry_tasks.values():
        task.cancel()


@pytest.mark.anyio
async def test_schedule_retry_noop_if_already_scheduled() -> None:
    config = _make_config()
    svc = CalendarSyncService(config=config, trigger_callback=_noop_trigger)
    svc._stop_event = asyncio.Event()
    svc._schedule_retry(config.feeds[0])
    first_task = svc._retry_tasks[config.feeds[0]]
    svc._schedule_retry(config.feeds[0])
    assert svc._retry_tasks[config.feeds[0]] is first_task
    first_task.cancel()


@pytest.mark.anyio
async def test_schedule_retry_unknown_feed_noop() -> None:
    config = _make_config()
    svc = CalendarSyncService(config=config, trigger_callback=_noop_trigger)
    svc._schedule_retry("https://unknown.example.com/feed.ics")
    assert "https://unknown.example.com/feed.ics" not in svc._retry_tasks


@pytest.mark.anyio
async def test_cancel_retry_removes_task() -> None:
    config = _make_config()
    svc = CalendarSyncService(config=config, trigger_callback=_noop_trigger)
    svc._stop_event = asyncio.Event()
    svc._schedule_retry(config.feeds[0])
    assert config.feeds[0] in svc._retry_tasks
    svc._cancel_retry(config.feeds[0])
    assert config.feeds[0] not in svc._retry_tasks
    assert config.feeds[0] not in svc._failed_feeds


@pytest.mark.anyio
async def test_cancel_retry_noop_if_not_scheduled() -> None:
    config = _make_config()
    svc = CalendarSyncService(config=config, trigger_callback=_noop_trigger)
    # Should not raise
    svc._cancel_retry(config.feeds[0])


# ---------------------------------------------------------------------------
# _schedule_reminders edge cases
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_schedule_reminders_skips_past_events() -> None:
    config = _make_config()
    svc = CalendarSyncService(config=config, trigger_callback=_noop_trigger)
    state = svc._feed_states[config.feeds[0]]
    now = datetime.now(UTC).astimezone()

    # Event already ended
    past_reminder = CalendarReminder(
        uid="past-1",
        summary="Past",
        description=None,
        location=None,
        start=now - timedelta(hours=2),
        end=now - timedelta(hours=1),
        all_day=False,
        trigger_time=now - timedelta(hours=3),
        calendar_name=None,
        source_url=config.feeds[0],
    )
    await svc._schedule_reminders(state, [past_reminder], now)
    assert len(svc._scheduled) == 0


@pytest.mark.anyio
async def test_schedule_reminders_stale_keys_cancelled() -> None:
    config = _make_config()
    svc = CalendarSyncService(config=config, trigger_callback=_noop_trigger)
    state = svc._feed_states[config.feeds[0]]
    now = datetime.now(UTC).astimezone()

    # Simulate an old reminder that's no longer in the feed
    stale_key = "stale-key"
    mock_task = MagicMock()
    svc._scheduled[stale_key] = mock_task
    svc._scheduled_reminders[stale_key] = MagicMock()
    svc._key_to_feed[stale_key] = config.feeds[0]
    state.active_keys.add(stale_key)

    # Schedule with empty reminders -> stale key should be cancelled
    await svc._schedule_reminders(state, [], now)
    mock_task.cancel.assert_called_once()
    assert stale_key not in svc._scheduled


# ---------------------------------------------------------------------------
# _sync_once
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_sync_once_sets_timestamps() -> None:
    config = _make_config()
    svc = CalendarSyncService(config=config, trigger_callback=_noop_trigger)
    svc._client = AsyncMock()

    with patch.object(svc, "_sync_feed", new_callable=AsyncMock):
        await svc._sync_once()
        assert svc._last_sync_started is not None
        assert svc._last_sync_completed is not None


@pytest.mark.anyio
async def test_sync_once_handles_feed_timeout() -> None:
    config = _make_config()
    svc = CalendarSyncService(config=config, trigger_callback=_noop_trigger)
    svc._client = AsyncMock()

    with patch.object(svc, "_sync_feed", new_callable=AsyncMock, side_effect=TimeoutError):
        # Should not raise - timeout is caught
        await svc._sync_once()
        # Snapshot still attempted even if feed times out
        assert svc._last_sync_completed is not None


@pytest.mark.anyio
async def test_sync_once_handles_feed_exception() -> None:
    config = _make_config()
    svc = CalendarSyncService(config=config, trigger_callback=_noop_trigger)
    svc._client = AsyncMock()

    with patch.object(svc, "_sync_feed", new_callable=AsyncMock, side_effect=RuntimeError("boom")):
        await svc._sync_once()
        # Snapshot should still succeed
        assert svc._last_sync_completed is not None


# ---------------------------------------------------------------------------
# _process_vevent edge cases
# ---------------------------------------------------------------------------


class TestProcessVeventEdgeCases(unittest.TestCase):
    def setUp(self) -> None:
        self.config = CalendarConfig(
            enabled=True,
            feeds=("https://example.com/cal.ics",),
            refresh_minutes=5,
            lookahead_hours=72,
            attendee_emails=(),
            default_notifications=(),
            hide_declined_events=False,
        )
        self.service = CalendarSyncService(config=self.config, trigger_callback=_noop_trigger)
        self.feed_state = self.service._feed_states[self.config.feeds[0]]
        self.now = datetime.now(UTC).astimezone()

    def test_event_no_uid_skipped(self) -> None:
        dtstart = (self.now + timedelta(hours=2)).strftime("%Y%m%dT%H%M%SZ")
        ics = f"""BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
DTSTART:{dtstart}
SUMMARY:No UID event
END:VEVENT
END:VCALENDAR
"""
        cal = Calendar.from_ical(ics.encode())
        for comp in cal.walk("VEVENT"):
            result = self.service._process_vevent(comp, self.feed_state, self.now)
            assert result == []

    def test_event_already_ended_skipped(self) -> None:
        start = (self.now - timedelta(hours=3)).strftime("%Y%m%dT%H%M%SZ")
        end = (self.now - timedelta(hours=1)).strftime("%Y%m%dT%H%M%SZ")
        ics = f"""BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:ended-event
DTSTART:{start}
DTEND:{end}
SUMMARY:Already over
END:VEVENT
END:VCALENDAR
"""
        cal = Calendar.from_ical(ics.encode())
        for comp in cal.walk("VEVENT"):
            result = self.service._process_vevent(comp, self.feed_state, self.now)
            assert result == []

    def test_event_with_description_location_url(self) -> None:
        dtstart = (self.now + timedelta(hours=2)).strftime("%Y%m%dT%H%M%SZ")
        ics = f"""BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:rich-event
DTSTART:{dtstart}
SUMMARY:Rich event
DESCRIPTION:Important meeting about things
LOCATION:Conference Room B
URL:https://meet.example.com/abc
SEQUENCE:3
END:VEVENT
END:VCALENDAR
"""
        cal = Calendar.from_ical(ics.encode())
        for comp in cal.walk("VEVENT"):
            result = self.service._process_vevent(comp, self.feed_state, self.now)
            assert len(result) >= 1
            r = result[0]
            assert r.description == "Important meeting about things"
            assert r.location == "Conference Room B"
            assert r.url == "https://meet.example.com/abc"
            assert r.sequence == 3


# ---------------------------------------------------------------------------
# _window_key and _reminder_key
# ---------------------------------------------------------------------------


class TestKeyMethods(unittest.TestCase):
    def test_window_key_format(self) -> None:
        config = _make_config()
        svc = CalendarSyncService(config=config, trigger_callback=_noop_trigger)
        now = datetime(2025, 6, 1, 10, 0, tzinfo=UTC)
        r = CalendarReminder(
            uid="k1",
            summary="T",
            description=None,
            location=None,
            start=now,
            end=None,
            all_day=False,
            trigger_time=now - timedelta(minutes=5),
            calendar_name=None,
            source_url="http://x",
        )
        wk = svc._window_key(r)
        assert "k1" in wk
        assert "http://x" in wk
        assert now.isoformat() in wk

    def test_reminder_key_uses_trigger_time(self) -> None:
        config = _make_config()
        svc = CalendarSyncService(config=config, trigger_callback=_noop_trigger)
        now = datetime(2025, 6, 1, 10, 0, tzinfo=UTC)
        trigger = now - timedelta(minutes=5)
        r = CalendarReminder(
            uid="k2",
            summary="T",
            description=None,
            location=None,
            start=now,
            end=None,
            all_day=False,
            trigger_time=trigger,
            calendar_name=None,
            source_url="http://x",
        )
        rk = svc._reminder_key(r)
        assert trigger.isoformat() in rk
