"""Tests for PreferenceManager (pulse/assistant/preference_manager.py).

Tests for preference handling logic including:
- MQTT command handlers for wake sound, speaking style, wake sensitivity
- Model override handling and persistence
- Sound preference commands
- Callback wiring
"""

from __future__ import annotations

import logging
from dataclasses import replace
from unittest.mock import Mock, patch

import pytest
from pulse.assistant.config import (
    AssistantConfig,
    AssistantPreferences,
    LLMConfig,
    MqttConfig,
)
from pulse.assistant.mqtt import AssistantMqtt
from pulse.assistant.mqtt_publisher import AssistantMqttPublisher
from pulse.assistant.preference_manager import PreferenceManager
from pulse.sound_library import SoundLibrary, SoundSettings

# Fixtures


@pytest.fixture
def mqtt_config():
    """Basic MQTT configuration for testing."""
    return MqttConfig(
        host="localhost",
        port=1883,
        topic_base="pulse/test-device",
        username=None,
        password=None,
        tls_enabled=False,
        ca_cert=None,
        cert=None,
        key=None,
    )


@pytest.fixture
def llm_config():
    """LLM configuration for testing."""
    config = Mock(spec=LLMConfig)
    config.provider = "openai"
    config.openai_model = "gpt-4o"
    config.gemini_model = "gemini-pro"
    config.anthropic_model = "claude-3-5-sonnet"
    config.groq_model = "llama-3.1-70b"
    config.mistral_model = "mistral-large"
    config.openrouter_model = "anthropic/claude-3.5-sonnet"
    return config


@pytest.fixture
def assistant_config(mqtt_config, llm_config):
    """Full assistant configuration for testing."""
    ha_config = Mock()
    ha_config.assist_pipeline = "default-pipeline"

    prefs = AssistantPreferences(
        wake_sound=True,
        speaking_style="normal",
        wake_sensitivity="normal",
        ha_response_mode="full",
        ha_tone_sound="notify-soft-chime",
    )
    sounds = SoundSettings(
        default_alarm="alarm-digital-rise",
        default_timer="timer-woodblock",
        default_reminder="reminder-marimba",
        default_notification="notify-soft-chime",
    )
    config = Mock(spec=AssistantConfig)
    config.mqtt = mqtt_config
    config.llm = llm_config
    config.home_assistant = ha_config
    config.preferences = prefs
    config.sounds = sounds
    config.hostname = "test-host"
    return config


@pytest.fixture
def mock_mqtt():
    """Mock MQTT client."""
    mqtt_client = Mock(spec=AssistantMqtt)
    mqtt_client.subscribe = Mock()
    mqtt_client.publish = Mock()
    return mqtt_client


@pytest.fixture
def mock_publisher():
    """Mock MQTT publisher."""
    publisher = Mock(spec=AssistantMqttPublisher)
    publisher._publish_preference_state = Mock()
    return publisher


@pytest.fixture
def mock_sound_library():
    """Mock sound library with test sounds."""
    library = Mock(spec=SoundLibrary)

    alarm_sound = Mock(sound_id="alarm-digital-rise", label="Digital Rise", kinds=["alarm"])
    timer_sound = Mock(sound_id="timer-woodblock", label="Woodblock", kinds=["timer"])
    reminder_sound = Mock(sound_id="reminder-marimba", label="Marimba", kinds=["reminder"])
    notify_sound = Mock(sound_id="notify-soft-chime", label="Soft Chime", kinds=["notification"])

    library.built_in_sounds = Mock(return_value=[alarm_sound, timer_sound, reminder_sound, notify_sound])
    library.custom_sounds = Mock(return_value=[])

    return library


@pytest.fixture
def preference_manager(assistant_config, mock_mqtt, mock_sound_library, mock_publisher):
    """Create PreferenceManager for testing."""
    return PreferenceManager(
        mqtt=mock_mqtt,
        config=assistant_config,
        sound_library=mock_sound_library,
        publisher=mock_publisher,
        logger=Mock(spec=logging.Logger),
    )


# Initialization Tests


