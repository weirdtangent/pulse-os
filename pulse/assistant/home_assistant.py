"""Async client helpers for Home Assistant REST/Assist APIs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from .config import HomeAssistantConfig


class HomeAssistantError(RuntimeError):
    """Generic Home Assistant API failure."""


class HomeAssistantAuthError(HomeAssistantError):
    """Raised when HA returns 401/403."""


@dataclass(slots=True)
class HomeAssistantClient:
    config: HomeAssistantConfig
    timeout: float = 10.0

    def __post_init__(self) -> None:
        if not self.config.base_url:
            raise ValueError("Home Assistant base URL is not configured")
        if not self.config.token:
            raise ValueError("Home Assistant token is not configured")
        base_url = self.config.base_url.rstrip("/")
        headers = {
            "Authorization": f"Bearer {self.config.token}",
            "Content-Type": "application/json",
        }
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers=headers,
            timeout=self.timeout,
            verify=self.config.verify_ssl,
        )
        self._closed = False

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._client.aclose()

    async def get_info(self) -> dict[str, Any]:
        """Return `/api/` payload with HA metadata."""
        return await self._request("GET", "/api/")

    async def get_state(self, entity_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/api/states/{entity_id}")

    async def call_service(self, domain: str, service: str, data: dict[str, Any] | None = None) -> Any:
        path = f"/api/services/{domain}/{service}"
        return await self._request("POST", path, json=data or {})

    async def assist_text(
        self,
        text: str,
        *,
        pipeline_id: str | None = None,
        language: str | None = None,
        conversation_id: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"text": text}
        if pipeline_id or self.config.assist_pipeline:
            payload["conversation_id"] = conversation_id
            payload["pipeline_id"] = pipeline_id or self.config.assist_pipeline
        if language:
            payload["language"] = language
        return await self._request("POST", "/api/conversation/process", json=payload)

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = await self._client.request(method, path, **kwargs)
        except httpx.RequestError as exc:  # pragma: no cover - network errors
            raise HomeAssistantError(f"Failed to contact Home Assistant: {exc}") from exc
        if response.status_code in (401, 403):
            raise HomeAssistantAuthError("Home Assistant rejected the token")
        if response.status_code >= 400:
            raise HomeAssistantError(f"Home Assistant error {response.status_code}: {response.text}")
        if response.headers.get("content-type", "").startswith("application/json"):
            return response.json()
        return response.text


async def verify_home_assistant_access(config: HomeAssistantConfig, *, timeout: float = 5.0) -> dict[str, Any]:
    """Fetch HA info to confirm credentials are valid."""
    client = HomeAssistantClient(config, timeout=timeout)
    try:
        return await client.get_info()
    finally:
        await client.close()
