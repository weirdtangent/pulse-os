#!/usr/bin/env python3
"""Pulse voice assistant daemon."""

from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
import json
import logging
import math
import signal
import sys
import time
from array import array
from collections.abc import Iterable
from dataclasses import dataclass, field, replace

from pulse.assistant.actions import ActionEngine, load_action_definitions
from pulse.assistant.audio import AplaySink, ArecordStream
from pulse.assistant.config import AssistantConfig, WyomingEndpoint
from pulse.assistant.home_assistant import HomeAssistantClient, HomeAssistantError
from pulse.assistant.llm import LLMProvider, OpenAIProvider
from pulse.assistant.mqtt import AssistantMqtt
from pulse.assistant.scheduler import AssistantScheduler
from pulse.audio import play_volume_feedback
from wyoming.asr import Transcribe, Transcript
from wyoming.audio import AudioChunk, AudioStart, AudioStop
from wyoming.client import AsyncTcpClient
from wyoming.tts import Synthesize, SynthesizeVoice
from wyoming.wake import Detect, Detection, NotDetected

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


def _compute_rms(chunk: bytes, sample_width: int) -> int:
    if not chunk or sample_width <= 0:
        return 0
    frames = len(chunk) // sample_width
    if frames <= 0:
        return 0
    trimmed = chunk[: frames * sample_width]
    typecode = {1: "b", 2: "h", 4: "i"}.get(sample_width)
    if typecode:
        samples = array(typecode)
        samples.frombytes(trimmed)
        if sample_width > 1 and sys.byteorder != "little":
            samples.byteswap()
        total = math.fsum(value * value for value in samples)
    else:
        total = 0.0
        for i in range(0, len(trimmed), sample_width):
            sample = int.from_bytes(trimmed[i : i + sample_width], "little", signed=True)
            total += sample * sample
    mean = total / frames
    return int(math.sqrt(mean))


