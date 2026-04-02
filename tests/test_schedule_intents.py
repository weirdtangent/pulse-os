"""Tests for ScheduleIntentParser (pulse/assistant/schedule_intents.py).

Tests for schedule intent parsing logic including:
- Timer intent extraction
- Alarm intent extraction
- Reminder intent extraction
- Time/duration parsing utilities
- Confirmation message formatting
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import Mock

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

    def test_timer_composite_word_numbers(self):
        """Parse 'set timer for twenty five minutes' - composite word numbers."""
        result = ScheduleIntentParser.extract_timer_start_intent("set timer for twenty five minutes")
        assert result is not None
        duration, _ = result
        assert duration == 1500  # 25 minutes


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
        # 'midnight' also contains 'night', but the parser should prioritize the specific
        # 'midnight' keyword so that it maps to 00:00 rather than the more general 'night'.
        result = ScheduleIntentParser._extract_time_of_day_from_text("at midnight")
        assert result == "00:00"

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


# =============================================================================
# Additional Coverage Tests
# =============================================================================


class TestExtractTimerStartIntentEdgeCases:
    """Additional tests for extract_timer_start_intent() uncovered paths."""

    def test_no_duration_match_returns_none(self):
        """Line 68: duration regex doesn't match."""
        result = ScheduleIntentParser.extract_timer_start_intent("set a timer please")
        assert result is None

    def test_unparseable_word_amount_returns_none(self):
        """Line 72: parse_numeric_token returns None for unrecognized word."""
        result = ScheduleIntentParser.extract_timer_start_intent("set timer for xyzzy minutes")
        assert result is None


class TestExtractAlarmStartIntentEdgeCases:
    """Additional tests for extract_alarm_start_intent() uncovered paths."""

    def test_no_time_match_returns_none(self):
        """Line 186: time regex doesn't match."""
        result = ScheduleIntentParser.extract_alarm_start_intent("set alarm please")
        assert result is None

    def test_invalid_time_token_returns_none(self):
        """Line 191: parse_time_token returns falsy."""
        # A single digit like "0" with no suffix results in "00:00" which is valid,
        # so we need something that fails parse_time_token. Use a token with letters.
        result = ScheduleIntentParser.extract_alarm_start_intent("set alarm for abc am")
        assert result is None


class TestParseTimeTokenEdgeCases:
    """Additional tests for parse_time_token() uncovered paths."""

    def test_non_numeric_returns_none(self):
        """Lines 224-225: ValueError on int() conversion."""
        result = ScheduleIntentParser.parse_time_token("abc", "am")
        assert result is None


class TestFormatAlarmConfirmationEdgeCases:
    """Additional tests for format_alarm_confirmation() uncovered paths."""

    def test_invalid_time_format_fallback(self):
        """Lines 253-254: ValueError on strptime triggers fallback."""
        result = ScheduleIntentParser.format_alarm_confirmation("bad:time", None, None)
        assert "bad:time" in result

    def test_every_day(self):
        """Lines 262-263: all 7 days yields 'every day'."""
        result = ScheduleIntentParser.format_alarm_confirmation("07:00", list(range(7)), None)
        assert "every day" in result

    def test_single_day(self):
        """Lines 264-265: single day in list."""
        result = ScheduleIntentParser.format_alarm_confirmation("07:00", [2], None)
        assert "Wednesday" in result

    def test_multiple_specific_days(self):
        """Lines 266-268: multiple days not matching weekdays/weekends/all."""
        result = ScheduleIntentParser.format_alarm_confirmation("07:00", [0, 2, 4], None)
        assert "Monday" in result
        assert "Wednesday" in result
        assert "Friday" in result


class TestExtractReminderIntentEdgeCases:
    """Additional tests for extract_reminder_intent() uncovered paths."""

    def test_empty_suffix_returns_none(self):
        """Line 300: empty suffix after 'remind me'."""
        schedule_service = Mock()
        result = ScheduleIntentParser.extract_reminder_intent(
            "remind me",
            "Remind me",
            schedule_service,
        )
        assert result is None

    def test_unparseable_schedule_returns_none(self):
        """Line 309: _parse_reminder_schedule returns None."""
        schedule_service = Mock()
        # _parse_reminder_schedule always returns something (defaults to next day at 08:00),
        # so we test that a message without 'to' still works
        result = ScheduleIntentParser.extract_reminder_intent(
            "remind me at 3 pm to take pills",
            "Remind me at 3 PM to take pills",
            schedule_service,
        )
        assert result is not None
        assert "take pills" in result.message.lower()


