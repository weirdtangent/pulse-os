"""Pipeline orchestrator for Pulse and Home Assistant voice pipelines."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from pulse.assistant.conversation_manager import should_listen_for_follow_up
from pulse.assistant.response_modes import select_ha_response
from pulse.assistant.wyoming import play_tts_stream, transcribe_audio
from pulse.audio import play_sound, play_volume_feedback

if TYPE_CHECKING:
    from pulse.assistant.actions import ActionEngine
    from pulse.assistant.audio import AplaySink
    from pulse.assistant.config import AssistantConfig, AssistantPreferences, WyomingEndpoint
    from pulse.assistant.conversation_manager import ConversationManager
    from pulse.assistant.home_assistant import HomeAssistantClient
    from pulse.assistant.info_query_handler import InfoQueryHandler
    from pulse.assistant.llm import LLMProvider, LLMResult
    from pulse.assistant.media_controller import MediaController
    from pulse.assistant.mqtt import AssistantMqtt
    from pulse.assistant.mqtt_publisher import AssistantMqttPublisher
    from pulse.assistant.music_handler import MusicCommandHandler
    from pulse.assistant.preference_manager import PreferenceManager
    from pulse.assistant.routines import RoutineEngine
    from pulse.assistant.schedule_service import ScheduleService
    from pulse.assistant.schedule_shortcuts import ScheduleShortcutHandler
    from pulse.assistant.scheduler import AssistantScheduler
    from pulse.assistant.wake_detector import WakeDetector
    from pulse.sound_library import SoundLibrary

LOGGER = logging.getLogger(__name__)


@dataclass
class AssistRunTracker:
    pipeline: str
    wake_word: str
    start: float = field(default_factory=time.monotonic)
    stage_start: float = field(default_factory=time.monotonic)
    current_stage: str | None = None
    stage_durations: dict[str, int] = field(default_factory=dict)

    def begin_stage(self, stage: str) -> None:
        now = time.monotonic()
        if self.current_stage:
            self.stage_durations[self.current_stage] = int((now - self.stage_start) * 1000)
        self.current_stage = stage
        self.stage_start = now

    def finalize(self, status: str) -> dict[str, object]:
        now = time.monotonic()
        if self.current_stage:
            self.stage_durations[self.current_stage] = int((now - self.stage_start) * 1000)
        return {
            "pipeline": self.pipeline,
            "wake_word": self.wake_word,
            "status": status,
            "total_ms": int((now - self.start) * 1000),
            "stages": self.stage_durations,
        }


class PipelineOrchestrator:
    """Orchestrates Pulse and Home Assistant voice pipelines."""

    def __init__(
        self,
        *,
        config: AssistantConfig,
        mqtt: AssistantMqtt,
        publisher: AssistantMqttPublisher,
        preference_manager: PreferenceManager,
        conversation_manager: ConversationManager,
        wake_detector: WakeDetector,
        media_controller: MediaController,
        music_handler: MusicCommandHandler,
        schedule_shortcuts: ScheduleShortcutHandler,
        info_query_handler: InfoQueryHandler,
        schedule_service: ScheduleService,
        actions: ActionEngine,
        routines: RoutineEngine,
        home_assistant: HomeAssistantClient | None,
        scheduler: AssistantScheduler,
        player: AplaySink,
        sound_library: SoundLibrary,
        logger: logging.Logger | None = None,
    ) -> None:
        self.config = config
        self.mqtt = mqtt
        self.publisher = publisher
        self.preference_manager = preference_manager
        self.conversation_manager = conversation_manager
        self.wake_detector = wake_detector
        self.media_controller = media_controller
        self.music_handler = music_handler
        self.schedule_shortcuts = schedule_shortcuts
        self.info_query_handler = info_query_handler
        self.schedule_service = schedule_service
        self.actions = actions
        self.routines = routines
        self.home_assistant = home_assistant
        self.scheduler = scheduler
        self.player = player
        self._sound_library = sound_library
        self.logger = logger or LOGGER

        self._current_tracker: AssistRunTracker | None = None
        self._assist_stage = "idle"
        self._assist_pipeline: str | None = None

        base_topic = config.mqtt.topic_base
        self._assist_in_progress_topic = f"{base_topic}/assistant/in_progress"
        self._assist_metrics_topic = f"{base_topic}/assistant/metrics"
        self._assist_stage_topic = f"{base_topic}/assistant/stage"
        self._assist_pipeline_topic = f"{base_topic}/assistant/active_pipeline"
        self._assist_wake_topic = f"{base_topic}/assistant/last_wake_word"

        # LLM provider — mutable, rebuilt on preference changes
        self._get_llm: Callable[[], LLMProvider] | None = None
        self._get_preferences: Callable[[], AssistantPreferences] | None = None

    def set_llm_provider_getter(self, getter: Callable[[], LLMProvider]) -> None:
        """Set callback that returns the current LLM provider."""
        self._get_llm = getter

    def set_preferences_getter(self, getter: Callable[[], AssistantPreferences]) -> None:
        """Set callback that returns current preferences."""
        self._get_preferences = getter

    @property
    def preferences(self) -> AssistantPreferences:
        if self._get_preferences:
            return self._get_preferences()
        return self.config.preferences  # type: ignore[return-value]

    @property
    def llm(self) -> LLMProvider:
        if self._get_llm:
            return self._get_llm()
        raise RuntimeError("LLM provider getter not set")

    @property
    def current_tracker(self) -> AssistRunTracker | None:
        return self._current_tracker

    async def run_pulse_pipeline(self, wake_word: str) -> None:
        self.media_controller.cancel_media_resume_task()
        tracker = AssistRunTracker("pulse", wake_word)
        tracker.begin_stage("listening")
        self._current_tracker = tracker
        self._set_assist_stage("pulse", "listening", {"wake_word": wake_word})
        await self._maybe_play_wake_sound()
        await self.media_controller.maybe_pause_media_playback()
        await self.schedule_service.pause_active_audio()
        try:
            audio_bytes = await self.conversation_manager.record_phrase()
            if not audio_bytes:
                self.logger.info("[pipeline] No speech captured for wake word %s", wake_word)
                self._finalize_assist_run(status="no_audio")
                return
            tracker.begin_stage("thinking")
            self._set_assist_stage("pulse", "thinking", {"wake_word": wake_word})
            transcript = await self._transcribe(audio_bytes)
            if not transcript:
                self._finalize_assist_run(status="no_transcript")
                return
            if self.config.log_transcripts:
                self.logger.info("[pipeline] Transcript [%s]: %s", wake_word, transcript)
            if self.preference_manager.log_llm_messages:
                transcript_payload = {"text": transcript, "wake_word": wake_word}
                self.publisher._publish_message(self.config.transcript_topic, json.dumps(transcript_payload))
            if await self._maybe_handle_stop_phrase(transcript, wake_word, tracker):
                self._finalize_assist_run(status="cancelled")
                return
            if await self.music_handler.maybe_handle(transcript):
                self._finalize_assist_run(status="success")
                return
            if await self.schedule_shortcuts.maybe_handle_schedule_shortcut(transcript):
                self._finalize_assist_run(status="success")
                return
            if await self.info_query_handler.maybe_handle(transcript, wake_word):
                self._finalize_assist_run(status="success")
                return
            llm_result = await self._execute_llm_turn(transcript, wake_word, tracker)
            follow_up_needed = should_listen_for_follow_up(llm_result)
            follow_up_attempts = 0
            max_follow_up_attempts = 2
            last_follow_up_normalized: str | None = None
            while follow_up_needed:
                tracker.begin_stage("listening")
                self._set_assist_stage("pulse", "listening", {"wake_word": wake_word, "follow_up": True})
                await self.conversation_manager.wait_for_speech_tail()
                await self._maybe_play_wake_sound()
                follow_up_audio = await self.conversation_manager.record_follow_up_phrase()
                if not follow_up_audio:
                    follow_up_attempts += 1
                    if follow_up_attempts >= max_follow_up_attempts:
                        tracker.begin_stage("speaking")
                        self._set_assist_stage("pulse", "speaking", {"wake_word": wake_word, "follow_up": True})
                        await self.speak("I didn't hear anything, so let's try again later.")
                        break
                    continue
                tracker.begin_stage("thinking")
                self._set_assist_stage("pulse", "thinking", {"wake_word": wake_word, "follow_up": True})
                follow_up_transcript = await self._transcribe(follow_up_audio)
                if not follow_up_transcript:
                    follow_up_attempts += 1
                    if follow_up_attempts >= max_follow_up_attempts:
                        tracker.begin_stage("speaking")
                        self._set_assist_stage("pulse", "speaking", {"wake_word": wake_word, "follow_up": True})
                        await self.speak("Sorry, I didn't catch that.")
                        break
                    continue
                follow_up_attempts = 0
                if self.preference_manager.log_llm_messages:
                    payload = {"text": follow_up_transcript, "wake_word": wake_word, "follow_up": True}
                    self.publisher._publish_message(self.config.transcript_topic, json.dumps(payload))
                is_useful_follow_up, normalized_follow_up = self.conversation_manager.evaluate_follow_up(
                    follow_up_transcript,
                    last_follow_up_normalized,
                )
                if not is_useful_follow_up:
                    follow_up_transcript = ""
                else:
                    last_follow_up_normalized = normalized_follow_up
                if not follow_up_transcript:
                    follow_up_attempts += 1
                    if follow_up_attempts >= max_follow_up_attempts:
                        tracker.begin_stage("speaking")
                        self._set_assist_stage("pulse", "speaking", {"wake_word": wake_word, "follow_up": True})
                        await self.speak("Sorry, I didn't catch that.")
                        break
                    continue
                if await self._maybe_handle_stop_phrase(
                    follow_up_transcript,
                    wake_word,
                    tracker,
                    follow_up=True,
                ):
                    self._finalize_assist_run(status="cancelled")
                    return
                if await self.music_handler.maybe_handle(follow_up_transcript):
                    follow_up_needed = False
                    continue
                if await self.schedule_shortcuts.maybe_handle_schedule_shortcut(follow_up_transcript):
                    follow_up_needed = False
                    continue
                if await self.info_query_handler.maybe_handle(
                    follow_up_transcript,
                    wake_word,
                    follow_up=True,
                ):
                    follow_up_needed = False
                    continue
                llm_result = await self._execute_llm_turn(follow_up_transcript, wake_word, tracker, follow_up=True)
                follow_up_needed = should_listen_for_follow_up(llm_result)
            self._finalize_assist_run(status="success")
        finally:
            await self.schedule_service.resume_active_audio()
            self.media_controller.ensure_media_resume()

    async def run_home_assistant_pipeline(self, wake_word: str) -> None:
        from pulse.assistant.home_assistant import HomeAssistantError

        self.media_controller.cancel_media_resume_task()
        tracker = AssistRunTracker("home_assistant", wake_word)
        tracker.begin_stage("listening")
        self._current_tracker = tracker
        self._set_assist_stage("home_assistant", "listening", {"wake_word": wake_word})
        await self._maybe_play_wake_sound()
        await self.media_controller.maybe_pause_media_playback()
        ha_config = self.config.home_assistant
        ha_client = self.home_assistant
        if not ha_config.base_url or not ha_config.token:
            self.logger.warning(
                "[pipeline] Home Assistant pipeline invoked for wake word '%s' but base URL/token are missing",
                wake_word,
            )
            self._finalize_assist_run(status="config_error")
            return
        if not ha_client:
            self.logger.warning(
                "[pipeline] Home Assistant client not initialized; cannot handle wake word '%s'", wake_word
            )
            self._finalize_assist_run(status="config_error")
            return
        await self.schedule_service.pause_active_audio()
        try:
            audio_bytes = await self.conversation_manager.record_phrase()
            if not audio_bytes:
                self.logger.info("[pipeline] No speech captured for Home Assistant wake word %s", wake_word)
                self._finalize_assist_run(status="no_audio")
                return
            tracker.begin_stage("thinking")
            self._set_assist_stage("home_assistant", "thinking", {"wake_word": wake_word})
            try:
                ha_result = await ha_client.assist_audio(
                    audio_bytes,
                    sample_rate=self.config.mic.rate,
                    sample_width=self.config.mic.width,
                    channels=self.config.mic.channels,
                    pipeline_id=ha_config.assist_pipeline,
                    language=self.config.language,
                )
            except HomeAssistantError as exc:
                self.logger.warning("[pipeline] Home Assistant Assist call failed: %s", exc)
                self._set_assist_stage(
                    "home_assistant",
                    "error",
                    {"wake_word": wake_word, "pipeline": "home_assistant", "reason": str(exc)},
                )
                self._finalize_assist_run(status="error")
                return
            transcript = self._extract_ha_transcript(ha_result)
            if transcript:
                if self.config.log_transcripts:
                    self.logger.info("[pipeline] Transcript [%s/HA]: %s", wake_word, transcript)
                if self.preference_manager.log_llm_messages:
                    self.publisher._publish_message(
                        self.config.transcript_topic,
                        json.dumps({"text": transcript, "wake_word": wake_word, "pipeline": "home_assistant"}),
                    )
            speech_text = self._extract_ha_speech(ha_result) or "Okay."
            if self.config.log_transcripts:
                self.logger.info("[pipeline] Response [%s/HA]: %s", wake_word, speech_text)
            tracker.begin_stage("speaking")
            self._set_assist_stage("home_assistant", "speaking", {"wake_word": wake_word})
            self.publisher._publish_message(
                self.config.response_topic,
                json.dumps(
                    {
                        "text": speech_text,
                        "wake_word": wake_word,
                        "pipeline": "home_assistant",
                        "conversation_id": ha_result.get("conversation_id"),
                    }
                ),
            )
            self._log_assistant_response(wake_word, speech_text, pipeline="home_assistant")
            tts_audio = self._extract_ha_tts_audio(ha_result)
            if tts_audio:
                await self._play_pcm_audio(
                    tts_audio["audio"],
                    tts_audio["rate"],
                    tts_audio["width"],
                    tts_audio["channels"],
                )
                self.media_controller.trigger_media_resume_after_response()
            else:
                tts_endpoint = ha_config.tts_endpoint or self.config.tts_endpoint
                await self._speak_via_endpoint(speech_text, tts_endpoint, self.config.tts_voice)
                self.media_controller.trigger_media_resume_after_response()
            self._finalize_assist_run(status="success")
        finally:
            await self.schedule_service.resume_active_audio()
            self.media_controller.ensure_media_resume()

    async def _execute_llm_turn(
        self,
        transcript: str,
        wake_word: str,
        tracker: AssistRunTracker,
        *,
        follow_up: bool = False,
    ) -> LLMResult | None:
        prompt_actions = self.actions.describe_for_prompt() + self._home_assistant_prompt_actions()
        llm_result = await self.llm.generate(transcript, prompt_actions)
        self.logger.debug(
            "[pipeline] LLM response [%s]: actions=%s, response=%s",
            wake_word,
            llm_result.actions,
            llm_result.response,
        )
        routine_actions = await self.routines.execute(llm_result.actions, self.home_assistant)
        executed_actions = list(routine_actions)
        executed_actions.extend(
            await self.actions.execute(
                llm_result.actions,
                self.mqtt if llm_result.actions else None,
                self.home_assistant,
                self.scheduler,
                self.schedule_service,
                media_controller=self.media_controller,
            )
        )
        if executed_actions:
            self.logger.debug("[pipeline] Executed actions [%s]: %s", wake_word, executed_actions)
        if executed_actions:
            self.publisher._publish_message(
                self.config.action_topic,
                json.dumps({"executed": executed_actions, "wake_word": wake_word}),
            )
            if routine_actions:
                self.publisher._publish_routine_overlay()
        response_text, play_tone = select_ha_response(
            self.preferences.ha_response_mode, executed_actions, llm_result.response
        )
        if response_text:
            if self.config.log_transcripts:
                self.logger.info("[pipeline] Response [%s]: %s", wake_word, response_text)
            tracker.begin_stage("speaking")
            stage_extra: dict[str, str | bool] = {"wake_word": wake_word}
            if follow_up:
                stage_extra["follow_up"] = True
            self._set_assist_stage("pulse", "speaking", stage_extra)
            response_payload: dict[str, str | bool] = {
                "text": response_text,
                "wake_word": wake_word,
            }
            if follow_up:
                response_payload["follow_up"] = True
            self.publisher._publish_message(self.config.response_topic, json.dumps(response_payload))
            tag = "follow_up" if follow_up else wake_word
            self._log_assistant_response(tag, response_text, pipeline="pulse")
            await self.speak(response_text)
            speech_finished_at = time.monotonic()
            self.conversation_manager.update_last_response_end(speech_finished_at)
            self.media_controller.trigger_media_resume_after_response()
        elif play_tone:
            tracker.begin_stage("speaking")
            tone_stage_extra: dict[str, str | bool] = {"wake_word": wake_word}
            if follow_up:
                tone_stage_extra["follow_up"] = True
            self._set_assist_stage("pulse", "speaking", tone_stage_extra)
            await self._play_ack_tone(self.preferences.ha_tone_sound)
            speech_finished_at = time.monotonic()
            self.conversation_manager.update_last_response_end(speech_finished_at)
            self.media_controller.trigger_media_resume_after_response()
        return llm_result

    def _home_assistant_prompt_actions(self) -> list[dict[str, str]]:
        if not self.home_assistant:
            return []
        prompt_entries = [
            {
                "slug": "ha.turn_on:name=bedroom ceiling fan,percentage=75",
                "description": (
                    "Turn on any HA entity by name or entity_id. Lights can be turned on without brightness or "
                    "color details; fans can use percentage=0-100 for speed."
                ),
            },
            {
                "slug": "ha.turn_off:name=bedroom ceiling fan",
                "description": "Turn off any HA entity by name or entity_id (lights, fans, switches, etc.).",
            },
            {
                "slug": "ha.light_on:name=nightstand light",
                "description": (
                    "Turn on a light by name or room. If the user does not provide brightness or color, just turn "
                    "it on with the current defaults instead of asking follow-ups."
                ),
            },
            {
                "slug": "ha.light_on:name=nightstand light,brightness=50",
                "description": (
                    "Turn on a light by name/room with a specific brightness percentage (0-100) or a color when "
                    "provided."
                ),
            },
            {
                "slug": "ha.light_off:name=nightstand light",
                "description": "Turn off a specific light by name or all lights in an area/room.",
            },
            {
                "slug": "ha.light_off:area=bedroom,all=true",
                "description": "Turn off all lights in a room/area by name.",
            },
            {
                "slug": "ha.scene:entity_id=scene.movie_time",
                "description": "Activate a Home Assistant scene like movie time.",
            },
            {
                "slug": "volume.set:percentage=75",
                "description": "Set device volume to a percentage (0-100).",
            },
            {
                "slug": "ha.turn_on:name=bedroom ceiling fan,percentage=40",
                "description": "Set a fan speed by name (0-100%).",
            },
            {
                "slug": "ha.turn_on:name=bedroom ceiling fan,percentage=0",
                "description": "Turn a fan off by setting speed to 0%.",
            },
            {
                "slug": "media.pause",
                "description": "Pause all configured media players or whole-home audio.",
            },
            {
                "slug": "media.resume",
                "description": "Resume music or TV audio after an interruption.",
            },
            {
                "slug": "timer.start:duration=10m,label=cookies",
                "description": "Start a timer (duration supports seconds/minutes/hours or ISO like PT5M).",
            },
            {
                "slug": "reminder.create:when=2025-01-01T09:00,message=Example",
                "description": "Schedule a reminder at a specific time or use 'in 10m' format.",
            },
        ]
        prompt_entries.extend(self.routines.prompt_entries())
        return prompt_entries

    async def _maybe_handle_stop_phrase(
        self,
        transcript: str,
        wake_word: str,
        tracker: AssistRunTracker | None,
        *,
        follow_up: bool = False,
    ) -> bool:
        if not self.conversation_manager.is_conversation_stop(transcript):
            return False
        if tracker:
            tracker.begin_stage("speaking")
        stage_extra: dict[str, str | bool] = {"wake_word": wake_word}
        if follow_up:
            stage_extra["follow_up"] = True
        self._set_assist_stage("pulse", "speaking", stage_extra)
        response_text = "Okay, no problem."
        payload: dict[str, str | bool] = {"text": response_text, "wake_word": wake_word}
        if follow_up:
            payload["follow_up"] = True
        self.publisher._publish_message(self.config.response_topic, json.dumps(payload))
        self._log_assistant_response("stop", response_text, pipeline="pulse")
        await self.speak(response_text)
        self.media_controller.trigger_media_resume_after_response()
        return True

    def _set_assist_stage(self, pipeline: str, stage: str, extra: dict | None = None) -> None:
        self._assist_stage = stage
        self._assist_pipeline = pipeline
        in_progress = stage not in {"idle", "error"}
        self.publisher._publish_message(self._assist_in_progress_topic, "ON" if in_progress else "OFF", retain=True)
        payload_extra = {"pipeline": pipeline, "stage": stage}
        if extra:
            payload_extra.update(extra)
        self.publisher._publish_state(stage, payload_extra)
        self.publisher._publish_message(self._assist_stage_topic, stage, retain=True)
        self.publisher._publish_message(self._assist_pipeline_topic, pipeline, retain=True)
        if extra and "wake_word" in extra:
            self.publisher._publish_message(self._assist_wake_topic, str(extra["wake_word"]), retain=True)

    def _finalize_assist_run(self, status: str) -> None:
        tracker = self._current_tracker
        if tracker is None:
            return
        metrics = tracker.finalize(status)
        self.publisher._publish_message(self._assist_metrics_topic, json.dumps(metrics))
        self._set_assist_stage(tracker.pipeline, "idle", {"wake_word": tracker.wake_word, "status": status})
        self._current_tracker = None

    def _log_assistant_response(self, wake_word: str, text: str | None, pipeline: str = "pulse") -> None:
        if not self.preference_manager.log_llm_messages or not text:
            return

    async def _transcribe(self, audio_bytes: bytes, endpoint: WyomingEndpoint | None = None) -> str | None:
        target = endpoint or self.config.stt_endpoint
        if not target:
            self.logger.warning("[pipeline] No STT endpoint configured")
            return None
        return await transcribe_audio(
            audio_bytes,
            endpoint=target,
            mic=self.config.mic,
            language=self.config.language,
            logger=self.logger,
        )

    async def speak(self, text: str) -> None:
        await self._speak_via_endpoint(text, self.config.tts_endpoint, self.config.tts_voice)

    async def _speak_via_endpoint(
        self,
        text: str,
        endpoint: WyomingEndpoint | None,
        voice_name: str | None,
    ) -> None:
        target = endpoint or self.config.tts_endpoint
        if not target:
            self.logger.warning("[pipeline] No TTS endpoint configured; cannot speak response")
            return
        await play_tts_stream(
            text,
            endpoint=target,
            sink=self.player,
            voice_name=voice_name,
            audio_guard=self.wake_detector.local_audio_block(),
            logger=self.logger,
        )

    async def _maybe_play_wake_sound(self) -> None:
        if not self.preferences.wake_sound:
            return
        async with self.wake_detector.local_audio_block():
            try:
                await asyncio.to_thread(play_volume_feedback)
            except Exception:
                self.logger.info("[pipeline] Wake sound playback failed", exc_info=True)

    async def _play_ack_tone(self, sound_id: str | None) -> None:
        sound_path = None
        if sound_id:
            info = self._sound_library.resolve_sound(sound_id)
            if info:
                sound_path = info.path
        if sound_path is None:
            sound_path = self._sound_library.resolve_with_default(
                None,
                kind="notification",
                settings=self.config.sounds,
            )
        if sound_path is None:
            self.logger.debug("No acknowledgement tone available (sound_id=%s)", sound_id)
            return
        async with self.wake_detector.local_audio_block():
            await asyncio.to_thread(play_sound, sound_path)

    async def _play_pcm_audio(self, audio_bytes: bytes, rate: int, width: int, channels: int) -> None:
        async with self.wake_detector.local_audio_block():
            await self.player.start(rate, width, channels)
            try:
                await self.player.write(audio_bytes)
            finally:
                await self.player.stop()

    @staticmethod
    def _extract_ha_speech(result: dict) -> str | None:
        response = result.get("response") if isinstance(result, dict) else None
        if not isinstance(response, dict):
            return None
        speech_block = response.get("speech")
        if isinstance(speech_block, dict):
            plain = speech_block.get("plain")
            if isinstance(plain, dict):
                speech_text = plain.get("speech")
                if isinstance(speech_text, str):
                    return speech_text.strip()
        return None

    @staticmethod
    def _extract_ha_transcript(result: dict) -> str | None:
        stt_output = result.get("stt_output")
        if isinstance(stt_output, dict):
            text = stt_output.get("text")
            if isinstance(text, str):
                return text.strip()
        intent_input = result.get("intent_input")
        if isinstance(intent_input, dict):
            text = intent_input.get("text")
            if isinstance(text, str):
                return text.strip()
        return None

    @staticmethod
    def _extract_ha_tts_audio(result: dict) -> dict | None:
        tts_output = result.get("tts_output")
        if not isinstance(tts_output, dict):
            return None
        audio_b64 = tts_output.get("audio")
        if not isinstance(audio_b64, str):
            return None
        try:
            audio_bytes = base64.b64decode(audio_b64)
        except (ValueError, TypeError):
            return None
        rate = int(tts_output.get("sample_rate") or 0)
        width = int(tts_output.get("sample_width") or 0)
        channels = int(tts_output.get("channels") or 0)
        if not rate or not width or not channels:
            return None
        return {"audio": audio_bytes, "rate": rate, "width": width, "channels": channels}

    @staticmethod
    def display_wake_word(name: str) -> str:
        return name.replace("_", " ").strip()
