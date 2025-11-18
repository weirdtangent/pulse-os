#!/usr/bin/env python3
"""Sync and reformat pulse.conf with pulse.conf.sample.

Reads the user's pulse.conf, merges it with pulse.conf.sample,
preserves user values, adds new variables with defaults,
and reformats everything to match pulse.conf.sample's structure.
"""

from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path
from typing import Any


def parse_config_file(path: Path) -> tuple[dict[str, str], dict[str, str]]:
    """Parse a config file and extract variables and comments.

    Returns:
        Tuple of (variables dict, comments dict where key is variable name)
    """
    variables: dict[str, str] = {}
    comments: dict[str, str] = {}
    current_comment: list[str] = []
    in_bash_block = False
    bash_block_lines: list[str] = []
    bash_block_var = ""

    if not path.exists():
        return variables, comments

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
                    current_comment = []  # Comments before block belong to it
                else:
                    bash_block_lines.append(original_line)
                    if "fi" in stripped:
                        # End of bash block
                        variables[bash_block_var] = "\n".join(bash_block_lines)
                        if current_comment:
                            comments[bash_block_var] = "\n".join(current_comment)
                        in_bash_block = False
                        bash_block_lines = []
                        current_comment = []
                i += 1
                continue

            # Collect comment lines
            if stripped.startswith("#"):
                # Skip section headers (they start with # and have ===)
                if "===" not in stripped:
                    # Check for NEW marker with variable assignment
                    if "NEW:" in stripped and "=" in stripped:
                        # Extract variable name and value from "# NEW: VARNAME="value""
                        match = re.search(r"NEW:\s*(\w+)=\"([^\"]*)\"", stripped)
                        if match:
                            var_name = match.group(1)
                            var_value = match.group(2)
                            variables[var_name] = var_value
                            # Store comment (without NEW marker)
                            new_marker = f'{var_name}="{var_value}"'
                            clean_comment = stripped.replace("NEW:", "").replace(new_marker, "").strip()
                            if clean_comment and clean_comment != "#":
                                current_comment.append(clean_comment)
                            if current_comment:
                                comments[var_name] = "\n".join(current_comment)
                                current_comment = []
                        else:
                            # Just a comment with NEW: in it, not a variable
                            current_comment.append(stripped)
                    else:
                        current_comment.append(stripped)
                i += 1
                continue

            # Empty line resets comment collection (unless in bash block)
            if not stripped:
                if not in_bash_block:
                    current_comment = []
                i += 1
                continue

            # Parse variable assignment
            if "=" in stripped and not stripped.startswith("#") and not in_bash_block:
                parts = stripped.split("=", 1)
                if len(parts) == 2:
                    var_name = parts[0].strip()
                    var_value = parts[1].strip()

                    # Remove quotes if present
                    if var_value.startswith('"') and var_value.endswith('"'):
                        var_value = var_value[1:-1]
                    elif var_value.startswith("'") and var_value.endswith("'"):
                        var_value = var_value[1:-1]

                    variables[var_name] = var_value

                    # Store comment if we have one
                    if current_comment:
                        comments[var_name] = "\n".join(current_comment)
                        current_comment = []

            i += 1

    return variables, comments