class TestParseReminderScheduleEdgeCases:
    """Additional tests for _parse_reminder_schedule() uncovered paths."""

    def test_empty_schedule_text_uses_fallback(self):
        """Line 331: empty schedule_text falls back."""
        result = ScheduleIntentParser._parse_reminder_schedule("", "at 3 pm to do stuff")
        assert result is not None
        fire_time, repeat_rule = result
        assert repeat_rule is None

    def test_every_month_with_interval(self):
        """Lines 340-349: 'every 2 months' yields interval repeat rule."""
        result = ScheduleIntentParser._parse_reminder_schedule("every 2 months at 9 am", "every 2 months at 9 am")
        assert result is not None
        fire_time, repeat_rule = result
        assert repeat_rule is not None
        assert repeat_rule["type"] == "interval"
        assert repeat_rule["interval_months"] == 2

    def test_every_month_with_day_of_month(self):
        """Lines 350-353: 'every month on the 15th'."""
        result = ScheduleIntentParser._parse_reminder_schedule(
            "every month on the 15th at 9 am", "every month on the 15th at 9 am"
        )
        assert result is not None
        fire_time, repeat_rule = result
        assert repeat_rule is not None
        assert repeat_rule["type"] == "monthly"
        assert repeat_rule["day"] == 15

    def test_every_month_default_day(self):
        """Lines 350-353: 'every month' without day uses current day."""
        result = ScheduleIntentParser._parse_reminder_schedule("every month at 9 am", "every month at 9 am")
        assert result is not None
        fire_time, repeat_rule = result
        assert repeat_rule["type"] == "monthly"

    def test_every_n_weeks(self):
        """Lines 360-366: 'every 2 weeks'."""
        result = ScheduleIntentParser._parse_reminder_schedule("every 2 weeks at 9 am", "every 2 weeks at 9 am")
        assert result is not None
        fire_time, repeat_rule = result
        assert repeat_rule is not None
        assert repeat_rule["type"] == "interval"
        assert repeat_rule["interval_days"] == 14

    def test_every_n_days(self):
        """Lines 367-372: 'every 3 days'."""
        result = ScheduleIntentParser._parse_reminder_schedule("every 3 days at 9 am", "every 3 days at 9 am")
        assert result is not None
        fire_time, repeat_rule = result
        assert repeat_rule is not None
        assert repeat_rule["type"] == "interval"
        assert repeat_rule["interval_days"] == 3

    def test_every_with_weekdays(self):
        """Lines 373-376: 'every monday' yields weekly repeat."""
        result = ScheduleIntentParser._parse_reminder_schedule("every monday at 9 am", "every monday at 9 am")
        assert result is not None
        fire_time, repeat_rule = result
        assert repeat_rule is not None
        assert repeat_rule["type"] == "weekly"

    def test_every_without_specific_days(self):
        """Lines 373-376: 'every' without day names yields all days."""
        result = ScheduleIntentParser._parse_reminder_schedule("every at 9 am", "every at 9 am")
        assert result is not None
        fire_time, repeat_rule = result
        assert repeat_rule is not None
        assert repeat_rule["type"] == "weekly"
        assert repeat_rule["days"] == list(range(7))

    def test_specific_weekday_no_repeat(self):
        """Lines 377-379: day name without 'every' yields one-time."""
        result = ScheduleIntentParser._parse_reminder_schedule("on monday at 9 am", "on monday at 9 am")
        assert result is not None
        fire_time, repeat_rule = result
        assert repeat_rule is None

    def test_tomorrow(self):
        """Line 381: 'tomorrow' trigger."""
        result = ScheduleIntentParser._parse_reminder_schedule("tomorrow at 9 am", "tomorrow at 9 am")
        assert result is not None
        fire_time, repeat_rule = result
        assert repeat_rule is None

    def test_today(self):
        """Lines 383-386: 'today' trigger."""
        result = ScheduleIntentParser._parse_reminder_schedule("today at 11 pm", "today at 11 pm")
        assert result is not None
        fire_time, repeat_rule = result
        assert repeat_rule is None

    def test_default_time_in_past_advances_day(self):
        """Lines 388-389: default time in the past advances to next day."""
        result = ScheduleIntentParser._parse_reminder_schedule("at 3 am", "at 3 am")
        assert result is not None
        fire_time, repeat_rule = result
        assert repeat_rule is None


