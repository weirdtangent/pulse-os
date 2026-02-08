"""LLM provider abstractions."""

from __future__ import annotations

import asyncio
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterable
from dataclasses import dataclass

from .config import LLMConfig

# Registry of supported LLM providers
SUPPORTED_PROVIDERS = {
    "openai": "OpenAI",
    "gemini": "Google Gemini",
    "anthropic": "Anthropic Claude",
    "groq": "Groq",
    "mistral": "Mistral AI",
    "openrouter": "OpenRouter",
}


@dataclass
class LLMResult:
    response: str
    actions: list[str]
    follow_up: bool = False


class LLMProvider:
    async def generate(self, user_text: str, actions_for_prompt: Iterable[dict[str, str]]) -> LLMResult:
        raise NotImplementedError


def _parse_llm_response(response_text: str) -> LLMResult:
    try:
        parsed = json.loads(response_text)
    except json.JSONDecodeError:
        return LLMResult(response=response_text.strip(), actions=[])

    response = (parsed.get("response") or "").strip()
    raw_actions = parsed.get("actions") or []
    follow_up = bool(parsed.get("follow_up"))
    actions: list[str] = []
    if isinstance(raw_actions, list):
        for slug in raw_actions:
            if isinstance(slug, str) and slug:
                actions.append(slug)
    return LLMResult(response=response or response_text.strip(), actions=actions, follow_up=follow_up)


def _format_system_prompt(config: LLMConfig, actions_for_prompt: list[dict[str, str]]) -> str:
    action_lines = []
    for action in actions_for_prompt:
        slug = action.get("slug")
        desc = action.get("description", "")
        if slug:
            action_lines.append(f"- {slug}: {desc}")

    action_section = "\n".join(action_lines) if action_lines else "  (no device actions are currently available)"

    system_content = f"""{config.system_prompt.strip()}

When you want to trigger hardware actions, you may only use the following slugs:
{action_section}

Always respond **only** with JSON in the form:
{{
  "response": "text to say aloud",
  "actions": ["optional_action_slug"],
  "follow_up": true  # optional, set true only when you explicitly need more info
}}
"""
    return system_content


class OpenAICompatibleProvider(LLMProvider):
    """Base class for OpenAI-compatible chat completion APIs (OpenAI, Groq, Mistral, OpenRouter)."""

    def __init__(self, config: LLMConfig, logger: logging.Logger | None = None) -> None:
        self.config = config
        self._logger = logger or logging.getLogger(__name__)

    def _get_api_key(self) -> str | None:
        """Return the API key for this provider."""
        raise NotImplementedError

    def _get_model(self) -> str:
        """Return the model name for this provider."""
        raise NotImplementedError

    def _get_base_url(self) -> str:
        """Return the base URL for this provider."""
        raise NotImplementedError

    def _get_timeout(self) -> int:
        """Return the timeout in seconds for this provider."""
        raise NotImplementedError

    def _get_provider_name(self) -> str:
        """Return the provider name for error messages."""
        raise NotImplementedError

    async def generate(self, user_text: str, actions_for_prompt: Iterable[dict[str, str]]) -> LLMResult:
        payload = self._build_payload(user_text, list(actions_for_prompt))
        try:
            response_text = await asyncio.to_thread(self._call_api, payload)
        except Exception as exc:
            self._logger.exception("[llm] LLM call failed: %s", exc)
            return LLMResult(response="Sorry, I ran into an error while thinking about that.", actions=[])

        return _parse_llm_response(response_text)

    def _build_payload(self, user_text: str, actions_for_prompt: list[dict[str, str]]) -> dict:
        system_content = _format_system_prompt(self.config, actions_for_prompt)

        messages = [
            {"role": "system", "content": system_content},
            {
                "role": "user",
                "content": user_text.strip(),
            },
        ]

        payload = {
            "model": self._get_model(),
            "messages": messages,
            "temperature": 0.3,
            "max_tokens": 400,
            "response_format": {"type": "json_object"},
        }
        return payload

    def _call_api(self, payload: dict) -> str:
        api_key = self._get_api_key()
        if not api_key:
            raise RuntimeError(f"{self._get_provider_name().upper()}_API_KEY is not set")

        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            url=f"{self._get_base_url().rstrip('/')}/chat/completions",
            data=data,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                request, timeout=self._get_timeout()
            ) as response:  # nosec B310 - timeout in kwargs
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"{self._get_provider_name()} HTTP error: {exc.code}") from exc

        parsed = json.loads(body)
        choices = parsed.get("choices") or []
        if not choices:
            raise RuntimeError("LLM response missing choices")
        message = choices[0].get("message") or {}
        content = message.get("content")
        if not content:
            raise RuntimeError("LLM response missing content")
        return str(content)


