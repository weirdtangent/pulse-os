"""Configuration helpers for the Pulse voice assistant."""

from __future__ import annotations

import os
import shlex
import socket
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _as_int(value: str | None, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _as_float(value: str | None, default: float) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


@dataclass(frozen=True)
class WyomingEndpoint:
    host: str
    port: int
    model: str | None = None


@dataclass(frozen=True)
class MicConfig:
    command: list[str]
    rate: int
    width: int
    channels: int
    chunk_ms: int

    @property
    def bytes_per_chunk(self) -> int:
        samples = int(self.rate * (self.chunk_ms / 1000))
        return samples * self.width * self.channels


@dataclass(frozen=True)
class PhraseConfig:
    min_seconds: float
    max_seconds: float
    silence_ms: int
    rms_floor: int


@dataclass(frozen=True)
class LLMConfig:
    provider: str
    system_prompt: str
    openai_model: str
    openai_api_key: str | None
    openai_base_url: str
    openai_timeout: int


@dataclass(frozen=True)
class MqttConfig:
    host: str | None
    port: int
    username: str | None
    password: str | None
    topic_base: str


@dataclass(frozen=True)
class AssistantConfig:
    hostname: str
    device_name: str
    language: str | None
    wake_models: list[str]
    mic: MicConfig
    phrase: PhraseConfig
    wake_endpoint: WyomingEndpoint
    stt_endpoint: WyomingEndpoint
    tts_endpoint: WyomingEndpoint
    tts_voice: str | None
    llm: LLMConfig
    mqtt: MqttConfig
    action_file: Path | None
    inline_actions: str | None
    transcript_topic: str
    response_topic: str
    state_topic: str
    action_topic: str

    @staticmethod
    def from_env(env: dict[str, str] | None = None) -> AssistantConfig:
        source = env or os.environ
        hostname = source.get("PULSE_HOSTNAME") or socket.gethostname()
        device_name = source.get("PULSE_NAME") or hostname.replace("-", " ").title()

        wake_models = _split_csv(source.get("PULSE_ASSISTANT_WAKE_WORDS")) or ["okay_pulse"]

        mic_cmd = shlex.split(
            source.get(
                "PULSE_ASSISTANT_MIC_CMD",
                "arecord -q -t raw -f S16_LE -c 1 -r 16000 -",
            )
        )

        mic = MicConfig(
            command=mic_cmd,
            rate=_as_int(source.get("PULSE_ASSISTANT_MIC_RATE"), 16000),
            width=_as_int(source.get("PULSE_ASSISTANT_MIC_WIDTH"), 2),
            channels=_as_int(source.get("PULSE_ASSISTANT_MIC_CHANNELS"), 1),
            chunk_ms=_as_int(source.get("PULSE_ASSISTANT_MIC_CHUNK_MS"), 30),
        )

        phrase = PhraseConfig(
            min_seconds=_as_float(source.get("PULSE_ASSISTANT_MIN_PHRASE_SECONDS"), 1.5),
            max_seconds=_as_float(source.get("PULSE_ASSISTANT_MAX_PHRASE_SECONDS"), 8.0),
            silence_ms=_as_int(source.get("PULSE_ASSISTANT_SILENCE_MS"), 1200),
            rms_floor=_as_int(source.get("PULSE_ASSISTANT_RMS_THRESHOLD"), 120),
        )

        wake_endpoint = WyomingEndpoint(
            host=source.get("WYOMING_OPENWAKEWORD_HOST", "127.0.0.1"),
            port=_as_int(source.get("WYOMING_OPENWAKEWORD_PORT"), 10400),
            model=None,
        )
        stt_endpoint = WyomingEndpoint(
            host=source.get("WYOMING_WHISPER_HOST", "127.0.0.1"),
            port=_as_int(source.get("WYOMING_WHISPER_PORT"), 10300),
            model=source.get("PULSE_ASSISTANT_STT_MODEL"),
        )
        tts_endpoint = WyomingEndpoint(
            host=source.get("WYOMING_PIPER_HOST", "127.0.0.1"),
            port=_as_int(source.get("WYOMING_PIPER_PORT"), 10200),
            model=None,
        )

        system_prompt = source.get("PULSE_ASSISTANT_SYSTEM_PROMPT", "").strip()
        prompt_file = source.get("PULSE_ASSISTANT_SYSTEM_PROMPT_FILE")
        if not system_prompt and prompt_file:
            candidate = Path(prompt_file)
            if candidate.is_file():
                system_prompt = candidate.read_text(encoding="utf-8").strip()
        if not system_prompt:
            system_prompt = DEFAULT_SYSTEM_PROMPT

        llm = LLMConfig(
            provider=source.get("PULSE_ASSISTANT_PROVIDER", "openai").lower(),
            system_prompt=system_prompt,
            openai_model=source.get("OPENAI_MODEL", "gpt-4o-mini"),
            openai_api_key=source.get("OPENAI_API_KEY"),
            openai_base_url=source.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
            openai_timeout=_as_int(source.get("OPENAI_TIMEOUT_SECONDS"), 45),
        )

        topic_base = source.get("PULSE_ASSISTANT_TOPIC_BASE") or f"pulse/{hostname}/assistant"
        mqtt = MqttConfig(
            host=source.get("MQTT_HOST"),
            port=_as_int(source.get("MQTT_PORT"), 1883),
            username=source.get("MQTT_USERNAME"),
            password=source.get("MQTT_PASSWORD"),
            topic_base=topic_base.rstrip("/"),
        )

        action_file = None
        if path := source.get("PULSE_ASSISTANT_ACTIONS_FILE"):
            candidate = Path(path)
            if candidate.exists():
                action_file = candidate

        inline_actions = source.get("PULSE_ASSISTANT_ACTIONS")

        transcript_topic = f"{mqtt.topic_base}/transcript"
        response_topic = f"{mqtt.topic_base}/response"
        state_topic = f"{mqtt.topic_base}/state"
        action_topic = f"{mqtt.topic_base}/actions"

        return AssistantConfig(
            hostname=hostname,
            device_name=device_name,
            language=source.get("PULSE_ASSISTANT_LANGUAGE"),
            wake_models=wake_models,
            mic=mic,
            phrase=phrase,
            wake_endpoint=wake_endpoint,
            stt_endpoint=stt_endpoint,
            tts_endpoint=tts_endpoint,
            tts_voice=source.get("PULSE_ASSISTANT_TTS_VOICE"),
            llm=llm,
            mqtt=mqtt,
            action_file=action_file,
            inline_actions=inline_actions,
            transcript_topic=transcript_topic,
            response_topic=response_topic,
            state_topic=state_topic,
            action_topic=action_topic,
        )


DEFAULT_SYSTEM_PROMPT = """You are Pulse, a calm and concise desk assistant.
- Answer questions directly using no more than three sentences unless the user
  explicitly asks for more detail.
- When a question sounds like a greeting or small talk, respond warmly and briefly.
- If the user asks you to perform an action and it matches an available action slug,
  include that slug in your response JSON. Otherwise, explain what information you
  still need or that the action is unavailable.
- Never invent actions or slugs.
- When unsure, ask clarifying questions instead of guessing."""


def render_actions_for_prompt(actions: Iterable[dict[str, Any]]) -> str:
    """Produce a human readable summary for the LLM system prompt."""
    lines: list[str] = []
    for action in actions:
        slug = action.get("slug")
        desc = action.get("description") or ""
        lines.append(f"- {slug}: {desc}".strip())
    return "\n".join(lines)
