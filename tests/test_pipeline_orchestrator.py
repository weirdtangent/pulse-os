"""Tests for PipelineOrchestrator and AssistRunTracker."""

from __future__ import annotations

import base64
import time
from unittest.mock import Mock

import pytest
from pulse.assistant.pipeline_orchestrator import AssistRunTracker, PipelineOrchestrator

# ============================================================================
# AssistRunTracker Tests
# ============================================================================


class TestAssistRunTracker:
    def test_initial_state(self):
        tracker = AssistRunTracker(pipeline="pulse", wake_word="hey_pulse")
        assert tracker.pipeline == "pulse"
        assert tracker.wake_word == "hey_pulse"
        assert tracker.current_stage is None
        assert tracker.stage_durations == {}

    def test_begin_stage(self):
        tracker = AssistRunTracker(pipeline="pulse", wake_word="hey_pulse")
        tracker.begin_stage("listening")
        assert tracker.current_stage == "listening"

    def test_stage_duration_tracking(self):
        tracker = AssistRunTracker(pipeline="pulse", wake_word="hey_pulse")
        tracker.begin_stage("listening")
        # Simulate time passing
        tracker.stage_start = time.monotonic() - 0.5
        tracker.begin_stage("thinking")
        assert "listening" in tracker.stage_durations
        assert tracker.stage_durations["listening"] >= 400  # At least 400ms

    def test_finalize(self):
        tracker = AssistRunTracker(pipeline="pulse", wake_word="hey_pulse")
        tracker.begin_stage("listening")
        tracker.stage_start = time.monotonic() - 0.1
        result = tracker.finalize("success")
        assert result["pipeline"] == "pulse"
        assert result["wake_word"] == "hey_pulse"
        assert result["status"] == "success"
        assert "total_ms" in result
        assert isinstance(result["stages"], dict)
        assert "listening" in result["stages"]

    def test_finalize_no_stages(self):
        tracker = AssistRunTracker(pipeline="pulse", wake_word="test")
        result = tracker.finalize("no_audio")
        assert result["status"] == "no_audio"
        assert result["stages"] == {}

    def test_multiple_stages(self):
        tracker = AssistRunTracker(pipeline="home_assistant", wake_word="hey_jarvis")
        tracker.begin_stage("listening")
        tracker.stage_start = time.monotonic() - 0.1
        tracker.begin_stage("thinking")
        tracker.stage_start = time.monotonic() - 0.2
        tracker.begin_stage("speaking")
        tracker.stage_start = time.monotonic() - 0.05
        result = tracker.finalize("success")
        assert set(result["stages"].keys()) == {"listening", "thinking", "speaking"}


# ============================================================================
# HA Response Extraction Tests (static methods)
# ============================================================================


class TestExtractHaSpeech:
    def test_valid_speech(self):
        result = {"response": {"speech": {"plain": {"speech": "The light is on."}}}}
        assert PipelineOrchestrator._extract_ha_speech(result) == "The light is on."

    def test_strips_whitespace(self):
        result = {"response": {"speech": {"plain": {"speech": "  Hello  "}}}}
        assert PipelineOrchestrator._extract_ha_speech(result) == "Hello"

    def test_missing_response(self):
        assert PipelineOrchestrator._extract_ha_speech({}) is None

    def test_invalid_response_type(self):
        assert PipelineOrchestrator._extract_ha_speech({"response": "string"}) is None

    def test_missing_speech_block(self):
        assert PipelineOrchestrator._extract_ha_speech({"response": {}}) is None

    def test_missing_plain(self):
        assert PipelineOrchestrator._extract_ha_speech({"response": {"speech": {}}}) is None

    def test_non_string_speech(self):
        result = {"response": {"speech": {"plain": {"speech": 42}}}}
        assert PipelineOrchestrator._extract_ha_speech(result) is None


