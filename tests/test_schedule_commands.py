"""Tests for ScheduleCommandProcessor."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, Mock

import pytest
from pulse.assistant.schedule_commands import ScheduleCommandProcessor


@pytest.fixture
def mock_schedule_service():
    """Create mock schedule service."""
    service = Mock()
    service.create_alarm = AsyncMock()
    service.update_alarm = AsyncMock()
    service.delete_event = AsyncMock()
    service.pause_alarm = AsyncMock()
    service.resume_alarm = AsyncMock()
    service.snooze_alarm = AsyncMock()
    service.get_next_alarm = Mock(return_value=None)
    service.set_ui_pause_date = AsyncMock()
    service.set_ui_enable_date = AsyncMock()
    service.create_timer = AsyncMock()
    service.extend_timer = AsyncMock()
    service.cancel_all_timers = AsyncMock()
    service.stop_event = AsyncMock()
    service.create_reminder = AsyncMock()
    service.delay_reminder = AsyncMock()
    return service


@pytest.fixture
def mock_publisher():
    """Create mock MQTT publisher."""
    publisher = Mock()
    publisher._publish_message = Mock()
    publisher._clone_schedule_snapshot = Mock(return_value={"alarms": [], "timers": [], "reminders": []})
    publisher._publish_schedule_state = Mock()
    return publisher


@pytest.fixture
def processor(mock_schedule_service, mock_publisher):
    """Create processor with mocked dependencies."""
    proc = ScheduleCommandProcessor(
        schedule_service=mock_schedule_service,
        publisher=mock_publisher,
        base_topic="pulse/test",
    )
    # Set up event loop for testing
    loop = asyncio.new_event_loop()
    proc.set_event_loop(loop)
    yield proc
    loop.close()


# =============================================================================
# Payload Parsing Utilities Tests
# =============================================================================


class TestPlaybackFromPayload:
    """Tests for _playback_from_payload() static method."""

    def test_none_payload_returns_default(self):
        result = ScheduleCommandProcessor._playback_from_payload(None)
        assert result.mode == "beep"
        assert result.sound_id is None

    def test_empty_string_returns_default(self):
        result = ScheduleCommandProcessor._playback_from_payload("")
        assert result.mode == "beep"

    def test_music_string_returns_music_mode(self):
        result = ScheduleCommandProcessor._playback_from_payload("music")
        assert result.mode == "music"

    def test_music_string_case_insensitive(self):
        result = ScheduleCommandProcessor._playback_from_payload("MUSIC")
        assert result.mode == "music"

    def test_dict_with_beep_mode(self):
        result = ScheduleCommandProcessor._playback_from_payload({"mode": "beep", "sound_id": "chime"})
        assert result.mode == "beep"
        assert result.sound_id == "chime"

    def test_dict_with_sound_key(self):
        result = ScheduleCommandProcessor._playback_from_payload({"sound": "bell"})
        assert result.sound_id == "bell"

    def test_dict_with_music_mode(self):
        result = ScheduleCommandProcessor._playback_from_payload(
            {
                "mode": "music",
                "entity": "media_player.bedroom",
                "source": "spotify:playlist:123",
                "media_content_type": "music",
                "provider": "spotify",
                "description": "Morning Playlist",
            }
        )
        assert result.mode == "music"
        assert result.music_entity == "media_player.bedroom"
        assert result.music_source == "spotify:playlist:123"
        assert result.media_content_type == "music"
        assert result.provider == "spotify"
        assert result.description == "Morning Playlist"

    def test_dict_with_type_instead_of_mode(self):
        result = ScheduleCommandProcessor._playback_from_payload({"type": "music"})
        assert result.mode == "music"

    def test_dict_with_alternative_keys(self):
        result = ScheduleCommandProcessor._playback_from_payload(
            {
                "mode": "music",
                "music_entity": "media_player.kitchen",
                "media_content_id": "spotify:album:456",
                "content_type": "album",
                "name": "Jazz Album",
            }
        )
        assert result.music_entity == "media_player.kitchen"
        assert result.music_source == "spotify:album:456"
        assert result.media_content_type == "album"
        assert result.description == "Jazz Album"


class TestCoerceDurationSeconds:
    """Tests for _coerce_duration_seconds() static method."""

    def test_integer_value(self):
        result = ScheduleCommandProcessor._coerce_duration_seconds(300)
        assert result == 300.0

    def test_float_value(self):
        result = ScheduleCommandProcessor._coerce_duration_seconds(90.5)
        assert result == 90.5

    def test_minutes_suffix_string(self):
        result = ScheduleCommandProcessor._coerce_duration_seconds("5m")
        assert result == 300.0

    def test_simple_seconds_string(self):
        result = ScheduleCommandProcessor._coerce_duration_seconds("30s")
        assert result == 30.0

    def test_none_raises_value_error(self):
        with pytest.raises(ValueError, match="duration is required"):
            ScheduleCommandProcessor._coerce_duration_seconds(None)

    def test_zero_raises_value_error(self):
        with pytest.raises(ValueError, match="duration must be positive"):
            ScheduleCommandProcessor._coerce_duration_seconds(0)

    def test_negative_raises_value_error(self):
        with pytest.raises(ValueError, match="duration must be positive"):
            ScheduleCommandProcessor._coerce_duration_seconds(-10)


class TestCoerceDayList:
    """Tests for _coerce_day_list() static method."""

    def test_none_returns_none(self):
        result = ScheduleCommandProcessor._coerce_day_list(None)
        assert result is None

    def test_string_weekdays(self):
        result = ScheduleCommandProcessor._coerce_day_list("mon,tue,wed")
        assert result is not None
        assert 0 in result  # Monday
        assert 1 in result  # Tuesday
        assert 2 in result  # Wednesday

    def test_list_of_strings(self):
        result = ScheduleCommandProcessor._coerce_day_list(["monday", "friday"])
        assert result is not None
        assert 0 in result  # Monday
        assert 4 in result  # Friday


# =============================================================================
# Command Processing Tests
# =============================================================================


class TestHandleCommandMessage:
    """Tests for handle_command_message() method."""

    def test_malformed_json_ignored(self, processor):
        """Malformed JSON should be silently ignored."""
        processor.handle_command_message("not valid json")
        # Should not raise, just log and return

    def test_empty_action_ignored(self, processor, mock_schedule_service):
        """Commands without action should be ignored."""
        processor.handle_command_message(json.dumps({"time": "7:00"}))
        # Give time for async processing
        processor._loop.run_until_complete(asyncio.sleep(0.1))
        mock_schedule_service.create_alarm.assert_not_called()


class TestAlarmCommands:
    """Tests for alarm command processing."""

    @pytest.mark.anyio
    async def test_create_alarm(self, processor, mock_schedule_service):
        """Test create_alarm command."""
        await processor._process_command(
            {
                "action": "create_alarm",
                "time": "7:00",
                "label": "Wake up",
                "days": "mon,tue,wed,thu,fri",
            }
        )
        mock_schedule_service.create_alarm.assert_called_once()
        call_kwargs = mock_schedule_service.create_alarm.call_args.kwargs
        assert call_kwargs["time_of_day"] == "7:00"
        assert call_kwargs["label"] == "Wake up"
        assert call_kwargs["days"] is not None

    @pytest.mark.anyio
    async def test_add_alarm_alias(self, processor, mock_schedule_service):
        """Test add_alarm as alias for create_alarm."""
        await processor._process_command(
            {
                "action": "add_alarm",
                "time_of_day": "6:30",
            }
        )
        mock_schedule_service.create_alarm.assert_called_once()

    @pytest.mark.anyio
    async def test_create_alarm_without_time_raises(self, processor, mock_schedule_service):
        """Test create_alarm without time logs error but doesn't crash."""
        await processor._process_command(
            {
                "action": "create_alarm",
                "label": "No time",
            }
        )
        mock_schedule_service.create_alarm.assert_not_called()

    @pytest.mark.anyio
    async def test_update_alarm(self, processor, mock_schedule_service):
        """Test update_alarm command."""
        await processor._process_command(
            {
                "action": "update_alarm",
                "event_id": "alarm-123",
                "time": "8:00",
                "label": "New label",
            }
        )
        mock_schedule_service.update_alarm.assert_called_once()
        args = mock_schedule_service.update_alarm.call_args
        assert args[0][0] == "alarm-123"

    @pytest.mark.anyio
    async def test_delete_alarm(self, processor, mock_schedule_service):
        """Test delete_alarm command."""
        await processor._process_command(
            {
                "action": "delete_alarm",
                "event_id": "alarm-456",
            }
        )
        mock_schedule_service.delete_event.assert_called_once_with("alarm-456")

    @pytest.mark.anyio
    async def test_pause_alarm(self, processor, mock_schedule_service):
        """Test pause_alarm command."""
        await processor._process_command(
            {
                "action": "pause_alarm",
                "event_id": "alarm-789",
            }
        )
        mock_schedule_service.pause_alarm.assert_called_once_with("alarm-789")

    @pytest.mark.anyio
    async def test_resume_alarm(self, processor, mock_schedule_service):
        """Test resume_alarm command."""
        await processor._process_command(
            {
                "action": "resume_alarm",
                "event_id": "alarm-789",
            }
        )
        mock_schedule_service.resume_alarm.assert_called_once_with("alarm-789")

    @pytest.mark.anyio
    async def test_play_alarm_alias(self, processor, mock_schedule_service):
        """Test play_alarm as alias for resume_alarm."""
        await processor._process_command(
            {
                "action": "play_alarm",
                "event_id": "alarm-789",
            }
        )
        mock_schedule_service.resume_alarm.assert_called_once()

    @pytest.mark.anyio
    async def test_snooze(self, processor, mock_schedule_service):
        """Test snooze command."""
        await processor._process_command(
            {
                "action": "snooze",
                "event_id": "alarm-123",
                "minutes": 10,
            }
        )
        mock_schedule_service.snooze_alarm.assert_called_once_with("alarm-123", minutes=10)

    @pytest.mark.anyio
    async def test_snooze_default_minutes(self, processor, mock_schedule_service):
        """Test snooze with default 5 minutes."""
        await processor._process_command(
            {
                "action": "snooze",
                "event_id": "alarm-123",
            }
        )
        mock_schedule_service.snooze_alarm.assert_called_once_with("alarm-123", minutes=5)

    @pytest.mark.anyio
    async def test_next_alarm(self, processor, mock_schedule_service, mock_publisher):
        """Test next_alarm command publishes result."""
        mock_schedule_service.get_next_alarm.return_value = {"time": "7:00"}
        await processor._process_command({"action": "next_alarm"})
        mock_publisher._publish_message.assert_called_once()
        call_args = mock_publisher._publish_message.call_args
        assert "next_alarm" in call_args[0][0]


