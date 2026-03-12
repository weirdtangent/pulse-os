"""Music command handler for voice-triggered media controls."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pulse.assistant.home_assistant import HomeAssistantClient
    from pulse.assistant.llm import LLMProvider
    from pulse.assistant.media_controller import MediaController

LOGGER = logging.getLogger(__name__)

_PLAY_PREFIXES = (
    "play ",
    "can you play ",
    "could you play ",
    "please play ",
    "put on ",
    "i want to hear ",
)

_COMPANION_SYSTEM_PROMPT = (
    "You are a music expert. Some tracks are famous for being inseparable pairs that "
    "should always be played together (e.g., 'Eruption' always followed by 'You Really "
    "Got Me' by Van Halen, 'Home by the Sea' always followed by 'Second Home by the Sea' "
    "by Genesis, 'Brain Damage' always followed by 'Eclipse' by Pink Floyd). Given a track "
    "the user wants to play, if it has such a well-known companion that should immediately "
    'follow, respond with JSON: {"companion": "track name by artist"}. If not, respond '
    'with: {"companion": null}. Only return companions for truly iconic, universally '
    "recognized pairings. NOTE: The input comes from speech-to-text and may contain "
    "misspellings or phonetic errors (e.g., 'jemisus' for 'Genesis', 'led zepplin' for "
    "'Led Zeppelin'). Use your best judgment to identify the intended track."
)


def _build_player_name_map(entity_ids: tuple[str, ...]) -> dict[str, str]:
    """Build a mapping from friendly names to entity IDs.

    For ``media_player.pulse_bedroom`` produces keys
    ``"pulse bedroom"`` and ``"bedroom"``.
    """
    name_map: dict[str, str] = {}
    for eid in entity_ids:
        # Strip domain prefix
        short = eid.split(".", 1)[-1] if "." in eid else eid
        friendly = short.replace("_", " ")
        name_map[friendly] = eid
        # Also add without the "pulse " prefix for convenience
        if friendly.startswith("pulse "):
            name_map[friendly[6:]] = eid
    return name_map


class MusicCommandHandler:
    """Handles voice commands for music playback control and track info."""

    def __init__(
        self,
        *,
        home_assistant: HomeAssistantClient | None,
        media_controller: MediaController,
        media_player_entity: str | None,
        media_player_entities: tuple[str, ...] = (),
        llm_provider_getter: Callable[[], LLMProvider] | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.home_assistant = home_assistant
        self.media_controller = media_controller
        self.media_player_entity = media_player_entity
        self._llm_provider_getter = llm_provider_getter
        self.logger = logger or LOGGER

        self._player_name_map = _build_player_name_map(media_player_entities)

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

        # Play commands — detect prefix on lowered text, extract from original transcript
        original = (transcript or "").strip()
        for prefix in _PLAY_PREFIXES:
            if query.startswith(prefix):
                after = original[len(prefix) :]
                if after.strip():
                    return await self._handle_play(after.strip())
                return False

        return False

    # ------------------------------------------------------------------
    # Play media
    # ------------------------------------------------------------------

    def _resolve_target_player(self, text_after_prefix: str) -> tuple[str, str]:
        """Split *text_after_prefix* into (media_query, entity_id).

        Tries ``rsplit(" on ", 1)`` to find an explicit player target.
        Falls back to the default ``self.media_player_entity``.
        """
        default_entity = self.media_player_entity or ""
        lowered = text_after_prefix.lower()
        if " on " in lowered:
            split_pos = lowered.rfind(" on ")
            candidate_name = lowered[split_pos + 4 :].strip()
            entity = self._player_name_map.get(candidate_name)
            if entity:
                return text_after_prefix[:split_pos].strip(), entity
        return text_after_prefix, default_entity

    async def _lookup_companion(self, media_query: str) -> str | None:
        """Ask the LLM if this track has a well-known companion."""
        if not self._llm_provider_getter:
            return None
        try:
            provider = self._llm_provider_getter()
            # Inner timeout (5s) caps the HTTP call; outer timeout (6s) guards
            # against the coroutine itself hanging (e.g. DNS resolution stall).
            raw = await asyncio.wait_for(
                provider.simple_chat(_COMPANION_SYSTEM_PROMPT, media_query, timeout=5),
                timeout=6,
            )
            parsed = json.loads(raw)
            companion = parsed.get("companion")
            return str(companion) if companion else None
        except Exception:
            self.logger.debug("[music] Companion lookup failed or timed out", exc_info=True)
            return None

    async def _handle_play(self, text_after_prefix: str) -> bool:
        """Play a media query via HA Music Assistant."""
        from pulse.assistant.home_assistant import HomeAssistantError

        media_query, target_entity = self._resolve_target_player(text_after_prefix)
        if not target_entity:
            return False

        self.logger.info("[music] Play request: query=%r entity=%s", media_query, target_entity)

        # Companion lookup BEFORE play so both can be queued upfront
        companion = await self._lookup_companion(media_query)
        self.logger.info("[music] Companion lookup result: %r", companion)

        try:
            await self.home_assistant.call_service(  # type: ignore[union-attr]
                "media_player",
                "play_media",
                {
                    "entity_id": target_entity,
                    "media_content_id": media_query,
                    "media_content_type": "music",
                },
            )
            if companion:
                await self.home_assistant.call_service(  # type: ignore[union-attr]
                    "media_player",
                    "play_media",
                    {
                        "entity_id": target_entity,
                        "media_content_id": companion,
                        "media_content_type": "music",
                        "enqueue": "next",
                    },
                )
        except HomeAssistantError as exc:
            self.logger.debug("[music] play_media failed: %s", exc)
            spoken = "I couldn't play that right now."
            if self._on_speak:
                await self._on_speak(spoken)
            if self._on_log_response:
                self._on_log_response("music", spoken, "pulse")
            return True

        if companion:
            spoken = f"Playing {media_query}, followed by {companion}."
        else:
            spoken = f"Playing {media_query}."
        if self._on_speak:
            await self._on_speak(spoken)
        if self._on_log_response:
            self._on_log_response("music", spoken, "pulse")
        return True

    async def _call_service(self, service: str, success_text: str) -> bool:
        entity = self.media_player_entity
        ha_client = self.home_assistant
        if not entity or not ha_client:
            return False
        try:
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
