"""Earmuffs manager for muting wake word detection."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pulse.assistant.mqtt import AssistantMqtt
    from pulse.assistant.mqtt_publisher import AssistantMqttPublisher

LOGGER = logging.getLogger(__name__)


class EarmuffsManager:
    """Manages earmuffs state (muting wake word detection).

    Thread-safe: all state access is guarded by a lock.
    """

    def __init__(
        self,
        *,
        mqtt: AssistantMqtt,
        publisher: AssistantMqttPublisher,
        base_topic: str,
        logger: logging.Logger | None = None,
    ) -> None:
        self.mqtt = mqtt
        self.publisher = publisher
        self.logger = logger or LOGGER

        self._lock = threading.Lock()
        self._enabled = False
        self._manual_override: bool | None = None
        self._state_restored = False

        self._state_topic = f"{base_topic}/earmuffs/state"
        self._set_topic = f"{base_topic}/earmuffs/set"

        self._on_wake_context_dirty: Callable[[], None] | None = None

    def set_wake_context_dirty_callback(self, callback: Callable[[], None]) -> None:
        """Set callback for wake_detector.mark_wake_context_dirty."""
        self._on_wake_context_dirty = callback

    def subscribe(self) -> None:
        """Subscribe to earmuffs MQTT topics."""
        try:
            self.mqtt.subscribe(self._set_topic, self._handle_command)
            self.mqtt.subscribe(self._state_topic, self._handle_state_restore)
        except RuntimeError as exc:
            self.logger.debug("[earmuffs] MQTT client not ready for subscription: %s", exc)
        except Exception as exc:
            self.logger.error("[earmuffs] Failed to subscribe to earmuffs topic: %s", exc, exc_info=True)

    @property
    def enabled(self) -> bool:
        """Thread-safe getter for earmuffs enabled state."""
        with self._lock:
            return self._enabled

    def get_enabled(self) -> bool:
        """Thread-safe getter as a callable (for wake loop compatibility)."""
        return self.enabled

    @property
    def state_restored(self) -> bool:
        """Whether retained state has been restored from MQTT."""
        with self._lock:
            return self._state_restored

    @property
    def manual_override(self) -> bool:
        """Whether earmuffs were manually toggled."""
        with self._lock:
            return self._manual_override or False

    def set_enabled(self, enabled: bool, *, manual: bool = False) -> None:
        """Set earmuffs enabled state.

        Args:
            enabled: Whether to enable earmuffs
            manual: Whether this is a manual override (vs auto)
        """
        changed = False
        with self._lock:
            if enabled != self._enabled:
                self._enabled = enabled
                if manual:
                    self._manual_override = enabled
                changed = True
        if changed:
            self.publisher._publish_earmuffs_state(self.enabled)
            if enabled and self._on_wake_context_dirty:
                self._on_wake_context_dirty()

    def _handle_state_restore(self, payload: str) -> None:
        """Restore earmuffs state from retained MQTT message on startup."""
        value = payload.strip().lower()
        enabled = value in {"on", "true", "1", "yes", "enable", "enabled"}
        mark_dirty = False
        with self._lock:
            if self._state_restored:
                return
            self._state_restored = True
            if enabled != self._enabled:
                self._enabled = enabled
                if enabled:
                    self._manual_override = True
                    mark_dirty = True
                else:
                    self._manual_override = None
        if mark_dirty and self._on_wake_context_dirty:
            self._on_wake_context_dirty()

    def _handle_command(self, payload: str) -> None:
        """Handle earmuffs set command from MQTT."""
        value = payload.strip().lower()
        if value == "toggle":
            current = self.enabled
            enabled = not current
        else:
            enabled = value in {"on", "true", "1", "yes", "enable", "enabled"}
        self.set_enabled(enabled, manual=True)
