"""Tests for MusicCommandHandler."""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock

import pytest
from pulse.assistant.music_handler import MusicCommandHandler


@pytest.fixture
def mock_home_assistant():
    ha = AsyncMock()
    ha.call_service = AsyncMock()
    return ha


@pytest.fixture
def mock_media_controller():
    mc = AsyncMock()
    mc.fetch_media_player_state = AsyncMock(return_value=None)
    return mc


@pytest.fixture
def handler(mock_home_assistant, mock_media_controller):
    h = MusicCommandHandler(
        home_assistant=mock_home_assistant,
        media_controller=mock_media_controller,
        media_player_entity="media_player.bedroom",
    )
    h.set_speak_callback(AsyncMock())
    h.set_log_response_callback(Mock())
    return h


class TestMaybeHandle:
    """Tests for maybe_handle() dispatch."""

    @pytest.mark.anyio
    async def test_empty_transcript_returns_false(self, handler):
        assert await handler.maybe_handle("") is False

    @pytest.mark.anyio
    async def test_no_home_assistant_returns_false(self, mock_media_controller):
        h = MusicCommandHandler(
            home_assistant=None,
            media_controller=mock_media_controller,
            media_player_entity="media_player.bedroom",
        )
        assert await h.maybe_handle("pause the music") is False

    @pytest.mark.anyio
    async def test_no_entity_returns_false(self, mock_home_assistant, mock_media_controller):
        h = MusicCommandHandler(
            home_assistant=mock_home_assistant,
            media_controller=mock_media_controller,
            media_player_entity=None,
        )
        assert await h.maybe_handle("pause the music") is False

    @pytest.mark.anyio
    async def test_unrecognized_returns_false(self, handler):
        assert await handler.maybe_handle("tell me a joke") is False

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        "phrase,service",
        [
            ("pause the music", "media_pause"),
            ("pause music", "media_pause"),
            ("stop the music", "media_stop"),
            ("stop music", "media_stop"),
            ("next song", "media_next_track"),
            ("skip song", "media_next_track"),
            ("next track", "media_next_track"),
        ],
    )
    async def test_control_commands(self, handler, phrase, service):
        result = await handler.maybe_handle(phrase)
        assert result is True
        handler.home_assistant.call_service.assert_awaited_once_with(
            "media_player", service, {"entity_id": "media_player.bedroom"}
        )
        handler._on_speak.assert_awaited_once()

    @pytest.mark.anyio
    async def test_control_command_speaks_and_logs(self, handler):
        await handler.maybe_handle("pause the music")
        handler._on_speak.assert_awaited_once_with("Paused the music.")
        handler._on_log_response.assert_called_once_with("music", "Paused the music.", "pulse")


class TestCallService:
    """Tests for _call_service error handling."""

    @pytest.mark.anyio
    async def test_ha_error_speaks_fallback(self, handler):
        from pulse.assistant.home_assistant import HomeAssistantError

        handler.home_assistant.call_service = AsyncMock(side_effect=HomeAssistantError("fail"))
        result = await handler.maybe_handle("pause the music")
        assert result is True
        handler._on_speak.assert_awaited_once_with("I couldn't control the music right now.")


class TestDescribeCurrentTrack:
    """Tests for track info queries."""

    @pytest.mark.anyio
    async def test_what_song_no_state(self, handler):
        handler.media_controller.fetch_media_player_state.return_value = None
        result = await handler.maybe_handle("what song is this")
        assert result is True
        handler._on_speak.assert_awaited_once_with("I couldn't reach the player for that info.")

    @pytest.mark.anyio
    async def test_what_song_not_playing(self, handler):
        handler.media_controller.fetch_media_player_state.return_value = {
            "state": "idle",
            "attributes": {},
        }
        result = await handler.maybe_handle("what song is this")
        assert result is True
        handler._on_speak.assert_awaited_once_with("Nothing is playing right now.")

    @pytest.mark.anyio
    async def test_what_song_with_artist_and_title(self, handler):
        handler.media_controller.fetch_media_player_state.return_value = {
            "state": "playing",
            "attributes": {"media_title": "Hey Jude", "media_artist": "The Beatles"},
        }
        result = await handler.maybe_handle("what song is this")
        assert result is True
        handler._on_speak.assert_awaited_once()
        spoken = handler._on_speak.call_args[0][0]
        assert "The Beatles" in spoken
        assert "Hey Jude" in spoken

    @pytest.mark.anyio
    async def test_what_song_title_only(self, handler):
        handler.media_controller.fetch_media_player_state.return_value = {
            "state": "playing",
            "attributes": {"media_title": "Hey Jude"},
        }
        result = await handler.maybe_handle("what's playing")
        assert result is True
        spoken = handler._on_speak.call_args[0][0]
        assert "Hey Jude" in spoken

    @pytest.mark.anyio
    async def test_what_song_artist_only(self, handler):
        handler.media_controller.fetch_media_player_state.return_value = {
            "state": "playing",
            "attributes": {"media_artist": "The Beatles"},
        }
        result = await handler.maybe_handle("what song is this")
        assert result is True
        spoken = handler._on_speak.call_args[0][0]
        assert "The Beatles" in spoken

    @pytest.mark.anyio
    async def test_who_is_this_emphasizes_artist(self, handler):
        handler.media_controller.fetch_media_player_state.return_value = {
            "state": "playing",
            "attributes": {"media_artist": "The Beatles"},
        }
        result = await handler.maybe_handle("who is this")
        assert result is True
        spoken = handler._on_speak.call_args[0][0]
        assert spoken == "This is The Beatles."

    @pytest.mark.anyio
    async def test_paused_state_still_describes(self, handler):
        handler.media_controller.fetch_media_player_state.return_value = {
            "state": "paused",
            "attributes": {"media_title": "Hey Jude", "media_artist": "The Beatles"},
        }
        result = await handler.maybe_handle("what song is this")
        assert result is True
        handler._on_speak.assert_awaited_once()


class TestCallbacks:
    """Tests for callback configuration."""

    @pytest.mark.anyio
    async def test_no_speak_callback_still_returns_true(self, mock_home_assistant, mock_media_controller):
        h = MusicCommandHandler(
            home_assistant=mock_home_assistant,
            media_controller=mock_media_controller,
            media_player_entity="media_player.bedroom",
        )
        # No callbacks set
        result = await h.maybe_handle("pause the music")
        assert result is True

    @pytest.mark.anyio
    async def test_no_log_callback_still_works(self, mock_home_assistant, mock_media_controller):
        h = MusicCommandHandler(
            home_assistant=mock_home_assistant,
            media_controller=mock_media_controller,
            media_player_entity="media_player.bedroom",
        )
        h.set_speak_callback(AsyncMock())
        # No log callback
        result = await h.maybe_handle("pause the music")
        assert result is True
