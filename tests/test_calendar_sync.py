from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from icalendar import Calendar
from pulse.assistant.calendar_sync import CalendarSyncService
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
