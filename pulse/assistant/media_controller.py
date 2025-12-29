"""Media player pause/resume control."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pulse.assistant.home_assistant import HomeAssistantClient

LOGGER = logging.getLogger("pulse-assistant.media")


class MediaController:
    """Manages media player pause/resume during assistant interactions."""

    def __init__(
        self,
        home_assistant: HomeAssistantClient | None,
        media_player_entity: str | None,
        additional_entities: list[str] | None = None,
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        self.home_assistant = home_assistant
        self.media_player_entity = media_player_entity
        extras = additional_entities or []
        unique_entities: list[str] = []
        for candidate in [media_player_entity, *extras]:
            if candidate and candidate not in unique_entities:
                unique_entities.append(candidate)
        self._entities = unique_entities
        self._loop = loop
        self._media_pause_pending = False
        self._media_resume_task: asyncio.Task | None = None
        self._media_resume_delay = 2.0

    def cancel_media_resume_task(self) -> None:
        """Cancel any pending media resume task."""
        task = self._media_resume_task
        if not task:
            return
        task.cancel()

        def _cleanup(done: asyncio.Task) -> None:
            with contextlib.suppress(asyncio.CancelledError):
                done.result()

        task.add_done_callback(_cleanup)
        self._media_resume_task = None

    async def maybe_pause_media_playback(self) -> None:
        """Pause media playback if currently playing."""
        if self._media_pause_pending or not self.home_assistant or not self._entities:
            return
        state = await self.fetch_media_player_state()
        if not state:
            return
        status = str(state.get("state") or "").lower()
        if status != "playing":
            return
        try:
            from pulse.assistant.home_assistant import HomeAssistantError

            await self._call_media_service("media_pause")
            self._media_pause_pending = True
        except HomeAssistantError as exc:
            LOGGER.debug("[media] Unable to pause media player %s: %s", self.media_player_entity, exc)

    def trigger_media_resume_after_response(self) -> None:
        """Schedule media resume after assistant response."""
        self.schedule_media_resume(self._media_resume_delay)

    def ensure_media_resume(self) -> None:
        """Ensure media is resumed if paused."""
        if self._media_pause_pending and not self._media_resume_task:
            self.schedule_media_resume(0.0)

    def schedule_media_resume(self, delay: float) -> None:
        """Schedule media resume after a delay."""
        if not self._media_pause_pending or self._media_resume_task or not self.home_assistant or not self._entities:
            return
        loop = self._loop or asyncio.get_running_loop()
        self._media_resume_task = loop.create_task(self._resume_media_after_delay(max(0.0, delay)))

    async def _resume_media_after_delay(self, delay: float) -> None:
        """Resume media playback after delay."""
        try:
            from pulse.assistant.home_assistant import HomeAssistantError

            await asyncio.sleep(delay)
            await self._call_media_service("media_play")
            LOGGER.debug("[media] Resumed media players %s", self._entities)
        except asyncio.CancelledError:
            raise
        except HomeAssistantError as exc:
            LOGGER.debug("[media] Unable to resume media player %s: %s", self.media_player_entity, exc)
        finally:
            self._media_pause_pending = False
            self._media_resume_task = None

    async def fetch_media_player_state(self) -> dict | None:
        """Fetch current media player state."""
        entity = self._entities[0] if self._entities else None
        ha_client = self.home_assistant
        if not entity or not ha_client:
            return None
        try:
            from pulse.assistant.home_assistant import HomeAssistantError

            return await ha_client.get_state(entity)
        except HomeAssistantError as exc:
            LOGGER.debug("[media] Unable to read media_player %s: %s", entity, exc)
            return None

    async def pause_all(self) -> None:
        """Pause all configured media players."""
        await self._call_media_service("media_pause")

    async def resume_all(self) -> None:
        """Resume all configured media players."""
        await self._call_media_service("media_play")

    async def stop_all(self) -> None:
        """Stop all configured media players."""
        await self._call_media_service("media_stop")

    async def _call_media_service(self, service: str) -> None:
        if not self.home_assistant or not self._entities:
            return
        payload = {"entity_id": self._entities}
        await self.home_assistant.call_service("media_player", service, payload)
