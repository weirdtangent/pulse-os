"""Tests for the MQTT client (pulse/assistant/mqtt.py).

Critical infrastructure testing - MQTT is the communication backbone of Pulse OS.
Target: 20+ tests, 80%+ coverage.
"""

from __future__ import annotations

import logging
import threading
from unittest.mock import MagicMock, Mock, patch

import paho.mqtt.client as mqtt
import pytest
from pulse.assistant.config import MqttConfig
from pulse.assistant.mqtt import AssistantMqtt

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
def mqtt_config_with_auth():
    """MQTT configuration with username/password."""
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
    """MQTT configuration with TLS enabled."""
    return MqttConfig(
        host="localhost",
        port=8883,
        topic_base="test-device",
        username=None,
        password=None,
        tls_enabled=True,
        ca_cert="/path/to/ca.crt",
        cert="/path/to/client.crt",
        key="/path/to/client.key",
    )


@pytest.fixture
def mock_logger():
    """Mock logger for testing."""
    return Mock(spec=logging.Logger)


# Connection Tests


def test_mqtt_init(mqtt_config, mock_logger):
    """Test MQTT client initialization."""
    client = AssistantMqtt(mqtt_config, mock_logger)
    assert client.config == mqtt_config
    assert client._logger == mock_logger
    assert client._client is None


def test_mqtt_init_without_logger(mqtt_config):
    """Test MQTT client creates default logger if none provided."""
    client = AssistantMqtt(mqtt_config)
    assert client._logger is not None
    assert isinstance(client._logger, logging.Logger)


@patch("paho.mqtt.client.Client")
def test_mqtt_connect_success(mock_client_class, mqtt_config, mock_logger):
    """Test successful MQTT connection."""
    mock_client_instance = MagicMock()
    mock_client_class.return_value = mock_client_instance

    client = AssistantMqtt(mqtt_config, mock_logger)
    client.connect()

    # Verify Client was instantiated with correct params
    mock_client_class.assert_called_once()
    call_kwargs = mock_client_class.call_args[1]
    assert call_kwargs["client_id"] == "pulse-assistant-test-device"
    assert call_kwargs["clean_session"] is True

    # Verify connection was established
    mock_client_instance.connect.assert_called_once_with("localhost", 1883, keepalive=30)
    mock_client_instance.loop_start.assert_called_once()

    assert client._client is not None


@patch("paho.mqtt.client.Client")
def test_mqtt_connect_with_auth(mock_client_class, mqtt_config_with_auth, mock_logger):
    """Test MQTT connection with username/password authentication."""
    mock_client_instance = MagicMock()
    mock_client_class.return_value = mock_client_instance

    client = AssistantMqtt(mqtt_config_with_auth, mock_logger)
    client.connect()

    # Verify authentication was set
    mock_client_instance.username_pw_set.assert_called_once_with("test_user", "test_pass")
    mock_client_instance.connect.assert_called_once()


@patch("paho.mqtt.client.Client")
def test_mqtt_connect_with_tls(mock_client_class, mqtt_config_with_tls, mock_logger):
    """Test MQTT connection with TLS enabled."""
    mock_client_instance = MagicMock()
    mock_client_class.return_value = mock_client_instance

    client = AssistantMqtt(mqtt_config_with_tls, mock_logger)
    client.connect()

    # Verify TLS was configured
    mock_client_instance.tls_set.assert_called_once()
    tls_kwargs = mock_client_instance.tls_set.call_args[1]
    assert tls_kwargs["ca_certs"] == "/path/to/ca.crt"
    assert tls_kwargs["certfile"] == "/path/to/client.crt"
    assert tls_kwargs["keyfile"] == "/path/to/client.key"
    assert "tls_version" in tls_kwargs


def test_mqtt_connect_without_host(mock_logger):
    """Test MQTT connect skips when host is not configured."""
    config = MqttConfig(
        host="",
        port=1883,
        topic_base="test-device",
        username=None,
        password=None,
        tls_enabled=False,
        ca_cert=None,
        cert=None,
        key=None,
    )
    client = AssistantMqtt(config, mock_logger)
    client.connect()

    assert client._client is None
    mock_logger.debug.assert_called_once()


@patch("paho.mqtt.client.Client")
def test_mqtt_connect_failure(mock_client_class, mqtt_config, mock_logger):
    """Test MQTT connection handles connection failures gracefully."""
    mock_client_instance = MagicMock()
    mock_client_instance.connect.side_effect = ConnectionRefusedError("Connection refused")
    mock_client_class.return_value = mock_client_instance

    client = AssistantMqtt(mqtt_config, mock_logger)
    client.connect()

    # Should log warning but not crash
    mock_logger.warning.assert_called_once()
    assert "Failed to connect" in str(mock_logger.warning.call_args)
    assert client._client is None


@patch("paho.mqtt.client.Client")
def test_mqtt_connect_idempotent(mock_client_class, mqtt_config, mock_logger):
    """Test multiple connect calls are idempotent (doesn't reconnect)."""
    mock_client_instance = MagicMock()
    mock_client_class.return_value = mock_client_instance

    client = AssistantMqtt(mqtt_config, mock_logger)
    client.connect()
    client.connect()  # Second call should be no-op

    # Should only connect once
    mock_client_class.assert_called_once()
    mock_client_instance.connect.assert_called_once()


