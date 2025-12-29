"""Audio control utilities for PulseOS."""

from __future__ import annotations

import logging
import math
import os
import shutil
import subprocess  # nosec B404 - subprocess used for pactl interactions
import wave
from collections.abc import Callable
from pathlib import Path

_LOGGER = logging.getLogger("pulse.audio")
_NOTIFICATION_FILENAME = "notification.wav"
_ALARM_FILENAME = "alarm.wav"
_REMINDER_FILENAME = "reminder.wav"
_NOTIFICATION_SAMPLE_RATE = 48_000
_NOTIFICATION_FREQUENCY_HZ = 720
_NOTIFICATION_MAX_AMPLITUDE = 30_000
_NOTIFICATION_DURATION_SECONDS = 0.22
_NOTIFICATION_DECAY_RATE = 4.5
_NOTIFICATION_FADE_IN_SECONDS = 0.01
# Alarm sound: more urgent, higher frequency
_ALARM_FREQUENCY_HZ = 880
_ALARM_MAX_AMPLITUDE = 35_000
_ALARM_DURATION_SECONDS = 0.25
_ALARM_DECAY_RATE = 3.0
_ALARM_FADE_IN_SECONDS = 0.02
# Reminder sound: gentler chime-like sound
_REMINDER_FREQUENCY_HZ = 600
_REMINDER_MAX_AMPLITUDE = 25_000
_REMINDER_DURATION_SECONDS = 0.3
_REMINDER_DECAY_RATE = 5.0
_REMINDER_FADE_IN_SECONDS = 0.015


def _runtime_env() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    return env


def _run_pactl(args: list[str]) -> subprocess.CompletedProcess[str] | None:
    try:
        result = subprocess.run(  # nosec B603 B607 - hardcoded command array
            ["pactl", *args],
            capture_output=True,
            text=True,
            check=True,
            env=_runtime_env(),
        )
        return result
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        _LOGGER.debug("[audio] pactl %s failed: %s", " ".join(args), exc)
        return None


def _notification_sample_path() -> Path:
    runtime_dir = Path(_runtime_env()["XDG_RUNTIME_DIR"])
    return runtime_dir / _NOTIFICATION_FILENAME


def _alarm_sample_path() -> Path:
    runtime_dir = Path(_runtime_env()["XDG_RUNTIME_DIR"])
    return runtime_dir / _ALARM_FILENAME


def _reminder_sample_path() -> Path:
    runtime_dir = Path(_runtime_env()["XDG_RUNTIME_DIR"])
    return runtime_dir / _REMINDER_FILENAME


def _bundled_notification_sample() -> Path | None:
    candidate = Path(__file__).resolve().parent.parent / "assets" / "sounds" / _NOTIFICATION_FILENAME
    if candidate.exists():
        return candidate
    return None


def _bundled_alarm_sample() -> Path | None:
    candidate = Path(__file__).resolve().parent.parent / "assets" / "sounds" / _ALARM_FILENAME
    if candidate.exists():
        return candidate
    return None


def _bundled_reminder_sample() -> Path | None:
    candidate = Path(__file__).resolve().parent.parent / "assets" / "sounds" / _REMINDER_FILENAME
    if candidate.exists():
        return candidate
    return None


def render_notification_sample(destination: Path) -> Path | None:
    """Render the default notification tone to the provided path."""
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(destination), "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(_NOTIFICATION_SAMPLE_RATE)
            _write_notification_beep(wav_file)
        return destination
    except OSError as exc:
        _LOGGER.debug("[audio] Unable to create thump sample at %s: %s", destination, exc)
        return None


