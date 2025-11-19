"""Audio control utilities for PulseOS."""

from __future__ import annotations

import logging
import math
import os
import shutil
import subprocess
import wave
from pathlib import Path

_LOGGER = logging.getLogger("pulse.audio")
_THUMP_FILENAME = "pulse-volume-thump.wav"
_THUMP_DURATION_SECONDS = 0.4
_THUMP_FREQUENCY_HZ = 140


def _runtime_env() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    return env


def _run_pactl(args: list[str]) -> subprocess.CompletedProcess[str] | None:
    try:
        result = subprocess.run(
            ["pactl", *args],
            capture_output=True,
            text=True,
            check=True,
            env=_runtime_env(),
        )
        return result
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        _LOGGER.debug("pactl %s failed: %s", " ".join(args), exc)
        return None


def _thump_sample_path() -> Path:
    runtime_dir = Path(_runtime_env()["XDG_RUNTIME_DIR"])
    return runtime_dir / _THUMP_FILENAME


def _ensure_thump_sample() -> Path | None:
    path = _thump_sample_path()
    if path.exists():
        return path
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        sample_rate = 48000
        samples = int(sample_rate * _THUMP_DURATION_SECONDS)
        decay_rate = 3.0
        with wave.open(path, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)
            for i in range(samples):
                env = math.exp(-decay_rate * i / samples)
                value = int(env * 28000 * math.sin(2 * math.pi * _THUMP_FREQUENCY_HZ * i / sample_rate))
                wav_file.writeframes(value.to_bytes(2, byteorder="little", signed=True))
        return path
    except OSError as exc:
        _LOGGER.debug("Unable to create thump sample: %s", exc)
        return None


def find_audio_sink() -> str | None:
    """Find the audio sink to use for volume control.

    Works with any audio output: Bluetooth, USB, analog (ReSpeaker), etc.
    Prefers the default sink, falls back to any available sink.

    Returns:
        The sink name (e.g., "bluez_output.XX_XX_XX_XX_XX_XX.1") or None if not found.
    """
    result = _run_pactl(["get-default-sink"])
    if result:
        default_sink = result.stdout.strip()
        if default_sink:
            _LOGGER.debug("Detected default sink: %s", default_sink)
            return default_sink

    result = _run_pactl(["list", "sinks", "short"])
    if result:
        for line in result.stdout.split("\n"):
            if line.strip():
                parts = line.split()
                if len(parts) > 1:
                    sink_name = parts[1]
                    if not sink_name.endswith(".monitor"):
                        _LOGGER.debug("Using fallback sink: %s", sink_name)
                        return sink_name
    _LOGGER.warning("find_audio_sink: no sinks detected (XDG_RUNTIME_DIR=%s)", _runtime_env().get("XDG_RUNTIME_DIR"))
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
        result = _run_pactl(["get-sink-volume", sink])
        if not result:
            raise subprocess.CalledProcessError(1, "pactl get-sink-volume")
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
                env=_runtime_env(),
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
        result = _run_pactl(["set-sink-volume", sink, f"{percent}%"])
        if not result:
            return False
        # Unmute if volume > 0
        if percent > 0:
            _run_pactl(["set-sink-mute", sink, "0"])
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def play_volume_feedback() -> None:
    """Play a short confirmation thump after adjusting volume."""
    sample = _ensure_thump_sample()
    if not sample:
        return
    player = None
    for candidate in ("pw-play", "aplay"):
        if shutil.which(candidate):
            player = candidate
            break
    if not player:
        _LOGGER.debug("No audio player available for volume feedback")
        return
    try:
        subprocess.run(
            [player, str(sample)],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=_runtime_env(),
        )
    except OSError as exc:
        _LOGGER.debug("Failed to play volume feedback: %s", exc)
