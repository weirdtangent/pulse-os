#!/usr/bin/env python3
"""Pulse voice assistant daemon."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import os
import signal
import time
from pathlib import Path
from typing import Any

from pulse.assistant.actions import ActionEngine, load_action_definitions
from pulse.assistant.audio import AplaySink, ArecordStream
from pulse.assistant.calendar_manager import CalendarEventManager
from pulse.assistant.calendar_sync import CalendarSyncService
from pulse.assistant.config import AssistantConfig, AssistantPreferences
from pulse.assistant.conversation_manager import ConversationManager, build_conversation_stop_prefixes
from pulse.assistant.earmuffs import EarmuffsManager
from pulse.assistant.event_handlers import EventHandlerManager
from pulse.assistant.home_assistant import HomeAssistantClient
from pulse.assistant.info_query_handler import InfoQueryHandler
from pulse.assistant.info_service import InfoService
from pulse.assistant.llm import SUPPORTED_PROVIDERS, LLMProvider, build_llm_provider_with_overrides
from pulse.assistant.media_controller import MediaController
from pulse.assistant.mqtt import AssistantMqtt
from pulse.assistant.mqtt_publisher import AssistantMqttPublisher
from pulse.assistant.music_handler import MusicCommandHandler
from pulse.assistant.pipeline_orchestrator import PipelineOrchestrator
from pulse.assistant.preference_manager import PreferenceManager
from pulse.assistant.routines import RoutineEngine, default_routines
from pulse.assistant.schedule_commands import ScheduleCommandProcessor
from pulse.assistant.schedule_intents import ScheduleIntentParser
from pulse.assistant.schedule_service import ScheduleService
from pulse.assistant.schedule_shortcuts import ScheduleShortcutHandler
from pulse.assistant.scheduler import AssistantScheduler
from pulse.assistant.wake_detector import WakeDetector, compute_rms
from pulse.sound_library import SoundLibrary

LOGGER = logging.getLogger("pulse-assistant")


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
        self._sound_library = SoundLibrary(custom_dir=self.config.sounds.custom_dir)

        self.publisher = AssistantMqttPublisher(
            mqtt=self.mqtt,
            config=self.config,
            home_assistant=self.home_assistant,
            schedule_service=self.schedule_service,
            sound_library=self._sound_library,
            logger=LOGGER,
        )

        self.preference_manager = PreferenceManager(
            mqtt=self.mqtt,
            config=self.config,
            sound_library=self._sound_library,
            publisher=self.publisher,
            logger=LOGGER,
        )

        self.schedule_intents = ScheduleIntentParser()

        self.schedule_shortcuts = ScheduleShortcutHandler(
            schedule_service=self.schedule_service,
            schedule_intents=self.schedule_intents,
            publisher=self.publisher,
            config=self.config,
            logger=LOGGER,
        )

        self.schedule_commands = ScheduleCommandProcessor(
            schedule_service=self.schedule_service,
            publisher=self.publisher,
            base_topic=self.config.mqtt.topic_base,
            logger=LOGGER,
        )
        self.schedule_commands.set_log_activity_callback(self._log_activity_event)

        self._latest_schedule_snapshot: dict[str, Any] | None = None

        self.calendar_manager = CalendarEventManager(
            schedule_service=self.schedule_service,
            ooo_summary_marker=getattr(self.config.calendar, "ooo_summary_marker", "OOO"),
            calendar_enabled=self.config.calendar.enabled,
            calendar_has_feeds=bool(self.config.calendar.feeds),
            logger=LOGGER,
        )
        self.calendar_manager.set_events_changed_callback(self._on_calendar_events_changed)

        self.calendar_sync: CalendarSyncService | None = None
        if self.config.calendar.enabled:
            if self.config.calendar.feeds:
                self.calendar_sync = CalendarSyncService(
                    config=self.config.calendar,
                    trigger_callback=self.calendar_manager.trigger_calendar_reminder,
                    snapshot_callback=self.calendar_manager.handle_calendar_snapshot,
                    logger=logging.getLogger("pulse.calendar_sync"),
                )
            else:
                LOGGER.warning(
                    "[assistant] Calendar sync enabled but no feeds configured (PULSE_CALENDAR_ICS_URLS is empty)"
                )

        self._shutdown = asyncio.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._heartbeat_topic = f"{self.config.mqtt.topic_base}/assistant/heartbeat"
        self._self_audio_trigger_level = max(2, self.config.self_audio_trigger_level)

        self.wake_detector = WakeDetector(
            config=self.config,
            preferences=self.preferences,
            mic=self.mic,
            self_audio_trigger_level=self._self_audio_trigger_level,
        )
        self.media_controller = MediaController(
            home_assistant=self.home_assistant,
            media_player_entity=self.config.media_player_entity,
            additional_entities=list(self.config.media_player_entities),
            loop=None,  # Will be set in run()
        )
        self.conversation_manager = ConversationManager(
            config=self.config,
            mic=self.mic,
            compute_rms=compute_rms,
            last_response_end=None,
        )

        self.preference_manager.log_llm_messages = config.log_llm_messages
        self._conversation_stop_prefixes = build_conversation_stop_prefixes(config)
        self._last_health_signature: tuple[tuple[str, str], ...] | None = None

        self.music_handler = MusicCommandHandler(
            home_assistant=self.home_assistant,
            media_controller=self.media_controller,
            media_player_entity=self.config.media_player_entity,
            logger=LOGGER,
        )

        self.info_query_handler = InfoQueryHandler(
            info_service=self.info_service,
            publisher=self.publisher,
            media_controller=self.media_controller,
            response_topic=self.config.response_topic,
            logger=LOGGER,
        )

        self.earmuffs = EarmuffsManager(
            mqtt=self.mqtt,
            publisher=self.publisher,
            base_topic=self.config.mqtt.topic_base,
            logger=LOGGER,
        )

        self.event_handlers = EventHandlerManager(
            mqtt=self.mqtt,
            publisher=self.publisher,
            wake_detector=self.wake_detector,
            alert_topics=self.config.alert_topics,
            intercom_topic=self.config.intercom_topic,
            playback_topic=f"pulse/{self.config.hostname}/telemetry/now_playing",
            kiosk_availability_topic=f"homeassistant/device/{self.config.hostname}/availability",
            logger=LOGGER,
        )

        self.orchestrator = PipelineOrchestrator(
            config=self.config,
            mqtt=self.mqtt,
            publisher=self.publisher,
            preference_manager=self.preference_manager,
            conversation_manager=self.conversation_manager,
            wake_detector=self.wake_detector,
            media_controller=self.media_controller,
            music_handler=self.music_handler,
            schedule_shortcuts=self.schedule_shortcuts,
            info_query_handler=self.info_query_handler,
            schedule_service=self.schedule_service,
            actions=self.actions,
            routines=self.routines,
            home_assistant=self.home_assistant,
            scheduler=self.scheduler,
            player=self.player,
            sound_library=self._sound_library,
            logger=LOGGER,
        )

        # Wire callbacks through orchestrator
        self.schedule_shortcuts.set_speak_callback(self.orchestrator.speak)
        self.schedule_shortcuts.set_log_response_callback(self.orchestrator._log_assistant_response)
        self.music_handler.set_speak_callback(self.orchestrator.speak)
        self.music_handler.set_log_response_callback(self.orchestrator._log_assistant_response)
        self.info_query_handler.set_speak_callback(self.orchestrator.speak)
        self.info_query_handler.set_log_response_callback(self.orchestrator._log_assistant_response)
        self.info_query_handler.set_tracker_provider(lambda: self.orchestrator.current_tracker)
        self.info_query_handler.set_stage_callback(self.orchestrator._set_assist_stage)
        self.event_handlers.set_speak_callback(self.orchestrator.speak)

        # Wire orchestrator getters
        self.orchestrator.set_llm_provider_getter(lambda: self.llm)
        self.orchestrator.set_preferences_getter(lambda: self.preferences)

        # Preference manager callbacks
        self.preference_manager.set_wake_sensitivity_callback(self.wake_detector.mark_wake_context_dirty)
        self.preference_manager.set_llm_provider_callback(self._rebuild_llm_provider)
        self.preference_manager.set_sound_settings_callback(self.schedule_service.update_sound_settings)
        self.preference_manager.set_config_updated_callback(self._handle_config_updated)
        self.earmuffs.set_wake_context_dirty_callback(self.wake_detector.mark_wake_context_dirty)

        # Build LLM provider
        self.llm: LLMProvider = self._build_llm_provider()

    def _handle_config_updated(self, new_config: AssistantConfig) -> None:
        self.config = new_config

    @property
    def preferences(self) -> AssistantPreferences:
        return self.preference_manager.preferences

    @preferences.setter
    def preferences(self, value: AssistantPreferences) -> None:
        self.preference_manager.preferences = value

    def _rebuild_llm_provider(self) -> LLMProvider:
        self.llm = self._build_llm_provider()
        return self.llm

    async def run(self) -> None:
        try:
            self._loop = asyncio.get_running_loop()
            self.media_controller._loop = self._loop
            self.schedule_commands.set_event_loop(self._loop)
            self.event_handlers.set_event_loop(self._loop)
            self.mqtt.connect()
            self.preference_manager.subscribe_preference_topics()
            self._subscribe_schedule_topics()
            self.event_handlers.subscribe_all()
            self.earmuffs.subscribe()

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
                self.publisher._publish_schedule_state(
                    {},
                    self.calendar_manager.calendar_events,
                    self.calendar_manager.calendar_updated_at,
                )
            else:
                LOGGER.warning("[assistant] calendar_sync is None, cannot start calendar sync service")

            self.publisher._publish_preferences(
                self.preferences,
                self.preference_manager.log_llm_messages,
                self.preference_manager.get_active_ha_pipeline(),
                self.preference_manager.get_active_llm_provider(),
                self.config.sounds,
            )
            await asyncio.sleep(0.5)
            if not self.earmuffs.state_restored:
                try:
                    self.publisher._publish_earmuffs_state(self.earmuffs.enabled)
                except Exception as exc:
                    LOGGER.exception("[assistant] Failed to publish earmuffs state: %s", exc)

            self.publisher._publish_assistant_discovery(self.config.hostname, self.config.device_name)
            self.publisher._publish_routine_overlay()
            await self.mic.start()
            self.orchestrator._set_assist_stage("pulse", "idle")
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
            from pulse.systemd_notify import ready as sd_ready

            sd_ready()
        except Exception as exc:
            LOGGER.exception("[assistant] Fatal error in assistant.run(): %s", exc)
            raise
        while not self._shutdown.is_set():
            wake_word = await self.wake_detector.wait_for_wake_word(self._shutdown, self.earmuffs.get_enabled)
            if wake_word is None:
                continue
            pipeline = self._pipeline_for_wake_word(wake_word)
            try:
                if pipeline == "home_assistant":
                    await self.orchestrator.run_home_assistant_pipeline(wake_word)
                else:
                    await self.orchestrator.run_pulse_pipeline(wake_word)
            except Exception as exc:
                LOGGER.exception("[assistant] Pipeline %s failed for wake word %s: %s", pipeline, wake_word, exc)
                self.orchestrator._set_assist_stage(pipeline, "error", {"wake_word": wake_word, "error": str(exc)})
                self.orchestrator._finalize_assist_run(status="error")

    async def _heartbeat_loop(self) -> None:
        from pulse.systemd_notify import watchdog as sd_watchdog

        while not self._shutdown.is_set():
            self.publisher._publish_message(self._heartbeat_topic, str(int(time.time())))
            sd_watchdog()
            await self.event_handlers.check_kiosk_health()
            await asyncio.sleep(30)

    async def shutdown(self) -> None:
        self._shutdown.set()
        heartbeat = getattr(self, "_heartbeat_task", None)
        if heartbeat:
            heartbeat.cancel()
            try:
                await heartbeat
            except asyncio.CancelledError:
                pass  # expected when cancelling the heartbeat task
        if self.calendar_sync:
            await self.calendar_sync.stop()
        await self.mic.stop()
        await self.schedule_service.stop()
        self.mqtt.disconnect()
        await self.player.stop()
        self.media_controller.cancel_media_resume_task()
        if self.home_assistant:
            await self.home_assistant.close()

    def _pipeline_for_wake_word(self, wake_word: str) -> str:
        return self.config.wake_routes.get(wake_word, "pulse")

    def _subscribe_schedule_topics(self) -> None:
        try:
            self.mqtt.subscribe(
                self.schedule_commands.command_topic,
                self.schedule_commands.handle_command_message,
            )
        except RuntimeError:
            LOGGER.debug("[assistant] MQTT client not ready for schedule command subscription")

    def _handle_schedule_state_changed(self, snapshot: dict[str, Any]) -> None:
        self.schedule_commands.handle_state_changed(snapshot)
        self._latest_schedule_snapshot = self.schedule_commands._latest_schedule_snapshot

    def _handle_active_schedule_event(self, event_type: str, payload: dict[str, Any] | None) -> None:
        self.schedule_commands.handle_active_event(event_type, payload)

    async def _handle_scheduler_notification(self, message: str) -> None:
        payload = json.dumps({"text": message, "source": "scheduler", "device": self.config.hostname})
        self.publisher._publish_message(self.config.response_topic, payload)
        try:
            await self.orchestrator.speak(message)
        except Exception as exc:
            LOGGER.warning("[assistant] Failed to speak scheduler message: %s", exc)

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

    def _on_calendar_events_changed(self, events: list[dict[str, Any]], updated_at: float | None) -> None:
        self.schedule_shortcuts.set_calendar_events(events)
        self.schedule_commands.update_calendar_state(events, updated_at)
        snapshot = self._latest_schedule_snapshot or {}
        self.publisher._publish_schedule_state(snapshot, events, updated_at)

    @staticmethod
    def _determine_schedule_file() -> Path:
        override = os.environ.get("PULSE_SCHEDULE_FILE")
        if override:
            return Path(override).expanduser()
        return Path.home() / ".local" / "share" / "pulse" / "schedules.json"

    def _build_llm_provider(self) -> LLMProvider:
        pm = self.preference_manager
        overrides = {p: pm.get_model_override(p) for p in SUPPORTED_PROVIDERS}
        return build_llm_provider_with_overrides(self.config.llm, pm.get_active_llm_provider(), overrides, LOGGER)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log-level", default=None)
    args = parser.parse_args()
    requested_level = logging.getLevelName(args.log_level.upper()) if args.log_level else logging.INFO
    resolved_level = requested_level if isinstance(requested_level, int) else logging.INFO
    logging.basicConfig(level=resolved_level)
    logging.getLogger("httpx").setLevel(logging.ERROR)
    logging.getLogger("httpcore").setLevel(logging.ERROR)

    config = AssistantConfig.from_env()
    assistant = PulseAssistant(config)

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _handle_signal(signum: int) -> None:
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