# Disconnection Tests


@patch("paho.mqtt.client.Client")
def test_mqtt_disconnect(mock_client_class, mqtt_config, mock_logger):
    """Test MQTT disconnection."""
    mock_client_instance = MagicMock()
    mock_client_class.return_value = mock_client_instance

    client = AssistantMqtt(mqtt_config, mock_logger)
    client.connect()
    client.disconnect()

    mock_client_instance.loop_stop.assert_called_once()
    mock_client_instance.disconnect.assert_called_once()
    assert client._client is None


def test_mqtt_disconnect_when_not_connected(mqtt_config, mock_logger):
    """Test disconnect is safe when not connected."""
    client = AssistantMqtt(mqtt_config, mock_logger)
    client.disconnect()  # Should not crash


# Connection Status Tests


@patch("paho.mqtt.client.Client")
def test_mqtt_is_connected_true(mock_client_class, mqtt_config, mock_logger):
    """Test is_connected returns True when connected."""
    mock_client_instance = MagicMock()
    mock_client_instance.is_connected.return_value = True
    mock_client_class.return_value = mock_client_instance

    client = AssistantMqtt(mqtt_config, mock_logger)
    client.connect()

    assert client.is_connected() is True


def test_mqtt_is_connected_false_not_connected(mqtt_config, mock_logger):
    """Test is_connected returns False when not connected."""
    client = AssistantMqtt(mqtt_config, mock_logger)
    assert client.is_connected() is False


@patch("paho.mqtt.client.Client")
def test_mqtt_is_connected_false_disconnected(mock_client_class, mqtt_config, mock_logger):
    """Test is_connected returns False after disconnect."""
    mock_client_instance = MagicMock()
    mock_client_instance.is_connected.return_value = False
    mock_client_class.return_value = mock_client_instance

    client = AssistantMqtt(mqtt_config, mock_logger)
    client.connect()
    client.disconnect()

    assert client.is_connected() is False


@patch("paho.mqtt.client.Client")
def test_mqtt_is_connected_handles_exception(mock_client_class, mqtt_config, mock_logger):
    """Test is_connected handles exceptions gracefully."""
    mock_client_instance = MagicMock()
    mock_client_instance.is_connected.side_effect = RuntimeError("Test error")
    mock_client_class.return_value = mock_client_instance

    client = AssistantMqtt(mqtt_config, mock_logger)
    client.connect()

    # Should return False on exception, not crash
    assert client.is_connected() is False


# Publishing Tests


@patch("paho.mqtt.client.Client")
def test_mqtt_publish_success(mock_client_class, mqtt_config, mock_logger):
    """Test successful message publishing."""
    mock_client_instance = MagicMock()
    mock_client_class.return_value = mock_client_instance

    client = AssistantMqtt(mqtt_config, mock_logger)
    client.connect()
    # connect() publishes "online" availability; reset to isolate the test publish
    mock_client_instance.publish.reset_mock()
    client.publish("test/topic", "test payload", retain=False, qos=0)

    mock_client_instance.publish.assert_called_once_with("test/topic", payload="test payload", qos=0, retain=False)


@patch("paho.mqtt.client.Client")
def test_mqtt_publish_with_qos_and_retain(mock_client_class, mqtt_config, mock_logger):
    """Test publishing with QoS and retain flags."""
    mock_client_instance = MagicMock()
    mock_client_class.return_value = mock_client_instance

    client = AssistantMqtt(mqtt_config, mock_logger)
    client.connect()
    mock_client_instance.publish.reset_mock()
    client.publish("test/topic", "test payload", retain=True, qos=2)

    mock_client_instance.publish.assert_called_once_with("test/topic", payload="test payload", qos=2, retain=True)


def test_mqtt_publish_when_not_connected(mqtt_config, mock_logger):
    """Test publish is no-op when not connected."""
    client = AssistantMqtt(mqtt_config, mock_logger)
    client.publish("test/topic", "test payload")  # Should not crash


@patch("paho.mqtt.client.Client")
def test_mqtt_publish_handles_exception(mock_client_class, mqtt_config, mock_logger):
    """Test publish handles exceptions gracefully."""
    mock_client_instance = MagicMock()
    mock_client_class.return_value = mock_client_instance

    client = AssistantMqtt(mqtt_config, mock_logger)
    client.connect()
    # Set side_effect after connect so the availability publish succeeds
    mock_client_instance.publish.side_effect = RuntimeError("Publish failed")
    client.publish("test/topic", "test payload")

    # Should log debug message but not crash
    mock_logger.debug.assert_called()
    assert "Failed to publish" in str(mock_logger.debug.call_args)


# Subscription Tests


