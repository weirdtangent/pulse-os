"""
PulseOS - Voice assistant system package

This is the root package for Pulse OS, containing shared utilities and modules
for building a local-first voice assistant with Home Assistant integration.

Core modules:
- audio: Audio control and playback (volume, sound generation)
- display: Screen brightness management
- overlay: Browser-based UI overlay system
- assistant: Voice assistant implementation with LLM and Home Assistant integration
- sound_library: Sound catalog and file resolution
- location_resolver: Geographic location parsing and resolution
"""

import os as _os
import subprocess as _subprocess
from pathlib import Path as _Path


def _get_version() -> str:
    """Derive version from PULSE_VERSION env var or git tag, with fallback."""
    env_version = _os.environ.get("PULSE_VERSION")
    if env_version and env_version != "0.0.0":
        return env_version
    try:
        repo_dir = _Path(__file__).resolve().parent.parent
        result = _subprocess.run(
            ["git", "describe", "--tags", "--abbrev=0"],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip().lstrip("v")
    except Exception:  # git not installed, not a repo, timeout, etc.
        pass
    return "0.0.0"


__version__ = _get_version()
