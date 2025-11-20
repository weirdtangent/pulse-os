#!/usr/bin/env python3
"""Print only the non-default values from pulse.conf."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    from sync_pulse_conf import parse_config_file
except ImportError:  # pragma: no cover - invoked outside repo root
    script_dir = Path(__file__).resolve().parent
    if str(script_dir) not in sys.path:
        sys.path.insert(0, str(script_dir))
    from sync_pulse_conf import parse_config_file  # type: ignore


def _resolve_repo_root() -> Path:
    default = Path("/opt/pulse-os")
    if default.exists():
        return default
    return Path(__file__).resolve().parents[2]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Show non-default pulse.conf values.")
    parser.add_argument(
        "--config",
        type=Path,
        help="Path to pulse.conf (defaults to ./pulse.conf, then /opt/pulse-os/pulse.conf).",
    )
    parser.add_argument(
        "--sample",
        type=Path,
        help="Path to pulse.conf.sample (defaults to ./pulse.conf.sample or /opt/pulse-os/pulse.conf.sample).",
    )
    return parser.parse_args()


def _resolve_config_path(cli_path: Path | None, fallback: Path) -> Path:
    if cli_path:
        target = cli_path.expanduser().resolve()
        if not target.exists():
            raise SystemExit(f"Config file not found: {target}")
        return target
    if fallback.exists():
        return fallback
    raise SystemExit(f"Config file not found: {fallback}")


def _format_assignment(name: str, value: str) -> str:
    if "\n" in value:
        return value
    return f'{name}="{value}"'


def main() -> int:
    args = _parse_args()
    repo_root = _resolve_repo_root()

    config_path = _resolve_config_path(args.config, repo_root / "pulse.conf")
    sample_path = _resolve_config_path(args.sample, repo_root / "pulse.conf.sample")

    sample_vars, _, _ = parse_config_file(sample_path)
    user_vars, _, _ = parse_config_file(config_path)

    defaults = sample_vars

    printed = False
    for var_name in sorted(user_vars):
        user_value = user_vars[var_name]
        default_value = defaults.get(var_name)
        if default_value is not None and user_value == default_value:
            continue
        print(_format_assignment(var_name, user_value))
        printed = True
    return 0 if printed else 1


if __name__ == "__main__":
    sys.exit(main())