class TestExtractHaTranscript:
    def test_stt_output(self):
        result = {"stt_output": {"text": "turn on the lights"}}
        assert PipelineOrchestrator._extract_ha_transcript(result) == "turn on the lights"

    def test_intent_input_fallback(self):
        result = {"intent_input": {"text": "turn off the fan"}}
        assert PipelineOrchestrator._extract_ha_transcript(result) == "turn off the fan"

    def test_stt_preferred_over_intent(self):
        result = {
            "stt_output": {"text": "from stt"},
            "intent_input": {"text": "from intent"},
        }
        assert PipelineOrchestrator._extract_ha_transcript(result) == "from stt"

    def test_empty_result(self):
        assert PipelineOrchestrator._extract_ha_transcript({}) is None

    def test_strips_whitespace(self):
        result = {"stt_output": {"text": "  hello  "}}
        assert PipelineOrchestrator._extract_ha_transcript(result) == "hello"

    def test_non_string_text(self):
        result = {"stt_output": {"text": 123}}
        assert PipelineOrchestrator._extract_ha_transcript(result) is None


class TestExtractHaTtsAudio:
    def test_valid_tts_audio(self):
        audio_data = b"fake audio data"
        result = {
            "tts_output": {
                "audio": base64.b64encode(audio_data).decode(),
                "sample_rate": 16000,
                "sample_width": 2,
                "channels": 1,
            }
        }
        extracted = PipelineOrchestrator._extract_ha_tts_audio(result)
        assert extracted is not None
        assert extracted["audio"] == audio_data
        assert extracted["rate"] == 16000
        assert extracted["width"] == 2
        assert extracted["channels"] == 1

    def test_missing_tts_output(self):
        assert PipelineOrchestrator._extract_ha_tts_audio({}) is None

    def test_invalid_audio_base64(self):
        result = {
            "tts_output": {
                "audio": "not-valid-base64!!!",
                "sample_rate": 16000,
                "sample_width": 2,
                "channels": 1,
            }
        }
        assert PipelineOrchestrator._extract_ha_tts_audio(result) is None

    def test_missing_sample_rate(self):
        result = {
            "tts_output": {
                "audio": base64.b64encode(b"data").decode(),
                "sample_rate": 0,
                "sample_width": 2,
                "channels": 1,
            }
        }
        assert PipelineOrchestrator._extract_ha_tts_audio(result) is None

    def test_missing_channels(self):
        result = {
            "tts_output": {
                "audio": base64.b64encode(b"data").decode(),
                "sample_rate": 16000,
                "sample_width": 2,
                "channels": 0,
            }
        }
        assert PipelineOrchestrator._extract_ha_tts_audio(result) is None

    def test_non_string_audio(self):
        result = {
            "tts_output": {
                "audio": 12345,
                "sample_rate": 16000,
                "sample_width": 2,
                "channels": 1,
            }
        }
        assert PipelineOrchestrator._extract_ha_tts_audio(result) is None


class TestDisplayWakeWord:
    def test_underscore_replacement(self):
        assert PipelineOrchestrator.display_wake_word("hey_pulse") == "hey pulse"

    def test_strips_whitespace(self):
        assert PipelineOrchestrator.display_wake_word(" hey_pulse ") == "hey pulse"

    def test_no_underscores(self):
        assert PipelineOrchestrator.display_wake_word("pulse") == "pulse"


# ============================================================================
# Orchestrator Stage/Metrics Tests
# ============================================================================


@pytest.fixture
def mock_orchestrator_deps():
    """Create minimal mocked dependencies for PipelineOrchestrator."""
    config = Mock()
    config.mqtt = Mock()
    config.mqtt.topic_base = "test-device"
    config.log_transcripts = False
    config.tts_endpoint = None
    config.tts_voice = None
    config.stt_endpoint = None
    config.language = "en"
    config.mic = Mock()
    config.response_topic = "test-device/assistant/response"
    config.transcript_topic = "test-device/assistant/transcript"
    config.action_topic = "test-device/assistant/action"
    config.home_assistant = Mock()
    config.sounds = Mock()

    publisher = Mock()
    publisher._publish_message = Mock()
    publisher._publish_state = Mock()
    publisher._publish_routine_overlay = Mock()

    preference_manager = Mock()
    preference_manager.log_llm_messages = False

    return {
        "config": config,
        "mqtt": Mock(),
        "publisher": publisher,
        "preference_manager": preference_manager,
        "conversation_manager": Mock(),
        "wake_detector": Mock(),
        "media_controller": Mock(),
        "music_handler": Mock(),
        "schedule_shortcuts": Mock(),
        "info_query_handler": Mock(),
        "schedule_service": Mock(),
        "actions": Mock(),
        "routines": Mock(),
        "home_assistant": None,
        "scheduler": Mock(),
        "player": Mock(),
        "sound_library": Mock(),
    }