def _write_notification_beep(wav_file: wave.Wave_write) -> None:
    samples = max(1, int(_NOTIFICATION_SAMPLE_RATE * _NOTIFICATION_DURATION_SECONDS))
    fade_in_samples = max(1, int(_NOTIFICATION_SAMPLE_RATE * _NOTIFICATION_FADE_IN_SECONDS))
    for i in range(samples):
        t = i / _NOTIFICATION_SAMPLE_RATE
        decay = math.exp(-_NOTIFICATION_DECAY_RATE * t / _NOTIFICATION_DURATION_SECONDS)
        fade_in = min(1.0, i / fade_in_samples)
        angle = 2 * math.pi * _NOTIFICATION_FREQUENCY_HZ * t
        value = int(fade_in * decay * _NOTIFICATION_MAX_AMPLITUDE * math.sin(angle))
        wav_file.writeframes(value.to_bytes(2, byteorder="little", signed=True))


def _write_alarm_beep(wav_file: wave.Wave_write) -> None:
    samples = max(1, int(_NOTIFICATION_SAMPLE_RATE * _ALARM_DURATION_SECONDS))
    fade_in_samples = max(1, int(_NOTIFICATION_SAMPLE_RATE * _ALARM_FADE_IN_SECONDS))
    for i in range(samples):
        t = i / _NOTIFICATION_SAMPLE_RATE
        decay = math.exp(-_ALARM_DECAY_RATE * t / _ALARM_DURATION_SECONDS)
        fade_in = min(1.0, i / fade_in_samples)
        angle = 2 * math.pi * _ALARM_FREQUENCY_HZ * t
        value = int(fade_in * decay * _ALARM_MAX_AMPLITUDE * math.sin(angle))
        wav_file.writeframes(value.to_bytes(2, byteorder="little", signed=True))


def _write_reminder_beep(wav_file: wave.Wave_write) -> None:
    samples = max(1, int(_NOTIFICATION_SAMPLE_RATE * _REMINDER_DURATION_SECONDS))
    fade_in_samples = max(1, int(_NOTIFICATION_SAMPLE_RATE * _REMINDER_FADE_IN_SECONDS))
    for i in range(samples):
        t = i / _NOTIFICATION_SAMPLE_RATE
        decay = math.exp(-_REMINDER_DECAY_RATE * t / _REMINDER_DURATION_SECONDS)
        fade_in = min(1.0, i / fade_in_samples)
        angle = 2 * math.pi * _REMINDER_FREQUENCY_HZ * t
        value = int(fade_in * decay * _REMINDER_MAX_AMPLITUDE * math.sin(angle))
        wav_file.writeframes(value.to_bytes(2, byteorder="little", signed=True))


def _ensure_notification_sample() -> Path | None:
    path = _notification_sample_path()
    if path.exists():
        return path
    bundled = _bundled_notification_sample()
    if bundled:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(bundled, path)
            return path
        except OSError as exc:
            _LOGGER.debug("[audio] Unable to copy bundled thump sample: %s", exc)
            return bundled
    return render_notification_sample(path)


def render_alarm_sample(destination: Path) -> Path | None:
    """Render the alarm tone to the provided path."""
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(destination), "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(_NOTIFICATION_SAMPLE_RATE)
            _write_alarm_beep(wav_file)
        return destination
    except OSError as exc:
        _LOGGER.debug("[audio] Unable to create alarm sample at %s: %s", destination, exc)
        return None


def _ensure_alarm_sample() -> Path | None:
    path = _alarm_sample_path()
    if path.exists():
        return path
    bundled = _bundled_alarm_sample()
    if bundled:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(bundled, path)
            return path
        except OSError as exc:
            _LOGGER.debug("[audio] Unable to copy bundled alarm sample: %s", exc)
            return bundled
    return render_alarm_sample(path)


def render_reminder_sample(destination: Path) -> Path | None:
    """Render the reminder tone to the provided path."""
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(destination), "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(_NOTIFICATION_SAMPLE_RATE)
            _write_reminder_beep(wav_file)
        return destination
    except OSError as exc:
        _LOGGER.debug("[audio] Unable to create reminder sample at %s: %s", destination, exc)
        return None


