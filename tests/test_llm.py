"""Tests for LLM providers."""

from __future__ import annotations

import json
from unittest.mock import Mock, patch

import pytest
from pulse.assistant.config import LLMConfig
from pulse.assistant.llm import (
    LLMResult,
    OpenAIProvider,
    _format_system_prompt,
    _parse_llm_response,
    build_llm_provider,
)

# Mark async tests in this module to use anyio
pytestmark = pytest.mark.anyio


def make_llm_config(**overrides):
    """Create a complete LLMConfig with defaults."""
    defaults = {
        "provider": "openai",
        "system_prompt": "You are a helpful assistant.",
        "openai_model": "gpt-4",
        "openai_api_key": "test_key",
        "openai_base_url": "https://api.openai.com/v1",
        "openai_timeout": 30,
        "gemini_model": "gemini-pro",
        "gemini_api_key": None,
        "gemini_base_url": "https://generativelanguage.googleapis.com/v1beta",
        "gemini_timeout": 30,
    }
    defaults.update(overrides)
    return LLMConfig(**defaults)


class TestLLMResultParsing:
    """Test LLM response parsing."""

    def test_parse_valid_json(self):
        """Test parsing valid JSON response."""
        response = json.dumps(
            {
                "response": "I've turned on the lights",
                "actions": ["turn_on_lights"],
                "follow_up": False,
            }
        )
        result = _parse_llm_response(response)
        assert result.response == "I've turned on the lights"
        assert result.actions == ["turn_on_lights"]
        assert result.follow_up is False

    def test_parse_json_with_follow_up(self):
        """Test parsing JSON with follow_up flag."""
        response = json.dumps(
            {
                "response": "Which room?",
                "actions": [],
                "follow_up": True,
            }
        )
        result = _parse_llm_response(response)
        assert result.response == "Which room?"
        assert result.actions == []
        assert result.follow_up is True

    def test_parse_json_multiple_actions(self):
        """Test parsing JSON with multiple actions."""
        response = json.dumps(
            {
                "response": "Setting the mood",
                "actions": ["dim_lights", "play_music"],
            }
        )
        result = _parse_llm_response(response)
        assert result.actions == ["dim_lights", "play_music"]

    def test_parse_json_filters_empty_actions(self):
        """Test that empty action strings are filtered out."""
        response = json.dumps(
            {
                "response": "Done",
                "actions": ["turn_on_lights", "", "activate_scene"],
            }
        )
        result = _parse_llm_response(response)
        assert result.actions == ["turn_on_lights", "activate_scene"]

    def test_parse_json_filters_non_string_actions(self):
        """Test that non-string actions are filtered out."""
        response = json.dumps(
            {
                "response": "Done",
                "actions": ["turn_on_lights", 123, None, "activate_scene"],
            }
        )
        result = _parse_llm_response(response)
        assert result.actions == ["turn_on_lights", "activate_scene"]

    def test_parse_plain_text_fallback(self):
        """Test parsing plain text when JSON parsing fails."""
        response = "I've turned on the lights"
        result = _parse_llm_response(response)
        assert result.response == "I've turned on the lights"
        assert result.actions == []
        assert result.follow_up is False

    def test_parse_invalid_json_fallback(self):
        """Test fallback when JSON is invalid."""
        response = '{"response": "Incomplete JSON'
        result = _parse_llm_response(response)
        assert result.response == '{"response": "Incomplete JSON'
        assert result.actions == []

    def test_parse_json_missing_response(self):
        """Test parsing JSON without response field."""
        response = json.dumps({"actions": ["turn_on_lights"]})
        result = _parse_llm_response(response)
        # Falls back to original text when response is missing/empty
        assert result.response == response
        assert result.actions == ["turn_on_lights"]

    def test_parse_json_empty_response(self):
        """Test parsing JSON with empty response."""
        original = json.dumps({"response": "", "actions": []})
        result = _parse_llm_response(original)
        # Falls back to original when response is empty
        assert result.response == original
        assert result.actions == []

    def test_parse_json_whitespace_handling(self):
        """Test that whitespace is properly stripped."""
        response = json.dumps(
            {
                "response": "  Hello there  ",
                "actions": [],
            }
        )
        result = _parse_llm_response(response)
        assert result.response == "Hello there"


