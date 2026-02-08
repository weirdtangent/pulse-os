"""Shared test fixtures and configuration for Pulse OS test suite.

This module provides reusable fixtures for common test scenarios including:
- Home Assistant client mocking
- MQTT broker/client mocking
- LLM provider mocking
- Configuration objects
- Async test utilities
"""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import AsyncMock, Mock

import httpx
import paho.mqtt.client as mqtt
import pytest
from pulse.assistant.config import HomeAssistantConfig, LLMConfig, MqttConfig

# ============================================================================
# Pytest Configuration
# ============================================================================


@pytest.fixture(scope="session")
def anyio_backend():
    """Configure anyio backend for async tests."""
    return "asyncio"


# ============================================================================
# Logging Fixtures
# ============================================================================


@pytest.fixture
def mock_logger():
    """Create a mock logger for testing.

    Returns a Mock with spec=logging.Logger to ensure only valid
    logger methods can be called.
    """
    return Mock(spec=logging.Logger)


# ============================================================================
# Home Assistant Fixtures
# ============================================================================


@pytest.fixture
def ha_config():
    """Create a basic Home Assistant configuration for testing."""
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
def ha_config_with_pipeline():
    """Create Home Assistant config with assist pipeline configured."""
    return HomeAssistantConfig(
        base_url="http://homeassistant.local:8123",
        token="test_token_123",
        verify_ssl=True,
        assist_pipeline="test_pipeline",
        wake_endpoint="wake",
        stt_endpoint="stt",
        tts_endpoint="tts",
        timer_entity="timer.test_timer",
        reminder_service="calendar.create_event",
        presence_entity="binary_sensor.home_presence",
    )


@pytest.fixture
def mock_httpx_client():
    """Create a mock httpx.AsyncClient for Home Assistant tests."""
    client = AsyncMock(spec=httpx.AsyncClient)
    client.headers = {}
    client.base_url = httpx.URL("http://homeassistant.local:8123")
    return client


@pytest.fixture
def mock_ha_response():
    """Create a factory for mock Home Assistant API responses.

    Usage:
        response = mock_ha_response(status_code=200, json_data={"state": "on"})
    """

    def _create_response(
        status_code: int = 200,
        json_data: dict[str, Any] | None = None,
        text: str = "",
    ) -> Mock:
        response = Mock(spec=httpx.Response)
        response.status_code = status_code
        response.json = Mock(return_value=json_data or {})
        response.text = text
        response.is_success = 200 <= status_code < 300
        return response

    return _create_response


# ============================================================================
# MQTT Fixtures
# ============================================================================


@pytest.fixture
def mqtt_config():
    """Create a basic MQTT configuration for testing."""
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
def mqtt_config_with_auth():
    """Create MQTT configuration with username/password authentication."""
    return MqttConfig(
        host="localhost",
        port=1883,
        topic_base="test-device",
        username="test_user",
        password="test_pass",
        tls_enabled=False,
        ca_cert=None,
        cert=None,
        key=None,
    )


@pytest.fixture
def mqtt_config_with_tls():
    """Create MQTT configuration with TLS encryption enabled."""
    return MqttConfig(
        host="localhost",
        port=8883,
        topic_base="test-device",
        username="mqtt_user",
        password="mqtt_pass",
        tls_enabled=True,
        ca_cert="/path/to/ca.crt",
        cert="/path/to/client.crt",
        key="/path/to/client.key",
    )


@pytest.fixture
def mock_mqtt_client():
    """Create a mock paho MQTT client.

    Provides common MQTT client methods as mocks for testing
    MQTT interactions without a real broker.
    """
    client = Mock(spec=mqtt.Client)
    client.connect = Mock()
    client.disconnect = Mock()
    client.subscribe = Mock()
    client.unsubscribe = Mock()

    # Mock MQTTMessageInfo return value to match paho-mqtt's Client.publish() API
    message_info = Mock(spec=mqtt.MQTTMessageInfo)
    message_info.rc = mqtt.MQTT_ERR_SUCCESS
    message_info.mid = 1
    message_info.wait_for_publish = Mock(return_value=True)
    message_info.is_published = Mock(return_value=True)
    client.publish = Mock(return_value=message_info)

    client.loop_start = Mock()
    client.loop_stop = Mock()
    client.is_connected = Mock(return_value=True)
    return client


# ============================================================================
# LLM Provider Fixtures
# ============================================================================


