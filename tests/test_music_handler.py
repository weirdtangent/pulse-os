"""Tests for MusicCommandHandler."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, Mock

import pytest
from pulse.assistant.music_handler import MusicCommandHandler, _build_player_name_map


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
def mock_llm_provider():
    provider = AsyncMock()
    provider.simple_chat = AsyncMock(return_value='{"companion": null}')
    return provider


@pytest.fixture
def handler(mock_home_assistant, mock_media_controller, mock_llm_provider):
    h = MusicCommandHandler(
        home_assistant=mock_home_assistant,
        media_controller=mock_media_controller,
        media_player_entity="media_player.bedroom",
        media_player_entities=(
            "media_player.pulse_bedroom",
            "media_player.pulse_great_room",
            "media_player.btsnap_gr",
        ),
        llm_provider_getter=lambda: mock_llm_provider,
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


class TestPlayCommands:
    """Tests for play media commands."""

    @pytest.mark.anyio
    async def test_play_song_calls_play_media(self, handler, mock_llm_provider):
        result = await handler.maybe_handle("play Home by the Sea by Genesis")
        assert result is True
        handler.home_assistant.call_service.assert_awaited_once_with(
            "media_player",
            "play_media",
            {
                "entity_id": "media_player.bedroom",
                "media_content_id": "Home by the Sea by Genesis",
                "media_content_type": "music",
            },
        )

    @pytest.mark.anyio
    async def test_play_on_specific_player(self, handler, mock_llm_provider):
        result = await handler.maybe_handle("play stairway on bedroom")
        assert result is True
        handler.home_assistant.call_service.assert_awaited_once_with(
            "media_player",
            "play_media",
            {
                "entity_id": "media_player.pulse_bedroom",
                "media_content_id": "stairway",
                "media_content_type": "music",
            },
        )

    @pytest.mark.anyio
    async def test_play_on_full_player_name(self, handler, mock_llm_provider):
        result = await handler.maybe_handle("play jazz on pulse great room")
        assert result is True
        handler.home_assistant.call_service.assert_awaited_once_with(
            "media_player",
            "play_media",
            {
                "entity_id": "media_player.pulse_great_room",
                "media_content_id": "jazz",
                "media_content_type": "music",
            },
        )

    @pytest.mark.anyio
    async def test_play_on_in_title_not_player(self, handler, mock_llm_provider):
        """'On the Run' contains ' on ' but 'the Run' isn't a player name."""
        result = await handler.maybe_handle("play On the Run by Pink Floyd")
        assert result is True
        handler.home_assistant.call_service.assert_awaited_once_with(
            "media_player",
            "play_media",
            {
                "entity_id": "media_player.bedroom",
                "media_content_id": "On the Run by Pink Floyd",
                "media_content_type": "music",
            },
        )

    @pytest.mark.anyio
    async def test_play_speaks_confirmation(self, handler, mock_llm_provider):
        await handler.maybe_handle("play Hey Jude")
        handler._on_speak.assert_awaited_once_with("Playing Hey Jude.")
        handler._on_log_response.assert_called_once_with("music", "Playing Hey Jude.", "pulse")

    @pytest.mark.anyio
    async def test_play_ha_error_speaks_fallback(self, handler, mock_llm_provider):
        from pulse.assistant.home_assistant import HomeAssistantError

        handler.home_assistant.call_service = AsyncMock(side_effect=HomeAssistantError("fail"))
        result = await handler.maybe_handle("play Hey Jude")
        assert result is True
        handler._on_speak.assert_awaited_once_with("I couldn't play that right now.")

    @pytest.mark.anyio
    async def test_play_no_entity_returns_false(self, mock_home_assistant, mock_media_controller):
        h = MusicCommandHandler(
            home_assistant=mock_home_assistant,
            media_controller=mock_media_controller,
            media_player_entity=None,
        )
        assert await h.maybe_handle("play Hey Jude") is False

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        "phrase",
        [
            "can you play Hey Jude",
            "could you play Hey Jude",
            "please play Hey Jude",
            "put on Hey Jude",
            "i want to hear Hey Jude",
        ],
    )
    async def test_play_prefix_variants(self, handler, mock_llm_provider, phrase):
        result = await handler.maybe_handle(phrase)
        assert result is True
        handler._on_speak.assert_awaited_once_with("Playing Hey Jude.")

    @pytest.mark.anyio
    async def test_unrelated_transcript_returns_false(self, handler):
        """Phrases like 'tell me about the play Hamilton' don't trigger play."""
        assert await handler.maybe_handle("tell me about the play Hamilton") is False

    @pytest.mark.anyio
    async def test_bare_play_returns_false(self, handler):
        assert await handler.maybe_handle("play") is False


