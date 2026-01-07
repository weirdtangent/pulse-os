"""
Multi-turn conversation management and follow-up detection

Handles conversation flow including follow-up phrase recording and stop detection.

Features:
- Follow-up recording: Captures additional user speech after assistant response
- Conversation stop detection: Recognizes phrases like "never mind", "that's all"
- Phrase normalization: Cleans transcripts for reliable stop phrase matching
- Microphone management: Records phrases with silence detection and max duration
- Wake word prefix handling: Strips wake words from conversation stop commands

Follow-ups are currently disabled by design (should_listen_for_follow_up returns False)
but the infrastructure supports re-enabling multi-turn conversations.

Stop phrases include common dismissals (never mind, forget it, cancel) and are
normalized to handle variations in punctuation, capitalization, and wake word prefixes.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pulse.assistant.audio import ArecordStream
    from pulse.assistant.config import AssistantConfig
    from pulse.assistant.llm import LLMResult

LOGGER = logging.getLogger("pulse-assistant.conversation")


def normalize_conversation_stop_text(
    text: str,
    prefixes: Sequence[str] | None = None,
) -> str:
    """Normalize text for conversation stop phrase matching."""
    lowered = (text or "").strip().lower()
    if not lowered:
        return ""
    lowered = lowered.replace("'", "'")
    lowered = re.sub(r"[^\w\s']", " ", lowered)
    lowered = lowered.replace("'", "")
    lowered = re.sub(r"\s+", " ", lowered).strip()
    if not lowered:
        return ""
    if prefixes:
        for prefix in prefixes:
            normalized_prefix = re.sub(r"\s+", " ", prefix.strip().lower())
            if not normalized_prefix:
                continue
            if lowered == normalized_prefix:
                return ""
            if lowered.startswith(normalized_prefix + " "):
                lowered = lowered[len(normalized_prefix) + 1 :].strip()
                break
    suffixes = ("please", "thanks", "thank you", "for now", "right now", "today")
    trimmed = True
    while trimmed and lowered:
        trimmed = False
        for suffix in suffixes:
            needle = " " + suffix
            if lowered.endswith(needle):
                lowered = lowered[: -len(needle)].rstrip()
                trimmed = True
    return lowered


_CONVERSATION_STOP_PHRASES_RAW = (
    "nevermind",
    "never mind",
    "never mind that",
    "never mind about that",
    "forget it",
    "forget about it",
    "forget that",
    "nothing",
    "nothing else",
    "nothing for now",
    "nothing right now",
    "that's all",
    "that is all",
    "that's it",
    "that is it",
    "cancel",
    "cancel that",
    "cancel it",
    "no thanks",
    "no thank you",
    "no thank you kindly",
    "stop listening",
    "you can stop",
    "all good",
    "we are good",
    "were good",
    "i am good",
    "im good",
    "dont worry about it",
    "don't worry about it",
)

CONVERSATION_STOP_PHRASES: set[str] = set()
for phrase in _CONVERSATION_STOP_PHRASES_RAW:
    normalized_phrase = normalize_conversation_stop_text(phrase)
    if normalized_phrase:
        CONVERSATION_STOP_PHRASES.add(normalized_phrase)


def should_listen_for_follow_up(llm_result: LLMResult | None) -> bool:
    """Determine if we should listen for a follow-up response."""
    # Temporarily disable automatic follow-ups; design to be revisited.
    return False


def evaluate_follow_up_transcript(
    transcript: str | None,
    previous_normalized: str | None = None,
) -> tuple[bool, str | None]:
    """Evaluate if a follow-up transcript is useful."""
    if not transcript or not transcript.strip():
        return False, None
    normalized = re.sub(r"[^a-z0-9\s]", " ", transcript.lower()).strip()
    if not normalized:
        return False, None
    if previous_normalized and normalized == previous_normalized:
        return False, normalized
    noise_tokens = {"you", "ya", "u"}
    if normalized in noise_tokens:
        return False, normalized
    return True, normalized


def is_conversation_stop_command(transcript: str | None, conversation_stop_prefixes: tuple[str, ...]) -> bool:
    """Check if transcript is a conversation stop command."""
    normalized = normalize_conversation_stop_text(
        transcript or "",
        prefixes=conversation_stop_prefixes,
    )
    if not normalized:
        return False
    return normalized in CONVERSATION_STOP_PHRASES


def build_conversation_stop_prefixes(config: AssistantConfig) -> tuple[str, ...]:
    """Build conversation stop prefixes from wake words."""
    prefixes: list[str] = []
    for model in config.wake_models:
        display_name = model.replace("_", " ").replace("-", " ").title()
        prefixes.append(display_name)
        prefixes.append(model)
        parts = model.split("_")
        if len(parts) > 1:
            prefixes.append(parts[-1])
    return tuple(prefixes)


class ConversationManager:
    """Manages multi-turn conversations and follow-ups."""

    def __init__(
        self,
        config: AssistantConfig,
        mic: ArecordStream,
        compute_rms,
        last_response_end: float | None = None,
    ) -> None:
        self.config = config
        self.mic = mic
        self.compute_rms = compute_rms
        self._last_response_end = last_response_end
        self._follow_up_start_delay = 0.4
        self._conversation_stop_prefixes = build_conversation_stop_prefixes(config)

    async def record_follow_up_phrase(self) -> bytes | None:
        """Record a follow-up phrase from the user."""
        listen_window = max(self.config.phrase.max_seconds, 10.0)
        return await self.record_phrase(
            min_seconds=0.4,
            max_seconds=listen_window,
            silence_ms=self.config.phrase.silence_ms,
        )

    async def record_phrase(
        self,
        *,
        min_seconds: float | None = None,
        max_seconds: float | None = None,
        silence_ms: int | None = None,
    ) -> bytes | None:
        """Record a phrase from the microphone."""
        chunk_ms = self.config.mic.chunk_ms
        min_duration = self.config.phrase.min_seconds if min_seconds is None else min_seconds
        max_duration = self.config.phrase.max_seconds if max_seconds is None else max_seconds
        silence_window = self.config.phrase.silence_ms if silence_ms is None else silence_ms
        min_chunks = int(max(1, (min_duration * 1000) / chunk_ms))
        max_chunks = int(max(1, (max_duration * 1000) / chunk_ms))
        silence_chunks = int(max(1, silence_window / chunk_ms))
        buffer = bytearray()
        silence_run = 0
        chunks = 0
        while chunks < max_chunks:
            chunk = await self.mic.read_chunk()
            buffer.extend(chunk)
            rms = self.compute_rms(chunk, self.config.mic.width)
            if rms < self.config.phrase.rms_floor and chunks >= min_chunks:
                silence_run += 1
                if silence_run >= silence_chunks:
                    break
            else:
                silence_run = 0
            chunks += 1
        return bytes(buffer) if buffer else None

    async def wait_for_speech_tail(self) -> None:
        """Wait for speech tail before listening for follow-up."""
        if self._last_response_end is None:
            return
        remaining = self._follow_up_start_delay - (time.monotonic() - self._last_response_end)
        if remaining > 0:
            await asyncio.sleep(remaining)

    def update_last_response_end(self, timestamp: float | None) -> None:
        """Update the timestamp of when the last response ended."""
        self._last_response_end = timestamp

    def is_conversation_stop(self, transcript: str | None) -> bool:
        """Check if transcript is a conversation stop command."""
        return is_conversation_stop_command(transcript, self._conversation_stop_prefixes)

    def evaluate_follow_up(
        self, transcript: str | None, previous_normalized: str | None = None
    ) -> tuple[bool, str | None]:
        """Evaluate if a follow-up transcript is useful."""
        return evaluate_follow_up_transcript(transcript, previous_normalized)
