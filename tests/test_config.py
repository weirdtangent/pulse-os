"""Tests for pulse.assistant.config — configuration parsing and helpers."""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from pulse.assistant.config import (
    DEFAULT_HA_WAKE_MODEL,
    DEFAULT_WAKE_MODEL,
    AssistantConfig,
    MicConfig,
    _normalize_calendar_url,
    _normalize_choice,
    _optional_wyoming_endpoint,
    _parse_wake_profiles,
    _parse_wake_route_string,
    _resolve_media_player_entity,
    _strip_or_none,
    render_actions_for_prompt,
)

# ---------------------------------------------------------------------------
# Factory helpers
# ---------------------------------------------------------------------------

_BASE_ENV: dict[str, str] = {
    "WYOMING_OPENWAKEWORD_HOST": "localhost",
    "WYOMING_WHISPER_HOST": "localhost",
    "WYOMING_PIPER_HOST": "localhost",
}


def _from_env(overrides: dict[str, str] | None = None) -> AssistantConfig:
    """Build an AssistantConfig from a minimal env dict, mocking external calls."""
    env = dict(_BASE_ENV)
    if overrides:
        env.update(overrides)
    mock_location = Mock()
    mock_location.country_code = "us"
    with patch("pulse.assistant.config.resolve_location_defaults", return_value=mock_location):
        with patch("pulse.assistant.config.SoundSettings.with_defaults") as mock_sounds:
            mock_sounds.return_value = Mock()
            return AssistantConfig.from_env(env)


# ===================================================================
# _strip_or_none
# ===================================================================


class TestStripOrNone:
    def test_none_returns_none(self) -> None:
        assert _strip_or_none(None) is None

    def test_empty_string_returns_none(self) -> None:
        assert _strip_or_none("") is None

    def test_whitespace_only_returns_none(self) -> None:
        assert _strip_or_none("   ") is None

    def test_normal_string_stripped(self) -> None:
        assert _strip_or_none("  hello  ") == "hello"

    def test_no_whitespace_unchanged(self) -> None:
        assert _strip_or_none("world") == "world"


# ===================================================================
# _parse_wake_route_string
# ===================================================================


class TestParseWakeRouteString:
    def test_none_returns_empty(self) -> None:
        assert _parse_wake_route_string(None) == {}

    def test_empty_string_returns_empty(self) -> None:
        assert _parse_wake_route_string("") == {}

    def test_equals_delimiter(self) -> None:
        result = _parse_wake_route_string("model=pulse")
        assert result == {"model": "pulse"}

    def test_colon_delimiter(self) -> None:
        result = _parse_wake_route_string("model:home_assistant")
        assert result == {"model": "home_assistant"}

    def test_multiple_routes_comma_separated(self) -> None:
        result = _parse_wake_route_string("a=pulse,b=home_assistant")
        assert result == {"a": "pulse", "b": "home_assistant"}

    def test_invalid_pipeline_skipped(self) -> None:
        result = _parse_wake_route_string("a=invalid_pipeline")
        assert result == {}

    def test_empty_segments_skipped(self) -> None:
        result = _parse_wake_route_string("a=pulse,,b=home_assistant")
        assert result == {"a": "pulse", "b": "home_assistant"}

    def test_no_delimiter_skipped(self) -> None:
        result = _parse_wake_route_string("justwords")
        assert result == {}

    def test_whitespace_handling(self) -> None:
        result = _parse_wake_route_string(" a = pulse , b = home_assistant ")
        assert result == {"a": "pulse", "b": "home_assistant"}

    def test_empty_name_after_strip_skipped(self) -> None:
        result = _parse_wake_route_string("  =pulse")
        assert result == {}


# ===================================================================
# _parse_wake_profiles
# ===================================================================


