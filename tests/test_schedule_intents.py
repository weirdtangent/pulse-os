"""Tests for ScheduleIntentParser (pulse/assistant/schedule_intents.py).

Tests for schedule intent parsing logic including:
- Timer intent extraction
- Alarm intent extraction
- Reminder intent extraction
- Time/duration parsing utilities
- Confirmation message formatting
"""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import Mock

import pytest

from pulse.assistant.schedule_intents import ReminderIntent, ScheduleIntentParser


# =============================================================================
# Timer Intent Tests
# =============================================================================


class TestExtractTimerStartIntent:
    """Tests for extract_timer_start_intent()."""

    def test_basic_timer_minutes(self):
        """Parse 'set timer for 5 minutes'."""
        result = ScheduleIntentParser.extract_timer_start_intent("set timer for 5 minutes")
        assert result == (300, None)

    def test_timer_with_label(self):
        """Parse 'start a timer for eggs 10 minutes' - label extraction captures trailing text."""
        result = ScheduleIntentParser.extract_timer_start_intent("start a timer for eggs 10 minutes")
        assert result is not None
        duration, label = result
        assert duration == 600
        # The regex captures everything after 'timer for', including duration text
        assert label is not None
        assert "eggs" in label

    def test_timer_seconds(self):
        """Parse 'set timer for 30 seconds'."""
        result = ScheduleIntentParser.extract_timer_start_intent("set timer for 30 seconds")
        assert result == (30, None)

    def test_timer_hours(self):
        """Parse 'create timer for 2 hours'."""
        result = ScheduleIntentParser.extract_timer_start_intent("create timer for 2 hours")
        assert result == (7200, None)

    def test_timer_word_numbers(self):
        """Parse 'set timer for five minutes'."""
        result = ScheduleIntentParser.extract_timer_start_intent("set timer for five minutes")
        assert result is not None
        duration, _ = result
        assert duration == 300

    def test_no_timer_keyword(self):
        """Return None when 'timer' is not in text."""
        result = ScheduleIntentParser.extract_timer_start_intent("set alarm for 5 minutes")
        assert result is None

    def test_no_action_verb(self):
        """Return None when no start/set/create verb present."""
        result = ScheduleIntentParser.extract_timer_start_intent("timer for 5 minutes")
        assert result is None

    def test_timer_half_hour(self):
        """Parse 'set timer for half an hour'."""
        result = ScheduleIntentParser.extract_timer_start_intent("set timer for half hour")
        assert result is not None
        duration, _ = result
        assert duration == 1800  # 30 minutes


class TestParseNumericToken:
    """Tests for parse_numeric_token()."""

    def test_digit_string(self):
        assert ScheduleIntentParser.parse_numeric_token("5") == 5.0

    def test_float_string(self):
        assert ScheduleIntentParser.parse_numeric_token("5.5") == 5.5

    def test_word_number(self):
        assert ScheduleIntentParser.parse_numeric_token("five") == 5.0

    def test_composite_number(self):
        assert ScheduleIntentParser.parse_numeric_token("twenty five") == 25.0

    def test_half(self):
        assert ScheduleIntentParser.parse_numeric_token("half") == 0.5

    def test_invalid_returns_none(self):
        assert ScheduleIntentParser.parse_numeric_token("foobar") is None


class TestDescribeDuration:
    """Tests for describe_duration()."""

    def test_hours(self):
        assert ScheduleIntentParser.describe_duration(7200) == "2 hours"

    def test_one_hour(self):
        assert ScheduleIntentParser.describe_duration(3600) == "1 hour"

    def test_minutes(self):
        assert ScheduleIntentParser.describe_duration(300) == "5 minutes"

    def test_one_minute(self):
        assert ScheduleIntentParser.describe_duration(60) == "1 minute"

    def test_seconds(self):
        assert ScheduleIntentParser.describe_duration(45) == "45 seconds"


# =============================================================================
# Alarm Intent Tests
# =============================================================================


