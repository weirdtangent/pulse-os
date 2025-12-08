#!/usr/bin/env python3
"""Pulse voice assistant daemon."""

from __future__ import annotations

import argparse
import asyncio
import base64
import calendar
import contextlib
import copy
import json
import logging
import os
import re
import signal
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from pulse.assistant.actions import ActionEngine, load_action_definitions
from pulse.assistant.audio import AplaySink, ArecordStream
from pulse.assistant.calendar_sync import CalendarReminder, CalendarSyncService
from pulse.assistant.config import AssistantConfig, WyomingEndpoint
from pulse.assistant.conversation_manager import (
    ConversationManager,
    build_conversation_stop_prefixes,
    should_listen_for_follow_up,
)
from pulse.assistant.home_assistant import HomeAssistantClient, HomeAssistantError
from pulse.assistant.info_service import InfoService
from pulse.assistant.llm import LLMProvider, LLMResult, build_llm_provider
from pulse.assistant.media_controller import MediaController
from pulse.assistant.mqtt import AssistantMqtt
from pulse.assistant.schedule_service import PlaybackConfig, ScheduledEvent, ScheduleService, parse_day_tokens
from pulse.assistant.scheduler import AssistantScheduler
from pulse.assistant.wake_detector import WakeDetector, compute_rms
from pulse.assistant.wyoming import play_tts_stream, transcribe_audio
from pulse.audio import play_volume_feedback
from pulse.datetime_utils import parse_datetime, parse_duration_seconds

LOGGER = logging.getLogger("pulse-assistant")


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


@dataclass
class ReminderIntent:
    message: str
    fire_time: datetime
    repeat_rule: dict[str, Any] | None


CALENDAR_EVENT_INFO_LIMIT = 25