class TestPreferenceManagerInit:
    """Test PreferenceManager initialization."""

    def test_init(self, preference_manager):
        """Test manager initializes correctly."""
        assert preference_manager.mqtt is not None
        assert preference_manager.config is not None
        assert preference_manager.sound_library is not None
        assert preference_manager._sound_options is not None

    def test_sound_options_populated(self, preference_manager):
        """Test that sound options are populated on init."""
        assert "alarm" in preference_manager._sound_options
        assert "timer" in preference_manager._sound_options
        assert "reminder" in preference_manager._sound_options
        assert "notification" in preference_manager._sound_options

    def test_callbacks_initially_none(self, preference_manager):
        """Test that callbacks start as None."""
        assert preference_manager._on_wake_sensitivity_changed is None
        assert preference_manager._on_llm_provider_changed is None
        assert preference_manager._on_sound_settings_changed is None
        assert preference_manager._on_config_updated is None


# Callback Tests


class TestCallbackWiring:
    """Test callback registration and invocation."""

    def test_set_wake_sensitivity_callback(self, preference_manager):
        """Test wake sensitivity callback registration."""
        callback = Mock()
        preference_manager.set_wake_sensitivity_callback(callback)
        assert preference_manager._on_wake_sensitivity_changed is callback

    def test_set_llm_provider_callback(self, preference_manager):
        """Test LLM provider callback registration."""
        callback = Mock()
        preference_manager.set_llm_provider_callback(callback)
        assert preference_manager._on_llm_provider_changed is callback

    def test_set_sound_settings_callback(self, preference_manager):
        """Test sound settings callback registration."""
        callback = Mock()
        preference_manager.set_sound_settings_callback(callback)
        assert preference_manager._on_sound_settings_changed is callback

    def test_set_config_updated_callback(self, preference_manager):
        """Test config updated callback registration."""
        callback = Mock()
        preference_manager.set_config_updated_callback(callback)
        assert preference_manager._on_config_updated is callback

    def test_wake_sensitivity_callback_invoked(self, preference_manager, mock_publisher):
        """Test that wake sensitivity callback is invoked on change."""
        callback = Mock()
        preference_manager.set_wake_sensitivity_callback(callback)

        with patch("pulse.assistant.preference_manager.persist_preference"):
            preference_manager._handle_wake_sensitivity_command("high")

        callback.assert_called_once()

    def test_llm_provider_callback_invoked(self, preference_manager, mock_publisher):
        """Test that LLM provider callback is invoked on change."""
        callback = Mock()
        preference_manager.set_llm_provider_callback(callback)

        with patch("pulse.assistant.preference_manager.persist_preference"):
            with patch("pulse.assistant.llm.get_supported_providers", return_value={"openai": {}, "anthropic": {}}):
                preference_manager._handle_llm_provider_command("anthropic")

        callback.assert_called_once()


# Wake Sound Handler Tests


class TestWakeSoundHandler:
    """Test wake sound toggle command handler."""

    def test_wake_sound_on(self, preference_manager, mock_publisher):
        """Test turning wake sound on."""
        with patch("pulse.assistant.preference_manager.persist_preference") as mock_persist:
            preference_manager._handle_wake_sound_command("on")

        assert preference_manager.preferences.wake_sound is True
        mock_publisher._publish_preference_state.assert_called_with("wake_sound", "on")
        mock_persist.assert_called()

    def test_wake_sound_off(self, preference_manager, mock_publisher):
        """Test turning wake sound off."""
        with patch("pulse.assistant.preference_manager.persist_preference"):
            preference_manager._handle_wake_sound_command("off")

        assert preference_manager.preferences.wake_sound is False
        mock_publisher._publish_preference_state.assert_called_with("wake_sound", "off")

    def test_wake_sound_true_variant(self, preference_manager, mock_publisher):
        """Test wake sound with 'true' value."""
        with patch("pulse.assistant.preference_manager.persist_preference"):
            preference_manager._handle_wake_sound_command("true")

        assert preference_manager.preferences.wake_sound is True


# Speaking Style Handler Tests


