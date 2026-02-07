"""MQTT publishing functionality for Pulse Assistant.

This module handles all MQTT message publishing including:
- State updates (assistant stage, pipeline, wake word)
- Info overlays (alerts, lights, health, routines)
- Schedule/calendar state
- Preference states
- Home Assistant MQTT discovery
- Earmuffs control

This is a proof-of-concept extraction to validate the stateless publisher approach.
Full implementation will follow after validation.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import os
from datetime import datetime
from typing import Any

from pulse import __version__
from pulse.assistant.config import AssistantConfig
from pulse.assistant.home_assistant import HomeAssistantClient, HomeAssistantError
from pulse.assistant.mqtt import AssistantMqtt
from pulse.assistant.schedule_service import ScheduleService
from pulse.sound_library import SoundLibrary

LOGGER = logging.getLogger(__name__)


class AssistantMqttPublisher:
    """Manages MQTT publishing for Pulse Assistant.

    This publisher follows a stateless design - it receives all necessary
    state as method parameters rather than maintaining internal state.
    This makes it easier to test and reason about.
    """

    def __init__(
        self,
        mqtt: AssistantMqtt,
        config: AssistantConfig,
        home_assistant: HomeAssistantClient | None,
        schedule_service: ScheduleService,
        sound_library: SoundLibrary,
        logger: logging.Logger | None = None,
    ) -> None:
        """Initialize MQTT publisher with dependencies.

        Args:
            mqtt: MQTT client for publishing messages
            config: Assistant configuration including MQTT topic base
            home_assistant: Home Assistant client (optional)
            schedule_service: Schedule/alarm/timer service
            sound_library: Sound library for managing notification sounds
            logger: Optional logger instance
        """
        self.mqtt = mqtt
        self.config = config
        self.home_assistant = home_assistant
        self.schedule_service = schedule_service
        self.sound_library = sound_library
        self.logger = logger or LOGGER

        # Initialize all topic variables
        base_topic = self.config.mqtt.topic_base
        self._assist_in_progress_topic = f"{base_topic}/assistant/in_progress"
        self._assist_metrics_topic = f"{base_topic}/assistant/metrics"
        self._assist_stage_topic = f"{base_topic}/assistant/stage"
        self._assist_pipeline_topic = f"{base_topic}/assistant/active_pipeline"
        self._assist_wake_topic = f"{base_topic}/assistant/last_wake_word"
        self._preferences_topic = f"{base_topic}/preferences"
        self._schedules_state_topic = f"{base_topic}/schedules/state"
        self._schedule_command_topic = f"{base_topic}/schedules/command"
        self._alarms_active_topic = f"{base_topic}/alarms/active"
        self._timers_active_topic = f"{base_topic}/timers/active"
        self._reminders_active_topic = f"{base_topic}/reminders/active"
        self._info_card_topic = f"{base_topic}/info_card"
        self._heartbeat_topic = f"{base_topic}/assistant/heartbeat"
        self._earmuffs_state_topic = f"{base_topic}/earmuffs/state"

        # Initialize sound options cache (performance optimization)
        self._sound_options: dict[str, list[tuple[str, str]]] = {}
        self._refresh_sound_options()

        # Initialize info overlay clear task tracking
        self._info_overlay_clear_task: asyncio.Task | None = None

    # ========================================================================
    # Static Helper Methods
    # ========================================================================

    @staticmethod
    def _clone_schedule_snapshot(snapshot: dict[str, Any]) -> dict[str, Any] | None:
        """Deep clone a schedule snapshot using JSON serialization.

        Args:
            snapshot: Schedule snapshot dictionary to clone

        Returns:
            Cloned snapshot or None if serialization fails
        """
        try:
            return json.loads(json.dumps(snapshot))
        except TypeError:
            LOGGER.warning("[mqtt_publisher] Unable to serialize schedule snapshot: %s", snapshot)
            return None

    @staticmethod
    def _format_lights_card(lights: list[dict[str, Any]]) -> dict[str, Any] | None:
        """Format light state data into a card for the info overlay.

        Args:
            lights: List of light entity dictionaries from Home Assistant

        Returns:
            Formatted card dictionary or None if no lights
        """
        if not lights:
            return None

        entries: list[dict[str, Any]] = []
        on_count = 0

        for light in lights:
            if not isinstance(light, dict):
                continue

            entity_id = light.get("entity_id")
            attrs = light.get("attributes") or {}
            name = attrs.get("friendly_name") or entity_id or "Light"
            state = str(light.get("state") or "unknown").lower()

            if state == "on":
                on_count += 1

            # Calculate brightness percentage
            brightness = attrs.get("brightness")
            brightness_pct = None
            if isinstance(brightness, (int, float)):
                brightness_pct = int(round(max(0.0, min(1.0, float(brightness) / 255.0)) * 100))

            # Format color temperature
            color_temp_attr = attrs.get("color_temp")
            color_temp_label = None
            if isinstance(color_temp_attr, (int, float)) and color_temp_attr > 0:
                try:
                    kelvin = int(round(1_000_000 / float(color_temp_attr)))
                    color_temp_label = f"{kelvin}K"
                except (TypeError, ValueError):
                    color_temp_label = None

            area = attrs.get("area_id")
            entries.append(
                {
                    "entity_id": entity_id,
                    "name": name,
                    "state": state,
                    "brightness_pct": brightness_pct,
                    "color_temp": color_temp_label,
                    "area": area,
                }
            )

        total = len(entries)
        if total == 0:
            return None

        # Sort: on lights first, then alphabetically by name
        entries.sort(key=lambda item: (item.get("state") != "on", (item.get("name") or "").lower()))

        subtitle_parts = [f"{on_count} on" if on_count else "All off", f"{total} total"]

        return {
            "type": "lights",
            "title": "Lights",
            "subtitle": " • ".join(subtitle_parts),
            "lights": entries[:12],  # Limit to 12 lights for display
        }

    # ========================================================================
    # Sound Management Methods
    # ========================================================================

    def _refresh_sound_options(self) -> None:
        """Build sound option lists for each kind from the sound library.

        Populates the internal _sound_options cache with sound_id and label
        tuples for each sound kind (alarm, timer, reminder, notification).
        """
        built_in = self.sound_library.built_in_sounds()
        custom = self.sound_library.custom_sounds()
        all_sounds = built_in + custom

        for kind in ("alarm", "timer", "reminder", "notification"):
            options: list[tuple[str, str]] = []
            for info in all_sounds:
                if kind in info.kinds:
                    options.append((info.sound_id, info.label))
            # Sort by label for display
            options.sort(key=lambda x: x[1].lower())
            self._sound_options[kind] = options

    def _get_sound_options_for_kind(self, kind: str) -> list[str]:
        """Get list of sound labels for a given kind.

        Args:
            kind: Sound kind (alarm, timer, reminder, notification)

        Returns:
            List of sound labels
        """
        return [label for _, label in self._sound_options.get(kind, [])]

    def _get_sound_id_by_label(self, kind: str, label: str) -> str | None:
        """Look up sound_id from its label.

        Args:
            kind: Sound kind (alarm, timer, reminder, notification)
            label: Display label to look up

        Returns:
            Sound ID or None if not found
        """
        for sound_id, sound_label in self._sound_options.get(kind, []):
            if sound_label == label:
                return sound_id
        return None

    def _get_sound_label_by_id(self, kind: str, sound_id: str) -> str | None:
        """Look up sound label from its ID.

        Args:
            kind: Sound kind (alarm, timer, reminder, notification)
            sound_id: Sound ID to look up

        Returns:
            Sound label or None if not found
        """
        for sid, label in self._sound_options.get(kind, []):
            if sid == sound_id:
                return label
        return None

    def _get_current_sound_id(self, kind: str, config_sound_id: str) -> str:
        """Get the current sound ID, validating it exists in the library.

        Args:
            kind: Sound kind (alarm, timer, reminder, notification)
            config_sound_id: Sound ID from configuration

        Returns:
            Valid sound ID (falls back to first available if invalid)
        """
        # Check if configured sound exists
        if self._get_sound_label_by_id(kind, config_sound_id):
            return config_sound_id

        # Fall back to first available sound for this kind
        options = self._sound_options.get(kind, [])
        if options:
            return options[0][0]

        # Last resort: return the configured ID even if invalid
        return config_sound_id

    # ========================================================================
    # Info Overlay Management Methods
    # ========================================================================

    def _cancel_info_overlay_clear(self) -> None:
        """Cancel any scheduled info overlay clear task."""
        task = self._info_overlay_clear_task
        if task:
            task.cancel()
            self._info_overlay_clear_task = None

    def _schedule_info_overlay_clear(self, delay: float) -> None:
        """Schedule info overlay to clear after a delay.

        Args:
            delay: Delay in seconds before clearing the overlay
        """
        self._cancel_info_overlay_clear()
        if delay <= 0:
            self._publish_info_overlay()
            return

        async def _clear_after() -> None:
            try:
                await asyncio.sleep(delay)
                self._publish_info_overlay()
            except asyncio.CancelledError:
                return

        self._info_overlay_clear_task = asyncio.create_task(_clear_after())

    # ========================================================================
    # Core Publishing Methods
    # ========================================================================

    def _publish_message(self, topic: str, payload: str, *, retain: bool = False) -> None:
        """Publish a message to MQTT broker.

        Args:
            topic: MQTT topic to publish to
            payload: Message payload (usually JSON string)
            retain: Whether to retain the message on the broker
        """
        self.mqtt.publish(topic, payload=payload, retain=retain)

    def _publish_state(self, state: str, extra: dict | None = None, hostname: str | None = None) -> None:
        """Publish assistant state to the state topic.

        Args:
            state: State string (e.g., "idle", "listening", "thinking")
            extra: Optional extra data to include in payload
            hostname: Device hostname (uses config hostname if not provided)
        """
        payload = {"state": state}
        if extra:
            payload.update(extra)
        payload["device"] = hostname or self.config.hostname
        self._publish_message(self.config.state_topic, json.dumps(payload))

    def _publish_info_overlay(
        self,
        text: str | None = None,
        category: str | None = None,
        extra: dict | None = None,
        info_topic: str | None = None,
    ) -> None:
        """Publish info overlay message to display on kiosk.

        Args:
            text: Text to display in overlay
            category: Category of overlay (e.g., "lights", "health", "calendar")
            extra: Optional extra data to include in payload
            info_topic: Optional info topic override (uses default if not provided)
        """
        topic = info_topic or self._info_card_topic
        if not topic:
            return

        payload = dict(extra or {})
        if text and text.strip():
            payload.setdefault("state", "show")
            payload.setdefault("category", category or "")
            payload["text"] = text.strip()
            payload.setdefault("ts", asyncio.get_event_loop().time() if asyncio._get_running_loop() else 0)
        elif payload:
            payload.setdefault("state", "show")
            payload.setdefault("ts", asyncio.get_event_loop().time() if asyncio._get_running_loop() else 0)
            if category:
                payload.setdefault("category", category)
        else:
            payload = {"state": "clear"}

        if payload.get("state") != "clear":
            self._cancel_info_overlay_clear()

        self._publish_message(topic, json.dumps(payload))

    def _publish_preference_state(self, key: str, value: str) -> None:
        """Publish a preference state value.

        Args:
            key: Preference key (e.g., "wake_sound", "speaking_style")
            value: Preference value to publish
        """
        topic = f"{self._preferences_topic}/{key}/state"
        self._publish_message(topic, value, retain=True)

    def _publish_schedule_state(
        self,
        snapshot: dict[str, Any],
        calendar_events: list[dict[str, Any]],
        calendar_updated_at: float | None,
    ) -> None:
        """Publish current schedule and calendar state.

        Args:
            snapshot: Schedule snapshot dictionary
            calendar_events: List of calendar event dictionaries
            calendar_updated_at: Timestamp when calendar was last updated
        """
        payload = copy.deepcopy(snapshot)
        payload["calendar_events"] = [dict(event) for event in calendar_events]

        if calendar_updated_at:
            payload["calendar_updated_at"] = datetime.fromtimestamp(
                calendar_updated_at, tz=datetime.now().astimezone().tzinfo
            ).isoformat()
        else:
            payload.setdefault("calendar_updated_at", None)

        try:
            message = json.dumps(payload)
        except TypeError:
            self.logger.warning("[mqtt_publisher] Unable to serialize schedule snapshot: %s", payload)
            return

        self._publish_message(self._schedules_state_topic, message, retain=True)

    async def _publish_light_overlay(self, home_assistant: HomeAssistantClient | None) -> None:
        """Fetch lights from Home Assistant and publish overlay.

        Args:
            home_assistant: Home Assistant client instance
        """
        if not home_assistant:
            return

        try:
            lights = await home_assistant.list_entities("light")
        except HomeAssistantError as exc:
            self.logger.info("[mqtt_publisher] Failed to fetch Home Assistant lights for overlay: %s", exc)
            return

        payload = self._format_lights_card(lights)
        if not payload:
            return

        self._publish_info_overlay(
            text=payload.get("subtitle") or "Lighting updated.",
            category="lights",
            extra=payload,
        )

    def _publish_routine_overlay(self) -> None:
        """Publish routine overlay (currently suppressed)."""
        return  # Suppress routines overlay (no longer shown)

    def _publish_health_overlay(self) -> None:
        """Publish health overlay (currently suppressed)."""
        return  # Suppress health overlay (no longer shown)

    def _publish_earmuffs_state(self, enabled: bool) -> None:
        """Publish earmuffs state.

        Args:
            enabled: Whether earmuffs are currently enabled
        """
        state = "on" if enabled else "off"
        self._publish_message(self._earmuffs_state_topic, state, retain=True)

    def _publish_preferences(
        self,
        preferences: Any,
        log_llm: bool,
        active_pipeline: str | None,
        active_provider: str,
        config_sounds: Any,
    ) -> None:
        """Publish all preference states.

        Args:
            preferences: PreferencesConfig object
            log_llm: Whether LLM logging is enabled
            active_pipeline: Active Home Assistant pipeline name
            active_provider: Active LLM provider name
            config_sounds: Sound configuration object
        """
        self._publish_preference_state("wake_sound", "on" if preferences.wake_sound else "off")
        self._publish_preference_state("speaking_style", preferences.speaking_style)
        self._publish_preference_state("wake_sensitivity", preferences.wake_sensitivity)
        self._publish_preference_state("ha_response_mode", preferences.ha_response_mode)

        tone_label = self._get_sound_label_by_id("notification", preferences.ha_tone_sound) or preferences.ha_tone_sound
        self._publish_preference_state("ha_tone_sound", tone_label)
        self._publish_preference_state("ha_pipeline", active_pipeline or "")
        self._publish_preference_state("llm_provider", active_provider)
        self._publish_preference_state("log_llm", "on" if log_llm else "off")

        # Sound preferences (publish labels, not sound IDs)
        for kind in ("alarm", "timer", "reminder", "notification"):
            sound_id = getattr(config_sounds, f"{kind}_sound", "")
            if not sound_id:
                continue
            sound_id_validated = self._get_current_sound_id(kind, sound_id)
            label = self._get_sound_label_by_id(kind, sound_id_validated) or sound_id_validated
            self._publish_preference_state(f"sound_{kind}", label)

    def _publish_assistant_discovery(self, hostname: str, device_name: str) -> None:
        """Publish Home Assistant MQTT discovery configurations.

        Args:
            hostname: Device hostname
            device_name: Human-readable device name
        """
        device = {
            "identifiers": [f"pulse:{hostname}"],
            "manufacturer": "Pulse",
            "model": "Pulse Kiosk",
            "name": device_name,
            "sw_version": os.environ.get("PULSE_VERSION") or __version__,
        }

        prefix = "homeassistant"
        hostname_safe = hostname.replace(" ", "_").replace("/", "_")

        # Assist in progress binary sensor
        self._publish_message(
            f"{prefix}/binary_sensor/{hostname_safe}_assist_in_progress/config",
            json.dumps(
                {
                    "name": "Assist In Progress",
                    "unique_id": f"{hostname}-assist-in-progress",
                    "state_topic": self._assist_in_progress_topic,
                    "payload_on": "ON",
                    "payload_off": "OFF",
                    "device": device,
                    "entity_category": "diagnostic",
                }
            ),
            retain=True,
        )

        # Assist stage sensor
        self._publish_message(
            f"{prefix}/sensor/{hostname_safe}_assist_stage/config",
            json.dumps(
                {
                    "name": "Assist Stage",
                    "unique_id": f"{hostname}-assist-stage",
                    "state_topic": self._assist_stage_topic,
                    "device": device,
                    "entity_category": "diagnostic",
                    "icon": "mdi:progress-clock",
                }
            ),
            retain=True,
        )

        # Last wake word sensor
        self._publish_message(
            f"{prefix}/sensor/{hostname_safe}_last_wake_word/config",
            json.dumps(
                {
                    "name": "Last Wake Word",
                    "unique_id": f"{hostname}-last-wake-word",
                    "state_topic": self._assist_wake_topic,
                    "device": device,
                    "entity_category": "diagnostic",
                    "icon": "mdi:account-voice",
                }
            ),
            retain=True,
        )

        # Speaking style select
        self._publish_message(
            f"{prefix}/select/{hostname_safe}_speaking_style/config",
            json.dumps(
                {
                    "name": "Speaking Style",
                    "unique_id": f"{hostname}-speaking-style",
                    "state_topic": f"{self._preferences_topic}/speaking_style/state",
                    "command_topic": f"{self._preferences_topic}/speaking_style/set",
                    "options": ["relaxed", "normal", "aggressive"],
                    "device": device,
                    "entity_category": "config",
                }
            ),
            retain=True,
        )

        # Wake sensitivity select
        self._publish_message(
            f"{prefix}/select/{hostname_safe}_wake_sensitivity/config",
            json.dumps(
                {
                    "name": "Wake Sensitivity",
                    "unique_id": f"{hostname}-wake-sensitivity",
                    "state_topic": f"{self._preferences_topic}/wake_sensitivity/state",
                    "command_topic": f"{self._preferences_topic}/wake_sensitivity/set",
                    "options": ["low", "normal", "high"],
                    "device": device,
                    "entity_category": "config",
                }
            ),
            retain=True,
        )

        # Wake sound switch
        self._publish_message(
            f"{prefix}/switch/{hostname_safe}_wake_sound/config",
            json.dumps(
                {
                    "name": "Wake Sound",
                    "unique_id": f"{hostname}-wake-sound",
                    "state_topic": f"{self._preferences_topic}/wake_sound/state",
                    "command_topic": f"{self._preferences_topic}/wake_sound/set",
                    "payload_on": "on",
                    "payload_off": "off",
                    "device": device,
                    "entity_category": "config",
                }
            ),
            retain=True,
        )

        # Log LLM switch
        self._publish_message(
            f"{prefix}/switch/{hostname_safe}_log_llm/config",
            json.dumps(
                {
                    "name": "Log LLM Responses",
                    "unique_id": f"{hostname}-log-llm",
                    "state_topic": f"{self._preferences_topic}/log_llm/state",
                    "command_topic": f"{self._preferences_topic}/log_llm/set",
                    "payload_on": "on",
                    "payload_off": "off",
                    "device": device,
                    "entity_category": "config",
                }
            ),
            retain=True,
        )

        # Earmuffs switch (disable LLM listening)
        earmuffs_set_topic = f"{self.config.mqtt.topic_base}/earmuffs/set"
        self._publish_message(
            f"{prefix}/switch/{hostname_safe}_earmuffs/config",
            json.dumps(
                {
                    "name": "Earmuffs",
                    "unique_id": f"{hostname}-earmuffs",
                    "state_topic": self._earmuffs_state_topic,
                    "command_topic": earmuffs_set_topic,
                    "payload_on": "on",
                    "payload_off": "off",
                    "device": device,
                    "entity_category": "config",
                    "icon": "mdi:ear-hearing-off",
                }
            ),
            retain=True,
        )

        # HA pipeline text entity
        self._publish_message(
            f"{prefix}/text/{hostname_safe}_ha_pipeline/config",
            json.dumps(
                {
                    "name": "HA Assist Pipeline",
                    "unique_id": f"{hostname}-ha-assist-pipeline",
                    "state_topic": f"{self._preferences_topic}/ha_pipeline/state",
                    "command_topic": f"{self._preferences_topic}/ha_pipeline/set",
                    "device": device,
                    "entity_category": "config",
                }
            ),
            retain=True,
        )

        # LLM provider select
        self._publish_message(
            f"{prefix}/select/{hostname_safe}_llm_provider/config",
            json.dumps(
                {
                    "name": "LLM Provider",
                    "unique_id": f"{hostname}-llm-provider",
                    "state_topic": f"{self._preferences_topic}/llm_provider/state",
                    "command_topic": f"{self._preferences_topic}/llm_provider/set",
                    "options": ["openai", "gemini"],
                    "device": device,
                    "entity_category": "config",
                }
            ),
            retain=True,
        )

        # HA response mode select
        self._publish_message(
            f"{prefix}/select/{hostname_safe}_ha_response_mode/config",
            json.dumps(
                {
                    "name": "HA Response Mode",
                    "unique_id": f"{hostname}-ha-response-mode",
                    "state_topic": f"{self._preferences_topic}/ha_response_mode/state",
                    "command_topic": f"{self._preferences_topic}/ha_response_mode/set",
                    "options": ["none", "tone", "minimal", "full"],
                    "device": device,
                    "entity_category": "config",
                }
            ),
            retain=True,
        )

        tone_options = self._get_sound_options_for_kind("notification")
        if tone_options:
            self._publish_message(
                f"{prefix}/select/{hostname_safe}_ha_tone_sound/config",
                json.dumps(
                    {
                        "name": "HA Tone Sound",
                        "unique_id": f"{hostname}-ha-tone-sound",
                        "state_topic": f"{self._preferences_topic}/ha_tone_sound/state",
                        "command_topic": f"{self._preferences_topic}/ha_tone_sound/set",
                        "options": tone_options,
                        "device": device,
                        "entity_category": "config",
                        "icon": "mdi:volume-medium",
                    }
                ),
                retain=True,
            )

        # Sound preference selects
        sound_configs = [
            ("alarm", "Alarm Sound", "mdi:alarm"),
            ("timer", "Timer Sound", "mdi:timer-outline"),
            ("reminder", "Reminder Sound", "mdi:bell-ring-outline"),
            ("notification", "Notification Sound", "mdi:bell-outline"),
        ]

        for kind, name, icon in sound_configs:
            options = self._get_sound_options_for_kind(kind)
            if not options:
                continue

            self._publish_message(
                f"{prefix}/select/{hostname_safe}_sound_{kind}/config",
                json.dumps(
                    {
                        "name": name,
                        "unique_id": f"{hostname}-sound-{kind}",
                        "state_topic": f"{self._preferences_topic}/sound_{kind}/state",
                        "command_topic": f"{self._preferences_topic}/sound_{kind}/set",
                        "options": options,
                        "device": device,
                        "entity_category": "config",
                        "icon": icon,
                    }
                ),
                retain=True,
            )