@pytest.fixture
def llm_config_openai():
    """Create OpenAI LLM configuration for testing."""
    return LLMConfig(
        provider="openai",
        system_prompt="You are a helpful assistant.",
        openai_model="gpt-4",
        openai_api_key="test_openai_key",
        openai_base_url="https://api.openai.com/v1",
        openai_timeout=30,
        gemini_model="gemini-pro",
        gemini_api_key=None,
        gemini_base_url="https://generativelanguage.googleapis.com/v1beta",
        gemini_timeout=30,
        anthropic_model="claude-3-5-haiku-20241022",
        anthropic_api_key=None,
        anthropic_base_url="https://api.anthropic.com/v1",
        anthropic_timeout=45,
        groq_model="llama-3.3-70b-versatile",
        groq_api_key=None,
        groq_base_url="https://api.groq.com/openai/v1",
        groq_timeout=30,
        mistral_model="mistral-small-latest",
        mistral_api_key=None,
        mistral_base_url="https://api.mistral.ai/v1",
        mistral_timeout=45,
        openrouter_model="meta-llama/llama-3.3-70b-instruct",
        openrouter_api_key=None,
        openrouter_base_url="https://openrouter.ai/api/v1",
        openrouter_timeout=45,
    )


@pytest.fixture
def llm_config_anthropic():
    """Create Anthropic (Claude) LLM configuration for testing."""
    return LLMConfig(
        provider="anthropic",
        system_prompt="You are a helpful assistant.",
        openai_model="gpt-4",
        openai_api_key=None,
        openai_base_url="https://api.openai.com/v1",
        openai_timeout=30,
        gemini_model="gemini-pro",
        gemini_api_key=None,
        gemini_base_url="https://generativelanguage.googleapis.com/v1beta",
        gemini_timeout=30,
        anthropic_model="claude-3-5-haiku-20241022",
        anthropic_api_key="test_anthropic_key",
        anthropic_base_url="https://api.anthropic.com/v1",
        anthropic_timeout=45,
        groq_model="llama-3.3-70b-versatile",
        groq_api_key=None,
        groq_base_url="https://api.groq.com/openai/v1",
        groq_timeout=30,
        mistral_model="mistral-small-latest",
        mistral_api_key=None,
        mistral_base_url="https://api.mistral.ai/v1",
        mistral_timeout=45,
        openrouter_model="meta-llama/llama-3.3-70b-instruct",
        openrouter_api_key=None,
        openrouter_base_url="https://openrouter.ai/api/v1",
        openrouter_timeout=45,
    )


@pytest.fixture
def mock_llm_provider():
    """Create a mock LLM provider for testing.

    Returns an AsyncMock with a chat() method that returns a mock LLMResult.
    """
    from pulse.assistant.llm import LLMResult

    provider = AsyncMock()
    provider.chat = AsyncMock(
        return_value=LLMResult(
            response="Test response",
            actions=[],
            follow_up=False,
        )
    )
    return provider


# ============================================================================
# Async Test Utilities
# ============================================================================


@pytest.fixture
def async_timeout():
    """Provide a reasonable timeout for async tests.

    Returns timeout in seconds. Useful for ensuring tests don't hang.
    """
    return 5.0


# ============================================================================
# Test Data Factories
# ============================================================================


@pytest.fixture
def make_llm_config():
    """Factory fixture for creating LLM configs with custom overrides.

    Usage:
        config = make_llm_config(provider="anthropic", anthropic_api_key="key")
    """

    def _create_config(**overrides: Any) -> LLMConfig:
        defaults = {
            "provider": "openai",
            "system_prompt": "You are a helpful assistant.",
            "openai_model": "gpt-4",
            "openai_api_key": "test_key",
            "openai_base_url": "https://api.openai.com/v1",
            "openai_timeout": 30,
            "gemini_model": "gemini-pro",
            "gemini_api_key": None,
            "gemini_base_url": "https://generativelanguage.googleapis.com/v1beta",
            "gemini_timeout": 30,
            "anthropic_model": "claude-3-5-haiku-20241022",
            "anthropic_api_key": None,
            "anthropic_base_url": "https://api.anthropic.com/v1",
            "anthropic_timeout": 45,
            "groq_model": "llama-3.3-70b-versatile",
            "groq_api_key": None,
            "groq_base_url": "https://api.groq.com/openai/v1",
            "groq_timeout": 30,
            "mistral_model": "mistral-small-latest",
            "mistral_api_key": None,
            "mistral_base_url": "https://api.mistral.ai/v1",
            "mistral_timeout": 45,
            "openrouter_model": "meta-llama/llama-3.3-70b-instruct",
            "openrouter_api_key": None,
            "openrouter_base_url": "https://openrouter.ai/api/v1",
            "openrouter_timeout": 45,
        }
        defaults.update(overrides)
        return LLMConfig(**defaults)  # type: ignore[arg-type]

    return _create_config
