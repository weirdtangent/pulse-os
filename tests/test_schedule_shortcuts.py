"""Tests for ScheduleShortcutHandler."""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock

import pytest

from pulse.assistant.schedule_shortcuts import ScheduleShortcutHandler


@pytest.fixture
def mock_schedule_service():
    """Create mock schedule service."""
    service = Mock()
    service.list_events = Mock(return_value=[])
    service.active_event = Mock(return_value=None)
    service.get_next_alarm = Mock(return_value=None)
    service.create_timer = AsyncMock()
    service.create_alarm = AsyncMock()
    service.create_reminder = AsyncMock()
    service.stop_event = AsyncMock()
    service.extend_timer = AsyncMock()
    service.delete_event = AsyncMock()
    service.cancel_all_timers = AsyncMock(return_value=0)
    return service


@pytest.fixture
def mock_schedule_intents():
    """Create mock schedule intents parser."""
    parser = Mock()
    parser.extract_timer_start_intent = Mock(return_value=None)
    parser.extract_alarm_start_intent = Mock(return_value=None)
    parser.extract_reminder_intent = Mock(return_value=None)
    parser.describe_duration = Mock(return_value="5 minutes")
    parser.format_alarm_confirmation = Mock(return_value="Alarm set.")
    parser.format_reminder_confirmation = Mock(return_value="Reminder set.")
    return parser


@pytest.fixture
def mock_publisher():
    """Create mock MQTT publisher."""
    publisher = Mock()
    publisher._publish_info_overlay = Mock()
    return publisher


@pytest.fixture
def mock_config():
    """Create mock assistant config."""
    config = Mock()
    config.calendar = Mock()
    config.calendar.enabled = True
    config.calendar.lookahead_hours = 48
    config.calendar.feeds = ["https://example.com/cal.ics"]
    return config


@pytest.fixture
def handler(mock_schedule_service, mock_schedule_intents, mock_publisher, mock_config):
    """Create handler with mocked dependencies."""
    h = ScheduleShortcutHandler(
        schedule_service=mock_schedule_service,
        schedule_intents=mock_schedule_intents,
        publisher=mock_publisher,
        config=mock_config,
    )
    h.set_speak_callback(AsyncMock())
    h.set_log_response_callback(Mock())
    return h


# =============================================================================
# Static Method Tests
# =============================================================================


class TestIsStopPhrase:
    """Tests for is_stop_phrase() static method."""

    def test_stop(self):
        """Single word 'stop' is a stop phrase."""
        assert ScheduleShortcutHandler.is_stop_phrase("stop") is True

    def test_stop_it(self):
        """'stop it' is a stop phrase."""
        assert ScheduleShortcutHandler.is_stop_phrase("stop it") is True

    def test_stop_alarm(self):
        """'stop the alarm' is a stop phrase."""
        assert ScheduleShortcutHandler.is_stop_phrase("stop the alarm") is True

    def test_cancel_timer(self):
        """'cancel my timer' triggers timer stop pattern."""
        assert ScheduleShortcutHandler.is_stop_phrase("cancel my timer") is True

    def test_turn_off_alarm(self):
        """'turn off the alarm' is a stop phrase."""
        assert ScheduleShortcutHandler.is_stop_phrase("turn off the alarm") is True

    def test_unrelated_phrase(self):
        """Unrelated phrases are not stop phrases."""
        assert ScheduleShortcutHandler.is_stop_phrase("set timer for 5 minutes") is False

    def test_what_time(self):
        """Question about time is not a stop phrase."""
        assert ScheduleShortcutHandler.is_stop_phrase("what time is it") is False


class TestMentionsAlarmCancel:
    """Tests for mentions_alarm_cancel() static method."""

    def test_cancel_alarm(self):
        """'cancel my alarm' mentions alarm cancel."""
        assert ScheduleShortcutHandler.mentions_alarm_cancel("cancel my alarm") is True

    def test_delete_alarm(self):
        """'delete the alarm' mentions alarm cancel."""
        assert ScheduleShortcutHandler.mentions_alarm_cancel("delete the alarm") is True

    def test_remove_alarm(self):
        """'remove my alarm' mentions alarm cancel."""
        assert ScheduleShortcutHandler.mentions_alarm_cancel("remove my alarm") is True

    def test_turn_off_alarm(self):
        """'turn off my alarm' mentions alarm cancel."""
        assert ScheduleShortcutHandler.mentions_alarm_cancel("turn off my alarm") is True

    def test_cancel_timer_no_alarm(self):
        """'cancel my timer' does not mention alarm cancel."""
        assert ScheduleShortcutHandler.mentions_alarm_cancel("cancel my timer") is False

    def test_set_alarm(self):
        """'set alarm' without cancel word returns false."""
        assert ScheduleShortcutHandler.mentions_alarm_cancel("set alarm for 7am") is False


