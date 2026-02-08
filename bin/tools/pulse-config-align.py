#!/usr/bin/env python3
"""Compare local pulse.conf against one or more remote hosts."""

from __future__ import annotations

import argparse
import concurrent.futures
import importlib.util
import os
import subprocess  # nosec B404 - required for controlled sync subprocesses
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

ParseResult = tuple[dict[str, str], dict[str, str], set[str], set[str] | None]
ParseFunc = Callable[[Path], ParseResult]


def _log(message: str) -> None:
    print(message)
    sys.stdout.flush()


def _resolve_repo_dir(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()

    default = Path("/opt/pulse-os")
    if default.exists():
        return default

    return Path(__file__).resolve().parents[2]


def _default_local_config(repo_dir: Path) -> Path:
    repo_config = repo_dir / "pulse.conf"
    if repo_config.exists():
        return repo_config
    return Path("/opt/pulse-os/pulse.conf")


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


def _format_value(value: str | None) -> list[str]:
    if value is None:
        return ["<missing>"]
    if value == "":
        return ['""']
    if "\n" in value:
        lines = value.splitlines()
        return ['"""'] + lines + ['"""']
    return [f'"{value}"']


def _collect_differences(
    local_vars: dict[str, str],
    remote_vars: dict[str, str],
    ignore_vars: set[str] | None = None,
) -> list[tuple[str, str | None, str | None]]:
    ignore_vars = ignore_vars or set()
    differences: list[tuple[str, str | None, str | None]] = []
    for name in sorted(set(local_vars) | set(remote_vars)):
        if name in ignore_vars:
            continue
        local_value = local_vars.get(name)
        remote_value = remote_vars.get(name)
        if local_value == remote_value:
            continue
        differences.append((name, local_value, remote_value))
    return differences


def _print_differences(
    host: str, differences: list[tuple[str, str | None, str | None]], base_label: str = "local"
) -> None:
    for line in _render_differences(host, differences, base_label):
        print(line)


def _render_differences(host: str, differences: list[tuple[str, str | None, str | None]], base_label: str) -> list[str]:
    if not differences:
        return [f"{host}: no differences"]

    label_width = max(len(host), len(base_label))
    lines = [f"{host}: {len(differences)} difference(s)"]
    for name, local_value, remote_value in differences:
        lines.append(f"- {name}")
        lines.append(_format_value_line(base_label, local_value, label_width))
        lines.extend(_format_extra_lines(base_label, local_value, label_width))
        lines.append(_format_value_line(host, remote_value, label_width))
        lines.extend(_format_extra_lines(host, remote_value, label_width))
    return lines


def _find_differing_vars(
    vars_by_host: dict[str, dict[str, str]],
    ignore_vars: set[str] | None = None,
) -> set[str]:
    """Find all variable names that have different values across any hosts."""
    ignore_vars = ignore_vars or set()
    all_vars: set[str] = set()
    for host_vars in vars_by_host.values():
        all_vars.update(host_vars.keys())
    all_vars -= ignore_vars

    differing: set[str] = set()
    for var in all_vars:
        values = set()
        for host_vars in vars_by_host.values():
            values.add(host_vars.get(var))
        if len(values) > 1:
            differing.add(var)
    return differing


def _print_consolidated_differences(
    vars_by_host: dict[str, dict[str, str]],
    ignore_vars: set[str] | None = None,
    label: str = "Differences across all hosts",
) -> None:
    """Print a consolidated view of variables that differ across hosts."""
    differing_vars = _find_differing_vars(vars_by_host, ignore_vars)
    if not differing_vars:
        _log(f"{label}: no differences")
        return

    hosts = sorted(vars_by_host.keys())
    label_width = max(len(h) for h in hosts)

    _log(f"{label}: {len(differing_vars)} variable(s) differ")
    for var in sorted(differing_vars):
        print(f"- {var}")
        for host in hosts:
            value = vars_by_host[host].get(var)
            _print_value(host, value, label_width)


def _format_value_line(label: str, value: str | None, label_width: int) -> str:
    formatted = _format_value(value)
    return f"  {label:<{label_width}}: {formatted[0]}"


def _format_extra_lines(label: str, value: str | None, label_width: int) -> list[str]:
    formatted = _format_value(value)
    if len(formatted) <= 1:
        return []
    return [f"  {'':<{label_width}}  {line}" for line in formatted[1:]]


def _flush() -> None:
    try:
        sys.stdout.flush()
    except Exception:  # nosec B110 - parsing external data
        pass


def _print_value(label: str, value: str | None, label_width: int) -> None:
    formatted = _format_value(value)
    print(f"  {label:<{label_width}}: {formatted[0]}")
    for line in formatted[1:]:
        print(f"  {'':<{label_width}}  {line}")


def _fetch_remote_config(host: str, remote_path: Path) -> Path:
    _log(f"Fetching remote config from {host}:{remote_path}")
    with tempfile.NamedTemporaryFile(delete=False, prefix=f"pulse-conf-{host}-", suffix=".tmp") as handle:
        tmp_path = Path(handle.name)
        result = subprocess.run(  # nosec B603 B607 - hardcoded command array
            ["ssh", host, "cat", str(remote_path)],
            stdout=handle,
            stderr=subprocess.PIPE,
            text=True,
        )
    if result.returncode != 0:
        tmp_path.unlink(missing_ok=True)
        stderr = (result.stderr or "").strip()
        message = stderr if stderr else f"ssh exited with {result.returncode}"
        raise RuntimeError(f"Failed to fetch config from {host}: {message}")
    return tmp_path


def _open_editor(path: Path) -> None:
    editor = os.environ.get("EDITOR") or "vi"
    _log(f"Opening editor '{editor}' for {path}")
    result = subprocess.run([editor, str(path)])  # nosec B603 - hardcoded command array
    if result.returncode != 0:
        raise RuntimeError(f"Editor {editor} exited with {result.returncode}")


def _push_remote_config(host: str, local_path: Path, remote_path: Path) -> None:
    _log(f"Pushing updated config to {host}:{remote_path}")
    result = subprocess.run(  # nosec B603 B607 - hardcoded command array
        ["scp", str(local_path), f"{host}:{remote_path}"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        message = stderr if stderr else f"scp exited with {result.returncode}"
        raise RuntimeError(f"Failed to push config to {host}: {message}")


def _run_remote_setup(host: str, remote_path: Path) -> None:
    setup_path = remote_path.parent / "setup.sh"
    _log(f"Running setup.sh on {host}")
    result = subprocess.run(  # nosec B603 B607 - hardcoded command array
        ["ssh", host, str(setup_path)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        message = stderr if stderr else f"ssh exited with {result.returncode}"
        raise RuntimeError(f"Failed to run setup.sh on {host}: {message}")


def _extract_vars(parse_result: ParseResult | tuple) -> dict[str, str]:
    if not isinstance(parse_result, tuple) or not parse_result:
        raise ValueError("parse_config_file returned unexpected result")
    return parse_result[0]


def _confirm_next() -> bool:
    while True:
        try:
            response = input("Proceed to next pulse device? [Y/n/q]: ").strip().lower()
        except EOFError:
            return False
        if response in {"", "y", "yes"}:
            return True
        if response in {"n", "no", "q", "quit", "exit"}:
            return False
        print("Please answer 'y' to continue or 'n/q' to stop.")


def _confirm_edit(host: str) -> bool:
    while True:
        try:
            response = input(f"Edit/push {host}? [y/N]: ").strip().lower()
        except EOFError:
            return False
        if response in {"y", "yes"}:
            return True
        if response in {"", "n", "no"}:
            return False
        print("Please answer 'y' to edit/push or 'n' to skip.")


def _apply_overrides(config_path: Path, overrides: dict[str, str | None]) -> None:
    if not overrides:
        return
    _REMOVED_MARKER = "\x00__REMOVE__"  # sentinel for lines to remove
    lines = config_path.read_text(encoding="utf-8").splitlines()
    updated: set[str] = set()
    for idx, line in enumerate(lines):
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in line:
            continue
        key_part = line.split("=", 1)[0].strip()
        if key_part in overrides:
            value = overrides[key_part]
            updated.add(key_part)
            if value is None:
                lines[idx] = _REMOVED_MARKER  # mark for removal
            else:
                lines[idx] = f'{key_part}="{value}"'
    # Append missing overrides (only for non-removals)
    for key, value in overrides.items():
        if key in updated:
            continue
        if value is None:
            continue
        lines.append(f'{key}="{value}"')
    # Drop removed lines
    lines = [ln for ln in lines if ln != _REMOVED_MARKER]
    config_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    applied_desc = []
    for key, value in overrides.items():
        applied_desc.append(f"{key}=<removed>" if value is None else f"{key}={value}")
    _log(f"Applied overrides to {config_path}: {', '.join(sorted(applied_desc))}")


def _overrides_needed(current: dict[str, str], overrides: dict[str, str | None]) -> bool:
    for key, value in overrides.items():
        if value is None and key in current:
            return True
        if value is not None and current.get(key) != value:
            return True
    return False


def _load_hosts(hosts_arg: list[str], devices_file: Path) -> list[str]:
    if hosts_arg:
        return hosts_arg
    if not devices_file.exists():
        print(
            f"Error: no hosts specified and {devices_file} not found. "
            "Create it (one host per line) or pass hosts explicitly.",
            file=sys.stderr,
        )
        return []
    hosts: list[str] = []
    with devices_file.open(encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            hosts.append(stripped)
    if not hosts:
        print(f"Error: {devices_file} is empty; add hostnames or pass hosts explicitly.", file=sys.stderr)
    return hosts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Show config differences between this host and one or more peers")
    parser.add_argument(
        "hosts",
        nargs="*",
        help="Hostnames or SSH targets to compare against (falls back to devices file if omitted)",
    )
    parser.add_argument(
        "--repo",
        help="Path to the PulseOS repository root (defaults to /opt/pulse-os if present)",
    )
    parser.add_argument(
        "--local-config",
        help="Path to the local pulse.conf (defaults to <repo>/pulse.conf)",
    )
    parser.add_argument(
        "--remote-path",
        default="/opt/pulse-os/pulse.conf",
        help="Remote path to pulse.conf on target hosts",
    )
    parser.add_argument(
        "--edit",
        action="store_true",
        help="Open each fetched remote config in $EDITOR before comparison and optional push",
    )
    parser.add_argument(
        "--push",
        action="store_true",
        help="After optional edits, push the config back to the remote host",
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Prompt before proceeding to each next host",
    )
    parser.add_argument(
        "--auto-apply",
        action="store_true",
        help="When editing/pushing, skip per-host edit confirmation prompts",
    )
    parser.add_argument(
        "--ignore-var",
        action="append",
        default=[],
        help="Variable name to ignore in diffs (can be passed multiple times)",
    )
    parser.add_argument(
        "--set-var",
        action="append",
        default=[],
        help=(
            "Set VAR=VALUE on all hosts before optional edit/push (repeatable). "
            "Use VAR= (empty value) to remove a setting."
        ),
    )
    parser.add_argument(
        "--devices-file",
        help=(
            "File containing hostnames (one per line) used if no hosts are passed "
            "(defaults to <repo>/pulse-devices.conf)"
        ),
    )
    parser.add_argument(
        "--compare",
        action="store_true",
        help="Show diff/summary output explicitly (useful with --set-var only runs)",
    )
    args = parser.parse_args(argv)

    repo_dir = _resolve_repo_dir(args.repo)
    parse_config = _load_parse_function(repo_dir)

    local_config = (
        Path(args.local_config).expanduser().resolve() if args.local_config else _default_local_config(repo_dir)
    )
    devices_file = (
        Path(args.devices_file).expanduser().resolve() if args.devices_file else (repo_dir / "pulse-devices.conf")
    )
    local_vars: dict[str, str] | None = None
    if local_config.exists():
        _log(f"Using local config {local_config}")
        local_vars = _extract_vars(parse_config(local_config))
    elif args.local_config:
        print(f"Error: local config {local_config} not found", file=sys.stderr)
        return 1
    else:
        print(f"Warning: local config {local_config} not found; comparing remotes to each other", file=sys.stderr)
    default_ignored = {
        "PULSE_BT_MAC",
        "PULSE_DISPLAY_TYPE",
        "PULSE_BLUETOOTH_AUTOCONNECT",
    }
    ignored_vars = default_ignored | {name.strip() for name in args.ignore_var}
    overrides: dict[str, str | None] = {}
    for item in args.set_var:
        if "=" not in item:
            print(f"Invalid --set-var '{item}', expected VAR=VALUE", file=sys.stderr)
            return 1
        key, value = item.split("=", 1)
        key = key.strip()
        if not key:
            print(f"Invalid --set-var '{item}', missing variable name", file=sys.stderr)
            return 1
        overrides[key] = value if value != "" else None
    hosts_list = _load_hosts(args.hosts, devices_file)
    if not hosts_list:
        return 1

    exit_code = 0
    remote_path = Path(args.remote_path)
    temp_paths: dict[str, Path] = {}
    pre_vars_by_host: dict[str, dict[str, str]] = {}
    post_vars_by_host: dict[str, dict[str, str]] = {}
    local_diff_summary: list[tuple[str, int]] = []
    diff_outputs: list[list[str]] = []
    overrides_only = bool(overrides) and not args.edit and not args.push
    show_diffs = args.compare or not overrides_only

    # Stage 1: fetch all configs
    for host in hosts_list:
        try:
            tmp = _fetch_remote_config(host, remote_path)
            temp_paths[host] = tmp
            pre_vars_by_host[host] = _extract_vars(parse_config(tmp))
        except Exception as exc:
            exit_code = 1
            print(f"{host}: error during fetch - {exc}", file=sys.stderr)

    # Stage 2: show pre-edit diffs (optional)
    hosts = sorted(pre_vars_by_host)
    if show_diffs:
        if local_vars is not None:
            for host in hosts:
                differences = _collect_differences(local_vars, pre_vars_by_host[host], ignore_vars=ignored_vars)
                _log(f"Pre-edit differences for {host} vs local:")
                _print_differences(host, differences, base_label="local")
                local_diff_summary.append((host, len(differences)))
                diff_outputs.append(_render_differences(host, differences, base_label="local"))
        if hosts:
            _print_consolidated_differences(pre_vars_by_host, ignore_vars=ignored_vars, label="Pre-edit comparison")
        _flush()

    # If nothing to change, stop after optional diffs
    if not args.edit and not args.push and not overrides:
        for tmp in temp_paths.values():
            if tmp.exists():
                tmp.unlink(missing_ok=True)
        return exit_code

    # Stage 3: optional edit/push per host, after diffs have been shown
    auto_push_overrides = bool(overrides) and not args.edit and not args.push

    if auto_push_overrides:
        _log("Applying overrides and pushing to all hosts in parallel...")

        def _apply_and_push(host: str) -> tuple[str, bool, str | None]:
            tmp = temp_paths.get(host)
            if not tmp or not tmp.exists():
                return host, False, "temp file missing"
            current_vars = pre_vars_by_host.get(host, {})
            if not _overrides_needed(current_vars, overrides):
                return host, True, "skipped (already up to date)"
            try:
                _apply_overrides(tmp, overrides)
                _push_remote_config(host, tmp, remote_path)
                _run_remote_setup(host, remote_path)
                post_vars_by_host[host] = _extract_vars(parse_config(tmp))
                return host, True, None
            except Exception as exc:  # noqa: BLE001
                return host, False, str(exc)

        with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(temp_paths))) as executor:
            futures = {executor.submit(_apply_and_push, host): host for host in hosts_list}
            for future in concurrent.futures.as_completed(futures):
                host = futures[future]
                ok = False
                err = None
                try:
                    host, ok, err = future.result()
                except Exception as exc:  # noqa: BLE001
                    err = str(exc)
                if ok and err and "skipped" in err:
                    _log(f"{host}: {err}")
                elif ok:
                    _log(f"{host}: overrides applied and pushed")
                else:
                    exit_code = 1
                    _log(f"{host}: error applying overrides - {err}")
    else:
        stop = False
        for idx, host in enumerate(hosts_list):
            host_tmp = temp_paths.get(host)
            if not host_tmp or not host_tmp.exists():
                continue
            try:
                do_edit = True
                if not args.auto_apply:
                    do_edit = _confirm_edit(host)
                if not do_edit:
                    _log(f"Skipping edit/push for {host}")
                    continue
                override_needed = _overrides_needed(pre_vars_by_host.get(host, {}), overrides) if overrides else False
                if overrides and override_needed:
                    _apply_overrides(host_tmp, overrides)
                elif overrides:
                    _log(f"{host}: overrides already match; not applying override changes")
                if args.edit:
                    _open_editor(host_tmp)
                do_push = args.push and (args.edit or override_needed)
                if do_push:
                    _push_remote_config(host, host_tmp, remote_path)
                    _run_remote_setup(host, remote_path)
                post_vars_by_host[host] = _extract_vars(parse_config(host_tmp))
            except Exception as exc:
                exit_code = 1
                print(f"{host}: error during edit/push - {exc}", file=sys.stderr)
                continue
            if args.confirm and idx != len(args.hosts) - 1:
                if not _confirm_next():
                    _log("Stopping before next host per user request.")
                    stop = True
                    break
        if stop:
            # Cleanup temps before exit
            for tmp in temp_paths.values():
                if tmp.exists():
                    tmp.unlink(missing_ok=True)
            return exit_code

    # Stage 4: post-edit diffs (only for hosts we edited/pushed)
    if post_vars_by_host:
        hosts = sorted(post_vars_by_host)
        if show_diffs:
            if local_vars is not None:
                for host in hosts:
                    differences = _collect_differences(local_vars, post_vars_by_host[host], ignore_vars=ignored_vars)
                    _log(f"Post-edit differences for {host} vs local:")
                    _print_differences(host, differences, base_label="local")
            if hosts:
                _print_consolidated_differences(
                    post_vars_by_host, ignore_vars=ignored_vars, label="Post-edit comparison"
                )

    if show_diffs:
        if local_diff_summary:
            _log("Summary vs local:")
            for host, count in local_diff_summary:
                _log(f"  {host}: {count} difference(s)")

        if diff_outputs:
            _log("=== Diff Results (collected) ===")
            for block in diff_outputs:
                for line in block:
                    print(line)
                print("")

    # Cleanup temps
    for tmp in temp_paths.values():
        if tmp.exists():
            tmp.unlink(missing_ok=True)

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
