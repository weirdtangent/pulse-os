"""Tests for InfoQueryHandler."""

from __future__ import annotations

import json
from dataclasses import dataclass
from unittest.mock import AsyncMock, Mock

import pytest
from pulse.assistant.info_query_handler import InfoQueryHandler


@dataclass
class FakeInfoResponse:
    category: str
    text: str
    display: str | None = None
    card: dict | None = None


@pytest.fixture
def mock_info_service():
    service = AsyncMock()
    service.maybe_answer = AsyncMock(return_value=None)
    return service


@pytest.fixture
def mock_publisher():
    publisher = Mock()
    publisher._publish_message = Mock()
    publisher._publish_info_overlay = Mock()
    publisher._schedule_info_overlay_clear = Mock()
    return publisher


@pytest.fixture
def mock_media_controller():
    mc = Mock()
    mc.trigger_media_resume_after_response = Mock()
    return mc


@pytest.fixture
def handler(mock_info_service, mock_publisher, mock_media_controller):
    h = InfoQueryHandler(
        info_service=mock_info_service,
        publisher=mock_publisher,
        media_controller=mock_media_controller,
        response_topic="pulse/test/response",
        overlay_min_seconds=1.5,
        overlay_buffer_seconds=0.5,
    )
    h.set_speak_callback(AsyncMock())
    h.set_log_response_callback(Mock())
    h.set_tracker_provider(lambda: None)
    h.set_stage_callback(Mock())
    return h


class TestMaybeHandle:
    """Tests for maybe_handle() dispatch."""

    @pytest.mark.anyio
    async def test_no_info_service_returns_false(self, mock_publisher, mock_media_controller):
        h = InfoQueryHandler(
            info_service=None,
            publisher=mock_publisher,
            media_controller=mock_media_controller,
            response_topic="pulse/test/response",
        )
        assert await h.maybe_handle("what's the weather", "pulse") is False

    @pytest.mark.anyio
    async def test_no_match_returns_false(self, handler, mock_info_service):
        mock_info_service.maybe_answer.return_value = None
        assert await handler.maybe_handle("tell me a joke", "pulse") is False

    @pytest.mark.anyio
    async def test_match_returns_true(self, handler, mock_info_service):
        mock_info_service.maybe_answer.return_value = FakeInfoResponse(category="weather", text="It's sunny.")
        result = await handler.maybe_handle("what's the weather", "pulse")
        assert result is True

    @pytest.mark.anyio
    async def test_speaks_response(self, handler, mock_info_service):
        mock_info_service.maybe_answer.return_value = FakeInfoResponse(category="weather", text="It's sunny.")
        await handler.maybe_handle("what's the weather", "pulse")
        handler._on_speak.assert_awaited_once_with("It's sunny.")

    @pytest.mark.anyio
    async def test_publishes_response_message(self, handler, mock_info_service, mock_publisher):
        mock_info_service.maybe_answer.return_value = FakeInfoResponse(category="weather", text="It's sunny.")
        await handler.maybe_handle("what's the weather", "jarvis")
        mock_publisher._publish_message.assert_called_once()
        topic, payload_str = mock_publisher._publish_message.call_args[0]
        assert topic == "pulse/test/response"
        payload = json.loads(payload_str)
        assert payload["text"] == "It's sunny."
        assert payload["wake_word"] == "jarvis"
        assert payload["info_category"] == "weather"

    @pytest.mark.anyio
    async def test_follow_up_flag(self, handler, mock_info_service, mock_publisher):
        mock_info_service.maybe_answer.return_value = FakeInfoResponse(category="news", text="Top story.")
        await handler.maybe_handle("top headlines", "pulse", follow_up=True)
        payload = json.loads(mock_publisher._publish_message.call_args[0][1])
        assert payload["follow_up"] is True

    @pytest.mark.anyio
    async def test_logs_response(self, handler, mock_info_service):
        mock_info_service.maybe_answer.return_value = FakeInfoResponse(category="weather", text="It's sunny.")
        await handler.maybe_handle("what's the weather", "pulse")
        handler._on_log_response.assert_called_once_with("info:weather", "It's sunny.", "pulse")

    @pytest.mark.anyio
    async def test_triggers_media_resume(self, handler, mock_info_service, mock_media_controller):
        mock_info_service.maybe_answer.return_value = FakeInfoResponse(category="weather", text="It's sunny.")
        await handler.maybe_handle("what's the weather", "pulse")
        mock_media_controller.trigger_media_resume_after_response.assert_called_once()


class TestOverlay:
    """Tests for info overlay publishing."""

    @pytest.mark.anyio
    async def test_overlay_published_with_display(self, handler, mock_info_service, mock_publisher):
        mock_info_service.maybe_answer.return_value = FakeInfoResponse(
            category="weather", text="It's sunny.", display="Sunny, 75F"
        )
        await handler.maybe_handle("what's the weather", "pulse")
        mock_publisher._publish_info_overlay.assert_called_once_with(text="Sunny, 75F", category="weather", extra=None)

    @pytest.mark.anyio
    async def test_overlay_published_with_card(self, handler, mock_info_service, mock_publisher):
        card = {"type": "weather", "temp": 75}
        mock_info_service.maybe_answer.return_value = FakeInfoResponse(
            category="weather", text="It's sunny.", card=card
        )
        await handler.maybe_handle("what's the weather", "pulse")
        mock_publisher._publish_info_overlay.assert_called_once_with(text="It's sunny.", category="weather", extra=card)

    @pytest.mark.anyio
    async def test_overlay_clear_scheduled(self, handler, mock_info_service, mock_publisher):
        mock_info_service.maybe_answer.return_value = FakeInfoResponse(
            category="weather", text="It's sunny and warm today."
        )
        await handler.maybe_handle("what's the weather", "pulse")
        mock_publisher._schedule_info_overlay_clear.assert_called_once()
        delay = mock_publisher._schedule_info_overlay_clear.call_args[0][0]
        assert delay >= 1.5  # at least overlay_min_seconds


class TestStageCallbacks:
    """Tests for stage and tracker integration."""

    @pytest.mark.anyio
    async def test_stage_callback_called(self, handler, mock_info_service):
        mock_info_service.maybe_answer.return_value = FakeInfoResponse(category="weather", text="It's sunny.")
        await handler.maybe_handle("what's the weather", "pulse")
        handler._stage_callback.assert_called_once()
        args = handler._stage_callback.call_args[0]
        assert args[0] == "pulse"
        assert args[1] == "speaking"
        assert args[2]["wake_word"] == "pulse"
        assert args[2]["info_category"] == "weather"

    @pytest.mark.anyio
    async def test_tracker_begin_stage_called(self, handler, mock_info_service):
        tracker = Mock()
        handler.set_tracker_provider(lambda: tracker)
        mock_info_service.maybe_answer.return_value = FakeInfoResponse(category="weather", text="It's sunny.")
        await handler.maybe_handle("what's the weather", "pulse")
        tracker.begin_stage.assert_called_once_with("speaking")


class TestEstimateSpeechDuration:
    """Tests for the static estimate_speech_duration method."""

    def test_single_word(self):
        assert InfoQueryHandler.estimate_speech_duration("Hello") == pytest.approx(0.4)

    def test_ten_words(self):
        text = "one two three four five six seven eight nine ten"
        assert InfoQueryHandler.estimate_speech_duration(text) == pytest.approx(4.0)

    def test_empty_string(self):
        # max(1, 0) = 1 word → 0.4s
        assert InfoQueryHandler.estimate_speech_duration("") == pytest.approx(0.4)