class TestDayLevelControls:
    """Tests for day-level alarm controls."""

    @pytest.mark.anyio
    async def test_pause_day(self, processor, mock_schedule_service):
        """Test pause_day command."""
        await processor._process_command(
            {
                "action": "pause_day",
                "date": "2024-01-15",
            }
        )
        mock_schedule_service.set_ui_pause_date.assert_called_once_with("2024-01-15", True)

    @pytest.mark.anyio
    async def test_resume_day(self, processor, mock_schedule_service):
        """Test resume_day command."""
        await processor._process_command(
            {
                "action": "resume_day",
                "date": "2024-01-15",
            }
        )
        mock_schedule_service.set_ui_pause_date.assert_called_once_with("2024-01-15", False)

    @pytest.mark.anyio
    async def test_unpause_day_alias(self, processor, mock_schedule_service):
        """Test unpause_day as alias for resume_day."""
        await processor._process_command(
            {
                "action": "unpause_day",
                "date": "2024-01-15",
            }
        )
        mock_schedule_service.set_ui_pause_date.assert_called_once_with("2024-01-15", False)

    @pytest.mark.anyio
    async def test_enable_day(self, processor, mock_schedule_service):
        """Test enable_day command."""
        await processor._process_command(
            {
                "action": "enable_day",
                "date": "2024-01-15",
                "alarm_id": "alarm-123",
            }
        )
        mock_schedule_service.set_ui_enable_date.assert_called_once_with("2024-01-15", "alarm-123", True)

    @pytest.mark.anyio
    async def test_disable_day(self, processor, mock_schedule_service):
        """Test disable_day command."""
        await processor._process_command(
            {
                "action": "disable_day",
                "date": "2024-01-15",
                "alarm_id": "alarm-123",
            }
        )
        mock_schedule_service.set_ui_enable_date.assert_called_once_with("2024-01-15", "alarm-123", False)


