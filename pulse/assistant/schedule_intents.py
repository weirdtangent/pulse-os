"""Schedule intent parsing for Pulse Assistant.

This module parses natural language into timer, alarm, and reminder intents.
It extracts schedule-related information from voice transcripts and formats
confirmation messages for the user.

Intent types:
- Timer: "set a timer for 5 minutes", "start a 30 second timer"
- Alarm: "set an alarm for 7:30 am", "alarm for 8am on weekdays"
- Reminder: "remind me to take pills at 3pm", "remind me every Monday at 9am"
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from pulse.assistant.schedule_service import ScheduledEvent, parse_day_tokens
from pulse.datetime_utils import parse_duration_seconds

if TYPE_CHECKING:
    from pulse.assistant.schedule_service import ScheduleService


@dataclass
class ReminderIntent:
    """Parsed reminder intent from natural language."""

    message: str
    fire_time: datetime
    repeat_rule: dict[str, Any] | None


class ScheduleIntentParser:
    """Parses natural language into timer, alarm, and reminder intents.

    This class provides stateless parsing methods for schedule-related
    voice commands. All methods are static except those that need to
    check for schedule_service availability.
    """

    # ========================================================================
    # Timer Intent Parsing
    # ========================================================================

    @staticmethod
    def extract_timer_start_intent(lowered: str) -> tuple[int, str | None] | None:
        """Extract timer intent from text.

        Args:
            lowered: Lowercase, normalized transcript text

        Returns:
            Tuple of (duration_seconds, optional_label) or None if not a timer command
        """
        if "timer" not in lowered:
            return None
        if not any(word in lowered for word in ("start", "set", "create")):
            return None
        duration_match = re.search(
            r"(?:for\s+)?((?:\d+(?:\.\d+)?|[a-z]+(?:\s+[a-z]+)?))\s*(seconds?|second|secs?|minutes?|minute|mins?|hours?|hour|hrs?)",
            lowered,
        )
        if not duration_match:
            return None
        raw_amount = duration_match.group(1)
        amount = ScheduleIntentParser.parse_numeric_token(raw_amount)
        if amount is None:
            return None
        unit = duration_match.group(2)
        unit = unit.rstrip("s")
        multipliers = {
            "second": 1,
            "sec": 1,
            "minute": 60,
            "min": 60,
            "hour": 3600,
            "hr": 3600,
        }
        multiplier = multipliers.get(unit, 60)
        duration_seconds = max(1, int(amount * multiplier))
        label = None
        label_match = re.search(r"timer for ([a-z][a-z0-9 ]+)", lowered)
        if label_match:
            candidate = label_match.group(1).strip()
            if candidate and not re.fullmatch(r"\d+(\.\d+)?\s*(seconds?|minutes?|hours?)", candidate):
                label = candidate
        return duration_seconds, label

    @staticmethod
    def parse_numeric_token(token: str) -> float | None:
        """Convert text number or numeric string to float.

        Args:
            token: Text like "five" or "5" or "5.5"

        Returns:
            Float value or None if not parseable
        """
        try:
            return float(token)
        except ValueError:
            pass
        token = token.strip().lower()
        number_words = {
            "zero": 0,
            "one": 1,
            "two": 2,
            "three": 3,
            "four": 4,
            "five": 5,
            "six": 6,
            "seven": 7,
            "eight": 8,
            "nine": 9,
            "ten": 10,
            "eleven": 11,
            "twelve": 12,
            "thirteen": 13,
            "fourteen": 14,
            "fifteen": 15,
            "sixteen": 16,
            "seventeen": 17,
            "eighteen": 18,
            "nineteen": 19,
            "twenty": 20,
            "thirty": 30,
            "forty": 40,
            "fifty": 50,
            "sixty": 60,
            "half": 0.5,
            "quarter": 0.25,
            "a": 1,
            "an": 1,
        }
        if token in number_words:
            return float(number_words[token])
        # Handle composite like "twenty five"
        parts = token.split()
        if len(parts) == 2 and parts[0] in number_words and parts[1] in number_words and number_words[parts[1]] < 10:
            return float(number_words[parts[0]] + number_words[parts[1]])
        return None

    @staticmethod
    def describe_duration(seconds: int) -> str:
        """Convert seconds to human-readable duration.

        Args:
            seconds: Duration in seconds

        Returns:
            Human-readable string like "5 minutes" or "1 hour"
        """
        if seconds % 3600 == 0:
            hours = seconds // 3600
            return f"{hours} hour{'s' if hours != 1 else ''}"
        if seconds % 60 == 0:
            minutes = seconds // 60
            return f"{minutes} minute{'s' if minutes != 1 else ''}"
        return f"{seconds} seconds"

    # ========================================================================
    # Alarm Intent Parsing
    # ========================================================================

    @staticmethod
    def extract_alarm_start_intent(text: str) -> tuple[str, list[int] | None, str | None] | None:
        """Extract alarm intent from text.

        Args:
            text: Lowercase, normalized transcript text

        Returns:
            Tuple of (time_of_day, optional_days, optional_label) or None
        """
        if "alarm" not in text:
            return None
        time_match = re.search(
            r"(?:alarm\s+(?:for|at)\s+)?((?:\d{1,2}\s+\d{2})|\d{1,4}(?::\d{2})?)\s*(am|pm)?",
            text,
        )
        if not time_match:
            return None
        time_token = time_match.group(1)
        suffix = time_match.group(2)
        time_of_day = ScheduleIntentParser.parse_time_token(time_token, suffix)
        if not time_of_day:
            return None
        days = None
        day_match = re.search(r"(?:on|every)\s+([a-z ,]+)", text)
        if day_match:
            days = parse_day_tokens(day_match.group(1))
        label = None
        label_match = re.search(r"(?:called|named)\s+([a-z0-9 ]+)", text)
        if label_match:
            label = label_match.group(1).strip()
        return time_of_day, days, label

    @staticmethod
    def parse_time_token(token: str, suffix: str | None) -> str | None:
        """Parse time token into HH:MM format.

        Args:
            token: Time string like "930", "9:30", "9 30"
            suffix: AM/PM suffix or None

        Returns:
            Time string in "HH:MM" format or None
        """
        token = token.replace(" ", "")
        hour_str = token
        minute_str = "00"
        if ":" in token:
            hour_str, minute_str = token.split(":", 1)
        elif len(token) in (3, 4):
            hour_str = token[:-2]
            minute_str = token[-2:]
        try:
            hour = int(hour_str)
            minute = int(minute_str)
        except ValueError:
            return None
        if suffix:
            if suffix.startswith("p") and hour < 12:
                hour += 12
            if suffix.startswith("a") and hour == 12:
                hour = 0
        hour %= 24
        minute = max(0, min(59, minute))
        return f"{hour:02d}:{minute:02d}"

    @staticmethod
    def format_alarm_confirmation(time_of_day: str, days: list[int] | None, label: str | None) -> str:
        """Format alarm confirmation message for user.

        Args:
            time_of_day: Time in HH:MM format
            days: List of weekday indexes (0=Monday) or None
            label: Optional alarm label

        Returns:
            User-facing confirmation message
        """
        try:
            dt = datetime.strptime(time_of_day, "%H:%M").replace(year=1900, month=1, day=1)
            time_phrase = dt.strftime("%-I:%M %p")
            if dt.minute == 0:
                # Many TTS voices over-articulate the ":00" segment, so drop it for o'clock times.
                time_phrase = dt.strftime("%-I %p")
        except ValueError:
            time_phrase = time_of_day
        if days:
            day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
            normalized_days = sorted({d % 7 for d in days})
            if normalized_days == [0, 1, 2, 3, 4]:
                day_phrase = " on weekdays"
            elif normalized_days == [5, 6]:
                day_phrase = " on weekends"
            elif normalized_days == list(range(7)):
                day_phrase = " every day"
            elif len(normalized_days) == 1:
                day_phrase = f" on {day_names[normalized_days[0]]}"
            else:
                names = ", ".join(day_names[d] for d in normalized_days)
                day_phrase = f" on {names}"
        else:
            day_phrase = ""
        label_phrase = f" called {label}" if label else ""
        return f"Setting an alarm for {time_phrase}{day_phrase}{label_phrase}."

    # ========================================================================
    # Reminder Intent Parsing
    # ========================================================================

    @staticmethod
    def extract_reminder_intent(
        normalized: str,
        original: str,
        schedule_service: ScheduleService | None,
    ) -> ReminderIntent | None:
        """Extract reminder intent from text.

        Args:
            normalized: Lowercase, normalized transcript text
            original: Original transcript text (preserves case for message)
            schedule_service: Schedule service instance (required for parsing)

        Returns:
            ReminderIntent or None if not a reminder command
        """
        if "remind me" not in normalized or not schedule_service:
            return None
        idx = normalized.find("remind me")
        suffix_original = original[idx + len("remind me") :].strip()
        suffix_lower = normalized[idx + len("remind me") :].strip()
        if not suffix_original:
            return None
        message = suffix_original.strip()
        schedule_section = suffix_lower
        to_idx = suffix_lower.find(" to ")
        if to_idx != -1:
            message = suffix_original[to_idx + 4 :].strip()
            schedule_section = suffix_lower[:to_idx].strip()
        parsed = ScheduleIntentParser._parse_reminder_schedule(schedule_section, suffix_lower)
        if not parsed:
            return None
        fire_time, repeat_rule = parsed
        message = message or "reminder"
        return ReminderIntent(message=message, fire_time=fire_time, repeat_rule=repeat_rule)

    @staticmethod
    def _parse_reminder_schedule(
        schedule_text: str,
        fallback_text: str,
    ) -> tuple[datetime, dict[str, Any] | None] | None:
        """Parse reminder schedule from text.

        Args:
            schedule_text: Primary text to parse for schedule
            fallback_text: Fallback text if schedule_text is empty

        Returns:
            Tuple of (fire_time, repeat_rule) or None
        """
        text = schedule_text or fallback_text
        lower = text.strip().lower()
        if not lower:
            lower = fallback_text.lower()
        now = datetime.now().astimezone()
        duration_seconds = ScheduleIntentParser._extract_duration_seconds_from_text(lower)
        if duration_seconds > 0:
            return now + timedelta(seconds=duration_seconds), None
        time_of_day = ScheduleIntentParser._extract_time_of_day_from_text(lower)
        has_every = "every" in lower
        day_indexes = parse_day_tokens(lower)
        if has_every:
            interval_months = ScheduleIntentParser._extract_interval_value(lower, ("month", "months"))
            interval_weeks = ScheduleIntentParser._extract_interval_value(lower, ("week", "weeks"))
            interval_days = ScheduleIntentParser._extract_interval_value(lower, ("day", "days"))
            if "month" in lower or "monthly" in lower:
                if interval_months and interval_months > 1:
                    start = ScheduleIntentParser._apply_time_of_day(now, time_of_day)
                    if start <= now:
                        start = ScheduleIntentParser._add_months_local(start, interval_months)
                    repeat_rule = {"type": "interval", "interval_months": interval_months, "time": time_of_day}
                    return start, repeat_rule
                day_of_month = ScheduleIntentParser._extract_day_of_month(lower) or now.day
                fire_time = ScheduleIntentParser._next_monthly_datetime(day_of_month, time_of_day, now)
                repeat_rule = {"type": "monthly", "day": day_of_month, "time": time_of_day}
                return fire_time, repeat_rule
            if interval_months:
                start = ScheduleIntentParser._apply_time_of_day(now, time_of_day)
                if start <= now:
                    start = ScheduleIntentParser._add_months_local(start, interval_months)
                repeat_rule = {"type": "interval", "interval_months": interval_months, "time": time_of_day}
                return start, repeat_rule
            if interval_weeks:
                days_to_add = interval_weeks * 7
                start = ScheduleIntentParser._apply_time_of_day(now, time_of_day)
                if start <= now:
                    start += timedelta(days=days_to_add)
                repeat_rule = {"type": "interval", "interval_days": days_to_add, "time": time_of_day}
                return start, repeat_rule
            if interval_days:
                start = ScheduleIntentParser._apply_time_of_day(now, time_of_day)
                if start <= now:
                    start += timedelta(days=interval_days)
                repeat_rule = {"type": "interval", "interval_days": interval_days, "time": time_of_day}
                return start, repeat_rule
            weekdays = day_indexes or list(range(7))
            fire_time = ScheduleIntentParser._next_weekly_datetime(weekdays, time_of_day, now)
            repeat_rule = {"type": "weekly", "days": weekdays, "time": time_of_day}
            return fire_time, repeat_rule
        if day_indexes:
            fire_time = ScheduleIntentParser._next_weekday_datetime(day_indexes[0], time_of_day, now)
            return fire_time, None
        if "tomorrow" in lower:
            return ScheduleIntentParser._apply_time_of_day(now + timedelta(days=1), time_of_day), None
        if "today" in lower:
            candidate = ScheduleIntentParser._apply_time_of_day(now, time_of_day)
            if candidate <= now:
                candidate += timedelta(days=1)
            return candidate, None
        default_time = ScheduleIntentParser._apply_time_of_day(now, time_of_day)
        if default_time <= now:
            default_time += timedelta(days=1)
        return default_time, None

    @staticmethod
    def _extract_interval_value(text: str, keywords: tuple[str, ...]) -> int | None:
        """Extract interval value from text like 'every 2 weeks'.

        Args:
            text: Text to search
            keywords: Keywords to match (e.g., ("week", "weeks"))

        Returns:
            Integer interval value or None
        """
        joined = "|".join(keywords)
        match = re.search(rf"every\s+(\d+)\s+({joined})", text)
        if match:
            try:
                return int(match.group(1))
            except ValueError:
                return None
        return None

    @staticmethod
    def _extract_day_of_month(text: str) -> int | None:
        """Extract day of month from text like 'on the 15th'.

        Args:
            text: Text to search

        Returns:
            Day of month (1-31) or None
        """
        match = re.search(r"\bon\s+the\s+(\d{1,2})(?:st|nd|rd|th)?\b", text)
        if match:
            day = int(match.group(1))
            if 1 <= day <= 31:
                return day
        return None

    @staticmethod
    def _extract_duration_seconds_from_text(text: str) -> float:
        """Extract duration from text like 'in 5 minutes'.

        Supports both compact formats (5m, 10s) and natural language (5 minutes, ten seconds).

        Args:
            text: Text to search

        Returns:
            Duration in seconds (0 if not found)
        """
        # First try to match natural language duration pattern: "in X minutes/hours/seconds"
        duration_match = re.search(
            r"\bin\s+((?:\d+(?:\.\d+)?|[a-z]+(?:\s+[a-z]+)?))\s*(seconds?|secs?|minutes?|mins?|hours?|hrs?)",
            text,
        )
        if duration_match:
            raw_amount = duration_match.group(1)
            amount = ScheduleIntentParser.parse_numeric_token(raw_amount)
            if amount is not None:
                unit = duration_match.group(2).rstrip("s")
                multipliers = {
                    "second": 1,
                    "sec": 1,
                    "minute": 60,
                    "min": 60,
                    "hour": 3600,
                    "hr": 3600,
                }
                return amount * multipliers.get(unit, 60)

        # Fall back to compact format via parse_duration_seconds
        match = re.search(r"\bin\s+([0-9][a-z0-9 :]*)", text)
        if not match:
            return 0.0
        candidate = match.group(1)
        for stop in (" to ", " for ", ",", " and "):
            idx = candidate.find(stop)
            if idx != -1:
                candidate = candidate[:idx]
        return parse_duration_seconds(candidate.strip())

    @staticmethod
    def _extract_time_of_day_from_text(text: str) -> str:
        """Extract time of day from text.

        Args:
            text: Text to search

        Returns:
            Time string in "HH:MM" format (defaults to "08:00")
        """
        lower = text.lower()
        match = re.search(r"(?<!\d)(\d{1,2})(?::(\d{2}))?\s*(am|pm)", lower)
        if match:
            token = match.group(1)
            if match.group(2):
                token = f"{token}:{match.group(2)}"
            parsed = ScheduleIntentParser.parse_time_token(token, match.group(3))
            if parsed:
                return parsed
        match = re.search(r"\b(\d{3,4})\s*(am|pm)\b", lower)
        if match:
            parsed = ScheduleIntentParser.parse_time_token(match.group(1), match.group(2))
            if parsed:
                return parsed
        match = re.search(r"\b(\d{1,2}:\d{2})\b", lower)
        if match:
            parsed = ScheduleIntentParser.parse_time_token(match.group(1), None)
            if parsed:
                return parsed
        # Check keyword-based times with whole-word matching.
        # Order matters: more specific/longer keywords first to avoid substring clashes
        # (e.g., "midnight" should not be matched as "night").
        keyword_list = [
            ("midnight", "00:00"),
            ("noon", "12:00"),
            ("tonight", "20:00"),
            ("night", "20:00"),
            ("evening", "17:00"),
            ("afternoon", "13:00"),
            ("morning", "08:00"),
        ]
        for keyword, value in keyword_list:
            if re.search(rf"\b{re.escape(keyword)}\b", lower):
                return value
        return "08:00"

    @staticmethod
    def _apply_time_of_day(reference: datetime, time_str: str) -> datetime:
        """Apply time of day to a reference datetime.

        Args:
            reference: Reference datetime
            time_str: Time in "HH:MM" format

        Returns:
            Datetime with time applied
        """
        hour_str, minute_str = time_str.split(":")
        hour = int(hour_str)
        minute = int(minute_str)
        return reference.replace(hour=hour, minute=minute, second=0, microsecond=0)

    @staticmethod
    def _next_weekday_datetime(weekday: int, time_str: str, now: datetime) -> datetime:
        """Calculate next occurrence of a specific weekday.

        Args:
            weekday: Weekday index (0=Monday)
            time_str: Time in "HH:MM" format
            now: Current datetime

        Returns:
            Next occurrence of the weekday at the specified time
        """
        weekday = weekday % 7
        candidate = ScheduleIntentParser._apply_time_of_day(now, time_str)
        offset = (weekday - candidate.weekday()) % 7
        if offset == 0 and candidate <= now:
            offset = 7
        return ScheduleIntentParser._apply_time_of_day(now + timedelta(days=offset), time_str)

    @staticmethod
    def _next_weekly_datetime(weekdays: list[int], time_str: str, now: datetime) -> datetime:
        """Find next date matching any of the weekdays.

        Args:
            weekdays: List of weekday indexes (0=Monday)
            time_str: Time in "HH:MM" format
            now: Current datetime

        Returns:
            Next occurrence matching any weekday at the specified time
        """
        weekdays = sorted({day % 7 for day in weekdays}) or list(range(7))
        for offset in range(0, 8):
            candidate = ScheduleIntentParser._apply_time_of_day(now + timedelta(days=offset), time_str)
            if candidate <= now:
                continue
            if candidate.weekday() in weekdays:
                return candidate
        return ScheduleIntentParser._apply_time_of_day(now + timedelta(days=1), time_str)

    @staticmethod
    def _next_monthly_datetime(day: int, time_str: str, now: datetime) -> datetime:
        """Find next occurrence of specific day of month.

        Args:
            day: Day of month (1-31)
            time_str: Time in "HH:MM" format
            now: Current datetime

        Returns:
            Next occurrence of the day at the specified time
        """
        day = max(1, min(31, day))
        candidate = ScheduleIntentParser._apply_time_of_day(now, time_str)
        last = calendar.monthrange(candidate.year, candidate.month)[1]
        candidate = candidate.replace(day=min(day, last))
        if candidate <= now:
            candidate = ScheduleIntentParser._add_months_local(candidate, 1)
            last = calendar.monthrange(candidate.year, candidate.month)[1]
            candidate = candidate.replace(day=min(day, last))
        return candidate

    @staticmethod
    def _add_months_local(dt_obj: datetime, months: int) -> datetime:
        """Add months to a datetime, handling month boundaries.

        Args:
            dt_obj: Source datetime
            months: Number of months to add

        Returns:
            Datetime with months added
        """
        total = dt_obj.month - 1 + months
        year = dt_obj.year + total // 12
        month = total % 12 + 1
        day = min(dt_obj.day, calendar.monthrange(year, month)[1])
        return dt_obj.replace(year=year, month=month, day=day)

    # ========================================================================
    # Confirmation Message Formatting
    # ========================================================================

    @staticmethod
    def format_reminder_confirmation(event: ScheduledEvent) -> str:
        """Format reminder confirmation message for user.

        Args:
            event: Created scheduled event

        Returns:
            User-facing confirmation message
        """
        next_fire = event.next_fire
        try:
            dt = datetime.fromisoformat(next_fire).astimezone()
        except (TypeError, ValueError):
            dt = datetime.now().astimezone()
        repeat_meta = event.metadata.get("reminder") if event.event_type == "reminder" else {}
        repeat_rule = repeat_meta.get("repeat") if isinstance(repeat_meta, dict) else None
        if repeat_rule:
            repeat_phrase = ScheduleIntentParser._describe_reminder_repeat(repeat_rule)
            return f"Okay, I'll remind you {repeat_phrase}."
        time_phrase = dt.strftime("%-I:%M %p")
        today = datetime.now().astimezone().date()
        if dt.date() == today:
            day_phrase = "today"
        elif dt.date() == today + timedelta(days=1):
            day_phrase = "tomorrow"
        else:
            day_phrase = f"on {dt.strftime('%A')}"
        return f"Got it, I'll remind you {day_phrase} at {time_phrase}."

    @staticmethod
    def _format_time_phrase_from_string(time_str: str) -> str:
        """Convert HH:MM to human-readable time phrase.

        Args:
            time_str: Time in "HH:MM" format

        Returns:
            Human-readable time like "3:30 PM" or "3 PM"
        """
        try:
            dt = datetime.strptime(time_str, "%H:%M")
        except ValueError:
            return time_str
        return dt.strftime("%-I:%M %p") if dt.minute else dt.strftime("%-I %p")

    @staticmethod
    def _ordinal(value: int) -> str:
        """Convert integer to ordinal string.

        Args:
            value: Integer value

        Returns:
            Ordinal string like "1st", "2nd", "3rd"
        """
        if 10 <= value % 100 <= 20:
            suffix = "th"
        else:
            suffix = {1: "st", 2: "nd", 3: "rd"}.get(value % 10, "th")
        return f"{value}{suffix}"

    @staticmethod
    def _describe_reminder_repeat(repeat: dict[str, Any]) -> str:
        """Describe a repeat rule for user output.

        Args:
            repeat: Repeat rule dictionary

        Returns:
            Human-readable repeat description
        """
        repeat_type = (repeat.get("type") or "").lower()
        time_text = repeat.get("time") or "08:00"
        time_phrase = ScheduleIntentParser._format_time_phrase_from_string(time_text)
        if repeat_type == "weekly":
            days = repeat.get("days") or list(range(7))
            if sorted(days) == list(range(7)):
                return f"every day at {time_phrase}"
            day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
            labels = [day_names[day % 7] for day in days]
            if len(labels) == 1:
                day_phrase = labels[0]
            else:
                day_phrase = ", ".join(labels[:-1]) + f", and {labels[-1]}"
            return f"every {day_phrase} at {time_phrase}"
        if repeat_type == "monthly":
            day = repeat.get("day")
            if isinstance(day, int):
                return f"on the {ScheduleIntentParser._ordinal(day)} of each month at {time_phrase}"
            return f"each month at {time_phrase}"
        if repeat_type == "interval":
            months = repeat.get("interval_months")
            days = repeat.get("interval_days")
            if months:
                return f"every {months} month{'s' if months != 1 else ''} at {time_phrase}"
            if days:
                if days % 7 == 0:
                    weeks = days // 7
                    return f"every {weeks} week{'s' if weeks != 1 else ''} at {time_phrase}"
                return f"every {days} day{'s' if days != 1 else ''} at {time_phrase}"
        return f"at {time_phrase}"
