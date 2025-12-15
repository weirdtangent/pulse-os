#!/usr/bin/env python3
"""Sync and reformat pulse.conf with pulse.conf.sample.

Reads the user's pulse.conf, merges it with pulse.conf.sample,
preserves user values, adds new variables with defaults,
and reformats everything to match pulse.conf.sample's structure.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any

COMMENT_ASSIGNMENT_RE = re.compile(r"#\s*([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)")
DEFAULT_COMMENT_RE = re.compile(r"#\s*\(default\)\s*([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)")
BASH_IF_RE = re.compile(r"^if\b")
BASH_FI_RE = re.compile(r"^fi\b")

LEGACY_REPLACEMENTS: dict[str, str] = {
    "PULSE_BACKLIGHT_SUN": "PULSE_DAY_NIGHT_AUTO",
    "PULSE_ASSISTANT_WAKE_WORDS": "PULSE_ASSISTANT_WAKE_WORDS_PULSE",
}

INFO_SECTION_HEADER = "Information Services (news, weather, sports)"
INFO_VAR_DEFAULTS: tuple[tuple[str, str], ...] = (
    ("PULSE_NEWS_API_KEY", ""),
    ("PULSE_NEWS_BASE_URL", "https://newsapi.org/v2"),
    ("PULSE_NEWS_COUNTRY", "us"),
    ("PULSE_NEWS_CATEGORY", "general"),
    ("PULSE_NEWS_LANGUAGE", "en"),
    ("PULSE_NEWS_MAX_ARTICLES", "5"),
    ("PULSE_WEATHER_LOCATION", ""),
    ("PULSE_WEATHER_BASE_URL", "https://api.open-meteo.com/v1/forecast"),
    ("PULSE_WEATHER_UNITS", "auto"),
    ("PULSE_WEATHER_LANGUAGE", "en"),
    ("PULSE_WEATHER_FORECAST_DAYS", "3"),
    ("WHAT3WORDS_API_KEY", ""),
    ("PULSE_SPORTS_BASE_URL", "https://site.api.espn.com/apis"),
    ("PULSE_SPORTS_DEFAULT_COUNTRY", "us"),
    ("PULSE_SPORTS_HEADLINE_COUNTRY", "us"),
    ("PULSE_SPORTS_DEFAULT_LEAGUES", "nfl,nba,mlb,nhl"),
    ("PULSE_SPORTS_FAVORITE_TEAMS", ""),
)


def _strip_quotes(value: str) -> str:
    """Remove matching single or double quotes from a value."""
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def parse_config_file(path: Path) -> tuple[dict[str, str], dict[str, str], set[str], set[str]]:
    """Parse a config file and extract variables and comments.

    Returns:
        Tuple of (variables dict, comments dict, placeholder vars set, explicit vars set)
    """
    variables: dict[str, str] = {}
    comments: dict[str, str] = {}
    placeholder_vars: set[str] = set()
    explicit_vars: set[str] = set()
    current_comment: list[str] = []
    current_comment_has_new_marker = False
    in_bash_block = False
    bash_block_lines: list[str] = []
    bash_block_var = ""
    bash_block_depth = 0

    if not path.exists():
        return variables, comments, placeholder_vars, explicit_vars

    with path.open(encoding="utf-8") as handle:
        lines = handle.readlines()
        i = 0
        while i < len(lines):
            line = lines[i]
            stripped = line.strip()
            original_line = line.rstrip()

            # Check if we're entering a bash code block (PULSE_VERSION)
            if "if [[ -z" in stripped or in_bash_block:
                if not in_bash_block:
                    # Starting a bash block - find the variable name before it
                    # Look backwards for the variable assignment
                    bash_block_var = "PULSE_VERSION"  # Known case
                    bash_block_lines = [original_line]
                    in_bash_block = True
                    bash_block_depth = 1
                    current_comment = []  # Comments before block belong to it
                    current_comment_has_new_marker = False
                else:
                    bash_block_lines.append(original_line)
                    if BASH_IF_RE.match(stripped):
                        bash_block_depth += 1
                    if BASH_FI_RE.match(stripped):
                        bash_block_depth -= 1
                        if bash_block_depth <= 0:
                            # End of bash block
                            variables[bash_block_var] = "\n".join(bash_block_lines)
                            explicit_vars.add(bash_block_var)
                            if current_comment:
                                comments[bash_block_var] = "\n".join(current_comment)
                            in_bash_block = False
                            bash_block_lines = []
                            current_comment = []
                            current_comment_has_new_marker = False
                            bash_block_depth = 0
                i += 1
                continue

            # Collect comment lines
            if stripped.startswith("#"):
                # Skip section headers (they start with # and have ===)
                if "===" not in stripped:
                    default_match = DEFAULT_COMMENT_RE.match(stripped)
                    if default_match:
                        var_name = default_match.group(1)
                        var_value = _strip_quotes(default_match.group(2))
                        variables[var_name] = var_value
                        if current_comment:
                            comments[var_name] = "\n".join(current_comment)
                            current_comment = []
                        current_comment_has_new_marker = False
                        i += 1
                        continue
                    # Check for NEW marker with variable assignment on the same line
                    if "NEW:" in stripped and "=" in stripped:
                        match = re.search(r"NEW:\s*([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)", stripped)
                        if match:
                            var_name = match.group(1)
                            var_value = _strip_quotes(match.group(2))
                            variables[var_name] = var_value
                            placeholder_vars.add(var_name)
                            comment_lines = current_comment + [stripped] if current_comment else [stripped]
                            comments[var_name] = "\n".join(comment_lines)
                            current_comment = []
                            current_comment_has_new_marker = False
                            i += 1
                            continue
                    # Handle commented assignments that belong to a NEW block
                    if current_comment_has_new_marker:
                        match = COMMENT_ASSIGNMENT_RE.match(stripped)
                        if match:
                            var_name = match.group(1)
                            var_value = _strip_quotes(match.group(2))
                            variables[var_name] = var_value
                            placeholder_vars.add(var_name)
                            if current_comment:
                                comments[var_name] = "\n".join(current_comment)
                            current_comment = []
                            current_comment_has_new_marker = False
                            i += 1
                            continue
                    current_comment.append(stripped)
                    if "NEW:" in stripped:
                        current_comment_has_new_marker = True
                i += 1
                continue

            # Empty line resets comment collection (unless in bash block)
            if not stripped:
                if not in_bash_block:
                    current_comment = []
                    current_comment_has_new_marker = False
                i += 1
                continue

            # Parse variable assignment
            if "=" in stripped and not stripped.startswith("#") and not in_bash_block:
                parts = stripped.split("=", 1)
                if len(parts) == 2:
                    var_name = parts[0].strip()
                    var_value = _strip_quotes(parts[1])

                    variables[var_name] = var_value
                    explicit_vars.add(var_name)

                    # Store comment if we have one
                    if current_comment:
                        comments[var_name] = "\n".join(current_comment)
                        current_comment = []
                        current_comment_has_new_marker = False

            i += 1

    return variables, comments, placeholder_vars, explicit_vars


def extract_sections_from_sample(sample_path: Path) -> list[dict[str, Any]]:
    """Extract section structure from pulse.conf.sample.

    Returns:
        List of section dicts with 'header', 'comment', and 'vars' keys
    """
    sections: list[dict[str, Any]] = []
    current_section: dict[str, Any] | None = None
    current_comment: list[str] = []
    current_comment_has_new_marker = False
    in_bash_block = False
    bash_block_lines: list[str] = []
    bash_block_var = ""
    bash_block_comment: list[str] = []
    bash_block_depth = 0

    def is_separator(line: str) -> bool:
        stripped = line.strip()
        if not stripped.startswith("#"):
            return False
        body = stripped.lstrip("#").strip()
        return bool(body) and set(body) == {"="}

    with sample_path.open(encoding="utf-8") as handle:
        lines = handle.readlines()
        i = 0
        while i < len(lines):
            line = lines[i]
            stripped = line.strip()
            original_line = line.rstrip()

            # Detect section header pattern:
            #   # =========
            #   # Section Name
            #   # =========
            if is_separator(line):
                if (
                    i + 2 < len(lines)
                    and lines[i + 1].strip().startswith("#")
                    and not is_separator(lines[i + 1])
                    and is_separator(lines[i + 2])
                ):
                    header_text = lines[i + 1].strip().lstrip("#").strip()
                    if header_text:
                        if current_section is not None:
                            sections.append(current_section)
                        current_section = {
                            "header": header_text,
                            "comment": "\n".join(current_comment) if current_comment else "",
                            "vars": [],
                        }
                        current_comment = []
                        current_comment_has_new_marker = False
                        i += 3
                        continue

            # Check if we're entering a bash code block (PULSE_VERSION)
            if "if [[ -z" in stripped or in_bash_block:
                if not in_bash_block:
                    bash_block_var = "PULSE_VERSION"  # Known case
                    bash_block_lines = [original_line]
                    bash_block_comment = current_comment.copy()
                    in_bash_block = True
                    bash_block_depth = 1
                    current_comment = []
                    current_comment_has_new_marker = False
                else:
                    bash_block_lines.append(original_line)
                    if BASH_IF_RE.match(stripped):
                        bash_block_depth += 1
                    if BASH_FI_RE.match(stripped):
                        bash_block_depth -= 1
                        if bash_block_depth <= 0:
                            # End of bash block
                            if current_section:
                                current_section["vars"].append(
                                    {
                                        "name": bash_block_var,
                                        "value": "\n".join(bash_block_lines),
                                        "comment": "\n".join(bash_block_comment) if bash_block_comment else "",
                                        "is_block": True,
                                    }
                                )
                            in_bash_block = False
                            bash_block_lines = []
                            bash_block_comment = []
                            bash_block_depth = 0
                i += 1
                continue

            # Collect comments
            if stripped.startswith("#"):
                handled_special_case = False

                default_match = DEFAULT_COMMENT_RE.match(stripped)
                if default_match and current_section:
                    var_name = default_match.group(1)
                    var_value = _strip_quotes(default_match.group(2))
                    comment_lines = current_comment + [stripped] if current_comment else [stripped]
                    current_section["vars"].append(
                        {
                            "name": var_name,
                            "value": var_value,
                            "comment": "\n".join(comment_lines),
                            "is_block": False,
                        }
                    )
                    current_comment = []
                    current_comment_has_new_marker = False
                    handled_special_case = True

                if not handled_special_case and "NEW:" in stripped and "=" in stripped:
                    match = re.search(r"NEW:\s*([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)", stripped)
                    if match and current_section:
                        var_name = match.group(1)
                        var_value = _strip_quotes(match.group(2))
                        comment_lines = current_comment + [stripped] if current_comment else [stripped]
                        current_section["vars"].append(
                            {
                                "name": var_name,
                                "value": var_value,
                                "comment": "\n".join(comment_lines),
                                "is_block": False,
                            }
                        )
                        current_comment = []
                        current_comment_has_new_marker = False
                        handled_special_case = True

                if not handled_special_case and current_comment_has_new_marker:
                    match = COMMENT_ASSIGNMENT_RE.match(stripped)
                    if match and current_section:
                        var_name = match.group(1)
                        var_value = _strip_quotes(match.group(2))
                        current_section["vars"].append(
                            {
                                "name": var_name,
                                "value": var_value,
                                "comment": "\n".join(current_comment),
                                "is_block": False,
                            }
                        )
                        current_comment = []
                        current_comment_has_new_marker = False
                        handled_special_case = True

                if handled_special_case:
                    i += 1
                    continue

                current_comment.append(stripped)
                if "NEW:" in stripped:
                    current_comment_has_new_marker = True
                i += 1
                continue

            # Empty line
            if not stripped:
                if current_comment and not in_bash_block:
                    if current_section:
                        current_section["comment"] += "\n" + "\n".join(current_comment)
                    current_comment = []
                    current_comment_has_new_marker = False
                i += 1
                continue

            # Parse variable
            if "=" in stripped and not stripped.startswith("#") and not in_bash_block:
                parts = stripped.split("=", 1)
                if len(parts) == 2:
                    var_name = parts[0].strip()
                    var_value = _strip_quotes(parts[1])

                    if current_section:
                        current_section["vars"].append(
                            {
                                "name": var_name,
                                "value": var_value,
                                "comment": "\n".join(current_comment) if current_comment else "",
                                "is_block": False,
                            }
                        )
                        current_comment = []
                        current_comment_has_new_marker = False

            i += 1

        # Add last section
        if current_section is not None:
            sections.append(current_section)

    _ensure_info_section(sections)
    return sections


def _ensure_info_section(sections: list[dict[str, Any]]) -> None:
    """Guarantee the Information Services section exists with all known vars."""
    target_section = None
    for section in sections:
        if section.get("header") == INFO_SECTION_HEADER:
            target_section = section
            break
    if target_section is None:
        target_section = {"header": INFO_SECTION_HEADER, "comment": "", "vars": []}
        insert_index = next(
            (idx for idx, section in enumerate(sections) if section.get("header") == "Snapcast Client (optional)"),
            len(sections),
        )
        sections.insert(insert_index, target_section)

    existing = {var_info.get("name") for var_info in target_section.get("vars", [])}
    for var_name, default_value in INFO_VAR_DEFAULTS:
        if var_name in existing:
            continue
        target_section.setdefault("vars", []).append(
            {
                "name": var_name,
                "value": default_value,
                "comment": "",
                "is_block": False,
            }
        )


def _annotate_new_comment(comment: str | None, var_name: str) -> str:
    lines = comment.split("\n") if comment else []
    for idx, line in enumerate(lines):
        stripped = line.lstrip()
        if stripped.startswith("#"):
            prefix_len = len(line) - len(stripped)
            prefix = line[:prefix_len]
            body = stripped.lstrip("#").strip()
            if not body.startswith("NEW:"):
                body_text = body if body else var_name
                lines[idx] = f"{prefix}# NEW: {body_text}"
            return "\n".join(lines)
    lines.insert(0, f"# NEW: {var_name}")
    return "\n".join(lines)


def _strip_new_markers(comment: str | None) -> str:
    """Remove any '# NEW:' prefixes from a stored comment."""
    if not comment:
        return ""

    cleaned: list[str] = []
    for line in comment.split("\n"):
        stripped = line.lstrip()
        if stripped.startswith("#"):
            prefix_len = len(line) - len(stripped)
            prefix = line[:prefix_len]
            body = stripped.lstrip("#").strip()
            if body.startswith("NEW:"):
                body = body[4:].strip()
                if not body:
                    continue
                cleaned.append(f"{prefix}# {body}")
                continue
        cleaned.append(line)
    return "\n".join(cleaned).strip("\n")


def _is_pulse_version_block(block: str | None) -> bool:
    if not block:
        return False
    stripped = block.lstrip()
    return stripped.startswith("if [[ -z") and "PULSE_VERSION" in stripped


def format_config_file(
    sections: list[dict[str, Any]],
    user_vars: dict[str, str],
    user_comments: dict[str, str],
    new_vars: set[str],
    explicit_user_vars: set[str] | None = None,
) -> str:
    """Format the config file with sections, preserving user values and marking new vars."""
    lines: list[str] = []
    explicit_user_vars = explicit_user_vars or set()

    lines.append("# PulseOS configuration file (template)")
    lines.append("# Copy this to: /opt/pulse-os/pulse.conf")
    lines.append("")

    for section in sections:
        # Section header
        if section["header"]:
            lines.append("# " + "=" * 75)
            lines.append(f"# {section['header']}")
            lines.append("# " + "=" * 75)
            lines.append("")

        # Section comment
        if section["comment"]:
            for comment_line in section["comment"].split("\n"):
                if comment_line.strip():
                    lines.append(comment_line)
            lines.append("")

        # Variables in this section
        for var_info in section["vars"]:
            var_name = var_info["name"]

            # Use user's value if they have one, otherwise use default
            var_value = user_vars.get(var_name, var_info["value"])

            # Check if this is a new variable
            is_new = var_name in new_vars

            # Get comment (prefer user's comment if exists, otherwise use sample's)
            comment = user_comments.get(var_name, var_info["comment"])
            if is_new:
                comment = _annotate_new_comment(comment, var_name)
            else:
                comment = _strip_new_markers(comment)

            # Add comment
            if comment:
                for comment_line in comment.split("\n"):
                    if comment_line.strip():
                        lines.append(comment_line)

            # Add variable with NEW marker if applicable
            is_block = bool(var_info.get("is_block")) or var_name == "PULSE_VERSION"
            if is_block:
                block_source = user_vars.get(var_name) or var_info["value"]
                if var_name == "PULSE_VERSION" and not _is_pulse_version_block(block_source):
                    block_source = var_info["value"]
                for block_line in block_source.split("\n"):
                    lines.append(block_line)
            else:
                default_value = var_info["value"]
                matches_default = var_value == default_value
                user_set_explicitly = var_name in explicit_user_vars
                if matches_default and not user_set_explicitly:
                    lines.append(f'# (default) {var_name}="{var_value}"')
                else:
                    lines.append(f'{var_name}="{var_value}"')

            lines.append("")

        # Empty line between sections
        lines.append("")

    # Add any user variables that aren't in the sample (legacy/unknown vars)
    user_only_vars = set(user_vars.keys()) - {var_info["name"] for section in sections for var_info in section["vars"]}
    if user_only_vars:
        lines.append("# " + "=" * 75)
        lines.append("# Legacy/Unknown Variables")
        lines.append("# " + "=" * 75)
        lines.append("")
        for var_name in sorted(user_only_vars):
            var_value = user_vars[var_name]
            comment = user_comments.get(var_name, "")
            if comment:
                for comment_line in comment.split("\n"):
                    if comment_line.strip():
                        lines.append(comment_line)
            lines.append(f'{var_name}="{var_value}"')
            lines.append("")

    return "\n".join(lines)


def repair_pulse_version_block(
    user_vars: dict[str, str],
    user_comments: dict[str, str],
    placeholder_vars: set[str],
    sample_vars: dict[str, str],
) -> None:
    """Ensure the multi-line PULSE_VERSION helper stays intact."""
    sample_block = sample_vars.get("PULSE_VERSION")
    if not sample_block:
        return

    user_block = user_vars.get("PULSE_VERSION")
    if not _is_pulse_version_block(user_block):
        user_vars["PULSE_VERSION"] = sample_block
        placeholder_vars.discard("PULSE_VERSION")

    for helper_name in ("_pulse_conf_dir", "_pulse_version_file"):
        user_vars.pop(helper_name, None)
        user_comments.pop(helper_name, None)
        placeholder_vars.discard(helper_name)


def apply_legacy_replacements(
    user_vars: dict[str, str],
    user_comments: dict[str, str],
    placeholder_vars: set[str],
    sample_vars: dict[str, str],
) -> list[dict[str, Any]]:
    """Remove legacy variables that have known replacements."""
    removed: list[dict[str, Any]] = []

    for legacy_var, replacement_var in LEGACY_REPLACEMENTS.items():
        if legacy_var not in user_vars:
            continue
        if replacement_var not in sample_vars:
            continue

        legacy_value = user_vars.pop(legacy_var)
        placeholder_vars.discard(legacy_var)
        user_comments.pop(legacy_var, None)

        migrated = False
        if replacement_var not in user_vars:
            user_vars[replacement_var] = legacy_value
            placeholder_vars.add(replacement_var)
            migrated = True

        removed.append(
            {
                "legacy": legacy_var,
                "replacement": replacement_var,
                "migrated": migrated,
            }
        )

    return removed


def secure_file(path: Path, reference: Path | None = None) -> None:
    """Clamp file permissions to 600 and optionally match ownership."""

    if not path.exists():
        return

    ref_stat = None
    if reference and reference.exists():
        try:
            ref_stat = os.stat(reference)
        except OSError:
            ref_stat = None

    if ref_stat:
        try:
            os.chown(path, ref_stat.st_uid, ref_stat.st_gid)
        except PermissionError:
            pass

    try:
        path.chmod(0o600)
    except PermissionError:
        try:
            os.chmod(path, 0o600)
        except PermissionError:
            pass


def main() -> int:
    """Main entry point."""
    repo_dir = Path("/opt/pulse-os")
    if not repo_dir.exists():
        # Try source checkout for development
        repo_dir = Path(__file__).resolve().parents[2]

    sample_path = repo_dir / "pulse.conf.sample"
    user_config_path = repo_dir / "pulse.conf"
    backup_path = repo_dir / "pulse.conf.backup"

    if not sample_path.exists():
        print(f"Error: {sample_path} not found", file=sys.stderr)
        return 1

    # Parse sample file
    sample_sections = extract_sections_from_sample(sample_path)
    sample_vars, _, _, _ = parse_config_file(sample_path)

    # Parse user config file
    user_vars, user_comments, user_placeholder_vars, user_explicit_vars = parse_config_file(user_config_path)
    repair_pulse_version_block(user_vars, user_comments, user_placeholder_vars, sample_vars)

    legacy_actions = apply_legacy_replacements(user_vars, user_comments, user_placeholder_vars, sample_vars)

    # Determine which variables are new
    # A variable is "NEW" if it's in the sample but not in user config
    new_vars: set[str] = set()

    # Check for variables in sample that user doesn't have
    for var_name in sample_vars:
        if var_name not in user_vars or var_name in user_placeholder_vars:
            new_vars.add(var_name)

    # Note: Variables that were previously marked as NEW but are now in user_vars
    # will automatically not be marked as NEW anymore (since they're in user_vars)

    # Create backup
    if user_config_path.exists():
        print(f"Creating backup: {backup_path}")
        shutil.copy2(user_config_path, backup_path)
        secure_file(backup_path, user_config_path)

    # Generate new config
    new_config = format_config_file(sample_sections, user_vars, user_comments, new_vars, user_explicit_vars)

    # Write new config
    print(f"Writing updated config: {user_config_path}")
    user_config_path.write_text(new_config, encoding="utf-8")
    secure_file(user_config_path)

    # Report changes
    if new_vars:
        print(f"\nAdded {len(new_vars)} new variable(s) marked as NEW:")
        for var_name in sorted(new_vars):
            print(f"  - {var_name}")
        print("\nReview 'NEW:' markers in pulse.conf and rerun setup.sh once verified.")
    else:
        print("\nNo new variables added. Config file synced and reformatted.")

    if legacy_actions:
        print(f"\nRemoved {len(legacy_actions)} superseded variable(s):")
        for action in legacy_actions:
            legacy_var = action["legacy"]
            replacement_var = action["replacement"]
            if action["migrated"]:
                print(f"  - {legacy_var} (migrated value to {replacement_var})")
            else:
                print(f"  - {legacy_var} (replacement already configured: {replacement_var})")

    return 0


if __name__ == "__main__":
    sys.exit(main())