class TestTimerCommands:
    """Tests for timer command processing."""

    @pytest.mark.anyio
    async def test_create_timer(self, processor, mock_schedule_service):
        """Test create_timer command."""
        await processor._process_command(
            {
                "action": "create_timer",
                "duration": 300,
                "label": "Cooking",
            }
        )
        mock_schedule_service.create_timer.assert_called_once()
        call_kwargs = mock_schedule_service.create_timer.call_args.kwargs
        assert call_kwargs["duration_seconds"] == 300.0
        assert call_kwargs["label"] == "Cooking"

    @pytest.mark.anyio
    async def test_start_timer_alias(self, processor, mock_schedule_service):
        """Test start_timer as alias for create_timer."""
        await processor._process_command(
            {
                "action": "start_timer",
                "seconds": 60,
            }
        )
        mock_schedule_service.create_timer.assert_called_once()

    @pytest.mark.anyio
    async def test_extend_timer(self, processor, mock_schedule_service):
        """Test extend_timer command."""
        await processor._process_command(
            {
                "action": "extend_timer",
                "event_id": "timer-123",
                "seconds": 60,
            }
        )
        mock_schedule_service.extend_timer.assert_called_once_with("timer-123", 60)

    @pytest.mark.anyio
    async def test_add_time_alias(self, processor, mock_schedule_service):
        """Test add_time as alias for extend_timer."""
        await processor._process_command(
            {
                "action": "add_time",
                "event_id": "timer-123",
                "duration": 120,
            }
        )
        mock_schedule_service.extend_timer.assert_called_once()

    @pytest.mark.anyio
    async def test_cancel_all_timers(self, processor, mock_schedule_service):
        """Test cancel_all command for timers."""
        await processor._process_command(
            {
                "action": "cancel_all",
                "event_type": "timer",
            }
        )
        mock_schedule_service.cancel_all_timers.assert_called_once()

    @pytest.mark.anyio
    async def test_cancel_all_defaults_to_timer(self, processor, mock_schedule_service):
        """Test cancel_all defaults to timer event type."""
        await processor._process_command({"action": "cancel_all"})
        mock_schedule_service.cancel_all_timers.assert_called_once()