class TestExtractTimerLabel:
    """Tests for extract_timer_label() static method."""

    def test_timer_for_label(self):
        """Extract label from 'timer for eggs'."""
        assert ScheduleShortcutHandler.extract_timer_label("timer for eggs") == "eggs"

    def test_timer_named_label(self):
        """Extract label from 'timer named laundry'."""
        assert ScheduleShortcutHandler.extract_timer_label("timer named laundry") == "laundry"

    def test_label_timer(self):
        """Extract label from 'cancel the eggs timer'."""
        assert ScheduleShortcutHandler.extract_timer_label("cancel the for eggs timer") == "eggs"

    def test_no_label(self):
        """No label when none specified."""
        assert ScheduleShortcutHandler.extract_timer_label("cancel the timer") is None

    def test_set_timer_no_label(self):
        """No label in basic timer command without 'for' pattern."""
        # Note: "set timer for 5 minutes" matches "timer for X" pattern
        # A command without explicit label would be "set a 5 minute timer"
        assert ScheduleShortcutHandler.extract_timer_label("set a 5 minute timer") is None


class TestFormatTimerLabel:
    """Tests for format_timer_label() static method."""

    def test_seconds_only(self):
        """Format seconds-only duration."""
        assert ScheduleShortcutHandler.format_timer_label(45) == "45s"

    def test_one_minute(self):
        """Format exactly one minute."""
        assert ScheduleShortcutHandler.format_timer_label(60) == "1m"

    def test_minutes_only(self):
        """Format minutes without seconds."""
        assert ScheduleShortcutHandler.format_timer_label(300) == "5m"

    def test_minutes_and_seconds(self):
        """Format minutes with seconds."""
        assert ScheduleShortcutHandler.format_timer_label(90) == "1m 30s"

    def test_one_hour(self):
        """Format exactly one hour."""
        assert ScheduleShortcutHandler.format_timer_label(3600) == "1h"

    def test_hours_and_minutes(self):
        """Format hours with minutes."""
        assert ScheduleShortcutHandler.format_timer_label(3660) == "1h 1m"

    def test_invalid_input(self):
        """Invalid input returns 'Timer'."""
        assert ScheduleShortcutHandler.format_timer_label("invalid") == "Timer"
        assert ScheduleShortcutHandler.format_timer_label(None) == "Timer"


class TestFormatReminderMeta:
    """Tests for format_reminder_meta() static method."""

    def test_basic_reminder(self):
        """Format reminder with just next_fire."""
        reminder = {"next_fire": "2025-03-15T14:30:00-05:00"}
        result = ScheduleShortcutHandler.format_reminder_meta(reminder)
        assert "Mar 15" in result
        # Time displayed depends on local timezone; just verify PM format
        assert "PM" in result

    def test_invalid_date(self):
        """Invalid date returns placeholder."""
        reminder = {"next_fire": "invalid"}
        assert ScheduleShortcutHandler.format_reminder_meta(reminder) == "—"

    def test_no_next_fire(self):
        """Missing next_fire returns placeholder."""
        reminder = {}
        assert ScheduleShortcutHandler.format_reminder_meta(reminder) == "—"

    def test_daily_repeat(self):
        """Daily repeat adds 'Daily' label."""
        reminder = {
            "next_fire": "2025-03-15T14:30:00-05:00",
            "metadata": {"reminder": {"repeat": {"type": "weekly", "days": [0, 1, 2, 3, 4, 5, 6]}}},
        }
        result = ScheduleShortcutHandler.format_reminder_meta(reminder)
        assert "Daily" in result

    def test_weekly_repeat(self):
        """Weekly repeat shows day names."""
        reminder = {
            "next_fire": "2025-03-15T14:30:00-05:00",
            "metadata": {"reminder": {"repeat": {"type": "weekly", "days": [0, 2, 4]}}},
        }
        result = ScheduleShortcutHandler.format_reminder_meta(reminder)
        assert "Mon" in result
        assert "Wed" in result
        assert "Fri" in result


