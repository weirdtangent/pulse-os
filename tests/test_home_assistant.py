"""Tests for Home Assistant client."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest

from pulse.assistant.config import HomeAssistantConfig
from pulse.assistant.home_assistant import (
    HomeAssistantAuthError,
    HomeAssistantClient,
    HomeAssistantError,
)

# Mark all tests in this module as anyio
pytestmark = pytest.mark.anyio


@pytest.fixture
def ha_config():
    """Create a test Home Assistant configuration."""
    return HomeAssistantConfig(
        base_url="http://homeassistant.local:8123",
        token="test_token_123",
        verify_ssl=True,
        assist_pipeline=None,
        wake_endpoint=None,
        stt_endpoint=None,
        tts_endpoint=None,
        timer_entity=None,
        reminder_service=None,
        presence_entity=None,
    )


@pytest.fixture
def mock_client():
    """Create a mock httpx.AsyncClient."""
    client = AsyncMock(spec=httpx.AsyncClient)
    return client


class TestHomeAssistantClientInit:
    """Test Home Assistant client initialization."""

    def test_init_success(self, ha_config):
        """Test successful client initialization."""
        client = HomeAssistantClient(ha_config)
        assert client.config == ha_config
        assert client.timeout == 10.0
        assert not client._closed

    def test_init_strips_trailing_slash(self):
        """Test that trailing slash is stripped from base URL."""
        config = HomeAssistantConfig(
            base_url="http://homeassistant.local:8123/",
            token="test_token",
            verify_ssl=True,
            assist_pipeline=None,
            wake_endpoint=None,
            stt_endpoint=None,
            tts_endpoint=None,
            timer_entity=None,
            reminder_service=None,
            presence_entity=None,
        )
        client = HomeAssistantClient(config)
        assert client._client.base_url == httpx.URL("http://homeassistant.local:8123")

    def test_init_sets_auth_header(self, ha_config):
        """Test that authorization header is set correctly."""
        client = HomeAssistantClient(ha_config)
        assert client._client.headers["Authorization"] == "Bearer test_token_123"
        assert client._client.headers["Content-Type"] == "application/json"

    def test_init_missing_base_url(self):
        """Test initialization fails without base URL."""
        config = HomeAssistantConfig(
            base_url="",
            token="test_token",
            verify_ssl=True,
            assist_pipeline=None,
            wake_endpoint=None,
            stt_endpoint=None,
            tts_endpoint=None,
            timer_entity=None,
            reminder_service=None,
            presence_entity=None,
        )
        with pytest.raises(ValueError, match="base URL is not configured"):
            HomeAssistantClient(config)

    def test_init_missing_token(self):
        """Test initialization fails without token."""
        config = HomeAssistantConfig(
            base_url="http://homeassistant.local:8123",
            token="",
            verify_ssl=True,
            assist_pipeline=None,
            wake_endpoint=None,
            stt_endpoint=None,
            tts_endpoint=None,
            timer_entity=None,
            reminder_service=None,
            presence_entity=None,
        )
        with pytest.raises(ValueError, match="token is not configured"):
            HomeAssistantClient(config)

    def test_init_custom_timeout(self, ha_config):
        """Test initialization with custom timeout."""
        client = HomeAssistantClient(ha_config, timeout=30.0)
        assert client.timeout == 30.0


class TestHomeAssistantClientBasicMethods:
    """Test basic Home Assistant client methods."""

    async def test_get_info(self, ha_config):
        """Test getting Home Assistant info."""
        with patch.object(HomeAssistantClient, "_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = {"version": "2024.1.0"}
            client = HomeAssistantClient(ha_config)
            result = await client.get_info()
            mock_request.assert_called_once_with("GET", "/api/")
            assert result == {"version": "2024.1.0"}

    async def test_get_state(self, ha_config):
        """Test getting entity state."""
        with patch.object(HomeAssistantClient, "_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = {
                "entity_id": "light.bedroom",
                "state": "on",
                "attributes": {"brightness": 255},
            }
            client = HomeAssistantClient(ha_config)
            result = await client.get_state("light.bedroom")
            mock_request.assert_called_once_with("GET", "/api/states/light.bedroom")
            assert result["state"] == "on"

    async def test_list_states(self, ha_config):
        """Test listing all entity states."""
        with patch.object(HomeAssistantClient, "_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = [
                {"entity_id": "light.bedroom", "state": "on"},
                {"entity_id": "light.kitchen", "state": "off"},
            ]
            client = HomeAssistantClient(ha_config)
            result = await client.list_states()
            mock_request.assert_called_once_with("GET", "/api/states")
            assert len(result) == 2
            assert result[0]["entity_id"] == "light.bedroom"

    async def test_list_states_filters_non_dict(self, ha_config):
        """Test that list_states filters out non-dict items."""
        with patch.object(HomeAssistantClient, "_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = [
                {"entity_id": "light.bedroom", "state": "on"},
                "invalid_item",
                {"entity_id": "light.kitchen", "state": "off"},
            ]
            client = HomeAssistantClient(ha_config)
            result = await client.list_states()
            assert len(result) == 2
            assert all(isinstance(item, dict) for item in result)

    async def test_list_entities_no_filter(self, ha_config):
        """Test listing entities without domain filter."""
        with patch.object(HomeAssistantClient, "list_states", new_callable=AsyncMock) as mock_list_states:
            mock_list_states.return_value = [
                {"entity_id": "light.bedroom"},
                {"entity_id": "switch.fan"},
            ]
            client = HomeAssistantClient(ha_config)
            result = await client.list_entities()
            assert len(result) == 2

    async def test_list_entities_with_domain_filter(self, ha_config):
        """Test listing entities with domain filter."""
        with patch.object(HomeAssistantClient, "list_states", new_callable=AsyncMock) as mock_list_states:
            mock_list_states.return_value = [
                {"entity_id": "light.bedroom"},
                {"entity_id": "light.kitchen"},
                {"entity_id": "switch.fan"},
            ]
            client = HomeAssistantClient(ha_config)
            result = await client.list_entities(domain="light")
            assert len(result) == 2
            assert all(item["entity_id"].startswith("light.") for item in result)

    async def test_close(self, ha_config):
        """Test closing the client."""
        client = HomeAssistantClient(ha_config)
        assert not client._closed
        await client.close()
        assert client._closed

    async def test_close_idempotent(self, ha_config):
        """Test that close() can be called multiple times safely."""
        client = HomeAssistantClient(ha_config)
        await client.close()
        await client.close()  # Should not raise
        assert client._closed


class TestHomeAssistantLightControl:
    """Test Home Assistant light control methods."""

    async def test_turn_on_light_basic(self, ha_config):
        """Test turning on a light without additional parameters."""
        with patch.object(HomeAssistantClient, "call_service", new_callable=AsyncMock) as mock_call:
            client = HomeAssistantClient(ha_config)
            result = await client.set_light_state(["light.bedroom"], on=True)
            mock_call.assert_called_once_with(
                "light",
                "turn_on",
                {"entity_id": ["light.bedroom"]},
            )
            assert result == ["light.bedroom"]

    async def test_turn_off_light(self, ha_config):
        """Test turning off a light."""
        with patch.object(HomeAssistantClient, "call_service", new_callable=AsyncMock) as mock_call:
            client = HomeAssistantClient(ha_config)
            result = await client.set_light_state(["light.bedroom"], on=False)
            mock_call.assert_called_once_with(
                "light",
                "turn_off",
                {"entity_id": ["light.bedroom"]},
            )
            assert result == ["light.bedroom"]

    async def test_turn_on_light_with_brightness(self, ha_config):
        """Test turning on a light with brightness."""
        with patch.object(HomeAssistantClient, "call_service", new_callable=AsyncMock) as mock_call:
            client = HomeAssistantClient(ha_config)
            await client.set_light_state(
                ["light.bedroom"],
                on=True,
                brightness_pct=50.0,
            )
            call_args = mock_call.call_args[0][2]
            assert "brightness" in call_args
            # 50% brightness = 127.5 -> 128 (0-255 scale, rounds up)
            assert call_args["brightness"] == 128

    async def test_turn_on_light_with_color_temp(self, ha_config):
        """Test turning on a light with color temperature."""
        with patch.object(HomeAssistantClient, "call_service", new_callable=AsyncMock) as mock_call:
            client = HomeAssistantClient(ha_config)
            await client.set_light_state(
                ["light.bedroom"],
                on=True,
                color_temp_mired=400,
            )
            call_args = mock_call.call_args[0][2]
            assert call_args["color_temp"] == 400

    async def test_turn_on_light_with_rgb_color(self, ha_config):
        """Test turning on a light with RGB color."""
        with patch.object(HomeAssistantClient, "call_service", new_callable=AsyncMock) as mock_call:
            client = HomeAssistantClient(ha_config)
            await client.set_light_state(
                ["light.bedroom"],
                on=True,
                rgb_color=(255, 128, 64),
            )
            call_args = mock_call.call_args[0][2]
            assert call_args["rgb_color"] == [255, 128, 64]

    async def test_turn_on_light_with_transition(self, ha_config):
        """Test turning on a light with transition."""
        with patch.object(HomeAssistantClient, "call_service", new_callable=AsyncMock) as mock_call:
            client = HomeAssistantClient(ha_config)
            await client.set_light_state(
                ["light.bedroom"],
                on=True,
                transition=2.5,
            )
            call_args = mock_call.call_args[0][2]
            assert call_args["transition"] == 2.5

    async def test_set_light_state_multiple_entities(self, ha_config):
        """Test controlling multiple lights at once."""
        with patch.object(HomeAssistantClient, "call_service", new_callable=AsyncMock) as mock_call:
            client = HomeAssistantClient(ha_config)
            result = await client.set_light_state(
                ["light.bedroom", "light.kitchen"],
                on=True,
            )
            call_args = mock_call.call_args[0][2]
            assert call_args["entity_id"] == ["light.bedroom", "light.kitchen"]
            assert result == ["light.bedroom", "light.kitchen"]

    async def test_set_light_state_filters_empty_ids(self, ha_config):
        """Test that empty entity IDs are filtered out."""
        with patch.object(HomeAssistantClient, "call_service", new_callable=AsyncMock) as mock_call:
            client = HomeAssistantClient(ha_config)
            result = await client.set_light_state(
                ["light.bedroom", "", "light.kitchen"],
                on=True,
            )
            call_args = mock_call.call_args[0][2]
            assert call_args["entity_id"] == ["light.bedroom", "light.kitchen"]

    async def test_set_light_state_empty_list(self, ha_config):
        """Test that empty entity list returns immediately."""
        with patch.object(HomeAssistantClient, "call_service", new_callable=AsyncMock) as mock_call:
            client = HomeAssistantClient(ha_config)
            result = await client.set_light_state([], on=True)
            mock_call.assert_not_called()
            assert result == []


class TestHomeAssistantSceneControl:
    """Test Home Assistant scene control."""

    async def test_activate_scene(self, ha_config):
        """Test activating a scene."""
        with patch.object(HomeAssistantClient, "call_service", new_callable=AsyncMock) as mock_call:
            client = HomeAssistantClient(ha_config)
            await client.activate_scene("scene.movie_time")
            mock_call.assert_called_once_with(
                "scene",
                "turn_on",
                {"entity_id": "scene.movie_time"},
            )

    async def test_activate_scene_empty_id(self, ha_config):
        """Test that empty scene ID is ignored."""
        with patch.object(HomeAssistantClient, "call_service", new_callable=AsyncMock) as mock_call:
            client = HomeAssistantClient(ha_config)
            await client.activate_scene("")
            mock_call.assert_not_called()


class TestHomeAssistantServiceCalls:
    """Test Home Assistant service calls."""

    async def test_call_service_with_data(self, ha_config):
        """Test calling a service with data."""
        with patch.object(HomeAssistantClient, "_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = {}
            client = HomeAssistantClient(ha_config)
            await client.call_service(
                "media_player",
                "play_media",
                {"entity_id": "media_player.living_room", "media_content_id": "123"},
            )
            mock_request.assert_called_once_with(
                "POST",
                "/api/services/media_player/play_media",
                json={"entity_id": "media_player.living_room", "media_content_id": "123"},
            )

    async def test_call_service_without_data(self, ha_config):
        """Test calling a service without data."""
        with patch.object(HomeAssistantClient, "_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = {}
            client = HomeAssistantClient(ha_config)
            await client.call_service("homeassistant", "restart")
            mock_request.assert_called_once_with(
                "POST",
                "/api/services/homeassistant/restart",
                json={},
            )


class TestHomeAssistantAssist:
    """Test Home Assistant Assist API."""

    async def test_assist_text_basic(self, ha_config):
        """Test basic text assist."""
        with patch.object(HomeAssistantClient, "_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = {"response": {"speech": {"plain": {"speech": "Hello"}}}}
            client = HomeAssistantClient(ha_config)
            result = await client.assist_text("turn on the lights")
            mock_request.assert_called_once()
            call_args = mock_request.call_args
            assert call_args[0][0] == "POST"
            assert call_args[0][1] == "/api/conversation/process"
            assert call_args[1]["json"]["text"] == "turn on the lights"

    async def test_assist_text_with_pipeline_id(self, ha_config):
        """Test assist with pipeline ID."""
        with patch.object(HomeAssistantClient, "_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = {}
            client = HomeAssistantClient(ha_config)
            await client.assist_text(
                "turn on the lights",
                pipeline_id="test_pipeline_123",
            )
            call_args = mock_request.call_args[1]["json"]
            assert call_args["pipeline_id"] == "test_pipeline_123"

    async def test_assist_text_with_language(self, ha_config):
        """Test assist with language."""
        with patch.object(HomeAssistantClient, "_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = {}
            client = HomeAssistantClient(ha_config)
            await client.assist_text("turn on the lights", language="en")
            call_args = mock_request.call_args[1]["json"]
            assert call_args["language"] == "en"

    async def test_assist_text_with_conversation_id(self):
        """Test assist with conversation ID."""
        # Create config with assist_pipeline set
        config = HomeAssistantConfig(
            base_url="http://homeassistant.local:8123",
            token="test_token_123",
            verify_ssl=True,
            assist_pipeline="default_pipeline",
            wake_endpoint=None,
            stt_endpoint=None,
            tts_endpoint=None,
            timer_entity=None,
            reminder_service=None,
            presence_entity=None,
        )
        with patch.object(HomeAssistantClient, "_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = {}
            client = HomeAssistantClient(config)
            await client.assist_text(
                "turn on the lights",
                conversation_id="conv_123",
            )
            call_args = mock_request.call_args[1]["json"]
            assert call_args["conversation_id"] == "conv_123"


class TestHomeAssistantErrorHandling:
    """Test Home Assistant error handling."""

    async def test_auth_error_on_401(self, ha_config):
        """Test that 401 response raises HomeAssistantAuthError."""
        with patch.object(HomeAssistantClient, "_request", new_callable=AsyncMock) as mock_request:
            mock_request.side_effect = HomeAssistantAuthError("Unauthorized")
            client = HomeAssistantClient(ha_config)
            with pytest.raises(HomeAssistantAuthError):
                await client.get_info()

    async def test_generic_error(self, ha_config):
        """Test that other errors raise HomeAssistantError."""
        with patch.object(HomeAssistantClient, "_request", new_callable=AsyncMock) as mock_request:
            mock_request.side_effect = HomeAssistantError("Connection failed")
            client = HomeAssistantClient(ha_config)
            with pytest.raises(HomeAssistantError):
                await client.get_info()


def test_brightness_pct_to_value():
    """Test brightness percentage conversion."""
    from pulse.assistant.home_assistant import _brightness_pct_to_value

    assert _brightness_pct_to_value(0.0) == 0
    assert _brightness_pct_to_value(50.0) == 128  # Rounds up from 127.5
    assert _brightness_pct_to_value(100.0) == 255
    assert _brightness_pct_to_value(-10.0) == 0  # Clamped to 0
    assert _brightness_pct_to_value(150.0) == 255  # Clamped to 255
