#!/usr/bin/env python3
"""Pulse voice assistant daemon."""

from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
import json
import logging
import os
import signal
import subprocess
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
from pulse.assistant.config import AssistantConfig, AssistantPreferences, WyomingEndpoint
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
from pulse.assistant.mqtt_publisher import AssistantMqttPublisher
from pulse.assistant.preference_manager import PreferenceManager
from pulse.assistant.response_modes import select_ha_response
from pulse.assistant.routines import RoutineEngine, default_routines
from pulse.assistant.schedule_commands import ScheduleCommandProcessor
from pulse.assistant.schedule_intents import ScheduleIntentParser
from pulse.assistant.schedule_service import ScheduleService
from pulse.assistant.schedule_shortcuts import ScheduleShortcutHandler
from pulse.assistant.scheduler import AssistantScheduler
from pulse.assistant.wake_detector import WakeDetector, compute_rms
from pulse.assistant.wyoming import play_tts_stream, transcribe_audio
from pulse.audio import play_sound, play_volume_feedback
from pulse.sound_library import SoundLibrary

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
        self.routines = RoutineEngine(default_routines())
        self.home_assistant: HomeAssistantClient | None = None
        if config.home_assistant.base_url and config.home_assistant.token:
            try:
                self.home_assistant = HomeAssistantClient(config.home_assistant)
            except ValueError as exc:
                LOGGER.warning("[assistant] Home Assistant config invalid: %s", exc)
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
            sound_settings=self.config.sounds,
            skip_dates=set(self.config.work_pause.skip_dates),
            skip_weekdays=set(self.config.work_pause.skip_weekdays),
        )
        # Sound library for dynamic sound options
        self._sound_library = SoundLibrary(custom_dir=self.config.sounds.custom_dir)

        # Initialize MQTT publisher
        self.publisher = AssistantMqttPublisher(
            mqtt=self.mqtt,
            config=self.config,
            home_assistant=self.home_assistant,
            schedule_service=self.schedule_service,
            sound_library=self._sound_library,
            logger=LOGGER,
        )

        # Initialize preference manager (Phase 2 extraction)
        self.preference_manager = PreferenceManager(
            mqtt=self.mqtt,
            config=self.config,
            sound_library=self._sound_library,
            publisher=self.publisher,
            logger=LOGGER,
        )

        # Initialize schedule intent parser (Phase 3 extraction)
        self.schedule_intents = ScheduleIntentParser()

        # Initialize schedule shortcut handler (Phase 4 extraction)
        self.schedule_shortcuts = ScheduleShortcutHandler(
            schedule_service=self.schedule_service,
            schedule_intents=self.schedule_intents,
            publisher=self.publisher,
            config=self.config,
            logger=LOGGER,
        )
        self.schedule_shortcuts.set_speak_callback(self._speak)
        self.schedule_shortcuts.set_log_response_callback(self._log_assistant_response)

        # Initialize schedule command processor (Phase 5 extraction)
        self.schedule_commands = ScheduleCommandProcessor(
            schedule_service=self.schedule_service,
            publisher=self.publisher,
            base_topic=self.config.mqtt.topic_base,
            logger=LOGGER,
        )
        self.schedule_commands.set_log_activity_callback(self._log_activity_event)

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
            else:
                LOGGER.warning(
                    "[assistant] Calendar sync enabled but no feeds configured (PULSE_CALENDAR_ICS_URLS is empty)"
                )
        else:
            pass
        self._shutdown = asyncio.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        base_topic = self.config.mqtt.topic_base
        self._assist_in_progress_topic = f"{base_topic}/assistant/in_progress"
        self._assist_metrics_topic = f"{base_topic}/assistant/metrics"
        self._assist_stage_topic = f"{base_topic}/assistant/stage"
        self._assist_pipeline_topic = f"{base_topic}/assistant/active_pipeline"
        self._assist_wake_topic = f"{base_topic}/assistant/last_wake_word"
        self._info_card_topic = f"{base_topic}/info_card"
        self._heartbeat_topic = f"{base_topic}/assistant/heartbeat"
        self._kiosk_availability_topic = f"homeassistant/device/{self.config.hostname}/availability"
        self._kiosk_available: bool = True
        self._last_kiosk_online: float = time.monotonic()
        self._last_kiosk_restart_attempt: float = 0.0
        self._assist_stage = "idle"
        self._assist_pipeline: str | None = None
        self._current_tracker: AssistRunTracker | None = None
        self._self_audio_trigger_level = max(2, self.config.self_audio_trigger_level)
        self._media_player_entity = self.config.media_player_entity
        self._media_player_entities = self.config.media_player_entities
        self._alert_topics = self.config.alert_topics
        self._intercom_topic = self.config.intercom_topic

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
            additional_entities=list(self._media_player_entities),
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
        self._log_transcripts = config.log_transcripts
        # Initialize LLM logging state from config, sync with preference manager
        self.preference_manager.log_llm_messages = config.log_llm_messages
        self._conversation_stop_prefixes = build_conversation_stop_prefixes(config)
        self._earmuffs_lock = threading.Lock()
        self._earmuffs_enabled = False
        self._earmuffs_manual_override: bool | None = None
        self._earmuffs_state_restored = False  # Track if we've restored state from MQTT
        base_topic = self.config.mqtt.topic_base
        self._earmuffs_state_topic = f"{base_topic}/earmuffs/state"
        self._earmuffs_set_topic = f"{base_topic}/earmuffs/set"
        self._last_health_signature: tuple[tuple[str, str], ...] | None = None

        # Set up preference manager callbacks
        self.preference_manager.set_wake_sensitivity_callback(self.wake_detector.mark_wake_context_dirty)
        self.preference_manager.set_llm_provider_callback(self._rebuild_llm_provider)
        self.preference_manager.set_sound_settings_callback(self.schedule_service.update_sound_settings)
        self.preference_manager.set_config_updated_callback(self._handle_config_updated)

        # Build LLM provider (uses preference_manager for overrides)
        self.llm: LLMProvider = self._build_llm_provider()

    def _handle_config_updated(self, new_config: AssistantConfig) -> None:
        """Handle config updates from preference manager to keep in sync."""
        self.config = new_config

    @property
    def preferences(self) -> AssistantPreferences:
        """Access current preferences via the preference manager."""
        return self.preference_manager.preferences

    @preferences.setter
    def preferences(self, value: AssistantPreferences) -> None:
        """Update preferences in the preference manager."""
        self.preference_manager.preferences = value

    def _rebuild_llm_provider(self) -> LLMProvider:
        """Rebuild and return the LLM provider with current settings."""
        self.llm = self._build_llm_provider()
        return self.llm

    async def run(self) -> None:
        try:
            self._loop = asyncio.get_running_loop()
            self.media_controller._loop = self._loop
            self.schedule_commands.set_event_loop(self._loop)
            self.mqtt.connect()
            self.preference_manager.subscribe_preference_topics()
            self._subscribe_schedule_topics()
            self._subscribe_playback_topic()
            self._subscribe_earmuffs_topic()
            self._subscribe_alert_topics()
            self._subscribe_intercom_topic()
            self._subscribe_kiosk_availability()

            # Start schedule + calendar before any retained-message waits
            try:
                await asyncio.wait_for(self.schedule_service.start(), timeout=8.0)
            except TimeoutError:
                LOGGER.exception("[assistant] Schedule service start() timed out")
                raise
            except Exception as exc:
                LOGGER.exception("[assistant] Schedule service start() failed: %s", exc)
                raise
            if self.calendar_sync:
                try:
                    await self.calendar_sync.start()
                except Exception as exc:
                    LOGGER.exception("[assistant] Failed to start calendar sync service: %s", exc)
                # Clear any stale calendar events on startup
                self._calendar_events = []
                self._calendar_updated_at = None
                # Publish empty schedule state to clear overlay cache
                self.publisher._publish_schedule_state({}, self._calendar_events, self._calendar_updated_at)
            else:
                LOGGER.warning("[assistant] calendar_sync is None, cannot start calendar sync service")

            # Publish preferences and retained-state after services are running
            self.publisher._publish_preferences(
                self.preferences,
                self.preference_manager.log_llm_messages,
                self.preference_manager.get_active_ha_pipeline(),
                self.preference_manager.get_active_llm_provider(),
                self.config.sounds,
            )
            # Wait a moment for retained MQTT messages to arrive before publishing state
            await asyncio.sleep(0.5)
            if not self._earmuffs_state_restored:
                # No retained message received, publish current state
                try:
                    self.publisher._publish_earmuffs_state(self._get_earmuffs_enabled())
                except Exception as exc:
                    LOGGER.exception("[assistant] Failed to publish earmuffs state: %s", exc)

            self.publisher._publish_assistant_discovery(self.config.hostname, self.config.device_name)
            self.publisher._publish_routine_overlay()
            await self.mic.start()
            self._set_assist_stage("pulse", "idle")
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
            from pulse.systemd_notify import ready as sd_ready

            sd_ready()
        except Exception as exc:
            LOGGER.exception("[assistant] Fatal error in assistant.run(): %s", exc)
            raise
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
            except Exception as exc:
                LOGGER.exception("[assistant] Pipeline %s failed for wake word %s: %s", pipeline, wake_word, exc)
                self._set_assist_stage(pipeline, "error", {"wake_word": wake_word, "error": str(exc)})
                self._finalize_assist_run(status="error")

    async def _heartbeat_loop(self) -> None:
        """Publish periodic heartbeat and monitor kiosk availability."""
        from pulse.systemd_notify import watchdog as sd_watchdog

        kiosk_grace_seconds = 90
        kiosk_restart_min_interval = 120
        while not self._shutdown.is_set():
            self.publisher._publish_message(self._heartbeat_topic, str(int(time.time())))
            sd_watchdog()
            # Check kiosk health and restart if needed
            now = time.monotonic()
            kiosk_silence = now - self._last_kiosk_online
            if not self._kiosk_available and kiosk_silence >= kiosk_grace_seconds:
                if now - self._last_kiosk_restart_attempt >= kiosk_restart_min_interval:
                    self._last_kiosk_restart_attempt = now
                    LOGGER.warning(
                        "[assistant] kiosk offline for %ds; restarting pulse-kiosk-mqtt.service",
                        int(kiosk_silence),
                    )
                    try:
                        await asyncio.to_thread(
                            subprocess.run,  # nosec B603 - hardcoded command array
                            ["sudo", "systemctl", "restart", "pulse-kiosk-mqtt.service"],
                            check=True,
                            timeout=30,
                        )
                    except Exception as exc:  # noqa: BLE001
                        LOGGER.warning("[assistant] kiosk restart failed: %s", exc)
            await asyncio.sleep(30)

    async def shutdown(self) -> None:
        self._shutdown.set()
        heartbeat = getattr(self, "_heartbeat_task", None)
        if heartbeat:
            heartbeat.cancel()
            try:
                await heartbeat
            except asyncio.CancelledError:
                pass  # expected during shutdown
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
            LOGGER.warning("[assistant] No STT endpoint configured")
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
            LOGGER.warning("[assistant] No TTS endpoint configured; cannot speak response")
            return
        await play_tts_stream(
            text,
            endpoint=target,
            sink=self.player,
            voice_name=voice_name,
            audio_guard=self.wake_detector.local_audio_block(),
            logger=LOGGER,
        )

    async def _maybe_publish_light_overlay(self, executed_actions: list[str]) -> None:
        # Suppress light overlay for HA actions (no info card needed)
        return

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
            except Exception:
                LOGGER.info("[assistant] Wake sound playback failed", exc_info=True)

    async def _play_ack_tone(self, sound_id: str | None) -> None:
        """Play a short acknowledgement tone for HA actions."""
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
            LOGGER.debug("No acknowledgement tone available (sound_id=%s)", sound_id)
            return
        async with self.wake_detector.local_audio_block():
            await asyncio.to_thread(play_sound, sound_path)

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
                LOGGER.info("[assistant] No speech captured for wake word %s", wake_word)
                self._finalize_assist_run(status="no_audio")
                return
            tracker.begin_stage("thinking")
            self._set_assist_stage("pulse", "thinking", {"wake_word": wake_word})
            transcript = await self._transcribe(audio_bytes)
            if not transcript:
                self._finalize_assist_run(status="no_transcript")
                return
            if self._log_transcripts:
                LOGGER.info("[assistant] Transcript [%s]: %s", wake_word, transcript)
            if self.preference_manager.log_llm_messages:
                transcript_payload = {"text": transcript, "wake_word": wake_word}
                self.publisher._publish_message(self.config.transcript_topic, json.dumps(transcript_payload))
            if await self._maybe_handle_stop_phrase(transcript, wake_word, tracker):
                self._finalize_assist_run(status="cancelled")
                return
            if await self._maybe_handle_music_command(transcript):
                self._finalize_assist_run(status="success")
                return
            if await self.schedule_shortcuts.maybe_handle_schedule_shortcut(transcript):
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
                    if follow_up_attempts >= max_follow_up_attempts:
                        tracker.begin_stage("speaking")
                        self._set_assist_stage("pulse", "speaking", {"wake_word": wake_word, "follow_up": True})
                        await self._speak("Sorry, I didn't catch that.")
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
                if await self.schedule_shortcuts.maybe_handle_schedule_shortcut(follow_up_transcript):
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
                "[assistant] Home Assistant pipeline invoked for wake word '%s' but base URL/token are missing",
                wake_word,
            )
            self._finalize_assist_run(status="config_error")
            return
        if not ha_client:
            LOGGER.warning("[assistant] Home Assistant client not initialized; cannot handle wake word '%s'", wake_word)
            self._finalize_assist_run(status="config_error")
            return
        await self.schedule_service.pause_active_audio()
        try:
            audio_bytes = await self._record_phrase()
            if not audio_bytes:
                LOGGER.info("[assistant] No speech captured for Home Assistant wake word %s", wake_word)
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
                LOGGER.warning("[assistant] Home Assistant Assist call failed: %s", exc)
                self._set_assist_stage(
                    "home_assistant",
                    "error",
                    {"wake_word": wake_word, "pipeline": "home_assistant", "reason": str(exc)},
                )
                self._finalize_assist_run(status="error")
                return
            transcript = self._extract_ha_transcript(ha_result)
            if transcript:
                if self._log_transcripts:
                    LOGGER.info("[assistant] Transcript [%s/HA]: %s", wake_word, transcript)
                if self.preference_manager.log_llm_messages:
                    self.publisher._publish_message(
                        self.config.transcript_topic,
                        json.dumps({"text": transcript, "wake_word": wake_word, "pipeline": "home_assistant"}),
                    )
            speech_text = self._extract_ha_speech(ha_result) or "Okay."
            if self._log_transcripts:
                LOGGER.info("[assistant] Response [%s/HA]: %s", wake_word, speech_text)
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
        LOGGER.debug(
            "[assistant] LLM response [%s]: actions=%s, response=%s", wake_word, llm_result.actions, llm_result.response
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
            LOGGER.debug("[assistant] Executed actions [%s]: %s", wake_word, executed_actions)
        if executed_actions:
            self.publisher._publish_message(
                self.config.action_topic,
                json.dumps({"executed": executed_actions, "wake_word": wake_word}),
            )
            await self._maybe_publish_light_overlay(executed_actions)
            if routine_actions:
                self.publisher._publish_routine_overlay()
        response_text, play_tone = select_ha_response(
            self.preferences.ha_response_mode, executed_actions, llm_result.response
        )
        if response_text:
            if self._log_transcripts:
                LOGGER.info("[assistant] Response [%s]: %s", wake_word, response_text)
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
            await self._speak(response_text)
            speech_finished_at = time.monotonic()
            self.conversation_manager.update_last_response_end(speech_finished_at)
            self.media_controller.trigger_media_resume_after_response()
        elif play_tone:
            tracker.begin_stage("speaking")
            stage_extra: dict[str, str | bool] = {"wake_word": wake_word}
            if follow_up:
                stage_extra["follow_up"] = True
            self._set_assist_stage("pulse", "speaking", stage_extra)
            await self._play_ack_tone(self.preferences.ha_tone_sound)
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

    async def _handle_scheduler_notification(self, message: str) -> None:
        pass
        payload = json.dumps({"text": message, "source": "scheduler", "device": self.config.hostname})
        self.publisher._publish_message(self.config.response_topic, payload)
        try:
            await self._speak(message)
        except Exception as exc:
            LOGGER.warning("[assistant] Failed to speak scheduler message: %s", exc)

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

    def _subscribe_schedule_topics(self) -> None:
        try:
            self.mqtt.subscribe(
                self.schedule_commands.command_topic,
                self.schedule_commands.handle_command_message,
            )
        except RuntimeError:
            LOGGER.debug("[assistant] MQTT client not ready for schedule command subscription")

    def _subscribe_playback_topic(self) -> None:
        try:
            self.mqtt.subscribe(self._playback_topic, self._handle_now_playing_message)
        except RuntimeError:
            LOGGER.debug("[assistant] MQTT client not ready for playback telemetry subscription")

    def _subscribe_earmuffs_topic(self) -> None:
        try:
            self.mqtt.subscribe(self._earmuffs_set_topic, self._handle_earmuffs_command)
            # Also subscribe to state topic to restore retained state on startup
            self.mqtt.subscribe(self._earmuffs_state_topic, self._handle_earmuffs_state_restore)
        except RuntimeError as exc:
            LOGGER.debug("[assistant] MQTT client not ready for earmuffs subscription: %s", exc)
        except Exception as exc:
            LOGGER.error("[assistant] Failed to subscribe to earmuffs topic: %s", exc, exc_info=True)

    def _subscribe_alert_topics(self) -> None:
        for topic in self._alert_topics:
            try:
                self.mqtt.subscribe(topic, lambda payload, t=topic: self._handle_alert_message(t, payload))
            except Exception as exc:
                LOGGER.warning("[assistant] Failed to subscribe to alert topic %s: %s", topic, exc)

    def _subscribe_intercom_topic(self) -> None:
        if not self._intercom_topic:
            return
        try:
            self.mqtt.subscribe(self._intercom_topic, self._handle_intercom_message)
        except Exception as exc:
            LOGGER.warning("[assistant] Failed to subscribe to intercom topic %s: %s", self._intercom_topic, exc)

    def _subscribe_kiosk_availability(self) -> None:
        try:
            self.mqtt.subscribe(self._kiosk_availability_topic, self._handle_kiosk_availability)
        except Exception as exc:
            LOGGER.warning(
                "[assistant] Failed to subscribe to kiosk availability topic %s: %s",
                self._kiosk_availability_topic,
                exc,
            )

    def _handle_kiosk_availability(self, payload: str) -> None:
        value = payload.strip().lower()
        self._kiosk_available = value == "online"
        if self._kiosk_available:
            self._last_kiosk_online = time.monotonic()

    def _handle_now_playing_message(self, payload: str) -> None:
        normalized = payload.strip()
        active = bool(normalized)
        changed = self.wake_detector.set_remote_audio_active(active)
        if changed:
            detail = normalized[:80] or "idle"
            LOGGER.debug(
                "[assistant] Self audio playback %s via telemetry (%s)", "active" if active else "idle", detail
            )

    def _handle_earmuffs_state_restore(self, payload: str) -> None:
        """Restore earmuffs state from retained MQTT message on startup."""
        if self._earmuffs_state_restored:
            # Already restored, ignore subsequent messages (they're just state updates)
            return
        value = payload.strip().lower()
        enabled = value in {"on", "true", "1", "yes", "enable", "enabled"}
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
        # command received; act without noisy logging
        if value == "toggle":
            current = self._get_earmuffs_enabled()
            enabled = not current
            # state change handled without status logging
        else:
            enabled = value in {"on", "true", "1", "yes", "enable", "enabled"}
        self._set_earmuffs_enabled(enabled, manual=True)

    def _handle_alert_message(self, topic: str, payload: str) -> None:
        message = payload
        try:
            parsed = json.loads(payload)
            if isinstance(parsed, dict):
                message = str(parsed.get("message") or parsed.get("text") or payload)
        except json.JSONDecodeError:
            pass
        clean = message.strip()
        if not clean:
            return
        self.publisher._publish_info_overlay(text=f"Alert: {clean}", category="alerts")
        self._schedule_info_overlay_clear(8.0)
        if self._loop is None:
            LOGGER.error("[assistant] Cannot handle alert: event loop not initialized")
            return
        self._loop.create_task(self._speak(clean))

    def _handle_intercom_message(self, payload: str) -> None:
        message = payload.strip()
        if not message:
            return
        self.publisher._publish_info_overlay(text=f"Intercom: {message}", category="intercom")
        self._schedule_info_overlay_clear(6.0)
        if self._loop is None:
            LOGGER.error("[assistant] Cannot handle intercom: event loop not initialized")
            return
        self._loop.create_task(self._speak(message))

    def _set_earmuffs_enabled(self, enabled: bool, *, manual: bool = False) -> None:
        changed = False
        with self._earmuffs_lock:
            if enabled != self._earmuffs_enabled:
                self._earmuffs_enabled = enabled
                if manual:
                    self._earmuffs_manual_override = enabled
                changed = True
        if changed:
            self.publisher._publish_earmuffs_state(self._get_earmuffs_enabled())
            if enabled:
                self.wake_detector.mark_wake_context_dirty()

    def _get_earmuffs_enabled(self) -> bool:
        with self._earmuffs_lock:
            return self._earmuffs_enabled

    def _is_earmuffs_manual_override(self) -> bool:
        with self._earmuffs_lock:
            return self._earmuffs_manual_override

    def _handle_schedule_state_changed(self, snapshot: dict[str, Any]) -> None:
        """Delegate to schedule command processor."""
        self.schedule_commands.handle_state_changed(snapshot)
        # Keep local snapshot reference for other components
        self._latest_schedule_snapshot = self.schedule_commands._latest_schedule_snapshot

    def _handle_active_schedule_event(self, event_type: str, payload: dict[str, Any] | None) -> None:
        """Delegate to schedule command processor."""
        self.schedule_commands.handle_active_event(event_type, payload)

    async def _log_activity_event(self, event_type: str, event: dict[str, Any]) -> None:
        if self.home_assistant is None:
            return
        message = self._build_activity_message(event_type, event)
        if not message:
            return
        payload = {
            "name": self.config.device_name,
            "message": message,
        }
        try:
            await self.home_assistant.call_service("logbook", "log", payload)
        except Exception:
            LOGGER.debug("[assistant] Failed to log event to Home Assistant activity log", exc_info=True)

    def _build_activity_message(self, event_type: str, event: dict[str, Any]) -> str | None:
        label = (event.get("label") or "").strip()
        if event_type == "alarm":
            title = label or "Alarm"
            return f"Alarm ringing: {title}"
        if event_type == "timer":
            duration = event.get("duration_seconds")
            timer_label = label or ScheduleShortcutHandler.format_timer_label(duration)
            return f"Timer finished: {timer_label}"
        if event_type == "reminder":
            reminder_meta = event.get("metadata", {}).get("reminder", {})
            reminder_message = ""
            if isinstance(reminder_meta, dict):
                reminder_message = str(reminder_meta.get("message") or "").strip()
            base_label = reminder_message or label or "Reminder"
            calendar_meta = event.get("metadata", {}).get("calendar")
            if isinstance(calendar_meta, dict):
                calendar_name = (calendar_meta.get("calendar_name") or "").strip()
                if calendar_name:
                    return f"Calendar reminder: {base_label} ({calendar_name})"
            return f"Reminder: {base_label}"
        return None

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
        except Exception as exc:
            LOGGER.exception("[assistant] Calendar reminder dispatch failed for %s: %s", label, exc)

    async def _handle_calendar_snapshot(self, reminders: list[CalendarReminder]) -> None:
        unique_reminders = self._deduplicate_calendar_reminders(reminders)
        # Filter out events that have already ended (or started if no end time)
        now = datetime.now().astimezone()
        future_reminders = [reminder for reminder in unique_reminders if (reminder.end or reminder.start) > now]
        ooo_marker = str(getattr(self.config.calendar, "ooo_summary_marker", "OOO") or "OOO").lower()
        ooo_dates: set[str] = set()
        for reminder in future_reminders:
            if reminder.all_day and ooo_marker and ooo_marker in (reminder.summary or "").lower():
                start_date = reminder.start.date()
                if reminder.end:
                    try:
                        # All-day ICS end is typically exclusive; subtract one day.
                        last = reminder.end.date() - timedelta(days=1)
                    except Exception:
                        last = start_date
                else:
                    last = start_date
                if last < start_date:
                    last = start_date
                current = start_date
                while current <= last:
                    ooo_dates.add(current.isoformat())
                    current += timedelta(days=1)
        service = getattr(self, "schedule_service", None)
        if service:
            await service.set_ooo_skip_dates(ooo_dates)
        events = [self._serialize_calendar_event(reminder) for reminder in future_reminders[:CALENDAR_EVENT_INFO_LIMIT]]
        if self.config.calendar.enabled and self.config.calendar.feeds and not events:
            LOGGER.warning(
                "[assistant] Calendar snapshot contained no upcoming events within the lookahead window (now=%s)",
                now.isoformat(),
            )
        self._calendar_events = events
        self._calendar_updated_at = time.time()
        # Update schedule shortcuts handler with new calendar events
        self.schedule_shortcuts.set_calendar_events(events)
        # Update schedule command processor with calendar state
        self.schedule_commands.update_calendar_state(events, self._calendar_updated_at)
        # Always publish schedule state to trigger overlay refresh, even if no schedule snapshot exists yet
        snapshot = self._latest_schedule_snapshot or {}
        self.publisher._publish_schedule_state(snapshot, self._calendar_events, self._calendar_updated_at)

    def _filter_past_calendar_events(self) -> None:
        """Filter out past events from _calendar_events list."""
        now = datetime.now().astimezone()
        filtered = []
        for event in self._calendar_events:
            # Event dict has 'start' and optionally 'end' as ISO strings
            start_str = event.get("start")
            end_str = event.get("end")
            if not start_str:
                continue
            try:
                start_dt = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
                if start_dt.tzinfo is None:
                    start_dt = start_dt.replace(tzinfo=now.tzinfo)
                else:
                    start_dt = start_dt.astimezone(now.tzinfo)
                event_end = start_dt
                if end_str:
                    try:
                        end_dt = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
                        if end_dt.tzinfo is None:
                            end_dt = end_dt.replace(tzinfo=now.tzinfo)
                        else:
                            end_dt = end_dt.astimezone(now.tzinfo)
                        event_end = end_dt
                    except (ValueError, AttributeError):
                        pass
                if event_end > now:
                    filtered.append(event)
            except (ValueError, AttributeError):
                # Keep event if we can't parse the date (better to show than hide)
                filtered.append(event)
        if len(filtered) != len(self._calendar_events):
            self._calendar_events = filtered
            self.schedule_shortcuts.set_calendar_events(filtered)
            self.schedule_commands.update_calendar_state(filtered, self._calendar_updated_at)
            snapshot = self._latest_schedule_snapshot or {}
            self.publisher._publish_schedule_state(snapshot, self._calendar_events, self._calendar_updated_at)

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

    @staticmethod
    def _determine_schedule_file() -> Path:
        override = os.environ.get("PULSE_SCHEDULE_FILE")
        if override:
            return Path(override).expanduser()
        return Path.home() / ".local" / "share" / "pulse" / "schedules.json"

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
        stage_extra: dict[str, str | bool] = {"wake_word": wake_word, "info_category": response.category}
        if follow_up:
            stage_extra["follow_up"] = True
        self._set_assist_stage("pulse", "speaking", stage_extra)
        payload: dict[str, str | bool] = {
            "text": response.text,
            "wake_word": wake_word,
            "info_category": response.category,
        }
        if follow_up:
            payload["follow_up"] = True
        self.publisher._publish_message(self.config.response_topic, json.dumps(payload))
        tag = f"info:{response.category}"
        self._log_assistant_response(tag, response.text, pipeline="pulse")
        overlay_active = False
        overlay_text = response.display or response.text
        overlay_payload = response.card
        estimated_clear_delay = self._estimate_speech_duration(response.text) + self._info_overlay_buffer_seconds
        try:
            if overlay_text or overlay_payload:
                self.publisher._publish_info_overlay(
                    text=overlay_text, category=response.category, extra=overlay_payload
                )
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
        await self._speak(response_text)
        self._trigger_media_resume_after_response()
        return True

    def _is_conversation_stop_command(self, transcript: str | None) -> bool:
        """Check if transcript is a conversation stop command."""
        return self.conversation_manager.is_conversation_stop(transcript)

    def _log_assistant_response(self, wake_word: str, text: str | None, pipeline: str = "pulse") -> None:
        if not self.preference_manager.log_llm_messages or not text:
            return
        _ = text if len(text) <= 240 else f"{text[:237]}..."

    @staticmethod
    def _estimate_speech_duration(text: str) -> float:
        words = max(1, len(text.split()))
        return words / 2.5

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
            LOGGER.debug("[assistant] Music control %s failed for %s: %s", service, entity, exc)
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

    def _build_llm_provider(self) -> LLMProvider:
        """Build LLM provider using current preference overrides."""
        pm = self.preference_manager
        provider = pm.get_active_llm_provider()
        # Apply model overrides for all providers
        llm_config = replace(
            self.config.llm,
            provider=provider,
            openai_model=pm.get_model_override("openai") or self.config.llm.openai_model,
            gemini_model=pm.get_model_override("gemini") or self.config.llm.gemini_model,
            anthropic_model=pm.get_model_override("anthropic") or self.config.llm.anthropic_model,
            groq_model=pm.get_model_override("groq") or self.config.llm.groq_model,
            mistral_model=pm.get_model_override("mistral") or self.config.llm.mistral_model,
            openrouter_model=pm.get_model_override("openrouter") or self.config.llm.openrouter_model,
        )

        # Log which provider and model we're using (helpful for debugging)
        model = getattr(llm_config, f"{provider}_model", "unknown")
        LOGGER.info(f"Using LLM provider: {provider} (model: {model})")

        return build_llm_provider(llm_config, LOGGER)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log-level", default=None)
    args = parser.parse_args()
    requested_level = logging.getLevelName(args.log_level.upper()) if args.log_level else logging.INFO
    resolved_level = requested_level if isinstance(requested_level, int) else logging.INFO
    logging.basicConfig(level=resolved_level)
    # Quiet third-party debug/info noise; problems still surface as warnings/errors.
    logging.getLogger("httpx").setLevel(logging.ERROR)
    logging.getLogger("httpcore").setLevel(logging.ERROR)

    config = AssistantConfig.from_env()
    assistant = PulseAssistant(config)
    # Assistant created; run loop starts below

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _handle_signal(signum: int) -> None:
        # shutdown requested
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