def _ensure_reminder_sample() -> Path | None:
    path = _reminder_sample_path()
    if path.exists():
        return path
    bundled = _bundled_reminder_sample()
    if bundled:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(bundled, path)
            return path
        except OSError as exc:
            _LOGGER.debug("[audio] Unable to copy bundled reminder sample: %s", exc)
            return bundled
    return render_reminder_sample(path)


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
            _LOGGER.debug("[audio] Detected default sink: %s", default_sink)
            return default_sink

    result = _run_pactl(["list", "sinks", "short"])
    if result:
        for line in result.stdout.split("\n"):
            if line.strip():
                parts = line.split()
                if len(parts) > 1:
                    sink_name = parts[1]
                    if not sink_name.endswith(".monitor"):
                        _LOGGER.debug("[audio] Using fallback sink: %s", sink_name)
                        return sink_name
    _LOGGER.warning("[audio] No audio sinks detected (XDG_RUNTIME_DIR=%s)", _runtime_env().get("XDG_RUNTIME_DIR"))
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
            result = subprocess.run(  # nosec B603 B607 - hardcoded command array
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


def set_volume(percent: int, sink: str | None = None, *, play_feedback: bool = False, allow_zero: bool = False) -> bool:
    """Set audio volume using pactl.

    Args:
        percent: Volume percentage (0-100), will be clamped to valid range.
        sink: Optional sink name. If None, will find the default sink.
        play_feedback: When True, play the thump sample after a successful change.
        allow_zero: When True, allows setting volume to 0%. Default False to prevent accidental muting.

    Returns:
        True if successful, False otherwise.
    """
    if sink is None:
        sink = find_audio_sink()
    if not sink:
        return False

    # Clamp to valid range, but prevent setting to 0% unless explicitly allowed
    percent = max(0, min(100, percent))
    if not allow_zero and percent == 0:
        _LOGGER.warning("[audio] Prevented setting volume to 0%% (use allow_zero=True to override)")
        percent = 20  # Use minimum safe volume instead

    try:
        result = _run_pactl(["set-sink-volume", sink, f"{percent}%"])
        if not result:
            return False
        # Unmute if volume > 0
        if percent > 0:
            _run_pactl(["set-sink-mute", sink, "0"])
        if play_feedback:
            play_volume_feedback()
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def _play_sample(sample_path: Path | None) -> None:
    """Play a sound sample using available audio player."""
    if not sample_path:
        return
    if not sample_path.exists():
        return
    player = None
    for candidate in ("pw-play", "aplay"):
        if shutil.which(candidate):
            player = candidate
            break
    if not player:
        _LOGGER.debug("[audio] No audio player available")
        return
    try:
        subprocess.run(  # nosec B603 - hardcoded command array
            [player, str(sample_path)],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=_runtime_env(),
        )
    except OSError as exc:
        _LOGGER.debug("[audio] Failed to play sample: %s", exc)


def play_sound(sound_path: Path | None, fallback: Callable[[], None] | None = None) -> None:
    """Play a provided sound path or fall back to a callable."""
    if sound_path and sound_path.exists():
        _play_sample(sound_path)
        return
    if fallback:
        fallback()


def play_volume_feedback() -> None:
    """Play a short confirmation thump after adjusting volume."""
    override_id = os.getenv("PULSE_SOUND_NOTIFICATION")
    if override_id:
        try:
            from pulse.sound_library import SoundLibrary, SoundSettings

            library = SoundLibrary()
            settings = SoundSettings.with_defaults(default_notification=override_id)
            override_path = library.resolve_with_default(override_id, kind="notification", settings=settings)
            if override_path:
                _play_sample(override_path)
                return
        except Exception:
            _LOGGER.debug("[audio] Failed to play override notification sound '%s'", override_id, exc_info=True)
    sample = _ensure_notification_sample()
    _play_sample(sample)


def play_alarm_sound() -> None:
    """Play the alarm sound (used for repeating alarm beeps)."""
    sample = _ensure_alarm_sample()
    _play_sample(sample)


def play_reminder_sound() -> None:
    """Play the reminder sound (used for reminders and calendar events)."""
    sample = _ensure_reminder_sample()
    _play_sample(sample)
