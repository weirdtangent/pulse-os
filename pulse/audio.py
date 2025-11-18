"""Audio control utilities for PulseOS."""

from __future__ import annotations

import subprocess


def find_audio_sink() -> str | None:
    """Find the audio sink to use for volume control.

    Works with any audio output: Bluetooth, USB, analog (ReSpeaker), etc.
    Prefers the default sink, falls back to any available sink.

    Returns:
        The sink name (e.g., "bluez_output.XX_XX_XX_XX_XX_XX.1") or None if not found.
    """
    try:
        # First, try to get the default sink (works for any audio type)
        result = subprocess.run(
            ["pactl", "get-default-sink"],
            capture_output=True,
            text=True,
            check=True,
        )
        default_sink = result.stdout.strip()
        if default_sink:
            return default_sink

        # Fallback: get the first available sink if no default is set
        result = subprocess.run(
            ["pactl", "list", "sinks", "short"],
            capture_output=True,
            text=True,
            check=True,
        )
        for line in result.stdout.split("\n"):
            if line.strip():
                # Format: "<index> <name> <description>"
                # Extract sink name (second field, index 1)
                parts = line.split()
                if len(parts) > 1:
                    sink_name = parts[1]
                    # Skip monitor sinks (they're for recording, not playback)
                    if not sink_name.endswith(".monitor"):
                        return sink_name
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
    return None


def get_current_volume(sink: str | None = None) -> int | None:
    """Get current volume percentage from audio sink.

    Args:
        sink: Optional sink name. If None, will find the default sink.

    Returns:
        Volume percentage (0-100) or None if unavailable.
    """
    if sink is None:
        sink = find_audio_sink()
    if not sink:
        return None

    try:
        # Use get-sink-volume for more reliable parsing
        result = subprocess.run(
            ["pactl", "get-sink-volume", sink],
            capture_output=True,
            text=True,
            check=True,
        )
        # Output format: "Volume: front-left: 32768 /  50% / -18.06 dB,   front-right: 32768 /  50% / -18.06 dB"
        import re

        match = re.search(r"(\d+)%", result.stdout)
        if match:
            return int(match.group(1))
    except (subprocess.CalledProcessError, FileNotFoundError):
        # Fallback: try list sinks if get-sink-volume fails
        try:
            result = subprocess.run(
                ["pactl", "list", "sinks"],
                capture_output=True,
                text=True,
                check=True,
            )
            lines = result.stdout.split("\n")
            in_sink = False
            for line in lines:
                if f"Name: {sink}" in line:
                    in_sink = True
                if in_sink and "Volume:" in line:
                    import re

                    match = re.search(r"(\d+)%", line)
                    if match:
                        return int(match.group(1))
                if in_sink and line.strip() == "" and "Volume:" in result.stdout[: result.stdout.find(line)]:
                    break
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass
    return None


def set_volume(percent: int, sink: str | None = None) -> bool:
    """Set audio volume using pactl.

    Args:
        percent: Volume percentage (0-100), will be clamped to valid range.
        sink: Optional sink name. If None, will find the default sink.

    Returns:
        True if successful, False otherwise.
    """
    if sink is None:
        sink = find_audio_sink()
    if not sink:
        return False

    # Clamp to valid range
    percent = max(0, min(100, percent))

    try:
        # Set volume using pactl
        subprocess.run(
            ["pactl", "set-sink-volume", sink, f"{percent}%"],
            check=True,
            capture_output=True,
        )
        # Unmute if volume > 0
        if percent > 0:
            subprocess.run(
                ["pactl", "set-sink-mute", sink, "0"],
                check=False,
                capture_output=True,
            )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False
