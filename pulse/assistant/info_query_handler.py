"""Information query handler for voice-triggered info lookups."""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pulse.assistant.info_service import InfoService
    from pulse.assistant.media_controller import MediaController
    from pulse.assistant.mqtt_publisher import AssistantMqttPublisher

LOGGER = logging.getLogger(__name__)


class InfoQueryHandler:
    """Handles information queries (weather, news, sports) from voice input."""

    def __init__(
        self,
        *,
        info_service: InfoService | None,
        publisher: AssistantMqttPublisher,
        media_controller: MediaController,
        response_topic: str,
        overlay_min_seconds: float | None = None,
        overlay_buffer_seconds: float | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.info_service = info_service
        self.publisher = publisher
        self.media_controller = media_controller
        self.response_topic = response_topic
        self._info_overlay_min_seconds = (
            overlay_min_seconds
            if overlay_min_seconds is not None
            else max(0.0, float(os.environ.get("PULSE_INFO_CARD_MIN_SECONDS", "1.5")))
        )
        self._info_overlay_buffer_seconds = (
            overlay_buffer_seconds
            if overlay_buffer_seconds is not None
            else max(0.0, float(os.environ.get("PULSE_INFO_CARD_BUFFER_SECONDS", "0.5")))
        )
        self.logger = logger or LOGGER

        self._on_speak: Callable[[str], Awaitable[None]] | None = None
        self._on_log_response: Callable[[str, str, str], None] | None = None
        self._tracker_provider: Callable[[], Any] | None = None
        self._stage_callback: Callable[[str, str, dict[str, Any] | None], None] | None = None

    def set_speak_callback(self, callback: Callable[[str], Awaitable[None]]) -> None:
        """Set callback to speak text to user (async)."""
        self._on_speak = callback

    def set_log_response_callback(self, callback: Callable[[str, str, str], None]) -> None:
        """Set callback to log assistant responses.

        Args:
            callback: Function(tag, text, pipeline) -> None
        """
        self._on_log_response = callback

    def set_tracker_provider(self, callback: Callable[[], Any]) -> None:
        """Set callback that returns the current AssistRunTracker."""
        self._tracker_provider = callback

    def set_stage_callback(self, callback: Callable[[str, str, dict[str, Any] | None], None]) -> None:
        """Set callback for _set_assist_stage(pipeline, stage, extra)."""
        self._stage_callback = callback

    async def maybe_handle(self, transcript: str, wake_word: str, *, follow_up: bool = False) -> bool:
        """Check if transcript is an info query and handle it.

        Returns:
            True if handled, False to pass to next handler.
        """
        if not self.info_service:
            return False
        response = await self.info_service.maybe_answer(transcript)
        if not response:
            return False

        tracker = self._tracker_provider() if self._tracker_provider else None
        if tracker:
            tracker.begin_stage("speaking")

        stage_extra: dict[str, str | bool] = {"wake_word": wake_word, "info_category": response.category}
        if follow_up:
            stage_extra["follow_up"] = True
        if self._stage_callback:
            self._stage_callback("pulse", "speaking", stage_extra)

        payload: dict[str, str | bool] = {
            "text": response.text,
            "wake_word": wake_word,
            "info_category": response.category,
        }
        if follow_up:
            payload["follow_up"] = True
        self.publisher._publish_message(self.response_topic, json.dumps(payload))

        tag = f"info:{response.category}"
        if self._on_log_response:
            self._on_log_response(tag, response.text, "pulse")

        overlay_active = False
        overlay_text = response.display or response.text
        overlay_payload = response.card
        estimated_clear_delay = self.estimate_speech_duration(response.text) + self._info_overlay_buffer_seconds
        try:
            if overlay_text or overlay_payload:
                self.publisher._publish_info_overlay(
                    text=overlay_text, category=response.category, extra=overlay_payload
                )
                overlay_active = True
            if self._on_speak:
                await self._on_speak(response.text)
        finally:
            if overlay_active:
                hold = max(self._info_overlay_min_seconds, estimated_clear_delay)
                self.publisher._schedule_info_overlay_clear(hold)

        self.media_controller.trigger_media_resume_after_response()
        return True

    @staticmethod
    def estimate_speech_duration(text: str) -> float:
        """Estimate how long it takes to speak the given text (in seconds)."""
        words = max(1, len(text.split()))
        return words / 2.5
