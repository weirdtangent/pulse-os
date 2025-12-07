#!/usr/bin/env python3
"""List wake-word models advertised by configured Wyoming OpenWakeWord endpoints."""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import sys
from pathlib import Path
from typing import Any

try:
    from pulse.assistant.config import AssistantConfig
except ModuleNotFoundError:  # pragma: no cover - runtime convenience
    repo_root = Path(__file__).resolve().parents[2]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    from pulse.assistant.config import AssistantConfig


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


def main() -> int:
    args = parse_args()
    verify_helpers = _load_verify_helpers()

    if getattr(verify_helpers, "AsyncTcpClient", None) is None or getattr(verify_helpers, "Describe", None) is None:
        print(
            "Wyoming protocol helpers are unavailable. " "Install the project's voice dependencies first.",
            file=sys.stderr,
        )
        return 1

    config_path = verify_helpers.resolve_config_path(args.config)
    env = verify_helpers.load_env_from_config(config_path)
    verify_helpers._apply_config_defaults(env, config_path)  # type: ignore[attr-defined]
    config = AssistantConfig.from_env(env)

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

    return 1 if had_error else 0


if __name__ == "__main__":
    raise SystemExit(main())
