"""LLM provider abstractions."""

from __future__ import annotations

import asyncio
import json
import logging
import urllib.error
import urllib.request
from collections.abc import Iterable
from dataclasses import dataclass

from .config import LLMConfig


@dataclass
class LLMResult:
    response: str
    actions: list[str]


class LLMProvider:
    async def generate(self, user_text: str, actions_for_prompt: Iterable[dict[str, str]]) -> LLMResult:
        raise NotImplementedError


class OpenAIProvider(LLMProvider):
    """Call OpenAI-compatible chat completion endpoints."""

    def __init__(self, config: LLMConfig, logger: logging.Logger | None = None) -> None:
        self.config = config
        self._logger = logger or logging.getLogger(__name__)

    async def generate(self, user_text: str, actions_for_prompt: Iterable[dict[str, str]]) -> LLMResult:
        payload = self._build_payload(user_text, list(actions_for_prompt))
        try:
            response_text = await asyncio.to_thread(self._call_api, payload)
        except Exception as exc:  # pylint: disable=broad-except
            self._logger.exception("LLM call failed: %s", exc)
            return LLMResult(response="Sorry, I ran into an error while thinking about that.", actions=[])

        try:
            parsed = json.loads(response_text)
        except json.JSONDecodeError:
            return LLMResult(response=response_text.strip(), actions=[])

        response = (parsed.get("response") or "").strip()
        raw_actions = parsed.get("actions") or []
        actions = []
        if isinstance(raw_actions, list):
            for slug in raw_actions:
                if isinstance(slug, str) and slug:
                    actions.append(slug)
        return LLMResult(response=response or response_text.strip(), actions=actions)

    def _build_payload(self, user_text: str, actions_for_prompt: list[dict[str, str]]) -> dict:
        action_lines = []
        for action in actions_for_prompt:
            slug = action.get("slug")
            desc = action.get("description", "")
            if slug:
                action_lines.append(f"- {slug}: {desc}")

        action_section = "\n".join(action_lines) if action_lines else "  (no device actions are currently available)"

        system_content = f"""{self.config.system_prompt.strip()}

When you want to trigger hardware actions, you may only use the following slugs:
{action_section}

Always respond **only** with JSON in the form:
{{
  "response": "text to say aloud",
  "actions": ["optional_action_slug"]
}}
"""

        messages = [
            {"role": "system", "content": system_content},
            {
                "role": "user",
                "content": user_text.strip(),
            },
        ]

        payload = {
            "model": self.config.openai_model,
            "messages": messages,
            "temperature": 0.3,
            "max_tokens": 400,
            "response_format": {"type": "json_object"},
        }
        return payload

    def _call_api(self, payload: dict) -> str:
        if not self.config.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is not set")

        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            url=f"{self.config.openai_base_url.rstrip('/')}/chat/completions",
            data=data,
            headers={
                "Authorization": f"Bearer {self.config.openai_api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.config.openai_timeout) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"OpenAI HTTP error: {exc.code}") from exc

        parsed = json.loads(body)
        choices = parsed.get("choices") or []
        if not choices:
            raise RuntimeError("LLM response missing choices")
        message = choices[0].get("message") or {}
        content = message.get("content")
        if not content:
            raise RuntimeError("LLM response missing content")
        return str(content)


