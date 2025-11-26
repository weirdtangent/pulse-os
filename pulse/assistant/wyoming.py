"""Shared helpers for interacting with Wyoming STT/TTS/wake services."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Sequence
from contextlib import AbstractAsyncContextManager

from wyoming.asr import Transcribe, Transcript
from wyoming.audio import AudioChunk, AudioStart, AudioStop
from wyoming.client import AsyncTcpClient
from wyoming.tts import Synthesize, SynthesizeVoice
from wyoming.wake import Detect, Detection, NotDetected

from pulse.utils import await_with_timeout, chunk_bytes

from .audio import AplaySink
from .config import MicConfig, WyomingEndpoint

LoggerLike = logging.Logger | None


async def transcribe_audio(
    audio_bytes: bytes,
    *,
    endpoint: WyomingEndpoint,
    mic: MicConfig,
    language: str | None = None,
    model: str | None = None,
    timeout: float | None = None,
    logger: LoggerLike = None,
) -> str | None:
    """Send PCM audio to a Wyoming STT endpoint and return the transcript text."""

    client = AsyncTcpClient(endpoint.host, endpoint.port)
    await await_with_timeout(client.connect(), timeout)
    requested_model = model or endpoint.model
    try:
        await await_with_timeout(
            client.write_event(
                Transcribe(
                    name=requested_model,
                    language=language,
                ).event()
            ),
            timeout,
        )
        await await_with_timeout(
            client.write_event(
                AudioStart(
                    rate=mic.rate,
                    width=mic.width,
                    channels=mic.channels,
                ).event()
            ),
            timeout,
        )
        for chunk in chunk_bytes(audio_bytes, mic.bytes_per_chunk):
            await await_with_timeout(
                client.write_event(
                    AudioChunk(
                        rate=mic.rate,
                        width=mic.width,
                        channels=mic.channels,
                        audio=chunk,
                    ).event()
                ),
                timeout,
            )
        await await_with_timeout(client.write_event(AudioStop().event()), timeout)
        while True:
            event = await await_with_timeout(client.read_event(), timeout)
            if event is None:
                if logger:
                    logger.debug("Wyoming STT connection closed before transcript returned")
                return None
            if Transcript.is_type(event.type):
                transcript = Transcript.from_event(event)
                return transcript.text
    finally:
        await client.disconnect()


async def play_tts_stream(
    text: str,
    *,
    endpoint: WyomingEndpoint,
    sink: AplaySink,
    voice_name: str | None = None,
    audio_guard: AbstractAsyncContextManager[None] | None = None,
    timeout: float | None = None,
    logger: LoggerLike = None,
) -> None:
    """Synthesize speech via Wyoming TTS and stream it directly to the provided sink."""

    async def _play() -> None:
        started = False
        try:
            async for event in _tts_event_stream(
                text,
                endpoint=endpoint,
                voice_name=voice_name,
                timeout=timeout,
            ):
                if AudioStart.is_type(event.type):
                    audio_start = AudioStart.from_event(event)
                    await sink.start(audio_start.rate, audio_start.width, audio_start.channels)
                    started = True
                elif AudioChunk.is_type(event.type):
                    chunk = AudioChunk.from_event(event)
                    await sink.write(chunk.audio)
                elif AudioStop.is_type(event.type):
                    break
        finally:
            if started:
                await sink.stop()

    if audio_guard is None:
        await _play()
    else:
        async with audio_guard:
            await _play()


async def probe_synthesize(
    *,
    endpoint: WyomingEndpoint,
    text: str,
    timeout: float | None = None,
) -> tuple[bool, int]:
    """Synthesize speech and report whether audio started plus how many chunks arrived."""

    started = False
    chunks = 0
    async for event in _tts_event_stream(text, endpoint=endpoint, timeout=timeout):
        if AudioStart.is_type(event.type):
            started = True
        elif AudioChunk.is_type(event.type):
            chunks += 1
        elif AudioStop.is_type(event.type):
            break
    return started, chunks


async def probe_wake_detection(
    *,
    endpoint: WyomingEndpoint,
    mic: MicConfig,
    models: Sequence[str],
    audio: bytes | None = None,
    timeout: float | None = None,
) -> str | None:
    """Send a short audio sample to Wyoming OpenWakeWord and return detection info."""

    client = AsyncTcpClient(endpoint.host, endpoint.port)
    await await_with_timeout(client.connect(), timeout)
    timestamp = 0
    sample = audio or silence_bytes(mic.chunk_ms, mic)
    try:
        await await_with_timeout(client.write_event(Detect(names=list(models)).event()), timeout)
        await await_with_timeout(
            client.write_event(
                AudioStart(
                    rate=mic.rate,
                    width=mic.width,
                    channels=mic.channels,
                    timestamp=timestamp,
                ).event()
            ),
            timeout,
        )
        await await_with_timeout(
            client.write_event(
                AudioChunk(
                    rate=mic.rate,
                    width=mic.width,
                    channels=mic.channels,
                    audio=sample,
                    timestamp=timestamp,
                ).event()
            ),
            timeout,
        )
        timestamp += mic.chunk_ms
        await await_with_timeout(client.write_event(AudioStop(timestamp=timestamp).event()), timeout)
        while True:
            event = await await_with_timeout(client.read_event(), timeout)
            if event is None:
                return None
            if Detection.is_type(event.type):
                detection = Detection.from_event(event)
                return detection.name or (models[0] if models else None)
            if NotDetected.is_type(event.type):
                return None
    finally:
        await client.disconnect()


def silence_bytes(duration_ms: int, mic: MicConfig) -> bytes:
    """Generate a silence buffer matching the given mic configuration."""

    frames = int(mic.rate * (duration_ms / 1000))
    frame_bytes = mic.width * mic.channels
    total_bytes = max(1, frames * frame_bytes)
    return bytes(total_bytes)


async def _tts_event_stream(
    text: str,
    *,
    endpoint: WyomingEndpoint,
    voice_name: str | None = None,
    timeout: float | None = None,
) -> AsyncIterator[object]:
    client = AsyncTcpClient(endpoint.host, endpoint.port)
    await await_with_timeout(client.connect(), timeout)
    voice = SynthesizeVoice(name=voice_name) if voice_name else None
    await await_with_timeout(client.write_event(Synthesize(text=text, voice=voice).event()), timeout)
    try:
        while True:
            event = await await_with_timeout(client.read_event(), timeout)
            if event is None:
                break
            yield event
            if AudioStop.is_type(event.type):
                break
    finally:
        await client.disconnect()
