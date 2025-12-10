from __future__ import annotations

import asyncio
import unittest
from datetime import UTC, datetime, timedelta

from icalendar import Calendar
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
        ics = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Pulse Test//EN
BEGIN:VEVENT
UID:event-123
DTSTART:20250105T150000Z
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
        expected_trigger = datetime(2025, 1, 5, 14, 30, tzinfo=UTC)
        self.assertEqual(reminders[0].trigger_time.astimezone(UTC), expected_trigger)

    def test_all_day_event_fires_day_before_at_noon(self) -> None:
        ics = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Pulse Test//EN
BEGIN:VEVENT
UID:event-456
DTSTART;VALUE=DATE:20250110
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
        ics = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Pulse Test//EN
BEGIN:VEVENT
UID:event-789
DTSTART:20250115T180000Z
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
        ics = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Pulse Test//EN
BEGIN:VEVENT
UID:event-456
DTSTART:20250120T120000Z
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
        expected_first = datetime(2025, 1, 20, 11, 40, tzinfo=UTC)
        expected_second = datetime(2025, 1, 20, 11, 55, tzinfo=UTC)
        self.assertEqual(triggers[0].astimezone(UTC), expected_first)
        self.assertEqual(triggers[1].astimezone(UTC), expected_second)

    def test_declined_status_is_detected_for_owner(self) -> None:
        ics = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Pulse Test//EN
BEGIN:VEVENT
UID:event-901
DTSTART:20250201T150000Z
SUMMARY:Skip me
ATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=DECLINED;CN=User;X-NUM-GUESTS=0:mailto:user@example.com
END:VEVENT
END:VCALENDAR
"""
        reminders = self._collect(ics)
        self.assertEqual(len(reminders), 1)
        self.assertTrue(reminders[0].declined)

    def test_other_attendee_decline_is_ignored(self) -> None:
        ics = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Pulse Test//EN
BEGIN:VEVENT
UID:event-902
DTSTART:20250202T150000Z
SUMMARY:Still attending
ATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;CN=User;X-NUM-GUESTS=0:mailto:user@example.com
ATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=DECLINED;CN=Other;X-NUM-GUESTS=0:mailto:other@example.com
END:VEVENT
END:VCALENDAR
"""
        reminders = self._collect(ics)
        self.assertEqual(len(reminders), 1)
        self.assertFalse(reminders[0].declined)

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