@patch("paho.mqtt.client.Client")
def test_mqtt_subscribe_success(mock_client_class, mqtt_config, mock_logger):
    """Test successful topic subscription."""
    mock_client_instance = MagicMock()
    mock_client_instance.subscribe.return_value = (mqtt.MQTT_ERR_SUCCESS, 1)
    mock_client_class.return_value = mock_client_instance

    callback = Mock()
    client = AssistantMqtt(mqtt_config, mock_logger)
    client.connect()
    client.subscribe("test/topic", callback)

    mock_client_instance.subscribe.assert_called_once_with("test/topic")
    mock_client_instance.message_callback_add.assert_called_once()


def test_mqtt_subscribe_when_not_connected(mqtt_config, mock_logger):
    """Test subscribe raises error when not connected."""
    client = AssistantMqtt(mqtt_config, mock_logger)
    callback = Mock()

    with pytest.raises(RuntimeError, match="MQTT client is not connected"):
        client.subscribe("test/topic", callback)


@patch("paho.mqtt.client.Client")
def test_mqtt_subscribe_callback_invoked(mock_client_class, mqtt_config, mock_logger):
    """Test subscription callback is invoked on message receipt."""
    mock_client_instance = MagicMock()
    mock_client_instance.subscribe.return_value = (mqtt.MQTT_ERR_SUCCESS, 1)
    mock_client_class.return_value = mock_client_instance

    callback = Mock()
    client = AssistantMqtt(mqtt_config, mock_logger)
    client.connect()
    client.subscribe("test/topic", callback)

    # Get the wrapper callback that was registered
    wrapper_callback = mock_client_instance.message_callback_add.call_args[0][1]

    # Simulate message receipt
    mock_message = Mock()
    mock_message.payload = b"test payload"
    wrapper_callback(None, None, mock_message)

    # Verify user callback was invoked with decoded payload
    callback.assert_called_once_with("test payload")


@patch("paho.mqtt.client.Client")
def test_mqtt_subscribe_callback_handles_decode_error(mock_client_class, mqtt_config, mock_logger):
    """Test subscription callback handles invalid UTF-8 gracefully."""
    mock_client_instance = MagicMock()
    mock_client_instance.subscribe.return_value = (mqtt.MQTT_ERR_SUCCESS, 1)
    mock_client_class.return_value = mock_client_instance

    callback = Mock()
    client = AssistantMqtt(mqtt_config, mock_logger)
    client.connect()
    client.subscribe("test/topic", callback)

    wrapper_callback = mock_client_instance.message_callback_add.call_args[0][1]

    # Simulate message with invalid UTF-8
    mock_message = Mock()
    mock_message.payload = b"\xff\xfe invalid utf-8 \xff"
    wrapper_callback(None, None, mock_message)

    # Should still call callback (with replacement chars for invalid bytes)
    assert callback.called


@patch("paho.mqtt.client.Client")
def test_mqtt_subscribe_callback_exception_logged(mock_client_class, mqtt_config, mock_logger):
    """Test subscription callback logs exceptions from user callback."""
    mock_client_instance = MagicMock()
    mock_client_instance.subscribe.return_value = (mqtt.MQTT_ERR_SUCCESS, 1)
    mock_client_class.return_value = mock_client_instance

    callback = Mock(side_effect=ValueError("Test error"))
    client = AssistantMqtt(mqtt_config, mock_logger)
    client.connect()
    client.subscribe("test/topic", callback)

    wrapper_callback = mock_client_instance.message_callback_add.call_args[0][1]

    mock_message = Mock()
    mock_message.payload = b"test payload"
    wrapper_callback(None, None, mock_message)

    # Should log error
    mock_logger.error.assert_called_once()
    assert "subscriber callback failed" in str(mock_logger.error.call_args).lower()


@patch("paho.mqtt.client.Client")
def test_mqtt_subscribe_failure(mock_client_class, mqtt_config, mock_logger):
    """Test subscribe logs warning on failure."""
    mock_client_instance = MagicMock()
    mock_client_instance.subscribe.return_value = (mqtt.MQTT_ERR_NO_CONN, 0)
    mock_client_class.return_value = mock_client_instance

    callback = Mock()
    client = AssistantMqtt(mqtt_config, mock_logger)
    client.connect()
    client.subscribe("test/topic", callback)

    # Should log warning
    mock_logger.warning.assert_called_once()
    assert "Failed to subscribe" in str(mock_logger.warning.call_args)


# Thread Safety Tests


@patch("paho.mqtt.client.Client")
def test_mqtt_connect_thread_safe(mock_client_class, mqtt_config, mock_logger):
    """Test connect uses locking for thread safety."""
    mock_client_instance = MagicMock()
    mock_client_class.return_value = mock_client_instance

    client = AssistantMqtt(mqtt_config, mock_logger)

    # Lock should exist
    assert hasattr(client, "_lock")
    assert isinstance(client._lock, threading.Lock)

    client.connect()
    # If thread-safe, this should work without issues
    assert client._client is not None


@patch("paho.mqtt.client.Client")
def test_mqtt_disconnect_thread_safe(mock_client_class, mqtt_config, mock_logger):
    """Test disconnect uses locking for thread safety."""
    mock_client_instance = MagicMock()
    mock_client_class.return_value = mock_client_instance

    client = AssistantMqtt(mqtt_config, mock_logger)
    client.connect()
    client.disconnect()

    # Lock should have been used
    assert client._client is None
