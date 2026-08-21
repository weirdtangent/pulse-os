"""Guards for the Python packages setup.sh installs on a device.

The fleet's Python deps do not come from uv.lock automatically: setup.sh pip-installs a
named list, and uv.lock only governs CI and dev. These tests keep the two from drifting,
which is how icalendar sat at 7.0.3 on every kiosk while the lockfile said 7.2.2.
"""

from __future__ import annotations

import re
import shutil
import subprocess  # nosec B404 - invoking uv to regenerate a lockfile export
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CONSTRAINTS = ROOT / "config" / "device-constraints.txt"
SETUP = ROOT / "setup.sh"

EXPORT_COMMAND = ["uv", "export", "--frozen", "--no-dev", "--no-hashes", "--no-emit-project"]


def _packages_in(array_name: str) -> list[str]:
    """Read a bash array literal out of setup.sh, e.g. CORE_PIP_PACKAGES=(a b c)."""
    match = re.search(rf"^{array_name}=\(([^)]*)\)", SETUP.read_text(), re.MULTILINE)
    assert match, f"{array_name} not found in setup.sh"
    return match.group(1).split()


def _declared_dependencies() -> dict[str, str]:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        deps = tomllib.load(handle)["project"]["dependencies"]
    parsed = {}
    for dep in deps:
        name = re.split(r"[\[><=!~;\s]", dep, maxsplit=1)[0].strip()
        parsed[name.lower().replace("_", "-")] = dep
    return parsed


def _pinned_names() -> set[str]:
    names = set()
    for line in CONSTRAINTS.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        names.add(re.split(r"[\[><=!~;\s]", stripped, maxsplit=1)[0].strip().lower().replace("_", "-"))
    return names


def test_constraints_file_matches_the_lockfile():
    """Regenerating must be a no-op, or the fleet installs versions CI never tested."""
    if shutil.which("uv") is None:
        pytest.skip("uv not installed")
    result = subprocess.run(EXPORT_COMMAND, cwd=ROOT, capture_output=True, text=True, check=True)  # nosec B603
    exported = [line for line in result.stdout.splitlines() if line.strip() and not line.startswith("#")]
    committed = [line for line in CONSTRAINTS.read_text().splitlines() if line.strip() and not line.startswith("#")]
    assert committed == exported, (
        "config/device-constraints.txt is stale; regenerate with:\n  " + " ".join(EXPORT_COMMAND) + " > "
        "config/device-constraints.txt"
    )


@pytest.mark.parametrize("array_name", ["CORE_PIP_PACKAGES", "ASSISTANT_PIP_PACKAGES"])
def test_installed_packages_are_declared_dependencies(array_name):
    """A package installed on devices but absent from pyproject has no pin at all."""
    declared = _declared_dependencies()
    undeclared = sorted(p for p in _packages_in(array_name) if p.lower().replace("_", "-") not in declared)
    assert undeclared == [], f"{array_name} installs undeclared packages: {undeclared}"


@pytest.mark.parametrize("array_name", ["CORE_PIP_PACKAGES", "ASSISTANT_PIP_PACKAGES"])
def test_installed_packages_are_pinned_by_the_constraints(array_name):
    pinned = _pinned_names()
    unpinned = sorted(p for p in _packages_in(array_name) if p.lower().replace("_", "-") not in pinned)
    assert unpinned == [], f"{array_name} installs packages with no constraint: {unpinned}"


def test_transitive_dependencies_are_pinned_too():
    """The actual bug: named packages upgraded, transitive ones froze.

    icalendar reaches a device only through recurring-ical-events, so it is the canary —
    if the closure stops being pinned, it goes first.
    """
    assert "icalendar" in _pinned_names()


def test_listener_only_packages_are_not_behind_the_assistant_guard():
    """bin/kiosk-mqtt-listener.py imports these at module scope.

    Left behind the PULSE_VOICE_ASSISTANT guard, a display with the assistant disabled
    could not start the listener at all.
    """
    core = {p.lower() for p in _packages_in("CORE_PIP_PACKAGES")}
    assert {"httpx", "openlocationcode", "websockets"} <= core