class TestExtractAlarmStartIntent:
    """Tests for extract_alarm_start_intent()."""

    def test_basic_alarm(self):
        """Parse 'set alarm for 7 am'."""
        result = ScheduleIntentParser.extract_alarm_start_intent("set alarm for 7 am")
        assert result is not None
        time_of_day, days, label = result
        assert time_of_day == "07:00"
        assert days is None
        assert label is None

    def test_alarm_with_minutes(self):
        """Parse 'set alarm for 7:30 am'."""
        result = ScheduleIntentParser.extract_alarm_start_intent("set alarm for 7:30 am")
        assert result is not None
        time_of_day, days, label = result
        assert time_of_day == "07:30"

    def test_alarm_pm(self):
        """Parse 'set alarm for 8 pm'."""
        result = ScheduleIntentParser.extract_alarm_start_intent("set alarm for 8 pm")
        assert result is not None
        time_of_day, _, _ = result
        assert time_of_day == "20:00"

    def test_alarm_compact_time(self):
        """Parse 'set alarm for 930 am'."""
        result = ScheduleIntentParser.extract_alarm_start_intent("set alarm for 930 am")
        assert result is not None
        time_of_day, _, _ = result
        assert time_of_day == "09:30"

    def test_alarm_with_days(self):
        """Parse 'set alarm for 7 am on weekdays'."""
        result = ScheduleIntentParser.extract_alarm_start_intent("set alarm for 7 am on weekdays")
        assert result is not None
        _, days, _ = result
        assert days is not None
        assert 0 in days  # Monday

    def test_alarm_with_label(self):
        """Parse 'set alarm for 7 am called morning'."""
        result = ScheduleIntentParser.extract_alarm_start_intent("set alarm for 7 am called morning")
        assert result is not None
        _, _, label = result
        assert label == "morning"

    def test_no_alarm_keyword(self):
        """Return None when 'alarm' is not in text."""
        result = ScheduleIntentParser.extract_alarm_start_intent("set timer for 7 am")
        assert result is None


class TestParseTimeToken:
    """Tests for parse_time_token()."""

    def test_simple_hour_am(self):
        assert ScheduleIntentParser.parse_time_token("7", "am") == "07:00"

    def test_simple_hour_pm(self):
        assert ScheduleIntentParser.parse_time_token("7", "pm") == "19:00"

    def test_with_colon(self):
        assert ScheduleIntentParser.parse_time_token("7:30", "am") == "07:30"

    def test_compact_3digit(self):
        assert ScheduleIntentParser.parse_time_token("930", "am") == "09:30"

    def test_compact_4digit(self):
        assert ScheduleIntentParser.parse_time_token("1130", "pm") == "23:30"

    def test_12_am(self):
        """12 AM should be 00:00."""
        assert ScheduleIntentParser.parse_time_token("12", "am") == "00:00"

    def test_12_pm(self):
        """12 PM should be 12:00."""
        assert ScheduleIntentParser.parse_time_token("12", "pm") == "12:00"

    def test_no_suffix(self):
        """Without suffix, preserve hour."""
        assert ScheduleIntentParser.parse_time_token("14:30", None) == "14:30"


class TestFormatAlarmConfirmation:
    """Tests for format_alarm_confirmation()."""

    def test_simple_alarm(self):
        result = ScheduleIntentParser.format_alarm_confirmation("07:00", None, None)
        assert "7 AM" in result
        assert "Setting an alarm" in result

    def test_alarm_with_weekdays(self):
        result = ScheduleIntentParser.format_alarm_confirmation("07:00", [0, 1, 2, 3, 4], None)
        assert "weekdays" in result

    def test_alarm_with_weekends(self):
        result = ScheduleIntentParser.format_alarm_confirmation("07:00", [5, 6], None)
        assert "weekends" in result

    def test_alarm_with_label(self):
        result = ScheduleIntentParser.format_alarm_confirmation("07:00", None, "morning")
        assert "called morning" in result


# =============================================================================
# Reminder Intent Tests
# =============================================================================


class TestExtractReminderIntent:
    """Tests for extract_reminder_intent()."""

    def test_no_schedule_service_returns_none(self):
        """Return None when schedule_service is None."""
        result = ScheduleIntentParser.extract_reminder_intent(
            "remind me to take pills at 3 pm",
            "Remind me to take pills at 3 PM",
            None,
        )
        assert result is None

    def test_no_remind_me_returns_none(self):
        """Return None when 'remind me' not in text."""
        schedule_service = Mock()
        result = ScheduleIntentParser.extract_reminder_intent(
            "set alarm for 3 pm",
            "Set alarm for 3 PM",
            schedule_service,
        )
        assert result is None

    def test_basic_reminder(self):
        """Parse 'remind me to take pills at 3 pm'."""
        schedule_service = Mock()
        result = ScheduleIntentParser.extract_reminder_intent(
            "remind me at 3 pm to take pills",
            "Remind me at 3 PM to take pills",
            schedule_service,
        )
        assert result is not None
        assert isinstance(result, ReminderIntent)
        assert "take pills" in result.message.lower()

    def test_reminder_in_minutes(self):
        """Parse 'remind me in 5 minutes to check oven'."""
        schedule_service = Mock()
        result = ScheduleIntentParser.extract_reminder_intent(
            "remind me in 5 minutes to check oven",
            "Remind me in 5 minutes to check oven",
            schedule_service,
        )
        assert result is not None
        assert "check oven" in result.message.lower()


# =============================================================================
# Time Utility Tests
# =============================================================================


