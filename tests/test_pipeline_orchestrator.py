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


# ============================================================================
# Preferences Property Tests
# ============================================================================


class TestPreferencesProperty:
    def test_returns_from_getter(self, orchestrator):
        mock_prefs = Mock()
        orchestrator.set_preferences_getter(lambda: mock_prefs)
        assert orchestrator.preferences is mock_prefs

    def test_falls_back_to_config(self, mock_orchestrator_deps, mock_logger):
        config_prefs = Mock()
        mock_orchestrator_deps["config"].preferences = config_prefs
        orch = PipelineOrchestrator(**mock_orchestrator_deps, logger=mock_logger)
        # No getter set, should return config.preferences
        assert orch.preferences is config_prefs

    def test_getter_overrides_config(self, mock_orchestrator_deps, mock_logger):
        config_prefs = Mock()
        mock_orchestrator_deps["config"].preferences = config_prefs
        orch = PipelineOrchestrator(**mock_orchestrator_deps, logger=mock_logger)
        custom_prefs = Mock()
        orch.set_preferences_getter(lambda: custom_prefs)
        assert orch.preferences is custom_prefs
        assert orch.preferences is not config_prefs


# ============================================================================
# Setter Methods Tests
# ============================================================================


class TestSetters:
    def test_set_llm_provider_getter(self, orchestrator):
        assert orchestrator._get_llm is None

        def getter():
            return Mock()

        orchestrator.set_llm_provider_getter(getter)
        assert orchestrator._get_llm is getter

    def test_set_preferences_getter(self, mock_orchestrator_deps, mock_logger):
        orch = PipelineOrchestrator(**mock_orchestrator_deps, logger=mock_logger)
        assert orch._get_preferences is None

        def getter():
            return Mock()

        orch.set_preferences_getter(getter)
        assert orch._get_preferences is getter


# ============================================================================
# Home Assistant Prompt Actions Tests
# ============================================================================


class TestHomeAssistantPromptActions:
    def test_returns_empty_without_home_assistant(self, mock_orchestrator_deps, mock_logger):
        mock_orchestrator_deps["home_assistant"] = None
        orch = PipelineOrchestrator(**mock_orchestrator_deps, logger=mock_logger)
        assert orch._home_assistant_prompt_actions() == []

    def test_returns_actions_with_home_assistant(self, mock_orchestrator_deps, mock_logger):
        ha = Mock()
        mock_orchestrator_deps["home_assistant"] = ha
        routines = Mock()
        routines.prompt_entries.return_value = []
        mock_orchestrator_deps["routines"] = routines
        orch = PipelineOrchestrator(**mock_orchestrator_deps, logger=mock_logger)
        result = orch._home_assistant_prompt_actions()
        assert len(result) > 0
        # All entries should have slug and description
        for entry in result:
            assert "slug" in entry
            assert "description" in entry

    def test_includes_routine_entries(self, mock_orchestrator_deps, mock_logger):
        ha = Mock()
        mock_orchestrator_deps["home_assistant"] = ha
        routines = Mock()
        routine_entry = {"slug": "routine.bedtime", "description": "Run bedtime routine"}
        routines.prompt_entries.return_value = [routine_entry]
        mock_orchestrator_deps["routines"] = routines
        orch = PipelineOrchestrator(**mock_orchestrator_deps, logger=mock_logger)
        result = orch._home_assistant_prompt_actions()
        assert routine_entry in result

    def test_has_expected_action_slugs(self, mock_orchestrator_deps, mock_logger):
        ha = Mock()
        mock_orchestrator_deps["home_assistant"] = ha
        routines = Mock()
        routines.prompt_entries.return_value = []
        mock_orchestrator_deps["routines"] = routines
        orch = PipelineOrchestrator(**mock_orchestrator_deps, logger=mock_logger)
        result = orch._home_assistant_prompt_actions()
        slugs = [e["slug"] for e in result]
        assert any("ha.turn_on" in s for s in slugs)
        assert any("ha.turn_off" in s for s in slugs)
        assert any("ha.light_on" in s for s in slugs)
        assert any("ha.light_off" in s for s in slugs)
        assert any("ha.scene" in s for s in slugs)
        assert any("volume.set" in s for s in slugs)
        assert any("media.pause" in s for s in slugs)
        assert any("media.resume" in s for s in slugs)
        assert any("timer.start" in s for s in slugs)
        assert any("reminder.create" in s for s in slugs)