class PulseAssistant:
    def __init__(self, config: AssistantConfig) -> None:
        self.config = config
        mic_bytes = config.mic.bytes_per_chunk
        self.mic = ArecordStream(config.mic.command, mic_bytes, LOGGER)
        self.player = AplaySink(logger=LOGGER)
        self.mqtt = AssistantMqtt(config.mqtt, logger=LOGGER)
        action_defs = load_action_definitions(config.action_file, config.inline_actions)
        self.actions = ActionEngine(action_defs)
        self.llm: LLMProvider = OpenAIProvider(config.llm, LOGGER)
        self.home_assistant: HomeAssistantClient | None = None
        self.preferences = config.preferences
        if config.home_assistant.base_url and config.home_assistant.token:
            try:
                self.home_assistant = HomeAssistantClient(config.home_assistant)
            except ValueError as exc:
                LOGGER.warning("Home Assistant config invalid: %s", exc)
        self.scheduler = AssistantScheduler(
            self.home_assistant, config.home_assistant, self._handle_scheduler_notification
        )
        self._shutdown = asyncio.Event()
        base_topic = self.config.mqtt.topic_base
        self._assist_in_progress_topic = f"{base_topic}/assistant/in_progress"
        self._assist_metrics_topic = f"{base_topic}/assistant/metrics"
        self._assist_stage_topic = f"{base_topic}/assistant/stage"
        self._assist_pipeline_topic = f"{base_topic}/assistant/active_pipeline"
        self._assist_wake_topic = f"{base_topic}/assistant/last_wake_word"
        self._preferences_topic = f"{base_topic}/preferences"
        self._assist_stage = "idle"
        self._assist_pipeline: str | None = None
        self._current_tracker: AssistRunTracker | None = None
        self._ha_pipeline_override: str | None = None

    async def run(self) -> None:
        self.mqtt.connect()
        self._subscribe_preference_topics()
        self._publish_preferences()
        self._publish_assistant_discovery()
        await self.mic.start()
        self._set_assist_stage("pulse", "idle")
        friendly_words = ", ".join(self._display_wake_word(word) for word in self.config.wake_models)
        LOGGER.info("Pulse assistant ready (wake words: %s)", friendly_words)
        while not self._shutdown.is_set():
            wake_word = await self._wait_for_wake_word()
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
        await self.mic.stop()
        self.mqtt.disconnect()
        await self.player.stop()
        if self.home_assistant:
            await self.home_assistant.close()

    async def _wait_for_wake_word(self) -> str | None:
        client = AsyncTcpClient(self.config.wake_endpoint.host, self.config.wake_endpoint.port)
        await client.connect()
        timestamp = 0
        detection_task: asyncio.Task[str | None] | None = None
        try:
            detect_context = self._context_for_detect()
            detect_message = Detect(names=self.config.wake_models, context=detect_context or None)
            await client.write_event(detect_message.event())
            await client.write_event(
                AudioStart(
                    rate=self.config.mic.rate,
                    width=self.config.mic.width,
                    channels=self.config.mic.channels,
                    timestamp=0,
                ).event()
            )
            detection_task = asyncio.create_task(self._read_wake_events(client))
            while not detection_task.done():
                chunk_bytes = await self.mic.read_chunk()
                chunk_event = AudioChunk(
                    rate=self.config.mic.rate,
                    width=self.config.mic.width,
                    channels=self.config.mic.channels,
                    audio=chunk_bytes,
                    timestamp=timestamp,
                )
                await client.write_event(chunk_event.event())
                timestamp += self.config.mic.chunk_ms
            if detection_task.done():
                detection = detection_task.result()
                return detection
            return None
        finally:
            await client.write_event(AudioStop(timestamp=timestamp).event())
            if detection_task:
                detection_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await detection_task
            await client.disconnect()

    async def _read_wake_events(self, client: AsyncTcpClient) -> str | None:
        while True:
            event = await client.read_event()
            if event is None:
                return None
            if Detection.is_type(event.type):
                detection = Detection.from_event(event)
                return detection.name or self.config.wake_models[0]
            if NotDetected.is_type(event.type):
                return None

    async def _record_phrase(self) -> bytes | None:
        min_chunks = int(max(1, (self.config.phrase.min_seconds * 1000) / self.config.mic.chunk_ms))
        max_chunks = int(max(1, (self.config.phrase.max_seconds * 1000) / self.config.mic.chunk_ms))
        silence_chunks = int(max(1, self.config.phrase.silence_ms / self.config.mic.chunk_ms))
        buffer = bytearray()
        silence_run = 0
        chunks = 0
        while chunks < max_chunks:
            chunk = await self.mic.read_chunk()
            buffer.extend(chunk)
            rms = _compute_rms(chunk, self.config.mic.width)
            if rms < self.config.phrase.rms_floor and chunks >= min_chunks:
                silence_run += 1
                if silence_run >= silence_chunks:
                    break
            else:
                silence_run = 0
            chunks += 1
        return bytes(buffer) if buffer else None

    async def _transcribe(self, audio_bytes: bytes, endpoint: WyomingEndpoint | None = None) -> str | None:
        target = endpoint or self.config.stt_endpoint
        if not target:
            LOGGER.warning("No STT endpoint configured")
            return None
        client = AsyncTcpClient(target.host, target.port)
        await client.connect()
        try:
            await client.write_event(
                Transcribe(
                    name=target.model,
                    language=self.config.language,
                ).event()
            )
            await client.write_event(
                AudioStart(
                    rate=self.config.mic.rate,
                    width=self.config.mic.width,
                    channels=self.config.mic.channels,
                ).event()
            )
            for chunk in _chunk_bytes(audio_bytes, self.config.mic.bytes_per_chunk):
                await client.write_event(
                    AudioChunk(
                        rate=self.config.mic.rate,
                        width=self.config.mic.width,
                        channels=self.config.mic.channels,
                        audio=chunk,
                    ).event()
                )
            await client.write_event(AudioStop().event())
            return await self._read_transcript_event(client)
        finally:
            await client.disconnect()

    async def _read_transcript_event(self, client: AsyncTcpClient) -> str | None:
        while True:
            event = await client.read_event()
            if event is None:
                return None
            if Transcript.is_type(event.type):
                transcript = Transcript.from_event(event)
                return transcript.text

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
        client = AsyncTcpClient(target.host, target.port)
        await client.connect()
        try:
            voice = None
            if voice_name:
                voice = SynthesizeVoice(name=voice_name)
            await client.write_event(Synthesize(text=text, voice=voice).event())
            await self._consume_tts_audio(client)
        finally:
            await client.disconnect()

    async def _consume_tts_audio(self, client: AsyncTcpClient) -> None:
        started = False
        while True:
            event = await client.read_event()
            if event is None:
                break
            if AudioStart.is_type(event.type):
                audio_start = AudioStart.from_event(event)
                await self.player.start(audio_start.rate, audio_start.width, audio_start.channels)
                started = True
            elif AudioChunk.is_type(event.type):
                chunk = AudioChunk.from_event(event)
                await self.player.write(chunk.audio)
            elif AudioStop.is_type(event.type):
                break
        if started:
            await self.player.stop()

    def _publish_state(self, state: str, extra: dict | None = None) -> None:
        payload = {"state": state}
        if extra:
            payload.update(extra)
        payload["device"] = self.config.hostname
        self._publish_message(self.config.state_topic, json.dumps(payload))

    def _publish_message(self, topic: str, payload: str, *, retain: bool = False) -> None:
        self.mqtt.publish(topic, payload=payload, retain=retain)

    def _pipeline_for_wake_word(self, wake_word: str) -> str:
        return self.config.wake_routes.get(wake_word, "pulse")

    @staticmethod
    def _display_wake_word(name: str) -> str:
        return name.replace("_", " ").strip()

    async def _maybe_play_wake_sound(self) -> None:
        if not self.preferences.wake_sound:
            return
        try:
            await asyncio.to_thread(play_volume_feedback)
        except Exception:  # pylint: disable=broad-except
            LOGGER.debug("Wake sound playback failed", exc_info=True)

    async def _run_pulse_pipeline(self, wake_word: str) -> None:
        tracker = AssistRunTracker("pulse", wake_word)
        tracker.begin_stage("listening")
        self._current_tracker = tracker
        self._set_assist_stage("pulse", "listening", {"wake_word": wake_word})
        await self._maybe_play_wake_sound()
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
        LOGGER.info("Transcript (%s): %s", wake_word, transcript)
        self._publish_message(self.config.transcript_topic, json.dumps({"text": transcript, "wake_word": wake_word}))
        prompt_actions = self.actions.describe_for_prompt() + self._home_assistant_prompt_actions()
        llm_result = await self.llm.generate(transcript, prompt_actions)
        LOGGER.debug("LLM response: %s", llm_result)
        executed_actions = await self.actions.execute(
            llm_result.actions,
            self.mqtt if llm_result.actions else None,
            self.home_assistant,
            self.scheduler,
        )
        if executed_actions:
            self._publish_message(
                self.config.action_topic,
                json.dumps({"executed": executed_actions, "wake_word": wake_word}),
            )
        if llm_result.response:
            tracker.begin_stage("speaking")
            self._set_assist_stage("pulse", "speaking", {"wake_word": wake_word})
            self._publish_message(
                self.config.response_topic,
                json.dumps({"text": llm_result.response, "wake_word": wake_word}),
            )
            await self._speak(llm_result.response)
        self._finalize_assist_run(status="success")

    async def _run_home_assistant_pipeline(self, wake_word: str) -> None:
        tracker = AssistRunTracker("home_assistant", wake_word)
        tracker.begin_stage("listening")
        self._current_tracker = tracker
        self._set_assist_stage("home_assistant", "listening", {"wake_word": wake_word})
        await self._maybe_play_wake_sound()
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
        tts_audio = self._extract_ha_tts_audio(ha_result)
        if tts_audio:
            await self._play_pcm_audio(tts_audio["audio"], tts_audio["rate"], tts_audio["width"], tts_audio["channels"])
        else:
            tts_endpoint = ha_config.tts_endpoint or self.config.tts_endpoint
            await self._speak_via_endpoint(speech_text, tts_endpoint, self.config.tts_voice)
        self._finalize_assist_run(status="success")

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
        except RuntimeError:
            LOGGER.debug("MQTT client not ready for preference subscriptions")

    def _handle_wake_sound_command(self, payload: str) -> None:
        value = payload.strip().lower()
        enabled = value in {"on", "true", "1", "yes"}
        self.preferences = replace(self.preferences, wake_sound=enabled)
        self._publish_preference_state("wake_sound", "on" if enabled else "off")

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
        self.preferences = replace(self.preferences, wake_sensitivity=value)  # type: ignore[arg-type]
        self._publish_preference_state("wake_sensitivity", value)

    def _publish_preferences(self) -> None:
        self._publish_preference_state("wake_sound", "on" if self.preferences.wake_sound else "off")
        self._publish_preference_state("speaking_style", self.preferences.speaking_style)
        self._publish_preference_state("wake_sensitivity", self.preferences.wake_sensitivity)
        self._publish_preference_state("ha_pipeline", self._active_ha_pipeline() or "")

    def _publish_preference_state(self, key: str, value: str) -> None:
        topic = f"{self._preferences_topic}/{key}/state"
        self._publish_message(topic, value, retain=True)

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

    def _handle_ha_pipeline_command(self, payload: str) -> None:
        value = payload.strip()
        self._ha_pipeline_override = value or None
        self._publish_preference_state("ha_pipeline", self._active_ha_pipeline() or "")

    def _active_ha_pipeline(self) -> str | None:
        return self._ha_pipeline_override or self.config.home_assistant.assist_pipeline

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

    def _context_for_detect(self) -> dict[str, int] | None:
        sensitivity = self.preferences.wake_sensitivity
        if sensitivity == "normal":
            return None
        trigger_level_map = {
            "low": 5,
            "high": 2,
        }
        trigger_level = trigger_level_map.get(sensitivity)
        if trigger_level is None:
            return None
        return {"trigger_level": trigger_level}


def _chunk_bytes(data: bytes, size: int) -> Iterable[bytes]:
    for start in range(0, len(data), size):
        end = min(start + size, len(data))
        yield data[start:end]


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
