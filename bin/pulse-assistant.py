#!/usr/bin/env python3
"""Pulse voice assistant daemon."""

from __future__ import annotations

import argparse
import asyncio
import audioop
import contextlib
import json
import logging
import signal
from collections.abc import Iterable

from pulse.assistant.actions import ActionEngine, load_action_definitions
from pulse.assistant.audio import AplaySink, ArecordStream
from pulse.assistant.config import AssistantConfig
from pulse.assistant.llm import LLMProvider, OpenAIProvider
from pulse.assistant.mqtt import AssistantMqtt
from wyoming.asr import Transcribe, Transcript
from wyoming.audio import AudioChunk, AudioStart, AudioStop
from wyoming.client import AsyncTcpClient
from wyoming.tts import Synthesize, SynthesizeVoice
from wyoming.wake import Detect, Detection, NotDetected

LOGGER = logging.getLogger("pulse-assistant")


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
            self._publish_state("listening", {"wake_word": wake_word})
            audio_bytes = await self._record_phrase()
            if not audio_bytes:
                LOGGER.debug("No speech captured, returning to idle")
                self._publish_state("idle")
                continue
            self._publish_state("thinking")
            transcript = await self._transcribe(audio_bytes)
            if not transcript:
                self._publish_state("idle")
                continue
            LOGGER.info("Transcript: %s", transcript)
            self._publish_message(self.config.transcript_topic, json.dumps({"text": transcript}))
            prompt_actions = self.actions.describe_for_prompt()
            llm_result = await self.llm.generate(transcript, prompt_actions)
            LOGGER.debug("LLM response: %s", llm_result)
            executed_actions = self.actions.execute(llm_result.actions, self.mqtt if llm_result.actions else None)
            if executed_actions:
                self._publish_message(
                    self.config.action_topic,
                    json.dumps({"executed": executed_actions}),
                )
            if llm_result.response:
                self._publish_state("speaking")
                self._publish_message(
                    self.config.response_topic,
                    json.dumps({"text": llm_result.response}),
                )
                await self._speak(llm_result.response)
            self._publish_state("idle")

    async def shutdown(self) -> None:
        self._shutdown.set()
        await self.mic.stop()
        self.mqtt.disconnect()
        await self.player.stop()

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
        min_chunks = int(
            max(1, (self.config.phrase.min_seconds * 1000) / self.config.mic.chunk_ms)
        )
        max_chunks = int(
            max(1, (self.config.phrase.max_seconds * 1000) / self.config.mic.chunk_ms)
        )
        silence_chunks = int(
            max(1, self.config.phrase.silence_ms / self.config.mic.chunk_ms)
        )
        buffer = bytearray()
        silence_run = 0
        chunks = 0
        while chunks < max_chunks:
            chunk = await self.mic.read_chunk()
            buffer.extend(chunk)
            rms = audioop.rms(chunk, self.config.mic.width)
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


