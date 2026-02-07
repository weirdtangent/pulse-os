"""Preference management for Pulse Assistant.

This module handles user preference storage, MQTT command handling,
and sound library integration. It centralizes all preference-related
logic that was previously scattered in pulse-assistant.py.

Preferences managed:
- Wake sound (on/off)
- Speaking style (relaxed/normal/aggressive)
- Wake sensitivity (low/normal/high)
- HA response mode (none/tone/minimal/full)
- HA tone sound selection
- HA pipeline selection
- LLM provider and model selection
- Sound preferences (alarm/timer/reminder/notification)
- LLM logging toggle
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from pulse.assistant.config import AssistantConfig, AssistantPreferences
from pulse.assistant.mqtt import AssistantMqtt
from pulse.config_persist import persist_preference
from pulse.sound_library import SoundKind, SoundLibrary, SoundSettings

if TYPE_CHECKING:
    from pulse.assistant.mqtt_publisher import AssistantMqttPublisher

LOGGER = logging.getLogger(__name__)


class PreferenceManager:
    """Manages user preferences and sound settings for Pulse Assistant.

    This class centralizes:
    - MQTT subscription and command handling for preferences
    - Sound option management (alarm, timer, reminder, notification sounds)
    - Preference state updates and persistence
    - Integration with SoundLibrary for sound lookups
    """

    def __init__(
        self,
        mqtt: AssistantMqtt,
        config: AssistantConfig,
        sound_library: SoundLibrary,
        publisher: AssistantMqttPublisher,
        logger: logging.Logger | None = None,
    ) -> None:
        """Initialize the preference manager.

        Args:
            mqtt: MQTT client for subscriptions
            config: Assistant configuration
            sound_library: Sound library for managing notification sounds
            publisher: MQTT publisher for state updates
            logger: Optional logger instance
        """
        self.mqtt = mqtt
        self.config = config
        self.sound_library = sound_library
        self.publisher = publisher
        self.logger = logger or LOGGER

        # Current preferences (mutable copy from config)
        self.preferences: AssistantPreferences = config.preferences

        # LLM logging state
        self._log_llm_messages: bool = False

        # Override states for runtime changes
        self._ha_pipeline_override: str | None = None
        self._llm_provider_override: str | None = None

        # Model overrides for each provider
        self._openai_model_override: str | None = None
        self._gemini_model_override: str | None = None
        self._anthropic_model_override: str | None = None
        self._groq_model_override: str | None = None
        self._mistral_model_override: str | None = None
        self._openrouter_model_override: str | None = None

        # Sound options cache (sound_id -> label mapping for each kind)
        self._sound_options: dict[str, list[tuple[str, str]]] = {}

        # Initialize topic
        base_topic = config.mqtt.topic_base
        self._preferences_topic = f"{base_topic}/preferences"

        # Callbacks for external components
        self._on_wake_sensitivity_changed: Callable[[], None] | None = None
        self._on_llm_provider_changed: Callable[[], Any] | None = None
        self._on_sound_settings_changed: Callable[[SoundSettings], None] | None = None
        self._on_config_updated: Callable[[AssistantConfig], None] | None = None

        # Build sound options cache
        self._refresh_sound_options()

    # ========================================================================
    # Callbacks
    # ========================================================================

    def set_wake_sensitivity_callback(self, callback: Callable[[], None]) -> None:
        """Set callback to invoke when wake sensitivity changes."""
        self._on_wake_sensitivity_changed = callback

    def set_llm_provider_callback(self, callback: Callable[[], Any]) -> None:
        """Set callback to invoke when the LLM provider changes."""
        self._on_llm_provider_changed = callback

    def set_sound_settings_callback(self, callback: Callable[[SoundSettings], None]) -> None:
        """Set callback to invoke when sound settings change."""
        self._on_sound_settings_changed = callback

    def set_config_updated_callback(self, callback: Callable[[AssistantConfig], None]) -> None:
        """Set callback to invoke when the assistant config changes.

        This keeps external holders of AssistantConfig (e.g., PulseAssistant)
        in sync when runtime preferences such as sound settings are updated.
        """
        self._on_config_updated = callback

    # ========================================================================
    # Sound Management
    # ========================================================================

    def _refresh_sound_options(self) -> None:
        """Build sound option lists for each kind from the sound library."""
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

    def get_sound_options_for_kind(self, kind: str) -> list[str]:
        """Get list of sound labels for a given kind.

        Args:
            kind: Sound kind (alarm, timer, reminder, notification)

        Returns:
            List of sound labels
        """
        return [label for _, label in self._sound_options.get(kind, [])]

    def get_sound_id_by_label(self, kind: str, label: str) -> str | None:
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

    def get_sound_label_by_id(self, kind: str, sound_id: str) -> str | None:
        """Look up label from sound_id.

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

    def get_current_sound_id(self, kind: SoundKind) -> str:
        """Get the current sound_id for a given kind.

        Args:
            kind: Sound kind (alarm, timer, reminder, notification)

        Returns:
            Current sound ID for the kind
        """
        sounds = self.config.sounds
        return {
            "alarm": sounds.default_alarm,
            "timer": sounds.default_timer,
            "reminder": sounds.default_reminder,
            "notification": sounds.default_notification,
        }.get(kind, "")

    def _update_sound_setting(self, kind: SoundKind, sound_id: str) -> None:
        """Update the sound setting for a given kind.

        Args:
            kind: Sound kind (alarm, timer, reminder, notification)
            sound_id: New sound ID to set
        """
        sounds = self.config.sounds
        new_sounds = SoundSettings(
            default_alarm=sound_id if kind == "alarm" else sounds.default_alarm,
            default_timer=sound_id if kind == "timer" else sounds.default_timer,
            default_reminder=sound_id if kind == "reminder" else sounds.default_reminder,
            default_notification=sound_id if kind == "notification" else sounds.default_notification,
            custom_dir=sounds.custom_dir,
        )
        # Update config sounds
        self.config = replace(self.config, sounds=new_sounds)

        # Notify external components
        if self._on_sound_settings_changed:
            self._on_sound_settings_changed(new_sounds)

        # Notify config holder to stay in sync
        if self._on_config_updated:
            self._on_config_updated(self.config)

    # ========================================================================
    # Pipeline and Provider Accessors
    # ========================================================================

    def get_active_ha_pipeline(self) -> str | None:
        """Get the currently active Home Assistant pipeline."""
        return self._ha_pipeline_override or self.config.home_assistant.assist_pipeline

    def get_active_llm_provider(self) -> str:
        """Get the currently active LLM provider."""
        provider = self._llm_provider_override or self.config.llm.provider or "openai"
        return provider.strip().lower() or "openai"

    def get_model_override(self, provider: str) -> str | None:
        """Get the model override for a specific provider."""
        return getattr(self, f"_{provider}_model_override", None)

    @property
    def log_llm_messages(self) -> bool:
        """Whether LLM message logging is enabled."""
        return self._log_llm_messages

    @log_llm_messages.setter
    def log_llm_messages(self, value: bool) -> None:
        """Set LLM message logging state."""
        self._log_llm_messages = value

    # ========================================================================
    # MQTT Subscriptions
    # ========================================================================

    def subscribe_preference_topics(self) -> None:
        """Subscribe to all preference MQTT topics."""
        base = self._preferences_topic
        try:
            self.mqtt.subscribe(f"{base}/wake_sound/set", self._handle_wake_sound_command)
            self.mqtt.subscribe(f"{base}/speaking_style/set", self._handle_speaking_style_command)
            self.mqtt.subscribe(f"{base}/wake_sensitivity/set", self._handle_wake_sensitivity_command)
            self.mqtt.subscribe(f"{base}/ha_response_mode/set", self._handle_ha_response_mode_command)
            self.mqtt.subscribe(f"{base}/ha_tone_sound/set", self._handle_ha_tone_sound_command)
            self.mqtt.subscribe(f"{base}/ha_pipeline/set", self._handle_ha_pipeline_command)
            self.mqtt.subscribe(f"{base}/llm_provider/set", self._handle_llm_provider_command)
            self.mqtt.subscribe(f"{base}/log_llm/set", self._handle_log_llm_command)
            # Model selection for each provider
            self.mqtt.subscribe(f"{base}/openai_model/set", self._handle_openai_model_command)
            self.mqtt.subscribe(f"{base}/gemini_model/set", self._handle_gemini_model_command)
            self.mqtt.subscribe(f"{base}/anthropic_model/set", self._handle_anthropic_model_command)
            self.mqtt.subscribe(f"{base}/groq_model/set", self._handle_groq_model_command)
            self.mqtt.subscribe(f"{base}/mistral_model/set", self._handle_mistral_model_command)
            self.mqtt.subscribe(f"{base}/openrouter_model/set", self._handle_openrouter_model_command)
            # Sound preferences
            self.mqtt.subscribe(f"{base}/sound_alarm/set", self._handle_sound_alarm_command)
            self.mqtt.subscribe(f"{base}/sound_timer/set", self._handle_sound_timer_command)
            self.mqtt.subscribe(f"{base}/sound_reminder/set", self._handle_sound_reminder_command)
            self.mqtt.subscribe(f"{base}/sound_notification/set", self._handle_sound_notification_command)
        except RuntimeError:
            self.logger.debug("[preference_manager] MQTT client not ready for preference subscriptions")

    # ========================================================================
    # Preference Command Handlers
    # ========================================================================

    def _handle_wake_sound_command(self, payload: str) -> None:
        """Handle wake sound toggle command."""
        value = payload.strip().lower()
        enabled = value in {"on", "true", "1", "yes"}
        self.preferences = replace(self.preferences, wake_sound=enabled)
        state = "on" if enabled else "off"
        self.publisher._publish_preference_state("wake_sound", state)
        persist_preference("wake_sound", state, logger=self.logger)

    def _handle_speaking_style_command(self, payload: str) -> None:
        """Handle speaking style selection command."""
        value = payload.strip().lower()
        if value not in {"relaxed", "normal", "aggressive"}:
            self.logger.debug("[preference_manager] Ignoring invalid speaking style: %s", payload)
            return
        self.preferences = replace(self.preferences, speaking_style=value)  # type: ignore[arg-type]
        self.publisher._publish_preference_state("speaking_style", value)
        persist_preference("speaking_style", value, logger=self.logger)

    def _handle_wake_sensitivity_command(self, payload: str) -> None:
        """Handle wake sensitivity selection command."""
        value = payload.strip().lower()
        if value not in {"low", "normal", "high"}:
            self.logger.debug("[preference_manager] Ignoring invalid wake sensitivity: %s", payload)
            return
        if value == self.preferences.wake_sensitivity:
            return
        self.preferences = replace(self.preferences, wake_sensitivity=value)  # type: ignore[arg-type]
        self.publisher._publish_preference_state("wake_sensitivity", value)
        persist_preference("wake_sensitivity", value, logger=self.logger)
        # Notify wake detector
        if self._on_wake_sensitivity_changed:
            self._on_wake_sensitivity_changed()

    def _handle_log_llm_command(self, payload: str) -> None:
        """Handle LLM logging toggle command."""
        value = payload.strip().lower()
        enabled = value in {"on", "true", "1", "yes"}
        if self._log_llm_messages == enabled:
            return
        self._log_llm_messages = enabled
        state = "on" if enabled else "off"
        self.publisher._publish_preference_state("log_llm", state)
        persist_preference("log_llm", state, logger=self.logger)

    def _handle_ha_response_mode_command(self, payload: str) -> None:
        """Handle HA response mode selection command."""
        value = payload.strip().lower()
        if value not in {"none", "tone", "minimal", "full"}:
            self.logger.debug("[preference_manager] Ignoring invalid HA response mode: %s", payload)
            return
        if value == self.preferences.ha_response_mode:
            return
        self.preferences = replace(self.preferences, ha_response_mode=value)  # type: ignore[arg-type]
        self.publisher._publish_preference_state("ha_response_mode", value)
        persist_preference("ha_response_mode", value, logger=self.logger)

    def _handle_ha_tone_sound_command(self, payload: str) -> None:
        """Handle HA tone sound selection command."""
        label = payload.strip()
        if not label:
            return
        sound_id = self.get_sound_id_by_label("notification", label) or label
        if sound_id == self.preferences.ha_tone_sound:
            return
        self.preferences = replace(self.preferences, ha_tone_sound=sound_id)
        label_or_id = self.get_sound_label_by_id("notification", sound_id) or label
        self.publisher._publish_preference_state("ha_tone_sound", label_or_id)
        persist_preference("ha_tone_sound", sound_id, logger=self.logger)

    def _handle_ha_pipeline_command(self, payload: str) -> None:
        """Handle HA pipeline selection command."""
        value = payload.strip()
        self._ha_pipeline_override = value or None
        pipeline_value = self.get_active_ha_pipeline() or ""
        self.publisher._publish_preference_state("ha_pipeline", pipeline_value)
        persist_preference("ha_pipeline", pipeline_value, logger=self.logger)

    def _handle_llm_provider_command(self, payload: str) -> None:
        """Handle LLM provider selection command."""
        # Import here to avoid circular dependency
        from pulse.assistant.llm import get_supported_providers

        value = payload.strip().lower()
        if not value:
            self._llm_provider_override = None
        elif value in get_supported_providers():
            self._llm_provider_override = value
        else:
            supported = ", ".join(get_supported_providers().keys())
            self.logger.warning("[preference_manager] Invalid LLM provider '%s'. Supported: %s", payload, supported)
            return

        # Notify that LLM provider changed (caller should rebuild provider)
        if self._on_llm_provider_changed:
            self._on_llm_provider_changed()

        provider = self.get_active_llm_provider()
        self.publisher._publish_preference_state("llm_provider", provider)
        persist_preference("llm_provider", provider, logger=self.logger)

    def _handle_model_command(self, provider: str, payload: str) -> None:
        """Generic handler for model selection commands across all providers.

        Args:
            provider: Provider name (e.g., "openai", "anthropic")
            payload: Model name from MQTT command
        """
        value = payload.strip()
        override_attr = f"_{provider}_model_override"
        config_attr = f"{provider}_model"

        # Update the model override for this provider
        setattr(self, override_attr, value if value else None)

        # Rebuild LLM provider if this provider is currently active
        if self.get_active_llm_provider() == provider:
            if self._on_llm_provider_changed:
                self._on_llm_provider_changed()

        # Publish state and persist preference
        # When clearing an override, persist the default model (not empty string)
        # to avoid config disagreement on restart
        default_model = getattr(self.config.llm, config_attr)
        effective_model = value or default_model
        self.publisher._publish_preference_state(config_attr, effective_model)
        persist_preference(config_attr, effective_model, logger=self.logger)

    def _handle_openai_model_command(self, payload: str) -> None:
        self._handle_model_command("openai", payload)

    def _handle_gemini_model_command(self, payload: str) -> None:
        self._handle_model_command("gemini", payload)

    def _handle_anthropic_model_command(self, payload: str) -> None:
        self._handle_model_command("anthropic", payload)

    def _handle_groq_model_command(self, payload: str) -> None:
        self._handle_model_command("groq", payload)

    def _handle_mistral_model_command(self, payload: str) -> None:
        self._handle_model_command("mistral", payload)

    def _handle_openrouter_model_command(self, payload: str) -> None:
        self._handle_model_command("openrouter", payload)

    # ========================================================================
    # Sound Preference Handlers
    # ========================================================================

    def _handle_sound_command(self, kind: SoundKind, payload: str) -> None:
        """Handle a sound preference command for a given kind.

        Args:
            kind: Sound kind (alarm, timer, reminder, notification)
            payload: Sound label from MQTT command
        """
        label = payload.strip()
        if not label:
            return
        sound_id = self.get_sound_id_by_label(kind, label)
        if sound_id is None:
            self.logger.debug("[preference_manager] Ignoring unknown %s sound: '%s'", kind, label)
            return
        current_id = self.get_current_sound_id(kind)
        if sound_id == current_id:
            return
        # Update sound settings
        self._update_sound_setting(kind, sound_id)
        self.publisher._publish_preference_state(f"sound_{kind}", label)
        persist_preference(f"sound_{kind}", sound_id, logger=self.logger)
        self.logger.info("[preference_manager] Set %s sound to '%s' (%s)", kind, label, sound_id)

    def _handle_sound_alarm_command(self, payload: str) -> None:
        self._handle_sound_command("alarm", payload)

    def _handle_sound_timer_command(self, payload: str) -> None:
        self._handle_sound_command("timer", payload)

    def _handle_sound_reminder_command(self, payload: str) -> None:
        self._handle_sound_command("reminder", payload)

    def _handle_sound_notification_command(self, payload: str) -> None:
        self._handle_sound_command("notification", payload)
