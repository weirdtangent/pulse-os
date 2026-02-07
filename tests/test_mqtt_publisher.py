"""Tests for the MQTT publisher (pulse/assistant/mqtt_publisher.py).

Tests for MQTT publishing logic extracted from the main assistant class.
Target: 20+ tests covering core publishing, discovery, sound management, and overlays.
"""

from __future__ import annotations

import asyncio
import json
import logging
from unittest.mock import AsyncMock, Mock

import pytest
from pulse.assistant.config import AssistantConfig, MqttConfig
from pulse.assistant.home_assistant import HomeAssistantClient
from pulse.assistant.mqtt import AssistantMqtt
from pulse.assistant.mqtt_publisher import AssistantMqttPublisher
from pulse.assistant.schedule_service import ScheduleService
from pulse.sound_library import SoundLibrary, SoundSettings

# Fixtures


@pytest.fixture
def mqtt_config():
    """Basic MQTT configuration for testing."""
    return MqttConfig(
        host="localhost",
        port=1883,
        topic_base="test-device",
        username=None,
        password=None,
        tls_enabled=False,
        ca_cert=None,
        cert=None,
        key=None,
    )


@pytest.fixture
def assistant_config(mqtt_config, tmp_path):
    """Full assistant configuration for testing."""
    config = Mock(spec=AssistantConfig)
    config.mqtt = mqtt_config
    config.hostname = "test-host"
    config.state_topic = f"{mqtt_config.topic_base}/state"
    config.device_name = "Test Device"
    return config


@pytest.fixture
def mock_mqtt():
    """Mock MQTT client."""
    mqtt_client = Mock(spec=AssistantMqtt)
    mqtt_client.publish = Mock()
    return mqtt_client


@pytest.fixture
def mock_schedule_service():
    """Mock schedule service."""
    return Mock(spec=ScheduleService)


@pytest.fixture
def mock_sound_library():
    """Mock sound library with test sounds."""
    library = Mock(spec=SoundLibrary)

    # Mock built-in sounds
    alarm_sound = Mock(sound_id="alarm-digital-rise", label="Digital Rise", kinds=["alarm"])
    timer_sound = Mock(sound_id="timer-woodblock", label="Woodblock", kinds=["timer"])
    reminder_sound = Mock(sound_id="reminder-marimba", label="Marimba", kinds=["reminder"])
    notify_sound = Mock(sound_id="notify-soft-chime", label="Soft Chime", kinds=["notification"])

    library.built_in_sounds = Mock(return_value=[alarm_sound, timer_sound, reminder_sound, notify_sound])
    library.custom_sounds = Mock(return_value=[])

    return library


@pytest.fixture
def publisher(assistant_config, mock_mqtt, mock_schedule_service, mock_sound_library):
    """Create MQTT publisher for testing."""
    return AssistantMqttPublisher(
        mqtt=mock_mqtt,
        config=assistant_config,
        home_assistant=None,
        schedule_service=mock_schedule_service,
        sound_library=mock_sound_library,
        logger=Mock(spec=logging.Logger),
    )


# Basic Publishing Tests


def test_publisher_init(publisher):
    """Test publisher initialization."""
    assert publisher.mqtt is not None
    assert publisher.config is not None
    assert publisher.sound_library is not None
    assert publisher._sound_options is not None
    assert "alarm" in publisher._sound_options
    assert "timer" in publisher._sound_options


def test_publish_message(publisher, mock_mqtt):
    """Test basic message publishing."""
    publisher._publish_message("test/topic", "test payload", retain=True)

    mock_mqtt.publish.assert_called_once_with("test/topic", payload="test payload", retain=True)


def test_publish_state(publisher, mock_mqtt):
    """Test state publishing."""
    publisher._publish_state("idle")

    assert mock_mqtt.publish.called
    call_args = mock_mqtt.publish.call_args
    topic, payload = call_args[0][0], call_args[1]["payload"]

    assert topic == "test-device/state"
    data = json.loads(payload)
    assert data["state"] == "idle"
    assert data["device"] == "test-host"


def test_publish_state_with_extra(publisher, mock_mqtt):
    """Test state publishing with extra data."""
    publisher._publish_state("thinking", extra={"duration": 1.5})

    call_args = mock_mqtt.publish.call_args
    payload = call_args[1]["payload"]
    data = json.loads(payload)

    assert data["state"] == "thinking"
    assert data["duration"] == 1.5


def test_publish_preference_state(publisher, mock_mqtt):
    """Test preference state publishing."""
    publisher._publish_preference_state("wake_sound", "on")

    assert mock_mqtt.publish.called
    call_args = mock_mqtt.publish.call_args
    topic = call_args[0][0]
    payload = call_args[1]["payload"]

    assert topic == "test-device/preferences/wake_sound/state"
    assert payload == "on"
    assert call_args[1]["retain"] is True


