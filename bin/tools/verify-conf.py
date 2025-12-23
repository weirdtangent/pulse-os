#!/usr/bin/env python3
"""Connectivity smoke-test for pulse.conf."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import importlib.util
import json
import os
import shlex
import shutil
import socket
import ssl
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from urllib import error as urllib_error
from urllib import request as urllib_request

try:
    import paho.mqtt.client as mqtt
except ModuleNotFoundError:  # pragma: no cover - aids bootstrap
    mqtt = None  # type: ignore[assignment]

MODULE_ROOT = Path(__file__).resolve().parents[2]
if str(MODULE_ROOT) not in sys.path:
    sys.path.insert(0, str(MODULE_ROOT))

from pulse.assistant.config import AssistantConfig, HomeAssistantConfig, WyomingEndpoint  # noqa: E402
from pulse.assistant.home_assistant import (  # noqa: E402
    HomeAssistantAuthError,
    HomeAssistantError,
    verify_home_assistant_access,
)
from pulse.utils import parse_bool  # noqa: E402

try:
    from wyoming.client import AsyncTcpClient
    from wyoming.info import Describe, Info
except ModuleNotFoundError:
    AsyncTcpClient = None  # type: ignore[assignment]
    Describe = None  # type: ignore[assignment]
    Info = None  # type: ignore[assignment]

try:
    from pulse.assistant import wyoming as wyoming_helpers
except ModuleNotFoundError:
    wyoming_helpers = None  # type: ignore[assignment]
    WYOMING_PROTOCOL_AVAILABLE = False
else:
    WYOMING_PROTOCOL_AVAILABLE = AsyncTcpClient is not None and wyoming_helpers is not None

OPENWAKEWORD_LABEL = "Wyoming OpenWakeWord"
HA_OPENWAKEWORD_LABEL = "Home Assistant OpenWakeWord"

Status = Literal["ok", "fail", "skip"]

EXPECTED_WYOMING_TYPES: dict[str, set[str]] = {
    "Wyoming Whisper": {"stt", "asr"},
    "Wyoming Piper": {"tts"},
    OPENWAKEWORD_LABEL: {"wake"},
    HA_OPENWAKEWORD_LABEL: {"wake"},
}

ParseResult = tuple[dict[str, str], dict[str, str], set[str]]
ParseFunc = Callable[[Path], ParseResult]
_PARSE_CONFIG_FUNC: ParseFunc | None = None


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
        proc = subprocess.run(  # nosec B603 B607 - hardcoded command array
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


def _resolve_repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _load_parse_config_func() -> ParseFunc:
    global _PARSE_CONFIG_FUNC
    if _PARSE_CONFIG_FUNC is not None:
        return _PARSE_CONFIG_FUNC

    repo_dir = _resolve_repo_root()
    script_path = repo_dir / "bin" / "tools" / "sync-pulse-conf.py"
    if not script_path.exists():
        raise FileNotFoundError(f"sync helper not found at {script_path}")

    spec = importlib.util.spec_from_file_location("_sync_pulse_conf", script_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load module spec from {script_path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[attr-defined]

    parse_func = getattr(module, "parse_config_file", None)
    if not callable(parse_func):
        raise AttributeError("sync-pulse-conf.py does not expose parse_config_file()")

    _PARSE_CONFIG_FUNC = parse_func  # type: ignore[assignment]
    return parse_func


def _parse_config_defaults(config_path: Path | None) -> dict[str, str]:
    target: Path | None = None
    if config_path and config_path.exists():
        target = config_path
    else:
        fallback = _resolve_repo_root() / "pulse.conf.sample"
        if fallback.exists():
            target = fallback
    if target is None:
        return {}

    parse_config = _load_parse_config_func()
    variables, _, _ = parse_config(target)
    return variables


def _apply_config_defaults(env: dict[str, str], config_path: Path | None) -> None:
    try:
        defaults = _parse_config_defaults(config_path)
    except Exception as exc:  # pragma: no cover - defensive guard
        print(f"Warning: unable to parse defaults ({exc}).", file=sys.stderr)
        return
    for key, value in defaults.items():
        env.setdefault(key, value)


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
    if config.mqtt.tls_enabled:
        tls_kwargs: dict[str, object] = {"tls_version": getattr(ssl, "PROTOCOL_TLS_CLIENT", ssl.PROTOCOL_TLS)}
        if config.mqtt.ca_cert:
            tls_kwargs["ca_certs"] = config.mqtt.ca_cert
        if config.mqtt.cert:
            tls_kwargs["certfile"] = config.mqtt.cert
        if config.mqtt.key:
            tls_kwargs["keyfile"] = config.mqtt.key
        client.tls_set(**tls_kwargs)

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
    enabled = parse_bool(env.get("PULSE_REMOTE_LOGGING"), default=False)
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


def check_snapcast(env: dict[str, str], timeout: float) -> CheckResult:
    enabled = parse_bool(env.get("PULSE_SNAPCLIENT"), default=False)
    if not enabled:
        return CheckResult("Snapcast client", "skip", "PULSE_SNAPCLIENT is disabled.")

    host = (env.get("PULSE_SNAPCAST_HOST") or "").strip()
    if not host:
        return CheckResult("Snapcast client", "fail", "PULSE_SNAPCAST_HOST is empty.")

    control_port = _int_or_default(env.get("PULSE_SNAPCAST_CONTROL_PORT"), 1705)
    if not control_port:
        return CheckResult("Snapcast client", "fail", "PULSE_SNAPCAST_CONTROL_PORT is missing or invalid.")

    if shutil.which("snapclient") is None:
        return CheckResult("Snapcast client", "fail", "snapclient binary not found on this system (rerun setup).")

    start = time.perf_counter()
    try:
        with socket.create_connection((host, control_port), timeout=timeout):
            pass
    except OSError as exc:
        return CheckResult("Snapcast client", "fail", f"Unable to reach {host}:{control_port} ({exc}).")

    elapsed = time.perf_counter() - start
    return CheckResult("Snapcast client", "ok", f"Connected to {host}:{control_port} in {elapsed:.2f}s.")


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
    except Exception as exc:
        return CheckResult("Home Assistant", "fail", f"Failed to query /api/: {exc}")
    location = info.get("location_name") or info.get("message") or "Home Assistant"
    version = info.get("version") or "unknown version"
    return CheckResult("Home Assistant", "ok", f"{location} responded (version {version}).")


def _build_ssl_context(config: HomeAssistantConfig, env: dict[str, str]) -> ssl.SSLContext | None:
    if not config.base_url or config.base_url.startswith("http://"):
        return None
    if not config.verify_ssl:
        return ssl._create_unverified_context()  # nosec B323 - user config
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
    with urllib_request.urlopen(request, **open_kwargs) as resp:  # type: ignore[arg-type]  # nosec B310 - timeout in kwargs
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
    # If Assist pipeline is not configured, skip this check
    assist_pipeline = (config.assist_pipeline or "").strip()
    if not assist_pipeline:
        return CheckResult(
            "HA Assist pipelines",
            "skip",
            "HOME_ASSISTANT_ASSIST_PIPELINE not configured (Assist is optional).",
        )
    # Home Assistant Assist API uses WebSocket for /api/assist_pipeline/run (not REST)
    # For REST, we use /api/conversation/process for text-based commands
    # Verify that the conversation endpoint works (this is what we use for light control)
    try:
        base = config.base_url.rstrip("/")
        url = f"{base}/api/conversation/process"
        headers = {
            "Authorization": f"Bearer {config.token}",
            "Content-Type": "application/json",
        }
        # Send a test request to verify the endpoint works
        # We can't verify WebSocket endpoints easily, but conversation/process is what we use
        test_payload = json.dumps({"text": "test"}).encode("utf-8")
        request = urllib_request.Request(url, data=test_payload, headers=headers)
        context = _build_ssl_context(config, env)
        open_kwargs: dict[str, object] = {"timeout": timeout}
        if context is not None:
            open_kwargs["context"] = context
        try:
            with urllib_request.urlopen(request, **open_kwargs):  # type: ignore[arg-type]  # nosec B310 - timeout in kwargs
                # Endpoint exists and responds - conversation API is available
                # Note: /api/assist_pipeline/run is WebSocket-only, but conversation/process works for text
                return CheckResult(
                    "HA Assist pipelines",
                    "ok",
                    f"Conversation API is available (verified via /api/conversation/process). "
                    f"Configured pipeline: '{assist_pipeline}'. "
                    "Note: Audio assist requires WebSocket (not verified here).",
                )
        except urllib_error.HTTPError as exc:
            if exc.code == 404:
                return CheckResult(
                    "HA Assist pipelines",
                    "fail",
                    f"Conversation API not available (404). HOME_ASSISTANT_ASSIST_PIPELINE is set to "
                    f"'{assist_pipeline}', but conversation processing is not available. "
                    "To fix: Ensure Home Assistant is properly configured, or remove "
                    "HOME_ASSISTANT_ASSIST_PIPELINE from your config if you don't need Assist.",
                )
            # Other errors might mean the endpoint exists but our test was invalid
            # Read the error to see if it's a validation error (endpoint exists) or not found
            try:
                body = exc.read().decode("utf-8", errors="ignore")
                if "conversation" in body.lower() or "text" in body.lower():
                    # Endpoint exists, just validation error
                    return CheckResult(
                        "HA Assist pipelines",
                        "ok",
                        f"Conversation API is available (verified via /api/conversation/process). "
                        f"Configured pipeline: '{assist_pipeline}'.",
                    )
            except Exception:  # nosec B110 - parsing external data
                pass
            return CheckResult(
                "HA Assist pipelines",
                "fail",
                f"Conversation API returned {exc.code}: {exc.reason}.",
            )
    except urllib_error.URLError as exc:
        return CheckResult(
            "HA Assist pipelines",
            "fail",
            f"Unable to reach conversation endpoint: {exc.reason}.",
        )
    except Exception as exc:
        return CheckResult(
            "HA Assist pipelines",
            "fail",
            f"Assist pipeline verification failed: {exc}.",
        )


def check_llm(config: AssistantConfig) -> CheckResult:
    provider = (config.llm.provider or "").strip().lower()
    if provider == "openai":
        api_key = (config.llm.openai_api_key or "").strip()
        if not api_key:
            return CheckResult("LLM", "fail", "OPENAI_API_KEY is not set but provider is 'openai'.")
        if not api_key.startswith("sk-"):
            return CheckResult("LLM", "fail", "OPENAI_API_KEY does not look like an 'sk-' token.")

        model = config.llm.openai_model or "<unspecified>"
        base_url = config.llm.openai_base_url or "https://api.openai.com/v1"
        return CheckResult("LLM", "ok", f"OpenAI model {model} configured (endpoint {base_url}).")

    if provider == "gemini":
        api_key = (config.llm.gemini_api_key or "").strip()
        if not api_key:
            return CheckResult("LLM", "fail", "GEMINI_API_KEY is not set but provider is 'gemini'.")
        if not api_key.startswith("AI"):
            return CheckResult("LLM", "fail", "GEMINI_API_KEY does not look like an 'AI...'/AIza token.")

        model = config.llm.gemini_model or "<unspecified>"
        base_url = config.llm.gemini_base_url or "https://generativelanguage.googleapis.com/v1beta"
        return CheckResult("LLM", "ok", f"Gemini model {model} configured (endpoint {base_url}).")

    display = provider or "<unknown>"
    return CheckResult(
        "LLM",
        "skip",
        f"LLM provider '{display}' is not validated by this tool (only 'openai' and 'gemini' are supported).",
    )


def _split_wake_word_targets(
    config: AssistantConfig,
    *,
    ha_endpoint_configured: bool,
) -> tuple[list[str], list[str]]:
    pulse_models: list[str] = []
    ha_models: list[str] = []
    for name in config.wake_models:
        pipeline = config.wake_routes.get(name, "pulse")
        if pipeline == "home_assistant" and ha_endpoint_configured:
            ha_models.append(name)
        else:
            pulse_models.append(name)
    return pulse_models, ha_models


def _extract_wake_model_names(info: Info | None) -> list[str]:
    if info is None:
        return []
    names: set[str] = set()
    for model in getattr(info, "models", []):  # type: ignore[arg-type]
        name = getattr(model, "name", None)
        if name:
            names.add(str(name))
    return sorted(names)


def _missing_wake_models(advertised: Sequence[str], expected: Sequence[str]) -> list[str]:
    if not expected:
        return []
    advertised_set = set(advertised)
    return [model for model in expected if model not in advertised_set]


def _missing_wake_model_detail(
    label: str,
    *,
    host: str | None,
    port: int | None,
    expected: Sequence[str],
    advertised: Sequence[str],
    missing: Sequence[str],
) -> str:
    pipeline = "Home Assistant" if label == HA_OPENWAKEWORD_LABEL else "Pulse"
    host_display = f"{host}:{port}" if host and port else "the configured endpoint"
    expected_display = ", ".join(expected) or "<none>"
    advertised_display = ", ".join(advertised) or "<none>"
    missing_display = ", ".join(missing)
    guidance = (
        "Install or preload the missing model(s) on that server (or adjust "
        "PULSE_ASSISTANT_WAKE_WORDS_*). Run bin/tools/list-wake-models.py for details."
    )
    return (
        f"{pipeline} wake pipeline expects [{expected_display}] but {host_display} advertised "
        f"[{advertised_display}] (missing: {missing_display}). {guidance}"
    )


def check_wyoming_endpoints(config: AssistantConfig, env: dict[str, str], timeout: float) -> list[CheckResult]:
    checks: list[tuple[str, str, str, WyomingEndpoint]] = [
        ("Wyoming Whisper", "WYOMING_WHISPER_HOST", "WYOMING_WHISPER_PORT", config.stt_endpoint),
        ("Wyoming Piper", "WYOMING_PIPER_HOST", "WYOMING_PIPER_PORT", config.tts_endpoint),
        (OPENWAKEWORD_LABEL, "WYOMING_OPENWAKEWORD_HOST", "WYOMING_OPENWAKEWORD_PORT", config.wake_endpoint),
    ]
    ha_wake_endpoint = config.home_assistant.wake_endpoint
    ha_endpoint_configured = ha_wake_endpoint is not None
    pulse_models, ha_models = _split_wake_word_targets(
        config,
        ha_endpoint_configured=ha_endpoint_configured,
    )
    if ha_endpoint_configured and ha_wake_endpoint is not None:
        checks.append(
            (
                HA_OPENWAKEWORD_LABEL,
                "HOME_ASSISTANT_OPENWAKEWORD_HOST",
                "HOME_ASSISTANT_OPENWAKEWORD_PORT",
                ha_wake_endpoint,
            )
        )

    expected_models: dict[str, list[str]] = {OPENWAKEWORD_LABEL: pulse_models}
    if ha_endpoint_configured:
        expected_models[HA_OPENWAKEWORD_LABEL] = ha_models

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

        expected = expected_models.get(name, [])
        advertised = _extract_wake_model_names(info) if name in expected_models else []
        if expected and advertised:
            missing = _missing_wake_models(advertised, expected)
            if missing:
                detail = _missing_wake_model_detail(
                    name,
                    host=host,
                    port=port,
                    expected=expected,
                    advertised=advertised,
                    missing=missing,
                )
                results.append(CheckResult(name, "fail", detail))
                continue

        if name in {OPENWAKEWORD_LABEL, HA_OPENWAKEWORD_LABEL}:
            probe_result = _exercise_openwakeword(
                name,
                host,
                port,
                config,
                timeout,
                models=expected or None,
            )
        else:
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
    if label in {OPENWAKEWORD_LABEL, HA_OPENWAKEWORD_LABEL}:
        return _exercise_openwakeword(label, host, port, config, timeout)
    return None


def _exercise_whisper(host: str, port: int, config: AssistantConfig, timeout: float) -> CheckResult:
    if not wyoming_helpers:
        return CheckResult("Wyoming Whisper", "fail", "Wyoming helpers unavailable for whisper probe.")
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
    if not wyoming_helpers:
        return CheckResult("Wyoming Piper", "fail", "Wyoming helpers unavailable for piper probe.")
    try:
        started, chunks = asyncio.run(_probe_piper_async(host, port, timeout))
    except TimeoutError:
        return CheckResult("Wyoming Piper", "fail", f"Piper probe timed out after {timeout:.1f}s.")
    except OSError as exc:
        return CheckResult("Wyoming Piper", "fail", f"Piper probe failed: {exc}.")
    if not started:
        return CheckResult("Wyoming Piper", "fail", "Piper probe never received AudioStart.")
    return CheckResult("Wyoming Piper", "ok", f"Probe synthesized {chunks} audio chunk(s).")


def _exercise_openwakeword(
    label: str,
    host: str,
    port: int,
    config: AssistantConfig,
    timeout: float,
    models: Sequence[str] | None = None,
) -> CheckResult:
    if not wyoming_helpers:
        return CheckResult(label, "fail", "Wyoming helpers unavailable for wake-word probe.")
    try:
        detection = asyncio.run(_probe_openwakeword_async(host, port, config, timeout, models=models))
    except TimeoutError:
        return CheckResult(label, "fail", f"Wake-word probe timed out after {timeout:.1f}s.")
    except OSError as exc:
        return CheckResult(label, "fail", f"Wake-word probe failed: {exc}.")
    if detection:
        return CheckResult(label, "ok", f"Probe detected wake word '{detection}'.")
    return CheckResult(label, "ok", "Probe returned NotDetected (expected for silence sample).")


async def _probe_whisper_async(host: str, port: int, config: AssistantConfig, timeout: float) -> str | None:
    assert wyoming_helpers is not None  # nosec B101 - dev tool only
    endpoint = WyomingEndpoint(host=host, port=port, model=config.stt_endpoint.model)
    silence = wyoming_helpers.silence_bytes(400, config.mic)
    return await wyoming_helpers.transcribe_audio(
        silence,
        endpoint=endpoint,
        mic=config.mic,
        language=config.language,
        timeout=timeout,
    )


async def _probe_piper_async(host: str, port: int, timeout: float) -> tuple[bool, int]:
    assert wyoming_helpers is not None  # nosec B101 - dev tool only
    endpoint = WyomingEndpoint(host=host, port=port)
    return await wyoming_helpers.probe_synthesize(
        endpoint=endpoint,
        text="Pulse verification ping",
        timeout=timeout,
    )


async def _probe_openwakeword_async(
    host: str,
    port: int,
    config: AssistantConfig,
    timeout: float,
    *,
    models: Sequence[str] | None = None,
) -> str | None:
    assert wyoming_helpers is not None  # nosec B101 - dev tool only
    endpoint = WyomingEndpoint(host=host, port=port)
    target_models = list(models) if models else (config.wake_models or ["hey_jarvis"])
    return await wyoming_helpers.probe_wake_detection(
        endpoint=endpoint,
        mic=config.mic,
        models=target_models,
        timeout=timeout,
    )


async def _describe_endpoint_async(host: str, port: int, timeout: float) -> Info | None:
    assert AsyncTcpClient is not None  # nosec B101 - dev tool only
    assert Describe is not None  # nosec B101 - dev tool only
    assert Info is not None  # nosec B101 - dev tool only

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
    _apply_config_defaults(env, config_path)
    config = AssistantConfig.from_env(env)

    results: list[CheckResult] = []
    results.append(check_mqtt(config, args.timeout))
    results.append(check_remote_logging(env, config.hostname, args.timeout))
    results.append(check_snapcast(env, args.timeout))
    results.append(check_home_assistant(config.home_assistant, args.timeout))
    results.append(check_home_assistant_assist_pipeline(config.home_assistant, env, args.timeout))
    results.append(check_llm(config))
    results.extend(check_wyoming_endpoints(config, env, args.timeout))

    print_summary(results, config_path)
    has_failure = any(result.status == "fail" for result in results)
    return 1 if has_failure else 0


if __name__ == "__main__":
    sys.exit(main())