class TestStopCancelCommands:
    """Tests for stop/cancel commands."""

    @pytest.mark.anyio
    async def test_stop_event(self, processor, mock_schedule_service):
        """Test stop command."""
        await processor._process_command(
            {
                "action": "stop",
                "event_id": "timer-123",
            }
        )
        mock_schedule_service.stop_event.assert_called_once_with("timer-123", reason="mqtt_stop")

    @pytest.mark.anyio
    async def test_cancel_event(self, processor, mock_schedule_service):
        """Test cancel as alias for stop."""
        await processor._process_command(
            {
                "action": "cancel",
                "event_id": "alarm-456",
            }
        )
        mock_schedule_service.stop_event.assert_called_once_with("alarm-456", reason="mqtt_stop")

    @pytest.mark.anyio
    async def test_delete_command(self, processor, mock_schedule_service):
        """Test delete command."""
        await processor._process_command(
            {
                "action": "delete",
                "event_id": "reminder-789",
            }
        )
        mock_schedule_service.delete_event.assert_called_once_with("reminder-789")

    @pytest.mark.anyio
    async def test_delete_timer(self, processor, mock_schedule_service):
        """Test delete_timer command."""
        await processor._process_command(
            {
                "action": "delete_timer",
                "event_id": "timer-123",
            }
        )
        mock_schedule_service.delete_event.assert_called_once_with("timer-123")