class TestSystemPromptFormatting:
    """Test system prompt formatting."""

    def test_format_system_prompt_with_actions(self):
        """Test formatting system prompt with actions."""
        config = make_llm_config()
        actions = [
            {"slug": "turn_on_lights", "description": "Turn on the lights"},
            {"slug": "play_music", "description": "Play music"},
        ]
        prompt = _format_system_prompt(config, actions)
        assert "You are a helpful assistant." in prompt
        assert "turn_on_lights: Turn on the lights" in prompt
        assert "play_music: Play music" in prompt
        assert "Always respond **only** with JSON" in prompt

    def test_format_system_prompt_no_actions(self):
        """Test formatting system prompt without actions."""
        config = make_llm_config()
        prompt = _format_system_prompt(config, [])
        assert "You are a helpful assistant." in prompt
        assert "(no device actions are currently available)" in prompt

    def test_format_system_prompt_filters_actions_without_slug(self):
        """Test that actions without slug are filtered out."""
        config = make_llm_config()
        actions = [
            {"slug": "turn_on_lights", "description": "Turn on the lights"},
            {"description": "No slug here"},
        ]
        prompt = _format_system_prompt(config, actions)
        assert "turn_on_lights" in prompt
        assert "No slug here" not in prompt


class TestOpenAIProvider:
    """Test OpenAI provider."""

    @pytest.fixture
    def llm_config(self):
        """Create a test LLM configuration."""
        return make_llm_config(openai_api_key="test_key_123")

    def test_init(self, llm_config):
        """Test OpenAI provider initialization."""
        provider = OpenAIProvider(llm_config)
        assert provider.config == llm_config

    def test_build_payload(self, llm_config):
        """Test building API payload."""
        provider = OpenAIProvider(llm_config)
        actions = [
            {"slug": "turn_on_lights", "description": "Turn on the lights"},
        ]
        payload = provider._build_payload("Turn on the lights", actions)

        assert payload["model"] == "gpt-4"
        assert len(payload["messages"]) == 2
        assert payload["messages"][0]["role"] == "system"
        assert payload["messages"][1]["role"] == "user"
        assert payload["messages"][1]["content"] == "Turn on the lights"
        assert "turn_on_lights" in payload["messages"][0]["content"]

    async def test_generate_success(self, llm_config):
        """Test successful LLM generation."""
        provider = OpenAIProvider(llm_config)
        response_json = json.dumps(
            {
                "response": "I've turned on the lights",
                "actions": ["turn_on_lights"],
            }
        )

        with patch.object(provider, "_call_api", return_value=response_json):
            result = await provider.generate(
                "Turn on the lights",
                [{"slug": "turn_on_lights", "description": "Turn on the lights"}],
            )

        assert result.response == "I've turned on the lights"
        assert result.actions == ["turn_on_lights"]

    async def test_generate_handles_exception(self, llm_config):
        """Test that exceptions are handled gracefully."""
        provider = OpenAIProvider(llm_config)

        with patch.object(provider, "_call_api", side_effect=Exception("API error")):
            result = await provider.generate(
                "Turn on the lights",
                [{"slug": "turn_on_lights", "description": "Turn on the lights"}],
            )

        assert "error" in result.response.lower()
        assert result.actions == []

    def test_call_api_headers(self, llm_config):
        """Test that API call includes proper headers."""
        provider = OpenAIProvider(llm_config)
        payload = {"messages": [{"role": "user", "content": "test"}]}

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_response = Mock()
            mock_response.read.return_value = json.dumps(
                {"choices": [{"message": {"content": '{"response": "test"}'}}]}
            ).encode()
            mock_urlopen.return_value.__enter__.return_value = mock_response

            provider._call_api(payload)

            call_args = mock_urlopen.call_args[0][0]
            assert call_args.get_header("Authorization") == "Bearer test_key_123"
            assert call_args.get_header("Content-type") == "application/json"


class TestBuildLLMProvider:
    """Test LLM provider factory."""

    def test_build_openai_provider(self):
        """Test building OpenAI provider."""
        config = make_llm_config(provider="openai")
        provider = build_llm_provider(config)
        assert isinstance(provider, OpenAIProvider)

    def test_build_gemini_provider(self):
        """Test building Gemini provider."""
        config = make_llm_config(provider="gemini", gemini_api_key="test_key")
        provider = build_llm_provider(config)
        # Check that it returns some provider (implementation may vary)
        assert provider is not None


class TestLLMResult:
    """Test LLMResult dataclass."""

    def test_llm_result_defaults(self):
        """Test LLMResult default values."""
        result = LLMResult(response="Hello", actions=["test"])
        assert result.response == "Hello"
        assert result.actions == ["test"]
        assert result.follow_up is False

    def test_llm_result_with_follow_up(self):
        """Test LLMResult with follow_up."""
        result = LLMResult(response="Question?", actions=[], follow_up=True)
        assert result.follow_up is True
