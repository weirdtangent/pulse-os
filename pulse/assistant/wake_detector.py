"""Wake word detection session management."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import sys
import threading
import time
from array import array
from dataclasses import dataclass
from typing import TYPE_CHECKING

from wyoming.audio import AudioChunk, AudioStart, AudioStop
from wyoming.client import AsyncTcpClient
from wyoming.wake import Detect, Detection, NotDetected

if TYPE_CHECKING:
    from pulse.assistant.audio import ArecordStream
    from pulse.assistant.config import AssistantConfig, AssistantPreferences, WyomingEndpoint

LOGGER = logging.getLogger("pulse-assistant.wake")


@dataclass
class WakeEndpointStream:
    """Grouping of wake-word models that share an OpenWakeWord endpoint."""

    endpoint: WyomingEndpoint
    labels: set[str]
    models: list[str]

    @property
    def display_label(self) -> str:
        label = "/".join(sorted(self.labels))
        return f"{label} ({self.endpoint.host}:{self.endpoint.port})"


class WakeContextChanged(Exception):
    """Internal signal used to restart wake detection when context shifts."""


def compute_rms(chunk: bytes, sample_width: int) -> int:
    """Compute RMS (Root Mean Square) for an audio chunk."""
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


class WakeDetector:
    """Manages wake word detection sessions and context."""

    def __init__(
        self,
        config: AssistantConfig,
        preferences: AssistantPreferences,
        mic: ArecordStream,
        self_audio_trigger_level: int,
    ) -> None:
        self.config = config
        self.preferences = preferences
        self.mic = mic
        self._self_audio_trigger_level = self_audio_trigger_level
        self._self_audio_lock = threading.Lock()
        self._self_audio_remote_active = False
        self._local_audio_depth = 0
        self._wake_context_lock = threading.Lock()
        self._wake_context_version = 0
        self._log_throttle: dict[str, float] = {}

    def self_audio_is_active(self) -> bool:
        """Check if local audio playback is active."""
        with self._self_audio_lock:
            return self._local_audio_depth > 0 or self._self_audio_remote_active

    def get_remote_audio_active(self) -> bool:
        """Get remote audio playback state."""
        with self._self_audio_lock:
            return self._self_audio_remote_active

    def set_remote_audio_active(self, active: bool) -> bool:
        """Set remote audio playback state. Returns True if state changed."""
        notify = False
        previous = False
        with self._self_audio_lock:
            previous = self._self_audio_remote_active
            if previous != active:
                self._self_audio_remote_active = active
                notify = True
        if notify:
            self.mark_wake_context_dirty()
        return notify

    def _debug_throttled(self, key: str, message: str, *args, interval: float = 30.0) -> None:
        """Emit a debug log with throttling to reduce noise."""
        now = time.monotonic()
        last = self._log_throttle.get(key, 0.0)
        if now - last >= interval:
            LOGGER.debug(message, *args)
            self._log_throttle[key] = now

    def increment_local_audio_depth(self) -> None:
        """Increment local audio depth counter."""
        notify = False
        with self._self_audio_lock:
            self._local_audio_depth += 1
            if self._local_audio_depth == 1:
                notify = True
        if notify:
            self.mark_wake_context_dirty()

    def decrement_local_audio_depth(self) -> None:
        """Decrement local audio depth counter."""
        notify = False
        with self._self_audio_lock:
            if self._local_audio_depth > 0:
                self._local_audio_depth -= 1
                if self._local_audio_depth == 0:
                    notify = True
        if notify:
            self.mark_wake_context_dirty()

    def mark_wake_context_dirty(self) -> None:
        """Mark wake context as changed, forcing restart."""
        with self._wake_context_lock:
            self._wake_context_version = (self._wake_context_version + 1) % 1_000_000

    @contextlib.asynccontextmanager
    async def local_audio_block(self):
        """Context manager to block wake detection during audio playback."""
        self.increment_local_audio_depth()
        try:
            yield
        finally:
            self.decrement_local_audio_depth()

    def _preferred_trigger_level(self) -> int | None:
        """Get preferred trigger level based on wake sensitivity."""
        mapping = {
            "low": 5,
            "high": 2,
        }
        return mapping.get(self.preferences.wake_sensitivity)

    def _context_for_detect(self) -> dict[str, int] | None:
        """Build context dictionary for wake detection."""
        trigger_level = self._preferred_trigger_level()
        if self.self_audio_is_active():
            enforced = self._self_audio_trigger_level
            trigger_level = enforced if trigger_level is None else max(trigger_level, enforced)
        if trigger_level is None:
            return None
        return {"trigger_level": trigger_level}

    def _wake_endpoint_streams(self) -> list[WakeEndpointStream]:
        """Group wake-word models by their assigned OpenWakeWord endpoint.

        Creates a separate stream for each model to work around openWakeWord
        limitation where only the first model in a Detect message is loaded.
        """
        ha_endpoint = self.config.home_assistant.wake_endpoint
        streams: list[WakeEndpointStream] = []
        for model in self.config.wake_models:
            pipeline = self.config.wake_routes.get(model, "pulse")
            if pipeline == "home_assistant" and ha_endpoint:
                endpoint = ha_endpoint
                label = "Home Assistant"
            else:
                endpoint = self.config.wake_endpoint
                label = "Pulse"
            # Create a separate stream for each model to ensure all models are loaded
            streams.append(
                WakeEndpointStream(
                    endpoint=endpoint,
                    labels={label},
                    models=[model],
                )
            )
        return streams

    def stable_detect_context(self) -> tuple[dict[str, int] | None, int]:
        """Get stable wake detection context (waits for version to stabilize)."""
        while True:
            start_version = self._wake_context_version
            context = self._context_for_detect()
            if start_version == self._wake_context_version:
                return context, start_version

    async def wait_for_wake_word(self, shutdown: asyncio.Event, get_earmuffs_enabled) -> str | None:
        """Wait for a wake word to be detected."""
        while not shutdown.is_set():
            if self.self_audio_is_active():
                # Suppress wake detection while local/remote audio is playing
                await asyncio.sleep(0.5)
                continue
            enabled = get_earmuffs_enabled()
            if enabled:
                await asyncio.sleep(0.5)
                continue
            try:
                return await self.run_wake_detector_session()
            except WakeContextChanged:
                LOGGER.debug("Wake context updated; restarting wake detector")
                continue
        return None

    async def run_wake_detector_session(self) -> str | None:
        """Run a single wake detection session."""
        detect_context, context_version = self.stable_detect_context()
        streams = self._wake_endpoint_streams()
        if not streams:
            LOGGER.warning("No wake models configured; skipping wake detection session")
            await asyncio.sleep(1.0)
            return None
        timestamp = 0
        chunk_ms = self.config.mic.chunk_ms
        clients: list[AsyncTcpClient] = []
        reader_tasks: dict[asyncio.Task[str | None], WakeEndpointStream] = {}
        try:
            for stream in streams:
                client = AsyncTcpClient(stream.endpoint.host, stream.endpoint.port)
                await client.connect()
                clients.append(client)
                detect_message = Detect(names=stream.models, context=detect_context or None)
                self._debug_throttled(
                    "wake_detect",
                    "Sending Detect message to %s with model names: %s",
                    stream.display_label,
                    stream.models,
                )
                await client.write_event(detect_message.event())
                await client.write_event(
                    AudioStart(
                        rate=self.config.mic.rate,
                        width=self.config.mic.width,
                        channels=self.config.mic.channels,
                        timestamp=0,
                    ).event()
                )
                task = asyncio.create_task(self._read_wake_events(client, endpoint_label=stream.display_label))
                reader_tasks[task] = stream
                self._debug_throttled(
                    "wake_stream",
                    "Started wake detection stream for %s with models: %s",
                    stream.display_label,
                    ", ".join(stream.models),
                )
            detected_word: str | None = None
            while reader_tasks:
                if context_version != self._wake_context_version:
                    raise WakeContextChanged
                chunk_bytes = await self.mic.read_chunk()
                if context_version != self._wake_context_version:
                    raise WakeContextChanged
                chunk_event = AudioChunk(
                    rate=self.config.mic.rate,
                    width=self.config.mic.width,
                    channels=self.config.mic.channels,
                    audio=chunk_bytes,
                    timestamp=timestamp,
                )
                for client in clients:
                    await client.write_event(chunk_event.event())
                timestamp += chunk_ms
                finished = [task for task in reader_tasks if task.done()]
                for task in finished:
                    stream = reader_tasks.pop(task)
                    try:
                        detection = task.result()
                    except Exception:
                        LOGGER.warning("Wake detector stream %s failed", stream.display_label, exc_info=True)
                        continue
                    if detection:
                        detected_word = detection
                        break
                if detected_word is not None:
                    break
            return detected_word
        finally:
            for client in clients:
                with contextlib.suppress(Exception):
                    await client.write_event(AudioStop(timestamp=timestamp).event())
            for task in reader_tasks:
                task.cancel()
            for task in reader_tasks:
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            for client in clients:
                with contextlib.suppress(Exception):
                    await client.disconnect()

    async def _read_wake_events(self, client: AsyncTcpClient, *, endpoint_label: str) -> str | None:
        """Read events from wake detection client."""
        while True:
            event = await client.read_event()
            if event is None:
                return None
            if Detection.is_type(event.type):
                detection = Detection.from_event(event)
                detected_name = detection.name or self.config.wake_models[0]
                return detected_name
            if NotDetected.is_type(event.type):
                LOGGER.debug("OpenWakeWord (%s) reported NotDetected", endpoint_label)
                return None