class TestSpeakingStyleHandler:
    """Test speaking style command handler."""

    def test_speaking_style_relaxed(self, preference_manager, mock_publisher):
        """Test setting speaking style to relaxed."""
        with patch("pulse.assistant.preference_manager.persist_preference"):
            preference_manager._handle_speaking_style_command("relaxed")

        assert preference_manager.preferences.speaking_style == "relaxed"
        mock_publisher._publish_preference_state.assert_called_with("speaking_style", "relaxed")

    def test_speaking_style_aggressive(self, preference_manager, mock_publisher):
        """Test setting speaking style to aggressive."""
        with patch("pulse.assistant.preference_manager.persist_preference"):
            preference_manager._handle_speaking_style_command("aggressive")

        assert preference_manager.preferences.speaking_style == "aggressive"

    def test_speaking_style_invalid_ignored(self, preference_manager, mock_publisher):
        """Test that invalid speaking style is ignored."""
        original_style = preference_manager.preferences.speaking_style
        with patch("pulse.assistant.preference_manager.persist_preference"):
            preference_manager._handle_speaking_style_command("invalid_style")

        assert preference_manager.preferences.speaking_style == original_style
        mock_publisher._publish_preference_state.assert_not_called()


# Wake Sensitivity Handler Tests


class TestWakeSensitivityHandler:
    """Test wake sensitivity command handler."""

    def test_wake_sensitivity_high(self, preference_manager, mock_publisher):
        """Test setting wake sensitivity to high."""
        with patch("pulse.assistant.preference_manager.persist_preference"):
            preference_manager._handle_wake_sensitivity_command("high")

        assert preference_manager.preferences.wake_sensitivity == "high"
        mock_publisher._publish_preference_state.assert_called_with("wake_sensitivity", "high")

    def test_wake_sensitivity_low(self, preference_manager, mock_publisher):
        """Test setting wake sensitivity to low."""
        with patch("pulse.assistant.preference_manager.persist_preference"):
            preference_manager._handle_wake_sensitivity_command("low")

        assert preference_manager.preferences.wake_sensitivity == "low"

    def test_wake_sensitivity_no_change_when_same(self, preference_manager, mock_publisher):
        """Test that same sensitivity value doesn't trigger update."""
        with patch("pulse.assistant.preference_manager.persist_preference"):
            preference_manager._handle_wake_sensitivity_command("normal")

        # Already "normal" from fixture, so no publish should happen
        mock_publisher._publish_preference_state.assert_not_called()

    def test_wake_sensitivity_invalid_ignored(self, preference_manager, mock_publisher):
        """Test that invalid sensitivity is ignored."""
        original = preference_manager.preferences.wake_sensitivity
        with patch("pulse.assistant.preference_manager.persist_preference"):
            preference_manager._handle_wake_sensitivity_command("extreme")

        assert preference_manager.preferences.wake_sensitivity == original


# Model Override Handler Tests


class TestModelOverrideHandler:
    """Test model selection command handlers."""

    def test_openai_model_override(self, preference_manager, mock_publisher):
        """Test setting OpenAI model override."""
        with patch("pulse.assistant.preference_manager.persist_preference") as mock_persist:
            preference_manager._handle_openai_model_command("gpt-4-turbo")

        assert preference_manager._openai_model_override == "gpt-4-turbo"
        mock_publisher._publish_preference_state.assert_called_with("openai_model", "gpt-4-turbo")
        mock_persist.assert_called_with("openai_model", "gpt-4-turbo", logger=preference_manager.logger)

    def test_model_override_empty_uses_default(self, preference_manager, mock_publisher):
        """Test that empty model override persists the default model."""
        preference_manager.config.llm.openai_model = "gpt-4o"

        with patch("pulse.assistant.preference_manager.persist_preference") as mock_persist:
            preference_manager._handle_openai_model_command("")

        # Should persist default model, not empty string
        mock_persist.assert_called_with("openai_model", "gpt-4o", logger=preference_manager.logger)
        mock_publisher._publish_preference_state.assert_called_with("openai_model", "gpt-4o")

    def test_anthropic_model_override(self, preference_manager, mock_publisher):
        """Test setting Anthropic model override."""
        with patch("pulse.assistant.preference_manager.persist_preference"):
            preference_manager._handle_anthropic_model_command("claude-3-opus")

        assert preference_manager._anthropic_model_override == "claude-3-opus"

    def test_model_override_triggers_provider_callback_when_active(self, preference_manager, mock_publisher):
        """Test that model change triggers callback when provider is active."""
        callback = Mock()
        preference_manager.set_llm_provider_callback(callback)
        preference_manager._llm_provider_override = "openai"

        with patch("pulse.assistant.preference_manager.persist_preference"):
            preference_manager._handle_openai_model_command("gpt-4-turbo")

        callback.assert_called_once()