def test_publish_earmuffs_state(publisher, mock_mqtt):
    """Test earmuffs state publishing."""
    publisher._publish_earmuffs_state(enabled=True)

    call_args = mock_mqtt.publish.call_args
    payload = call_args[1]["payload"]

    assert payload == "on"
    assert call_args[1]["retain"] is True


# Info Overlay Tests


def test_publish_info_overlay_with_text(publisher, mock_mqtt):
    """Test publishing info overlay with text."""
    publisher._publish_info_overlay(text="Test message", category="test")

    assert mock_mqtt.publish.called
    call_args = mock_mqtt.publish.call_args
    payload = call_args[1]["payload"]
    data = json.loads(payload)

    assert data["state"] == "show"
    assert data["text"] == "Test message"
    assert data["category"] == "test"
    assert "ts" in data
    assert isinstance(data["ts"], float)


def test_publish_info_overlay_clear(publisher, mock_mqtt):
    """Test publishing info overlay clear."""
    publisher._publish_info_overlay()

    call_args = mock_mqtt.publish.call_args
    payload = call_args[1]["payload"]
    data = json.loads(payload)

    assert data["state"] == "clear"


def test_publish_info_overlay_with_extra(publisher, mock_mqtt):
    """Test publishing info overlay with extra data."""
    extra_data = {"lights": [{"name": "Living Room", "state": "on"}]}
    publisher._publish_info_overlay(text="Lights updated", category="lights", extra=extra_data)

    call_args = mock_mqtt.publish.call_args
    payload = call_args[1]["payload"]
    data = json.loads(payload)

    assert data["lights"] == extra_data["lights"]


# Sound Management Tests


def test_get_sound_options_for_kind(publisher):
    """Test getting sound options for a kind."""
    options = publisher._get_sound_options_for_kind("alarm")

    assert "Digital Rise" in options
    assert len(options) > 0


def test_get_sound_label_by_id(publisher):
    """Test looking up sound label by ID."""
    label = publisher._get_sound_label_by_id("alarm", "alarm-digital-rise")

    assert label == "Digital Rise"


def test_get_sound_label_by_id_not_found(publisher):
    """Test looking up non-existent sound ID."""
    label = publisher._get_sound_label_by_id("alarm", "nonexistent")

    assert label is None


def test_get_sound_id_by_label(publisher):
    """Test looking up sound ID by label."""
    sound_id = publisher._get_sound_id_by_label("timer", "Woodblock")

    assert sound_id == "timer-woodblock"


def test_get_sound_id_by_label_not_found(publisher):
    """Test looking up non-existent sound label."""
    sound_id = publisher._get_sound_id_by_label("timer", "Nonexistent Sound")

    assert sound_id is None


def test_get_current_sound_id_valid(publisher):
    """Test getting current sound ID when valid."""
    sound_id = publisher._get_current_sound_id("alarm", "alarm-digital-rise")

    assert sound_id == "alarm-digital-rise"


def test_get_current_sound_id_fallback(publisher):
    """Test getting current sound ID with fallback."""
    # Invalid sound ID should fall back to first available
    sound_id = publisher._get_current_sound_id("alarm", "invalid-alarm")

    assert sound_id == "alarm-digital-rise"  # First in list


# Schedule State Tests


def test_publish_schedule_state(publisher, mock_mqtt):
    """Test publishing schedule state."""
    snapshot = {
        "alarms": [{"id": "alarm-1", "time": "08:00", "enabled": True}],
        "timers": [],
        "reminders": [],
    }
    calendar_events = [
        {"summary": "Meeting", "start": "2026-02-07T10:00:00"},
    ]

    publisher._publish_schedule_state(
        snapshot=snapshot,
        calendar_events=calendar_events,
        calendar_updated_at=1707310000.0,
    )

    assert mock_mqtt.publish.called
    call_args = mock_mqtt.publish.call_args
    payload = call_args[1]["payload"]
    data = json.loads(payload)

    assert data["alarms"] == snapshot["alarms"]
    assert len(data["calendar_events"]) == 1
    assert "calendar_updated_at" in data
    assert call_args[1]["retain"] is True


def test_publish_schedule_state_no_calendar_update(publisher, mock_mqtt):
    """Test publishing schedule state with no calendar update."""
    snapshot = {"alarms": [], "timers": [], "reminders": []}

    publisher._publish_schedule_state(
        snapshot=snapshot,
        calendar_events=[],
        calendar_updated_at=None,
    )

    call_args = mock_mqtt.publish.call_args
    payload = call_args[1]["payload"]
    data = json.loads(payload)

    assert data["calendar_updated_at"] is None


# Lights Card Tests


def test_format_lights_card_empty(publisher):
    """Test formatting lights card with no lights."""
    result = publisher._format_lights_card([])

    assert result is None