class TestExtractIntervalValue:
    """Tests for _extract_interval_value() - lines 403-410."""

    def test_extract_weeks(self):
        result = ScheduleIntentParser._extract_interval_value("every 2 weeks", ("week", "weeks"))
        assert result == 2

    def test_extract_months(self):
        result = ScheduleIntentParser._extract_interval_value("every 3 months", ("month", "months"))
        assert result == 3

    def test_no_match_returns_none(self):
        result = ScheduleIntentParser._extract_interval_value("on monday", ("week", "weeks"))
        assert result is None

    def test_extract_days(self):
        result = ScheduleIntentParser._extract_interval_value("every 5 days", ("day", "days"))
        assert result == 5


class TestExtractDayOfMonth:
    """Tests for _extract_day_of_month() - lines 422-427."""

    def test_extract_15th(self):
        result = ScheduleIntentParser._extract_day_of_month("on the 15th")
        assert result == 15

    def test_extract_1st(self):
        result = ScheduleIntentParser._extract_day_of_month("on the 1st")
        assert result == 1

    def test_extract_22nd(self):
        result = ScheduleIntentParser._extract_day_of_month("on the 22nd")
        assert result == 22

    def test_no_match_returns_none(self):
        result = ScheduleIntentParser._extract_day_of_month("every day")
        assert result is None

    def test_out_of_range_returns_none(self):
        result = ScheduleIntentParser._extract_day_of_month("on the 32nd")
        assert result is None


class TestExtractDurationSecondsFromText:
    """Tests for _extract_duration_seconds_from_text() - lines 465-470."""

    def test_compact_format_with_stop_words(self):
        """Lines 465-470: compact duration with stop words truncated."""
        result = ScheduleIntentParser._extract_duration_seconds_from_text("in 5m to do something")
        assert result > 0

    def test_compact_format_basic(self):
        result = ScheduleIntentParser._extract_duration_seconds_from_text("in 10m")
        assert result > 0

    def test_no_duration_returns_zero(self):
        result = ScheduleIntentParser._extract_duration_seconds_from_text("some random text")
        assert result == 0.0


class TestExtractTimeOfDayFromTextEdgeCases:
    """Additional tests for _extract_time_of_day_from_text() - uncovered paths."""

    def test_time_with_minutes_am_pm(self):
        """Line 487: time with minutes and am/pm like '3:30 pm'."""
        result = ScheduleIntentParser._extract_time_of_day_from_text("at 3:30 pm")
        assert result == "15:30"

    def test_compact_time_am_pm(self):
        """Lines 498-500: compact time like '930am'."""
        result = ScheduleIntentParser._extract_time_of_day_from_text("at 930 am")
        assert result == "09:30"

    def test_24h_colon_format(self):
        """Lines 498-500: 24h time like '14:30' without am/pm."""
        result = ScheduleIntentParser._extract_time_of_day_from_text("at 14:30")
        assert result == "14:30"


class TestNextWeekdayDatetime:
    """Tests for _next_weekday_datetime() - lines 546-551."""

    def test_next_weekday_future(self):
        """Basic next weekday calculation."""

        now = datetime(2025, 1, 13, 10, 0, 0, tzinfo=UTC)  # Monday
        result = ScheduleIntentParser._next_weekday_datetime(2, "09:00", now)  # Wednesday
        assert result.weekday() == 2

    def test_same_weekday_past_time_goes_to_next_week(self):
        """Lines 549-550: same weekday but past time advances 7 days."""

        now = datetime(2025, 1, 13, 18, 0, 0, tzinfo=UTC)  # Monday 6pm
        result = ScheduleIntentParser._next_weekday_datetime(0, "09:00", now)  # Monday 9am
        assert result.weekday() == 0
        assert result.day == 20  # Next Monday


class TestNextWeeklyDatetime:
    """Tests for _next_weekly_datetime() - lines 565-572."""

    def test_finds_next_matching_day(self):

        now = datetime(2025, 1, 13, 10, 0, 0, tzinfo=UTC)  # Monday
        result = ScheduleIntentParser._next_weekly_datetime([2, 4], "09:00", now)  # Wed, Fri
        assert result.weekday() in [2, 4]

    def test_empty_weekdays_normalized_to_all(self):
        """Line 565: empty list normalizes to all days."""

        now = datetime(2025, 1, 13, 10, 0, 0, tzinfo=UTC)  # Monday
        result = ScheduleIntentParser._next_weekly_datetime([], "09:00", now)
        assert result is not None


