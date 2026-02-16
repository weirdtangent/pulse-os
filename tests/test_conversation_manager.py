"""Tests for conversation_manager module."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, Mock

import pytest
from pulse.assistant.conversation_manager import (
    CONVERSATION_STOP_PHRASES,
    ConversationManager,
    build_conversation_stop_prefixes,
    evaluate_follow_up_transcript,
    is_conversation_stop_command,
    normalize_conversation_stop_text,
    should_listen_for_follow_up,
)

pytestmark = pytest.mark.anyio


# ---------------------------------------------------------------------------
# normalize_conversation_stop_text
# ---------------------------------------------------------------------------


class TestNormalizeConversationStopText:
    def test_empty_string(self):
        assert normalize_conversation_stop_text("") == ""

    def test_none_input(self):
        assert normalize_conversation_stop_text(None) == ""  # type: ignore[arg-type]

    def test_lowercases(self):
        assert normalize_conversation_stop_text("NEVER MIND") == "never mind"

    def test_strips_punctuation(self):
        assert normalize_conversation_stop_text("that's all!") == "thats all"

    def test_removes_apostrophes(self):
        assert normalize_conversation_stop_text("that's all") == "thats all"

    def test_collapses_whitespace(self):
        assert normalize_conversation_stop_text("  never   mind  ") == "never mind"

    def test_strips_trailing_please(self):
        assert normalize_conversation_stop_text("cancel please") == "cancel"

    def test_strips_trailing_thanks(self):
        assert normalize_conversation_stop_text("forget it thanks") == "forget it"

    def test_strips_trailing_thank_you(self):
        assert normalize_conversation_stop_text("forget it thank you") == "forget it"

    def test_strips_trailing_for_now(self):
        assert normalize_conversation_stop_text("nothing for now") == "nothing"

    def test_strips_multiple_suffixes(self):
        assert normalize_conversation_stop_text("cancel please thanks") == "cancel"

    def test_strips_wake_word_prefix(self):
        result = normalize_conversation_stop_text("hey pulse never mind", prefixes=["hey pulse"])
        assert result == "never mind"

    def test_prefix_exact_match_returns_empty(self):
        result = normalize_conversation_stop_text("hey pulse", prefixes=["hey pulse"])
        assert result == ""

    def test_prefix_not_matching(self):
        result = normalize_conversation_stop_text("never mind", prefixes=["hey pulse"])
        assert result == "never mind"

    def test_only_punctuation_returns_empty(self):
        assert normalize_conversation_stop_text("!!!") == ""


# ---------------------------------------------------------------------------
# CONVERSATION_STOP_PHRASES set
# ---------------------------------------------------------------------------


class TestConversationStopPhrases:
    def test_common_phrases_present(self):
        assert "never mind" in CONVERSATION_STOP_PHRASES
        assert "forget it" in CONVERSATION_STOP_PHRASES
        assert "cancel" in CONVERSATION_STOP_PHRASES
        assert "thats all" in CONVERSATION_STOP_PHRASES
        assert "im good" in CONVERSATION_STOP_PHRASES

    def test_no_empty_phrases(self):
        assert "" not in CONVERSATION_STOP_PHRASES


# ---------------------------------------------------------------------------
# should_listen_for_follow_up
# ---------------------------------------------------------------------------


class TestShouldListenForFollowUp:
    def test_returns_false_always(self):
        assert should_listen_for_follow_up(None) is False

    def test_returns_false_with_result(self):
        from pulse.assistant.llm import LLMResult

        result = LLMResult(response="test", actions=[], follow_up=True)
        assert should_listen_for_follow_up(result) is False


# ---------------------------------------------------------------------------
# evaluate_follow_up_transcript
# ---------------------------------------------------------------------------


class TestEvaluateFollowUpTranscript:
    def test_none_transcript(self):
        useful, normalized = evaluate_follow_up_transcript(None)
        assert useful is False
        assert normalized is None

    def test_empty_transcript(self):
        useful, normalized = evaluate_follow_up_transcript("")
        assert useful is False
        assert normalized is None

    def test_whitespace_only(self):
        useful, normalized = evaluate_follow_up_transcript("   ")
        assert useful is False
        assert normalized is None

    def test_valid_transcript(self):
        useful, normalized = evaluate_follow_up_transcript("Turn on the lights")
        assert useful is True
        assert normalized == "turn on the lights"

    def test_duplicate_of_previous(self):
        useful, normalized = evaluate_follow_up_transcript("hello", previous_normalized="hello")
        assert useful is False
        assert normalized == "hello"

    def test_noise_token_you(self):
        useful, normalized = evaluate_follow_up_transcript("you")
        assert useful is False
        assert normalized == "you"

    def test_noise_token_ya(self):
        useful, normalized = evaluate_follow_up_transcript("ya")
        assert useful is False
        assert normalized == "ya"


# ---------------------------------------------------------------------------
# is_conversation_stop_command
# ---------------------------------------------------------------------------


class TestIsConversationStopCommand:
    def test_stop_phrase_detected(self):
        assert is_conversation_stop_command("never mind", ()) is True

    def test_non_stop_phrase(self):
        assert is_conversation_stop_command("turn on the lights", ()) is False

    def test_none_transcript(self):
        assert is_conversation_stop_command(None, ()) is False

    def test_stop_phrase_with_prefix_stripped(self):
        assert is_conversation_stop_command("hey pulse cancel", ("hey pulse",)) is True

    def test_stop_phrase_with_suffix(self):
        assert is_conversation_stop_command("cancel please", ()) is True


# ---------------------------------------------------------------------------
# build_conversation_stop_prefixes
# ---------------------------------------------------------------------------


class TestBuildConversationStopPrefixes:
    def test_builds_from_wake_models(self):
        config = Mock()
        config.wake_models = ["hey_pulse", "ok_google"]
        prefixes = build_conversation_stop_prefixes(config)
        assert "Hey Pulse" in prefixes
        assert "hey_pulse" in prefixes
        assert "pulse" in prefixes
        assert "Ok Google" in prefixes


# ---------------------------------------------------------------------------
# ConversationManager
# ---------------------------------------------------------------------------


class TestConversationManager:
    def _make_manager(self):
        config = Mock()
        config.wake_models = ["hey_pulse"]
        config.mic = Mock(chunk_ms=30, width=2, bytes_per_chunk=960)
        config.phrase = Mock(min_seconds=0.5, max_seconds=10.0, silence_ms=300, rms_floor=200)
        mic = AsyncMock()
        return ConversationManager(config=config, mic=mic, compute_rms=lambda c, w: 0)

    def test_is_conversation_stop_delegates(self):
        mgr = self._make_manager()
        assert mgr.is_conversation_stop("never mind") is True
        assert mgr.is_conversation_stop("turn on lights") is False

    def test_evaluate_follow_up_delegates(self):
        mgr = self._make_manager()
        useful, _ = mgr.evaluate_follow_up("hello world")
        assert useful is True

    def test_update_last_response_end(self):
        mgr = self._make_manager()
        mgr.update_last_response_end(123.0)
        assert mgr._last_response_end == 123.0

    async def test_wait_for_speech_tail_no_response(self):
        mgr = self._make_manager()
        mgr._last_response_end = None
        await mgr.wait_for_speech_tail()  # Should return immediately

    async def test_wait_for_speech_tail_already_elapsed(self):
        mgr = self._make_manager()
        mgr._last_response_end = time.monotonic() - 10.0
        await mgr.wait_for_speech_tail()  # Should return immediately

    async def test_record_phrase_returns_bytes(self):
        mgr = self._make_manager()
        mgr.mic.read_chunk = AsyncMock(return_value=b"\x00" * 960)
        # compute_rms returns 0 (below rms_floor), so silence detection triggers
        result = await mgr.record_phrase()
        assert isinstance(result, bytes)
        assert len(result) > 0

    async def test_record_follow_up_phrase(self):
        mgr = self._make_manager()
        mgr.mic.read_chunk = AsyncMock(return_value=b"\x00" * 960)
        result = await mgr.record_follow_up_phrase()
        assert isinstance(result, bytes)
