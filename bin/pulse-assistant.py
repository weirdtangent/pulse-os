#!/usr/bin/env python3
"""Pulse voice assistant daemon."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import math
import signal
import sys
from array import array
from collections.abc import Iterable

from pulse.assistant.actions import ActionEngine, load_action_definitions
from pulse.assistant.audio import AplaySink, ArecordStream
from pulse.assistant.config import AssistantConfig
from pulse.assistant.home_assistant import HomeAssistantClient
from pulse.assistant.llm import LLMProvider, OpenAIProvider
from pulse.assistant.mqtt import AssistantMqtt
from pulse.assistant.scheduler import AssistantScheduler
from wyoming.asr import Transcribe, Transcript
from wyoming.audio import AudioChunk, AudioStart, AudioStop
from wyoming.client import AsyncTcpClient
from wyoming.tts import Synthesize, SynthesizeVoice
from wyoming.wake import Detect, Detection, NotDetected

LOGGER = logging.getLogger("pulse-assistant")


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
        if config.home_assistant.base_url and config.home_assistant.token:
            try:
                self.home_assistant = HomeAssistantClient(config.home_assistant)
            except ValueError as exc:
                LOGGER.warning("Home Assistant config invalid: %s", exc)
        self.scheduler = AssistantScheduler(
            self.home_assistant, config.home_assistant, self._handle_scheduler_notification
        )
        self._shutdown = asyncio.Event()

    async def run(self) -> None:
        self.mqtt.connect()
        await self.mic.start()
        self._publish_state("idle")
        LOGGER.info("Pulse assistant ready (wake words: %s)", ", ".join(self.config.wake_models))
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
                self._publish_state("error", {"wake_word": wake_word, "pipeline": pipeline})
            finally:
                self._publish_state("idle")

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
            await client.write_event(Detect(names=self.config.wake_models).event())
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

    async def _transcribe(self, audio_bytes: bytes) -> str | None:
        client = AsyncTcpClient(self.config.stt_endpoint.host, self.config.stt_endpoint.port)
        await client.connect()
        try:
            await client.write_event(
                Transcribe(
                    name=self.config.stt_endpoint.model,
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
        client = AsyncTcpClient(self.config.tts_endpoint.host, self.config.tts_endpoint.port)
        await client.connect()
        try:
            voice = None
            if self.config.tts_voice:
                voice = SynthesizeVoice(name=self.config.tts_voice)
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

    def _publish_message(self, topic: str, payload: str) -> None:
        self.mqtt.publish(topic, payload=payload, retain=False)

    def _pipeline_for_wake_word(self, wake_word: str) -> str:
        return self.config.wake_routes.get(wake_word, "pulse")

    async def _run_pulse_pipeline(self, wake_word: str) -> None:
        self._publish_state("listening", {"wake_word": wake_word, "pipeline": "pulse"})
        audio_bytes = await self._record_phrase()
        if not audio_bytes:
            LOGGER.debug("No speech captured for wake word %s", wake_word)
            return
        self._publish_state("thinking", {"wake_word": wake_word, "pipeline": "pulse"})
        transcript = await self._transcribe(audio_bytes)
        if not transcript:
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
            self._publish_state("speaking", {"wake_word": wake_word, "pipeline": "pulse"})
            self._publish_message(
                self.config.response_topic,
                json.dumps({"text": llm_result.response, "wake_word": wake_word}),
            )
            await self._speak(llm_result.response)

    async def _run_home_assistant_pipeline(self, wake_word: str) -> None:
        self._publish_state("listening", {"wake_word": wake_word, "pipeline": "home_assistant"})
        ha_config = self.config.home_assistant
        ha_client = self.home_assistant
        if not ha_config.base_url or not ha_config.token:
            LOGGER.warning(
                "Home Assistant pipeline invoked for wake word '%s' but base URL/token are missing",
                wake_word,
            )
            return
        if not ha_client:
            LOGGER.warning("Home Assistant client not initialized; cannot handle wake word '%s'", wake_word)
            return
        LOGGER.info(
            "Wake word '%s' mapped to Home Assistant pipeline at %s (implementation pending)",
            wake_word,
            ha_config.base_url,
        )

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