def extract_sections_from_sample(sample_path: Path) -> list[dict[str, Any]]:
    """Extract section structure from pulse.conf.sample.

    Returns:
        List of section dicts with 'header', 'comment', and 'vars' keys
    """
    sections: list[dict[str, Any]] = []
    current_section: dict[str, Any] | None = None
    current_comment: list[str] = []
    in_bash_block = False
    bash_block_lines: list[str] = []
    bash_block_var = ""
    bash_block_comment: list[str] = []

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
                        i += 3
                        continue

            # Check if we're entering a bash code block (PULSE_VERSION)
            if "if [[ -z" in stripped or in_bash_block:
                if not in_bash_block:
                    bash_block_var = "PULSE_VERSION"  # Known case
                    bash_block_lines = [original_line]
                    bash_block_comment = current_comment.copy()
                    in_bash_block = True
                    current_comment = []
                else:
                    bash_block_lines.append(original_line)
                    if "fi" in stripped:
                        # End of bash block
                        if current_section:
                            current_section["vars"].append(
                                {
                                    "name": bash_block_var,
                                    "value": "\n".join(bash_block_lines),
                                    "comment": "\n".join(bash_block_comment) if bash_block_comment else "",
                                }
                            )
                        in_bash_block = False
                        bash_block_lines = []
                        bash_block_comment = []
                i += 1
                continue

            # Collect comments
            if stripped.startswith("#"):
                current_comment.append(stripped)
                i += 1
                continue

            # Empty line
            if not stripped:
                if current_comment and not in_bash_block:
                    if current_section:
                        current_section["comment"] += "\n" + "\n".join(current_comment)
                    current_comment = []
                i += 1
                continue

            # Parse variable
            if "=" in stripped and not stripped.startswith("#") and not in_bash_block:
                parts = stripped.split("=", 1)
                if len(parts) == 2:
                    var_name = parts[0].strip()
                    var_value = parts[1].strip()

                    # Remove quotes
                    if var_value.startswith('"') and var_value.endswith('"'):
                        var_value = var_value[1:-1]
                    elif var_value.startswith("'") and var_value.endswith("'"):
                        var_value = var_value[1:-1]

                    if current_section:
                        current_section["vars"].append(
                            {
                                "name": var_name,
                                "value": var_value,
                                "comment": "\n".join(current_comment) if current_comment else "",
                            }
                        )
                        current_comment = []

            i += 1

        # Add last section
        if current_section is not None:
            sections.append(current_section)

    return sections


def format_config_file(
    sections: list[dict[str, Any]],
    user_vars: dict[str, str],
    user_comments: dict[str, str],
    new_vars: set[str],
) -> str:
    """Format the config file with sections, preserving user values and marking new vars."""
    lines: list[str] = []

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

            # Add comment
            if comment:
                for comment_line in comment.split("\n"):
                    if comment_line.strip():
                        lines.append(comment_line)

            # Add variable with NEW marker if applicable
            if is_new:
                lines.append(f'# NEW: {var_name}="{var_value}"')
            else:
                # Handle special case for PULSE_VERSION (bash code block)
                if var_name == "PULSE_VERSION" and ("if [[ -z" in var_value or "\n" in var_value):
                    # This is the bash code block, preserve it as-is (multi-line)
                    for block_line in var_value.split("\n"):
                        lines.append(block_line)
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


def main() -> int:
    """Main entry point."""
    repo_dir = Path("/opt/pulse-os")
    if not repo_dir.exists():
        # Try current directory for development
        repo_dir = Path(__file__).parent.parent

    sample_path = repo_dir / "pulse.conf.sample"
    user_config_path = repo_dir / "pulse.conf"
    backup_path = repo_dir / "pulse.conf.backup"

    if not sample_path.exists():
        print(f"Error: {sample_path} not found", file=sys.stderr)
        return 1

    # Parse sample file
    sample_sections = extract_sections_from_sample(sample_path)
    sample_vars, _ = parse_config_file(sample_path)

    # Parse user config file
    user_vars, user_comments = parse_config_file(user_config_path)

    # Determine which variables are new
    # A variable is "NEW" if it's in the sample but not in user config
    new_vars: set[str] = set()

    # Check for variables in sample that user doesn't have
    for var_name in sample_vars:
        if var_name not in user_vars:
            new_vars.add(var_name)

    # Note: Variables that were previously marked as NEW but are now in user_vars
    # will automatically not be marked as NEW anymore (since they're in user_vars)

    # Create backup
    if user_config_path.exists():
        print(f"Creating backup: {backup_path}")
        shutil.copy2(user_config_path, backup_path)

    # Generate new config
    new_config = format_config_file(sample_sections, user_vars, user_comments, new_vars)

    # Write new config
    print(f"Writing updated config: {user_config_path}")
    user_config_path.write_text(new_config, encoding="utf-8")

    # Report changes
    if new_vars:
        print(f"\nAdded {len(new_vars)} new variable(s) marked as NEW:")
        for var_name in sorted(new_vars):
            print(f"  - {var_name}")
        print("\nReview and remove 'NEW:' markers once you've verified the values.")
    else:
        print("\nNo new variables added. Config file synced and reformatted.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
