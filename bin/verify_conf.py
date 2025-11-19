#!/usr/bin/env python3
"""Connectivity smoke-test for pulse.conf."""

from __future__ import annotations

import argparse
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

try:
    import paho.mqtt.client as mqtt
except ModuleNotFoundError:  # pragma: no cover - aids bootstrap
    mqtt = None  # type: ignore[assignment]

try:
    from pulse.assistant.config import AssistantConfig, WyomingEndpoint
except ModuleNotFoundError:
    repo_dir = Path(__file__).resolve().parents[1]
    if str(repo_dir) not in sys.path:
        sys.path.insert(0, str(repo_dir))
    from pulse.assistant.config import AssistantConfig, WyomingEndpoint

Status = Literal["ok", "fail", "skip"]


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

    repo_candidate = Path(__file__).resolve().parents[1] / "pulse.conf"
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


def check_wyoming_endpoints(config: AssistantConfig, env: dict[str, str], timeout: float) -> list[CheckResult]:
    checks: list[tuple[str, str, str, WyomingEndpoint]] = [
        ("Wyoming Whisper", "WYOMING_WHISPER_HOST", "WYOMING_WHISPER_PORT", config.stt_endpoint),
        ("Wyoming Piper", "WYOMING_PIPER_HOST", "WYOMING_PIPER_PORT", config.tts_endpoint),
        ("Wyoming OpenWakeWord", "WYOMING_OPENWAKEWORD_HOST", "WYOMING_OPENWAKEWORD_PORT", config.wake_endpoint),
    ]
    results: list[CheckResult] = []
    for name, host_key, port_key, endpoint in checks:
        results.append(_check_wyoming_endpoint(name, host_key, port_key, endpoint, env, timeout))
    return results


def _check_wyoming_endpoint(
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
    results.extend(check_wyoming_endpoints(config, env, args.timeout))

    print_summary(results, config_path)
    has_failure = any(result.status == "fail" for result in results)
    return 1 if has_failure else 0


if __name__ == "__main__":
    sys.exit(main())