class TestParseWakeProfiles:
    def test_defaults_no_ha_endpoint(self) -> None:
        models, routes = _parse_wake_profiles({})
        assert DEFAULT_WAKE_MODEL in routes
        assert DEFAULT_HA_WAKE_MODEL in routes
        # Without HA wake endpoint, ok_nabu falls back to pulse
        assert routes[DEFAULT_WAKE_MODEL] == "pulse"
        assert routes[DEFAULT_HA_WAKE_MODEL] == "pulse"
        assert models == sorted(routes)

    def test_ha_wake_endpoint_configured(self) -> None:
        source = {"HOME_ASSISTANT_OPENWAKEWORD_HOST": "ha.local"}
        models, routes = _parse_wake_profiles(source)
        assert routes[DEFAULT_HA_WAKE_MODEL] == "home_assistant"
        assert routes[DEFAULT_WAKE_MODEL] == "pulse"

    def test_manual_route_overrides_auto(self) -> None:
        source = {
            "HOME_ASSISTANT_OPENWAKEWORD_HOST": "ha.local",
            "PULSE_ASSISTANT_WAKE_ROUTES": f"{DEFAULT_HA_WAKE_MODEL}=pulse",
        }
        _, routes = _parse_wake_profiles(source)
        # Manual override should take precedence
        assert routes[DEFAULT_HA_WAKE_MODEL] == "pulse"

    def test_custom_pulse_wake_words(self) -> None:
        source = {"PULSE_ASSISTANT_WAKE_WORDS_PULSE": "hey_pulse,hey_buddy"}
        models, routes = _parse_wake_profiles(source)
        assert routes.get("hey_pulse") == "pulse"
        assert routes.get("hey_buddy") == "pulse"

    def test_custom_ha_wake_words(self) -> None:
        source = {
            "PULSE_ASSISTANT_WAKE_WORDS_HA": "hey_home,hey_house",
            "HOME_ASSISTANT_OPENWAKEWORD_HOST": "ha.local",
        }
        _, routes = _parse_wake_profiles(source)
        assert routes["hey_home"] == "home_assistant"
        assert routes["hey_house"] == "home_assistant"

    def test_empty_routes_fallback(self) -> None:
        # Even with empty wake word lists, we get the default fallback
        models, routes = _parse_wake_profiles({})
        assert len(routes) >= 1

    def test_models_are_sorted(self) -> None:
        source = {"PULSE_ASSISTANT_WAKE_WORDS_PULSE": "zzz_model,aaa_model"}
        models, _ = _parse_wake_profiles(source)
        assert models == sorted(models)


# ===================================================================
# _normalize_calendar_url
# ===================================================================


class TestNormalizeCalendarUrl:
    def test_none_returns_none(self) -> None:
        assert _normalize_calendar_url(None) is None

    def test_empty_returns_none(self) -> None:
        assert _normalize_calendar_url("") is None

    def test_whitespace_only_returns_none(self) -> None:
        assert _normalize_calendar_url("   ") is None

    def test_webcal_converted_to_https(self) -> None:
        result = _normalize_calendar_url("webcal://example.com/cal.ics")
        assert result == "https://example.com/cal.ics"

    def test_https_url_unchanged(self) -> None:
        url = "https://example.com/cal.ics"
        assert _normalize_calendar_url(url) == url

    def test_whitespace_trimmed(self) -> None:
        result = _normalize_calendar_url("  https://example.com/cal.ics  ")
        assert result == "https://example.com/cal.ics"

    def test_webcal_case_insensitive(self) -> None:
        result = _normalize_calendar_url("WEBCAL://example.com/cal.ics")
        assert result == "https://example.com/cal.ics"


# ===================================================================
# _normalize_choice
# ===================================================================


class TestNormalizeChoice:
    def test_none_returns_default(self) -> None:
        assert _normalize_choice(None, {"a", "b"}, "a") == "a"

    def test_empty_returns_default(self) -> None:
        assert _normalize_choice("", {"a", "b"}, "a") == "a"

    def test_valid_choice_returned(self) -> None:
        assert _normalize_choice("b", {"a", "b"}, "a") == "b"

    def test_invalid_choice_returns_default(self) -> None:
        assert _normalize_choice("c", {"a", "b"}, "a") == "a"

    def test_case_insensitive(self) -> None:
        assert _normalize_choice("NORMAL", {"normal", "high"}, "normal") == "normal"

    def test_whitespace_stripped(self) -> None:
        assert _normalize_choice("  high  ", {"normal", "high"}, "normal") == "high"


# ===================================================================
# _optional_wyoming_endpoint
# ===================================================================