class TestCompanionTrack:
    """Tests for companion track lookup and queuing."""

    @pytest.mark.anyio
    async def test_companion_track_queued(self, handler, mock_llm_provider):
        mock_llm_provider.simple_chat.return_value = json.dumps({"companion": "Second Home by the Sea by Genesis"})
        result = await handler.maybe_handle("play Home by the Sea by Genesis")
        assert result is True
        calls = handler.home_assistant.call_service.call_args_list
        assert len(calls) == 2
        # First call: primary track
        primary_args = calls[0][0]
        assert primary_args[0] == "media_player"
        assert primary_args[1] == "play_media"
        assert primary_args[2]["media_content_id"] == "Home by the Sea by Genesis"
        # Second call: companion with enqueue
        companion_args = calls[1][0]
        assert companion_args[2]["media_content_id"] == "Second Home by the Sea by Genesis"
        assert companion_args[2]["enqueue"] == "next"
        # Confirmation mentions companion
        spoken = handler._on_speak.call_args[0][0]
        assert "followed by Second Home by the Sea by Genesis" in spoken

    @pytest.mark.anyio
    async def test_companion_lookup_timeout_plays_single(self, handler, mock_llm_provider):
        mock_llm_provider.simple_chat.side_effect = TimeoutError()
        result = await handler.maybe_handle("play Eruption by Van Halen")
        assert result is True
        handler.home_assistant.call_service.assert_awaited_once()
        spoken = handler._on_speak.call_args[0][0]
        assert spoken == "Playing Eruption by Van Halen."

    @pytest.mark.anyio
    async def test_companion_null_plays_single(self, handler, mock_llm_provider):
        mock_llm_provider.simple_chat.return_value = '{"companion": null}'
        result = await handler.maybe_handle("play Bohemian Rhapsody")
        assert result is True
        handler.home_assistant.call_service.assert_awaited_once()
        spoken = handler._on_speak.call_args[0][0]
        assert spoken == "Playing Bohemian Rhapsody."

    @pytest.mark.anyio
    async def test_no_llm_provider_plays_single(self, mock_home_assistant, mock_media_controller):
        h = MusicCommandHandler(
            home_assistant=mock_home_assistant,
            media_controller=mock_media_controller,
            media_player_entity="media_player.bedroom",
            llm_provider_getter=None,
        )
        h.set_speak_callback(AsyncMock())
        h.set_log_response_callback(Mock())
        result = await h.maybe_handle("play Hey Jude")
        assert result is True
        mock_home_assistant.call_service.assert_awaited_once()


class TestBuildPlayerNameMap:
    """Tests for _build_player_name_map helper."""

    def test_basic_mapping(self):
        result = _build_player_name_map(("media_player.pulse_bedroom",))
        assert result["pulse bedroom"] == "media_player.pulse_bedroom"
        assert result["bedroom"] == "media_player.pulse_bedroom"

    def test_non_pulse_entity(self):
        result = _build_player_name_map(("media_player.btsnap_gr",))
        assert result["btsnap gr"] == "media_player.btsnap_gr"
        assert "gr" not in result  # no "pulse " prefix to strip

    def test_empty_tuple(self):
        assert _build_player_name_map(()) == {}