# ============================================================================
# Additional _set_assist_stage Tests
# ============================================================================


class TestSetAssistStageExtended:
    def test_no_extra(self, orchestrator):
        orchestrator._set_assist_stage("pulse", "listening")
        pub = orchestrator.publisher
        # Should not publish to wake topic when no extra
        wake_calls = [c for c in pub._publish_message.call_args_list if "last_wake_word" in str(c)]
        assert len(wake_calls) == 0

    def test_extra_without_wake_word(self, orchestrator):
        orchestrator._set_assist_stage("pulse", "listening", {"follow_up": True})
        pub = orchestrator.publisher
        wake_calls = [c for c in pub._publish_message.call_args_list if "last_wake_word" in str(c)]
        assert len(wake_calls) == 0

    def test_updates_internal_state(self, orchestrator):
        orchestrator._set_assist_stage("home_assistant", "thinking")
        assert orchestrator._assist_stage == "thinking"
        assert orchestrator._assist_pipeline == "home_assistant"

    def test_stage_topic_has_retain(self, orchestrator):
        orchestrator._set_assist_stage("pulse", "listening", {"wake_word": "test"})
        pub = orchestrator.publisher
        stage_calls = [
            c
            for c in pub._publish_message.call_args_list
            if "assistant/stage" in str(c) and "in_progress" not in str(c)
        ]
        assert len(stage_calls) == 1
        assert stage_calls[0].kwargs.get("retain") is True or stage_calls[0][1].get("retain") is True


# ============================================================================
# Additional _finalize_assist_run Tests
# ============================================================================


class TestFinalizeAssistRunExtended:
    def test_metrics_payload_structure(self, orchestrator):
        import json

        tracker = AssistRunTracker("pulse", "hey_pulse")
        tracker.begin_stage("listening")
        orchestrator._current_tracker = tracker
        orchestrator._finalize_assist_run(status="success")
        pub = orchestrator.publisher
        metrics_calls = [c for c in pub._publish_message.call_args_list if "metrics" in str(c)]
        assert len(metrics_calls) == 1
        payload = json.loads(metrics_calls[0][0][1])
        assert payload["pipeline"] == "pulse"
        assert payload["wake_word"] == "hey_pulse"
        assert payload["status"] == "success"
        assert "total_ms" in payload
        assert "stages" in payload
        assert "listening" in payload["stages"]

    def test_sets_stage_to_idle(self, orchestrator):
        tracker = AssistRunTracker("home_assistant", "hey_jarvis")
        tracker.begin_stage("speaking")
        orchestrator._current_tracker = tracker
        orchestrator._finalize_assist_run(status="error")
        assert orchestrator._assist_stage == "idle"
        assert orchestrator._assist_pipeline == "home_assistant"


# ============================================================================
# Additional Static Method Edge Case Tests
# ============================================================================


class TestExtractHaSpeechEdgeCases:
    def test_non_dict_result(self):
        # The method accepts dict but should handle non-dict gracefully
        assert PipelineOrchestrator._extract_ha_speech("not a dict") is None

    def test_speech_block_is_not_dict(self):
        result = {"response": {"speech": "just a string"}}
        assert PipelineOrchestrator._extract_ha_speech(result) is None

    def test_plain_is_not_dict(self):
        result = {"response": {"speech": {"plain": "just a string"}}}
        assert PipelineOrchestrator._extract_ha_speech(result) is None

    def test_empty_speech_string(self):
        result = {"response": {"speech": {"plain": {"speech": "   "}}}}
        assert PipelineOrchestrator._extract_ha_speech(result) == ""