# =============================================================================
# Lookup Method Tests
# =============================================================================


class TestFindAlarmCandidate:
    """Tests for find_alarm_candidate()."""

    def test_no_alarms(self, handler, mock_schedule_service):
        """Returns None when no alarms exist."""
        mock_schedule_service.list_events.return_value = []
        result = handler.find_alarm_candidate("07:00", None)
        assert result is None

    def test_match_by_time(self, handler, mock_schedule_service):
        """Find alarm by time."""
        mock_schedule_service.list_events.return_value = [
            {"id": "a1", "time": "07:00", "label": "Morning"},
            {"id": "a2", "time": "08:00", "label": "Work"},
        ]
        result = handler.find_alarm_candidate("07:00", None)
        assert result["id"] == "a1"

    def test_match_by_label(self, handler, mock_schedule_service):
        """Find alarm by label."""
        mock_schedule_service.list_events.return_value = [
            {"id": "a1", "time": "07:00", "label": "Morning"},
            {"id": "a2", "time": "08:00", "label": "Work"},
        ]
        result = handler.find_alarm_candidate(None, "work")
        assert result["id"] == "a2"

    def test_no_match(self, handler, mock_schedule_service):
        """Returns None when no match found."""
        mock_schedule_service.list_events.return_value = [
            {"id": "a1", "time": "07:00", "label": "Morning"},
        ]
        result = handler.find_alarm_candidate("09:00", None)
        assert result is None


class TestFindTimerCandidate:
    """Tests for find_timer_candidate()."""

    def test_no_timers(self, handler, mock_schedule_service):
        """Returns None when no timers exist."""
        mock_schedule_service.list_events.return_value = []
        result = handler.find_timer_candidate(None)
        assert result is None

    def test_single_timer_no_label(self, handler, mock_schedule_service):
        """Return single timer when no label specified."""
        mock_schedule_service.list_events.return_value = [{"id": "t1", "label": ""}]
        mock_schedule_service.active_event.return_value = None
        result = handler.find_timer_candidate(None)
        assert result["id"] == "t1"

    def test_match_by_label(self, handler, mock_schedule_service):
        """Find timer by label."""
        mock_schedule_service.list_events.return_value = [
            {"id": "t1", "label": "eggs"},
            {"id": "t2", "label": "laundry"},
        ]
        result = handler.find_timer_candidate("laundry")
        assert result["id"] == "t2"

    def test_active_timer_preferred(self, handler, mock_schedule_service):
        """Active timer returned when no label specified."""
        mock_schedule_service.list_events.return_value = [
            {"id": "t1", "label": ""},
            {"id": "t2", "label": ""},
        ]
        mock_schedule_service.active_event.return_value = {"id": "t2", "label": ""}
        result = handler.find_timer_candidate(None)
        assert result["id"] == "t2"


class TestFormatAlarmSummary:
    """Tests for format_alarm_summary()."""

    def test_with_next_fire(self, handler):
        """Format alarm with next_fire datetime."""
        alarm = {"next_fire": "2025-03-15T07:00:00-05:00", "label": "Morning"}
        result = handler.format_alarm_summary(alarm)
        # Time displayed depends on local timezone; just verify AM format
        assert "AM" in result
        assert "Saturday" in result
        assert "Morning" in result

    def test_without_next_fire(self, handler):
        """Format alarm without next_fire."""
        alarm = {"label": "Morning"}
        result = handler.format_alarm_summary(alarm)
        assert "upcoming alarm" in result
        assert "Morning" in result

    def test_without_label(self, handler):
        """Format alarm without label."""
        alarm = {"next_fire": "2025-03-15T07:00:00-05:00"}
        result = handler.format_alarm_summary(alarm)
        # Time displayed depends on local timezone; just verify AM format
        assert "AM" in result
        assert "." in result


# =============================================================================
# Action Method Tests
# =============================================================================