class TestOptionalWyomingEndpoint:
    def test_host_missing_returns_none(self) -> None:
        result = _optional_wyoming_endpoint({}, host_key="H", port_key="P")
        assert result is None

    def test_port_missing_returns_none(self) -> None:
        source = {"H": "localhost"}
        result = _optional_wyoming_endpoint(source, host_key="H", port_key="P")
        assert result is None

    def test_port_zero_returns_none(self) -> None:
        source = {"H": "localhost", "P": "0"}
        result = _optional_wyoming_endpoint(source, host_key="H", port_key="P")
        assert result is None

    def test_host_and_port_returns_endpoint(self) -> None:
        source = {"H": "localhost", "P": "10300"}
        result = _optional_wyoming_endpoint(source, host_key="H", port_key="P")
        assert result is not None
        assert result.host == "localhost"
        assert result.port == 10300
        assert result.model is None

    def test_with_model_key(self) -> None:
        source = {"H": "localhost", "P": "10300", "M": "tiny-int8"}
        result = _optional_wyoming_endpoint(source, host_key="H", port_key="P", model_key="M")
        assert result is not None
        assert result.model == "tiny-int8"


# ===================================================================
# _resolve_media_player_entity
# ===================================================================


class TestResolveMediaPlayerEntity:
    def test_override_provided(self) -> None:
        result = _resolve_media_player_entity("myhost", "media_player.custom")
        assert result == "media_player.custom"

    def test_override_whitespace_only_uses_hostname(self) -> None:
        result = _resolve_media_player_entity("my-host", "   ")
        assert result == "media_player.my_host"

    def test_no_override_generates_from_hostname(self) -> None:
        result = _resolve_media_player_entity("pulse-office", None)
        assert result == "media_player.pulse_office"

    def test_hostname_dots_sanitized(self) -> None:
        result = _resolve_media_player_entity("pulse.local", None)
        assert result == "media_player.pulse_local"


# ===================================================================
# render_actions_for_prompt
# ===================================================================


class TestRenderActionsForPrompt:
    def test_empty_list(self) -> None:
        assert render_actions_for_prompt([]) == ""

    def test_actions_with_slug_and_description(self) -> None:
        actions = [
            {"slug": "lights_on", "description": "Turn on all lights"},
            {"slug": "fan_off", "description": "Turn off the fan"},
        ]
        result = render_actions_for_prompt(actions)
        assert "- lights_on: Turn on all lights" in result
        assert "- fan_off: Turn off the fan" in result

    def test_action_missing_description(self) -> None:
        actions = [{"slug": "my_action"}]
        result = render_actions_for_prompt(actions)
        assert "- my_action:" in result


# ===================================================================
# MicConfig.bytes_per_chunk
# ===================================================================


class TestMicConfig:
    def test_bytes_per_chunk_standard(self) -> None:
        mic = MicConfig(
            command=["arecord"],
            rate=16000,
            width=2,
            channels=1,
            chunk_ms=30,
        )
        # 16000 * 0.030 = 480 samples; 480 * 2 bytes * 1 channel = 960
        assert mic.bytes_per_chunk == 960

    def test_bytes_per_chunk_stereo(self) -> None:
        mic = MicConfig(
            command=["arecord"],
            rate=16000,
            width=2,
            channels=2,
            chunk_ms=30,
        )
        assert mic.bytes_per_chunk == 1920


# ===================================================================
# AssistantConfig.from_env — integration tests
# ===================================================================


class TestFromEnvBasics:
    def test_default_hostname_from_socket(self) -> None:
        cfg = _from_env()
        # Should fall back to socket.gethostname() since PULSE_HOSTNAME not set
        import socket

        assert cfg.hostname == socket.gethostname()

    def test_custom_hostname(self) -> None:
        cfg = _from_env({"PULSE_HOSTNAME": "my-desk"})
        assert cfg.hostname == "my-desk"

    def test_device_name_from_env(self) -> None:
        cfg = _from_env({"PULSE_NAME": "Office Speaker"})
        assert cfg.device_name == "Office Speaker"

    def test_device_name_fallback_from_hostname(self) -> None:
        cfg = _from_env({"PULSE_HOSTNAME": "office-desk"})
        assert cfg.device_name == "Office Desk"

    def test_default_llm_provider(self) -> None:
        cfg = _from_env()
        assert cfg.llm.provider == "openai"

    def test_custom_llm_provider(self) -> None:
        cfg = _from_env({"PULSE_ASSISTANT_PROVIDER": "Anthropic"})
        assert cfg.llm.provider == "anthropic"

    def test_default_wake_models(self) -> None:
        cfg = _from_env()
        assert DEFAULT_WAKE_MODEL in cfg.wake_models
        assert DEFAULT_HA_WAKE_MODEL in cfg.wake_models


