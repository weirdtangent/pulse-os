"""Async client helpers for Home Assistant REST/Assist APIs."""

from __future__ import annotations

import asyncio
import json
import logging
import ssl
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

import httpx

try:
    import websockets
    from websockets.client import WebSocketClientProtocol
    from websockets.exceptions import WebSocketException
except ImportError:
    websockets = None  # type: ignore[assignment]
    WebSocketClientProtocol = None  # type: ignore[assignment,misc]
    WebSocketException = None  # type: ignore[assignment,misc]

from .config import HomeAssistantConfig


class HomeAssistantError(RuntimeError):
    """Generic Home Assistant API failure."""


class HomeAssistantAuthError(HomeAssistantError):
    """Raised when HA returns 401/403."""


@dataclass(slots=True)
class HomeAssistantClient:
    config: HomeAssistantConfig
    timeout: float = 10.0
    _client: httpx.AsyncClient = field(init=False, repr=False)
    _closed: bool = field(init=False, default=True, repr=False)

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
        try:
            self._client = httpx.AsyncClient(
                base_url=base_url,
                headers=headers,
                timeout=self.timeout,
                verify=self.config.verify_ssl,
                trust_env=False,
            )
        except PermissionError:
            logging.getLogger(__name__).warning(
                "Falling back to insecure SSL verification for Home Assistant client due to permission error"
            )
            self._client = httpx.AsyncClient(
                base_url=base_url,
                headers=headers,
                timeout=self.timeout,
                verify=False,
                trust_env=False,
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

    async def list_states(self) -> list[dict[str, Any]]:
        """Return all entity state payloads."""
        payload = await self._request("GET", "/api/states")
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        return []

    async def list_entities(self, domain: str | None = None) -> list[dict[str, Any]]:
        """List entities, optionally filtered by domain (e.g., 'light')."""
        states = await self.list_states()
        if not domain:
            return states
        prefix = f"{domain}."
        return [state for state in states if str(state.get("entity_id") or "").startswith(prefix)]

    async def set_light_state(
        self,
        entity_ids: Iterable[str],
        *,
        on: bool,
        brightness_pct: float | None = None,
        color_temp_mired: int | None = None,
        rgb_color: tuple[int, int, int] | None = None,
        transition: float | None = None,
    ) -> list[str]:
        """Turn lights on/off with optional brightness, color temperature, and RGB color."""

        ids = [entity_id for entity_id in entity_ids if entity_id]
        if not ids:
            return []

        payload: dict[str, Any] = {"entity_id": ids}
        if transition is not None:
            try:
                payload["transition"] = max(0.0, float(transition))
            except (TypeError, ValueError):
                pass
        if on:
            if brightness_pct is not None:
                payload["brightness"] = _brightness_pct_to_value(brightness_pct)
            if color_temp_mired is not None:
                payload["color_temp"] = int(color_temp_mired)
            if rgb_color is not None:
                payload["rgb_color"] = list(rgb_color)
            await self.call_service("light", "turn_on", payload)
        else:
            await self.call_service("light", "turn_off", payload)
        return ids

    async def activate_scene(self, scene_id: str) -> None:
        """Activate a Home Assistant scene."""
        if not scene_id:
            return
        await self.call_service("scene", "turn_on", {"entity_id": scene_id})

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

    async def _resolve_pipeline_id(self, ws: WebSocketClientProtocol | Any, pipeline_name_or_id: str) -> str:
        """Resolve pipeline name to ID via WebSocket."""
        # If it looks like a UUID/ID (long alphanumeric string), assume it's already an ID
        if len(pipeline_name_or_id) > 20 and pipeline_name_or_id.replace("-", "").replace("_", "").isalnum():
            return pipeline_name_or_id

        # Otherwise, list pipelines and find by name
        list_payload = {"id": 1, "type": "assist_pipeline/pipeline/list"}
        await ws.send(json.dumps(list_payload))

        # Wait for the list response
        list_response_raw = await asyncio.wait_for(ws.recv(), timeout=self.timeout)
        list_response = json.loads(list_response_raw)

        if list_response.get("type") == "result" and list_response.get("success"):
            result_data = list_response.get("result", {})
            pipelines = result_data.get("pipelines", []) if isinstance(result_data, dict) else []
            for pipeline in pipelines:
                if isinstance(pipeline, dict) and pipeline.get("name") == pipeline_name_or_id:
                    pipeline_id = pipeline.get("id")
                    if pipeline_id:
                        return pipeline_id

        # If not found, raise an error
        raise HomeAssistantError(f"Pipeline '{pipeline_name_or_id}' not found")

    async def assist_audio(
        self,
        audio_bytes: bytes,
        *,
        sample_rate: int,
        sample_width: int,
        channels: int,
        pipeline_id: str | None = None,
        language: str | None = None,
    ) -> dict[str, Any]:
        """Run Assist pipeline with audio input via WebSocket."""
        if websockets is None:
            raise HomeAssistantError(
                "websockets library is required for audio assist (install with: pip install websockets)"
            )

        pipeline_name_or_id = pipeline_id or self.config.assist_pipeline
        base_url = self.config.base_url.rstrip("/")
        # Convert http/https to ws/wss
        ws_url = base_url.replace("http://", "ws://").replace("https://", "wss://")
        ws_path = "/api/websocket"
        ws_uri = f"{ws_url}{ws_path}"

        # Build SSL context if needed
        ssl_context = None
        if ws_url.startswith("wss://"):
            ssl_context = ssl.create_default_context()
            if not self.config.verify_ssl:
                ssl_context.check_hostname = False
                ssl_context.verify_mode = ssl.CERT_NONE

        # Connect to WebSocket
        async with websockets.connect(ws_uri, ssl=ssl_context) as ws:
            # First, authenticate via WebSocket
            auth_msg_raw = await ws.recv()
            auth_msg = json.loads(auth_msg_raw)
            if auth_msg.get("type") != "auth_required":
                raise HomeAssistantError("Expected auth_required message")

            await ws.send(json.dumps({"type": "auth", "access_token": self.config.token}))
            auth_result_raw = await ws.recv()
            auth_result = json.loads(auth_result_raw)
            if auth_result.get("type") != "auth_ok":
                raise HomeAssistantAuthError("WebSocket authentication failed")

            # Resolve pipeline name to ID if needed
            resolved_pipeline_id: str | None = None
            if pipeline_name_or_id:
                resolved_pipeline_id = await self._resolve_pipeline_id(ws, pipeline_name_or_id)

            # Now send the assist_pipeline/run command
            # WebSocket API requires an 'id' field for commands
            run_payload: dict[str, Any] = {
                "id": 2,
                "type": "assist_pipeline/run",
                "start_stage": "stt",
                "end_stage": "tts",
                "input": {
                    "sample_rate": sample_rate,
                },
            }
            if resolved_pipeline_id:
                run_payload["pipeline"] = resolved_pipeline_id
            if language:
                run_payload["language"] = language

            await ws.send(json.dumps(run_payload))

            # Wait for run-start event
            # The response might be a "result" first, then events
            while True:
                run_start_raw = await asyncio.wait_for(ws.recv(), timeout=self.timeout)
                # Handle binary messages (skip them)
                if isinstance(run_start_raw, bytes):
                    continue
                run_start = json.loads(run_start_raw)
                if run_start.get("type") == "result":
                    # Command was accepted, continue waiting for events
                    if not run_start.get("success"):
                        error = run_start.get("error", {})
                        raise HomeAssistantError(f"Assist pipeline command failed: {error}")
                    continue
                elif run_start.get("type") == "event":
                    event = run_start.get("event", {})
                    if event.get("type") == "run-start":
                        break
                    # Other events might come first, continue waiting
                    continue
                else:
                    raise HomeAssistantError(f"Unexpected response: {run_start}")

            # Get stt_binary_handler_id from run-start event
            event_data = run_start.get("event", {})
            runner_data = event_data.get("runner_data", {})
            stt_binary_handler_id = runner_data.get("stt_binary_handler_id")

            if not stt_binary_handler_id:
                raise HomeAssistantError("No stt_binary_handler_id in run-start event")

            # Wait for stt-start event
            stt_start_raw = await ws.recv()
            stt_start = json.loads(stt_start_raw)
            if stt_start.get("type") != "event" or stt_start.get("event", {}).get("type") != "stt-start":
                raise HomeAssistantError(f"Unexpected response: {stt_start}")

            # Send audio data as binary messages
            # Each chunk needs to be prefixed with the handler ID byte
            handler_byte = bytes([stt_binary_handler_id])
            chunk_size = 4096  # Send in chunks
            for i in range(0, len(audio_bytes), chunk_size):
                chunk = audio_bytes[i : i + chunk_size]
                await ws.send(handler_byte + chunk)

            # Send end-of-audio marker (just the handler byte)
            await ws.send(handler_byte)

            # Collect events until run-end
            result: dict[str, Any] = {
                "stt_output": {},
                "intent_input": {},
                "response": {"speech": {"plain": {"speech": ""}}},
                "tts_output": {},
            }

            while True:
                try:
                    msg_raw = await asyncio.wait_for(ws.recv(), timeout=self.timeout)
                    # Handle both text and binary messages
                    if isinstance(msg_raw, bytes):
                        # Binary messages are audio chunks - we've already sent all audio
                        # so we can ignore these or handle TTS audio if needed
                        continue
                    msg = json.loads(msg_raw)
                    if msg.get("type") == "event":
                        event = msg.get("event", {})
                        event_type = event.get("type")

                        if event_type == "stt-vad-start":
                            # Voice activity detected
                            pass
                        elif event_type == "stt-vad-end":
                            # Voice activity ended
                            pass
                        elif event_type == "stt-end":
                            # STT completed - extract transcript
                            # Event data contains stt_output with text
                            stt_output = event.get("stt_output", {})
                            if isinstance(stt_output, dict) and "text" in stt_output:
                                result["stt_output"]["text"] = stt_output["text"]
                                result["intent_input"]["text"] = stt_output["text"]
                        elif event_type == "intent-start":
                            # Intent processing started
                            pass
                        elif event_type == "intent-end":
                            # Intent processing completed
                            # Event data contains intent_output with response and speech
                            intent_output = event.get("intent_output", {})
                            if isinstance(intent_output, dict):
                                result["intent"] = intent_output
                                # Extract speech from intent_output if available
                                response = intent_output.get("response", {})
                                if isinstance(response, dict):
                                    speech = response.get("speech", {})
                                    if isinstance(speech, dict):
                                        plain = speech.get("plain", {})
                                        if isinstance(plain, dict) and "speech" in plain:
                                            result["response"]["speech"]["plain"]["speech"] = plain["speech"]
                        elif event_type == "tts-start":
                            # TTS generation started
                            pass
                        elif event_type == "tts-end":
                            # TTS generation completed
                            # Event may contain url or other TTS data
                            tts_url = event.get("url")
                            if tts_url:
                                result["response"]["speech"]["plain"]["speech"] = tts_url
                        elif event_type == "tts-output":
                            # TTS audio output - may contain URL or binary data
                            tts_url = event.get("url")
                            if tts_url:
                                result["response"]["speech"]["plain"]["speech"] = tts_url
                            # Note: binary audio would come as separate binary messages
                        elif event_type == "run-end":
                            # Pipeline run completed
                            break
                        elif event_type == "error":
                            error_data = event.get("data", {})
                            raise HomeAssistantError(f"Assist pipeline error: {error_data}")
                except TimeoutError as exc:
                    raise HomeAssistantError("Assist pipeline timeout") from exc

            return result

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


def _brightness_pct_to_value(brightness_pct: float) -> int:
    """Convert 0-100% brightness into HA's 0-255 scale."""
    try:
        pct = float(brightness_pct)
    except (TypeError, ValueError):
        return 0
    clamped = max(0.0, min(100.0, pct))
    return int(round((clamped / 100.0) * 255))


def kelvin_to_mired(kelvin: float | int) -> int:
    """Convert Kelvin temperature into mireds (rounded)."""
    try:
        kelvin_value = float(kelvin)
    except (TypeError, ValueError):
        return 0
    if kelvin_value <= 0:
        return 0
    return int(round(1_000_000 / kelvin_value))