class PulseAssistant:
    _conversation_stop_prefixes: tuple[str, ...] = ()

    def __init__(self, config: AssistantConfig) -> None:
        self.config = config
        mic_bytes = config.mic.bytes_per_chunk
        self.mic = ArecordStream(config.mic.command, mic_bytes, LOGGER)
        self.player = AplaySink(logger=LOGGER)
        self.mqtt = AssistantMqtt(config.mqtt, logger=LOGGER)
        action_defs = load_action_definitions(config.action_file, config.inline_actions)
        self.actions = ActionEngine(action_defs)
        self._llm_provider_override: str | None = None
        self.llm: LLMProvider = self._build_llm_provider()
        self.home_assistant: HomeAssistantClient | None = None
        self.preferences = config.preferences
        if config.home_assistant.base_url and config.home_assistant.token:
            try:
                self.home_assistant = HomeAssistantClient(config.home_assistant)
            except ValueError as exc:
                LOGGER.warning("Home Assistant config invalid: %s", exc)
        self.info_service = InfoService(config.info, logger=LOGGER)
        self.scheduler = AssistantScheduler(
            self.home_assistant, config.home_assistant, self._handle_scheduler_notification
        )
        schedule_path = self._determine_schedule_file()
        self.schedule_service = ScheduleService(
            storage_path=schedule_path,
            hostname=self.config.hostname,
            ha_client=self.home_assistant,
            on_state_changed=self._handle_schedule_state_changed,
            on_active_event=self._handle_active_schedule_event,
        )
        self._calendar_events: list[dict[str, Any]] = []
        self._calendar_updated_at: float | None = None
        self._latest_schedule_snapshot: dict[str, Any] | None = None
        self.calendar_sync: CalendarSyncService | None = None
        if self.config.calendar.enabled:
            if self.config.calendar.feeds:
                self.calendar_sync = CalendarSyncService(
                    config=self.config.calendar,
                    trigger_callback=self._trigger_calendar_reminder,
                    snapshot_callback=self._handle_calendar_snapshot,
                    logger=logging.getLogger("pulse.calendar_sync"),
                )
                LOGGER.info("Calendar sync service initialized with %d feed(s)", len(self.config.calendar.feeds))
            else:
                LOGGER.warning(
                    "Calendar sync is enabled but no feeds are configured (PULSE_CALENDAR_ICS_URLS is empty)"
                )
        else:
            LOGGER.info("Calendar sync is disabled (PULSE_CALENDAR_ICS_URLS not set or calendar disabled)")
        self._shutdown = asyncio.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        base_topic = self.config.mqtt.topic_base
        self._assist_in_progress_topic = f"{base_topic}/assistant/in_progress"
        self._assist_metrics_topic = f"{base_topic}/assistant/metrics"
        self._assist_stage_topic = f"{base_topic}/assistant/stage"
        self._assist_pipeline_topic = f"{base_topic}/assistant/active_pipeline"
        self._assist_wake_topic = f"{base_topic}/assistant/last_wake_word"
        self._preferences_topic = f"{base_topic}/preferences"
        self._schedules_state_topic = f"{base_topic}/schedules/state"
        self._schedule_command_topic = f"{base_topic}/schedules/command"
        self._alarms_active_topic = f"{base_topic}/alarms/active"
        self._timers_active_topic = f"{base_topic}/timers/active"
        self._reminders_active_topic = f"{base_topic}/reminders/active"
        self._info_card_topic = f"{base_topic}/info_card"
        self._assist_stage = "idle"
        self._assist_pipeline: str | None = None
        self._current_tracker: AssistRunTracker | None = None
        self._ha_pipeline_override: str | None = None
        self._self_audio_trigger_level = max(2, self.config.self_audio_trigger_level)
        self._media_player_entity = self.config.media_player_entity

        # Initialize extracted modules
        self.wake_detector = WakeDetector(
            config=self.config,
            preferences=self.preferences,
            mic=self.mic,
            self_audio_trigger_level=self._self_audio_trigger_level,
        )
        self.media_controller = MediaController(
            home_assistant=self.home_assistant,
            media_player_entity=self._media_player_entity,
            loop=None,  # Will be set in run()
        )
        self.conversation_manager = ConversationManager(
            config=self.config,
            mic=self.mic,
            compute_rms=compute_rms,
            last_response_end=None,
        )
        self._playback_topic = f"pulse/{self.config.hostname}/telemetry/now_playing"
        self._info_topic = f"{self.config.mqtt.topic_base}/info_card"
        self._info_overlay_clear_task: asyncio.Task | None = None
        self._info_overlay_min_seconds = max(0.0, float(os.environ.get("PULSE_INFO_CARD_MIN_SECONDS", "1.5")))
        self._info_overlay_buffer_seconds = max(0.0, float(os.environ.get("PULSE_INFO_CARD_BUFFER_SECONDS", "0.5")))
        self._media_pause_pending = False
        self._media_resume_task: asyncio.Task | None = None
        self._media_resume_delay = 2.0
        self._log_llm_messages = config.log_llm_messages
        self._conversation_stop_prefixes = build_conversation_stop_prefixes(config)
        self._earmuffs_lock = threading.Lock()
        self._earmuffs_enabled = False
        self._earmuffs_manual_override: bool | None = None
        self._earmuffs_state_restored = False  # Track if we've restored state from MQTT
        base_topic = self.config.mqtt.topic_base
        self._earmuffs_state_topic = f"{base_topic}/earmuffs/state"
        self._earmuffs_set_topic = f"{base_topic}/earmuffs/set"

    async def run(self) -> None:
        self._loop = asyncio.get_running_loop()
        self.media_controller._loop = self._loop
        self.mqtt.connect()
        self._subscribe_preference_topics()
        self._subscribe_schedule_topics()
        self._subscribe_playback_topic()
        self._subscribe_earmuffs_topic()
        self._publish_preferences()
        # Wait a moment for retained MQTT messages to arrive before publishing state
        await asyncio.sleep(0.5)
        if not self._earmuffs_state_restored:
            # No retained message received, publish current state
            self._publish_earmuffs_state()
        LOGGER.info("About to publish assistant discovery...")
        self._publish_assistant_discovery()
        LOGGER.info("Assistant discovery published, starting schedule service...")
        await self.schedule_service.start()
        LOGGER.info(
            "Schedule service started, about to start calendar sync service (calendar_sync=%s)",
            self.calendar_sync is not None,
        )
        if self.calendar_sync:
            LOGGER.info("Starting calendar sync service...")
            try:
                await self.calendar_sync.start()
                LOGGER.info("Calendar sync start() completed")
            except Exception as exc:  # pylint: disable=broad-except
                LOGGER.exception("Failed to start calendar sync service: %s", exc)
            # Clear any stale calendar events on startup
            self._calendar_events = []
            self._calendar_updated_at = None
            LOGGER.info("Cleared stale calendar events cache on startup")
            # Publish empty schedule state to clear overlay cache
            self._publish_schedule_state({})
        else:
            LOGGER.warning("calendar_sync is None, cannot start calendar sync service")
        await self.mic.start()
        self._set_assist_stage("pulse", "idle")
        friendly_words = ", ".join(self._display_wake_word(word) for word in self.config.wake_models)
        LOGGER.info("Pulse assistant ready (wake words: %s)", friendly_words)
        while not self._shutdown.is_set():
            wake_word = await self.wake_detector.wait_for_wake_word(self._shutdown, self._get_earmuffs_enabled)
            if wake_word is None:
                continue
            pipeline = self._pipeline_for_wake_word(wake_word)
            try:
                if pipeline == "home_assistant":
                    await self._run_home_assistant_pipeline(wake_word)
                else:
                    await self._run_pulse_pipeline(wake_word)
            except Exception as exc:  # pylint: disable=broad-except
                LOGGER.exception("Pipeline %s failed for wake word %s: %s", pipeline, wake_word, exc)
                self._set_assist_stage(pipeline, "error", {"wake_word": wake_word, "error": str(exc)})
                self._finalize_assist_run(status="error")

    async def shutdown(self) -> None:
        self._shutdown.set()
        if self.calendar_sync:
            await self.calendar_sync.stop()
        await self.mic.stop()
        await self.schedule_service.stop()
        self.mqtt.disconnect()
        await self.player.stop()
        self.media_controller.cancel_media_resume_task()
        if self.home_assistant:
            await self.home_assistant.close()

    def _cancel_media_resume_task(self) -> None:
        """Backward compatibility wrapper."""
        self.media_controller.cancel_media_resume_task()

    async def _maybe_pause_media_playback(self) -> None:
        """Backward compatibility wrapper."""
        await self.media_controller.maybe_pause_media_playback()

    def _trigger_media_resume_after_response(self) -> None:
        """Backward compatibility wrapper."""
        self.media_controller.trigger_media_resume_after_response()

    def _ensure_media_resume(self) -> None:
        """Backward compatibility wrapper."""
        self.media_controller.ensure_media_resume()

    async def _record_phrase(
        self,
        *,
        min_seconds: float | None = None,
        max_seconds: float | None = None,
        silence_ms: int | None = None,
    ) -> bytes | None:
        """Record a phrase using the conversation manager."""
        return await self.conversation_manager.record_phrase(
            min_seconds=min_seconds,
            max_seconds=max_seconds,
            silence_ms=silence_ms,
        )

    async def _transcribe(self, audio_bytes: bytes, endpoint: WyomingEndpoint | None = None) -> str | None:
        target = endpoint or self.config.stt_endpoint
        if not target:
            LOGGER.warning("No STT endpoint configured")
            return None
        return await transcribe_audio(
            audio_bytes,
            endpoint=target,
            mic=self.config.mic,
            language=self.config.language,
            logger=LOGGER,
        )

    async def _speak(self, text: str) -> None:
        await self._speak_via_endpoint(text, self.config.tts_endpoint, self.config.tts_voice)

    async def _speak_via_endpoint(
        self,
        text: str,
        endpoint: WyomingEndpoint | None,
        voice_name: str | None,
    ) -> None:
        target = endpoint or self.config.tts_endpoint
        if not target:
            LOGGER.warning("No TTS endpoint configured; cannot speak response")
            return
        await play_tts_stream(
            text,
            endpoint=target,
            sink=self.player,
            voice_name=voice_name,
            audio_guard=self.wake_detector.local_audio_block(),
            logger=LOGGER,
        )

    def _publish_state(self, state: str, extra: dict | None = None) -> None:
        payload = {"state": state}
        if extra:
            payload.update(extra)
        payload["device"] = self.config.hostname
        self._publish_message(self.config.state_topic, json.dumps(payload))

    def _publish_message(self, topic: str, payload: str, *, retain: bool = False) -> None:
        self.mqtt.publish(topic, payload=payload, retain=retain)

    def _publish_info_overlay(
        self, text: str | None = None, category: str | None = None, extra: dict | None = None
    ) -> None:
        if not self._info_topic:
            return
        payload = dict(extra or {})
        if text and text.strip():
            payload.setdefault("state", "show")
            payload.setdefault("category", category or "")
            payload["text"] = text.strip()
            payload.setdefault("ts", time.time())
        elif payload:
            payload.setdefault("state", "show")
            payload.setdefault("ts", time.time())
            if category:
                payload.setdefault("category", category)
        else:
            payload = {"state": "clear"}
        if payload.get("state") != "clear":
            self._cancel_info_overlay_clear()
        self._publish_message(self._info_topic, json.dumps(payload))

    def _cancel_info_overlay_clear(self) -> None:
        task = self._info_overlay_clear_task
        if task:
            task.cancel()
            self._info_overlay_clear_task = None

    def _schedule_info_overlay_clear(self, delay: float) -> None:
        self._cancel_info_overlay_clear()
        if delay <= 0:
            self._publish_info_overlay()
            return

        async def _clear_after() -> None:
            try:
                await asyncio.sleep(delay)
                self._publish_info_overlay()
            except asyncio.CancelledError:
                return

        self._info_overlay_clear_task = asyncio.create_task(_clear_after())

    @staticmethod
    def _clone_schedule_snapshot(snapshot: dict[str, Any]) -> dict[str, Any] | None:
        try:
            return json.loads(json.dumps(snapshot))
        except TypeError:
            LOGGER.debug("Unable to serialize schedule snapshot: %s", snapshot)
            return None

    def _publish_schedule_state(self, snapshot: dict[str, Any]) -> None:
        payload = copy.deepcopy(snapshot)
        payload["calendar_events"] = [dict(event) for event in self._calendar_events]
        if self._calendar_updated_at:
            payload["calendar_updated_at"] = datetime.fromtimestamp(
                self._calendar_updated_at, tz=datetime.now().astimezone().tzinfo
            ).isoformat()
        else:
            payload.setdefault("calendar_updated_at", None)
        try:
            message = json.dumps(payload)
        except TypeError:
            LOGGER.debug("Unable to serialize schedule snapshot: %s", payload)
            return
        self._publish_message(self._schedules_state_topic, message, retain=True)

    def _pipeline_for_wake_word(self, wake_word: str) -> str:
        return self.config.wake_routes.get(wake_word, "pulse")

    @staticmethod
    def _display_wake_word(name: str) -> str:
        return name.replace("_", " ").strip()

    async def _maybe_play_wake_sound(self) -> None:
        """Play wake sound if enabled."""
        if not self.preferences.wake_sound:
            return
        async with self.wake_detector.local_audio_block():
            try:
                await asyncio.to_thread(play_volume_feedback)
            except Exception:  # pylint: disable=broad-except
                LOGGER.debug("Wake sound playback failed", exc_info=True)

    async def _run_pulse_pipeline(self, wake_word: str) -> None:
        self.media_controller.cancel_media_resume_task()
        tracker = AssistRunTracker("pulse", wake_word)
        tracker.begin_stage("listening")
        self._current_tracker = tracker
        self._set_assist_stage("pulse", "listening", {"wake_word": wake_word})
        await self._maybe_play_wake_sound()
        await self.media_controller.maybe_pause_media_playback()
        await self.schedule_service.pause_active_audio()
        try:
            audio_bytes = await self._record_phrase()
            if not audio_bytes:
                LOGGER.debug("No speech captured for wake word %s", wake_word)
                self._finalize_assist_run(status="no_audio")
                return
            tracker.begin_stage("thinking")
            self._set_assist_stage("pulse", "thinking", {"wake_word": wake_word})
            transcript = await self._transcribe(audio_bytes)
            if not transcript:
                self._finalize_assist_run(status="no_transcript")
                return
            if self._log_llm_messages:
                LOGGER.info("Transcript (%s): %s", wake_word, transcript)
            transcript_payload = {"text": transcript, "wake_word": wake_word}
            self._publish_message(self.config.transcript_topic, json.dumps(transcript_payload))
            if await self._maybe_handle_stop_phrase(transcript, wake_word, tracker):
                self._finalize_assist_run(status="cancelled")
                return
            if await self._maybe_handle_music_command(transcript):
                self._finalize_assist_run(status="success")
                return
            if await self._maybe_handle_schedule_shortcut(transcript):
                self._finalize_assist_run(status="success")
                return
            if await self._maybe_handle_information_query(transcript, wake_word):
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
                await self._wait_for_speech_tail()
                await self._maybe_play_wake_sound()
                follow_up_audio = await self._record_follow_up_phrase()
                if not follow_up_audio:
                    follow_up_attempts += 1
                    LOGGER.info("Follow-up attempt %d captured no audio", follow_up_attempts)
                    if follow_up_attempts >= max_follow_up_attempts:
                        tracker.begin_stage("speaking")
                        self._set_assist_stage("pulse", "speaking", {"wake_word": wake_word, "follow_up": True})
                        await self._speak("I didn't hear anything, so let's try again later.")
                        break
                    continue
                tracker.begin_stage("thinking")
                self._set_assist_stage("pulse", "thinking", {"wake_word": wake_word, "follow_up": True})
                follow_up_transcript = await self._transcribe(follow_up_audio)
                if not follow_up_transcript:
                    follow_up_attempts += 1
                    LOGGER.info("Follow-up attempt %d produced no transcript", follow_up_attempts)
                    if follow_up_attempts >= max_follow_up_attempts:
                        tracker.begin_stage("speaking")
                        self._set_assist_stage("pulse", "speaking", {"wake_word": wake_word, "follow_up": True})
                        await self._speak("Sorry, I didn't catch that.")
                        break
                    continue
                follow_up_attempts = 0
                if self._log_llm_messages:
                    LOGGER.info("Follow-up transcript (%s): %s", wake_word, follow_up_transcript)
                payload = {"text": follow_up_transcript, "wake_word": wake_word, "follow_up": True}
                self._publish_message(self.config.transcript_topic, json.dumps(payload))
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
                    LOGGER.info("Follow-up attempt %d produced no useful transcript", follow_up_attempts)
                    if follow_up_attempts >= max_follow_up_attempts:
                        tracker.begin_stage("speaking")
                        self._set_assist_stage("pulse", "speaking", {"wake_word": wake_word, "follow_up": True})
                        await self._speak("Sorry, I didn't catch that.")
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
                if await self._maybe_handle_music_command(follow_up_transcript):
                    follow_up_needed = False
                    continue
                if await self._maybe_handle_schedule_shortcut(follow_up_transcript):
                    follow_up_needed = False
                    continue
                if await self._maybe_handle_information_query(
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

    async def _run_home_assistant_pipeline(self, wake_word: str) -> None:
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
            LOGGER.warning(
                "Home Assistant pipeline invoked for wake word '%s' but base URL/token are missing",
                wake_word,
            )
            self._finalize_assist_run(status="config_error")
            return
        if not ha_client:
            LOGGER.warning("Home Assistant client not initialized; cannot handle wake word '%s'", wake_word)
            self._finalize_assist_run(status="config_error")
            return
        await self.schedule_service.pause_active_audio()
        try:
            audio_bytes = await self._record_phrase()
            if not audio_bytes:
                LOGGER.debug("No speech captured for Home Assistant wake word %s", wake_word)
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
                LOGGER.warning("Home Assistant Assist call failed: %s", exc)
                self._set_assist_stage(
                    "home_assistant",
                    "error",
                    {"wake_word": wake_word, "pipeline": "home_assistant", "reason": str(exc)},
                )
                self._finalize_assist_run(status="error")
                return
            transcript = self._extract_ha_transcript(ha_result)
            if transcript:
                if self._log_llm_messages:
                    LOGGER.info("HA transcript (%s): %s", wake_word, transcript)
                self._publish_message(
                    self.config.transcript_topic,
                    json.dumps({"text": transcript, "wake_word": wake_word, "pipeline": "home_assistant"}),
                )
            speech_text = self._extract_ha_speech(ha_result) or "Okay."
            tracker.begin_stage("speaking")
            self._set_assist_stage("home_assistant", "speaking", {"wake_word": wake_word})
            self._publish_message(
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
        LOGGER.debug("LLM response: %s", llm_result)
        executed_actions = await self.actions.execute(
            llm_result.actions,
            self.mqtt if llm_result.actions else None,
            self.home_assistant,
            self.scheduler,
            self.schedule_service,
        )
        if executed_actions:
            self._publish_message(
                self.config.action_topic,
                json.dumps({"executed": executed_actions, "wake_word": wake_word}),
            )
        if llm_result.response:
            tracker.begin_stage("speaking")
            stage_extra = {"wake_word": wake_word}
            if follow_up:
                stage_extra["follow_up"] = True
            self._set_assist_stage("pulse", "speaking", stage_extra)
            response_payload = {
                "text": llm_result.response,
                "wake_word": wake_word,
            }
            if follow_up:
                response_payload["follow_up"] = True
            self._publish_message(self.config.response_topic, json.dumps(response_payload))
            tag = "follow_up" if follow_up else wake_word
            self._log_assistant_response(tag, llm_result.response, pipeline="pulse")
            await self._speak(llm_result.response)
            speech_finished_at = time.monotonic()
            self.conversation_manager.update_last_response_end(speech_finished_at)
            self.media_controller.trigger_media_resume_after_response()
        return llm_result

    async def _record_follow_up_phrase(self) -> bytes | None:
        """Record a follow-up phrase using the conversation manager."""
        return await self.conversation_manager.record_follow_up_phrase()

    async def _wait_for_speech_tail(self) -> None:
        """Wait for speech tail using the conversation manager."""
        await self.conversation_manager.wait_for_speech_tail()

    def _home_assistant_prompt_actions(self) -> list[dict[str, str]]:
        if not self.home_assistant:
            return []
        return [
            {
                "slug": "ha.turn_on:entity_id",
                "description": "Turn on a Home Assistant entity (replace entity_id with light.kitchen etc.)",
            },
            {
                "slug": "ha.turn_off:entity_id",
                "description": "Turn off a Home Assistant entity (replace entity_id with switch.projector)",
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

    async def _handle_scheduler_notification(self, message: str) -> None:
        LOGGER.info("Scheduler notification: %s", message)
        payload = json.dumps({"text": message, "source": "scheduler", "device": self.config.hostname})
        self._publish_message(self.config.response_topic, payload)
        try:
            await self._speak(message)
        except Exception as exc:  # pylint: disable=broad-except
            LOGGER.warning("Failed to speak scheduler message: %s", exc)

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

    async def _play_pcm_audio(self, audio_bytes: bytes, rate: int, width: int, channels: int) -> None:
        async with self.wake_detector.local_audio_block():
            await self.player.start(rate, width, channels)
            try:
                await self.player.write(audio_bytes)
            finally:
                await self.player.stop()

    def _subscribe_preference_topics(self) -> None:
        base = self._preferences_topic
        try:
            self.mqtt.subscribe(f"{base}/wake_sound/set", self._handle_wake_sound_command)
            self.mqtt.subscribe(f"{base}/speaking_style/set", self._handle_speaking_style_command)
            self.mqtt.subscribe(f"{base}/wake_sensitivity/set", self._handle_wake_sensitivity_command)
            self.mqtt.subscribe(f"{base}/ha_pipeline/set", self._handle_ha_pipeline_command)
            self.mqtt.subscribe(f"{base}/llm_provider/set", self._handle_llm_provider_command)
            self.mqtt.subscribe(f"{base}/log_llm/set", self._handle_log_llm_command)
        except RuntimeError:
            LOGGER.debug("MQTT client not ready for preference subscriptions")

    def _subscribe_schedule_topics(self) -> None:
        try:
            self.mqtt.subscribe(self._schedule_command_topic, self._handle_schedule_command_message)
        except RuntimeError:
            LOGGER.debug("MQTT client not ready for schedule command subscription")

    def _subscribe_playback_topic(self) -> None:
        try:
            self.mqtt.subscribe(self._playback_topic, self._handle_now_playing_message)
        except RuntimeError:
            LOGGER.debug("MQTT client not ready for playback telemetry subscription")

    def _subscribe_earmuffs_topic(self) -> None:
        try:
            LOGGER.info("Subscribing to earmuffs set topic: %s", self._earmuffs_set_topic)
            self.mqtt.subscribe(self._earmuffs_set_topic, self._handle_earmuffs_command)
            LOGGER.info("Successfully subscribed to earmuffs set topic")
            # Also subscribe to state topic to restore retained state on startup
            LOGGER.info("Subscribing to earmuffs state topic: %s", self._earmuffs_state_topic)
            self.mqtt.subscribe(self._earmuffs_state_topic, self._handle_earmuffs_state_restore)
            LOGGER.info("Successfully subscribed to earmuffs state topic")
        except RuntimeError as exc:
            LOGGER.warning("MQTT client not ready for earmuffs subscription: %s", exc)
        except Exception as exc:
            LOGGER.error("Failed to subscribe to earmuffs topic: %s", exc, exc_info=True)

    def _handle_wake_sound_command(self, payload: str) -> None:
        value = payload.strip().lower()
        enabled = value in {"on", "true", "1", "yes"}
        self.preferences = replace(self.preferences, wake_sound=enabled)
        self._publish_preference_state("wake_sound", "on" if enabled else "off")

    def _handle_log_llm_command(self, payload: str) -> None:
        value = payload.strip().lower()
        enabled = value in {"on", "true", "1", "yes"}
        if self._log_llm_messages == enabled:
            return
        self._log_llm_messages = enabled
        self._publish_preference_state("log_llm", "on" if enabled else "off")

    def _handle_now_playing_message(self, payload: str) -> None:
        normalized = payload.strip()
        active = bool(normalized)
        previous_active = self.wake_detector.get_remote_audio_active()
        changed = self.wake_detector.set_remote_audio_active(active)
        if changed:
            detail = normalized[:80] or "idle"
            LOGGER.debug("Self audio playback %s via telemetry (%s)", "active" if active else "idle", detail)
            # Auto-enable earmuffs when music starts (unless manually disabled)
            if active and not previous_active:
                with self._earmuffs_lock:
                    if self._earmuffs_manual_override is not False:
                        self._set_earmuffs_enabled(True, manual=False)
            # Auto-disable earmuffs when music stops (only if not manually enabled)
            elif not active and previous_active:
                with self._earmuffs_lock:
                    if self._earmuffs_manual_override is not True:
                        self._set_earmuffs_enabled(False, manual=False)

    def _handle_speaking_style_command(self, payload: str) -> None:
        value = payload.strip().lower()
        if value not in {"relaxed", "normal", "aggressive"}:
            LOGGER.debug("Ignoring invalid speaking style: %s", payload)
            return
        self.preferences = replace(self.preferences, speaking_style=value)  # type: ignore[arg-type]
        self._publish_preference_state("speaking_style", value)

    def _handle_wake_sensitivity_command(self, payload: str) -> None:
        value = payload.strip().lower()
        if value not in {"low", "normal", "high"}:
            LOGGER.debug("Ignoring invalid wake sensitivity: %s", payload)
            return
        if value == self.preferences.wake_sensitivity:
            return
        self.preferences = replace(self.preferences, wake_sensitivity=value)  # type: ignore[arg-type]
        self._publish_preference_state("wake_sensitivity", value)
        self._mark_wake_context_dirty()

    def _handle_earmuffs_state_restore(self, payload: str) -> None:
        """Restore earmuffs state from retained MQTT message on startup."""
        if self._earmuffs_state_restored:
            # Already restored, ignore subsequent messages (they're just state updates)
            return
        value = payload.strip().lower()
        enabled = value in {"on", "true", "1", "yes", "enable", "enabled"}
        LOGGER.info("Restoring earmuffs state from MQTT: %s (enabled=%s)", value, enabled)
        # Restore state - if enabled, assume it was manually set (auto-enabled would have been cleared)
        with self._earmuffs_lock:
            if enabled != self._earmuffs_enabled:
                self._earmuffs_enabled = enabled
                # If enabled, assume manual override (auto-enabled would have been auto-disabled)
                if enabled:
                    self._earmuffs_manual_override = True
                    self.wake_detector.mark_wake_context_dirty()
                # If disabled, clear manual override to allow auto-enable
                else:
                    self._earmuffs_manual_override = None
        self._earmuffs_state_restored = True
        # Don't republish - the state is already in MQTT

    def _handle_earmuffs_command(self, payload: str) -> None:
        value = payload.strip().lower()
        LOGGER.info("Received earmuffs command: %s", value)
        if value == "toggle":
            current = self._get_earmuffs_enabled()
            enabled = not current
            LOGGER.info("Toggling earmuffs from %s to %s", current, enabled)
        else:
            enabled = value in {"on", "true", "1", "yes", "enable", "enabled"}
        self._set_earmuffs_enabled(enabled, manual=True)

    def _set_earmuffs_enabled(self, enabled: bool, *, manual: bool = False) -> None:
        changed = False
        with self._earmuffs_lock:
            if enabled != self._earmuffs_enabled:
                self._earmuffs_enabled = enabled
                if manual:
                    self._earmuffs_manual_override = enabled
                changed = True
        if changed:
            reason = "manual" if manual else "automatic"
            LOGGER.info("Earmuffs %s (%s)", "enabled" if enabled else "disabled", reason)
            self._publish_earmuffs_state()
            if enabled:
                self.wake_detector.mark_wake_context_dirty()

    def _get_earmuffs_enabled(self) -> bool:
        with self._earmuffs_lock:
            enabled = self._earmuffs_enabled
            LOGGER.debug("Earmuffs state check: enabled=%s", enabled)
            return enabled

    def _is_earmuffs_manual_override(self) -> bool:
        with self._earmuffs_lock:
            return self._earmuffs_manual_override

    def _publish_earmuffs_state(self) -> None:
        enabled = self._get_earmuffs_enabled()
        state = "on" if enabled else "off"
        LOGGER.info("Publishing earmuffs state to %s: %s (enabled=%s)", self._earmuffs_state_topic, state, enabled)
        self._publish_message(self._earmuffs_state_topic, state, retain=True)

    def _publish_preferences(self) -> None:
        self._publish_preference_state("wake_sound", "on" if self.preferences.wake_sound else "off")
        self._publish_preference_state("speaking_style", self.preferences.speaking_style)
        self._publish_preference_state("wake_sensitivity", self.preferences.wake_sensitivity)
        self._publish_preference_state("ha_pipeline", self._active_ha_pipeline() or "")
        self._publish_preference_state("llm_provider", self._active_llm_provider())
        self._publish_preference_state("log_llm", "on" if self._log_llm_messages else "off")

    def _publish_preference_state(self, key: str, value: str) -> None:
        topic = f"{self._preferences_topic}/{key}/state"
        self._publish_message(topic, value, retain=True)

    def _handle_schedule_state_changed(self, snapshot: dict[str, Any]) -> None:
        cloned = self._clone_schedule_snapshot(snapshot)
        if cloned is None:
            return
        self._latest_schedule_snapshot = cloned
        self._publish_schedule_state(cloned)

    def _handle_active_schedule_event(self, event_type: str, payload: dict[str, Any] | None) -> None:
        if event_type == "alarm":
            topic = self._alarms_active_topic
        elif event_type == "timer":
            topic = self._timers_active_topic
        else:
            topic = self._reminders_active_topic
        message = payload or {"state": "idle"}
        self._publish_message(topic, json.dumps(message))

    async def _trigger_calendar_reminder(self, reminder: CalendarReminder) -> None:
        label = reminder.summary or "Calendar event"
        local_start = reminder.start.astimezone()
        metadata = {
            "reminder": {"message": label},
            "calendar": {
                "allow_delay": False,
                "calendar_name": reminder.calendar_name,
                "source": reminder.source_url,
                "start": reminder.start.isoformat(),
                "start_local": local_start.isoformat(),
                "end": reminder.end.isoformat() if reminder.end else None,
                "all_day": reminder.all_day,
                "description": reminder.description,
                "location": reminder.location,
                "trigger": reminder.trigger_time.isoformat(),
                "url": reminder.url,
                "uid": reminder.uid,
            },
        }
        try:
            await self.schedule_service.trigger_ephemeral_reminder(
                label=label,
                message=label,
                metadata=metadata,
                auto_clear_seconds=900,
            )
        except Exception as exc:  # pylint: disable=broad-except
            LOGGER.exception("Calendar reminder dispatch failed for %s: %s", label, exc)

    async def _handle_calendar_snapshot(self, reminders: list[CalendarReminder]) -> None:
        unique_reminders = self._deduplicate_calendar_reminders(reminders)
        # Filter out events that have already ended (or started if no end time)
        now = datetime.now().astimezone()
        future_reminders = [reminder for reminder in unique_reminders if (reminder.end or reminder.start) > now]
        events = [self._serialize_calendar_event(reminder) for reminder in future_reminders[:CALENDAR_EVENT_INFO_LIMIT]]
        self._calendar_events = events
        self._calendar_updated_at = time.time()
        # Always publish schedule state to trigger overlay refresh, even if no schedule snapshot exists yet
        snapshot = self._latest_schedule_snapshot or {}
        self._publish_schedule_state(snapshot)

    def _deduplicate_calendar_reminders(self, reminders: Sequence[CalendarReminder]) -> list[CalendarReminder]:
        """Collapse duplicate events that arise from multiple VALARMs."""

        unique: list[CalendarReminder] = []
        seen: set[tuple[str, str, str]] = set()
        for reminder in reminders:
            key = (
                reminder.source_url or "",
                reminder.uid,
                reminder.start.isoformat(),
            )
            if key in seen:
                continue
            seen.add(key)
            unique.append(reminder)
        return unique

    def _serialize_calendar_event(self, reminder: CalendarReminder) -> dict[str, Any]:
        local_start = reminder.start.astimezone()
        start_utc = reminder.start.astimezone(UTC)
        payload: dict[str, Any] = {
            "uid": reminder.uid,
            "summary": reminder.summary,
            "description": reminder.description,
            "location": reminder.location,
            "calendar_name": reminder.calendar_name,
            "all_day": reminder.all_day,
            "start": start_utc.isoformat(),
            "start_local": local_start.isoformat(),
            "trigger": reminder.trigger_time.astimezone().isoformat(),
            "source": reminder.source_url,
            "url": reminder.url,
            "declined": reminder.declined,
        }
        if reminder.end:
            payload["end"] = reminder.end.astimezone().isoformat()
        return payload

    def _handle_schedule_command_message(self, payload: str) -> None:
        if not self._loop:
            return
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            LOGGER.debug("Ignoring malformed schedule command: %s", payload)
            return
        asyncio.run_coroutine_threadsafe(self._process_schedule_command(data), self._loop)

    def _set_assist_stage(self, pipeline: str, stage: str, extra: dict | None = None) -> None:
        self._assist_stage = stage
        self._assist_pipeline = pipeline
        in_progress = stage not in {"idle", "error"}
        self._publish_message(self._assist_in_progress_topic, "ON" if in_progress else "OFF", retain=True)
        payload_extra = {"pipeline": pipeline, "stage": stage}
        if extra:
            payload_extra.update(extra)
        self._publish_state(stage, payload_extra)
        self._publish_message(self._assist_stage_topic, stage, retain=True)
        self._publish_message(self._assist_pipeline_topic, pipeline, retain=True)
        if extra and "wake_word" in extra:
            self._publish_message(self._assist_wake_topic, str(extra["wake_word"]), retain=True)

    def _finalize_assist_run(self, status: str) -> None:
        tracker = self._current_tracker
        if tracker is None:
            return
        metrics = tracker.finalize(status)
        self._publish_message(self._assist_metrics_topic, json.dumps(metrics))
        self._set_assist_stage(tracker.pipeline, "idle", {"wake_word": tracker.wake_word, "status": status})
        self._current_tracker = None

    @staticmethod
    def _determine_schedule_file() -> Path:
        override = os.environ.get("PULSE_SCHEDULE_FILE")
        if override:
            return Path(override).expanduser()
        return Path.home() / ".local" / "share" / "pulse" / "schedules.json"

    async def _process_schedule_command(self, payload: dict[str, Any]) -> None:
        if not isinstance(payload, dict):
            return
        action = str(payload.get("action") or "").lower()
        if not action:
            return
        try:
            if action in {"create_alarm", "add_alarm"}:
                time_text = payload.get("time") or payload.get("time_of_day")
                if not time_text:
                    raise ValueError("alarm time is required")
                days = self._coerce_day_list(payload.get("days"))
                playback = self._playback_from_payload(payload.get("playback"))
                single_flag = payload.get("single_shot")
                single_shot = bool(single_flag) if single_flag is not None else None
                await self.schedule_service.create_alarm(
                    time_of_day=str(time_text),
                    label=payload.get("label"),
                    days=days,
                    playback=playback,
                    single_shot=single_shot,
                )
            elif action == "update_alarm":
                event_id = payload.get("event_id")
                if not event_id:
                    raise ValueError("event_id is required to update an alarm")
                days = self._coerce_day_list(payload.get("days")) if "days" in payload else None
                playback = self._playback_from_payload(payload.get("playback")) if "playback" in payload else None
                await self.schedule_service.update_alarm(
                    str(event_id),
                    time_of_day=payload.get("time") or payload.get("time_of_day"),
                    days=days,
                    label=payload.get("label"),
                    playback=playback,
                )
            elif action in {"delete_alarm", "delete_timer", "delete"}:
                event_id = payload.get("event_id")
                if event_id:
                    await self.schedule_service.delete_event(str(event_id))
            elif action == "pause_alarm":
                event_id = payload.get("event_id")
                if event_id:
                    await self.schedule_service.pause_alarm(str(event_id))
            elif action in {"resume_alarm", "play_alarm"}:
                event_id = payload.get("event_id")
                if event_id:
                    await self.schedule_service.resume_alarm(str(event_id))
            elif action in {"start_timer", "create_timer"}:
                seconds = self._coerce_duration_seconds(payload.get("duration") or payload.get("seconds"))
                playback = self._playback_from_payload(payload.get("playback"))
                await self.schedule_service.create_timer(
                    duration_seconds=seconds,
                    label=payload.get("label"),
                    playback=playback,
                )
            elif action in {"add_time", "extend_timer"}:
                event_id = payload.get("event_id")
                seconds = self._coerce_duration_seconds(payload.get("seconds") or payload.get("duration"))
                if event_id:
                    await self.schedule_service.extend_timer(str(event_id), int(seconds))
            elif action in {"stop", "cancel"}:
                event_id = payload.get("event_id")
                if event_id:
                    await self.schedule_service.stop_event(str(event_id), reason="mqtt_stop")
            elif action == "snooze":
                event_id = payload.get("event_id")
                minutes = int(payload.get("minutes", 5))
                if event_id:
                    await self.schedule_service.snooze_alarm(str(event_id), minutes=max(1, minutes))
            elif action == "cancel_all":
                event_type = (payload.get("event_type") or "timer").lower()
                if event_type == "timer":
                    await self.schedule_service.cancel_all_timers()
            elif action == "next_alarm":
                info = self.schedule_service.get_next_alarm()
                response = {"next_alarm": info}
                self._publish_message(f"{self._schedules_state_topic}/next_alarm", json.dumps(response))
            elif action in {"create_reminder", "add_reminder"}:
                message = payload.get("message") or payload.get("text")
                when_text = payload.get("when") or payload.get("time")
                if not message or not when_text:
                    raise ValueError("reminder message and time are required")
                fire_time = parse_datetime(str(when_text))
                if fire_time is None:
                    raise ValueError("reminder time is invalid")
                repeat_rule = payload.get("repeat") if isinstance(payload.get("repeat"), dict) else None
                await self.schedule_service.create_reminder(
                    fire_time=fire_time,
                    message=str(message),
                    repeat=repeat_rule,
                )
            elif action == "delete_reminder":
                event_id = payload.get("event_id")
                if event_id:
                    await self.schedule_service.delete_event(str(event_id))
            elif action in {"complete_reminder", "finish_reminder"}:
                event_id = payload.get("event_id")
                if event_id:
                    await self.schedule_service.stop_event(str(event_id), reason="complete")
            elif action == "delay_reminder":
                event_id = payload.get("event_id")
                seconds = self._coerce_duration_seconds(payload.get("seconds") or payload.get("duration") or "0")
                if event_id and seconds > 0:
                    await self.schedule_service.delay_reminder(str(event_id), int(seconds))
        except Exception as exc:  # pylint: disable=broad-except
            LOGGER.debug("Schedule command %s failed: %s", action, exc)

    @staticmethod
    def _playback_from_payload(payload: dict[str, Any] | None) -> PlaybackConfig:
        if not isinstance(payload, dict):
            if str(payload or "").lower() == "music":
                return PlaybackConfig(mode="music")
            return PlaybackConfig()
        mode = (payload.get("mode") or payload.get("type") or "beep").lower()
        if mode != "music":
            return PlaybackConfig()
        return PlaybackConfig(
            mode="music",
            music_entity=payload.get("entity") or payload.get("music_entity"),
            music_source=payload.get("source") or payload.get("media_content_id"),
            media_content_type=payload.get("media_content_type") or payload.get("content_type"),
            provider=payload.get("provider"),
            description=payload.get("description") or payload.get("name"),
        )

    @staticmethod
    def _coerce_duration_seconds(raw_value: Any) -> float:
        if raw_value is None:
            raise ValueError("duration is required")
        if isinstance(raw_value, (int, float)):
            seconds = float(raw_value)
        else:
            seconds = parse_duration_seconds(str(raw_value))
        if seconds <= 0:
            raise ValueError("duration must be positive")
        return seconds

    @staticmethod
    def _coerce_day_list(value: Any) -> list[int] | None:
        if value is None:
            return None
        if isinstance(value, list):
            tokens = ",".join(str(item) for item in value)
            return parse_day_tokens(tokens)
        return parse_day_tokens(str(value))

    async def _maybe_handle_schedule_shortcut(self, transcript: str) -> bool:
        if not transcript or not transcript.strip():
            return False
        if not self.schedule_service:
            return False
        lowered = transcript.strip().lower()
        normalized = re.sub(r"[^\w\s:]", " ", lowered)
        normalized = re.sub(r"\b([ap])\s+m\b", r"\1m", normalized)
        normalized = re.sub(r"^(?:hey|ok|okay)\s+(?:jarvis|pulse)\s+", "", normalized)
        normalized = re.sub(r"^(?:jarvis|pulse)\s+", "", normalized)
        normalized = re.sub(r"\s+", " ", normalized).strip()
        alarm_intent = self._extract_alarm_start_intent(normalized)
        if self._mentions_alarm_cancel(normalized):
            handled = await self._stop_active_schedule(normalized)
            if handled:
                return True
            if await self._cancel_alarm_shortcut(alarm_intent):
                spoken = "Alarm cancelled."
                self._log_assistant_response("shortcut", spoken, pipeline="pulse")
                await self._speak(spoken)
                return True
            return False
        timer_start = self._extract_timer_start_intent(normalized)
        if timer_start:
            duration, label = timer_start
            await self.schedule_service.create_timer(duration_seconds=duration, label=label)
            phrase = self._describe_duration(duration)
            spoken = f"Starting a timer for {phrase}."
            self._log_assistant_response("shortcut", spoken, pipeline="pulse")
            await self._speak(spoken)
            return True
        reminder_intent = self._extract_reminder_intent(normalized, transcript)
        if reminder_intent:
            event = await self.schedule_service.create_reminder(
                fire_time=reminder_intent.fire_time,
                message=reminder_intent.message,
                repeat=reminder_intent.repeat_rule,
            )
            spoken = self._format_reminder_confirmation(event)
            self._log_assistant_response("shortcut", spoken, pipeline="pulse")
            await self._speak(spoken)
            return True
        if alarm_intent:
            time_of_day, days, label = alarm_intent
            await self.schedule_service.create_alarm(time_of_day=time_of_day, days=days, label=label)
            spoken = self._format_alarm_confirmation(time_of_day, days, label)
            self._log_assistant_response("shortcut", spoken, pipeline="pulse")
            await self._speak(spoken)
            return True
        if "next alarm" in normalized or normalized.startswith("when is my alarm"):
            info = self.schedule_service.get_next_alarm()
            if info:
                message = self._format_alarm_summary(info)
            else:
                message = "You do not have any alarms scheduled."
            self._log_assistant_response("shortcut", message, pipeline="pulse")
            await self._speak(message)
            return True
        if any(
            phrase in normalized
            for phrase in (
                "show me my alarms",
                "show my alarms",
                "show alarms",
                "list my alarms",
                "list alarms",
                "what alarms do i have",
                "what are my alarms",
            )
        ):
            await self._show_alarm_list()
            return True
        if any(
            phrase in normalized
            for phrase in (
                "show me my reminders",
                "show my reminders",
                "show reminders",
                "list my reminders",
                "list reminders",
                "what reminders do i have",
                "what are my reminders",
            )
        ):
            await self._show_reminder_list()
            return True
        if any(
            phrase in normalized
            for phrase in (
                "show me my calendar",
                "show my calendar",
                "show calendar events",
                "show my calendar events",
                "show upcoming events",
                "show my upcoming events",
                "list my calendar",
                "list calendar events",
                "what are my calendar events",
                "what are my calendar",
                "what calendar events",
                "what are my upcoming events",
                "what upcoming events",
                "tell me about my calendar",
                "tell me my calendar events",
                "what is on my calendar",
                "what events are coming up",
                "what is coming up on my calendar",
            )
        ):
            await self._show_calendar_events()
            return True
        if "cancel all timers" in normalized:
            count = await self.schedule_service.cancel_all_timers()
            if count > 0:
                spoken = f"Cancelled {count} timer{'s' if count != 1 else ''}."
            else:
                spoken = "You do not have any timers running."
            self._log_assistant_response("shortcut", spoken, pipeline="pulse")
            await self._speak(spoken)
            return True
        if self._is_stop_phrase(normalized):
            handled = await self._stop_active_schedule(normalized)
            if handled:
                return True
        add_match = re.search(r"(add|plus)\s+(\d+)\s*(minute|min|minutes|mins)", normalized)
        if add_match:
            minutes = int(add_match.group(2))
            seconds = minutes * 60
            label = self._extract_timer_label(normalized)
            if await self._extend_timer_shortcut(seconds, label):
                label_text = f" to the {label} timer" if label else ""
                spoken = f"Added {minutes} minutes{label_text}."
                self._log_assistant_response("shortcut", spoken, pipeline="pulse")
                await self._speak(spoken)
                return True
        if "cancel my timer" in normalized or "cancel the timer" in normalized:
            label = self._extract_timer_label(normalized)
            if await self._cancel_timer_shortcut(label):
                spoken = "Timer cancelled."
                self._log_assistant_response("shortcut", spoken, pipeline="pulse")
                await self._speak(spoken)
                return True
        return False

    async def _show_alarm_list(self) -> None:
        if not self.schedule_service:
            spoken = "I can't access your alarms right now."
            await self._speak(spoken)
            self._log_assistant_response("shortcut", spoken, pipeline="pulse")
            return
        alarms = self.schedule_service.list_events("alarm")
        if not alarms:
            spoken = "You do not have any alarms scheduled."
            await self._speak(spoken)
            self._log_assistant_response("shortcut", spoken, pipeline="pulse")
            self._publish_info_overlay()
            return
        alarm_payload = []
        for alarm in alarms:
            alarm_id = alarm.get("id")
            if not alarm_id:
                continue
            alarm_payload.append(
                {
                    "id": alarm_id,
                    "label": alarm.get("label") or "Alarm",
                    "time": alarm.get("time") or alarm.get("time_of_day"),
                    "time_of_day": alarm.get("time_of_day"),
                    "repeat_days": alarm.get("repeat_days"),
                    "days": alarm.get("days"),
                    "status": alarm.get("status"),
                    "next_fire": alarm.get("next_fire"),
                }
            )
        self._publish_info_overlay(
            text="Use ⏸️ to pause, ▶️ to resume, or 🗑️ to delete an alarm.",
            category="alarms",
            extra={"type": "alarms", "title": "Alarms", "alarms": alarm_payload},
        )
        count = len(alarms)
        spoken = f"You have {count} alarm{'s' if count != 1 else ''}."
        await self._speak("Here are your alarms.")
        self._log_assistant_response("shortcut", spoken, pipeline="pulse")

    async def _show_reminder_list(self) -> None:
        if not self.schedule_service:
            spoken = "I can't access your reminders right now."
            await self._speak(spoken)
            self._log_assistant_response("shortcut", spoken, pipeline="pulse")
            return
        reminders = self.schedule_service.list_events("reminder")
        if not reminders:
            spoken = "You do not have any reminders scheduled."
            await self._speak(spoken)
            self._log_assistant_response("shortcut", spoken, pipeline="pulse")
            self._publish_info_overlay()
            return
        reminder_payload = []
        for reminder in reminders:
            reminder_id = reminder.get("id")
            if not reminder_id:
                continue
            reminder_payload.append(
                {
                    "id": reminder_id,
                    "label": reminder.get("label") or "Reminder",
                    "meta": self._format_reminder_meta(reminder),
                    "status": reminder.get("status"),
                }
            )
        self._publish_info_overlay(
            text="Tap Complete when you're done or choose a delay.",
            category="reminders",
            extra={"type": "reminders", "title": "Reminders", "reminders": reminder_payload},
        )
        count = len(reminders)
        spoken = f"You have {count} reminder{'s' if count != 1 else ''}."
        await self._speak("Here are your reminders.")
        self._log_assistant_response("shortcut", spoken, pipeline="pulse")

    async def _show_calendar_events(self) -> None:
        if not (self.calendar_sync and self.config.calendar.enabled):
            spoken = "Calendar syncing is not enabled on this device."
            await self._speak(spoken)
            self._log_assistant_response("shortcut", spoken, pipeline="pulse")
            return
        events = self._calendar_events[:CALENDAR_EVENT_INFO_LIMIT]
        lookahead = self.config.calendar.lookahead_hours
        if not events:
            spoken = f"You don't have any calendar events in the next {lookahead} hours."
            await self._speak(spoken)
            self._log_assistant_response("shortcut", spoken, pipeline="pulse")
            self._publish_info_overlay()
            return
        subtitle = f"Upcoming events in the next {lookahead} hours."
        self._publish_info_overlay(
            text=subtitle,
            category="calendar",
            extra={
                "type": "calendar",
                "title": "Calendar",
                "events": events,
                "lookahead_hours": lookahead,
            },
        )
        count = len(self._calendar_events)
        spoken = f"You have {count} calendar event{'s' if count != 1 else ''} coming up."
        await self._speak("Here are your upcoming events.")
        self._log_assistant_response("shortcut", spoken, pipeline="pulse")

    @staticmethod
    def _format_reminder_meta(reminder: dict[str, Any]) -> str:
        next_fire = reminder.get("next_fire")
        try:
            dt = datetime.fromisoformat(next_fire).astimezone()
            time_phrase = dt.strftime("%-I:%M %p")
            date_phrase = dt.strftime("%b %-d")
            base = f"{date_phrase} · {time_phrase}"
        except (TypeError, ValueError):
            base = "—"
        repeat = ((reminder.get("metadata") or {}).get("reminder") or {}).get("repeat")
        if repeat:
            repeat_type = repeat.get("type")
            if repeat_type == "weekly":
                days = repeat.get("days") or []
                if sorted(days) == list(range(7)):
                    base = f"{base} · Daily"
                else:
                    names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
                    labels = ", ".join(names[day % 7] for day in days)
                    base = f"{base} · {labels}"
            elif repeat_type == "monthly":
                day = repeat.get("day")
                if isinstance(day, int):
                    base = f"{base} · {PulseAssistant._ordinal(day)} monthly"
                else:
                    base = f"{base} · Monthly"
            elif repeat_type == "interval":
                months = repeat.get("interval_months")
                days = repeat.get("interval_days")
                if months:
                    base = f"{base} · Every {months} mo"
                elif days:
                    base = f"{base} · Every {days} d"
        return base

    async def _maybe_handle_information_query(
        self,
        transcript: str,
        wake_word: str,
        *,
        follow_up: bool = False,
    ) -> bool:
        if not self.info_service:
            return False
        response = await self.info_service.maybe_answer(transcript)
        if not response:
            return False
        tracker = self._current_tracker
        if tracker:
            tracker.begin_stage("speaking")
        stage_extra = {"wake_word": wake_word, "info_category": response.category}
        if follow_up:
            stage_extra["follow_up"] = True
        self._set_assist_stage("pulse", "speaking", stage_extra)
        payload = {
            "text": response.text,
            "wake_word": wake_word,
            "info_category": response.category,
        }
        if follow_up:
            payload["follow_up"] = True
        self._publish_message(self.config.response_topic, json.dumps(payload))
        tag = f"info:{response.category}"
        self._log_assistant_response(tag, response.text, pipeline="pulse")
        overlay_active = False
        overlay_text = response.display or response.text
        overlay_payload = response.card
        estimated_clear_delay = self._estimate_speech_duration(response.text) + self._info_overlay_buffer_seconds
        try:
            if overlay_text or overlay_payload:
                self._publish_info_overlay(text=overlay_text, category=response.category, extra=overlay_payload)
                overlay_active = True
            await self._speak(response.text)
        finally:
            if overlay_active:
                hold = max(self._info_overlay_min_seconds, estimated_clear_delay)
                self._schedule_info_overlay_clear(hold)
        self._trigger_media_resume_after_response()
        return True

    async def _maybe_handle_stop_phrase(
        self,
        transcript: str,
        wake_word: str,
        tracker: AssistRunTracker | None,
        *,
        follow_up: bool = False,
    ) -> bool:
        if not self._is_conversation_stop_command(transcript):
            return False
        if tracker:
            tracker.begin_stage("speaking")
        stage_extra = {"wake_word": wake_word}
        if follow_up:
            stage_extra["follow_up"] = True
        self._set_assist_stage("pulse", "speaking", stage_extra)
        response_text = "Okay, no problem."
        payload = {"text": response_text, "wake_word": wake_word}
        if follow_up:
            payload["follow_up"] = True
        self._publish_message(self.config.response_topic, json.dumps(payload))
        self._log_assistant_response("stop", response_text, pipeline="pulse")
        await self._speak(response_text)
        self._trigger_media_resume_after_response()
        return True

    def _is_conversation_stop_command(self, transcript: str | None) -> bool:
        """Check if transcript is a conversation stop command."""
        return self.conversation_manager.is_conversation_stop(transcript)

    @staticmethod
    def _is_stop_phrase(lowered: str) -> bool:
        stop_phrases = {
            "stop",
            "stop it",
            "stop alarm",
            "stop the alarm",
            "turn off the alarm",
            "cancel the alarm",
            "stop the timer",
        }
        if lowered in stop_phrases:
            return True
        alarm_stop_pattern = r"\b(cancel|stop|turn off)\b.*\balarm\b"
        timer_stop_pattern = r"\b(cancel|stop|turn off)\b.*\btimer\b"
        if re.search(alarm_stop_pattern, lowered):
            return True
        if re.search(timer_stop_pattern, lowered):
            return True
        return False

    @staticmethod
    def _mentions_alarm_cancel(text: str) -> bool:
        if "alarm" not in text:
            return False
        cancel_words = ("cancel", "delete", "remove", "clear", "turn off")
        return any(word in text for word in cancel_words)

    async def _cancel_alarm_shortcut(self, alarm_intent: tuple[str, list[int] | None, str | None] | None) -> bool:
        if not self.schedule_service or not alarm_intent:
            return False
        time_of_day, _, label = alarm_intent
        target = self._find_alarm_candidate(time_of_day, label)
        if not target:
            return False
        await self.schedule_service.delete_event(target["id"])
        return True

    def _find_alarm_candidate(self, time_of_day: str | None, label: str | None) -> dict[str, Any] | None:
        alarms = self.schedule_service.list_events("alarm")
        if not alarms:
            return None
        label_lower = label.lower() if label else None
        matches: list[dict[str, Any]] = []
        for alarm in alarms:
            event_time = alarm.get("time")
            if time_of_day and event_time != time_of_day:
                continue
            event_label = (alarm.get("label") or "").lower()
            if label_lower and (not event_label or label_lower not in event_label):
                continue
            matches.append(alarm)
        if not matches:
            return None
        return matches[0]

    async def _stop_active_schedule(self, lowered: str) -> bool:
        alarm = self.schedule_service.active_event("alarm")
        if alarm:
            await self.schedule_service.stop_event(alarm["id"], reason="voice")
            return True
        timer = self.schedule_service.active_event("timer")
        if timer and ("timer" in lowered or lowered in {"stop", "stop it"}):
            await self.schedule_service.stop_event(timer["id"], reason="voice")
            return True
        return False

    def _format_alarm_summary(self, alarm: dict[str, Any]) -> str:
        next_fire = alarm.get("next_fire")
        label = alarm.get("label")
        try:
            dt = datetime.fromisoformat(next_fire) if next_fire else None
        except (TypeError, ValueError):
            dt = None
        if dt:
            dt = dt.astimezone()
            time_str = dt.strftime("%-I:%M %p")
            if dt.minute == 0:
                # Drop ":00" for cleaner TTS output on o'clock times.
                time_str = dt.strftime("%-I %p")
            day = dt.strftime("%A")
            base = f"Your next alarm is set for {time_str} on {day}"
        else:
            base = "You have an upcoming alarm"
        if label:
            base = f"{base} ({label})"
        return f"{base}."

    def _extract_timer_label(self, lowered: str) -> str | None:
        match = re.search(r"timer (?:for|named)\s+([a-z0-9 ]+)", lowered)
        if match:
            return match.group(1).strip()
        match = re.search(r"for ([a-z0-9 ]+) timer", lowered)
        if match:
            return match.group(1).strip()
        return None

    async def _extend_timer_shortcut(self, seconds: int, label: str | None) -> bool:
        timer = self._find_timer_candidate(label)
        if not timer:
            return False
        await self.schedule_service.extend_timer(timer["id"], seconds)
        return True

    async def _cancel_timer_shortcut(self, label: str | None) -> bool:
        timer = self._find_timer_candidate(label)
        if not timer:
            return False
        await self.schedule_service.stop_event(timer["id"], reason="voice_cancel")
        return True

    def _find_timer_candidate(self, label: str | None) -> dict[str, Any] | None:
        timers = self.schedule_service.list_events("timer")
        if not timers:
            return None
        if label:
            wanted = label.lower()
            for timer in timers:
                current_label = (timer.get("label") or "").lower()
                if current_label and wanted in current_label:
                    return timer
        active = self.schedule_service.active_event("timer")
        if active:
            if not label:
                return active
            current_label = (active.get("label") or "").lower()
            if current_label and label.lower() in current_label:
                return active
        if len(timers) == 1 and not label:
            return timers[0]
        return None

    def _log_assistant_response(self, wake_word: str, text: str | None, pipeline: str = "pulse") -> None:
        if not self._log_llm_messages or not text:
            return
        snippet = text if len(text) <= 240 else f"{text[:237]}..."
        LOGGER.info("Response (%s/%s): %s", pipeline, wake_word, snippet)

    @staticmethod
    def _estimate_speech_duration(text: str) -> float:
        words = max(1, len(text.split()))
        return words / 2.5

    @staticmethod
    def _extract_timer_start_intent(lowered: str) -> tuple[int, str | None] | None:
        if "timer" not in lowered:
            return None
        if not any(word in lowered for word in ("start", "set", "create")):
            return None
        duration_match = re.search(
            r"((?:\d+(?:\.\d+)?|[a-z]+))\s*(seconds?|second|secs?|minutes?|minute|mins?|hours?|hour|hrs?)",
            lowered,
        )
        if not duration_match:
            return None
        raw_amount = duration_match.group(1)
        amount = PulseAssistant._parse_numeric_token(raw_amount)
        if amount is None:
            return None
        unit = duration_match.group(2)
        unit = unit.rstrip("s")
        multipliers = {
            "second": 1,
            "sec": 1,
            "minute": 60,
            "min": 60,
            "hour": 3600,
            "hr": 3600,
        }
        multiplier = multipliers.get(unit, 60)
        duration_seconds = max(1, int(amount * multiplier))
        label = None
        label_match = re.search(r"timer for ([a-z][a-z0-9 ]+)", lowered)
        if label_match:
            candidate = label_match.group(1).strip()
            if candidate and not re.fullmatch(r"\d+(\.\d+)?\s*(seconds?|minutes?|hours?)", candidate):
                label = candidate
        return duration_seconds, label

    @staticmethod
    def _parse_numeric_token(token: str) -> float | None:
        try:
            return float(token)
        except ValueError:
            pass
        token = token.strip().lower()
        number_words = {
            "zero": 0,
            "one": 1,
            "two": 2,
            "three": 3,
            "four": 4,
            "five": 5,
            "six": 6,
            "seven": 7,
            "eight": 8,
            "nine": 9,
            "ten": 10,
            "eleven": 11,
            "twelve": 12,
            "thirteen": 13,
            "fourteen": 14,
            "fifteen": 15,
            "sixteen": 16,
            "seventeen": 17,
            "eighteen": 18,
            "nineteen": 19,
            "twenty": 20,
            "thirty": 30,
            "forty": 40,
            "fifty": 50,
            "sixty": 60,
            "half": 0.5,
            "quarter": 0.25,
            "a": 1,
            "an": 1,
        }
        if token in number_words:
            return float(number_words[token])
        # Handle composite like "twenty five"
        parts = token.split()
        if len(parts) == 2 and parts[0] in number_words and parts[1] in number_words and number_words[parts[1]] < 10:
            return float(number_words[parts[0]] + number_words[parts[1]])
        return None

    @staticmethod
    def _describe_duration(seconds: int) -> str:
        if seconds % 3600 == 0:
            hours = seconds // 3600
            return f"{hours} hour{'s' if hours != 1 else ''}"
        if seconds % 60 == 0:
            minutes = seconds // 60
            return f"{minutes} minute{'s' if minutes != 1 else ''}"
        return f"{seconds} seconds"

    @staticmethod
    def _extract_alarm_start_intent(text: str) -> tuple[str, list[int] | None, str | None] | None:
        if "alarm" not in text:
            return None
        time_match = re.search(
            r"(?:alarm\s+(?:for|at)\s+)?((?:\d{1,2}\s+\d{2})|\d{1,4}(?::\d{2})?)\s*(am|pm)?",
            text,
        )
        if not time_match:
            return None
        time_token = time_match.group(1)
        suffix = time_match.group(2)
        time_of_day = PulseAssistant._parse_time_token(time_token, suffix)
        if not time_of_day:
            return None
        days = None
        day_match = re.search(r"(?:on|every)\s+([a-z ,]+)", text)
        if day_match:
            days = parse_day_tokens(day_match.group(1))
        label = None
        label_match = re.search(r"(?:called|named)\s+([a-z0-9 ]+)", text)
        if label_match:
            label = label_match.group(1).strip()
        return time_of_day, days, label

    @staticmethod
    def _parse_time_token(token: str, suffix: str | None) -> str | None:
        token = token.replace(" ", "")
        hour_str = token
        minute_str = "00"
        if ":" in token:
            hour_str, minute_str = token.split(":", 1)
        elif len(token) in (3, 4):
            hour_str = token[:-2]
            minute_str = token[-2:]
        try:
            hour = int(hour_str)
            minute = int(minute_str)
        except ValueError:
            return None
        if suffix:
            if suffix.startswith("p") and hour < 12:
                hour += 12
            if suffix.startswith("a") and hour == 12:
                hour = 0
        hour %= 24
        minute = max(0, min(59, minute))
        return f"{hour:02d}:{minute:02d}"

    @staticmethod
    def _format_alarm_confirmation(time_of_day: str, days: list[int] | None, label: str | None) -> str:
        try:
            dt = datetime.strptime(time_of_day, "%H:%M").replace(year=1900, month=1, day=1)
            time_phrase = dt.strftime("%-I:%M %p")
            if dt.minute == 0:
                # Many TTS voices over-articulate the ":00" segment, so drop it for o'clock times.
                time_phrase = dt.strftime("%-I %p")
        except ValueError:
            time_phrase = time_of_day
        if days:
            day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
            normalized_days = sorted({d % 7 for d in days})
            if normalized_days == [0, 1, 2, 3, 4]:
                day_phrase = " on weekdays"
            elif normalized_days == [5, 6]:
                day_phrase = " on weekends"
            elif normalized_days == list(range(7)):
                day_phrase = " every day"
            elif len(normalized_days) == 1:
                day_phrase = f" on {day_names[normalized_days[0]]}"
            else:
                names = ", ".join(day_names[d] for d in normalized_days)
                day_phrase = f" on {names}"
        else:
            day_phrase = ""
        label_phrase = f" called {label}" if label else ""
        return f"Setting an alarm for {time_phrase}{day_phrase}{label_phrase}."

    def _extract_reminder_intent(self, normalized: str, original: str) -> ReminderIntent | None:
        if "remind me" not in normalized or not self.schedule_service:
            return None
        idx = normalized.find("remind me")
        suffix_original = original[idx + len("remind me") :].strip()
        suffix_lower = normalized[idx + len("remind me") :].strip()
        if not suffix_original:
            return None
        message = suffix_original.strip()
        schedule_section = suffix_lower
        to_idx = suffix_lower.find(" to ")
        if to_idx != -1:
            message = suffix_original[to_idx + 4 :].strip()
            schedule_section = suffix_lower[:to_idx].strip()
        parsed = self._parse_reminder_schedule(schedule_section, suffix_lower)
        if not parsed:
            return None
        fire_time, repeat_rule = parsed
        message = message or "reminder"
        return ReminderIntent(message=message, fire_time=fire_time, repeat_rule=repeat_rule)

    def _parse_reminder_schedule(
        self,
        schedule_text: str,
        fallback_text: str,
    ) -> tuple[datetime, dict[str, Any] | None] | None:
        text = schedule_text or fallback_text
        lower = text.strip().lower()
        if not lower:
            lower = fallback_text.lower()
        now = datetime.now().astimezone()
        duration_seconds = self._extract_duration_seconds_from_text(lower)
        if duration_seconds > 0:
            return now + timedelta(seconds=duration_seconds), None
        time_of_day = self._extract_time_of_day_from_text(lower)
        has_every = "every" in lower
        day_indexes = parse_day_tokens(lower)
        if has_every:
            interval_months = self._extract_interval_value(lower, ("month", "months"))
            interval_weeks = self._extract_interval_value(lower, ("week", "weeks"))
            interval_days = self._extract_interval_value(lower, ("day", "days"))
            if "month" in lower or "monthly" in lower:
                if interval_months and interval_months > 1:
                    start = self._apply_time_of_day(now, time_of_day)
                    if start <= now:
                        start = self._add_months_local(start, interval_months)
                    repeat_rule = {"type": "interval", "interval_months": interval_months, "time": time_of_day}
                    return start, repeat_rule
                day_of_month = self._extract_day_of_month(lower) or now.day
                fire_time = self._next_monthly_datetime(day_of_month, time_of_day, now)
                repeat_rule = {"type": "monthly", "day": day_of_month, "time": time_of_day}
                return fire_time, repeat_rule
            if interval_months:
                start = self._apply_time_of_day(now, time_of_day)
                if start <= now:
                    start = self._add_months_local(start, interval_months)
                repeat_rule = {"type": "interval", "interval_months": interval_months, "time": time_of_day}
                return start, repeat_rule
            if interval_weeks:
                days_to_add = interval_weeks * 7
                start = self._apply_time_of_day(now, time_of_day)
                if start <= now:
                    start += timedelta(days=days_to_add)
                repeat_rule = {"type": "interval", "interval_days": days_to_add, "time": time_of_day}
                return start, repeat_rule
            if interval_days:
                start = self._apply_time_of_day(now, time_of_day)
                if start <= now:
                    start += timedelta(days=interval_days)
                repeat_rule = {"type": "interval", "interval_days": interval_days, "time": time_of_day}
                return start, repeat_rule
            weekdays = day_indexes or list(range(7))
            fire_time = self._next_weekly_datetime(weekdays, time_of_day, now)
            repeat_rule = {"type": "weekly", "days": weekdays, "time": time_of_day}
            return fire_time, repeat_rule
        if day_indexes:
            fire_time = self._next_weekday_datetime(day_indexes[0], time_of_day, now)
            return fire_time, None
        if "tomorrow" in lower:
            return self._apply_time_of_day(now + timedelta(days=1), time_of_day), None
        if "today" in lower:
            candidate = self._apply_time_of_day(now, time_of_day)
            if candidate <= now:
                candidate += timedelta(days=1)
            return candidate, None
        default_time = self._apply_time_of_day(now, time_of_day)
        if default_time <= now:
            default_time += timedelta(days=1)
        return default_time, None

    @staticmethod
    def _extract_interval_value(text: str, keywords: tuple[str, ...]) -> int | None:
        joined = "|".join(keywords)
        match = re.search(rf"every\s+(\d+)\s+({joined})", text)
        if match:
            try:
                return int(match.group(1))
            except ValueError:
                return None
        return None

    @staticmethod
    def _extract_day_of_month(text: str) -> int | None:
        match = re.search(r"\bon\s+the\s+(\d{1,2})", text)
        if match:
            return int(match.group(1))
        return None

    def _extract_duration_seconds_from_text(self, text: str) -> float:
        match = re.search(r"\bin\s+([0-9][a-z0-9 :]*)", text)
        if not match:
            return 0.0
        candidate = match.group(1)
        for stop in (" to ", " for ", ",", " and "):
            idx = candidate.find(stop)
            if idx != -1:
                candidate = candidate[:idx]
        return parse_duration_seconds(candidate.strip())

    def _extract_time_of_day_from_text(self, text: str) -> str:
        lower = text.lower()
        match = re.search(r"(?<!\d)(\d{1,2})(?::(\d{2}))?\s*(am|pm)", lower)
        if match:
            token = match.group(1)
            if match.group(2):
                token = f"{token}:{match.group(2)}"
            parsed = self._parse_time_token(token, match.group(3))
            if parsed:
                return parsed
        match = re.search(r"\b(\d{3,4})\s*(am|pm)\b", lower)
        if match:
            parsed = self._parse_time_token(match.group(1), match.group(2))
            if parsed:
                return parsed
        match = re.search(r"\b(\d{1,2}:\d{2})\b", lower)
        if match:
            parsed = self._parse_time_token(match.group(1), None)
            if parsed:
                return parsed
        keyword_map = {
            "morning": "08:00",
            "afternoon": "13:00",
            "evening": "17:00",
            "night": "20:00",
            "tonight": "20:00",
            "noon": "12:00",
            "midnight": "00:00",
        }
        for keyword, value in keyword_map.items():
            if keyword in lower:
                return value
        return "08:00"

    @staticmethod
    def _apply_time_of_day(reference: datetime, time_str: str) -> datetime:
        hour_str, minute_str = time_str.split(":")
        hour = int(hour_str)
        minute = int(minute_str)
        return reference.replace(hour=hour, minute=minute, second=0, microsecond=0)

    def _next_weekday_datetime(self, weekday: int, time_str: str, now: datetime) -> datetime:
        weekday = weekday % 7
        candidate = self._apply_time_of_day(now, time_str)
        offset = (weekday - candidate.weekday()) % 7
        if offset == 0 and candidate <= now:
            offset = 7
        return self._apply_time_of_day(now + timedelta(days=offset), time_str)

    def _next_weekly_datetime(self, weekdays: list[int], time_str: str, now: datetime) -> datetime:
        weekdays = sorted({day % 7 for day in weekdays}) or list(range(7))
        for offset in range(0, 8):
            candidate = self._apply_time_of_day(now + timedelta(days=offset), time_str)
            if candidate <= now:
                continue
            if candidate.weekday() in weekdays:
                return candidate
        return self._apply_time_of_day(now + timedelta(days=1), time_str)

    def _next_monthly_datetime(self, day: int, time_str: str, now: datetime) -> datetime:
        day = max(1, min(31, day))
        candidate = self._apply_time_of_day(now, time_str)
        last = calendar.monthrange(candidate.year, candidate.month)[1]
        candidate = candidate.replace(day=min(day, last))
        if candidate <= now:
            candidate = self._add_months_local(candidate, 1)
            last = calendar.monthrange(candidate.year, candidate.month)[1]
            candidate = candidate.replace(day=min(day, last))
        return candidate

    @staticmethod
    def _add_months_local(dt_obj: datetime, months: int) -> datetime:
        total = dt_obj.month - 1 + months
        year = dt_obj.year + total // 12
        month = total % 12 + 1
        day = min(dt_obj.day, calendar.monthrange(year, month)[1])
        return dt_obj.replace(year=year, month=month, day=day)

    def _format_reminder_confirmation(self, event: ScheduledEvent) -> str:
        next_fire = event.next_fire
        try:
            dt = datetime.fromisoformat(next_fire).astimezone()
        except (TypeError, ValueError):
            dt = datetime.now().astimezone()
        repeat_meta = event.metadata.get("reminder") if event.event_type == "reminder" else {}
        repeat_rule = repeat_meta.get("repeat") if isinstance(repeat_meta, dict) else None
        if repeat_rule:
            repeat_phrase = self._describe_reminder_repeat(repeat_rule)
            return f"Okay, I'll remind you {repeat_phrase}."
        time_phrase = dt.strftime("%-I:%M %p")
        today = datetime.now().astimezone().date()
        if dt.date() == today:
            day_phrase = "today"
        elif dt.date() == today + timedelta(days=1):
            day_phrase = "tomorrow"
        else:
            day_phrase = f"on {dt.strftime('%A')}"
        return f"Got it, I'll remind you {day_phrase} at {time_phrase}."

    @staticmethod
    def _format_time_phrase_from_string(time_str: str) -> str:
        try:
            dt = datetime.strptime(time_str, "%H:%M")
        except ValueError:
            return time_str
        return dt.strftime("%-I:%M %p") if dt.minute else dt.strftime("%-I %p")

    @staticmethod
    def _ordinal(value: int) -> str:
        if 10 <= value % 100 <= 20:
            suffix = "th"
        else:
            suffix = {1: "st", 2: "nd", 3: "rd"}.get(value % 10, "th")
        return f"{value}{suffix}"

    def _describe_reminder_repeat(self, repeat: dict[str, Any]) -> str:
        repeat_type = (repeat.get("type") or "").lower()
        time_text = repeat.get("time") or "08:00"
        time_phrase = self._format_time_phrase_from_string(time_text)
        if repeat_type == "weekly":
            days = repeat.get("days") or list(range(7))
            if sorted(days) == list(range(7)):
                return f"every day at {time_phrase}"
            day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
            labels = [day_names[day % 7] for day in days]
            if len(labels) == 1:
                day_phrase = labels[0]
            else:
                day_phrase = ", ".join(labels[:-1]) + f", and {labels[-1]}"
            return f"every {day_phrase} at {time_phrase}"
        if repeat_type == "monthly":
            day = repeat.get("day")
            if isinstance(day, int):
                return f"on the {self._ordinal(day)} of each month at {time_phrase}"
            return f"each month at {time_phrase}"
        if repeat_type == "interval":
            months = repeat.get("interval_months")
            days = repeat.get("interval_days")
            if months:
                return f"every {months} month{'s' if months != 1 else ''} at {time_phrase}"
            if days:
                if days % 7 == 0:
                    weeks = days // 7
                    return f"every {weeks} week{'s' if weeks != 1 else ''} at {time_phrase}"
                return f"every {days} day{'s' if days != 1 else ''} at {time_phrase}"
        return f"at {time_phrase}"

    async def _maybe_handle_music_command(self, transcript: str) -> bool:
        query = (transcript or "").strip().lower()
        if not query or not self.home_assistant or not self.config.media_player_entity:
            return False
        controls = [
            (("pause the music", "pause music", "pause the song", "pause song"), "media_pause", "Paused the music."),
            (
                ("stop the music", "stop music", "stop the song", "stop song"),
                "media_stop",
                "Stopped the music.",
            ),
            (
                ("next song", "skip song", "skip this song", "next track"),
                "media_next_track",
                "Skipping to the next song.",
            ),
        ]
        for phrases, service, success_text in controls:
            if any(phrase in query for phrase in phrases):
                return await self._call_music_service(service, success_text)
        info_phrases = (
            "what song is this",
            "what song am i listening to",
            "what is this song",
            "what's this song",
            "what's playing",
            "what song",
            "who is this",
            "who's this",
        )
        if any(phrase in query for phrase in info_phrases):
            return await self._describe_current_track("who" in query)
        return False

    async def _call_music_service(self, service: str, success_text: str) -> bool:
        entity = self.config.media_player_entity
        ha_client = self.home_assistant
        if not entity or not ha_client:
            return False
        try:
            await ha_client.call_service("media_player", service, {"entity_id": entity})
        except HomeAssistantError as exc:
            LOGGER.debug("Music control %s failed for %s: %s", service, entity, exc)
            spoken = "I couldn't control the music right now."
            await self._speak(spoken)
            self._log_assistant_response("music", spoken, pipeline="pulse")
            return True
        await self._speak(success_text)
        self._log_assistant_response("music", success_text, pipeline="pulse")
        return True

    async def _describe_current_track(self, emphasize_artist: bool) -> bool:
        state = await self._fetch_media_player_state()
        if state is None:
            spoken = "I couldn't reach the player for that info."
            await self._speak(spoken)
            self._log_assistant_response("music", spoken, pipeline="pulse")
            return True
        status = str(state.get("state") or "")
        attributes = state.get("attributes") or {}
        title = attributes.get("media_title") or attributes.get("media_episode_title")
        artist = (
            attributes.get("media_artist")
            or attributes.get("media_album_artist")
            or attributes.get("media_series_title")
        )
        if status not in {"playing", "paused"} or not (title or artist):
            spoken = "Nothing is playing right now."
            await self._speak(spoken)
            self._log_assistant_response("music", spoken, pipeline="pulse")
            return True
        if title and artist:
            message = f"This is {artist} — {title}."
        elif title:
            message = f"This song is {title}."
        else:
            message = f"This is by {artist}."
        if emphasize_artist and artist and not title:
            message = f"This is {artist}."
        await self._speak(message)
        self._log_assistant_response("music", message, pipeline="pulse")
        return True

    async def _fetch_media_player_state(self) -> dict[str, Any] | None:
        """Backward compatibility wrapper."""
        return await self.media_controller.fetch_media_player_state()

    def _handle_ha_pipeline_command(self, payload: str) -> None:
        value = payload.strip()
        self._ha_pipeline_override = value or None
        self._publish_preference_state("ha_pipeline", self._active_ha_pipeline() or "")

    def _active_ha_pipeline(self) -> str | None:
        return self._ha_pipeline_override or self.config.home_assistant.assist_pipeline

    def _handle_llm_provider_command(self, payload: str) -> None:
        value = payload.strip().lower()
        if not value:
            self._llm_provider_override = None
        elif value in {"openai", "gemini"}:
            self._llm_provider_override = value
        else:
            LOGGER.debug("Ignoring invalid LLM provider: %s", payload)
            return
        self.llm = self._build_llm_provider()
        self._publish_preference_state("llm_provider", self._active_llm_provider())

    def _active_llm_provider(self) -> str:
        provider = self._llm_provider_override or self.config.llm.provider or "openai"
        return provider.strip().lower() or "openai"

    def _build_llm_provider(self) -> LLMProvider:
        provider = self._active_llm_provider()
        llm_config = replace(self.config.llm, provider=provider)
        LOGGER.info("Using %s LLM provider", provider)
        return build_llm_provider(llm_config, LOGGER)

    def _publish_assistant_discovery(self) -> None:
        device = {
            "identifiers": [f"pulse:{self.config.hostname}"],
            "manufacturer": "Pulse",
            "model": "Pulse Kiosk",
            "name": self.config.device_name,
        }
        prefix = "homeassistant"
        hostname_safe = self.config.hostname.replace(" ", "_").replace("/", "_")
        # Assist in progress binary sensor
        self._publish_message(
            f"{prefix}/binary_sensor/{hostname_safe}_assist_in_progress/config",
            json.dumps(
                {
                    "name": f"{self.config.device_name} Assist In Progress",
                    "unique_id": f"{self.config.hostname}-assist-in-progress",
                    "state_topic": self._assist_in_progress_topic,
                    "payload_on": "ON",
                    "payload_off": "OFF",
                    "device": device,
                    "entity_category": "diagnostic",
                }
            ),
            retain=True,
        )
        # Assist stage sensor
        self._publish_message(
            f"{prefix}/sensor/{hostname_safe}_assist_stage/config",
            json.dumps(
                {
                    "name": f"{self.config.device_name} Assist Stage",
                    "unique_id": f"{self.config.hostname}-assist-stage",
                    "state_topic": self._assist_stage_topic,
                    "device": device,
                    "entity_category": "diagnostic",
                    "icon": "mdi:progress-clock",
                }
            ),
            retain=True,
        )
        # Last wake word sensor
        self._publish_message(
            f"{prefix}/sensor/{hostname_safe}_last_wake_word/config",
            json.dumps(
                {
                    "name": f"{self.config.device_name} Last Wake Word",
                    "unique_id": f"{self.config.hostname}-last-wake-word",
                    "state_topic": self._assist_wake_topic,
                    "device": device,
                    "entity_category": "diagnostic",
                    "icon": "mdi:account-voice",
                }
            ),
            retain=True,
        )
        # Speaking style select
        self._publish_message(
            f"{prefix}/select/{hostname_safe}_speaking_style/config",
            json.dumps(
                {
                    "name": f"{self.config.device_name} Speaking Style",
                    "unique_id": f"{self.config.hostname}-speaking-style",
                    "state_topic": f"{self._preferences_topic}/speaking_style/state",
                    "command_topic": f"{self._preferences_topic}/speaking_style/set",
                    "options": ["relaxed", "normal", "aggressive"],
                    "device": device,
                    "entity_category": "config",
                }
            ),
            retain=True,
        )
        # Wake sensitivity select
        self._publish_message(
            f"{prefix}/select/{hostname_safe}_wake_sensitivity/config",
            json.dumps(
                {
                    "name": f"{self.config.device_name} Wake Sensitivity",
                    "unique_id": f"{self.config.hostname}-wake-sensitivity",
                    "state_topic": f"{self._preferences_topic}/wake_sensitivity/state",
                    "command_topic": f"{self._preferences_topic}/wake_sensitivity/set",
                    "options": ["low", "normal", "high"],
                    "device": device,
                    "entity_category": "config",
                }
            ),
            retain=True,
        )
        # Wake sound switch
        self._publish_message(
            f"{prefix}/switch/{hostname_safe}_wake_sound/config",
            json.dumps(
                {
                    "name": f"{self.config.device_name} Wake Sound",
                    "unique_id": f"{self.config.hostname}-wake-sound",
                    "state_topic": f"{self._preferences_topic}/wake_sound/state",
                    "command_topic": f"{self._preferences_topic}/wake_sound/set",
                    "payload_on": "on",
                    "payload_off": "off",
                    "device": device,
                    "entity_category": "config",
                }
            ),
            retain=True,
        )
        # Log LLM switch
        self._publish_message(
            f"{prefix}/switch/{hostname_safe}_log_llm/config",
            json.dumps(
                {
                    "name": f"{self.config.device_name} Log LLM Responses",
                    "unique_id": f"{self.config.hostname}-log-llm",
                    "state_topic": f"{self._preferences_topic}/log_llm/state",
                    "command_topic": f"{self._preferences_topic}/log_llm/set",
                    "payload_on": "on",
                    "payload_off": "off",
                    "device": device,
                    "entity_category": "config",
                }
            ),
            retain=True,
        )
        # HA pipeline text entity
        self._publish_message(
            f"{prefix}/text/{hostname_safe}_ha_pipeline/config",
            json.dumps(
                {
                    "name": f"{self.config.device_name} HA Assist Pipeline",
                    "unique_id": f"{self.config.hostname}-ha-assist-pipeline",
                    "state_topic": f"{self._preferences_topic}/ha_pipeline/state",
                    "command_topic": f"{self._preferences_topic}/ha_pipeline/set",
                    "device": device,
                    "entity_category": "config",
                }
            ),
            retain=True,
        )
        # LLM provider select
        self._publish_message(
            f"{prefix}/select/{hostname_safe}_llm_provider/config",
            json.dumps(
                {
                    "name": f"{self.config.device_name} LLM Provider",
                    "unique_id": f"{self.config.hostname}-llm-provider",
                    "state_topic": f"{self._preferences_topic}/llm_provider/state",
                    "command_topic": f"{self._preferences_topic}/llm_provider/set",
                    "options": ["openai", "gemini"],
                    "device": device,
                    "entity_category": "config",
                }
            ),
            retain=True,
        )


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()
    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO))

    config = AssistantConfig.from_env()
    assistant = PulseAssistant(config)

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _handle_signal(signum: int) -> None:
        LOGGER.info("Received signal %s, shutting down", signum)
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _handle_signal, sig)

    run_task = asyncio.create_task(assistant.run())
    await stop_event.wait()
    await assistant.shutdown()
    run_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await run_task


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
