"""Configuration helpers for the Pulse voice assistant."""

from __future__ import annotations

import os
import shlex
import socket
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pulse.utils import (
    parse_bool,
    parse_float,
    parse_int,
    sanitize_hostname_for_entity_id,
    split_csv,
)


def _strip_or_none(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


DEFAULT_WAKE_MODEL = "hey_jarvis"
WAKE_PIPELINES = {"pulse", "home_assistant"}


def _parse_wake_route_string(value: str | None) -> dict[str, str]:
    if not value:
        return {}
    routes: dict[str, str] = {}
    for raw in value.split(","):
        stripped = raw.strip()
        if not stripped:
            continue
        if "=" in stripped:
            name, pipeline = stripped.split("=", 1)
        elif ":" in stripped:
            name, pipeline = stripped.split(":", 1)
        else:
            continue
        name = name.strip()
        pipeline = pipeline.strip().lower()
        if not name or pipeline not in WAKE_PIPELINES:
            continue
        routes[name] = pipeline
    return routes


def _parse_wake_profiles(source: dict[str, str]) -> tuple[list[str], dict[str, str]]:
    routes: dict[str, str] = {}

    pulse_words = split_csv(source.get("PULSE_ASSISTANT_WAKE_WORDS_PULSE")) or [DEFAULT_WAKE_MODEL]

    ha_words = split_csv(source.get("PULSE_ASSISTANT_WAKE_WORDS_HA"))
    manual_routes = _parse_wake_route_string(source.get("PULSE_ASSISTANT_WAKE_ROUTES"))

    for model in pulse_words:
        if model:
            routes.setdefault(model, "pulse")
    for model in ha_words:
        if model:
            routes[model] = "home_assistant"
    for model, pipeline in manual_routes.items():
        routes[model] = pipeline

    if not routes:
        routes[DEFAULT_WAKE_MODEL] = "pulse"

    wake_models = sorted(routes)
    return wake_models, routes


def _normalize_calendar_url(value: str | None) -> str | None:
    if not value:
        return None
    trimmed = value.strip()
    if not trimmed:
        return None
    lowered = trimmed.lower()
    if lowered.startswith("webcal://"):
        trimmed = "https://" + trimmed[9:]
    return trimmed


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
    gemini_model: str
    gemini_api_key: str | None
    gemini_base_url: str
    gemini_timeout: int


@dataclass(frozen=True)
class MqttConfig:
    host: str | None
    port: int
    username: str | None
    password: str | None
    tls_enabled: bool
    cert: str | None
    key: str | None
    ca_cert: str | None
    topic_base: str


@dataclass(frozen=True)
class HomeAssistantConfig:
    base_url: str | None
    token: str | None
    verify_ssl: bool
    assist_pipeline: str | None
    wake_endpoint: WyomingEndpoint | None
    stt_endpoint: WyomingEndpoint | None
    tts_endpoint: WyomingEndpoint | None
    timer_entity: str | None
    reminder_service: str | None


@dataclass(frozen=True)
class AssistantPreferences:
    wake_sound: bool
    speaking_style: Literal["relaxed", "normal", "aggressive"]
    wake_sensitivity: Literal["low", "normal", "high"]


@dataclass(frozen=True)
class NewsConfig:
    api_key: str | None
    base_url: str
    country: str
    category: str
    language: str
    max_articles: int


@dataclass(frozen=True)
class WeatherConfig:
    location: str | None
    units: Literal["auto", "imperial", "metric"]
    language: str
    forecast_days: int
    base_url: str


@dataclass(frozen=True)
class SportsConfig:
    default_country: str
    headline_country: str
    favorite_teams: tuple[str, ...]
    default_leagues: tuple[str, ...]
    base_url: str


@dataclass(frozen=True)
class InfoConfig:
    news: NewsConfig
    weather: WeatherConfig
    sports: SportsConfig
    what3words_api_key: str | None


@dataclass(frozen=True)
class CalendarConfig:
    enabled: bool
    feeds: tuple[str, ...]
    refresh_minutes: int
    lookahead_hours: int
    attendee_emails: tuple[str, ...]
    default_notifications: tuple[int, ...]  # Minutes before event start (e.g., (10, 5) for 10-min and 5-min reminders)


@dataclass(frozen=True)
class AssistantConfig:
    hostname: str
    device_name: str
    language: str | None
    wake_models: list[str]
    wake_routes: dict[str, Literal["pulse", "home_assistant"]]
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
    home_assistant: HomeAssistantConfig
    preferences: AssistantPreferences
    media_player_entity: str | None
    self_audio_trigger_level: int
    log_llm_messages: bool
    info: InfoConfig
    calendar: CalendarConfig

    @staticmethod
    def from_env(env: dict[str, str] | None = None) -> AssistantConfig:
        source = env or os.environ
        hostname = source.get("PULSE_HOSTNAME") or socket.gethostname()
        device_name = source.get("PULSE_NAME") or hostname.replace("-", " ").title()

        wake_models, wake_routes = _parse_wake_profiles(source)

        mic_cmd = shlex.split(
            source.get(
                "PULSE_ASSISTANT_MIC_CMD",
                "arecord -q -t raw -f S16_LE -c 1 -r 16000 -",
            )
        )

        mic = MicConfig(
            command=mic_cmd,
            rate=parse_int(source.get("PULSE_ASSISTANT_MIC_RATE"), 16000),
            width=parse_int(source.get("PULSE_ASSISTANT_MIC_WIDTH"), 2),
            channels=parse_int(source.get("PULSE_ASSISTANT_MIC_CHANNELS"), 1),
            chunk_ms=parse_int(source.get("PULSE_ASSISTANT_MIC_CHUNK_MS"), 30),
        )

        phrase = PhraseConfig(
            min_seconds=parse_float(source.get("PULSE_ASSISTANT_MIN_PHRASE_SECONDS"), 1.5),
            max_seconds=parse_float(source.get("PULSE_ASSISTANT_MAX_PHRASE_SECONDS"), 8.0),
            silence_ms=parse_int(source.get("PULSE_ASSISTANT_SILENCE_MS"), 1200),
            rms_floor=parse_int(source.get("PULSE_ASSISTANT_RMS_THRESHOLD"), 120),
        )

        wake_endpoint = WyomingEndpoint(
            host=source.get("WYOMING_OPENWAKEWORD_HOST", "127.0.0.1"),
            port=parse_int(source.get("WYOMING_OPENWAKEWORD_PORT"), 10400),
            model=None,
        )
        stt_endpoint = WyomingEndpoint(
            host=source.get("WYOMING_WHISPER_HOST", "127.0.0.1"),
            port=parse_int(source.get("WYOMING_WHISPER_PORT"), 10300),
            model=source.get("PULSE_ASSISTANT_STT_MODEL"),
        )
        tts_endpoint = WyomingEndpoint(
            host=source.get("WYOMING_PIPER_HOST", "127.0.0.1"),
            port=parse_int(source.get("WYOMING_PIPER_PORT"), 10200),
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
            openai_timeout=parse_int(source.get("OPENAI_TIMEOUT_SECONDS"), 45),
            gemini_model=source.get("GEMINI_MODEL", "gemini-1.5-flash-latest"),
            gemini_api_key=source.get("GEMINI_API_KEY"),
            gemini_base_url=source.get("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta"),
            gemini_timeout=parse_int(source.get("GEMINI_TIMEOUT_SECONDS"), 45),
        )

        topic_base = source.get("PULSE_ASSISTANT_TOPIC_BASE") or f"pulse/{hostname}/assistant"
        mqtt_username = _strip_or_none(source.get("MQTT_USER") or source.get("MQTT_USERNAME"))
        mqtt_password = _strip_or_none(source.get("MQTT_PASS") or source.get("MQTT_PASSWORD"))
        mqtt_tls_enabled = parse_bool(source.get("MQTT_TLS_ENABLED"), False)
        mqtt_cert = _strip_or_none(source.get("MQTT_CERT"))
        mqtt_key = _strip_or_none(source.get("MQTT_KEY"))
        mqtt_ca_cert = _strip_or_none(source.get("MQTT_CA_CERT"))
        mqtt = MqttConfig(
            host=source.get("MQTT_HOST"),
            port=parse_int(source.get("MQTT_PORT"), 1883),
            username=mqtt_username,
            password=mqtt_password,
            tls_enabled=mqtt_tls_enabled,
            cert=mqtt_cert,
            key=mqtt_key,
            ca_cert=mqtt_ca_cert,
            topic_base=topic_base.rstrip("/"),
        )

        action_file = None
        if path := source.get("PULSE_ASSISTANT_ACTIONS_FILE"):
            candidate = Path(path)
            if candidate.exists():
                action_file = candidate

        inline_actions = source.get("PULSE_ASSISTANT_ACTIONS")

        ha_base_url = source.get("HOME_ASSISTANT_BASE_URL")
        if ha_base_url:
            ha_base_url = ha_base_url.rstrip("/")
        ha_token = source.get("HOME_ASSISTANT_TOKEN") or source.get("HOME_ASSISTANT_LONG_LIVED_TOKEN")
        ha_verify_ssl = parse_bool(source.get("HOME_ASSISTANT_VERIFY_SSL"), True)
        ha_assist_pipeline = source.get("HOME_ASSISTANT_ASSIST_PIPELINE")
        ha_timer_entity = source.get("HOME_ASSISTANT_TIMER_ENTITY")
        ha_reminder_service = source.get("HOME_ASSISTANT_REMINDER_SERVICE")

        ha_wake_endpoint = _optional_wyoming_endpoint(
            source,
            host_key="HOME_ASSISTANT_OPENWAKEWORD_HOST",
            port_key="HOME_ASSISTANT_OPENWAKEWORD_PORT",
        )
        ha_stt_endpoint = _optional_wyoming_endpoint(
            source,
            host_key="HOME_ASSISTANT_WHISPER_HOST",
            port_key="HOME_ASSISTANT_WHISPER_PORT",
            model_key="HOME_ASSISTANT_STT_MODEL",
        )
        ha_tts_endpoint = _optional_wyoming_endpoint(
            source,
            host_key="HOME_ASSISTANT_PIPER_HOST",
            port_key="HOME_ASSISTANT_PIPER_PORT",
        )

        home_assistant = HomeAssistantConfig(
            base_url=ha_base_url,
            token=ha_token,
            verify_ssl=ha_verify_ssl,
            assist_pipeline=ha_assist_pipeline,
            wake_endpoint=ha_wake_endpoint,
            stt_endpoint=ha_stt_endpoint,
            tts_endpoint=ha_tts_endpoint,
            timer_entity=ha_timer_entity,
            reminder_service=ha_reminder_service,
        )

        preferences = AssistantPreferences(
            wake_sound=parse_bool(source.get("PULSE_ASSISTANT_WAKE_SOUND"), True),
            speaking_style=_normalize_choice(
                source.get("PULSE_ASSISTANT_SPEAKING_STYLE"),
                {"relaxed", "normal", "aggressive"},
                "normal",
            ),
            wake_sensitivity=_normalize_choice(
                source.get("PULSE_ASSISTANT_WAKE_SENSITIVITY"),
                {"low", "normal", "high"},
                "normal",
            ),
        )

        media_player_entity = _resolve_media_player_entity(hostname, source.get("PULSE_MEDIA_PLAYER_ENTITY"))
        self_audio_trigger_level = parse_int(source.get("PULSE_ASSISTANT_SELF_AUDIO_TRIGGER_LEVEL"), 7)
        self_audio_trigger_level = max(2, self_audio_trigger_level)

        transcript_topic = f"{mqtt.topic_base}/transcript"
        response_topic = f"{mqtt.topic_base}/response"
        state_topic = f"{mqtt.topic_base}/state"
        action_topic = f"{mqtt.topic_base}/actions"

        log_llm_messages = parse_bool(source.get("PULSE_ASSISTANT_LOG_LLM"), True)

        news_config = NewsConfig(
            api_key=source.get("PULSE_NEWS_API_KEY"),
            base_url=(source.get("PULSE_NEWS_BASE_URL") or "https://newsapi.org/v2").rstrip("/"),
            country=(source.get("PULSE_NEWS_COUNTRY") or "us").strip().lower() or "us",
            category=(source.get("PULSE_NEWS_CATEGORY") or "general").strip().lower() or "general",
            language=(source.get("PULSE_NEWS_LANGUAGE") or "en").strip().lower() or "en",
            max_articles=max(1, parse_int(source.get("PULSE_NEWS_MAX_ARTICLES"), 5)),
        )
        weather_config = WeatherConfig(
            location=(source.get("PULSE_WEATHER_LOCATION") or "").strip() or None,
            units=_normalize_choice(
                source.get("PULSE_WEATHER_UNITS"),
                {"auto", "imperial", "metric"},
                "auto",
            ),
            language=(source.get("PULSE_WEATHER_LANGUAGE") or "en").strip().lower() or "en",
            forecast_days=max(1, min(5, parse_int(source.get("PULSE_WEATHER_FORECAST_DAYS"), 3))),
            base_url=(source.get("PULSE_WEATHER_BASE_URL") or "https://api.open-meteo.com/v1/forecast").rstrip("/"),
        )
        favorite_teams = tuple(team.strip() for team in split_csv(source.get("PULSE_SPORTS_FAVORITE_TEAMS")))
        default_leagues = tuple(
            league.strip().lower()
            for league in split_csv(source.get("PULSE_SPORTS_DEFAULT_LEAGUES") or "nfl,nba,mlb,nhl")
        )
        sports_config = SportsConfig(
            default_country=(source.get("PULSE_SPORTS_DEFAULT_COUNTRY") or "us").strip().lower() or "us",
            headline_country=(source.get("PULSE_SPORTS_HEADLINE_COUNTRY") or "us").strip().lower() or "us",
            favorite_teams=favorite_teams,
            default_leagues=default_leagues,
            base_url=(source.get("PULSE_SPORTS_BASE_URL") or "https://site.api.espn.com/apis").rstrip("/"),
        )
        info_config = InfoConfig(
            news=news_config,
            weather=weather_config,
            sports=sports_config,
            what3words_api_key=(source.get("WHAT3WORDS_API_KEY") or "").strip() or None,
        )

        raw_calendar_urls = split_csv(source.get("PULSE_CALENDAR_ICS_URLS"))
        feeds: tuple[str, ...] = tuple(
            normalized
            for normalized in (_normalize_calendar_url(url) for url in (raw_calendar_urls or []))
            if normalized
        )
        refresh_minutes = max(1, parse_int(source.get("PULSE_CALENDAR_REFRESH_MINUTES"), 5))
        lookahead_hours = max(1, parse_int(source.get("PULSE_CALENDAR_LOOKAHEAD_HOURS"), 72))
        owner_emails = tuple(
            email.strip().lower()
            for email in split_csv(source.get("PULSE_CALENDAR_OWNER_EMAILS"))
            if email and email.strip()
        )
        default_notifications_raw = source.get("PULSE_CALENDAR_DEFAULT_NOTIFICATIONS", "")
        default_notifications = tuple(
            sorted(
                {
                    max(0, int(minutes.strip()))
                    for minutes in split_csv(default_notifications_raw)
                    if minutes.strip().isdigit()
                },
                reverse=True,
            )
        )

        calendar_config = CalendarConfig(
            enabled=bool(feeds),
            feeds=feeds,
            refresh_minutes=refresh_minutes,
            lookahead_hours=lookahead_hours,
            attendee_emails=owner_emails,
            default_notifications=default_notifications,
        )

        return AssistantConfig(
            hostname=hostname,
            device_name=device_name,
            language=source.get("PULSE_ASSISTANT_LANGUAGE"),
            wake_models=wake_models,
            wake_routes=wake_routes,
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
            home_assistant=home_assistant,
            preferences=preferences,
            media_player_entity=media_player_entity,
            self_audio_trigger_level=self_audio_trigger_level,
            log_llm_messages=log_llm_messages,
            info=info_config,
            calendar=calendar_config,
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


def _optional_wyoming_endpoint(
    source: dict[str, str],
    *,
    host_key: str,
    port_key: str,
    model_key: str | None = None,
) -> WyomingEndpoint | None:
    host = source.get(host_key)
    if not host:
        return None
    port = parse_int(source.get(port_key), 0)
    if not port:
        return None
    model = source.get(model_key) if model_key else None
    return WyomingEndpoint(host=host, port=port, model=model)


def _normalize_choice(value: str | None, allowed: set[str], default: str) -> str:
    if not value:
        return default
    lowered = value.strip().lower()
    if lowered in allowed:
        return lowered
    return default


def _resolve_media_player_entity(hostname: str, override: str | None) -> str | None:
    candidate = (override or "").strip()
    if candidate:
        return candidate
    sanitized = sanitize_hostname_for_entity_id(hostname)
    return f"media_player.{sanitized}"