class TestStopActiveSchedule:
    """Tests for stop_active_schedule()."""

    @pytest.mark.anyio
    async def test_stops_active_alarm(self, handler, mock_schedule_service):
        """Stop active alarm."""
        mock_schedule_service.active_event.return_value = {"id": "alarm-1"}
        result = await handler.stop_active_schedule("stop")
        assert result is True
        mock_schedule_service.stop_event.assert_called_once_with("alarm-1", reason="voice")

    @pytest.mark.anyio
    async def test_stops_active_timer_with_stop(self, handler, mock_schedule_service):
        """Stop active timer with 'stop' command."""
        mock_schedule_service.active_event.side_effect = lambda t: {"id": "timer-1"} if t == "timer" else None
        result = await handler.stop_active_schedule("stop")
        assert result is True
        mock_schedule_service.stop_event.assert_called_once_with("timer-1", reason="voice")

    @pytest.mark.anyio
    async def test_no_active_event(self, handler, mock_schedule_service):
        """Returns False when no active event."""
        mock_schedule_service.active_event.return_value = None
        result = await handler.stop_active_schedule("stop")
        assert result is False


class TestExtendTimerShortcut:
    """Tests for extend_timer_shortcut()."""

    @pytest.mark.anyio
    async def test_extend_timer(self, handler, mock_schedule_service):
        """Extend timer by specified seconds."""
        mock_schedule_service.list_events.return_value = [{"id": "t1", "label": ""}]
        mock_schedule_service.active_event.return_value = {"id": "t1", "label": ""}
        result = await handler.extend_timer_shortcut(300, None)
        assert result is True
        mock_schedule_service.extend_timer.assert_called_once_with("t1", 300)

    @pytest.mark.anyio
    async def test_no_timer_to_extend(self, handler, mock_schedule_service):
        """Returns False when no timer found."""
        mock_schedule_service.list_events.return_value = []
        result = await handler.extend_timer_shortcut(300, None)
        assert result is False


class TestCancelTimerShortcut:
    """Tests for cancel_timer_shortcut()."""

    @pytest.mark.anyio
    async def test_cancel_timer(self, handler, mock_schedule_service):
        """Cancel timer."""
        mock_schedule_service.list_events.return_value = [{"id": "t1", "label": ""}]
        mock_schedule_service.active_event.return_value = {"id": "t1", "label": ""}
        result = await handler.cancel_timer_shortcut(None)
        assert result is True
        mock_schedule_service.stop_event.assert_called_once_with("t1", reason="voice_cancel")

    @pytest.mark.anyio
    async def test_no_timer_to_cancel(self, handler, mock_schedule_service):
        """Returns False when no timer found."""
        mock_schedule_service.list_events.return_value = []
        result = await handler.cancel_timer_shortcut(None)
        assert result is False


# =============================================================================
# Display Method Tests
# =============================================================================


class TestShowAlarmList:
    """Tests for show_alarm_list()."""

    @pytest.mark.anyio
    async def test_no_alarms(self, handler, mock_schedule_service, mock_publisher):
        """Show message when no alarms."""
        mock_schedule_service.list_events.return_value = []
        await handler.show_alarm_list()
        handler._on_speak.assert_called()
        mock_publisher._publish_info_overlay.assert_called()

    @pytest.mark.anyio
    async def test_with_alarms(self, handler, mock_schedule_service, mock_publisher):
        """Show alarm overlay with alarms."""
        mock_schedule_service.list_events.return_value = [
            {"id": "a1", "label": "Morning", "time": "07:00", "status": "scheduled"}
        ]
        await handler.show_alarm_list()
        call_args = mock_publisher._publish_info_overlay.call_args
        assert call_args.kwargs.get("category") == "alarms"


class TestShowReminderList:
    """Tests for show_reminder_list()."""

    @pytest.mark.anyio
    async def test_no_reminders(self, handler, mock_schedule_service, mock_publisher):
        """Show message when no reminders."""
        mock_schedule_service.list_events.return_value = []
        await handler.show_reminder_list()
        handler._on_speak.assert_called()
        mock_publisher._publish_info_overlay.assert_called()

    @pytest.mark.anyio
    async def test_with_reminders(self, handler, mock_schedule_service, mock_publisher):
        """Show reminder overlay with reminders."""
        mock_schedule_service.list_events.return_value = [
            {"id": "r1", "label": "Take pills", "status": "scheduled", "next_fire": "2025-03-15T09:00:00-05:00"}
        ]
        await handler.show_reminder_list()
        call_args = mock_publisher._publish_info_overlay.call_args
        assert call_args.kwargs.get("category") == "reminders"


