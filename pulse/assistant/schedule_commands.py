"""Schedule command processor for MQTT commands.

This module handles MQTT schedule commands and routes them to the ScheduleService.
It provides payload parsing, validation, and state change callbacks for schedule events.

Note: Voice shortcut processing is handled by ScheduleShortcutHandler (Phase 4).
Intent parsing is handled by ScheduleIntentParser (Phase 3).
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING, Any

from pulse.datetime_utils import parse_datetime, parse_duration_seconds

from .schedule_service import PlaybackConfig, parse_day_tokens

if TYPE_CHECKING:
    from .mqtt_publisher import AssistantMqttPublisher
    from .schedule_service import ScheduleService

LOGGER = logging.getLogger(__name__)


class ScheduleCommandProcessor:
    """Processes MQTT schedule commands and routes them to ScheduleService.

    This class handles:
    - MQTT command message parsing and validation
    - Routing commands to appropriate ScheduleService methods
    - State change callbacks for schedule events
    - Publishing active event notifications

    Supported command actions:
    - Alarms: create_alarm, add_alarm, update_alarm, delete_alarm, pause_alarm,
              resume_alarm, play_alarm, snooze, next_alarm, pause_day, resume_day,
              unpause_day, enable_day, disable_day
    - Timers: start_timer, create_timer, extend_timer, add_time, cancel_all, delete_timer
    - Reminders: create_reminder, add_reminder, delete_reminder, complete_reminder,
                 finish_reminder, delay_reminder
    - General: stop, cancel, delete
    """

    def __init__(
        self,
        schedule_service: ScheduleService,
        publisher: AssistantMqttPublisher,
        base_topic: str,
        logger: logging.Logger | None = None,
    ) -> None:
        """Initialize schedule command processor.

        Args:
            schedule_service: Service for timer/alarm/reminder CRUD operations
            publisher: MQTT publisher for state updates
            base_topic: Base MQTT topic for schedule commands
            logger: Optional logger instance
        """
        self.schedule_service = schedule_service
        self.publisher = publisher
        self.logger = logger or LOGGER

        # MQTT topics
        self._schedules_state_topic = f"{base_topic}/schedules/state"
        self._schedule_command_topic = f"{base_topic}/schedules/command"
        self._alarms_active_topic = f"{base_topic}/alarms/active"
        self._timers_active_topic = f"{base_topic}/timers/active"
        self._reminders_active_topic = f"{base_topic}/reminders/active"

        # External dependencies (set via setters)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._latest_schedule_snapshot: dict[str, Any] = {}
        self._calendar_events: list[dict[str, Any]] = []
        self._calendar_updated_at: float | None = None

        # Callbacks for external coordination
        self._on_log_activity: Callable[[str, dict[str, Any]], Coroutine[Any, Any, None]] | None = None

    # ========================================================================
    # Configuration
    # ========================================================================

    def set_event_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Set the event loop for async command processing."""
        self._loop = loop

    def set_log_activity_callback(
        self,
        callback: Callable[[str, dict[str, Any]], Coroutine[Any, Any, None]],
    ) -> None:
        """Set callback to log activity events.

        Args:
            callback: Function(event_type, event_payload) -> Coroutine
        """
        self._on_log_activity = callback

    def update_calendar_state(
        self,
        events: list[dict[str, Any]],
        updated_at: float | None,
    ) -> None:
        """Update calendar events for state publishing.

        Args:
            events: List of calendar event dicts
            updated_at: Timestamp of last calendar update (time.time())
        """
        self._calendar_events = events
        self._calendar_updated_at = updated_at

    @property
    def command_topic(self) -> str:
        """MQTT topic for schedule commands."""
        return self._schedule_command_topic

    # ========================================================================
    # MQTT Message Handler
    # ========================================================================

    def handle_command_message(self, payload: str) -> None:
        """Handle incoming MQTT schedule command message.

        This is the MQTT callback - it parses JSON and dispatches to async processing.

        Args:
            payload: Raw JSON payload from MQTT
        """
        if not self._loop:
            return
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            self.logger.debug("[schedule_commands] Ignoring malformed command: %s", payload)
            return
        asyncio.run_coroutine_threadsafe(self._process_command(data), self._loop)

    # ========================================================================
    # State Change Callbacks (for ScheduleService)
    # ========================================================================

    def handle_state_changed(self, snapshot: dict[str, Any]) -> None:
        """Handle schedule state change from ScheduleService.

        Args:
            snapshot: Current schedule state snapshot
        """
        cloned = self.publisher._clone_schedule_snapshot(snapshot)
        if cloned is None:
            return
        self._latest_schedule_snapshot = cloned
        self.publisher._publish_schedule_state(
            cloned,
            self._calendar_events,
            self._calendar_updated_at,
        )

    def handle_active_event(self, event_type: str, payload: dict[str, Any] | None) -> None:
        """Handle active schedule event notification from ScheduleService.

        Routes notifications to appropriate MQTT topics and logs ringing events.

        Args:
            event_type: Type of event (alarm, timer, reminder)
            payload: Event payload or None for idle state
        """
        if event_type == "alarm":
            topic = self._alarms_active_topic
        elif event_type == "timer":
            topic = self._timers_active_topic
        else:
            topic = self._reminders_active_topic

        message = payload or {"state": "idle"}
        self.publisher._publish_message(topic, json.dumps(message))

        # Log ringing events to activity log
        if payload and payload.get("state") == "ringing":
            event_payload = payload.get("event") or {}
            if self._loop is None:
                self.logger.error("[schedule_commands] Cannot log activity: event loop not initialized")
                return
            if self._on_log_activity:
                self._loop.create_task(self._on_log_activity(event_type, event_payload))

    # ========================================================================
    # Command Processing
    # ========================================================================

    async def _process_command(self, payload: dict[str, Any]) -> None:
        """Process a schedule command payload.

        Args:
            payload: Parsed command payload dict
        """
        if not isinstance(payload, dict):
            return
        action = str(payload.get("action") or "").lower()
        if not action:
            return

        try:
            await self._dispatch_action(action, payload)
        except Exception as exc:
            self.logger.debug("[schedule_commands] Command %s failed: %s", action, exc)

    async def _dispatch_action(self, action: str, payload: dict[str, Any]) -> None:
        """Dispatch action to appropriate handler.

        Args:
            action: Lowercased action name
            payload: Full command payload
        """
        # Alarm actions
        if action in {"create_alarm", "add_alarm"}:
            await self._create_alarm(payload)
        elif action == "update_alarm":
            await self._update_alarm(payload)
        elif action in {"delete_alarm", "delete_timer", "delete"}:
            await self._delete_event(payload)
        elif action == "pause_alarm":
            await self._pause_alarm(payload)
        elif action in {"resume_alarm", "play_alarm"}:
            await self._resume_alarm(payload)
        elif action == "snooze":
            await self._snooze_alarm(payload)
        elif action == "next_alarm":
            self._publish_next_alarm()

        # Day-level controls
        elif action == "pause_day":
            await self._pause_day(payload)
        elif action in {"resume_day", "unpause_day"}:
            await self._resume_day(payload)
        elif action == "enable_day":
            await self._enable_day(payload)
        elif action == "disable_day":
            await self._disable_day(payload)

        # Timer actions
        elif action in {"start_timer", "create_timer"}:
            await self._create_timer(payload)
        elif action in {"add_time", "extend_timer"}:
            await self._extend_timer(payload)
        elif action == "cancel_all":
            await self._cancel_all(payload)

        # Stop/cancel actions
        elif action in {"stop", "cancel"}:
            await self._stop_event(payload)

        # Reminder actions
        elif action in {"create_reminder", "add_reminder"}:
            await self._create_reminder(payload)
        elif action == "delete_reminder":
            await self._delete_event(payload)
        elif action in {"complete_reminder", "finish_reminder"}:
            await self._complete_reminder(payload)
        elif action == "delay_reminder":
            await self._delay_reminder(payload)

    # ========================================================================
    # Alarm Handlers
    # ========================================================================

    async def _create_alarm(self, payload: dict[str, Any]) -> None:
        """Create a new alarm."""
        time_text = payload.get("time") or payload.get("time_of_day")
        if not time_text:
            raise ValueError("alarm time is required")
        days = self._coerce_day_list(payload.get("days"))
        playback = self._playback_from_payload(payload.get("playback"))
        single_flag = payload.get("single_shot")
        single_shot = bool(single_flag) if single_flag is not None else None
        await self.schedule_service.create_alarm(
            time_of_day=str(time_text),
            label=payload.get("label"),
            days=days,
            playback=playback,
            single_shot=single_shot,
        )

    async def _update_alarm(self, payload: dict[str, Any]) -> None:
        """Update an existing alarm."""
        event_id = payload.get("event_id")
        if not event_id:
            raise ValueError("event_id is required to update an alarm")
        days = self._coerce_day_list(payload.get("days")) if "days" in payload else None
        playback = self._playback_from_payload(payload.get("playback")) if "playback" in payload else None
        await self.schedule_service.update_alarm(
            str(event_id),
            time_of_day=payload.get("time") or payload.get("time_of_day"),
            days=days,
            label=payload.get("label"),
            playback=playback,
        )

    async def _pause_alarm(self, payload: dict[str, Any]) -> None:
        """Pause an alarm."""
        event_id = payload.get("event_id")
        if event_id:
            await self.schedule_service.pause_alarm(str(event_id))

    async def _resume_alarm(self, payload: dict[str, Any]) -> None:
        """Resume a paused alarm."""
        event_id = payload.get("event_id")
        if event_id:
            await self.schedule_service.resume_alarm(str(event_id))

    async def _snooze_alarm(self, payload: dict[str, Any]) -> None:
        """Snooze an active alarm."""
        event_id = payload.get("event_id")
        try:
            minutes = int(payload.get("minutes", 5))
        except (ValueError, TypeError):
            minutes = 5
        if event_id:
            await self.schedule_service.snooze_alarm(str(event_id), minutes=max(1, minutes))

    def _publish_next_alarm(self) -> None:
        """Publish next alarm information."""
        info = self.schedule_service.get_next_alarm()
        response = {"next_alarm": info}
        self.publisher._publish_message(
            f"{self._schedules_state_topic}/next_alarm",
            json.dumps(response),
        )

    # ========================================================================
    # Day-Level Control Handlers
    # ========================================================================

    async def _pause_day(self, payload: dict[str, Any]) -> None:
        """Pause all alarms for a specific day."""
        date_str = str(payload.get("date") or "").strip()
        if not date_str:
            raise ValueError("date is required for pause_day")
        await self.schedule_service.set_ui_pause_date(date_str, True)

    async def _resume_day(self, payload: dict[str, Any]) -> None:
        """Resume alarms for a specific day."""
        date_str = str(payload.get("date") or "").strip()
        if not date_str:
            raise ValueError("date is required for resume_day")
        await self.schedule_service.set_ui_pause_date(date_str, False)

    async def _enable_day(self, payload: dict[str, Any]) -> None:
        """Enable a specific alarm for a specific day."""
        date_str = str(payload.get("date") or "").strip()
        alarm_id = str(payload.get("alarm_id") or "").strip()
        if not date_str or not alarm_id:
            raise ValueError("date and alarm_id are required for enable_day")
        await self.schedule_service.set_ui_enable_date(date_str, alarm_id, True)

    async def _disable_day(self, payload: dict[str, Any]) -> None:
        """Disable a specific alarm for a specific day."""
        date_str = str(payload.get("date") or "").strip()
        alarm_id = str(payload.get("alarm_id") or "").strip()
        if not date_str or not alarm_id:
            raise ValueError("date and alarm_id are required for disable_day")
        await self.schedule_service.set_ui_enable_date(date_str, alarm_id, False)

    # ========================================================================
    # Timer Handlers
    # ========================================================================

    async def _create_timer(self, payload: dict[str, Any]) -> None:
        """Create a new timer."""
        seconds = self._coerce_duration_seconds(payload.get("duration") or payload.get("seconds"))
        playback = self._playback_from_payload(payload.get("playback"))
        await self.schedule_service.create_timer(
            duration_seconds=seconds,
            label=payload.get("label"),
            playback=playback,
        )

    async def _extend_timer(self, payload: dict[str, Any]) -> None:
        """Extend an active timer."""
        event_id = payload.get("event_id")
        seconds = self._coerce_duration_seconds(payload.get("seconds") or payload.get("duration"))
        if event_id:
            await self.schedule_service.extend_timer(str(event_id), int(seconds))

    async def _cancel_all(self, payload: dict[str, Any]) -> None:
        """Cancel all events of a specific type."""
        event_type = (payload.get("event_type") or "timer").lower()
        if event_type == "timer":
            await self.schedule_service.cancel_all_timers()

    # ========================================================================
    # Stop/Delete Handlers
    # ========================================================================

    async def _stop_event(self, payload: dict[str, Any]) -> None:
        """Stop an active event."""
        event_id = payload.get("event_id")
        if event_id:
            await self.schedule_service.stop_event(str(event_id), reason="mqtt_stop")

    async def _delete_event(self, payload: dict[str, Any]) -> None:
        """Delete an event."""
        event_id = payload.get("event_id")
        if event_id:
            await self.schedule_service.delete_event(str(event_id))

    # ========================================================================
    # Reminder Handlers
    # ========================================================================

    async def _create_reminder(self, payload: dict[str, Any]) -> None:
        """Create a new reminder."""
        message = payload.get("message") or payload.get("text")
        when_text = payload.get("when") or payload.get("time")
        if not message or not when_text:
            raise ValueError("reminder message and time are required")
        fire_time = parse_datetime(str(when_text))
        if fire_time is None:
            raise ValueError("reminder time is invalid")
        repeat_rule = payload.get("repeat") if isinstance(payload.get("repeat"), dict) else None
        await self.schedule_service.create_reminder(
            fire_time=fire_time,
            message=str(message),
            repeat=repeat_rule,
        )

    async def _complete_reminder(self, payload: dict[str, Any]) -> None:
        """Mark a reminder as complete."""
        event_id = payload.get("event_id")
        if event_id:
            await self.schedule_service.stop_event(str(event_id), reason="complete")

    async def _delay_reminder(self, payload: dict[str, Any]) -> None:
        """Delay a reminder."""
        event_id = payload.get("event_id")
        raw_seconds = payload.get("seconds") or payload.get("duration")
        if raw_seconds in (None, "", 0, "0"):
            return
        try:
            seconds = self._coerce_duration_seconds(raw_seconds)
        except ValueError:
            LOGGER.warning("Invalid delay_reminder duration %r; ignoring", raw_seconds)
            return
        if event_id and seconds > 0:
            await self.schedule_service.delay_reminder(str(event_id), int(seconds))

    # ========================================================================
    # Payload Parsing Utilities
    # ========================================================================

    @staticmethod
    def _playback_from_payload(payload: Any) -> PlaybackConfig:
        """Parse playback configuration from command payload.

        Args:
            payload: Playback configuration dict or string

        Returns:
            PlaybackConfig instance
        """
        if not isinstance(payload, dict):
            if str(payload or "").lower() == "music":
                return PlaybackConfig(mode="music")
            return PlaybackConfig()
        mode = (payload.get("mode") or payload.get("type") or "beep").lower()
        sound_id = payload.get("sound") or payload.get("sound_id")
        if mode != "music":
            return PlaybackConfig(sound_id=sound_id)
        return PlaybackConfig(
            mode="music",
            music_entity=payload.get("entity") or payload.get("music_entity"),
            music_source=payload.get("source") or payload.get("media_content_id"),
            media_content_type=payload.get("media_content_type") or payload.get("content_type"),
            provider=payload.get("provider"),
            description=payload.get("description") or payload.get("name"),
            sound_id=sound_id,
        )

    @staticmethod
    def _coerce_duration_seconds(raw_value: Any) -> float:
        """Convert duration value to positive float seconds.

        Args:
            raw_value: Duration as int, float, or string (e.g., "PT5M", "10s")

        Returns:
            Duration in seconds

        Raises:
            ValueError: If duration is None or not positive
        """
        if raw_value is None:
            raise ValueError("duration is required")
        if isinstance(raw_value, int | float):
            seconds = float(raw_value)
        else:
            seconds = parse_duration_seconds(str(raw_value))
        if seconds <= 0:
            raise ValueError("duration must be positive")
        return seconds

    @staticmethod
    def _coerce_day_list(value: Any) -> list[int] | None:
        """Convert day specification to list of weekday integers.

        Args:
            value: Day specification as list or comma-separated string

        Returns:
            List of integers (0=Monday, 6=Sunday) or None
        """
        if value is None:
            return None
        if isinstance(value, list):
            tokens = ",".join(str(item) for item in value)
            return parse_day_tokens(tokens)
        return parse_day_tokens(str(value))
