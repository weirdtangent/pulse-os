"""Tests for Home Assistant client."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest
from pulse.assistant.config import HomeAssistantConfig
from pulse.assistant.home_assistant import (
    HomeAssistantAuthError,
    HomeAssistantClient,
    HomeAssistantError,
    _brightness_pct_to_value,
    kelvin_to_mired,
    verify_home_assistant_access,
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
            await client.set_light_state(
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
            await client.assist_text("turn on the lights")
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
    assert _brightness_pct_to_value(0.0) == 0
    assert _brightness_pct_to_value(50.0) == 128  # Rounds up from 127.5
    assert _brightness_pct_to_value(100.0) == 255
    assert _brightness_pct_to_value(-10.0) == 0  # Clamped to 0
    assert _brightness_pct_to_value(150.0) == 255  # Clamped to 255


def test_brightness_pct_to_value_invalid():
    """Test brightness conversion with non-numeric input."""
    assert _brightness_pct_to_value("not_a_number") == 0
    assert _brightness_pct_to_value(None) == 0


def test_brightness_pct_to_value_edge_cases():
    """Test brightness conversion edge cases."""
    assert _brightness_pct_to_value(1.0) == 3  # 1% of 255 = 2.55 -> rounds to 3
    assert _brightness_pct_to_value(99.0) == 252  # 99% of 255 = 252.45 -> rounds to 252


# ============================================================================
# kelvin_to_mired Tests
# ============================================================================


class TestKelvinToMired:
    """Test Kelvin to mired conversion."""

    def test_normal_conversion(self):
        """Test standard Kelvin to mired conversion."""
        # 2700K -> 370 mireds
        assert kelvin_to_mired(2700) == 370
        # 6500K -> 154 mireds
        assert kelvin_to_mired(6500) == 154

    def test_zero_kelvin(self):
        """Test that zero Kelvin returns 0."""
        assert kelvin_to_mired(0) == 0

    def test_negative_kelvin(self):
        """Test that negative Kelvin returns 0."""
        assert kelvin_to_mired(-100) == 0

    def test_invalid_input(self):
        """Test that non-numeric input returns 0."""
        assert kelvin_to_mired("invalid") == 0
        assert kelvin_to_mired(None) == 0

    def test_float_input(self):
        """Test float Kelvin value."""
        result = kelvin_to_mired(4000.0)
        assert result == 250  # 1_000_000 / 4000 = 250


# ============================================================================
# _request Method Tests
# ============================================================================


class TestHomeAssistantRequest:
    """Test HomeAssistantClient._request method directly."""

    async def test_request_json_response(self, ha_config):
        """Test _request returns parsed JSON for application/json responses."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "application/json"}
        mock_response.json.return_value = {"result": "ok"}

        client = HomeAssistantClient(ha_config)
        client._client = AsyncMock()
        client._client.request = AsyncMock(return_value=mock_response)

        result = await client._request("GET", "/api/test")
        assert result == {"result": "ok"}
        client._client.request.assert_called_once_with("GET", "/api/test")

    async def test_request_text_response(self, ha_config):
        """Test _request returns text for non-JSON responses."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "text/plain"}
        mock_response.text = "OK"

        client = HomeAssistantClient(ha_config)
        client._client = AsyncMock()
        client._client.request = AsyncMock(return_value=mock_response)

        result = await client._request("GET", "/api/test")
        assert result == "OK"

    async def test_request_401_raises_auth_error(self, ha_config):
        """Test _request raises HomeAssistantAuthError on 401."""
        mock_response = Mock()
        mock_response.status_code = 401

        client = HomeAssistantClient(ha_config)
        client._client = AsyncMock()
        client._client.request = AsyncMock(return_value=mock_response)

        with pytest.raises(HomeAssistantAuthError, match="rejected the token"):
            await client._request("GET", "/api/test")

    async def test_request_403_raises_auth_error(self, ha_config):
        """Test _request raises HomeAssistantAuthError on 403."""
        mock_response = Mock()
        mock_response.status_code = 403

        client = HomeAssistantClient(ha_config)
        client._client = AsyncMock()
        client._client.request = AsyncMock(return_value=mock_response)

        with pytest.raises(HomeAssistantAuthError, match="rejected the token"):
            await client._request("GET", "/api/test")

    async def test_request_400_raises_error(self, ha_config):
        """Test _request raises HomeAssistantError on 400+ status codes."""
        mock_response = Mock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"

        client = HomeAssistantClient(ha_config)
        client._client = AsyncMock()
        client._client.request = AsyncMock(return_value=mock_response)

        with pytest.raises(HomeAssistantError, match="error 500"):
            await client._request("GET", "/api/test")

    async def test_request_passes_kwargs(self, ha_config):
        """Test _request forwards kwargs to httpx client."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "application/json"}
        mock_response.json.return_value = {}

        client = HomeAssistantClient(ha_config)
        client._client = AsyncMock()
        client._client.request = AsyncMock(return_value=mock_response)

        await client._request("POST", "/api/test", json={"key": "value"})
        client._client.request.assert_called_once_with("POST", "/api/test", json={"key": "value"})


