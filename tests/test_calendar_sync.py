from __future__ import annotations

import asyncio
import unittest
from datetime import UTC, datetime, timedelta

from icalendar import Calendar  # type: ignore[import-untyped]
from pulse.assistant.calendar_sync import CalendarReminder, CalendarSyncService
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
