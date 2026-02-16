"""Tests for EventHandlerManager."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, Mock

import pytest
from pulse.assistant.event_handlers import EventHandlerManager


@pytest.fixture
def mock_mqtt():
    mqtt = Mock()
    mqtt.subscribe = Mock()
    return mqtt


@pytest.fixture
def mock_publisher():
    publisher = Mock()
    publisher._publish_info_overlay = Mock()
    publisher._schedule_info_overlay_clear = Mock()
    return publisher


@pytest.fixture
def mock_wake_detector():
    detector = Mock()
    detector.set_remote_audio_active = Mock(return_value=False)
    return detector


@pytest.fixture
def handler(mock_mqtt, mock_publisher, mock_wake_detector, mock_logger):
    return EventHandlerManager(
        mqtt=mock_mqtt,
        publisher=mock_publisher,
        wake_detector=mock_wake_detector,
        alert_topics=["alerts/fire", "alerts/intrusion"],
        intercom_topic="intercom/main",
        playback_topic="pulse/test/telemetry/now_playing",
        kiosk_availability_topic="homeassistant/device/test/availability",
        logger=mock_logger,
    )


class TestInitialState:
    def test_kiosk_available_by_default(self, handler):
        assert handler.kiosk_available is True

    def test_speak_callback_not_set(self, handler):
        assert handler._on_speak is None

    def test_loop_not_set(self, handler):
        assert handler._loop is None


class TestSubscribeAll:
    def test_subscribes_all_topics(self, handler, mock_mqtt):
        handler.subscribe_all()
        assert mock_mqtt.subscribe.call_count == 5  # playback + 2 alerts + intercom + kiosk

    def test_no_intercom_subscription_when_topic_is_none(
        self, mock_mqtt, mock_publisher, mock_wake_detector, mock_logger
    ):
        mgr = EventHandlerManager(
            mqtt=mock_mqtt,
            publisher=mock_publisher,
            wake_detector=mock_wake_detector,
            alert_topics=[],
            intercom_topic=None,
            playback_topic="pulse/test/telemetry/now_playing",
            kiosk_availability_topic="homeassistant/device/test/availability",
            logger=mock_logger,
        )
        mgr.subscribe_all()
        # playback + kiosk only (no alerts, no intercom)
        assert mock_mqtt.subscribe.call_count == 2


class TestNowPlaying:
    def test_active_playback(self, handler, mock_wake_detector):
        handler.handle_now_playing_message("Artist - Song Title")
        mock_wake_detector.set_remote_audio_active.assert_called_once_with(True)

    def test_idle_playback(self, handler, mock_wake_detector):
        handler.handle_now_playing_message("")
        mock_wake_detector.set_remote_audio_active.assert_called_once_with(False)

    def test_whitespace_only_is_idle(self, handler, mock_wake_detector):
        handler.handle_now_playing_message("   ")
        mock_wake_detector.set_remote_audio_active.assert_called_once_with(False)

    def test_state_change_logged(self, handler, mock_wake_detector, mock_logger):
        mock_wake_detector.set_remote_audio_active.return_value = True
        handler.handle_now_playing_message("Song")
        mock_logger.debug.assert_called()


class TestAlertMessage:
    def test_plain_text_alert(self, handler, mock_publisher):
        loop = asyncio.new_event_loop()
        handler.set_event_loop(loop)
        handler.set_speak_callback(AsyncMock())
        handler.handle_alert_message("alerts/fire", "Fire detected in kitchen")
        mock_publisher._publish_info_overlay.assert_called_once_with(
            text="Alert: Fire detected in kitchen", category="alerts"
        )
        mock_publisher._schedule_info_overlay_clear.assert_called_once_with(8.0)
        loop.close()

    def test_json_alert_with_message_key(self, handler, mock_publisher):
        loop = asyncio.new_event_loop()
        handler.set_event_loop(loop)
        handler.set_speak_callback(AsyncMock())
        payload = json.dumps({"message": "Motion detected"})
        handler.handle_alert_message("alerts/motion", payload)
        mock_publisher._publish_info_overlay.assert_called_once_with(text="Alert: Motion detected", category="alerts")
        loop.close()

    def test_json_alert_with_text_key(self, handler, mock_publisher):
        loop = asyncio.new_event_loop()
        handler.set_event_loop(loop)
        handler.set_speak_callback(AsyncMock())
        payload = json.dumps({"text": "Door opened"})
        handler.handle_alert_message("alerts/door", payload)
        mock_publisher._publish_info_overlay.assert_called_once_with(text="Alert: Door opened", category="alerts")
        loop.close()

    def test_empty_alert_ignored(self, handler, mock_publisher):
        handler.handle_alert_message("alerts/test", "   ")
        mock_publisher._publish_info_overlay.assert_not_called()

    def test_no_loop_logs_error(self, handler, mock_logger):
        handler.handle_alert_message("alerts/test", "Fire!")
        mock_logger.error.assert_called()


class TestIntercomMessage:
    def test_intercom_message(self, handler, mock_publisher):
        loop = asyncio.new_event_loop()
        handler.set_event_loop(loop)
        handler.set_speak_callback(AsyncMock())
        handler.handle_intercom_message("Hello from the front door")
        mock_publisher._publish_info_overlay.assert_called_once_with(
            text="Intercom: Hello from the front door", category="intercom"
        )
        mock_publisher._schedule_info_overlay_clear.assert_called_once_with(6.0)
        loop.close()

    def test_empty_intercom_ignored(self, handler, mock_publisher):
        handler.handle_intercom_message("")
        mock_publisher._publish_info_overlay.assert_not_called()

    def test_whitespace_intercom_ignored(self, handler, mock_publisher):
        handler.handle_intercom_message("   ")
        mock_publisher._publish_info_overlay.assert_not_called()

    def test_no_loop_logs_error(self, handler, mock_logger):
        handler.handle_intercom_message("Hello")
        mock_logger.error.assert_called()


class TestKioskAvailability:
    def test_online(self, handler):
        handler.handle_kiosk_availability("online")
        assert handler.kiosk_available is True

    def test_offline(self, handler):
        handler.handle_kiosk_availability("offline")
        assert handler.kiosk_available is False

    def test_case_insensitive(self, handler):
        handler.handle_kiosk_availability("ONLINE")
        assert handler.kiosk_available is True

    def test_unknown_value_is_offline(self, handler):
        handler.handle_kiosk_availability("unknown")
        assert handler.kiosk_available is False


class TestKioskHealthCheck:
    @pytest.mark.anyio
    async def test_no_restart_when_online(self, handler):
        handler.handle_kiosk_availability("online")
        await handler.check_kiosk_health()
        # Should complete without attempting restart

    @pytest.mark.anyio
    async def test_no_restart_within_grace_period(self, handler):
        handler.handle_kiosk_availability("offline")
        # Just went offline, within grace period
        await handler.check_kiosk_health()
        # Should not attempt restart within 90s grace


class TestCallbacks:
    def test_set_speak_callback(self, handler):
        cb = AsyncMock()
        handler.set_speak_callback(cb)
        assert handler._on_speak is cb

    def test_set_event_loop(self, handler):
        loop = asyncio.new_event_loop()
        handler.set_event_loop(loop)
        assert handler._loop is loop
        loop.close()