# ============================================================================
# __post_init__ PermissionError Fallback Tests
# ============================================================================


class TestHomeAssistantClientInitPermissionError:
    """Test PermissionError fallback during client initialization."""

    def test_permission_error_fallback(self):
        """Test that PermissionError triggers insecure SSL fallback."""
        config = HomeAssistantConfig(
            base_url="http://homeassistant.local:8123",
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

        with patch("httpx.AsyncClient") as mock_async_client:
            # First call raises PermissionError, second succeeds
            mock_client_instance = Mock()
            mock_async_client.side_effect = [PermissionError("cert access denied"), mock_client_instance]

            client = HomeAssistantClient(config)

            assert mock_async_client.call_count == 2
            # Second call should have verify=False
            second_call_kwargs = mock_async_client.call_args_list[1][1]
            assert second_call_kwargs["verify"] is False
            assert client._client is mock_client_instance


# ============================================================================
# list_config_entries Tests
# ============================================================================


class TestHomeAssistantConfigEntries:
    """Test list_config_entries method."""

    async def test_list_config_entries_all(self, ha_config):
        """Test listing all config entries."""
        with patch.object(HomeAssistantClient, "_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = [
                {"domain": "hue", "entry_id": "1"},
                {"domain": "zwave", "entry_id": "2"},
            ]
            client = HomeAssistantClient(ha_config)
            result = await client.list_config_entries()
            assert len(result) == 2
            mock_request.assert_called_once_with("GET", "/api/config/config_entries/entry")

    async def test_list_config_entries_filtered_by_domain(self, ha_config):
        """Test listing config entries filtered by domain."""
        with patch.object(HomeAssistantClient, "_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = [
                {"domain": "hue", "entry_id": "1"},
                {"domain": "zwave", "entry_id": "2"},
                {"domain": "hue", "entry_id": "3"},
            ]
            client = HomeAssistantClient(ha_config)
            result = await client.list_config_entries(domain="hue")
            assert len(result) == 2
            assert all(e["domain"] == "hue" for e in result)

    async def test_list_config_entries_non_list_response(self, ha_config):
        """Test list_config_entries when response is not a list."""
        with patch.object(HomeAssistantClient, "_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = "unexpected"
            client = HomeAssistantClient(ha_config)
            result = await client.list_config_entries()
            assert result == []

    async def test_list_config_entries_filters_non_dict(self, ha_config):
        """Test that list_config_entries filters out non-dict items."""
        with patch.object(HomeAssistantClient, "_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = [
                {"domain": "hue", "entry_id": "1"},
                "invalid_item",
                42,
            ]
            client = HomeAssistantClient(ha_config)
            result = await client.list_config_entries()
            assert len(result) == 1


# ============================================================================
# list_states Non-List Response Test
# ============================================================================


class TestHomeAssistantListStatesEdgeCases:
    """Test edge cases for list_states."""

    async def test_list_states_non_list_response(self, ha_config):
        """Test list_states when response is not a list."""
        with patch.object(HomeAssistantClient, "_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = "unexpected string"
            client = HomeAssistantClient(ha_config)
            result = await client.list_states()
            assert result == []


# ============================================================================
# verify_home_assistant_access Tests
# ============================================================================


class TestVerifyHomeAssistantAccess:
    """Test verify_home_assistant_access function."""

    async def test_verify_success(self, ha_config):
        """Test successful access verification."""
        with patch.object(HomeAssistantClient, "_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = {"version": "2024.1.0"}
            result = await verify_home_assistant_access(ha_config)
            assert result == {"version": "2024.1.0"}

    async def test_verify_closes_client_on_success(self, ha_config):
        """Test that client is closed after successful verification."""
        with patch.object(HomeAssistantClient, "_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = {"version": "2024.1.0"}
            with patch.object(HomeAssistantClient, "close", new_callable=AsyncMock) as mock_close:
                await verify_home_assistant_access(ha_config)
                mock_close.assert_called_once()

    async def test_verify_closes_client_on_failure(self, ha_config):
        """Test that client is closed even when verification fails."""
        with patch.object(HomeAssistantClient, "_request", new_callable=AsyncMock) as mock_request:
            mock_request.side_effect = HomeAssistantAuthError("bad token")
            with patch.object(HomeAssistantClient, "close", new_callable=AsyncMock) as mock_close:
                with pytest.raises(HomeAssistantAuthError):
                    await verify_home_assistant_access(ha_config)
                mock_close.assert_called_once()

    async def test_verify_custom_timeout(self, ha_config):
        """Test that custom timeout is passed through."""
        with patch.object(HomeAssistantClient, "__post_init__") as mock_init:
            mock_init.return_value = None
            with patch.object(HomeAssistantClient, "get_info", new_callable=AsyncMock) as mock_info:
                mock_info.return_value = {"version": "2024.1.0"}
                with patch.object(HomeAssistantClient, "close", new_callable=AsyncMock):
                    result = await verify_home_assistant_access(ha_config, timeout=15.0)
                    assert result == {"version": "2024.1.0"}


# ============================================================================
# set_light_state Edge Cases
# ============================================================================


class TestSetLightStateEdgeCases:
    """Test edge cases for set_light_state."""

    async def test_turn_on_with_all_params(self, ha_config):
        """Test turning on light with all parameters combined."""
        with patch.object(HomeAssistantClient, "call_service", new_callable=AsyncMock) as mock_call:
            client = HomeAssistantClient(ha_config)
            result = await client.set_light_state(
                ["light.bedroom"],
                on=True,
                brightness_pct=75.0,
                color_temp_mired=350,
                rgb_color=(255, 0, 0),
                transition=1.5,
            )
            call_args = mock_call.call_args[0][2]
            assert call_args["brightness"] == 191
            assert call_args["color_temp"] == 350
            assert call_args["rgb_color"] == [255, 0, 0]
            assert call_args["transition"] == 1.5
            assert result == ["light.bedroom"]

    async def test_turn_off_ignores_brightness_and_color(self, ha_config):
        """Test that turn_off does not include brightness/color params."""
        with patch.object(HomeAssistantClient, "call_service", new_callable=AsyncMock) as mock_call:
            client = HomeAssistantClient(ha_config)
            await client.set_light_state(
                ["light.bedroom"],
                on=False,
                brightness_pct=50.0,
                color_temp_mired=400,
                rgb_color=(0, 255, 0),
            )
            call_args = mock_call.call_args[0][2]
            assert "brightness" not in call_args
            assert "color_temp" not in call_args
            assert "rgb_color" not in call_args

    async def test_turn_off_with_transition(self, ha_config):
        """Test turn_off includes transition when provided."""
        with patch.object(HomeAssistantClient, "call_service", new_callable=AsyncMock) as mock_call:
            client = HomeAssistantClient(ha_config)
            await client.set_light_state(
                ["light.bedroom"],
                on=False,
                transition=3.0,
            )
            call_args = mock_call.call_args[0][2]
            assert call_args["transition"] == 3.0

    async def test_invalid_transition_ignored(self, ha_config):
        """Test that invalid transition value is silently ignored."""
        with patch.object(HomeAssistantClient, "call_service", new_callable=AsyncMock) as mock_call:
            client = HomeAssistantClient(ha_config)
            await client.set_light_state(
                ["light.bedroom"],
                on=True,
                transition="not_a_number",
            )
            call_args = mock_call.call_args[0][2]
            assert "transition" not in call_args

    async def test_negative_transition_clamped(self, ha_config):
        """Test that negative transition is clamped to 0."""
        with patch.object(HomeAssistantClient, "call_service", new_callable=AsyncMock) as mock_call:
            client = HomeAssistantClient(ha_config)
            await client.set_light_state(
                ["light.bedroom"],
                on=True,
                transition=-5.0,
            )
            call_args = mock_call.call_args[0][2]
            assert call_args["transition"] == 0.0

    async def test_all_empty_entity_ids_filtered(self, ha_config):
        """Test that a list of only empty strings returns empty."""
        with patch.object(HomeAssistantClient, "call_service", new_callable=AsyncMock) as mock_call:
            client = HomeAssistantClient(ha_config)
            result = await client.set_light_state(["", "", ""], on=True)
            mock_call.assert_not_called()
            assert result == []


# ============================================================================
# assist_text with Default Pipeline Tests
# ============================================================================


class TestAssistTextPipeline:
    """Test assist_text with configured default pipeline."""

    async def test_assist_text_uses_config_pipeline(self, ha_config_with_pipeline):
        """Test that assist_text uses pipeline from config when none specified."""
        with patch.object(HomeAssistantClient, "_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = {}
            client = HomeAssistantClient(ha_config_with_pipeline)
            await client.assist_text("hello")
            call_args = mock_request.call_args[1]["json"]
            assert call_args["pipeline_id"] == "test_pipeline"

    async def test_assist_text_explicit_pipeline_overrides_config(self, ha_config_with_pipeline):
        """Test that explicit pipeline_id overrides config pipeline."""
        with patch.object(HomeAssistantClient, "_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = {}
            client = HomeAssistantClient(ha_config_with_pipeline)
            await client.assist_text("hello", pipeline_id="custom_pipeline")
            call_args = mock_request.call_args[1]["json"]
            assert call_args["pipeline_id"] == "custom_pipeline"

    async def test_assist_text_no_pipeline_no_conversation_id(self, ha_config):
        """Test that without pipeline, conversation_id is not included."""
        with patch.object(HomeAssistantClient, "_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = {}
            client = HomeAssistantClient(ha_config)
            await client.assist_text("hello")
            call_args = mock_request.call_args[1]["json"]
            assert "pipeline_id" not in call_args
            assert "conversation_id" not in call_args


# ============================================================================
# _resolve_pipeline_id Tests
# ============================================================================


class TestResolvePipelineId:
    """Test _resolve_pipeline_id WebSocket helper."""

    async def test_uuid_like_string_returned_as_is(self, ha_config):
        """Test that a UUID-like string is returned without querying."""
        client = HomeAssistantClient(ha_config)
        ws = AsyncMock()
        # A long alphanumeric string with dashes (like a UUID)
        pipeline_id = "01234567-89ab-cdef-0123-456789abcdef"
        result = await client._resolve_pipeline_id(ws, pipeline_id)
        assert result == pipeline_id
        ws.send.assert_not_called()

    async def test_pipeline_name_resolved_to_id(self, ha_config):
        """Test that a pipeline name is resolved by listing pipelines."""
        client = HomeAssistantClient(ha_config)
        ws = AsyncMock()
        ws.recv = AsyncMock(
            return_value=json.dumps(
                {
                    "type": "result",
                    "success": True,
                    "result": {
                        "pipelines": [
                            {"id": "abc123def456789012345", "name": "My Pipeline"},
                            {"id": "xyz789", "name": "Other Pipeline"},
                        ]
                    },
                }
            )
        )
        result = await client._resolve_pipeline_id(ws, "My Pipeline")
        assert result == "abc123def456789012345"
        ws.send.assert_called_once()
        sent = json.loads(ws.send.call_args[0][0])
        assert sent["type"] == "assist_pipeline/pipeline/list"

    async def test_pipeline_name_not_found_raises(self, ha_config):
        """Test that a missing pipeline name raises HomeAssistantError."""
        client = HomeAssistantClient(ha_config)
        ws = AsyncMock()
        ws.recv = AsyncMock(
            return_value=json.dumps(
                {
                    "type": "result",
                    "success": True,
                    "result": {
                        "pipelines": [
                            {"id": "abc123", "name": "Other Pipeline"},
                        ]
                    },
                }
            )
        )
        with pytest.raises(HomeAssistantError, match="not found"):
            await client._resolve_pipeline_id(ws, "Nonexistent")

    async def test_pipeline_list_failure_raises(self, ha_config):
        """Test that a failed list response raises HomeAssistantError."""
        client = HomeAssistantClient(ha_config)
        ws = AsyncMock()
        ws.recv = AsyncMock(
            return_value=json.dumps(
                {
                    "type": "result",
                    "success": False,
                    "error": {"code": "unknown", "message": "fail"},
                }
            )
        )
        with pytest.raises(HomeAssistantError, match="not found"):
            await client._resolve_pipeline_id(ws, "My Pipeline")


# ============================================================================
# assist_audio Tests
# ============================================================================


def _make_ha_config(**overrides):
    """Helper to create HomeAssistantConfig with defaults."""
    defaults = dict(
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
    defaults.update(overrides)
    return HomeAssistantConfig(**defaults)


def _ws_messages(*messages):
    """Create an AsyncMock recv that yields JSON messages in order.

    Each element can be a dict (auto-serialised to JSON), a str, or bytes.
    """
    encoded = []
    for m in messages:
        if isinstance(m, dict):
            encoded.append(json.dumps(m))
        else:
            encoded.append(m)
    mock = AsyncMock(side_effect=encoded)
    return mock


class TestAssistAudio:
    """Test assist_audio WebSocket method."""

    async def test_websockets_not_installed(self, ha_config):
        """Test that missing websockets library raises."""
        client = HomeAssistantClient(ha_config)
        with patch("pulse.assistant.home_assistant.websockets", None):
            with pytest.raises(HomeAssistantError, match="websockets library is required"):
                await client.assist_audio(b"\x00" * 100, sample_rate=16000, sample_width=2, channels=1)

    async def test_no_base_url_raises(self):
        """Test that missing base_url raises."""
        config = _make_ha_config()
        client = HomeAssistantClient(config)
        # Temporarily clear base_url after init
        client.config = _make_ha_config(base_url="")
        # websockets must be non-None for this path
        mock_ws_module = Mock()
        with patch("pulse.assistant.home_assistant.websockets", mock_ws_module):
            with pytest.raises(HomeAssistantError, match="base_url is not configured"):
                await client.assist_audio(b"\x00", sample_rate=16000, sample_width=2, channels=1)

    async def test_auth_failure_raises(self, ha_config):
        """Test that auth failure (non auth_ok) raises."""
        mock_ws = AsyncMock()
        mock_ws.recv = _ws_messages(
            {"type": "auth_required"},
            {"type": "auth_invalid", "message": "bad token"},
        )
        mock_ws.send = AsyncMock()

        mock_connect = AsyncMock()
        mock_connect.__aenter__ = AsyncMock(return_value=mock_ws)
        mock_connect.__aexit__ = AsyncMock(return_value=False)

        mock_ws_module = Mock()
        mock_ws_module.connect = Mock(return_value=mock_connect)

        client = HomeAssistantClient(ha_config)
        with patch("pulse.assistant.home_assistant.websockets", mock_ws_module):
            with pytest.raises(HomeAssistantAuthError, match="authentication failed"):
                await client.assist_audio(b"\x00", sample_rate=16000, sample_width=2, channels=1)

    async def test_unexpected_auth_required_raises(self, ha_config):
        """Test that missing auth_required raises."""
        mock_ws = AsyncMock()
        mock_ws.recv = _ws_messages(
            {"type": "something_else"},
        )
        mock_ws.send = AsyncMock()

        mock_connect = AsyncMock()
        mock_connect.__aenter__ = AsyncMock(return_value=mock_ws)
        mock_connect.__aexit__ = AsyncMock(return_value=False)

        mock_ws_module = Mock()
        mock_ws_module.connect = Mock(return_value=mock_connect)

        client = HomeAssistantClient(ha_config)
        with patch("pulse.assistant.home_assistant.websockets", mock_ws_module):
            with pytest.raises(HomeAssistantError, match="Expected auth_required"):
                await client.assist_audio(b"\x00", sample_rate=16000, sample_width=2, channels=1)

    async def test_pipeline_command_failure_raises(self, ha_config):
        """Test that a failed pipeline run command raises."""
        mock_ws = AsyncMock()
        mock_ws.recv = _ws_messages(
            {"type": "auth_required"},
            {"type": "auth_ok"},
            # Response to run command: failure
            {"type": "result", "success": False, "error": {"code": "unknown", "message": "boom"}},
        )
        mock_ws.send = AsyncMock()

        mock_connect = AsyncMock()
        mock_connect.__aenter__ = AsyncMock(return_value=mock_ws)
        mock_connect.__aexit__ = AsyncMock(return_value=False)

        mock_ws_module = Mock()
        mock_ws_module.connect = Mock(return_value=mock_connect)

        client = HomeAssistantClient(ha_config)
        with patch("pulse.assistant.home_assistant.websockets", mock_ws_module):
            with pytest.raises(HomeAssistantError, match="pipeline command failed"):
                await client.assist_audio(b"\x00", sample_rate=16000, sample_width=2, channels=1)

    async def test_unexpected_response_type_raises(self, ha_config):
        """Test that an unexpected WS message type raises."""
        mock_ws = AsyncMock()
        mock_ws.recv = _ws_messages(
            {"type": "auth_required"},
            {"type": "auth_ok"},
            # Unexpected message type while waiting for run-start
            {"type": "pong"},
        )
        mock_ws.send = AsyncMock()

        mock_connect = AsyncMock()
        mock_connect.__aenter__ = AsyncMock(return_value=mock_ws)
        mock_connect.__aexit__ = AsyncMock(return_value=False)

        mock_ws_module = Mock()
        mock_ws_module.connect = Mock(return_value=mock_connect)

        client = HomeAssistantClient(ha_config)
        with patch("pulse.assistant.home_assistant.websockets", mock_ws_module):
            with pytest.raises(HomeAssistantError, match="Unexpected response"):
                await client.assist_audio(b"\x00", sample_rate=16000, sample_width=2, channels=1)

    async def test_error_event_raises(self, ha_config):
        """Test that an error event during collection raises."""
        mock_ws = AsyncMock()
        mock_ws.recv = _ws_messages(
            {"type": "auth_required"},
            {"type": "auth_ok"},
            # Successful result
            {"type": "result", "success": True},
            # run-start event
            {
                "type": "event",
                "event": {
                    "type": "run-start",
                    "runner_data": {"stt_binary_handler_id": 1},
                },
            },
            # stt-start event
            {"type": "event", "event": {"type": "stt-start"}},
            # error event
            {"type": "event", "event": {"type": "error", "data": {"message": "something broke"}}},
        )
        mock_ws.send = AsyncMock()

        mock_connect = AsyncMock()
        mock_connect.__aenter__ = AsyncMock(return_value=mock_ws)
        mock_connect.__aexit__ = AsyncMock(return_value=False)

        mock_ws_module = Mock()
        mock_ws_module.connect = Mock(return_value=mock_connect)

        client = HomeAssistantClient(ha_config)
        with patch("pulse.assistant.home_assistant.websockets", mock_ws_module):
            with pytest.raises(HomeAssistantError, match="pipeline error"):
                await client.assist_audio(b"\x00", sample_rate=16000, sample_width=2, channels=1)

    async def test_timeout_during_event_collection(self, ha_config):
        """Test that timeout during event collection raises."""
        mock_ws = AsyncMock()

        recv_responses = [
            json.dumps({"type": "auth_required"}),
            json.dumps({"type": "auth_ok"}),
            json.dumps({"type": "result", "success": True}),
            json.dumps(
                {
                    "type": "event",
                    "event": {
                        "type": "run-start",
                        "runner_data": {"stt_binary_handler_id": 1},
                    },
                }
            ),
            json.dumps({"type": "event", "event": {"type": "stt-start"}}),
            # Timeout on next recv
            TimeoutError("timed out"),
        ]

        async def _recv_side_effect():
            val = recv_responses.pop(0)
            if isinstance(val, Exception):
                raise val
            return val

        mock_ws.recv = AsyncMock(side_effect=_recv_side_effect)
        mock_ws.send = AsyncMock()

        mock_connect = AsyncMock()
        mock_connect.__aenter__ = AsyncMock(return_value=mock_ws)
        mock_connect.__aexit__ = AsyncMock(return_value=False)

        mock_ws_module = Mock()
        mock_ws_module.connect = Mock(return_value=mock_connect)

        client = HomeAssistantClient(ha_config, timeout=0.1)
        with patch("pulse.assistant.home_assistant.websockets", mock_ws_module):
            with pytest.raises(HomeAssistantError, match="timeout"):
                await client.assist_audio(b"\x00", sample_rate=16000, sample_width=2, channels=1)

    async def test_no_stt_binary_handler_id_raises(self, ha_config):
        """Test that missing stt_binary_handler_id raises."""
        mock_ws = AsyncMock()
        mock_ws.recv = _ws_messages(
            {"type": "auth_required"},
            {"type": "auth_ok"},
            {"type": "result", "success": True},
            {
                "type": "event",
                "event": {
                    "type": "run-start",
                    "runner_data": {},
                },
            },
        )
        mock_ws.send = AsyncMock()

        mock_connect = AsyncMock()
        mock_connect.__aenter__ = AsyncMock(return_value=mock_ws)
        mock_connect.__aexit__ = AsyncMock(return_value=False)

        mock_ws_module = Mock()
        mock_ws_module.connect = Mock(return_value=mock_connect)

        client = HomeAssistantClient(ha_config)
        with patch("pulse.assistant.home_assistant.websockets", mock_ws_module):
            with pytest.raises(HomeAssistantError, match="stt_binary_handler_id"):
                await client.assist_audio(b"\x00", sample_rate=16000, sample_width=2, channels=1)

    async def test_unexpected_stt_start_response_raises(self, ha_config):
        """Test that unexpected message instead of stt-start raises."""
        mock_ws = AsyncMock()
        mock_ws.recv = _ws_messages(
            {"type": "auth_required"},
            {"type": "auth_ok"},
            {"type": "result", "success": True},
            {
                "type": "event",
                "event": {
                    "type": "run-start",
                    "runner_data": {"stt_binary_handler_id": 1},
                },
            },
            # Not stt-start
            {"type": "result", "success": True},
        )
        mock_ws.send = AsyncMock()

        mock_connect = AsyncMock()
        mock_connect.__aenter__ = AsyncMock(return_value=mock_ws)
        mock_connect.__aexit__ = AsyncMock(return_value=False)

        mock_ws_module = Mock()
        mock_ws_module.connect = Mock(return_value=mock_connect)

        client = HomeAssistantClient(ha_config)
        with patch("pulse.assistant.home_assistant.websockets", mock_ws_module):
            with pytest.raises(HomeAssistantError, match="Unexpected response"):
                await client.assist_audio(b"\x00", sample_rate=16000, sample_width=2, channels=1)

    async def test_successful_full_pipeline(self, ha_config):
        """Test a successful full audio pipeline run."""
        mock_ws = AsyncMock()
        mock_ws.recv = _ws_messages(
            {"type": "auth_required"},
            {"type": "auth_ok"},
            {"type": "result", "success": True},
            {
                "type": "event",
                "event": {
                    "type": "run-start",
                    "runner_data": {"stt_binary_handler_id": 1},
                },
            },
            {"type": "event", "event": {"type": "stt-start"}},
            {"type": "event", "event": {"type": "stt-vad-start"}},
            {"type": "event", "event": {"type": "stt-vad-end"}},
            {
                "type": "event",
                "event": {
                    "type": "stt-end",
                    "stt_output": {"text": "turn on the lights"},
                },
            },
            {"type": "event", "event": {"type": "intent-start"}},
            {
                "type": "event",
                "event": {
                    "type": "intent-end",
                    "intent_output": {
                        "response": {
                            "speech": {
                                "plain": {"speech": "Turned on the lights"},
                            },
                        },
                    },
                },
            },
            {"type": "event", "event": {"type": "tts-start"}},
            {
                "type": "event",
                "event": {
                    "type": "tts-end",
                    "url": "http://ha.local/tts.mp3",
                },
            },
            {"type": "event", "event": {"type": "run-end"}},
        )
        mock_ws.send = AsyncMock()

        mock_connect = AsyncMock()
        mock_connect.__aenter__ = AsyncMock(return_value=mock_ws)
        mock_connect.__aexit__ = AsyncMock(return_value=False)

        mock_ws_module = Mock()
        mock_ws_module.connect = Mock(return_value=mock_connect)

        audio_data = b"\x00" * 8192
        client = HomeAssistantClient(ha_config)
        with patch("pulse.assistant.home_assistant.websockets", mock_ws_module):
            result = await client.assist_audio(audio_data, sample_rate=16000, sample_width=2, channels=1)

        assert result["stt_output"]["text"] == "turn on the lights"
        assert result["intent_input"]["text"] == "turn on the lights"
        # tts-end url overwrites the intent speech
        assert result["response"]["speech"]["plain"]["speech"] == "http://ha.local/tts.mp3"

        # Verify audio was sent in chunks with handler byte prefix
        send_calls = mock_ws.send.call_args_list
        # First call: auth, second: run command, then audio chunks, then end marker
        # Auth JSON
        auth_sent = json.loads(send_calls[0][0][0])
        assert auth_sent["type"] == "auth"
        # Run command JSON
        run_sent = json.loads(send_calls[1][0][0])
        assert run_sent["type"] == "assist_pipeline/run"
        assert run_sent["start_stage"] == "stt"
        # Audio chunks: 8192 bytes / 4096 = 2 chunks + 1 end marker
        assert isinstance(send_calls[2][0][0], bytes)
        assert send_calls[2][0][0][0] == 1  # handler byte
        assert len(send_calls[2][0][0]) == 4097  # handler byte + 4096 data
        assert isinstance(send_calls[4][0][0], bytes)
        assert send_calls[4][0][0] == bytes([1])  # end marker

    async def test_binary_messages_skipped_in_run_start_loop(self, ha_config):
        """Test that binary messages are skipped while waiting for run-start."""
        mock_ws = AsyncMock()

        recv_responses = [
            json.dumps({"type": "auth_required"}),
            json.dumps({"type": "auth_ok"}),
            # Binary message before result
            b"\x00\x01\x02",
            json.dumps({"type": "result", "success": True}),
            json.dumps(
                {
                    "type": "event",
                    "event": {
                        "type": "run-start",
                        "runner_data": {"stt_binary_handler_id": 1},
                    },
                }
            ),
            json.dumps({"type": "event", "event": {"type": "stt-start"}}),
            json.dumps({"type": "event", "event": {"type": "run-end"}}),
        ]

        mock_ws.recv = AsyncMock(side_effect=recv_responses)
        mock_ws.send = AsyncMock()

        mock_connect = AsyncMock()
        mock_connect.__aenter__ = AsyncMock(return_value=mock_ws)
        mock_connect.__aexit__ = AsyncMock(return_value=False)

        mock_ws_module = Mock()
        mock_ws_module.connect = Mock(return_value=mock_connect)

        client = HomeAssistantClient(ha_config)
        with patch("pulse.assistant.home_assistant.websockets", mock_ws_module):
            result = await client.assist_audio(b"\x00", sample_rate=16000, sample_width=2, channels=1)
        assert "stt_output" in result

    async def test_binary_messages_skipped_in_event_collection(self, ha_config):
        """Test that binary messages are skipped during event collection."""
        mock_ws = AsyncMock()

        recv_responses = [
            json.dumps({"type": "auth_required"}),
            json.dumps({"type": "auth_ok"}),
            json.dumps({"type": "result", "success": True}),
            json.dumps(
                {
                    "type": "event",
                    "event": {
                        "type": "run-start",
                        "runner_data": {"stt_binary_handler_id": 1},
                    },
                }
            ),
            json.dumps({"type": "event", "event": {"type": "stt-start"}}),
            # Binary message during collection
            b"\x01audio_data_here",
            json.dumps({"type": "event", "event": {"type": "run-end"}}),
        ]

        mock_ws.recv = AsyncMock(side_effect=recv_responses)
        mock_ws.send = AsyncMock()

        mock_connect = AsyncMock()
        mock_connect.__aenter__ = AsyncMock(return_value=mock_ws)
        mock_connect.__aexit__ = AsyncMock(return_value=False)

        mock_ws_module = Mock()
        mock_ws_module.connect = Mock(return_value=mock_connect)

        client = HomeAssistantClient(ha_config)
        with patch("pulse.assistant.home_assistant.websockets", mock_ws_module):
            result = await client.assist_audio(b"\x00", sample_rate=16000, sample_width=2, channels=1)
        assert "stt_output" in result

    async def test_pipeline_resolved_when_configured(self, ha_config_with_pipeline):
        """Test that pipeline name from config is resolved."""
        mock_ws = AsyncMock()

        recv_responses = [
            json.dumps({"type": "auth_required"}),
            json.dumps({"type": "auth_ok"}),
            # Pipeline list response (for _resolve_pipeline_id)
            json.dumps(
                {
                    "type": "result",
                    "success": True,
                    "result": {
                        "pipelines": [
                            {"id": "resolved_id_01234567890", "name": "test_pipeline"},
                        ]
                    },
                }
            ),
            json.dumps({"type": "result", "success": True}),
            json.dumps(
                {
                    "type": "event",
                    "event": {
                        "type": "run-start",
                        "runner_data": {"stt_binary_handler_id": 1},
                    },
                }
            ),
            json.dumps({"type": "event", "event": {"type": "stt-start"}}),
            json.dumps({"type": "event", "event": {"type": "run-end"}}),
        ]

        mock_ws.recv = AsyncMock(side_effect=recv_responses)
        mock_ws.send = AsyncMock()

        mock_connect = AsyncMock()
        mock_connect.__aenter__ = AsyncMock(return_value=mock_ws)
        mock_connect.__aexit__ = AsyncMock(return_value=False)

        mock_ws_module = Mock()
        mock_ws_module.connect = Mock(return_value=mock_connect)

        client = HomeAssistantClient(ha_config_with_pipeline)
        with patch("pulse.assistant.home_assistant.websockets", mock_ws_module):
            await client.assist_audio(b"\x00", sample_rate=16000, sample_width=2, channels=1)

        # Verify the run command includes the resolved pipeline ID
        send_calls = mock_ws.send.call_args_list
        # send[0]=auth, send[1]=pipeline list, send[2]=run command
        run_sent = json.loads(send_calls[2][0][0])
        assert run_sent["pipeline"] == "resolved_id_01234567890"

    async def test_https_url_uses_wss(self):
        """Test that https base_url is converted to wss."""
        config = _make_ha_config(base_url="https://ha.example.com")
        mock_ws = AsyncMock()
        mock_ws.recv = _ws_messages(
            {"type": "auth_required"},
            {"type": "auth_ok"},
            {"type": "result", "success": True},
            {
                "type": "event",
                "event": {
                    "type": "run-start",
                    "runner_data": {"stt_binary_handler_id": 1},
                },
            },
            {"type": "event", "event": {"type": "stt-start"}},
            {"type": "event", "event": {"type": "run-end"}},
        )
        mock_ws.send = AsyncMock()

        mock_connect = AsyncMock()
        mock_connect.__aenter__ = AsyncMock(return_value=mock_ws)
        mock_connect.__aexit__ = AsyncMock(return_value=False)

        mock_ws_module = Mock()
        mock_ws_module.connect = Mock(return_value=mock_connect)

        client = HomeAssistantClient(config)
        with patch("pulse.assistant.home_assistant.websockets", mock_ws_module):
            await client.assist_audio(b"\x00", sample_rate=16000, sample_width=2, channels=1)

        # Check the connect URL uses wss://
        connect_call = mock_ws_module.connect.call_args
        assert connect_call[0][0] == "wss://ha.example.com/api/websocket"

    async def test_tts_output_event_captures_url(self, ha_config):
        """Test that tts-output event URL is captured."""
        mock_ws = AsyncMock()
        mock_ws.recv = _ws_messages(
            {"type": "auth_required"},
            {"type": "auth_ok"},
            {"type": "result", "success": True},
            {
                "type": "event",
                "event": {
                    "type": "run-start",
                    "runner_data": {"stt_binary_handler_id": 1},
                },
            },
            {"type": "event", "event": {"type": "stt-start"}},
            {
                "type": "event",
                "event": {
                    "type": "tts-output",
                    "url": "http://ha.local/tts_output.wav",
                },
            },
            {"type": "event", "event": {"type": "run-end"}},
        )
        mock_ws.send = AsyncMock()

        mock_connect = AsyncMock()
        mock_connect.__aenter__ = AsyncMock(return_value=mock_ws)
        mock_connect.__aexit__ = AsyncMock(return_value=False)

        mock_ws_module = Mock()
        mock_ws_module.connect = Mock(return_value=mock_connect)

        client = HomeAssistantClient(ha_config)
        with patch("pulse.assistant.home_assistant.websockets", mock_ws_module):
            result = await client.assist_audio(b"\x00", sample_rate=16000, sample_width=2, channels=1)
        assert result["response"]["speech"]["plain"]["speech"] == "http://ha.local/tts_output.wav"

    async def test_run_with_language_param(self, ha_config):
        """Test that language parameter is included in run command."""
        mock_ws = AsyncMock()
        mock_ws.recv = _ws_messages(
            {"type": "auth_required"},
            {"type": "auth_ok"},
            {"type": "result", "success": True},
            {
                "type": "event",
                "event": {
                    "type": "run-start",
                    "runner_data": {"stt_binary_handler_id": 1},
                },
            },
            {"type": "event", "event": {"type": "stt-start"}},
            {"type": "event", "event": {"type": "run-end"}},
        )
        mock_ws.send = AsyncMock()

        mock_connect = AsyncMock()
        mock_connect.__aenter__ = AsyncMock(return_value=mock_ws)
        mock_connect.__aexit__ = AsyncMock(return_value=False)

        mock_ws_module = Mock()
        mock_ws_module.connect = Mock(return_value=mock_connect)

        client = HomeAssistantClient(ha_config)
        with patch("pulse.assistant.home_assistant.websockets", mock_ws_module):
            await client.assist_audio(
                b"\x00",
                sample_rate=16000,
                sample_width=2,
                channels=1,
                language="en",
            )

        send_calls = mock_ws.send.call_args_list
        run_sent = json.loads(send_calls[1][0][0])
        assert run_sent["language"] == "en"
