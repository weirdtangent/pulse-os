"""Music command handler for voice-triggered media controls."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pulse.assistant.home_assistant import HomeAssistantClient
    from pulse.assistant.media_controller import MediaController

LOGGER = logging.getLogger(__name__)


class MusicCommandHandler:
    """Handles voice commands for music playback control and track info."""

    def __init__(
        self,
        *,
        home_assistant: HomeAssistantClient | None,
        media_controller: MediaController,
        media_player_entity: str | None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.home_assistant = home_assistant
        self.media_controller = media_controller
        self.media_player_entity = media_player_entity
        self.logger = logger or LOGGER

        self._on_speak: Callable[[str], Awaitable[None]] | None = None
        self._on_log_response: Callable[[str, str, str], None] | None = None

    def set_speak_callback(self, callback: Callable[[str], Awaitable[None]]) -> None:
        """Set callback to speak text to user (async)."""
        self._on_speak = callback

    def set_log_response_callback(self, callback: Callable[[str, str, str], None]) -> None:
        """Set callback to log assistant responses.

        Args:
            callback: Function(tag, text, pipeline) -> None
        """
        self._on_log_response = callback

    async def maybe_handle(self, transcript: str) -> bool:
        """Check if transcript is a music command and handle it.

        Returns:
            True if handled, False to pass to next handler.
        """
        query = (transcript or "").strip().lower()
        if not query or not self.home_assistant or not self.media_player_entity:
            return False

        controls = [
            (("pause the music", "pause music", "pause the song", "pause song"), "media_pause", "Paused the music."),
            (
                ("stop the music", "stop music", "stop the song", "stop song"),
                "media_stop",
                "Stopped the music.",
            ),
            (
                ("next song", "skip song", "skip this song", "next track"),
                "media_next_track",
                "Skipping to the next song.",
            ),
        ]
        for phrases, service, success_text in controls:
            if any(phrase in query for phrase in phrases):
                return await self._call_service(service, success_text)

        info_phrases = (
            "what song is this",
            "what song am i listening to",
            "what is this song",
            "what's this song",
            "what's playing",
            "what song",
            "who is this",
            "who's this",
        )
        if any(phrase in query for phrase in info_phrases):
            return await self._describe_current_track("who" in query)

        return False

    async def _call_service(self, service: str, success_text: str) -> bool:
        entity = self.media_player_entity
        ha_client = self.home_assistant
        if not entity or not ha_client:
            return False
        try:
            from pulse.assistant.home_assistant import HomeAssistantError

            await ha_client.call_service("media_player", service, {"entity_id": entity})
        except Exception as exc:
            from pulse.assistant.home_assistant import HomeAssistantError

            if isinstance(exc, HomeAssistantError):
                self.logger.debug("[music] Music control %s failed for %s: %s", service, entity, exc)
                spoken = "I couldn't control the music right now."
                if self._on_speak:
                    await self._on_speak(spoken)
                if self._on_log_response:
                    self._on_log_response("music", spoken, "pulse")
                return True
            raise
        if self._on_speak:
            await self._on_speak(success_text)
        if self._on_log_response:
            self._on_log_response("music", success_text, "pulse")
        return True

    async def _describe_current_track(self, emphasize_artist: bool) -> bool:
        state = await self.media_controller.fetch_media_player_state()
        if state is None:
            spoken = "I couldn't reach the player for that info."
            if self._on_speak:
                await self._on_speak(spoken)
            if self._on_log_response:
                self._on_log_response("music", spoken, "pulse")
            return True

        status = str(state.get("state") or "")
        attributes = state.get("attributes") or {}
        title = attributes.get("media_title") or attributes.get("media_episode_title")
        artist = (
            attributes.get("media_artist")
            or attributes.get("media_album_artist")
            or attributes.get("media_series_title")
        )

        if status not in {"playing", "paused"} or not (title or artist):
            spoken = "Nothing is playing right now."
            if self._on_speak:
                await self._on_speak(spoken)
            if self._on_log_response:
                self._on_log_response("music", spoken, "pulse")
            return True

        if title and artist:
            message = f"This is {artist} — {title}."
        elif title:
            message = f"This song is {title}."
        else:
            message = f"This is by {artist}."

        if emphasize_artist and artist and not title:
            message = f"This is {artist}."

        if self._on_speak:
            await self._on_speak(message)
        if self._on_log_response:
            self._on_log_response("music", message, "pulse")
        return True
