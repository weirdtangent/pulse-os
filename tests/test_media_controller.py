"""Tests for media_controller module."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from pulse.assistant.media_controller import MediaController

pytestmark = pytest.mark.anyio


def _make_controller(ha_client=None, entity="media_player.living_room", additional=None):
    return MediaController(
        home_assistant=ha_client,
        media_player_entity=entity,
        additional_entities=additional,
    )


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestMediaControllerInit:
    def test_entity_list_deduplicates(self):
        mc = _make_controller(entity="media_player.a", additional=["media_player.a", "media_player.b"])
        assert mc._entities == ["media_player.a", "media_player.b"]

    def test_no_entity(self):
        mc = _make_controller(entity=None, additional=None)
        assert mc._entities == []

    def test_initial_state(self):
        mc = _make_controller()
        assert mc._media_pause_pending is False
        assert mc._media_resume_task is None


# ---------------------------------------------------------------------------
# cancel_media_resume_task
# ---------------------------------------------------------------------------


class TestCancelMediaResumeTask:
    def test_no_task(self):
        mc = _make_controller()
        mc.cancel_media_resume_task()  # Should not raise

    async def test_cancels_existing_task(self):
        mc = _make_controller()
        mc._media_resume_task = asyncio.create_task(asyncio.sleep(100))
        mc.cancel_media_resume_task()
        assert mc._media_resume_task is None


# ---------------------------------------------------------------------------
# maybe_pause_media_playback
# ---------------------------------------------------------------------------


class TestMaybePauseMediaPlayback:
    async def test_skips_if_no_ha_client(self):
        mc = _make_controller(ha_client=None)
        await mc.maybe_pause_media_playback()
        assert mc._media_pause_pending is False

    async def test_skips_if_no_entities(self):
        mc = _make_controller(ha_client=AsyncMock(), entity=None)
        await mc.maybe_pause_media_playback()
        assert mc._media_pause_pending is False

    async def test_skips_if_already_pending(self):
        ha = AsyncMock()
        mc = _make_controller(ha_client=ha)
        mc._media_pause_pending = True
        await mc.maybe_pause_media_playback()
        ha.get_state.assert_not_awaited()

    async def test_skips_if_not_playing(self):
        ha = AsyncMock()
        ha.get_state = AsyncMock(return_value={"state": "paused"})
        mc = _make_controller(ha_client=ha)
        await mc.maybe_pause_media_playback()
        assert mc._media_pause_pending is False

    async def test_pauses_when_playing(self):
        ha = AsyncMock()
        ha.get_state = AsyncMock(return_value={"state": "playing"})
        mc = _make_controller(ha_client=ha)
        await mc.maybe_pause_media_playback()
        assert mc._media_pause_pending is True
        ha.call_service.assert_awaited_once()

    async def test_handles_ha_error_gracefully(self):
        ha = AsyncMock()
        ha.get_state = AsyncMock(return_value={"state": "playing"})
        # Import the actual error class for the mock
        from pulse.assistant.home_assistant import HomeAssistantError

        ha.call_service = AsyncMock(side_effect=HomeAssistantError("fail"))
        mc = _make_controller(ha_client=ha)
        await mc.maybe_pause_media_playback()
        assert mc._media_pause_pending is False

    async def test_skips_when_state_is_none(self):
        ha = AsyncMock()
        ha.get_state = AsyncMock(return_value=None)
        mc = _make_controller(ha_client=ha)
        await mc.maybe_pause_media_playback()
        assert mc._media_pause_pending is False


# ---------------------------------------------------------------------------
# trigger_media_resume_after_response / ensure_media_resume
# ---------------------------------------------------------------------------


class TestMediaResume:
    def test_trigger_resume_when_not_paused(self):
        mc = _make_controller(ha_client=AsyncMock())
        mc._media_pause_pending = False
        mc.trigger_media_resume_after_response()
        assert mc._media_resume_task is None

    def test_ensure_resume_when_not_paused(self):
        mc = _make_controller(ha_client=AsyncMock())
        mc.ensure_media_resume()
        assert mc._media_resume_task is None


# ---------------------------------------------------------------------------
# fetch_media_player_state
# ---------------------------------------------------------------------------


class TestFetchMediaPlayerState:
    async def test_returns_state(self):
        ha = AsyncMock()
        ha.get_state = AsyncMock(return_value={"state": "playing", "attributes": {}})
        mc = _make_controller(ha_client=ha)
        state = await mc.fetch_media_player_state()
        assert state == {"state": "playing", "attributes": {}}

    async def test_returns_none_without_ha(self):
        mc = _make_controller(ha_client=None)
        state = await mc.fetch_media_player_state()
        assert state is None

    async def test_returns_none_without_entities(self):
        mc = _make_controller(ha_client=AsyncMock(), entity=None)
        state = await mc.fetch_media_player_state()
        assert state is None

    async def test_handles_ha_error(self):
        ha = AsyncMock()
        from pulse.assistant.home_assistant import HomeAssistantError

        ha.get_state = AsyncMock(side_effect=HomeAssistantError("fail"))
        mc = _make_controller(ha_client=ha)
        state = await mc.fetch_media_player_state()
        assert state is None


# ---------------------------------------------------------------------------
# pause_all / resume_all / stop_all
# ---------------------------------------------------------------------------


class TestBulkOperations:
    async def test_pause_all(self):
        ha = AsyncMock()
        mc = _make_controller(ha_client=ha)
        await mc.pause_all()
        ha.call_service.assert_awaited_once_with(
            "media_player", "media_pause", {"entity_id": ["media_player.living_room"]}
        )

    async def test_resume_all(self):
        ha = AsyncMock()
        mc = _make_controller(ha_client=ha)
        await mc.resume_all()
        ha.call_service.assert_awaited_once_with(
            "media_player", "media_play", {"entity_id": ["media_player.living_room"]}
        )

    async def test_stop_all(self):
        ha = AsyncMock()
        mc = _make_controller(ha_client=ha)
        await mc.stop_all()
        ha.call_service.assert_awaited_once_with(
            "media_player", "media_stop", {"entity_id": ["media_player.living_room"]}
        )

    async def test_noop_without_ha_client(self):
        mc = _make_controller(ha_client=None)
        await mc.pause_all()
        await mc.resume_all()
        await mc.stop_all()
        # No exceptions raised
