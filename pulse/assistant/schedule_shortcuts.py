"""Schedule shortcut handler for voice commands.

This module handles voice shortcuts for timers, alarms, reminders, and calendar
display. It processes natural language schedule commands from voice input and
coordinates with ScheduleService for execution.

Note: MQTT command processing is handled separately by ScheduleCommandProcessor (Phase 5).
Calendar state management is handled by CalendarManager (Phase 6).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import TYPE_CHECKING, Any

from pulse.assistant.schedule_intents import ScheduleIntentParser

if TYPE_CHECKING:
    from pulse.assistant.config import AssistantConfig
    from pulse.assistant.mqtt_publisher import AssistantMqttPublisher
    from pulse.assistant.schedule_service import ScheduleService

LOGGER = logging.getLogger(__name__)

CALENDAR_EVENT_INFO_LIMIT = 25


class ScheduleShortcutHandler:
    """Handles voice shortcuts for timers, alarms, reminders, and calendar.

    This class processes natural language schedule commands from voice input
    and coordinates with ScheduleService for execution. It provides spoken
    feedback via callbacks and publishes UI overlays via the publisher.
    """

    def __init__(
        self,
        schedule_service: ScheduleService,
        schedule_intents: ScheduleIntentParser,
        publisher: AssistantMqttPublisher,
        config: AssistantConfig,
        logger: logging.Logger | None = None,
    ) -> None:
        """Initialize schedule shortcut handler.

        Args:
            schedule_service: Service for timer/alarm/reminder CRUD operations
            schedule_intents: Parser for extracting intents from natural language
            publisher: MQTT publisher for overlay and state updates
            config: Assistant configuration for calendar settings
            logger: Optional logger instance
        """
        self.schedule_service = schedule_service
        self.schedule_intents = schedule_intents
        self.publisher = publisher
        self.config = config
        self.logger = logger or LOGGER

        # Callbacks for external coordination
        self._on_speak: Callable[[str], Awaitable[None]] | None = None
        self._on_log_response: Callable[[str, str, str], None] | None = None

        # Calendar events (passed in, managed by CalendarManager in Phase 6)
        self._calendar_events: list[dict[str, Any]] = []

    # ========================================================================
    # Callback Configuration
    # ========================================================================

    def set_speak_callback(self, callback: Callable[[str], Awaitable[None]]) -> None:
        """Set callback to speak text to user (async)."""
        self._on_speak = callback

    def set_log_response_callback(
        self,
        callback: Callable[[str, str, str], None],
    ) -> None:
        """Set callback to log assistant responses.

        Args:
            callback: Function(tag, text, pipeline) -> None
        """
        self._on_log_response = callback

    def set_calendar_events(self, events: list[dict[str, Any]]) -> None:
        """Update calendar events list (called by CalendarManager)."""
        self._calendar_events = events

    # ========================================================================
    # Static Detection Helpers
    # ========================================================================

    @staticmethod
    def is_stop_phrase(lowered: str) -> bool:
        """Check if text is a stop/cancel command."""
        stop_phrases = {
            "stop",
            "stop it",
            "stop alarm",
            "stop the alarm",
            "turn off the alarm",
            "cancel the alarm",
            "stop the timer",
        }
        if lowered in stop_phrases:
            return True
        alarm_stop_pattern = r"\b(cancel|stop|turn off)\b.*\balarm\b"
        timer_stop_pattern = r"\b(cancel|stop|turn off)\b.*\btimer\b"
        if re.search(alarm_stop_pattern, lowered):
            return True
        if re.search(timer_stop_pattern, lowered):
            return True
        return False

    @staticmethod
    def mentions_alarm_cancel(text: str) -> bool:
        """Check if text mentions canceling an alarm."""
        if "alarm" not in text:
            return False
        cancel_words = ("cancel", "delete", "remove", "clear", "turn off")
        return any(word in text for word in cancel_words)

    @staticmethod
    def extract_timer_label(lowered: str) -> str | None:
        """Extract timer label from command text."""
        match = re.search(r"timer (?:for|named)\s+([a-z0-9 ]+)", lowered)
        if match:
            return match.group(1).strip()
        match = re.search(r"for ([a-z0-9 ]+) timer", lowered)
        if match:
            return match.group(1).strip()
        return None

    # ========================================================================
    # Static Formatting Helpers
    # ========================================================================

    @staticmethod
    def format_timer_label(duration_seconds: Any) -> str:
        """Format timer duration for display (e.g., '5m 30s')."""
        if not isinstance(duration_seconds, (int, float)):
            return "Timer"
        seconds = max(0, int(duration_seconds))
        if seconds < 60:
            return f"{seconds}s"
        minutes, seconds = divmod(seconds, 60)
        if minutes < 60:
            if seconds == 0:
                return f"{minutes}m"
            return f"{minutes}m {seconds}s"
        hours, minutes = divmod(minutes, 60)
        if minutes == 0:
            return f"{hours}h"
        return f"{hours}h {minutes}m"

    @staticmethod
    def format_reminder_meta(reminder: dict[str, Any]) -> str:
        """Format reminder metadata for display."""
        next_fire = reminder.get("next_fire")
        try:
            dt = datetime.fromisoformat(next_fire).astimezone()
            time_phrase = dt.strftime("%-I:%M %p")
            date_phrase = dt.strftime("%b %-d")
            base = f"{date_phrase} · {time_phrase}"
        except (TypeError, ValueError):
            base = "—"
        repeat = ((reminder.get("metadata") or {}).get("reminder") or {}).get("repeat")
        if repeat:
            repeat_type = repeat.get("type")
            if repeat_type == "weekly":
                days = repeat.get("days") or []
                if sorted(days) == list(range(7)):
                    base = f"{base} · Daily"
                else:
                    names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
                    labels = ", ".join(names[day % 7] for day in days)
                    base = f"{base} · {labels}"
            elif repeat_type == "monthly":
                day = repeat.get("day")
                if isinstance(day, int):
                    base = f"{base} · {ScheduleIntentParser._ordinal(day)} monthly"
                else:
                    base = f"{base} · Monthly"
            elif repeat_type == "interval":
                months = repeat.get("interval_months")
                days = repeat.get("interval_days")
                if months:
                    base = f"{base} · Every {months} mo"
                elif days:
                    base = f"{base} · Every {days} d"
        return base

    # ========================================================================
    # Lookup Helpers
    # ========================================================================

    def find_alarm_candidate(
        self,
        time_of_day: str | None,
        label: str | None,
    ) -> dict[str, Any] | None:
        """Find alarm matching time and/or label."""
        alarms = self.schedule_service.list_events("alarm")
        if not alarms:
            return None
        label_lower = label.lower() if label else None
        matches: list[dict[str, Any]] = []
        for alarm in alarms:
            event_time = alarm.get("time")
            if time_of_day and event_time != time_of_day:
                continue
            event_label = (alarm.get("label") or "").lower()
            if label_lower and (not event_label or label_lower not in event_label):
                continue
            matches.append(alarm)
        if not matches:
            return None
        return matches[0]

    def find_timer_candidate(self, label: str | None) -> dict[str, Any] | None:
        """Find timer by label or return active/only timer."""
        timers = self.schedule_service.list_events("timer")
        if not timers:
            return None
        if label:
            wanted = label.lower()
            for timer in timers:
                current_label = (timer.get("label") or "").lower()
                if current_label and wanted in current_label:
                    return timer
        active = self.schedule_service.active_event("timer")
        if active:
            if not label:
                return active
            current_label = (active.get("label") or "").lower()
            if current_label and label.lower() in current_label:
                return active
        if len(timers) == 1 and not label:
            return timers[0]
        return None

    def format_alarm_summary(self, alarm: dict[str, Any]) -> str:
        """Format alarm info for speech output."""
        next_fire = alarm.get("next_fire")
        label = alarm.get("label")
        try:
            dt = datetime.fromisoformat(next_fire) if next_fire else None
        except (TypeError, ValueError):
            dt = None
        if dt:
            dt = dt.astimezone()
            time_str = dt.strftime("%-I:%M %p")
            if dt.minute == 0:
                # Drop ":00" for cleaner TTS output on o'clock times.
                time_str = dt.strftime("%-I %p")
            day = dt.strftime("%A")
            base = f"Your next alarm is set for {time_str} on {day}"
        else:
            base = "You have an upcoming alarm"
        if label:
            base = f"{base} ({label})"
        return f"{base}."

    # ========================================================================
    # Stop/Cancel Operations
    # ========================================================================

    async def stop_active_schedule(self, lowered: str) -> bool:
        """Stop currently firing alarm or timer."""
        alarm = self.schedule_service.active_event("alarm")
        if alarm:
            await self.schedule_service.stop_event(alarm["id"], reason="voice")
            return True
        timer = self.schedule_service.active_event("timer")
        if timer and ("timer" in lowered or lowered in {"stop", "stop it"}):
            await self.schedule_service.stop_event(timer["id"], reason="voice")
            return True
        return False

    async def cancel_alarm_shortcut(
        self,
        alarm_intent: tuple[str, list[int] | None, str | None] | None,
    ) -> bool:
        """Cancel alarm matching the parsed intent."""
        if not self.schedule_service or not alarm_intent:
            return False
        time_of_day, _, label = alarm_intent
        target = self.find_alarm_candidate(time_of_day, label)
        if not target:
            return False
        await self.schedule_service.delete_event(target["id"])
        return True

    async def cancel_timer_shortcut(self, label: str | None) -> bool:
        """Cancel timer by optional label."""
        timer = self.find_timer_candidate(label)
        if not timer:
            return False
        await self.schedule_service.stop_event(timer["id"], reason="voice_cancel")
        return True

    async def extend_timer_shortcut(self, seconds: int, label: str | None) -> bool:
        """Add time to a timer."""
        timer = self.find_timer_candidate(label)
        if not timer:
            return False
        await self.schedule_service.extend_timer(timer["id"], seconds)
        return True

    # ========================================================================
    # Display Methods
    # ========================================================================

    async def show_alarm_list(self) -> None:
        """Show alarms overlay and speak summary."""
        if not self.schedule_service:
            spoken = "I can't access your alarms right now."
            if self._on_speak:
                await self._on_speak(spoken)
            if self._on_log_response:
                self._on_log_response("shortcut", spoken, "pulse")
            return
        alarms = self.schedule_service.list_events("alarm")
        if not alarms:
            spoken = "You do not have any alarms scheduled."
            if self._on_speak:
                await self._on_speak(spoken)
            if self._on_log_response:
                self._on_log_response("shortcut", spoken, "pulse")
            self.publisher._publish_info_overlay()
            return
        alarm_payload = []
        for alarm in alarms:
            alarm_id = alarm.get("id")
            if not alarm_id:
                continue
            alarm_payload.append(
                {
                    "id": alarm_id,
                    "label": alarm.get("label") or "Alarm",
                    "time": alarm.get("time") or alarm.get("time_of_day"),
                    "time_of_day": alarm.get("time_of_day"),
                    "repeat_days": alarm.get("repeat_days"),
                    "days": alarm.get("days"),
                    "status": alarm.get("status"),
                    "next_fire": alarm.get("next_fire"),
                }
            )
        self.publisher._publish_info_overlay(
            text="Use ⏸️ to pause, ▶️ to resume, or 🗑️ to delete an alarm.",
            category="alarms",
            extra={"type": "alarms", "title": "Alarms", "alarms": alarm_payload},
        )
        count = len(alarms)
        spoken = f"You have {count} alarm{'s' if count != 1 else ''}."
        if self._on_speak:
            await self._on_speak("Here are your alarms.")
        if self._on_log_response:
            self._on_log_response("shortcut", spoken, "pulse")

    async def show_reminder_list(self) -> None:
        """Show reminders overlay and speak summary."""
        if not self.schedule_service:
            spoken = "I can't access your reminders right now."
            if self._on_speak:
                await self._on_speak(spoken)
            if self._on_log_response:
                self._on_log_response("shortcut", spoken, "pulse")
            return
        reminders = self.schedule_service.list_events("reminder")
        if not reminders:
            spoken = "You do not have any reminders scheduled."
            if self._on_speak:
                await self._on_speak(spoken)
            if self._on_log_response:
                self._on_log_response("shortcut", spoken, "pulse")
            self.publisher._publish_info_overlay()
            return
        reminder_payload = []
        for reminder in reminders:
            reminder_id = reminder.get("id")
            if not reminder_id:
                continue
            reminder_payload.append(
                {
                    "id": reminder_id,
                    "label": reminder.get("label") or "Reminder",
                    "meta": self.format_reminder_meta(reminder),
                    "status": reminder.get("status"),
                }
            )
        self.publisher._publish_info_overlay(
            text="Tap Complete when you're done or choose a delay.",
            category="reminders",
            extra={"type": "reminders", "title": "Reminders", "reminders": reminder_payload},
        )
        count = len(reminders)
        spoken = f"You have {count} reminder{'s' if count != 1 else ''}."
        if self._on_speak:
            await self._on_speak("Here are your reminders.")
        if self._on_log_response:
            self._on_log_response("shortcut", spoken, "pulse")

    async def show_calendar_events(self) -> None:
        """Show calendar overlay and speak summary."""
        if not self.config.calendar.enabled:
            spoken = "Calendar syncing is not enabled on this device."
            if self._on_speak:
                await self._on_speak(spoken)
            if self._on_log_response:
                self._on_log_response("shortcut", spoken, "pulse")
            return
        events = self._calendar_events[:CALENDAR_EVENT_INFO_LIMIT]
        lookahead = self.config.calendar.lookahead_hours
        if not events:
            spoken = f"You don't have any calendar events in the next {lookahead} hours."
            if self._on_speak:
                await self._on_speak(spoken)
            if self._on_log_response:
                self._on_log_response("shortcut", spoken, "pulse")
            self.publisher._publish_info_overlay()
            return
        subtitle = f"Upcoming events in the next {lookahead} hours."
        self.publisher._publish_info_overlay(
            text=subtitle,
            category="calendar",
            extra={
                "type": "calendar",
                "title": "Calendar",
                "events": events,
                "lookahead_hours": lookahead,
            },
        )
        count = len(self._calendar_events)
        spoken = f"You have {count} calendar event{'s' if count != 1 else ''} coming up."
        if self._on_speak:
            await self._on_speak("Here are your upcoming events.")
        if self._on_log_response:
            self._on_log_response("shortcut", spoken, "pulse")

    # ========================================================================
    # Main Dispatcher
    # ========================================================================

    async def maybe_handle_schedule_shortcut(self, transcript: str) -> bool:
        """Check if transcript is a schedule shortcut and handle it.

        Args:
            transcript: Raw voice transcript

        Returns:
            True if handled as shortcut, False to pass to LLM
        """
        if not transcript or not transcript.strip():
            return False
        if not self.schedule_service:
            return False
        lowered = transcript.strip().lower()
        normalized = re.sub(r"[^\w\s:]", " ", lowered)
        normalized = re.sub(r"\b([ap])\s+m\b", r"\1m", normalized)
        normalized = re.sub(r"^(?:hey|ok|okay)\s+(?:jarvis|pulse)\s+", "", normalized)
        normalized = re.sub(r"^(?:jarvis|pulse)\s+", "", normalized)
        normalized = re.sub(r"\s+", " ", normalized).strip()

        alarm_intent = self.schedule_intents.extract_alarm_start_intent(normalized)

        # Handle alarm cancellation
        if self.mentions_alarm_cancel(normalized):
            handled = await self.stop_active_schedule(normalized)
            if handled:
                return True
            if await self.cancel_alarm_shortcut(alarm_intent):
                spoken = "Alarm cancelled."
                if self._on_log_response:
                    self._on_log_response("shortcut", spoken, "pulse")
                if self._on_speak:
                    await self._on_speak(spoken)
                return True
            return False

        # Handle timer creation
        timer_start = self.schedule_intents.extract_timer_start_intent(normalized)
        if timer_start:
            duration, label = timer_start
            await self.schedule_service.create_timer(duration_seconds=duration, label=label)
            phrase = self.schedule_intents.describe_duration(duration)
            spoken = f"Starting a timer for {phrase}."
            if self._on_log_response:
                self._on_log_response("shortcut", spoken, "pulse")
            if self._on_speak:
                await self._on_speak(spoken)
            return True

        # Handle reminder creation
        reminder_intent = self.schedule_intents.extract_reminder_intent(normalized, transcript, self.schedule_service)
        if reminder_intent:
            event = await self.schedule_service.create_reminder(
                fire_time=reminder_intent.fire_time,
                message=reminder_intent.message,
                repeat=reminder_intent.repeat_rule,
            )
            spoken = self.schedule_intents.format_reminder_confirmation(event)
            if self._on_log_response:
                self._on_log_response("shortcut", spoken, "pulse")
            if self._on_speak:
                await self._on_speak(spoken)
            return True

        # Handle alarm creation
        if alarm_intent:
            time_of_day, days, label = alarm_intent
            await self.schedule_service.create_alarm(time_of_day=time_of_day, days=days, label=label)
            spoken = self.schedule_intents.format_alarm_confirmation(time_of_day, days, label)
            if self._on_log_response:
                self._on_log_response("shortcut", spoken, "pulse")
            if self._on_speak:
                await self._on_speak(spoken)
            return True

        # Handle "next alarm" query
        if "next alarm" in normalized or normalized.startswith("when is my alarm"):
            info = self.schedule_service.get_next_alarm()
            if info:
                message = self.format_alarm_summary(info)
            else:
                message = "You do not have any alarms scheduled."
            if self._on_log_response:
                self._on_log_response("shortcut", message, "pulse")
            if self._on_speak:
                await self._on_speak(message)
            return True

        # Handle "show alarms" command
        if any(
            phrase in normalized
            for phrase in (
                "show me my alarms",
                "show my alarms",
                "show alarms",
                "list my alarms",
                "list alarms",
                "what alarms do i have",
                "what are my alarms",
            )
        ):
            await self.show_alarm_list()
            return True

        # Handle "show reminders" command
        if any(
            phrase in normalized
            for phrase in (
                "show me my reminders",
                "show my reminders",
                "show reminders",
                "list my reminders",
                "list reminders",
                "what reminders do i have",
                "what are my reminders",
            )
        ):
            await self.show_reminder_list()
            return True

        # Handle "show calendar" command
        if any(
            phrase in normalized
            for phrase in (
                "show me my calendar",
                "show my calendar",
                "show calendar events",
                "show my calendar events",
                "show upcoming events",
                "show my upcoming events",
                "list my calendar",
                "list calendar events",
                "what are my calendar events",
                "what are my calendar",
                "what calendar events",
                "what are my upcoming events",
                "what upcoming events",
                "tell me about my calendar",
                "tell me my calendar events",
                "what is on my calendar",
                "what events are coming up",
                "what is coming up on my calendar",
            )
        ):
            await self.show_calendar_events()
            return True

        # Handle "cancel all timers"
        if "cancel all timers" in normalized:
            count = await self.schedule_service.cancel_all_timers()
            if count > 0:
                spoken = f"Cancelled {count} timer{'s' if count != 1 else ''}."
            else:
                spoken = "You do not have any timers running."
            if self._on_log_response:
                self._on_log_response("shortcut", spoken, "pulse")
            if self._on_speak:
                await self._on_speak(spoken)
            return True

        # Handle generic stop command
        if self.is_stop_phrase(normalized):
            handled = await self.stop_active_schedule(normalized)
            if handled:
                return True

        # Handle timer extend ("add X minutes")
        add_match = re.search(r"(add|plus)\s+(\d+)\s*(minute|min|minutes|mins)", normalized)
        if add_match:
            minutes = int(add_match.group(2))
            seconds = minutes * 60
            label = self.extract_timer_label(normalized)
            if await self.extend_timer_shortcut(seconds, label):
                label_text = f" to the {label} timer" if label else ""
                spoken = f"Added {minutes} minutes{label_text}."
                if self._on_log_response:
                    self._on_log_response("shortcut", spoken, "pulse")
                if self._on_speak:
                    await self._on_speak(spoken)
                return True

        # Handle timer cancellation
        if "cancel my timer" in normalized or "cancel the timer" in normalized:
            label = self.extract_timer_label(normalized)
            if await self.cancel_timer_shortcut(label):
                spoken = "Timer cancelled."
                if self._on_log_response:
                    self._on_log_response("shortcut", spoken, "pulse")
                if self._on_speak:
                    await self._on_speak(spoken)
                return True

        return False