class TestFromEnvMqtt:
    def test_mqtt_user_legacy_key(self) -> None:
        cfg = _from_env({"MQTT_USER": "admin", "MQTT_PASS": "secret"})
        assert cfg.mqtt.username == "admin"
        assert cfg.mqtt.password == "secret"

    def test_mqtt_username_key(self) -> None:
        cfg = _from_env({"MQTT_USERNAME": "admin2", "MQTT_PASSWORD": "secret2"})
        assert cfg.mqtt.username == "admin2"
        assert cfg.mqtt.password == "secret2"

    def test_topic_base_trailing_slash_stripped(self) -> None:
        cfg = _from_env({"PULSE_ASSISTANT_TOPIC_BASE": "pulse/test/"})
        assert cfg.mqtt.topic_base == "pulse/test"
        assert not cfg.mqtt.topic_base.endswith("/")


class TestFromEnvActionFile:
    def test_action_file_exists(self, tmp_path: Path) -> None:
        action_file = tmp_path / "actions.yaml"
        action_file.write_text("actions: []")
        cfg = _from_env({"PULSE_ASSISTANT_ACTIONS_FILE": str(action_file)})
        assert cfg.action_file == action_file

    def test_action_file_missing(self) -> None:
        cfg = _from_env({"PULSE_ASSISTANT_ACTIONS_FILE": "/nonexistent/actions.yaml"})
        assert cfg.action_file is None


class TestFromEnvSystemPrompt:
    def test_system_prompt_from_env(self) -> None:
        cfg = _from_env({"PULSE_ASSISTANT_SYSTEM_PROMPT": "Be nice."})
        assert cfg.llm.system_prompt == "Be nice."

    def test_system_prompt_from_file(self, tmp_path: Path) -> None:
        prompt_file = tmp_path / "prompt.txt"
        prompt_file.write_text("  From file prompt  ")
        cfg = _from_env({"PULSE_ASSISTANT_SYSTEM_PROMPT_FILE": str(prompt_file)})
        assert cfg.llm.system_prompt == "From file prompt"

    def test_system_prompt_env_takes_precedence_over_file(self, tmp_path: Path) -> None:
        prompt_file = tmp_path / "prompt.txt"
        prompt_file.write_text("File prompt")
        cfg = _from_env(
            {
                "PULSE_ASSISTANT_SYSTEM_PROMPT": "Env prompt",
                "PULSE_ASSISTANT_SYSTEM_PROMPT_FILE": str(prompt_file),
            }
        )
        assert cfg.llm.system_prompt == "Env prompt"

    def test_system_prompt_default_fallback(self) -> None:
        cfg = _from_env()
        assert "Pulse" in cfg.llm.system_prompt
        assert len(cfg.llm.system_prompt) > 50


class TestFromEnvCalendar:
    def test_calendar_feeds_with_webcal(self) -> None:
        cfg = _from_env(
            {
                "PULSE_CALENDAR_ICS_URLS": "webcal://cal.example.com/a.ics,https://cal.example.com/b.ics",
            }
        )
        assert cfg.calendar.enabled is True
        assert len(cfg.calendar.feeds) == 2
        assert cfg.calendar.feeds[0] == "https://cal.example.com/a.ics"
        assert cfg.calendar.feeds[1] == "https://cal.example.com/b.ics"

    def test_calendar_disabled_when_no_feeds(self) -> None:
        cfg = _from_env()
        assert cfg.calendar.enabled is False
        assert cfg.calendar.feeds == ()

    def test_calendar_notifications_valid(self) -> None:
        cfg = _from_env({"PULSE_CALENDAR_DEFAULT_NOTIFICATIONS": "10,5,2"})
        assert cfg.calendar.default_notifications == (10, 5, 2)

    def test_calendar_notifications_non_digits_ignored(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING, logger="pulse.config"):
            cfg = _from_env({"PULSE_CALENDAR_DEFAULT_NOTIFICATIONS": "abc,xyz"})
        assert cfg.calendar.default_notifications == ()
        assert "no valid values" in caplog.text.lower()

    def test_calendar_notifications_empty_string(self) -> None:
        cfg = _from_env({"PULSE_CALENDAR_DEFAULT_NOTIFICATIONS": ""})
        assert cfg.calendar.default_notifications == ()