# LLM Logging Handler Tests


class TestLogLlmHandler:
    """Test LLM logging toggle handler."""

    def test_log_llm_on(self, preference_manager, mock_publisher):
        """Test enabling LLM logging."""
        with patch("pulse.assistant.preference_manager.persist_preference"):
            preference_manager._handle_log_llm_command("on")

        assert preference_manager._log_llm_messages is True
        mock_publisher._publish_preference_state.assert_called_with("log_llm", "on")

    def test_log_llm_off(self, preference_manager, mock_publisher):
        """Test disabling LLM logging."""
        preference_manager._log_llm_messages = True
        with patch("pulse.assistant.preference_manager.persist_preference"):
            preference_manager._handle_log_llm_command("off")

        assert preference_manager._log_llm_messages is False

    def test_log_llm_no_change_when_same(self, preference_manager, mock_publisher):
        """Test that same value doesn't trigger update."""
        preference_manager._log_llm_messages = False
        with patch("pulse.assistant.preference_manager.persist_preference"):
            preference_manager._handle_log_llm_command("off")

        mock_publisher._publish_preference_state.assert_not_called()


# HA Preference Handler Tests


class TestHaResponseModeHandler:
    """Test HA response mode command handler."""

    def test_ha_response_mode_valid(self, preference_manager, mock_publisher):
        """Test setting valid HA response modes."""
        for mode in ["none", "tone", "minimal", "full"]:
            # Reset to different mode first
            preference_manager.preferences = replace(preference_manager.preferences, ha_response_mode="other")
            with patch("pulse.assistant.preference_manager.persist_preference"):
                preference_manager._handle_ha_response_mode_command(mode)

            assert preference_manager.preferences.ha_response_mode == mode
            mock_publisher._publish_preference_state.assert_called_with("ha_response_mode", mode)

    def test_ha_response_mode_invalid_ignored(self, preference_manager, mock_publisher):
        """Test that invalid HA response mode is ignored."""
        original = preference_manager.preferences.ha_response_mode
        preference_manager._handle_ha_response_mode_command("invalid_mode")

        assert preference_manager.preferences.ha_response_mode == original
        mock_publisher._publish_preference_state.assert_not_called()

    def test_ha_response_mode_no_change_when_same(self, preference_manager, mock_publisher):
        """Test that same mode doesn't trigger update."""
        # Fixture already has ha_response_mode="full"
        with patch("pulse.assistant.preference_manager.persist_preference"):
            preference_manager._handle_ha_response_mode_command("full")

        mock_publisher._publish_preference_state.assert_not_called()


class TestHaToneSoundHandler:
    """Test HA tone sound command handler."""

    def test_ha_tone_sound_valid(self, preference_manager, mock_publisher):
        """Test setting HA tone sound."""
        # Set different current value first
        preference_manager.preferences = replace(preference_manager.preferences, ha_tone_sound="other-sound")

        with patch("pulse.assistant.preference_manager.persist_preference"):
            preference_manager._handle_ha_tone_sound_command("Soft Chime")

        mock_publisher._publish_preference_state.assert_called()

    def test_ha_tone_sound_empty_ignored(self, preference_manager, mock_publisher):
        """Test that empty payload is ignored."""
        preference_manager._handle_ha_tone_sound_command("")

        mock_publisher._publish_preference_state.assert_not_called()

    def test_ha_tone_sound_no_change_when_same(self, preference_manager, mock_publisher):
        """Test that same sound doesn't trigger update."""
        # Set current to the sound ID we'll send
        preference_manager.preferences = replace(preference_manager.preferences, ha_tone_sound="notify-soft-chime")

        with patch("pulse.assistant.preference_manager.persist_preference"):
            preference_manager._handle_ha_tone_sound_command("Soft Chime")

        mock_publisher._publish_preference_state.assert_not_called()


