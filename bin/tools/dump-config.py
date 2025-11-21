#!/usr/bin/env python3
"""Print PulseOS config variables that diverge from pulse.conf.sample defaults."""

from __future__ import annotations

import argparse
import importlib.util
import sys
from collections.abc import Callable
from pathlib import Path

ParseResult = tuple[dict[str, str], dict[str, str], set[str]]
ParseFunc = Callable[[Path], ParseResult]


def _resolve_repo_dir(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()

    default = Path("/opt/pulse-os")
    if default.exists():
        return default

    return Path(__file__).resolve().parents[2]


def _load_parse_function(repo_dir: Path) -> ParseFunc:
    script_path = repo_dir / "bin" / "tools" / "sync-pulse-conf.py"
    if not script_path.exists():
        raise FileNotFoundError(f"Unable to locate sync tool at {script_path}")

    spec = importlib.util.spec_from_file_location("_sync_pulse_conf", script_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not import parser from {script_path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[attr-defined]

    parse_func = getattr(module, "parse_config_file", None)
    if not callable(parse_func):
        raise AttributeError("sync-pulse-conf.py does not expose parse_config_file()")

    return parse_func


def _quote_value(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _changed_assignments(sample_vars: dict[str, str], user_vars: dict[str, str]) -> list[str]:
    changed: list[str] = []
    for name in sorted(user_vars):
        user_value = user_vars[name]
        default_value = sample_vars.get(name)
        if default_value == user_value:
            continue
        if "\n" in user_value:
            changed.append(user_value)
        else:
            changed.append(f"{name}={_quote_value(user_value)}")
    return changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Print config overrides relative to pulse.conf.sample")
    parser.add_argument("--repo", help="Path to the PulseOS repository root")
    parser.add_argument("--config", help="Path to pulse.conf (defaults to <repo>/pulse.conf)")
    parser.add_argument("--sample", help="Path to pulse.conf.sample (defaults to <repo>/pulse.conf.sample)")
    args = parser.parse_args(argv)

    repo_dir = _resolve_repo_dir(args.repo)
    config_path = Path(args.config).expanduser().resolve() if args.config else (repo_dir / "pulse.conf")
    sample_path = Path(args.sample).expanduser().resolve() if args.sample else (repo_dir / "pulse.conf.sample")

    if not sample_path.exists():
        print(f"Error: {sample_path} not found", file=sys.stderr)
        return 1
    if not config_path.exists():
        print(f"Error: {config_path} not found", file=sys.stderr)
        return 1

    parse_config = _load_parse_function(repo_dir)

    sample_vars, _, _ = parse_config(sample_path)
    user_vars, _, _ = parse_config(config_path)

    changed = _changed_assignments(sample_vars, user_vars)
    if not changed:
        return 0

    for line in changed:
        print(line)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