class TestFromEnvWorkPause:
    def test_skip_dates_valid(self) -> None:
        cfg = _from_env({"PULSE_WORK_ALARM_SKIP_DATES": "2026-01-01,2026-12-25"})
        assert cfg.work_pause.skip_dates == ("2026-01-01", "2026-12-25")

    def test_skip_dates_invalid_formats_skipped(self) -> None:
        cfg = _from_env({"PULSE_WORK_ALARM_SKIP_DATES": "2026-01-01,not-a-date,2026/12/25"})
        assert cfg.work_pause.skip_dates == ("2026-01-01",)

    def test_skip_weekdays_numbers(self) -> None:
        cfg = _from_env({"PULSE_WORK_ALARM_SKIP_DAYS": "5,6"})
        assert cfg.work_pause.skip_weekdays == (5, 6)

    def test_skip_weekdays_day_names(self) -> None:
        cfg = _from_env({"PULSE_WORK_ALARM_SKIP_DAYS": "sat,sun"})
        assert cfg.work_pause.skip_weekdays == (5, 6)

    def test_skip_weekdays_mixed(self) -> None:
        cfg = _from_env({"PULSE_WORK_ALARM_SKIP_DAYS": "0,friday,6"})
        assert set(cfg.work_pause.skip_weekdays) == {0, 4, 6}


class TestFromEnvMediaPlayer:
    def test_media_player_entity_deduplication(self) -> None:
        cfg = _from_env(
            {
                "PULSE_HOSTNAME": "office",
                "PULSE_MEDIA_PLAYER_ENTITIES": "media_player.office,media_player.kitchen",
            }
        )
        assert cfg.media_player_entity == "media_player.office"
        # office entity appears in both primary and extra list; should be deduplicated
        assert cfg.media_player_entities.count("media_player.office") == 1
        assert "media_player.kitchen" in cfg.media_player_entities

    def test_media_player_override(self) -> None:
        cfg = _from_env(
            {
                "PULSE_HOSTNAME": "office",
                "PULSE_MEDIA_PLAYER_ENTITY": "media_player.custom_speaker",
            }
        )
        assert cfg.media_player_entity == "media_player.custom_speaker"


class TestFromEnvClamping:
    def test_self_audio_trigger_level_minimum_clamped(self) -> None:
        cfg = _from_env({"PULSE_ASSISTANT_SELF_AUDIO_TRIGGER_LEVEL": "1"})
        assert cfg.self_audio_trigger_level >= 2

    def test_self_audio_trigger_level_zero_clamped(self) -> None:
        cfg = _from_env({"PULSE_ASSISTANT_SELF_AUDIO_TRIGGER_LEVEL": "0"})
        assert cfg.self_audio_trigger_level == 2

    def test_weather_forecast_days_clamped_low(self) -> None:
        cfg = _from_env({"PULSE_WEATHER_FORECAST_DAYS": "0"})
        assert cfg.info.weather.forecast_days == 1

    def test_weather_forecast_days_clamped_high(self) -> None:
        cfg = _from_env({"PULSE_WEATHER_FORECAST_DAYS": "10"})
        assert cfg.info.weather.forecast_days == 5

    def test_weather_forecast_days_normal(self) -> None:
        cfg = _from_env({"PULSE_WEATHER_FORECAST_DAYS": "3"})
        assert cfg.info.weather.forecast_days == 3

    def test_news_max_articles_minimum_one(self) -> None:
        cfg = _from_env({"PULSE_NEWS_MAX_ARTICLES": "0"})
        assert cfg.info.news.max_articles == 1

    def test_news_max_articles_negative_clamped(self) -> None:
        cfg = _from_env({"PULSE_NEWS_MAX_ARTICLES": "-5"})
        assert cfg.info.news.max_articles == 1


class TestFromEnvTopics:
    def test_default_topics_use_hostname(self) -> None:
        cfg = _from_env({"PULSE_HOSTNAME": "testhost"})
        assert cfg.transcript_topic == "pulse/testhost/assistant/transcript"
        assert cfg.response_topic == "pulse/testhost/assistant/response"
        assert cfg.state_topic == "pulse/testhost/assistant/state"
        assert cfg.action_topic == "pulse/testhost/assistant/actions"

    def test_custom_topic_base(self) -> None:
        cfg = _from_env({"PULSE_ASSISTANT_TOPIC_BASE": "custom/base"})
        assert cfg.transcript_topic == "custom/base/transcript"