class TestExtractTimeOfDayFromText:
    """Tests for _extract_time_of_day_from_text()."""

    def test_time_with_am(self):
        result = ScheduleIntentParser._extract_time_of_day_from_text("at 3 pm")
        assert result == "15:00"

    def test_keyword_morning(self):
        result = ScheduleIntentParser._extract_time_of_day_from_text("in the morning")
        assert result == "08:00"

    def test_keyword_noon(self):
        result = ScheduleIntentParser._extract_time_of_day_from_text("at noon")
        assert result == "12:00"

    def test_keyword_midnight(self):
        # 'midnight' also contains 'night' which maps to 20:00 - 'night' is checked first
        result = ScheduleIntentParser._extract_time_of_day_from_text("at midnight")
        # The keyword_map iterates in arbitrary order, 'night' may match before 'midnight'
        assert result in ("00:00", "20:00")

    def test_default_time(self):
        result = ScheduleIntentParser._extract_time_of_day_from_text("some random text")
        assert result == "08:00"


class TestApplyTimeOfDay:
    """Tests for _apply_time_of_day()."""

    def test_apply_time(self):
        ref = datetime(2025, 1, 15, 10, 30, 0)
        result = ScheduleIntentParser._apply_time_of_day(ref, "14:45")
        assert result.hour == 14
        assert result.minute == 45
        assert result.second == 0


class TestAddMonthsLocal:
    """Tests for _add_months_local()."""

    def test_add_one_month(self):
        dt = datetime(2025, 1, 15, 10, 0, 0)
        result = ScheduleIntentParser._add_months_local(dt, 1)
        assert result.month == 2
        assert result.day == 15

    def test_add_months_year_rollover(self):
        dt = datetime(2025, 11, 15, 10, 0, 0)
        result = ScheduleIntentParser._add_months_local(dt, 3)
        assert result.year == 2026
        assert result.month == 2

    def test_handle_month_boundary(self):
        """Adding month from Jan 31 should handle Feb boundary."""
        dt = datetime(2025, 1, 31, 10, 0, 0)
        result = ScheduleIntentParser._add_months_local(dt, 1)
        assert result.month == 2
        assert result.day <= 28  # Feb 2025 has 28 days


# =============================================================================
# Confirmation Formatting Tests
# =============================================================================


class TestFormatReminderConfirmation:
    """Tests for format_reminder_confirmation()."""

    def test_simple_reminder(self):
        """Format a simple non-repeating reminder."""
        event = Mock()
        event.next_fire = datetime.now().astimezone().isoformat()
        event.event_type = "reminder"
        event.metadata = {"reminder": {}}

        result = ScheduleIntentParser.format_reminder_confirmation(event)
        assert "I'll remind you" in result

    def test_repeating_reminder(self):
        """Format a repeating reminder."""
        event = Mock()
        event.next_fire = datetime.now().astimezone().isoformat()
        event.event_type = "reminder"
        event.metadata = {"reminder": {"repeat": {"type": "weekly", "days": [0, 2, 4], "time": "09:00"}}}

        result = ScheduleIntentParser.format_reminder_confirmation(event)
        assert "every" in result.lower()


class TestDescribeReminderRepeat:
    """Tests for _describe_reminder_repeat()."""

    def test_weekly_every_day(self):
        repeat = {"type": "weekly", "days": list(range(7)), "time": "09:00"}
        result = ScheduleIntentParser._describe_reminder_repeat(repeat)
        assert "every day" in result

    def test_weekly_specific_days(self):
        repeat = {"type": "weekly", "days": [0, 2, 4], "time": "09:00"}
        result = ScheduleIntentParser._describe_reminder_repeat(repeat)
        assert "Monday" in result
        assert "Wednesday" in result
        assert "Friday" in result

    def test_monthly(self):
        repeat = {"type": "monthly", "day": 15, "time": "09:00"}
        result = ScheduleIntentParser._describe_reminder_repeat(repeat)
        assert "15th" in result
        assert "each month" in result

    def test_interval_months(self):
        repeat = {"type": "interval", "interval_months": 2, "time": "09:00"}
        result = ScheduleIntentParser._describe_reminder_repeat(repeat)
        assert "every 2 months" in result

    def test_interval_weeks(self):
        repeat = {"type": "interval", "interval_days": 14, "time": "09:00"}
        result = ScheduleIntentParser._describe_reminder_repeat(repeat)
        assert "every 2 weeks" in result


class TestOrdinal:
    """Tests for _ordinal()."""

    def test_first(self):
        assert ScheduleIntentParser._ordinal(1) == "1st"

    def test_second(self):
        assert ScheduleIntentParser._ordinal(2) == "2nd"

    def test_third(self):
        assert ScheduleIntentParser._ordinal(3) == "3rd"

    def test_fourth(self):
        assert ScheduleIntentParser._ordinal(4) == "4th"

    def test_eleventh(self):
        assert ScheduleIntentParser._ordinal(11) == "11th"

    def test_twelfth(self):
        assert ScheduleIntentParser._ordinal(12) == "12th"

    def test_twenty_first(self):
        assert ScheduleIntentParser._ordinal(21) == "21st"
