#!/usr/bin/env python3
"""List wake-word models advertised by configured Wyoming OpenWakeWord endpoints."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import importlib.util
import sys
import wave
from pathlib import Path
from typing import Any

try:
    from pulse.assistant.config import AssistantConfig, WyomingEndpoint
except ModuleNotFoundError:  # pragma: no cover - runtime convenience
    repo_root = Path(__file__).resolve().parents[2]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    from pulse.assistant.config import AssistantConfig, WyomingEndpoint


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Display the wake-word models each configured OpenWakeWord endpoint exposes.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        help="Path to pulse.conf (defaults to repo ./pulse.conf or /opt/pulse-os/pulse.conf).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=5.0,
        help="Seconds to wait for each Describe request (default: 5).",
    )
    parser.add_argument(
        "--probe",
        action="append",
        metavar="MODEL=PATH",
        help=(
            "Optional audio sample to test a model (WAV 16-bit mono at the configured mic rate or raw PCM). "
            "Repeat to check multiple models."
        ),
    )
    return parser.parse_args()


def _load_verify_helpers() -> Any:
    script_path = Path(__file__).resolve().with_name("verify-conf.py")
    if not script_path.exists():
        raise FileNotFoundError(f"Unable to locate verify-conf.py at {script_path}")

    spec = importlib.util.spec_from_file_location("_pulse_verify_conf", script_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load verify helpers from {script_path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)  # type: ignore[attr-defined]
    return module


def _describe_endpoint(verify_helpers: Any, host: str, port: int, timeout: float) -> tuple[Any | None, str | None]:
    describe_async = verify_helpers._describe_endpoint_async
    try:
        info = asyncio.run(describe_async(host, port, timeout))
        if info is None:
            return None, "Endpoint closed the connection before sending Describe info."
        return info, None
    except TimeoutError:
        return None, f"Describe request timed out after {timeout:.1f}s."
    except OSError as exc:
        return None, f"Unable to communicate with {host}:{port} ({exc})."


def _format_list(values: list[str]) -> str:
    return ", ".join(values) if values else "<none>"


def _endpoint_for_model(
    model: str,
    config: AssistantConfig,
    *,
    ha_endpoint: WyomingEndpoint | None,
) -> WyomingEndpoint:
    pipeline = config.wake_routes.get(model, "pulse")
    if pipeline == "home_assistant" and ha_endpoint is not None:
        return ha_endpoint
    return config.wake_endpoint


def _parse_probe_map(raw: list[str] | None) -> dict[str, Path]:
    mapping: dict[str, Path] = {}
    if not raw:
        return mapping
    for entry in raw:
        if "=" not in entry:
            raise SystemExit(f"Invalid --probe entry '{entry}'. Expected MODEL=/path/to/sample.wav")
        model, path = entry.split("=", 1)
        model = model.strip()
        if not model:
            raise SystemExit(f"Invalid --probe entry '{entry}'. Model name cannot be empty.")
        sample_path = Path(path.strip()).expanduser()
        if not sample_path.exists():
            raise SystemExit(f"Probe sample not found: {sample_path}")
        mapping[model] = sample_path
    return mapping


def _load_sample_bytes(sample_path: Path, mic_config) -> bytes:
    if sample_path.suffix.lower() == ".wav":
        with contextlib.closing(wave.open(str(sample_path), "rb")) as wav_file:
            rate = wav_file.getframerate()
            channels = wav_file.getnchannels()
            width = wav_file.getsampwidth()
            if rate != mic_config.rate or channels != mic_config.channels or width != mic_config.width:
                raise ValueError(
                    f"WAV sample {sample_path} does not match mic config "
                    f"(rate={rate}, channels={channels}, width={width})."
                )
            frames = wav_file.readframes(wav_file.getnframes())
            if not frames:
                raise ValueError(f"WAV sample {sample_path} is empty.")
            return frames
    data = sample_path.read_bytes()
    if not data:
        raise ValueError(f"Sample {sample_path} is empty.")
    return data


async def _probe_model_async(
    verify_helpers: Any,
    endpoint: WyomingEndpoint,
    config: AssistantConfig,
    model: str,
    audio: bytes,
    timeout: float,
):
    return await verify_helpers.wyoming_helpers.probe_wake_detection(
        endpoint=endpoint,
        mic=config.mic,
        models=[model],
        audio=audio,
        timeout=timeout,
    )


def main() -> int:
    args = parse_args()
    verify_helpers = _load_verify_helpers()

    if getattr(verify_helpers, "AsyncTcpClient", None) is None or getattr(verify_helpers, "Describe", None) is None:
        print(
            "Wyoming protocol helpers are unavailable. Install the project's voice dependencies first.",
            file=sys.stderr,
        )
        return 1

    config_path = verify_helpers.resolve_config_path(args.config)
    env = verify_helpers.load_env_from_config(config_path)
    verify_helpers._apply_config_defaults(env, config_path)  # type: ignore[attr-defined]
    config = AssistantConfig.from_env(env)
    probe_map = _parse_probe_map(args.probe)

    ha_endpoint = config.home_assistant.wake_endpoint
    ha_endpoint_configured = ha_endpoint is not None

    split_targets = verify_helpers._split_wake_word_targets
    pulse_runtime, ha_runtime = split_targets(config, ha_endpoint_configured=ha_endpoint_configured)
    pulse_intended, ha_intended = split_targets(config, ha_endpoint_configured=True)

    sections: list[dict[str, Any]] = [
        {
            "title": "Pulse OpenWakeWord",
            "pipeline": "Pulse",
            "endpoint": config.wake_endpoint,
            "expected": pulse_runtime,
        }
    ]
    if ha_endpoint_configured and ha_endpoint is not None:
        sections.append(
            {
                "title": "Home Assistant OpenWakeWord",
                "pipeline": "Home Assistant",
                "endpoint": ha_endpoint,
                "expected": ha_runtime,
            }
        )

    extract_models = verify_helpers._extract_wake_model_names

    had_error = False
    for section in sections:
        endpoint = section["endpoint"]
        title = section["title"]
        pipeline = section["pipeline"]
        expected = section["expected"]
        host = endpoint.host
        port = endpoint.port
        print(f"{title} ({host}:{port})")
        print(f"  Pipeline: {pipeline}")
        print(f"  Expected models: {_format_list(expected)}")
        info, error = _describe_endpoint(verify_helpers, host, port, args.timeout)
        if error:
            had_error = True
            print(f"  Error: {error}")
            print()
            continue
        advertised = extract_models(info)
        print(f"  Advertised models ({len(advertised)}): {_format_list(advertised)}")
        if advertised:
            missing = [model for model in expected if model not in advertised]
            if missing:
                had_error = True
                print(f"  Missing models: {_format_list(missing)}")
        elif expected:
            print("  Note: endpoint did not advertise any models; detection may still work if models are preloaded.")
        service_name = getattr(info, "name", None) or "Service"
        version = getattr(info, "version", None) or ""
        type_list = [t for t in getattr(info, "types", []) if t]
        type_display = ", ".join(type_list) if type_list else "unknown"
        service_line = service_name if not version else f"{service_name} {version}"
        print(f"  Service: {service_line}")
        print(f"  Types: {type_display}")
        print()

    if ha_intended and not ha_endpoint_configured:
        print(
            "Note: HOME_ASSISTANT_OPENWAKEWORD_* is unset, so these Home Assistant "
            "wake words run on the Pulse endpoint:",
        )
        print(f"  {_format_list(ha_intended)}")
        print()

    if probe_map:
        print("Probe results")
        for model, sample_path in probe_map.items():
            endpoint = _endpoint_for_model(model, config, ha_endpoint=ha_endpoint if ha_endpoint_configured else None)
            pipeline = (
                "Home Assistant" if ha_endpoint_configured and ha_endpoint and endpoint == ha_endpoint else "Pulse"
            )
            try:
                audio_bytes = _load_sample_bytes(sample_path, config.mic)
            except ValueError as exc:
                had_error = True
                print(f"  [{model}] Sample error: {exc}")
                continue
            try:
                detection = asyncio.run(
                    _probe_model_async(
                        verify_helpers,
                        endpoint,
                        config,
                        model,
                        audio_bytes,
                        timeout=args.timeout,
                    )
                )
            except Exception as exc:  # pragma: no cover - network/IO failures
                had_error = True
                print(f"  [{model}] Probe failed ({pipeline} {endpoint.host}:{endpoint.port}): {exc}")
                continue
            if detection:
                print(f"  [{model}] Detected via {pipeline} ({endpoint.host}:{endpoint.port}) as '{detection}'.")
            else:
                had_error = True
                print(
                    f"  [{model}] No detection reported by {pipeline} ({endpoint.host}:{endpoint.port}). "
                    "Confirm the sample contains the wake phrase and the model is loaded."
                )
        print()

    return 1 if had_error else 0


if __name__ == "__main__":
    raise SystemExit(main())
