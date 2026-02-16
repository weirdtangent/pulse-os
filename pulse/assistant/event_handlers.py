"""Event handlers for alerts, intercom, now-playing, and kiosk availability."""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import time
from collections.abc import Callable, Coroutine, Sequence
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pulse.assistant.mqtt import AssistantMqtt
    from pulse.assistant.mqtt_publisher import AssistantMqttPublisher
    from pulse.assistant.wake_detector import WakeDetector

LOGGER = logging.getLogger(__name__)


class EventHandlerManager:
    """Manages event subscriptions and handlers for alerts, intercom, playback, and kiosk."""

    def __init__(
        self,
        *,
        mqtt: AssistantMqtt,
        publisher: AssistantMqttPublisher,
        wake_detector: WakeDetector,
        alert_topics: Sequence[str],
        intercom_topic: str | None,
        playback_topic: str,
        kiosk_availability_topic: str,
        logger: logging.Logger | None = None,
    ) -> None:
        self.mqtt = mqtt
        self.publisher = publisher
        self.wake_detector = wake_detector
        self.logger = logger or LOGGER

        self._alert_topics = list(alert_topics)
        self._intercom_topic = intercom_topic
        self._playback_topic = playback_topic
        self._kiosk_availability_topic = kiosk_availability_topic

        self._kiosk_available: bool = True
        self._last_kiosk_online: float = time.monotonic()
        self._last_kiosk_restart_attempt: float = 0.0

        self._on_speak: Callable[[str], Coroutine[Any, Any, None]] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def set_speak_callback(self, callback: Callable[[str], Coroutine[Any, Any, None]]) -> None:
        """Set async callback to speak text to user."""
        self._on_speak = callback

    def set_event_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Set event loop for creating async tasks from sync handlers."""
        self._loop = loop

    @property
    def kiosk_available(self) -> bool:
        return self._kiosk_available

    def subscribe_all(self) -> None:
        """Subscribe to all event MQTT topics."""
        self._subscribe_playback_topic()
        self._subscribe_alert_topics()
        self._subscribe_intercom_topic()
        self._subscribe_kiosk_availability()

    def _subscribe_playback_topic(self) -> None:
        try:
            self.mqtt.subscribe(self._playback_topic, self.handle_now_playing_message)
        except RuntimeError:
            self.logger.debug("[events] MQTT client not ready for playback telemetry subscription")

    def _subscribe_alert_topics(self) -> None:
        for topic in self._alert_topics:
            try:
                self.mqtt.subscribe(topic, lambda payload, t=topic: self.handle_alert_message(t, payload))  # type: ignore[misc]
            except Exception as exc:
                self.logger.warning("[events] Failed to subscribe to alert topic %s: %s", topic, exc)

    def _subscribe_intercom_topic(self) -> None:
        if not self._intercom_topic:
            return
        try:
            self.mqtt.subscribe(self._intercom_topic, self.handle_intercom_message)
        except Exception as exc:
            self.logger.warning("[events] Failed to subscribe to intercom topic %s: %s", self._intercom_topic, exc)

    def _subscribe_kiosk_availability(self) -> None:
        try:
            self.mqtt.subscribe(self._kiosk_availability_topic, self.handle_kiosk_availability)
        except Exception as exc:
            self.logger.warning(
                "[events] Failed to subscribe to kiosk availability topic %s: %s",
                self._kiosk_availability_topic,
                exc,
            )

    def handle_now_playing_message(self, payload: str) -> None:
        normalized = payload.strip()
        active = bool(normalized)
        changed = self.wake_detector.set_remote_audio_active(active)
        if changed:
            detail = normalized[:80] or "idle"
            self.logger.debug(
                "[events] Self audio playback %s via telemetry (%s)", "active" if active else "idle", detail
            )

    def handle_alert_message(self, topic: str, payload: str) -> None:
        message = payload
        try:
            parsed = json.loads(payload)
            if isinstance(parsed, dict):
                message = str(parsed.get("message") or parsed.get("text") or payload)
        except json.JSONDecodeError:
            pass  # Not JSON — use raw payload as the message text
        clean = message.strip()
        if not clean:
            return
        self.publisher._publish_info_overlay(text=f"Alert: {clean}", category="alerts")
        self.publisher._schedule_info_overlay_clear(8.0)
        self._schedule_speak(clean, context="alert")

    def handle_intercom_message(self, payload: str) -> None:
        message = payload.strip()
        if not message:
            return
        self.publisher._publish_info_overlay(text=f"Intercom: {message}", category="intercom")
        self.publisher._schedule_info_overlay_clear(6.0)
        self._schedule_speak(message, context="intercom")

    def _schedule_speak(self, text: str, context: str) -> None:
        """Thread-safe: schedule an async speak coroutine from the MQTT callback thread."""
        if self._loop is None or self._on_speak is None:
            self.logger.error("[events] Cannot handle %s: event loop or speak callback not initialized", context)
            return
        asyncio.run_coroutine_threadsafe(self._on_speak(text), self._loop)

    def handle_kiosk_availability(self, payload: str) -> None:
        value = payload.strip().lower()
        self._kiosk_available = value == "online"
        if self._kiosk_available:
            self._last_kiosk_online = time.monotonic()

    async def check_kiosk_health(self) -> None:
        """Check kiosk health and restart service if offline too long.

        Call this periodically from the heartbeat loop.
        """
        kiosk_grace_seconds = 90
        kiosk_restart_min_interval = 120
        now = time.monotonic()
        kiosk_silence = now - self._last_kiosk_online
        if not self._kiosk_available and kiosk_silence >= kiosk_grace_seconds:
            if now - self._last_kiosk_restart_attempt >= kiosk_restart_min_interval:
                self._last_kiosk_restart_attempt = now
                self.logger.warning(
                    "[events] kiosk offline for %ds; restarting pulse-kiosk-mqtt.service",
                    int(kiosk_silence),
                )
                try:
                    await asyncio.to_thread(
                        subprocess.run,  # nosec B603 - hardcoded command array
                        ["sudo", "systemctl", "restart", "pulse-kiosk-mqtt.service"],
                        check=True,
                        timeout=30,
                    )
                except Exception as exc:  # noqa: BLE001
                    self.logger.warning("[events] kiosk restart failed: %s", exc)
