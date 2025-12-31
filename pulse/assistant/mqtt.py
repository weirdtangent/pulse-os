"""Simple MQTT helper for the assistant."""

from __future__ import annotations

import logging
import ssl
import threading
from collections.abc import Callable

import paho.mqtt.client as mqtt

from .config import MqttConfig


class AssistantMqtt:
    def __init__(self, config: MqttConfig, logger: logging.Logger | None = None) -> None:
        self.config = config
        self._logger = logger or logging.getLogger(__name__)
        self._client: mqtt.Client | None = None
        self._lock = threading.Lock()

    def connect(self) -> None:
        if not self.config.host:
            self._logger.debug("[mqtt] MQTT host not configured; assistant telemetry disabled")
            return
        with self._lock:
            if self._client is not None:
                return
            callback_kwargs: dict[str, object] = {}
            if hasattr(mqtt, "CallbackAPIVersion"):
                callback_kwargs["callback_api_version"] = mqtt.CallbackAPIVersion.VERSION2
            client = mqtt.Client(
                client_id=f"pulse-assistant-{self.config.topic_base}",
                clean_session=True,
                **callback_kwargs,
            )
            if self.config.username:
                client.username_pw_set(self.config.username, self.config.password or "")
            if self.config.tls_enabled:
                tls_kwargs: dict[str, object] = {}
                if self.config.ca_cert:
                    tls_kwargs["ca_certs"] = self.config.ca_cert
                if self.config.cert:
                    tls_kwargs["certfile"] = self.config.cert
                if self.config.key:
                    tls_kwargs["keyfile"] = self.config.key
                tls_kwargs["tls_version"] = getattr(ssl, "PROTOCOL_TLS_CLIENT", ssl.PROTOCOL_TLS)
                client.tls_set(**tls_kwargs)
            try:
                client.connect(self.config.host, self.config.port, keepalive=30)
            except Exception as exc:
                self._logger.warning("[mqtt] Failed to connect to MQTT: %s", exc)
                return
            client.loop_start()
            self._client = client

    def disconnect(self) -> None:
        with self._lock:
            client = self._client
            self._client = None
        if client:
            client.loop_stop()
            client.disconnect()

    def is_connected(self) -> bool:
        client = self._client
        try:
            return bool(client and client.is_connected())
        except Exception:
            return False

    def publish(self, topic: str, payload: str, retain: bool = False, qos: int = 0) -> None:
        client = self._client
        if not client:
            return
        try:
            client.publish(topic, payload=payload, qos=qos, retain=retain)
        except Exception as exc:
            self._logger.debug("[mqtt] Failed to publish MQTT message: %s", exc)

    def subscribe(self, topic: str, on_message: Callable[[str], None]) -> None:
        client = self._client
        if not client:
            raise RuntimeError("MQTT client is not connected")

        def _callback(_client, _userdata, message):  # type: ignore[no-untyped-def]
            try:
                payload = message.payload.decode("utf-8", errors="ignore")
                on_message(payload)
            except Exception as exc:
                self._logger.error(
                    "[mqtt] MQTT subscriber callback failed for topic '%s': %s", topic, exc, exc_info=True
                )

        result, mid = client.subscribe(topic)
        if result != mqtt.MQTT_ERR_SUCCESS:
            self._logger.warning("[mqtt] Failed to subscribe to topic: %s (rc=%s)", topic, result)
        client.message_callback_add(topic, _callback)