@pytest.fixture
def orchestrator(mock_orchestrator_deps, mock_logger):
    orch = PipelineOrchestrator(**mock_orchestrator_deps, logger=mock_logger)
    prefs = Mock()
    prefs.wake_sound = False
    prefs.ha_response_mode = "full"
    prefs.ha_tone_sound = None
    orch.set_preferences_getter(lambda: prefs)
    return orch


class TestSetAssistStage:
    def test_publishes_stage(self, orchestrator):
        orchestrator._set_assist_stage("pulse", "listening", {"wake_word": "hey_pulse"})
        pub = orchestrator.publisher
        assert pub._publish_message.call_count >= 3  # in_progress + stage + pipeline + wake
        assert pub._publish_state.call_count == 1

    def test_idle_publishes_off(self, orchestrator):
        orchestrator._set_assist_stage("pulse", "idle")
        calls = orchestrator.publisher._publish_message.call_args_list
        in_progress_call = [c for c in calls if "in_progress" in str(c)]
        assert any("OFF" in str(c) for c in in_progress_call)

    def test_error_publishes_off(self, orchestrator):
        orchestrator._set_assist_stage("pulse", "error")
        calls = orchestrator.publisher._publish_message.call_args_list
        in_progress_call = [c for c in calls if "in_progress" in str(c)]
        assert any("OFF" in str(c) for c in in_progress_call)

    def test_listening_publishes_on(self, orchestrator):
        orchestrator._set_assist_stage("pulse", "listening")
        calls = orchestrator.publisher._publish_message.call_args_list
        in_progress_call = [c for c in calls if "in_progress" in str(c)]
        assert any("ON" in str(c) for c in in_progress_call)


class TestFinalizeAssistRun:
    def test_publishes_metrics(self, orchestrator):
        tracker = AssistRunTracker("pulse", "hey_pulse")
        tracker.begin_stage("listening")
        orchestrator._current_tracker = tracker
        orchestrator._finalize_assist_run(status="success")
        pub = orchestrator.publisher
        # Should publish metrics + stage change to idle
        assert pub._publish_message.call_count >= 1
        assert orchestrator._current_tracker is None

    def test_noop_without_tracker(self, orchestrator):
        orchestrator._current_tracker = None
        orchestrator._finalize_assist_run(status="success")
        orchestrator.publisher._publish_message.assert_not_called()


class TestLogAssistantResponse:
    def test_no_log_when_disabled(self, orchestrator):
        orchestrator.preference_manager.log_llm_messages = False
        orchestrator._log_assistant_response("test", "Hello world")
        # Should not raise

    def test_no_log_when_text_is_none(self, orchestrator):
        orchestrator.preference_manager.log_llm_messages = True
        orchestrator._log_assistant_response("test", None)
        # Should not raise

    def test_logs_when_enabled(self, orchestrator):
        orchestrator.preference_manager.log_llm_messages = True
        orchestrator._log_assistant_response("test", "Hello world")
        # Should not raise (method is a near no-op currently)


class TestCurrentTracker:
    def test_initially_none(self, orchestrator):
        assert orchestrator.current_tracker is None

    def test_returns_tracker(self, orchestrator):
        tracker = AssistRunTracker("pulse", "test")
        orchestrator._current_tracker = tracker
        assert orchestrator.current_tracker is tracker


class TestLLMProviderGetter:
    def test_raises_without_getter(self, mock_orchestrator_deps, mock_logger):
        orch = PipelineOrchestrator(**mock_orchestrator_deps, logger=mock_logger)
        with pytest.raises(RuntimeError, match="LLM provider getter not set"):
            _ = orch.llm

    def test_returns_from_getter(self, orchestrator):
        mock_llm = Mock()
        orchestrator.set_llm_provider_getter(lambda: mock_llm)
        assert orchestrator.llm is mock_llm