class TestShowCalendarEvents:
    """Tests for show_calendar_events()."""

    @pytest.mark.anyio
    async def test_calendar_disabled(self, handler, mock_config):
        """Show message when calendar disabled."""
        mock_config.calendar.enabled = False
        await handler.show_calendar_events()
        handler._on_speak.assert_called()

    @pytest.mark.anyio
    async def test_no_events(self, handler, mock_publisher):
        """Show message when no calendar events."""
        handler._calendar_events = []
        await handler.show_calendar_events()
        handler._on_speak.assert_called()
        mock_publisher._publish_info_overlay.assert_called()

    @pytest.mark.anyio
    async def test_with_events(self, handler, mock_publisher):
        """Show calendar overlay with events."""
        handler._calendar_events = [{"id": "e1", "summary": "Meeting", "start": "2025-03-15T10:00:00-05:00"}]
        await handler.show_calendar_events()
        call_args = mock_publisher._publish_info_overlay.call_args
        assert call_args.kwargs.get("category") == "calendar"


# =============================================================================
# Main Dispatcher Tests
# =============================================================================


class TestMaybeHandleScheduleShortcut:
    """Tests for maybe_handle_schedule_shortcut()."""

    @pytest.mark.anyio
    async def test_empty_transcript(self, handler):
        """Returns False for empty transcript."""
        result = await handler.maybe_handle_schedule_shortcut("")
        assert result is False

    @pytest.mark.anyio
    async def test_whitespace_transcript(self, handler):
        """Returns False for whitespace transcript."""
        result = await handler.maybe_handle_schedule_shortcut("   ")
        assert result is False

    @pytest.mark.anyio
    async def test_timer_creation(self, handler, mock_schedule_intents, mock_schedule_service):
        """Handle timer creation command."""
        mock_schedule_intents.extract_timer_start_intent.return_value = (300, None)
        result = await handler.maybe_handle_schedule_shortcut("set timer for 5 minutes")
        assert result is True
        mock_schedule_service.create_timer.assert_called_once_with(duration_seconds=300, label=None)

    @pytest.mark.anyio
    async def test_alarm_creation(self, handler, mock_schedule_intents, mock_schedule_service):
        """Handle alarm creation command."""
        mock_schedule_intents.extract_alarm_start_intent.return_value = ("07:00", None, None)
        result = await handler.maybe_handle_schedule_shortcut("set alarm for 7am")
        assert result is True
        mock_schedule_service.create_alarm.assert_called_once()

    @pytest.mark.anyio
    async def test_unrecognized_passes_through(self, handler, mock_schedule_intents):
        """Unrecognized commands return False."""
        mock_schedule_intents.extract_timer_start_intent.return_value = None
        mock_schedule_intents.extract_alarm_start_intent.return_value = None
        mock_schedule_intents.extract_reminder_intent.return_value = None
        result = await handler.maybe_handle_schedule_shortcut("what is the weather")
        assert result is False

    @pytest.mark.anyio
    async def test_show_alarms_command(self, handler, mock_schedule_intents, mock_schedule_service):
        """Handle 'show my alarms' command."""
        mock_schedule_intents.extract_timer_start_intent.return_value = None
        mock_schedule_intents.extract_alarm_start_intent.return_value = None
        mock_schedule_intents.extract_reminder_intent.return_value = None
        mock_schedule_service.list_events.return_value = []
        result = await handler.maybe_handle_schedule_shortcut("show my alarms")
        assert result is True

    @pytest.mark.anyio
    async def test_cancel_all_timers(self, handler, mock_schedule_intents, mock_schedule_service):
        """Handle 'cancel all timers' command."""
        mock_schedule_intents.extract_timer_start_intent.return_value = None
        mock_schedule_intents.extract_alarm_start_intent.return_value = None
        mock_schedule_intents.extract_reminder_intent.return_value = None
        mock_schedule_service.cancel_all_timers.return_value = 2
        result = await handler.maybe_handle_schedule_shortcut("cancel all timers")
        assert result is True
        mock_schedule_service.cancel_all_timers.assert_called_once()