class TestExtractHaTranscriptEdgeCases:
    def test_stt_output_not_dict(self):
        result = {"stt_output": "not a dict"}
        assert PipelineOrchestrator._extract_ha_transcript(result) is None

    def test_intent_input_not_dict(self):
        result = {"intent_input": "not a dict"}
        assert PipelineOrchestrator._extract_ha_transcript(result) is None

    def test_stt_output_empty_text(self):
        result = {"stt_output": {"text": "   "}}
        assert PipelineOrchestrator._extract_ha_transcript(result) == ""

    def test_falls_through_to_intent_when_stt_text_not_string(self):
        result = {"stt_output": {"text": None}, "intent_input": {"text": "hello"}}
        assert PipelineOrchestrator._extract_ha_transcript(result) == "hello"


class TestExtractHaTtsAudioEdgeCases:
    def test_tts_output_not_dict(self):
        result = {"tts_output": "not a dict"}
        assert PipelineOrchestrator._extract_ha_tts_audio(result) is None

    def test_missing_sample_width(self):
        result = {
            "tts_output": {
                "audio": base64.b64encode(b"data").decode(),
                "sample_rate": 16000,
                "sample_width": 0,
                "channels": 1,
            }
        }
        assert PipelineOrchestrator._extract_ha_tts_audio(result) is None

    def test_none_sample_rate(self):
        result = {
            "tts_output": {
                "audio": base64.b64encode(b"data").decode(),
                "sample_rate": None,
                "sample_width": 2,
                "channels": 1,
            }
        }
        assert PipelineOrchestrator._extract_ha_tts_audio(result) is None

    def test_missing_keys_default_to_zero(self):
        result = {
            "tts_output": {
                "audio": base64.b64encode(b"data").decode(),
            }
        }
        assert PipelineOrchestrator._extract_ha_tts_audio(result) is None


class TestDisplayWakeWordEdgeCases:
    def test_multiple_underscores(self):
        assert PipelineOrchestrator.display_wake_word("hey_there_pulse") == "hey there pulse"

    def test_empty_string(self):
        assert PipelineOrchestrator.display_wake_word("") == ""

    def test_only_underscores(self):
        assert PipelineOrchestrator.display_wake_word("___") == ""


# ============================================================================
# Constructor Tests
# ============================================================================


class TestConstructor:
    def test_default_logger(self, mock_orchestrator_deps):
        orch = PipelineOrchestrator(**mock_orchestrator_deps)
        # Should use module-level LOGGER when no logger arg
        assert orch.logger is not None

    def test_custom_logger(self, mock_orchestrator_deps, mock_logger):
        orch = PipelineOrchestrator(**mock_orchestrator_deps, logger=mock_logger)
        assert orch.logger is mock_logger

    def test_topics_use_config_base(self, mock_orchestrator_deps, mock_logger):
        mock_orchestrator_deps["config"].mqtt.topic_base = "my-device"
        orch = PipelineOrchestrator(**mock_orchestrator_deps, logger=mock_logger)
        assert orch._assist_in_progress_topic == "my-device/assistant/in_progress"
        assert orch._assist_metrics_topic == "my-device/assistant/metrics"
        assert orch._assist_stage_topic == "my-device/assistant/stage"
        assert orch._assist_pipeline_topic == "my-device/assistant/active_pipeline"
        assert orch._assist_wake_topic == "my-device/assistant/last_wake_word"

    def test_initial_state(self, mock_orchestrator_deps, mock_logger):
        orch = PipelineOrchestrator(**mock_orchestrator_deps, logger=mock_logger)
        assert orch._current_tracker is None
        assert orch._assist_stage == "idle"
        assert orch._assist_pipeline is None
        assert orch._get_llm is None
        assert orch._get_preferences is None