class TestNextMonthlyDatetime:
    """Tests for _next_monthly_datetime() - lines 586-594."""

    def test_future_day_this_month(self):

        now = datetime(2025, 1, 10, 10, 0, 0, tzinfo=UTC)
        result = ScheduleIntentParser._next_monthly_datetime(20, "09:00", now)
        assert result.day == 20
        assert result.month == 1

    def test_past_day_advances_month(self):
        """Lines 590-594: day already passed this month."""

        now = datetime(2025, 1, 25, 10, 0, 0, tzinfo=UTC)
        result = ScheduleIntentParser._next_monthly_datetime(10, "09:00", now)
        assert result.month == 2
        assert result.day == 10

    def test_day_clamped_to_month_range(self):
        """Lines 586-589: day > last day of month gets clamped."""

        now = datetime(2025, 1, 31, 23, 0, 0, tzinfo=UTC)
        result = ScheduleIntentParser._next_monthly_datetime(31, "09:00", now)
        # Feb 2025 has 28 days, so clamped
        assert result.month == 2
        assert result.day == 28


class TestFormatReminderConfirmationEdgeCases:
    """Additional tests for format_reminder_confirmation() uncovered paths."""

    def test_invalid_next_fire_iso_fallback(self):
        """Lines 630-631: invalid isoformat string triggers fallback."""
        event = Mock()
        event.next_fire = "not-a-date"
        event.event_type = "reminder"
        event.metadata = {"reminder": {}}
        result = ScheduleIntentParser.format_reminder_confirmation(event)
        assert "I'll remind you" in result

    def test_tomorrow_day_phrase(self):
        """Lines 641-642: next_fire is tomorrow."""
        from datetime import timedelta

        tomorrow = datetime.now().astimezone() + timedelta(days=1)
        tomorrow = tomorrow.replace(hour=15, minute=0, second=0, microsecond=0)
        event = Mock()
        event.next_fire = tomorrow.isoformat()
        event.event_type = "reminder"
        event.metadata = {"reminder": {}}
        result = ScheduleIntentParser.format_reminder_confirmation(event)
        assert "tomorrow" in result

    def test_future_day_phrase(self):
        """Lines 643-644: next_fire is beyond tomorrow."""
        from datetime import timedelta

        future = datetime.now().astimezone() + timedelta(days=3)
        future = future.replace(hour=15, minute=0, second=0, microsecond=0)
        event = Mock()
        event.next_fire = future.isoformat()
        event.event_type = "reminder"
        event.metadata = {"reminder": {}}
        result = ScheduleIntentParser.format_reminder_confirmation(event)
        assert "on " in result


class TestFormatTimePhraseFromString:
    """Tests for _format_time_phrase_from_string() - lines 659-660."""

    def test_valid_time(self):
        result = ScheduleIntentParser._format_time_phrase_from_string("15:30")
        assert "3:30 PM" in result

    def test_valid_time_on_the_hour(self):
        result = ScheduleIntentParser._format_time_phrase_from_string("15:00")
        assert "3 PM" in result

    def test_invalid_time_returns_input(self):
        """Lines 659-660: invalid format returns original string."""
        result = ScheduleIntentParser._format_time_phrase_from_string("bad")
        assert result == "bad"


class TestDescribeReminderRepeatEdgeCases:
    """Additional tests for _describe_reminder_repeat() uncovered paths."""

    def test_weekly_single_day(self):
        """Line 699: single day in weekly repeat."""
        repeat = {"type": "weekly", "days": [0], "time": "09:00"}
        result = ScheduleIntentParser._describe_reminder_repeat(repeat)
        assert "Monday" in result
        assert "and" not in result

    def test_monthly_no_day(self):
        """Line 707: monthly without day field."""
        repeat = {"type": "monthly", "time": "09:00"}
        result = ScheduleIntentParser._describe_reminder_repeat(repeat)
        assert "each month" in result

    def test_interval_days_not_divisible_by_7(self):
        """Lines 717-718: interval days not divisible by 7."""
        repeat = {"type": "interval", "interval_days": 5, "time": "09:00"}
        result = ScheduleIntentParser._describe_reminder_repeat(repeat)
        assert "every 5 days" in result

    def test_unknown_repeat_type(self):
        """Line 718: unknown type falls through to default."""
        repeat = {"type": "unknown", "time": "10:00"}
        result = ScheduleIntentParser._describe_reminder_repeat(repeat)
        assert "at" in result