class TestReminderCommands:
    """Tests for reminder command processing."""

    @pytest.mark.anyio
    async def test_create_reminder(self, processor, mock_schedule_service):
        """Test create_reminder command."""
        future_time = (datetime.now() + timedelta(hours=1)).isoformat()
        await processor._process_command(
            {
                "action": "create_reminder",
                "message": "Take medication",
                "when": future_time,
            }
        )
        mock_schedule_service.create_reminder.assert_called_once()

    @pytest.mark.anyio
    async def test_add_reminder_alias(self, processor, mock_schedule_service):
        """Test add_reminder as alias for create_reminder."""
        future_time = (datetime.now() + timedelta(hours=1)).isoformat()
        await processor._process_command(
            {
                "action": "add_reminder",
                "text": "Call mom",
                "time": future_time,
            }
        )
        mock_schedule_service.create_reminder.assert_called_once()

    @pytest.mark.anyio
    async def test_delete_reminder(self, processor, mock_schedule_service):
        """Test delete_reminder command."""
        await processor._process_command(
            {
                "action": "delete_reminder",
                "event_id": "reminder-123",
            }
        )
        mock_schedule_service.delete_event.assert_called_once_with("reminder-123")

    @pytest.mark.anyio
    async def test_complete_reminder(self, processor, mock_schedule_service):
        """Test complete_reminder command."""
        await processor._process_command(
            {
                "action": "complete_reminder",
                "event_id": "reminder-123",
            }
        )
        mock_schedule_service.stop_event.assert_called_once_with("reminder-123", reason="complete")

    @pytest.mark.anyio
    async def test_finish_reminder_alias(self, processor, mock_schedule_service):
        """Test finish_reminder as alias for complete_reminder."""
        await processor._process_command(
            {
                "action": "finish_reminder",
                "event_id": "reminder-123",
            }
        )
        mock_schedule_service.stop_event.assert_called_once_with("reminder-123", reason="complete")

    @pytest.mark.anyio
    async def test_delay_reminder(self, processor, mock_schedule_service):
        """Test delay_reminder command."""
        await processor._process_command(
            {
                "action": "delay_reminder",
                "event_id": "reminder-123",
                "seconds": 600,
            }
        )
        mock_schedule_service.delay_reminder.assert_called_once_with("reminder-123", 600)


# =============================================================================
# State Change Callback Tests
# =============================================================================


class TestStateChangeCallbacks:
    """Tests for schedule state change callbacks."""

    def test_handle_state_changed(self, processor, mock_publisher):
        """Test handle_state_changed delegates to publisher."""
        snapshot = {"alarms": [{"id": "1"}], "timers": [], "reminders": []}
        processor.handle_state_changed(snapshot)
        mock_publisher._clone_schedule_snapshot.assert_called_once_with(snapshot)
        mock_publisher._publish_schedule_state.assert_called_once()

    def test_handle_state_changed_with_none_snapshot(self, processor, mock_publisher):
        """Test handle_state_changed with None cloned snapshot."""
        mock_publisher._clone_schedule_snapshot.return_value = None
        processor.handle_state_changed({})
        mock_publisher._publish_schedule_state.assert_not_called()

    def test_handle_active_event_alarm(self, processor, mock_publisher):
        """Test handle_active_event for alarm."""
        payload = {"state": "ringing", "event": {"id": "alarm-1"}}
        processor.handle_active_event("alarm", payload)
        mock_publisher._publish_message.assert_called_once()
        call_args = mock_publisher._publish_message.call_args
        assert "alarms/active" in call_args[0][0]

    def test_handle_active_event_timer(self, processor, mock_publisher):
        """Test handle_active_event for timer."""
        payload = {"state": "ringing", "event": {"id": "timer-1"}}
        processor.handle_active_event("timer", payload)
        call_args = mock_publisher._publish_message.call_args
        assert "timers/active" in call_args[0][0]

    def test_handle_active_event_reminder(self, processor, mock_publisher):
        """Test handle_active_event for reminder."""
        payload = {"state": "ringing", "event": {"id": "reminder-1"}}
        processor.handle_active_event("reminder", payload)
        call_args = mock_publisher._publish_message.call_args
        assert "reminders/active" in call_args[0][0]

    def test_handle_active_event_idle(self, processor, mock_publisher):
        """Test handle_active_event with None payload (idle state)."""
        processor.handle_active_event("timer", None)
        call_args = mock_publisher._publish_message.call_args
        published_message = json.loads(call_args[0][1])
        assert published_message == {"state": "idle"}


# =============================================================================
# Configuration Tests
# =============================================================================


class TestConfiguration:
    """Tests for processor configuration."""

    def test_command_topic_property(self, processor):
        """Test command_topic property returns correct topic."""
        assert processor.command_topic == "pulse/test/schedules/command"

    def test_update_calendar_state(self, processor):
        """Test update_calendar_state stores calendar info."""
        events = [{"summary": "Meeting", "start": "2024-01-15T10:00:00"}]
        processor.update_calendar_state(events, 1705312800.0)
        assert processor._calendar_events == events
        assert processor._calendar_updated_at == 1705312800.0

    def test_set_log_activity_callback(self, processor):
        """Test set_log_activity_callback stores callback."""
        callback = AsyncMock()
        processor.set_log_activity_callback(callback)
        assert processor._on_log_activity == callback