class TestHaPipelineHandler:
    """Test HA pipeline command handler."""

    def test_ha_pipeline_set(self, preference_manager, mock_publisher):
        """Test setting HA pipeline."""
        with patch("pulse.assistant.preference_manager.persist_preference"):
            preference_manager._handle_ha_pipeline_command("custom-pipeline")

        assert preference_manager._ha_pipeline_override == "custom-pipeline"
        mock_publisher._publish_preference_state.assert_called()

    def test_ha_pipeline_clear(self, preference_manager, mock_publisher):
        """Test clearing HA pipeline override with empty payload."""
        preference_manager._ha_pipeline_override = "some-pipeline"

        with patch("pulse.assistant.preference_manager.persist_preference"):
            preference_manager._handle_ha_pipeline_command("")

        assert preference_manager._ha_pipeline_override is None


# LLM Provider Handler Tests


class TestLlmProviderHandler:
    """Test LLM provider command handler branches."""

    def test_llm_provider_valid(self, preference_manager, mock_publisher):
        """Test setting valid LLM provider."""
        with patch("pulse.assistant.preference_manager.persist_preference"):
            with patch("pulse.assistant.llm.get_supported_providers", return_value={"openai": {}, "anthropic": {}}):
                preference_manager._handle_llm_provider_command("anthropic")

        assert preference_manager._llm_provider_override == "anthropic"
        mock_publisher._publish_preference_state.assert_called()

    def test_llm_provider_empty_clears_override(self, preference_manager, mock_publisher):
        """Test that empty payload clears the override."""
        preference_manager._llm_provider_override = "anthropic"

        with patch("pulse.assistant.preference_manager.persist_preference"):
            with patch("pulse.assistant.llm.get_supported_providers", return_value={"openai": {}, "anthropic": {}}):
                preference_manager._handle_llm_provider_command("")

        assert preference_manager._llm_provider_override is None

    def test_llm_provider_invalid_ignored(self, preference_manager, mock_publisher):
        """Test that invalid provider is ignored and logged."""
        original = preference_manager._llm_provider_override

        with patch("pulse.assistant.preference_manager.persist_preference"):
            with patch("pulse.assistant.llm.get_supported_providers", return_value={"openai": {}, "anthropic": {}}):
                preference_manager._handle_llm_provider_command("invalid_provider")

        assert preference_manager._llm_provider_override == original
        mock_publisher._publish_preference_state.assert_not_called()

    def test_llm_provider_triggers_callback(self, preference_manager, mock_publisher):
        """Test that provider change triggers callback."""
        callback = Mock()
        preference_manager.set_llm_provider_callback(callback)

        with patch("pulse.assistant.preference_manager.persist_preference"):
            with patch("pulse.assistant.llm.get_supported_providers", return_value={"openai": {}, "anthropic": {}}):
                preference_manager._handle_llm_provider_command("anthropic")

        callback.assert_called_once()

    def test_llm_provider_persists(self, preference_manager, mock_publisher):
        """Test that provider change is persisted."""
        with patch("pulse.assistant.preference_manager.persist_preference") as mock_persist:
            with patch("pulse.assistant.llm.get_supported_providers", return_value={"openai": {}, "anthropic": {}}):
                preference_manager._handle_llm_provider_command("anthropic")

        mock_persist.assert_called_with("llm_provider", "anthropic", logger=preference_manager.logger)


# Sound Preference Handler Tests


