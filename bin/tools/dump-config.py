#!/usr/bin/env python3
"""Print only the non-default values from pulse.conf."""

from __future__ import annotations

import argparse
import runpy
import sys
from pathlib import Path


def _load_parse_fn(script_dir: Path):
    candidate = script_dir / "sync-pulse-conf.py"
    if not candidate.exists():
        raise SystemExit(f"Missing helper script: {candidate}")
    module_globals = runpy.run_path(str(candidate))
    if "parse_config_file" not in module_globals:
        raise SystemExit("sync-pulse-conf.py does not expose parse_config_file")
    return module_globals["parse_config_file"]


PARSE_CONFIG_FILE = _load_parse_fn(Path(__file__).resolve().parent)


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

    sample_vars, _, _ = PARSE_CONFIG_FILE(sample_path)
    user_vars, _, _ = PARSE_CONFIG_FILE(config_path)

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