def test_format_lights_card(publisher):
    """Test formatting lights card with lights."""
    lights = [
        {
            "entity_id": "light.living_room",
            "state": "on",
            "attributes": {
                "friendly_name": "Living Room",
                "brightness": 200,
                "area_id": "living_room",
            },
        },
        {
            "entity_id": "light.bedroom",
            "state": "off",
            "attributes": {
                "friendly_name": "Bedroom",
                "area_id": "bedroom",
            },
        },
    ]

    result = publisher._format_lights_card(lights)

    assert result is not None
    assert result["type"] == "lights"
    assert result["title"] == "Lights"
    assert "1 on" in result["subtitle"]
    assert "2 total" in result["subtitle"]
    assert len(result["lights"]) == 2
    # Should sort with "on" lights first
    assert result["lights"][0]["state"] == "on"


def test_format_lights_card_with_color_temp(publisher):
    """Test formatting lights card with color temperature."""
    lights = [
        {
            "entity_id": "light.kitchen",
            "state": "on",
            "attributes": {
                "friendly_name": "Kitchen",
                "brightness": 255,
                "color_temp": 250,  # ~4000K
            },
        },
    ]

    result = publisher._format_lights_card(lights)

    assert result["lights"][0]["color_temp"] == "4000K"


# Async Tests


@pytest.mark.anyio
async def test_publish_light_overlay_no_ha(publisher, mock_mqtt):
    """Test publishing light overlay with no Home Assistant."""
    await publisher._publish_light_overlay(None)

    # Should not publish anything
    assert not mock_mqtt.publish.called


@pytest.mark.anyio
async def test_publish_light_overlay_with_lights(publisher, mock_mqtt):
    """Test publishing light overlay with lights from HA."""
    mock_ha = AsyncMock(spec=HomeAssistantClient)
    mock_ha.list_entities.return_value = [
        {
            "entity_id": "light.test",
            "state": "on",
            "attributes": {"friendly_name": "Test Light", "brightness": 128},
        }
    ]

    await publisher._publish_light_overlay(mock_ha)

    assert mock_mqtt.publish.called
    call_args = mock_mqtt.publish.call_args
    payload = call_args[1]["payload"]
    data = json.loads(payload)

    assert data["category"] == "lights"
    assert "lights" in data


# Preferences Tests


def test_publish_preferences(publisher, mock_mqtt):
    """Test publishing all preferences."""
    preferences = Mock()
    preferences.wake_sound = True
    preferences.speaking_style = "normal"
    preferences.wake_sensitivity = "high"
    preferences.ha_response_mode = "full"
    preferences.ha_tone_sound = "notify-soft-chime"

    config_sounds = SoundSettings(
        default_alarm="alarm-digital-rise",
        default_timer="timer-woodblock",
        default_reminder="reminder-marimba",
        default_notification="notify-soft-chime",
    )

    publisher._publish_preferences(
        preferences=preferences,
        log_llm=True,
        active_pipeline="test_pipeline",
        active_provider="openai",
        config_sounds=config_sounds,
    )

    # Should publish multiple preference states
    assert mock_mqtt.publish.call_count >= 8  # At least 8 different preferences


# Discovery Tests


def test_publish_assistant_discovery(publisher, mock_mqtt):
    """Test publishing Home Assistant MQTT discovery configs."""
    publisher._publish_assistant_discovery(hostname="test-device", device_name="Test Device")

    # Should publish multiple discovery configs
    assert mock_mqtt.publish.call_count > 10  # Discovery publishes many entities

    # Check that all calls use retain=True
    for call in mock_mqtt.publish.call_args_list:
        assert call[1]["retain"] is True


def test_clone_schedule_snapshot(publisher):
    """Test cloning schedule snapshot."""
    snapshot = {
        "alarms": [{"id": "1", "time": "08:00"}],
        "nested": {"key": "value"},
    }

    cloned = publisher._clone_schedule_snapshot(snapshot)

    assert cloned is not None
    assert cloned == snapshot
    assert cloned is not snapshot  # Different object


def test_clone_schedule_snapshot_invalid(publisher):
    """Test cloning un-serializable snapshot."""
    # Create a snapshot with non-serializable object
    snapshot = {"function": lambda x: x}

    cloned = publisher._clone_schedule_snapshot(snapshot)

    assert cloned is None


# Overlay Task Management Tests


def test_cancel_info_overlay_clear(publisher):
    """Test canceling info overlay clear task."""
    # Create a mock task
    mock_task = Mock(spec=asyncio.Task)
    mock_task.cancel = Mock()
    publisher._info_overlay_clear_task = mock_task

    publisher._cancel_info_overlay_clear()

    mock_task.cancel.assert_called_once()
    assert publisher._info_overlay_clear_task is None


def test_schedule_info_overlay_clear_zero_delay(publisher, mock_mqtt):
    """Test scheduling info overlay clear with zero delay."""
    publisher._schedule_info_overlay_clear(delay=0)

    # Should publish immediately
    assert mock_mqtt.publish.called
