"""Media player pause/resume control and staleness detection."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pulse.assistant.home_assistant import HomeAssistantClient

LOGGER = logging.getLogger("pulse-assistant.media")

# Staleness detection thresholds
_STALE_GRACE_SECONDS = 120  # extra grace beyond track duration before declaring stale
_STALE_MIN_SECONDS = 300  # minimum staleness window (5 min) for very short/zero-duration tracks
_STALE_COOLDOWN_SECONDS = 600  # minimum interval between remediation attempts


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

        # Staleness tracking
        self._last_seen_position_updated_at: str | None = None
        self._last_remediation_time: float = 0.0
        self._remediation_attempts: int = 0

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

    async def check_media_player_staleness(self) -> None:
        """Detect stale media player state and attempt remediation.

        Called periodically from the heartbeat loop. Detects when HA reports
        a media player as "playing" but the position/track metadata has not
        updated for longer than expected (track duration + grace period).
        """
        state = await self.fetch_media_player_state()
        if not state:
            return

        status = str(state.get("state") or "").lower()
        if status != "playing":
            # Not playing — nothing to check, reset tracking
            self._last_seen_position_updated_at = None
            return

        attrs = state.get("attributes") or {}
        position_updated_at = attrs.get("media_position_updated_at")
        if not position_updated_at:
            return

        # If the position timestamp changed since last check, player is healthy
        if position_updated_at != self._last_seen_position_updated_at:
            self._last_seen_position_updated_at = position_updated_at
            return

        # Position timestamp hasn't changed — check how long it's been stale
        try:
            updated_dt = datetime.fromisoformat(position_updated_at)
            age_seconds = (datetime.now(UTC) - updated_dt).total_seconds()
        except (ValueError, TypeError):
            return

        duration = attrs.get("media_duration") or 0
        try:
            duration = float(duration)
        except (ValueError, TypeError):
            duration = 0.0

        stale_threshold = max(_STALE_MIN_SECONDS, duration + _STALE_GRACE_SECONDS)
        if age_seconds < stale_threshold:
            return

        # State is stale — check cooldown before attempting remediation
        now = time.monotonic()
        if now - self._last_remediation_time < _STALE_COOLDOWN_SECONDS:
            return

        entity = self._entities[0] if self._entities else self.media_player_entity
        title = attrs.get("media_title", "unknown")
        LOGGER.warning(
            "[media] Stale media player detected: %s shows '%s' but position unchanged for %.0fs "
            "(duration=%.0fs); requesting entity update",
            entity,
            title,
            age_seconds,
            duration,
        )

        self._last_remediation_time = now
        await self._remediate_stale_player(entity)

    async def _remediate_stale_player(self, entity: str | None) -> None:
        """Reload the Music Assistant integration to re-sync player state.

        Discovers the ``music_assistant`` config entry ID via the HA REST API,
        then calls ``homeassistant.reload_config_entry``.  This re-establishes
        the HA↔MA connection without restarting the MA server or interrupting
        audio playback through Snapcast.
        """
        from pulse.assistant.home_assistant import HomeAssistantError

        if not self.home_assistant:
            return

        entry_id = await self._find_music_assistant_config_entry()
        if not entry_id:
            LOGGER.debug("[media] Could not find music_assistant config entry; skipping remediation")
            return

        try:
            await self.home_assistant.call_service(
                "homeassistant",
                "reload_config_entry",
                {"entry_id": entry_id},
            )
            LOGGER.info(
                "[media] Reloaded music_assistant config entry %s to fix stale player %s",
                entry_id,
                entity,
            )
        except HomeAssistantError as exc:
            LOGGER.warning("[media] reload_config_entry failed for %s: %s", entry_id, exc)

    async def _find_music_assistant_config_entry(self) -> str | None:
        """Query HA for the music_assistant integration config entry ID."""
        from pulse.assistant.home_assistant import HomeAssistantError

        if not self.home_assistant:
            return None
        try:
            entries = await self.home_assistant._request("GET", "/api/config/config_entries/entry")
            if isinstance(entries, list):
                for entry in entries:
                    if isinstance(entry, dict) and entry.get("domain") == "music_assistant":
                        return entry.get("entry_id")
        except HomeAssistantError as exc:
            LOGGER.debug("[media] Failed to query config entries: %s", exc)
        return None

    async def _call_media_service(self, service: str) -> None:
        if not self.home_assistant or not self._entities:
            return
        payload = {"entity_id": self._entities}
        await self.home_assistant.call_service("media_player", service, payload)
