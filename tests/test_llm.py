"""Tests for LLM providers."""

from __future__ import annotations

import json
import urllib.error
from io import BytesIO
from unittest.mock import Mock, patch

import pytest
from pulse.assistant.config import LLMConfig
from pulse.assistant.llm import (
    AnthropicProvider,
    GeminiProvider,
    GroqProvider,
    LLMResult,
    MistralProvider,
    OpenAIProvider,
    OpenRouterProvider,
    _error_response,
    _extract_first_json_object,
    _format_system_prompt,
    _parse_llm_response,
    build_llm_provider,
    build_llm_provider_with_overrides,
    get_supported_providers,
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
        "anthropic_model": "claude-3-5-haiku-20241022",
        "anthropic_api_key": None,
        "anthropic_base_url": "https://api.anthropic.com/v1",
        "anthropic_timeout": 45,
        "groq_model": "llama-3.3-70b-versatile",
        "groq_api_key": None,
        "groq_base_url": "https://api.groq.com/openai/v1",
        "groq_timeout": 30,
        "mistral_model": "mistral-small-latest",
        "mistral_api_key": None,
        "mistral_base_url": "https://api.mistral.ai/v1",
        "mistral_timeout": 45,
        "openrouter_model": "meta-llama/llama-3.3-70b-instruct",
        "openrouter_api_key": None,
        "openrouter_base_url": "https://openrouter.ai/api/v1",
        "openrouter_timeout": 45,
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

    def test_parse_embedded_json_in_prose(self):
        """Test extraction of JSON embedded in natural-language wrapper text."""
        inner = '{"response": "The lights are on", "actions": ["turn_on_lights"], "follow_up": true}'
        response = f"Here is my json response: {inner}"
        result = _parse_llm_response(response)
        assert result.response == "The lights are on"
        assert result.actions == ["turn_on_lights"]
        assert result.follow_up is True

    def test_parse_embedded_json_with_trailing_text(self):
        """Test extraction of JSON with text before and after."""
        response = 'Sure! {"response": "Done", "actions": []} Hope that helps!'
        result = _parse_llm_response(response)
        assert result.response == "Done"
        assert result.actions == []

    def test_parse_non_dict_json_fallback(self):
        """Test fallback when JSON parses to a non-dict type."""
        result = _parse_llm_response("[1, 2, 3]")
        assert result.response == "[1, 2, 3]"
        assert result.actions == []

    def test_parse_json_string_fallback(self):
        """Test fallback when JSON parses to a plain string."""
        result = _parse_llm_response('"just a string"')
        assert result.response == '"just a string"'
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


class TestExtractFirstJsonObject:
    """Test _extract_first_json_object helper."""

    def test_extracts_simple_object(self):
        result = _extract_first_json_object('blah {"key": "val"} blah')
        assert result == {"key": "val"}

    def test_extracts_first_object_when_multiple(self):
        result = _extract_first_json_object('{"a": 1} {"b": 2}')
        assert result == {"a": 1}

    def test_returns_none_for_no_json(self):
        assert _extract_first_json_object("no json here") is None

    def test_returns_none_for_empty_string(self):
        assert _extract_first_json_object("") is None

    def test_skips_invalid_brace_and_finds_valid(self):
        result = _extract_first_json_object('{bad {"response": "ok"}')
        assert result == {"response": "ok"}

    def test_skips_json_array(self):
        """Arrays are valid JSON but not dicts — should be skipped."""
        assert _extract_first_json_object("[1, 2, 3]") is None


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


class TestBuildLLMProviderWithOverrides:
    """Test LLM provider factory with preference overrides."""

    def test_applies_provider_override(self):
        config = make_llm_config(provider="openai", gemini_api_key="test_key")
        provider = build_llm_provider_with_overrides(config, "gemini", {})
        from pulse.assistant.llm import GeminiProvider

        assert isinstance(provider, GeminiProvider)

    def test_applies_model_override(self):
        config = make_llm_config(provider="openai")
        provider = build_llm_provider_with_overrides(config, "openai", {"openai": "gpt-4o-mini"})
        assert isinstance(provider, OpenAIProvider)
        assert provider.config.openai_model == "gpt-4o-mini"

    def test_falls_back_to_base_model_when_override_is_none(self):
        config = make_llm_config(provider="openai", openai_model="gpt-4")
        provider = build_llm_provider_with_overrides(config, "openai", {"openai": None})
        assert provider.config.openai_model == "gpt-4"

    def test_multiple_model_overrides(self):
        config = make_llm_config(provider="groq", groq_api_key="test_key")
        overrides = {"groq": "llama-custom", "openai": "gpt-custom"}
        provider = build_llm_provider_with_overrides(config, "groq", overrides)
        assert provider.config.groq_model == "llama-custom"
        assert provider.config.openai_model == "gpt-custom"


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


class TestValidateApiKey:
    """Test API key validation for all providers."""

    async def test_openai_missing_key(self):
        config = make_llm_config(openai_api_key=None)
        provider = OpenAIProvider(config)
        assert await provider.validate_api_key() is False

    @patch("pulse.assistant.llm.urllib.request.urlopen")
    async def test_openai_valid_key(self, mock_urlopen):
        mock_resp = Mock()
        mock_resp.read.return_value = b'{"data": []}'
        mock_resp.__enter__ = Mock(return_value=mock_resp)
        mock_resp.__exit__ = Mock(return_value=False)
        mock_urlopen.return_value = mock_resp

        config = make_llm_config(openai_api_key="valid-key")
        provider = OpenAIProvider(config)
        assert await provider.validate_api_key() is True

    @patch("pulse.assistant.llm.urllib.request.urlopen")
    async def test_openai_invalid_key_401(self, mock_urlopen):
        mock_urlopen.side_effect = urllib.error.HTTPError(
            url="", code=401, msg="Unauthorized", hdrs=None, fp=None  # type: ignore[arg-type]
        )
        config = make_llm_config(openai_api_key="bad-key")
        provider = OpenAIProvider(config)
        assert await provider.validate_api_key() is False

    @patch("pulse.assistant.llm.urllib.request.urlopen")
    async def test_openai_rate_limit_429_is_inconclusive(self, mock_urlopen):
        mock_urlopen.side_effect = urllib.error.HTTPError(
            url="", code=429, msg="Too Many Requests", hdrs=None, fp=None  # type: ignore[arg-type]
        )
        config = make_llm_config(openai_api_key="valid-key")
        provider = OpenAIProvider(config)
        assert await provider.validate_api_key() is True

    @patch("pulse.assistant.llm.urllib.request.urlopen")
    async def test_openai_network_error_is_inconclusive(self, mock_urlopen):
        mock_urlopen.side_effect = urllib.error.URLError("Network unreachable")
        config = make_llm_config(openai_api_key="valid-key")
        provider = OpenAIProvider(config)
        assert await provider.validate_api_key() is True

    async def test_anthropic_missing_key(self):
        config = make_llm_config(anthropic_api_key=None)
        provider = AnthropicProvider(config)
        assert await provider.validate_api_key() is False

    @patch("pulse.assistant.llm.urllib.request.urlopen")
    async def test_anthropic_invalid_key_403(self, mock_urlopen):
        mock_urlopen.side_effect = urllib.error.HTTPError(
            url="", code=403, msg="Forbidden", hdrs=None, fp=None  # type: ignore[arg-type]
        )
        config = make_llm_config(anthropic_api_key="bad-key")
        provider = AnthropicProvider(config)
        assert await provider.validate_api_key() is False

    async def test_gemini_missing_key(self):
        config = make_llm_config(gemini_api_key=None)
        provider = GeminiProvider(config)
        assert await provider.validate_api_key() is False

    @patch("pulse.assistant.llm.urllib.request.urlopen")
    async def test_gemini_server_error_is_inconclusive(self, mock_urlopen):
        mock_urlopen.side_effect = urllib.error.HTTPError(
            url="", code=500, msg="Internal Server Error", hdrs=None, fp=None  # type: ignore[arg-type]
        )
        config = make_llm_config(gemini_api_key="valid-key")
        provider = GeminiProvider(config)
        assert await provider.validate_api_key() is True


class TestErrorResponse:
    """Test _error_response capacity detection."""

    @pytest.mark.parametrize(
        "msg",
        [
            "Anthropic HTTP 529: overloaded",
            "OpenAI HTTP error: 429",
            "Gemini HTTP error: 503",
        ],
    )
    def test_capacity_errors_return_overloaded_message(self, msg):
        result = _error_response(RuntimeError(msg))
        assert "at capacity" in result
        assert "Try again" in result

    def test_generic_error_returns_default_message(self):
        result = _error_response(RuntimeError("something broke"))
        assert "Sorry" in result
        assert "at capacity" not in result


# ============================================================================
# Additional coverage tests
# ============================================================================


class TestProviderSubclassAccessors:
    """Test that each OpenAI-compatible subclass returns the correct config values."""

    def test_groq_accessors(self):
        config = make_llm_config(groq_api_key="gk", groq_model="llama-x", groq_timeout=10)
        p = GroqProvider(config)
        assert p._get_api_key() == "gk"
        assert p._get_model() == "llama-x"
        assert p._get_base_url() == "https://api.groq.com/openai/v1"
        assert p._get_timeout() == 10
        assert p._get_provider_name() == "Groq"

    def test_mistral_accessors(self):
        config = make_llm_config(mistral_api_key="mk", mistral_model="mistral-x", mistral_timeout=20)
        p = MistralProvider(config)
        assert p._get_api_key() == "mk"
        assert p._get_model() == "mistral-x"
        assert p._get_base_url() == "https://api.mistral.ai/v1"
        assert p._get_timeout() == 20
        assert p._get_provider_name() == "Mistral"

    def test_openrouter_accessors(self):
        config = make_llm_config(openrouter_api_key="ork", openrouter_model="or-model", openrouter_timeout=15)
        p = OpenRouterProvider(config)
        assert p._get_api_key() == "ork"
        assert p._get_model() == "or-model"
        assert p._get_base_url() == "https://openrouter.ai/api/v1"
        assert p._get_timeout() == 15
        assert p._get_provider_name() == "OpenRouter"

    def test_openai_accessors(self):
        config = make_llm_config(openai_api_key="oak", openai_model="gpt-x", openai_timeout=25)
        p = OpenAIProvider(config)
        assert p._get_api_key() == "oak"
        assert p._get_model() == "gpt-x"
        assert p._get_base_url() == "https://api.openai.com/v1"
        assert p._get_timeout() == 25
        assert p._get_provider_name() == "OpenAI"


class TestOpenAICompatibleCallApi:
    """Test _call_api for the OpenAI-compatible base class."""

    def _make_provider(self, **overrides):
        config = make_llm_config(**overrides)
        return OpenAIProvider(config)

    def test_call_api_missing_api_key(self):
        provider = self._make_provider(openai_api_key=None)
        with pytest.raises(RuntimeError, match="OPENAI_API_KEY is not set"):
            provider._call_api({"messages": []})

    @patch("pulse.assistant.llm.urllib.request.urlopen")
    def test_call_api_success(self, mock_urlopen):
        mock_resp = Mock()
        mock_resp.read.return_value = json.dumps({"choices": [{"message": {"content": "hello"}}]}).encode()
        mock_resp.__enter__ = Mock(return_value=mock_resp)
        mock_resp.__exit__ = Mock(return_value=False)
        mock_urlopen.return_value = mock_resp

        provider = self._make_provider(openai_api_key="key")
        result = provider._call_api({"messages": []})
        assert result == "hello"

    @patch("pulse.assistant.llm.urllib.request.urlopen")
    def test_call_api_http_error(self, mock_urlopen):
        mock_urlopen.side_effect = urllib.error.HTTPError(
            url="", code=500, msg="Server Error", hdrs=None, fp=None  # type: ignore[arg-type]
        )
        provider = self._make_provider(openai_api_key="key")
        with pytest.raises(RuntimeError, match="OpenAI HTTP error: 500"):
            provider._call_api({"messages": []})

    @patch("pulse.assistant.llm.urllib.request.urlopen")
    def test_call_api_missing_choices(self, mock_urlopen):
        mock_resp = Mock()
        mock_resp.read.return_value = json.dumps({"choices": []}).encode()
        mock_resp.__enter__ = Mock(return_value=mock_resp)
        mock_resp.__exit__ = Mock(return_value=False)
        mock_urlopen.return_value = mock_resp

        provider = self._make_provider(openai_api_key="key")
        with pytest.raises(RuntimeError, match="missing choices"):
            provider._call_api({"messages": []})

    @patch("pulse.assistant.llm.urllib.request.urlopen")
    def test_call_api_missing_content(self, mock_urlopen):
        mock_resp = Mock()
        mock_resp.read.return_value = json.dumps({"choices": [{"message": {}}]}).encode()
        mock_resp.__enter__ = Mock(return_value=mock_resp)
        mock_resp.__exit__ = Mock(return_value=False)
        mock_urlopen.return_value = mock_resp

        provider = self._make_provider(openai_api_key="key")
        with pytest.raises(RuntimeError, match="missing content"):
            provider._call_api({"messages": []})

    @patch("pulse.assistant.llm.urllib.request.urlopen")
    def test_call_api_custom_timeout(self, mock_urlopen):
        """Verify the custom timeout kwarg is forwarded."""
        mock_resp = Mock()
        mock_resp.read.return_value = json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode()
        mock_resp.__enter__ = Mock(return_value=mock_resp)
        mock_resp.__exit__ = Mock(return_value=False)
        mock_urlopen.return_value = mock_resp

        provider = self._make_provider(openai_api_key="key")
        provider._call_api({"messages": []}, timeout=7)
        _, kwargs = mock_urlopen.call_args
        assert kwargs["timeout"] == 7


class TestOpenAICompatibleBuildPayload:
    """Test _build_payload for OpenAI-compatible providers."""

    def test_payload_structure(self):
        config = make_llm_config(openai_api_key="key", openai_model="gpt-4o")
        provider = OpenAIProvider(config)
        payload = provider._build_payload("Hello", [])
        assert payload["model"] == "gpt-4o"
        assert payload["temperature"] == 0.3
        assert payload["max_tokens"] == 400
        assert payload["response_format"] == {"type": "json_object"}
        assert len(payload["messages"]) == 2
        assert payload["messages"][0]["role"] == "system"
        assert payload["messages"][1]["content"] == "Hello"


class TestOpenAICompatibleGenerate:
    """Test generate() for OpenAI-compatible providers."""

    async def test_generate_success(self):
        config = make_llm_config(openai_api_key="key")
        provider = OpenAIProvider(config)
        resp = json.dumps({"response": "hi", "actions": ["a1"]})
        with patch.object(provider, "_call_api", return_value=resp):
            result = await provider.generate("hello", [])
        assert result.response == "hi"
        assert result.actions == ["a1"]

    async def test_generate_exception_capacity(self):
        config = make_llm_config(openai_api_key="key")
        provider = OpenAIProvider(config)
        with patch.object(provider, "_call_api", side_effect=RuntimeError("OpenAI HTTP error: 429")):
            result = await provider.generate("hello", [])
        assert "at capacity" in result.response
        assert result.actions == []

    async def test_generate_exception_generic(self):
        config = make_llm_config(openai_api_key="key")
        provider = OpenAIProvider(config)
        with patch.object(provider, "_call_api", side_effect=RuntimeError("connection reset")):
            result = await provider.generate("hello", [])
        assert "Sorry" in result.response


class TestOpenAICompatibleSimpleChat:
    """Test simple_chat() for OpenAI-compatible providers."""

    async def test_simple_chat(self):
        config = make_llm_config(openai_api_key="key")
        provider = OpenAIProvider(config)
        with patch.object(provider, "_call_api", return_value="response text") as mock_call:
            result = await provider.simple_chat("sys prompt", "user msg", timeout=3)
        assert result == "response text"
        call_payload = mock_call.call_args[0][0]
        assert call_payload["messages"][0]["content"] == "sys prompt"
        assert call_payload["messages"][1]["content"] == "user msg"
        assert call_payload["max_tokens"] == 200
        assert mock_call.call_args[1]["timeout"] == 3


class TestOpenAICompatibleValidateApiKey:
    """Test validate_api_key for OpenAI-compatible providers (extra cases)."""

    @patch("pulse.assistant.llm.urllib.request.urlopen")
    async def test_403_returns_false(self, mock_urlopen):
        mock_urlopen.side_effect = urllib.error.HTTPError(
            url="", code=403, msg="Forbidden", hdrs=None, fp=None  # type: ignore[arg-type]
        )
        config = make_llm_config(openai_api_key="bad")
        provider = OpenAIProvider(config)
        assert await provider.validate_api_key() is False

    @patch("pulse.assistant.llm.urllib.request.urlopen")
    async def test_500_is_inconclusive(self, mock_urlopen):
        mock_urlopen.side_effect = urllib.error.HTTPError(
            url="", code=500, msg="ISE", hdrs=None, fp=None  # type: ignore[arg-type]
        )
        config = make_llm_config(openai_api_key="key")
        provider = OpenAIProvider(config)
        assert await provider.validate_api_key() is True


class TestAnthropicProvider:
    """Test Anthropic-specific provider behaviour."""

    def _make_provider(self, **overrides):
        defaults = {"provider": "anthropic", "anthropic_api_key": "ant-key"}
        defaults.update(overrides)
        config = make_llm_config(**defaults)
        return AnthropicProvider(config)

    def test_build_payload_structure(self):
        provider = self._make_provider()
        payload = provider._build_payload("Hello", [{"slug": "a1", "description": "Action 1"}])
        assert payload["model"] == "claude-3-5-haiku-20241022"
        assert payload["max_tokens"] == 400
        assert "system" in payload
        assert "a1" in payload["system"]
        assert len(payload["messages"]) == 1
        assert payload["messages"][0]["role"] == "user"
        assert payload["messages"][0]["content"] == "Hello"

    def test_call_api_missing_key(self):
        provider = self._make_provider(anthropic_api_key=None)
        with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY is not set"):
            provider._call_api({"messages": []})

    @patch("pulse.assistant.llm.urllib.request.urlopen")
    def test_call_api_success(self, mock_urlopen):
        mock_resp = Mock()
        mock_resp.read.return_value = json.dumps({"content": [{"type": "text", "text": "hello from claude"}]}).encode()
        mock_resp.__enter__ = Mock(return_value=mock_resp)
        mock_resp.__exit__ = Mock(return_value=False)
        mock_urlopen.return_value = mock_resp

        provider = self._make_provider()
        result = provider._call_api({"messages": []})
        assert result == "hello from claude"

    @patch("pulse.assistant.llm.urllib.request.urlopen")
    def test_call_api_http_error_with_body(self, mock_urlopen):
        err = urllib.error.HTTPError(
            url="", code=529, msg="Overloaded", hdrs=None, fp=BytesIO(b"overloaded")  # type: ignore[arg-type]
        )
        mock_urlopen.side_effect = err
        provider = self._make_provider()
        with pytest.raises(RuntimeError, match="Anthropic HTTP 529"):
            provider._call_api({"messages": []})

    @patch("pulse.assistant.llm.urllib.request.urlopen")
    def test_call_api_missing_content(self, mock_urlopen):
        mock_resp = Mock()
        mock_resp.read.return_value = json.dumps({"content": []}).encode()
        mock_resp.__enter__ = Mock(return_value=mock_resp)
        mock_resp.__exit__ = Mock(return_value=False)
        mock_urlopen.return_value = mock_resp

        provider = self._make_provider()
        with pytest.raises(RuntimeError, match="missing content"):
            provider._call_api({"messages": []})

    @patch("pulse.assistant.llm.urllib.request.urlopen")
    def test_call_api_skips_non_text_blocks(self, mock_urlopen):
        mock_resp = Mock()
        mock_resp.read.return_value = json.dumps(
            {"content": [{"type": "image", "data": "..."}, {"type": "text", "text": "actual"}]}
        ).encode()
        mock_resp.__enter__ = Mock(return_value=mock_resp)
        mock_resp.__exit__ = Mock(return_value=False)
        mock_urlopen.return_value = mock_resp

        provider = self._make_provider()
        assert provider._call_api({"messages": []}) == "actual"

    async def test_generate_success(self):
        provider = self._make_provider()
        resp = json.dumps({"response": "done", "actions": ["a"]})
        with patch.object(provider, "_call_api", return_value=resp):
            result = await provider.generate("do it", [])
        assert result.response == "done"
        assert result.actions == ["a"]

    async def test_generate_exception(self):
        provider = self._make_provider()
        with patch.object(provider, "_call_api", side_effect=RuntimeError("Anthropic HTTP 529: overloaded")):
            result = await provider.generate("do it", [])
        assert "at capacity" in result.response

    async def test_simple_chat(self):
        provider = self._make_provider()
        with patch.object(provider, "_call_api", return_value="chat reply") as mock_call:
            result = await provider.simple_chat("sys", "user", timeout=4)
        assert result == "chat reply"
        payload = mock_call.call_args[0][0]
        assert payload["system"] == "sys"
        assert payload["messages"][0]["content"] == "user"
        assert mock_call.call_args[1]["timeout"] == 4

    @patch("pulse.assistant.llm.urllib.request.urlopen")
    async def test_validate_valid_key(self, mock_urlopen):
        mock_resp = Mock()
        mock_resp.read.return_value = b'{"data": []}'
        mock_resp.__enter__ = Mock(return_value=mock_resp)
        mock_resp.__exit__ = Mock(return_value=False)
        mock_urlopen.return_value = mock_resp

        provider = self._make_provider()
        assert await provider.validate_api_key() is True

    @patch("pulse.assistant.llm.urllib.request.urlopen")
    async def test_validate_401(self, mock_urlopen):
        mock_urlopen.side_effect = urllib.error.HTTPError(
            url="", code=401, msg="Unauthorized", hdrs=None, fp=None  # type: ignore[arg-type]
        )
        provider = self._make_provider()
        assert await provider.validate_api_key() is False

    @patch("pulse.assistant.llm.urllib.request.urlopen")
    async def test_validate_network_error(self, mock_urlopen):
        mock_urlopen.side_effect = urllib.error.URLError("timeout")
        provider = self._make_provider()
        assert await provider.validate_api_key() is True


class TestGeminiProvider:
    """Test Gemini-specific provider behaviour."""

    def _make_provider(self, **overrides):
        defaults = {"provider": "gemini", "gemini_api_key": "gem-key", "gemini_model": "gemini-pro"}
        defaults.update(overrides)
        config = make_llm_config(**defaults)
        return GeminiProvider(config)

    def test_build_payload_structure(self):
        provider = self._make_provider()
        payload = provider._build_payload("Hello", [{"slug": "a1", "description": "D"}])
        assert "contents" in payload
        assert payload["contents"][0]["role"] == "user"
        assert payload["contents"][0]["parts"][0]["text"] == "Hello"
        assert "system_instruction" in payload
        assert "a1" in payload["system_instruction"]["parts"][0]["text"]
        assert payload["generationConfig"]["temperature"] == 0.3
        assert payload["generationConfig"]["maxOutputTokens"] == 400
        assert payload["generationConfig"]["responseMimeType"] == "application/json"

    def test_call_api_missing_key(self):
        provider = self._make_provider(gemini_api_key=None)
        with pytest.raises(RuntimeError, match="GEMINI_API_KEY is not set"):
            provider._call_api({})

    def test_call_api_missing_model(self):
        provider = self._make_provider(gemini_model="")
        with pytest.raises(RuntimeError, match="GEMINI_MODEL is not set"):
            provider._call_api({})

    @patch("pulse.assistant.llm.urllib.request.urlopen")
    def test_call_api_success(self, mock_urlopen):
        body = {"candidates": [{"content": {"parts": [{"text": "gemini says hi"}]}}]}
        mock_resp = Mock()
        mock_resp.read.return_value = json.dumps(body).encode()
        mock_resp.__enter__ = Mock(return_value=mock_resp)
        mock_resp.__exit__ = Mock(return_value=False)
        mock_urlopen.return_value = mock_resp

        provider = self._make_provider()
        assert provider._call_api({}) == "gemini says hi"

    @patch("pulse.assistant.llm.urllib.request.urlopen")
    def test_call_api_http_error(self, mock_urlopen):
        mock_urlopen.side_effect = urllib.error.HTTPError(
            url="", code=503, msg="Unavailable", hdrs=None, fp=None  # type: ignore[arg-type]
        )
        provider = self._make_provider()
        with pytest.raises(RuntimeError, match="Gemini HTTP error: 503"):
            provider._call_api({})

    @patch("pulse.assistant.llm.urllib.request.urlopen")
    def test_call_api_blocked_prompt(self, mock_urlopen):
        body = {"candidates": [], "promptFeedback": {"blockReason": "SAFETY"}}
        mock_resp = Mock()
        mock_resp.read.return_value = json.dumps(body).encode()
        mock_resp.__enter__ = Mock(return_value=mock_resp)
        mock_resp.__exit__ = Mock(return_value=False)
        mock_urlopen.return_value = mock_resp

        provider = self._make_provider()
        with pytest.raises(RuntimeError, match="Gemini blocked prompt: SAFETY"):
            provider._call_api({})

    @patch("pulse.assistant.llm.urllib.request.urlopen")
    def test_call_api_no_candidates_no_feedback(self, mock_urlopen):
        body = {"candidates": []}
        mock_resp = Mock()
        mock_resp.read.return_value = json.dumps(body).encode()
        mock_resp.__enter__ = Mock(return_value=mock_resp)
        mock_resp.__exit__ = Mock(return_value=False)
        mock_urlopen.return_value = mock_resp

        provider = self._make_provider()
        with pytest.raises(RuntimeError, match="missing content"):
            provider._call_api({})

    @patch("pulse.assistant.llm.urllib.request.urlopen")
    def test_call_api_skips_bad_content_types(self, mock_urlopen):
        """Candidates with non-dict content or non-list parts are skipped."""
        body = {
            "candidates": [
                {"content": "not a dict"},
                {"content": {"parts": "not a list"}},
                {"content": {"parts": [{"text": "good"}]}},
            ]
        }
        mock_resp = Mock()
        mock_resp.read.return_value = json.dumps(body).encode()
        mock_resp.__enter__ = Mock(return_value=mock_resp)
        mock_resp.__exit__ = Mock(return_value=False)
        mock_urlopen.return_value = mock_resp

        provider = self._make_provider()
        assert provider._call_api({}) == "good"

    async def test_generate_success(self):
        provider = self._make_provider()
        resp = json.dumps({"response": "hi", "actions": []})
        with patch.object(provider, "_call_api", return_value=resp):
            result = await provider.generate("hey", [])
        assert result.response == "hi"

    async def test_generate_exception(self):
        provider = self._make_provider()
        with patch.object(provider, "_call_api", side_effect=RuntimeError("Gemini HTTP error: 503")):
            result = await provider.generate("hey", [])
        assert "at capacity" in result.response

    async def test_simple_chat(self):
        provider = self._make_provider()
        with patch.object(provider, "_call_api", return_value="chat") as mock_call:
            result = await provider.simple_chat("sys", "msg", timeout=2)
        assert result == "chat"
        payload = mock_call.call_args[0][0]
        assert payload["system_instruction"]["parts"][0]["text"] == "sys"
        assert payload["contents"][0]["parts"][0]["text"] == "msg"

    async def test_simple_chat_no_system_prompt(self):
        provider = self._make_provider()
        with patch.object(provider, "_call_api", return_value="chat") as mock_call:
            await provider.simple_chat("", "msg")
        payload = mock_call.call_args[0][0]
        assert "system_instruction" not in payload

    @patch("pulse.assistant.llm.urllib.request.urlopen")
    async def test_validate_valid_key(self, mock_urlopen):
        mock_resp = Mock()
        mock_resp.read.return_value = b'{"models": []}'
        mock_resp.__enter__ = Mock(return_value=mock_resp)
        mock_resp.__exit__ = Mock(return_value=False)
        mock_urlopen.return_value = mock_resp

        provider = self._make_provider()
        assert await provider.validate_api_key() is True

    @patch("pulse.assistant.llm.urllib.request.urlopen")
    async def test_validate_401(self, mock_urlopen):
        mock_urlopen.side_effect = urllib.error.HTTPError(
            url="", code=401, msg="Unauthorized", hdrs=None, fp=None  # type: ignore[arg-type]
        )
        provider = self._make_provider()
        assert await provider.validate_api_key() is False

    @patch("pulse.assistant.llm.urllib.request.urlopen")
    async def test_validate_network_error(self, mock_urlopen):
        mock_urlopen.side_effect = urllib.error.URLError("dns fail")
        provider = self._make_provider()
        assert await provider.validate_api_key() is True


class TestBuildLLMProviderExtended:
    """Extended tests for the build_llm_provider factory."""

    def test_build_anthropic(self):
        config = make_llm_config(provider="anthropic", anthropic_api_key="k")
        assert isinstance(build_llm_provider(config), AnthropicProvider)

    def test_build_groq(self):
        config = make_llm_config(provider="groq", groq_api_key="k")
        assert isinstance(build_llm_provider(config), GroqProvider)

    def test_build_mistral(self):
        config = make_llm_config(provider="mistral", mistral_api_key="k")
        assert isinstance(build_llm_provider(config), MistralProvider)

    def test_build_openrouter(self):
        config = make_llm_config(provider="openrouter", openrouter_api_key="k")
        assert isinstance(build_llm_provider(config), OpenRouterProvider)

    def test_unknown_provider_falls_back_to_openai(self):
        config = make_llm_config(provider="doesnotexist")
        provider = build_llm_provider(config)
        assert isinstance(provider, OpenAIProvider)

    def test_empty_provider_falls_back_to_openai(self):
        config = make_llm_config(provider="")
        provider = build_llm_provider(config)
        assert isinstance(provider, OpenAIProvider)

    def test_whitespace_provider_falls_back_to_openai(self):
        config = make_llm_config(provider="  ")
        provider = build_llm_provider(config)
        assert isinstance(provider, OpenAIProvider)


class TestBuildLLMProviderWithOverridesExtended:
    """Extended tests for build_llm_provider_with_overrides."""

    def test_unknown_provider_falls_back_to_openai(self):
        config = make_llm_config()
        provider = build_llm_provider_with_overrides(config, "unknown_provider", {})
        assert isinstance(provider, OpenAIProvider)

    def test_empty_provider_defaults_to_openai(self):
        config = make_llm_config()
        provider = build_llm_provider_with_overrides(config, "", {})
        assert isinstance(provider, OpenAIProvider)

    def test_none_provider_defaults_to_openai(self):
        config = make_llm_config()
        provider = build_llm_provider_with_overrides(config, None, {})  # type: ignore[arg-type]
        assert isinstance(provider, OpenAIProvider)


class TestGetSupportedProviders:
    """Test get_supported_providers."""

    def test_returns_all_providers(self):
        providers = get_supported_providers()
        assert "openai" in providers
        assert "gemini" in providers
        assert "anthropic" in providers
        assert "groq" in providers
        assert "mistral" in providers
        assert "openrouter" in providers

    def test_returns_copy(self):
        """Ensure the returned dict is a copy, not the original."""
        a = get_supported_providers()
        b = get_supported_providers()
        a["new"] = "value"
        assert "new" not in b
