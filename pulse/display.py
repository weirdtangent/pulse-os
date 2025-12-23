"""Display control utilities for PulseOS."""

from __future__ import annotations

import os
import subprocess  # nosec B404 - brightness control relies on CLI calls
from pathlib import Path

CONF_PATH = Path("/etc/pulse-backlight.conf")


def find_backlight_device() -> str | None:
    """Find the backlight device path.

    First tries reading from /etc/pulse-backlight.conf, then falls back
    to auto-detecting any backlight device in /sys/class/backlight.

    Returns:
        The backlight device path (e.g., "/sys/class/backlight/11-0045") or None if not found.
    """
    # Explicit override via env
    env_path = os.environ.get("PULSE_BACKLIGHT_DEVICE")
    if env_path and Path(env_path).exists():
        return env_path

    # Try reading from config file first
    try:
        with CONF_PATH.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line.startswith("BACKLIGHT="):
                    device_path = line.split("=", 1)[1].strip()
                    if Path(device_path).exists():
                        return device_path
    except (OSError, IndexError):
        pass

    # Fallback: find any backlight device
    backlight_dir = Path("/sys/class/backlight")
    if backlight_dir.exists():
        for device in backlight_dir.iterdir():
            if (device / "brightness").exists() and (device / "max_brightness").exists():
                return str(device)
    return None


def get_current_brightness(device_path: str | None = None) -> int | None:
    """Get current screen brightness percentage.

    Args:
        device_path: Optional backlight device path. If None, will find it automatically.

    Returns:
        Brightness percentage (0-100) or None if unavailable.
    """
    if device_path is None:
        device_path = find_backlight_device()
    if not device_path:
        return None

    try:
        # Try brightnessctl first
        result = subprocess.run(  # nosec B603 B607 - hardcoded command array
            ["brightnessctl", "get"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            # brightnessctl get returns raw value, need max to calculate %
            max_result = subprocess.run(  # nosec B603 B607 - hardcoded command array
                ["brightnessctl", "max"],
                capture_output=True,
                text=True,
                check=False,
            )
            if max_result.returncode == 0:
                current = int(result.stdout.strip())
                max_val = int(max_result.stdout.strip())
                if max_val > 0:
                    return int((current * 100) / max_val)
    except (subprocess.CalledProcessError, FileNotFoundError, ValueError):
        pass

    # Fallback: read from sysfs
    try:
        device = Path(device_path)
        max_path = device / "max_brightness"
        brightness_path = device / "brightness"
        if max_path.exists() and brightness_path.exists():
            max_brightness = int(max_path.read_text(encoding="utf-8").strip())
            current_brightness = int(brightness_path.read_text(encoding="utf-8").strip())
            if max_brightness > 0:
                return int((current_brightness * 100) / max_brightness)
    except (OSError, ValueError):
        pass
    return None


def set_brightness(percent: int, device_path: str | None = None) -> bool:
    """Set screen brightness.

    Args:
        percent: Brightness percentage (0-100), will be clamped to valid range.
        device_path: Optional backlight device path. If None, will find it automatically.

    Returns:
        True if successful, False otherwise.
    """
    if device_path is None:
        device_path = find_backlight_device()
    if not device_path:
        return False

    # Clamp to valid range
    percent = max(0, min(100, percent))

    try:
        # Try brightnessctl first (easier and more portable)
        # nosec B603: command is hardcoded and percent is clamped to 0-100
        result = subprocess.run(
            ["brightnessctl", "set", f"{percent}%"],
            check=False,
            capture_output=True,
        )
        if result.returncode == 0:
            return True

        # Fallback: write directly to sysfs
        device = Path(device_path)
        max_path = device / "max_brightness"
        brightness_path = device / "brightness"
        if max_path.exists() and brightness_path.exists():
            max_brightness = int(max_path.read_text(encoding="utf-8").strip())
            scaled = max(0, min(max_brightness, int(max_brightness * percent / 100)))
            brightness_path.write_text(f"{scaled}\n", encoding="utf-8")
            return True
    except (subprocess.CalledProcessError, OSError):
        pass
    return False