class TestSoundPreferenceHandler:
    """Test sound preference command handlers."""

    def test_sound_alarm_command(self, preference_manager, mock_publisher):
        """Test setting alarm sound when sound differs from current."""
        # Set current sound to a different value
        preference_manager.config.sounds = Mock()
        preference_manager.config.sounds.default_alarm = "other-alarm"
        preference_manager.config.sounds.default_timer = "timer-woodblock"
        preference_manager.config.sounds.default_reminder = "reminder-marimba"
        preference_manager.config.sounds.default_notification = "notify-soft-chime"
        preference_manager.config.sounds.custom_dir = None

        with patch("pulse.assistant.preference_manager.persist_preference"):
            with patch.object(preference_manager, "_update_sound_setting"):
                preference_manager._handle_sound_alarm_command("Digital Rise")

        mock_publisher._publish_preference_state.assert_called_with("sound_alarm", "Digital Rise")

    def test_sound_timer_command(self, preference_manager, mock_publisher):
        """Test setting timer sound when sound differs from current."""
        # Set current sound to a different value
        preference_manager.config.sounds = Mock()
        preference_manager.config.sounds.default_alarm = "alarm-digital-rise"
        preference_manager.config.sounds.default_timer = "other-timer"
        preference_manager.config.sounds.default_reminder = "reminder-marimba"
        preference_manager.config.sounds.default_notification = "notify-soft-chime"
        preference_manager.config.sounds.custom_dir = None

        with patch("pulse.assistant.preference_manager.persist_preference"):
            with patch.object(preference_manager, "_update_sound_setting"):
                preference_manager._handle_sound_timer_command("Woodblock")

        mock_publisher._publish_preference_state.assert_called_with("sound_timer", "Woodblock")

    def test_sound_command_empty_ignored(self, preference_manager, mock_publisher):
        """Test that empty sound payload is ignored."""
        preference_manager._handle_sound_alarm_command("")

        mock_publisher._publish_preference_state.assert_not_called()

    def test_sound_command_unknown_ignored(self, preference_manager, mock_publisher):
        """Test that unknown sound is ignored."""
        preference_manager._handle_sound_alarm_command("Unknown Sound")

        mock_publisher._publish_preference_state.assert_not_called()

    def test_sound_command_no_change_when_same(self, preference_manager, mock_publisher):
        """Test that same sound doesn't trigger update."""
        # Fixture already has default_alarm="alarm-digital-rise" matching "Digital Rise"
        with patch("pulse.assistant.preference_manager.persist_preference"):
            preference_manager._handle_sound_alarm_command("Digital Rise")

        # Should not publish since it's already the current sound
        mock_publisher._publish_preference_state.assert_not_called()


# Sound Setting Update Tests


class TestUpdateSoundSetting:
    """Test _update_sound_setting side effects."""

    def test_update_sound_setting_creates_new_sound_settings(self, preference_manager):
        """Test that _update_sound_setting creates correct new SoundSettings."""
        # Set up sounds
        preference_manager.config.sounds = SoundSettings(
            default_alarm="old-alarm",
            default_timer="timer-woodblock",
            default_reminder="reminder-marimba",
            default_notification="notify-soft-chime",
        )

        # Mock config to capture the new config
        new_config = Mock()
        with patch("pulse.assistant.preference_manager.replace", return_value=new_config) as mock_replace:
            preference_manager._update_sound_setting("alarm", "new-alarm")

        # Verify replace was called with correct new_sounds
        mock_replace.assert_called_once()
        call_kwargs = mock_replace.call_args[1]
        new_sounds = call_kwargs["sounds"]
        assert new_sounds.default_alarm == "new-alarm"
        assert new_sounds.default_timer == "timer-woodblock"
        assert new_sounds.default_reminder == "reminder-marimba"
        assert new_sounds.default_notification == "notify-soft-chime"

    def test_update_sound_setting_invokes_sound_callback(self, preference_manager):
        """Test that _update_sound_setting invokes the sound settings callback."""
        preference_manager.config.sounds = SoundSettings(
            default_alarm="old-alarm",
            default_timer="timer-woodblock",
            default_reminder="reminder-marimba",
            default_notification="notify-soft-chime",
        )

        sound_callback = Mock()
        preference_manager.set_sound_settings_callback(sound_callback)

        new_config = Mock()
        with patch("pulse.assistant.preference_manager.replace", return_value=new_config):
            preference_manager._update_sound_setting("timer", "new-timer")

        # Verify callback was invoked with the new SoundSettings
        sound_callback.assert_called_once()
        new_sounds = sound_callback.call_args[0][0]
        assert new_sounds.default_timer == "new-timer"
        assert new_sounds.default_alarm == "old-alarm"  # unchanged

    def test_update_sound_setting_invokes_config_callback(self, preference_manager):
        """Test that _update_sound_setting invokes the config updated callback."""
        preference_manager.config.sounds = SoundSettings(
            default_alarm="old-alarm",
            default_timer="timer-woodblock",
            default_reminder="reminder-marimba",
            default_notification="notify-soft-chime",
        )

        config_callback = Mock()
        preference_manager.set_config_updated_callback(config_callback)

        new_config = Mock()
        new_config.sounds = SoundSettings(
            default_alarm="old-alarm",
            default_timer="timer-woodblock",
            default_reminder="new-reminder",
            default_notification="notify-soft-chime",
        )
        with patch("pulse.assistant.preference_manager.replace", return_value=new_config):
            preference_manager._update_sound_setting("reminder", "new-reminder")

        # Verify config callback was invoked
        config_callback.assert_called_once()
        updated_config = config_callback.call_args[0][0]
        assert updated_config.sounds.default_reminder == "new-reminder"