class OpenAIProvider(OpenAICompatibleProvider):
    """Call OpenAI chat completion endpoints."""

    def _get_api_key(self) -> str | None:
        return self.config.openai_api_key

    def _get_model(self) -> str:
        return self.config.openai_model

    def _get_base_url(self) -> str:
        return self.config.openai_base_url

    def _get_timeout(self) -> int:
        return self.config.openai_timeout

    def _get_provider_name(self) -> str:
        return "OpenAI"


class GroqProvider(OpenAICompatibleProvider):
    """Call Groq inference API (OpenAI-compatible, ultra-fast)."""

    def _get_api_key(self) -> str | None:
        return self.config.groq_api_key

    def _get_model(self) -> str:
        return self.config.groq_model

    def _get_base_url(self) -> str:
        return self.config.groq_base_url

    def _get_timeout(self) -> int:
        return self.config.groq_timeout

    def _get_provider_name(self) -> str:
        return "Groq"


class MistralProvider(OpenAICompatibleProvider):
    """Call Mistral AI API (OpenAI-compatible)."""

    def _get_api_key(self) -> str | None:
        return self.config.mistral_api_key

    def _get_model(self) -> str:
        return self.config.mistral_model

    def _get_base_url(self) -> str:
        return self.config.mistral_base_url

    def _get_timeout(self) -> int:
        return self.config.mistral_timeout

    def _get_provider_name(self) -> str:
        return "Mistral"


class OpenRouterProvider(OpenAICompatibleProvider):
    """Call OpenRouter API (OpenAI-compatible model aggregator)."""

    def _get_api_key(self) -> str | None:
        return self.config.openrouter_api_key

    def _get_model(self) -> str:
        return self.config.openrouter_model

    def _get_base_url(self) -> str:
        return self.config.openrouter_base_url

    def _get_timeout(self) -> int:
        return self.config.openrouter_timeout

    def _get_provider_name(self) -> str:
        return "OpenRouter"


class AnthropicProvider(LLMProvider):
    """Call Anthropic Claude API (Messages format)."""

    def __init__(self, config: LLMConfig, logger: logging.Logger | None = None) -> None:
        self.config = config
        self._logger = logger or logging.getLogger(__name__)

    async def generate(self, user_text: str, actions_for_prompt: Iterable[dict[str, str]]) -> LLMResult:
        payload = self._build_payload(user_text, list(actions_for_prompt))
        try:
            response_text = await asyncio.to_thread(self._call_api, payload)
        except Exception as exc:
            self._logger.exception("LLM call failed: %s", exc)
            return LLMResult(response="Sorry, I ran into an error while thinking about that.", actions=[])
        return _parse_llm_response(response_text)

    def _build_payload(self, user_text: str, actions_for_prompt: list[dict[str, str]]) -> dict:
        system_content = _format_system_prompt(self.config, actions_for_prompt)

        # Anthropic uses top-level "system" field instead of system message
        payload = {
            "model": self.config.anthropic_model,
            "max_tokens": 400,  # Required by Anthropic API
            "temperature": 0.3,
            "system": system_content,
            "messages": [
                {
                    "role": "user",
                    "content": user_text.strip(),
                }
            ],
        }
        return payload

    def _call_api(self, payload: dict) -> str:
        if not self.config.anthropic_api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set")

        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            url=f"{self.config.anthropic_base_url.rstrip('/')}/messages",
            data=data,
            headers={
                "x-api-key": self.config.anthropic_api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(
                request, timeout=self.config.anthropic_timeout
            ) as response:  # nosec B310 - timeout in kwargs
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"Anthropic HTTP error: {exc.code}") from exc

        # Parse Anthropic response format: {"content": [{"type": "text", "text": "..."}]}
        parsed = json.loads(body)
        content = parsed.get("content") or []

        # Anthropic returns content as array of blocks
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text")
                if text:
                    return str(text)

        raise RuntimeError("LLM response missing content")


