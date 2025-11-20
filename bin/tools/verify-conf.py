#!/usr/bin/env python3
"""Connectivity smoke-test for pulse.conf."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import shlex
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
import json
import ssl
from urllib import error as urllib_error
from urllib import request as urllib_request

try:
    import paho.mqtt.client as mqtt
except ModuleNotFoundError:  # pragma: no cover - aids bootstrap
    mqtt = None  # type: ignore[assignment]

try:
    from pulse.assistant.config import AssistantConfig, HomeAssistantConfig, MicConfig, WyomingEndpoint
    from pulse.assistant.home_assistant import (
        HomeAssistantAuthError,
        HomeAssistantError,
        verify_home_assistant_access,
    )
except ModuleNotFoundError:
    repo_dir = Path(__file__).resolve().parents[2]
    if str(repo_dir) not in sys.path:
        sys.path.insert(0, str(repo_dir))
    from pulse.assistant.config import AssistantConfig, HomeAssistantConfig, MicConfig, WyomingEndpoint
    from pulse.assistant.home_assistant import (
        HomeAssistantAuthError,
        HomeAssistantError,
        verify_home_assistant_access,
    )

try:
    from wyoming.asr import Transcribe, Transcript
    from wyoming.audio import AudioChunk, AudioStart, AudioStop
    from wyoming.client import AsyncTcpClient
    from wyoming.info import Describe, Info
    from wyoming.tts import Synthesize
    from wyoming.wake import Detect, Detection, NotDetected

    WYOMING_PROTOCOL_AVAILABLE = True
except ModuleNotFoundError:
    AsyncTcpClient = None  # type: ignore[assignment]
    Describe = None  # type: ignore[assignment]
    Info = None  # type: ignore[assignment]
    Transcribe = None  # type: ignore[assignment]
    Transcript = None  # type: ignore[assignment]
    AudioChunk = None  # type: ignore[assignment]
    AudioStart = None  # type: ignore[assignment]
    AudioStop = None  # type: ignore[assignment]
    Synthesize = None  # type: ignore[assignment]
    Detect = None  # type: ignore[assignment]
    Detection = None  # type: ignore[assignment]
    NotDetected = None  # type: ignore[assignment]
    WYOMING_PROTOCOL_AVAILABLE = False

Status = Literal["ok", "fail", "skip"]

EXPECTED_WYOMING_TYPES: dict[str, set[str]] = {
    "Wyoming Whisper": {"stt", "asr"},
    "Wyoming Piper": {"tts"},
    "Wyoming OpenWakeWord": {"wake"},
}


def _silence_bytes(duration_ms: int, mic: MicConfig) -> bytes:
    frames = int(mic.rate * (duration_ms / 1000))
    frame_bytes = mic.width * mic.channels
    total_bytes = max(1, frames * frame_bytes)
    return bytes(total_bytes)


@dataclass(slots=True)
class CheckResult:
    """Container for an individual verification step."""

    name: str
    status: Status
    detail: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate MQTT, remote logging, and Wyoming endpoints defined in pulse.conf. "
            "The script sources the config file, attempts TCP connections, and reports a concise summary."
        )
    )
    parser.add_argument(
        "--config",
        type=Path,
        help="Path to pulse.conf (defaults to ./pulse.conf, then /opt/pulse-os/pulse.conf).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=5.0,
        help="Seconds to wait for each network check (default: 5).",
    )
    return parser.parse_args()


def resolve_config_path(cli_path: Path | None) -> Path | None:
    """Best-effort lookup for pulse.conf."""
    if cli_path:
        candidate = cli_path.expanduser().resolve()
        if not candidate.exists():
            raise SystemExit(f"Config file not found: {candidate}")
        return candidate

    repo_candidate = Path(__file__).resolve().parents[2] / "pulse.conf"
    if repo_candidate.exists():
        return repo_candidate

    system_candidate = Path("/opt/pulse-os/pulse.conf")
    if system_candidate.exists():
        return system_candidate

    return None


def load_env_from_config(config_path: Path | None) -> dict[str, str]:
    """Source pulse.conf in a subshell and merge the exported variables into a dict."""
    env = os.environ.copy()
    if config_path is None:
        return env

    command = f"set -a; source {shlex.quote(str(config_path))}; env -0"

    try:
        proc = subprocess.run(
            ["bash", "-c", command],
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as exc:  # pragma: no cover - tooling aid
        raise SystemExit(f"Failed to source {config_path}: {exc.stderr.decode('utf-8', errors='ignore')}") from exc

    stdout = proc.stdout
    for entry in stdout.split(b"\0"):
        if not entry:
            continue
        if b"=" not in entry:
            continue
        key, value = entry.split(b"=", 1)
        env[key.decode("utf-8")] = value.decode("utf-8")

    return env


def _is_truthy(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int_or_default(value: str | None, fallback: int | None) -> int | None:
    if value is None or value.strip() == "":
        return fallback
    try:
        return int(value)
    except ValueError:
        return fallback


def check_mqtt(config: AssistantConfig, timeout: float) -> CheckResult:
    if mqtt is None:
        detail = (
            "paho-mqtt is not installed in this environment. "
            "Install project dependencies (e.g. `uv sync` or `pip install -r requirements.txt`)."
        )
        return CheckResult("MQTT", "fail", detail)

    host = (config.mqtt.host or "").strip()
    if not host:
        return CheckResult("MQTT", "skip", "MQTT_HOST is not defined; telemetry disabled.")

    callback_kwargs: dict[str, object] = {}
    if hasattr(mqtt, "CallbackAPIVersion"):
        callback_kwargs["callback_api_version"] = mqtt.CallbackAPIVersion.VERSION2

    client = mqtt.Client(
        client_id=f"pulse-verify-{config.hostname}",
        clean_session=True,
        protocol=mqtt.MQTTv311,
        transport="tcp",
        **callback_kwargs,
    )
    if config.mqtt.username:
        client.username_pw_set(config.mqtt.username, config.mqtt.password or "")

    connect_event = threading.Event()
    result_code: dict[str, int | None] = {"rc": None}

    def _handle_connect(_client, _userdata, _flags, rc, properties=None):  # type: ignore[no-untyped-def]
        result_code["rc"] = rc
        connect_event.set()

    client.on_connect = _handle_connect

    start = time.perf_counter()
    loop_started = False
    try:
        client.connect(host, config.mqtt.port, keepalive=30)
        client.loop_start()
        loop_started = True
        if not connect_event.wait(timeout):
            return CheckResult("MQTT", "fail", f"Timed out waiting for CONNACK from {host}:{config.mqtt.port}.")
        rc = result_code["rc"]
        if rc is None:
            return CheckResult("MQTT", "fail", "MQTT connect callback never returned a result code.")
        if rc != 0:
            return CheckResult("MQTT", "fail", f"MQTT broker rejected connection ({mqtt.error_string(rc)}).")
    except OSError as exc:
        return CheckResult("MQTT", "fail", f"Unable to reach {host}:{config.mqtt.port} ({exc}).")
    finally:
        if loop_started:
            client.loop_stop()
        with contextlib.suppress(Exception):  # pragma: no cover
            client.disconnect()

    elapsed = time.perf_counter() - start
    return CheckResult("MQTT", "ok", f"Connected to {host}:{config.mqtt.port} in {elapsed:.2f}s.")


def check_remote_logging(env: dict[str, str], hostname: str, timeout: float) -> CheckResult:
    enabled = _is_truthy(env.get("PULSE_REMOTE_LOGGING"), default=False)
    if not enabled:
        return CheckResult("Remote logging", "skip", "PULSE_REMOTE_LOGGING is disabled.")

    host = (env.get("PULSE_REMOTE_LOG_HOST") or "").strip()
    port = _int_or_default(env.get("PULSE_REMOTE_LOG_PORT"), None)

    if not host:
        return CheckResult("Remote logging", "fail", "PULSE_REMOTE_LOG_HOST is empty.")
    if not port:
        return CheckResult("Remote logging", "fail", "PULSE_REMOTE_LOG_PORT is missing or invalid.")

    payload = (
        f"<134>1 {datetime.now(UTC).isoformat()} {hostname} pulse-verify-conf - - - "
        "PulseOS configuration verification message"
    ).encode()

    start = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.sendall(payload)
    except OSError as exc:
        return CheckResult("Remote logging", "fail", f"Unable to send syslog to {host}:{port} ({exc}).")

    elapsed = time.perf_counter() - start
    return CheckResult("Remote logging", "ok", f"Sent test syslog to {host}:{port} in {elapsed:.2f}s.")


def check_home_assistant(config: HomeAssistantConfig, timeout: float) -> CheckResult:
    if not config.base_url:
        return CheckResult("Home Assistant", "skip", "HOME_ASSISTANT_BASE_URL not set.")
    if not config.token:
        return CheckResult("Home Assistant", "fail", "HOME_ASSISTANT_TOKEN is missing.")
    try:
        info = asyncio.run(verify_home_assistant_access(config, timeout=timeout))
    except HomeAssistantAuthError as exc:
        return CheckResult("Home Assistant", "fail", f"Token rejected: {exc}")
    except HomeAssistantError as exc:
        return CheckResult("Home Assistant", "fail", str(exc))
    except Exception as exc:  # pylint: disable=broad-except
        return CheckResult("Home Assistant", "fail", f"Failed to query /api/: {exc}")
    location = info.get("location_name") or info.get("message") or "Home Assistant"
    version = info.get("version") or "unknown version"
    return CheckResult("Home Assistant", "ok", f"{location} responded (version {version}).")


def _build_ssl_context(config: HomeAssistantConfig, env: dict[str, str]) -> ssl.SSLContext | None:
    if not config.base_url or config.base_url.startswith("http://"):
        return None
    if not config.verify_ssl:
        return ssl._create_unverified_context()
    cafile = env.get("REQUESTS_CA_BUNDLE") or env.get("SSL_CERT_FILE")
    capath = env.get("SSL_CERT_DIR")
    if cafile or capath:
        return ssl.create_default_context(cafile=cafile or None, capath=capath or None)
    return ssl.create_default_context()


def _fetch_home_assistant_json(
    config: HomeAssistantConfig,
    env: dict[str, str],
    path: str,
    timeout: float,
) -> dict | list:
    if not config.base_url:
        raise ValueError("Base URL not configured")
    base = config.base_url.rstrip("/")
    url = f"{base}{path}"
    headers = {
        "Authorization": f"Bearer {config.token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    request = urllib_request.Request(url, headers=headers)
    context = _build_ssl_context(config, env)
    open_kwargs: dict[str, object] = {"timeout": timeout}
    if context is not None:
        open_kwargs["context"] = context
    with urllib_request.urlopen(request, **open_kwargs) as resp:  # type: ignore[arg-type]
        payload = resp.read()
    if not payload:
        return {}
    return json.loads(payload.decode("utf-8"))


def check_home_assistant_assist_pipeline(
    config: HomeAssistantConfig,
    env: dict[str, str],
    timeout: float,
) -> CheckResult:
    if not config.base_url:
        return CheckResult("HA Assist pipelines", "skip", "HOME_ASSISTANT_BASE_URL not set.")
    if not config.token:
        return CheckResult("HA Assist pipelines", "fail", "HOME_ASSISTANT_TOKEN is missing.")
    path = "/api/assist_pipeline/pipeline/list"
    try:
        payload = _fetch_home_assistant_json(config, env, path, timeout)
    except urllib_error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore").strip()
        snippet = body[:160] + ("…" if len(body) > 160 else "")
        return CheckResult(
            "HA Assist pipelines",
            "fail",
            f"{exc.code} error when calling {path}: {snippet or exc.reason}.",
        )
    except urllib_error.URLError as exc:
        return CheckResult("HA Assist pipelines", "fail", f"Unable to reach Assist pipeline endpoint: {exc.reason}.")
    except json.JSONDecodeError as exc:
        return CheckResult(
            "HA Assist pipelines",
            "fail",
            f"Assist pipeline endpoint returned invalid JSON: {exc}.",
        )
    except Exception as exc:  # pylint: disable=broad-except
        return CheckResult("HA Assist pipelines", "fail", f"Assist pipeline request failed: {exc}.")

    if isinstance(payload, dict):
        candidates = payload.get("pipelines")
        if isinstance(candidates, list):
            pipelines = candidates
        else:
            pipelines = payload.get("items") if isinstance(payload.get("items"), list) else []
    elif isinstance(payload, list):
        pipelines = payload
    else:
        pipelines = []

    count = len(pipelines)
    if count == 0:
        return CheckResult(
            "HA Assist pipelines",
            "fail",
            "Assist endpoint responded but no pipelines were returned. Create a pipeline in HA first.",
        )
    return CheckResult("HA Assist pipelines", "ok", f"Assist endpoint returned {count} pipeline(s).")


def check_llm(config: AssistantConfig) -> CheckResult:
    provider = (config.llm.provider or "").strip().lower()
    if provider != "openai":
        display = provider or "<unknown>"
        return CheckResult(
            "LLM",
            "skip",
            f"LLM provider '{display}' is not validated by this tool (only 'openai' is supported).",
        )

    api_key = (config.llm.openai_api_key or "").strip()
    if not api_key:
        return CheckResult("LLM", "fail", "OPENAI_API_KEY is not set but provider is 'openai'.")
    if not api_key.startswith("sk-"):
        return CheckResult("LLM", "fail", "OPENAI_API_KEY does not look like an 'sk-' token.")

    model = config.llm.openai_model or "<unspecified>"
    base_url = config.llm.openai_base_url or "https://api.openai.com/v1"
    return CheckResult("LLM", "ok", f"OpenAI model {model} configured (endpoint {base_url}).")


def check_wyoming_endpoints(config: AssistantConfig, env: dict[str, str], timeout: float) -> list[CheckResult]:
    checks: list[tuple[str, str, str, WyomingEndpoint]] = [
        ("Wyoming Whisper", "WYOMING_WHISPER_HOST", "WYOMING_WHISPER_PORT", config.stt_endpoint),
        ("Wyoming Piper", "WYOMING_PIPER_HOST", "WYOMING_PIPER_PORT", config.tts_endpoint),
        ("Wyoming OpenWakeWord", "WYOMING_OPENWAKEWORD_HOST", "WYOMING_OPENWAKEWORD_PORT", config.wake_endpoint),
    ]
    results: list[CheckResult] = []
    for name, host_key, port_key, endpoint in checks:
        if not WYOMING_PROTOCOL_AVAILABLE:
            results.append(_check_wyoming_tcp(name, host_key, port_key, endpoint, env, timeout))
            continue

        describe_result, info, host, port = _describe_wyoming_endpoint(
            name,
            host_key,
            port_key,
            endpoint,
            env,
            timeout,
        )
        if describe_result.status != "ok":
            results.append(describe_result)
            continue

        probe_result = _exercise_wyoming_service(name, host, port, timeout, config)

        if probe_result is None:
            results.append(describe_result)
        elif probe_result.status != "ok":
            results.append(probe_result)
        else:
            detail = f"{describe_result.detail} {probe_result.detail}".strip()
            results.append(CheckResult(name, "ok", detail))
    return results


def _check_wyoming_tcp(
    label: str,
    host_key: str,
    port_key: str,
    endpoint: WyomingEndpoint,
    env: dict[str, str],
    timeout: float,
) -> CheckResult:
    host = (env.get(host_key) or endpoint.host or "").strip()
    if not host:
        return CheckResult(label, "fail", f"{host_key} is not set.")

    port = _int_or_default(env.get(port_key), endpoint.port)
    if not port:
        return CheckResult(label, "fail", f"{port_key} is missing or invalid.")

    start = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            pass
    except OSError as exc:
        return CheckResult(label, "fail", f"Unable to connect to {host}:{port} ({exc}).")

    elapsed = time.perf_counter() - start
    suffix = f" (model hint: {endpoint.model})" if endpoint.model else ""
    return CheckResult(label, "ok", f"TCP handshake succeeded with {host}:{port} in {elapsed:.2f}s{suffix}.")


def _describe_wyoming_endpoint(
    label: str,
    host_key: str,
    port_key: str,
    endpoint: WyomingEndpoint,
    env: dict[str, str],
    timeout: float,
) -> tuple[CheckResult, Info | None, str | None, int | None]:
    host = (env.get(host_key) or endpoint.host or "").strip()
    if not host:
        return CheckResult(label, "fail", f"{host_key} is not set."), None, None, None

    port = _int_or_default(env.get(port_key), endpoint.port)
    if not port:
        return CheckResult(label, "fail", f"{port_key} is missing or invalid."), None, host, None

    if not WYOMING_PROTOCOL_AVAILABLE or AsyncTcpClient is None or Describe is None or Info is None:
        tcp_result = _check_wyoming_tcp(label, host_key, port_key, endpoint, env, timeout)
        return tcp_result, None, host, port

    try:
        info = asyncio.run(_describe_endpoint_async(host, port, timeout))
    except TimeoutError:
        return (
            CheckResult(label, "fail", f"Describe request to {host}:{port} timed out after {timeout:.1f}s."),
            None,
            host,
            port,
        )
    except OSError as exc:
        return (
            CheckResult(label, "fail", f"Unable to communicate with {host}:{port} ({exc})."),
            None,
            host,
            port,
        )

    if info is None:
        return (
            CheckResult(label, "fail", f"{host}:{port} closed the connection before sending Describe info."),
            None,
            host,
            port,
        )

    info_types = {t.lower() for t in getattr(info, "types", []) if t}
    expected = EXPECTED_WYOMING_TYPES.get(label)
    if expected and info_types and info_types.isdisjoint(expected):
        expected_display = ", ".join(sorted(expected))
        type_display = ", ".join(sorted(info_types)) or "<none>"
        return (
            CheckResult(
                label,
                "fail",
                f"{host}:{port} responded but types {type_display} do not include expected {expected_display}.",
            ),
            info,
            host,
            port,
        )

    model_names = [
        getattr(model, "name", None)
        for model in getattr(info, "models", [])  # type: ignore[arg-type]
        if getattr(model, "name", None)
    ]
    model_display = ", ".join(model_names) if model_names else (endpoint.model or "no models advertised")
    version = getattr(info, "version", None)
    service_name = getattr(info, "name", None) or "Service"
    type_display = ", ".join(sorted(info_types)) if info_types else "unknown"
    detail = f"{service_name} {version or ''} responded (types: {type_display}; models: {model_display})."
    return CheckResult(label, "ok", detail.strip()), info, host, port


def _exercise_wyoming_service(
    label: str,
    host: str | None,
    port: int | None,
    timeout: float,
    config: AssistantConfig,
) -> CheckResult | None:
    if host is None or port is None:
        return None
    if not WYOMING_PROTOCOL_AVAILABLE or AsyncTcpClient is None:
        return None

    if label == "Wyoming Whisper":
        return _exercise_whisper(host, port, config, timeout)
    if label == "Wyoming Piper":
        return _exercise_piper(host, port, timeout)
    if label == "Wyoming OpenWakeWord":
        return _exercise_openwakeword(host, port, config, timeout)
    return None


def _exercise_whisper(host: str, port: int, config: AssistantConfig, timeout: float) -> CheckResult:
    if Transcribe is None or Transcript is None or AudioStart is None or AudioChunk is None or AudioStop is None:
        return CheckResult("Wyoming Whisper", "fail", "Wyoming client libraries missing for whisper probe.")
    try:
        transcript = asyncio.run(_probe_whisper_async(host, port, config, timeout))
    except TimeoutError:
        return CheckResult("Wyoming Whisper", "fail", f"Whisper probe timed out after {timeout:.1f}s.")
    except OSError as exc:
        return CheckResult("Wyoming Whisper", "fail", f"Whisper probe failed: {exc}.")
    if transcript is None:
        return CheckResult("Wyoming Whisper", "fail", "Whisper probe returned no transcript event.")
    snippet = transcript if transcript else "<empty>"
    return CheckResult("Wyoming Whisper", "ok", f"Probe transcript: {snippet!r}.")


def _exercise_piper(host: str, port: int, timeout: float) -> CheckResult:
    if Synthesize is None or AudioStart is None or AudioChunk is None or AudioStop is None:
        return CheckResult("Wyoming Piper", "fail", "Wyoming client libraries missing for piper probe.")
    try:
        started, chunks = asyncio.run(_probe_piper_async(host, port, timeout))
    except TimeoutError:
        return CheckResult("Wyoming Piper", "fail", f"Piper probe timed out after {timeout:.1f}s.")
    except OSError as exc:
        return CheckResult("Wyoming Piper", "fail", f"Piper probe failed: {exc}.")
    if not started:
        return CheckResult("Wyoming Piper", "fail", "Piper probe never received AudioStart.")
    return CheckResult("Wyoming Piper", "ok", f"Probe synthesized {chunks} audio chunk(s).")


def _exercise_openwakeword(host: str, port: int, config: AssistantConfig, timeout: float) -> CheckResult:
    if Detect is None or AudioStart is None or AudioChunk is None or AudioStop is None:
        return CheckResult("Wyoming OpenWakeWord", "fail", "Wyoming client libraries missing for wake-word probe.")
    try:
        detection = asyncio.run(_probe_openwakeword_async(host, port, config, timeout))
    except TimeoutError:
        return CheckResult("Wyoming OpenWakeWord", "fail", f"Wake-word probe timed out after {timeout:.1f}s.")
    except OSError as exc:
        return CheckResult("Wyoming OpenWakeWord", "fail", f"Wake-word probe failed: {exc}.")
    if detection:
        return CheckResult("Wyoming OpenWakeWord", "ok", f"Probe detected wake word '{detection}'.")
    return CheckResult("Wyoming OpenWakeWord", "ok", "Probe returned NotDetected (expected for silence sample).")


async def _probe_whisper_async(host: str, port: int, config: AssistantConfig, timeout: float) -> str | None:
    assert AsyncTcpClient is not None
    assert Transcribe is not None
    assert Transcript is not None
    assert AudioStart is not None
    assert AudioChunk is not None
    assert AudioStop is not None

    client = AsyncTcpClient(host, port)
    await asyncio.wait_for(client.connect(), timeout=timeout)
    try:
        await asyncio.wait_for(
            client.write_event(
                Transcribe(
                    name=config.stt_endpoint.model,
                    language=config.language,
                ).event()
            ),
            timeout=timeout,
        )
        await asyncio.wait_for(
            client.write_event(
                AudioStart(
                    rate=config.mic.rate,
                    width=config.mic.width,
                    channels=config.mic.channels,
                ).event()
            ),
            timeout=timeout,
        )
        chunk = AudioChunk(
            rate=config.mic.rate,
            width=config.mic.width,
            channels=config.mic.channels,
            audio=_silence_bytes(400, config.mic),
        )
        await asyncio.wait_for(client.write_event(chunk.event()), timeout=timeout)
        await asyncio.wait_for(client.write_event(AudioStop().event()), timeout=timeout)
        while True:
            event = await asyncio.wait_for(client.read_event(), timeout=timeout)
            if event is None:
                return None
            if Transcript.is_type(event.type):
                return Transcript.from_event(event).text
    finally:
        await client.disconnect()


async def _probe_piper_async(host: str, port: int, timeout: float) -> tuple[bool, int]:
    assert AsyncTcpClient is not None
    assert Synthesize is not None
    assert AudioStart is not None
    assert AudioChunk is not None
    assert AudioStop is not None

    client = AsyncTcpClient(host, port)
    await asyncio.wait_for(client.connect(), timeout=timeout)
    started = False
    chunks = 0
    try:
        await asyncio.wait_for(client.write_event(Synthesize(text="Pulse verification ping").event()), timeout=timeout)
        while True:
            event = await asyncio.wait_for(client.read_event(), timeout=timeout)
            if event is None:
                break
            if AudioStart.is_type(event.type):
                started = True
            elif AudioChunk.is_type(event.type):
                chunks += 1
            elif AudioStop.is_type(event.type):
                break
    finally:
        await client.disconnect()
    return started, chunks


async def _probe_openwakeword_async(host: str, port: int, config: AssistantConfig, timeout: float) -> str | None:
    assert AsyncTcpClient is not None
    assert Detect is not None
    assert AudioStart is not None
    assert AudioChunk is not None
    assert AudioStop is not None
    assert Detection is not None
    assert NotDetected is not None

    client = AsyncTcpClient(host, port)
    await asyncio.wait_for(client.connect(), timeout=timeout)
    models = config.wake_models or ["okay_pulse"]
    timestamp = 0
    try:
        await asyncio.wait_for(client.write_event(Detect(names=models).event()), timeout=timeout)
        await asyncio.wait_for(
            client.write_event(
                AudioStart(
                    rate=config.mic.rate,
                    width=config.mic.width,
                    channels=config.mic.channels,
                    timestamp=timestamp,
                ).event()
            ),
            timeout=timeout,
        )
        chunk = AudioChunk(
            rate=config.mic.rate,
            width=config.mic.width,
            channels=config.mic.channels,
            audio=_silence_bytes(config.mic.chunk_ms, config.mic),
            timestamp=timestamp,
        )
        await asyncio.wait_for(client.write_event(chunk.event()), timeout=timeout)
        timestamp += config.mic.chunk_ms
        await asyncio.wait_for(client.write_event(AudioStop(timestamp=timestamp).event()), timeout=timeout)
        while True:
            event = await asyncio.wait_for(client.read_event(), timeout=timeout)
            if event is None:
                return None
            if Detection.is_type(event.type):
                detection = Detection.from_event(event)
                return detection.name or models[0]
            if NotDetected.is_type(event.type):
                return None
    finally:
        await client.disconnect()


async def _describe_endpoint_async(host: str, port: int, timeout: float) -> Info | None:
    assert AsyncTcpClient is not None
    assert Describe is not None
    assert Info is not None

    client = AsyncTcpClient(host, port)
    await asyncio.wait_for(client.connect(), timeout=timeout)
    try:
        await asyncio.wait_for(client.write_event(Describe().event()), timeout=timeout)
        while True:
            event = await asyncio.wait_for(client.read_event(), timeout=timeout)
            if event is None:
                return None
            if Info.is_type(event.type):
                return Info.from_event(event)
    finally:
        await client.disconnect()


def print_summary(results: list[CheckResult], config_path: Path | None) -> None:
    status_labels = {"ok": "[OK] ", "fail": "[FAIL]", "skip": "[SKIP]"}
    print("PulseOS configuration verification")
    if config_path:
        print(f"Config file: {config_path}")
    else:
        print("Config file: <environment variables>")
    print("-" * 60)
    for result in results:
        print(f"{status_labels[result.status]} {result.name}: {result.detail}")
    print("-" * 60)
    failures = sum(1 for result in results if result.status == "fail")
    skips = sum(1 for result in results if result.status == "skip")
    print(f"Checks: {len(results)}  Failures: {failures}  Skipped: {skips}")


def main() -> int:
    args = parse_args()
    config_path = resolve_config_path(args.config)
    if config_path is None:
        print("Warning: no pulse.conf found, falling back to current environment.", file=sys.stderr)
    env = load_env_from_config(config_path)
    config = AssistantConfig.from_env(env)

    results: list[CheckResult] = []
    results.append(check_mqtt(config, args.timeout))
    results.append(check_remote_logging(env, config.hostname, args.timeout))
    results.append(check_home_assistant(config.home_assistant, args.timeout))
    results.append(check_home_assistant_assist_pipeline(config.home_assistant, env, args.timeout))
    results.append(check_llm(config))
    results.extend(check_wyoming_endpoints(config, env, args.timeout))

    print_summary(results, config_path)
    has_failure = any(result.status == "fail" for result in results)
    return 1 if has_failure else 0


if __name__ == "__main__":
    sys.exit(main())