# Sound Lookup Tests


class TestSoundLookup:
    """Test sound ID/label lookup methods."""

    def test_get_sound_options_for_kind(self, preference_manager):
        """Test getting sound options for a kind."""
        options = preference_manager.get_sound_options_for_kind("alarm")
        assert "Digital Rise" in options

    def test_get_sound_id_by_label(self, preference_manager):
        """Test looking up sound ID from label."""
        sound_id = preference_manager.get_sound_id_by_label("alarm", "Digital Rise")
        assert sound_id == "alarm-digital-rise"

    def test_get_sound_id_by_label_not_found(self, preference_manager):
        """Test looking up non-existent sound returns None."""
        sound_id = preference_manager.get_sound_id_by_label("alarm", "Non-existent")
        assert sound_id is None

    def test_get_sound_label_by_id(self, preference_manager):
        """Test looking up label from sound ID."""
        label = preference_manager.get_sound_label_by_id("alarm", "alarm-digital-rise")
        assert label == "Digital Rise"

    def test_get_current_sound_id(self, preference_manager):
        """Test getting current sound ID for a kind."""
        sound_id = preference_manager.get_current_sound_id("alarm")
        assert sound_id == "alarm-digital-rise"


# Pipeline and Provider Accessor Tests


class TestPipelineAndProviderAccessors:
    """Test pipeline and provider accessor methods."""

    def test_get_active_ha_pipeline_default(self, preference_manager):
        """Test getting HA pipeline with no override."""
        pipeline = preference_manager.get_active_ha_pipeline()
        assert pipeline == "default-pipeline"

    def test_get_active_ha_pipeline_override(self, preference_manager):
        """Test getting HA pipeline with override."""
        preference_manager._ha_pipeline_override = "custom-pipeline"
        pipeline = preference_manager.get_active_ha_pipeline()
        assert pipeline == "custom-pipeline"

    def test_get_active_llm_provider_default(self, preference_manager):
        """Test getting LLM provider with no override."""
        provider = preference_manager.get_active_llm_provider()
        assert provider == "openai"

    def test_get_active_llm_provider_override(self, preference_manager):
        """Test getting LLM provider with override."""
        preference_manager._llm_provider_override = "anthropic"
        provider = preference_manager.get_active_llm_provider()
        assert provider == "anthropic"

    def test_get_model_override(self, preference_manager):
        """Test getting model override for provider."""
        preference_manager._openai_model_override = "gpt-4-turbo"
        override = preference_manager.get_model_override("openai")
        assert override == "gpt-4-turbo"

    def test_get_model_override_none(self, preference_manager):
        """Test getting model override when not set."""
        override = preference_manager.get_model_override("openai")
        assert override is None


# MQTT Subscription Tests


class TestMqttSubscription:
    """Test MQTT topic subscription."""

    def test_subscribe_preference_topics(self, preference_manager, mock_mqtt):
        """Test that all preference topics are subscribed."""
        preference_manager.subscribe_preference_topics()

        # Check that subscribe was called for expected topics
        calls = mock_mqtt.subscribe.call_args_list
        topics = [call[0][0] for call in calls]

        assert any("wake_sound/set" in t for t in topics)
        assert any("speaking_style/set" in t for t in topics)
        assert any("wake_sensitivity/set" in t for t in topics)
        assert any("llm_provider/set" in t for t in topics)
        assert any("sound_alarm/set" in t for t in topics)
        assert any("openai_model/set" in t for t in topics)