class GeminiProvider(LLMProvider):
    """Call Google Gemini (Generative Language) models."""

    def __init__(self, config: LLMConfig, logger: logging.Logger | None = None) -> None:
        self.config = config
        self._logger = logger or logging.getLogger(__name__)

    async def generate(self, user_text: str, actions_for_prompt: Iterable[dict[str, str]]) -> LLMResult:
        payload = self._build_payload(user_text, list(actions_for_prompt))
        try:
            response_text = await asyncio.to_thread(self._call_api, payload)
        except Exception as exc:
            self._logger.exception("[llm] LLM call failed: %s", exc)
            return LLMResult(response="Sorry, I ran into an error while thinking about that.", actions=[])
        return _parse_llm_response(response_text)

    def _build_payload(self, user_text: str, actions_for_prompt: list[dict[str, str]]) -> dict:
        system_content = _format_system_prompt(self.config, actions_for_prompt)
        payload: dict[str, object] = {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": user_text.strip()}],
                }
            ],
            "generationConfig": {
                "temperature": 0.3,
                "maxOutputTokens": 400,
                "responseMimeType": "application/json",
            },
        }
        if system_content:
            payload["system_instruction"] = {
                "parts": [
                    {
                        "text": system_content,
                    }
                ]
            }
        return payload

    def _call_api(self, payload: dict) -> str:
        if not self.config.gemini_api_key:
            raise RuntimeError("GEMINI_API_KEY is not set")
        model = (self.config.gemini_model or "").strip()
        if not model:
            raise RuntimeError("GEMINI_MODEL is not set")
        base_url = self.config.gemini_base_url.rstrip("/")
        endpoint = f"{base_url}/models/{model}:generateContent"
        query = urllib.parse.urlencode({"key": self.config.gemini_api_key})
        url = f"{endpoint}?{query}"

        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            url=url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": self.config.gemini_api_key,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                request, timeout=self.config.gemini_timeout
            ) as response:  # nosec B310 - timeout in kwargs
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"Gemini HTTP error: {exc.code}") from exc

        parsed = json.loads(body)
        candidates = parsed.get("candidates") or []
        for candidate in candidates:
            content = candidate.get("content") or {}
            if not isinstance(content, dict):
                continue
            parts = content.get("parts") or []
            if not isinstance(parts, list):
                continue
            for part in parts:
                if isinstance(part, dict):
                    text = part.get("text")
                    if isinstance(text, str) and text.strip():
                        return text

        prompt_feedback = parsed.get("promptFeedback")
        if isinstance(prompt_feedback, dict):
            block_reason = prompt_feedback.get("blockReason")
            if block_reason:
                raise RuntimeError(f"Gemini blocked prompt: {block_reason}")
        raise RuntimeError("LLM response missing content")


def get_supported_providers() -> dict[str, str]:
    """Return mapping of provider IDs to display names."""
    return SUPPORTED_PROVIDERS.copy()


def build_llm_provider(config: LLMConfig, logger: logging.Logger | None = None) -> LLMProvider:
    """Build an LLM provider based on configuration.

    Args:
        config: LLM configuration containing provider selection and credentials
        logger: Optional logger instance

    Returns:
        Configured LLM provider instance

    Raises:
        RuntimeError: If provider credentials are missing
    """
    provider = (config.provider or "").strip().lower()
    log = logger or logging.getLogger(__name__)

    # Validate provider and log helpful message if unknown
    if provider and provider not in SUPPORTED_PROVIDERS:
        supported = ", ".join(SUPPORTED_PROVIDERS.keys())
        log.warning(f"Unknown LLM provider '{provider}', falling back to OpenAI. Supported providers: {supported}")
        provider = "openai"

    # Default to OpenAI if no provider specified
    if not provider:
        provider = "openai"

    # Build the appropriate provider
    if provider == "gemini":
        return GeminiProvider(config, log)
    elif provider == "anthropic":
        return AnthropicProvider(config, log)
    elif provider == "groq":
        return GroqProvider(config, log)
    elif provider == "mistral":
        return MistralProvider(config, log)
    elif provider == "openrouter":
        return OpenRouterProvider(config, log)

    return OpenAIProvider(config, log)
