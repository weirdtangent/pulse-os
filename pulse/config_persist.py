"""Persist configuration changes to pulse.conf.

This module provides a debounced mechanism to update individual variables
in pulse.conf while preserving the file's format, comments, and structure.
Changes are batched and written after a short delay to avoid excessive disk I/O.
"""

from __future__ import annotations

import fcntl
import logging
import re
import shutil
import threading
from collections.abc import Callable
from pathlib import Path

LOGGER = logging.getLogger(__name__)

# Default config file location
DEFAULT_CONFIG_PATH = Path("/opt/pulse-os/pulse.conf")

# Debounce delay in seconds - wait this long after last change before writing
DEBOUNCE_DELAY_SECONDS = 2.0

# Lock file for cross-process synchronization
LOCK_FILE_SUFFIX = ".lock"

# Regex to match a variable assignment line: VAR_NAME="value" or VAR_NAME=value
_ASSIGNMENT_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")

# Regex to match a commented-out default: # (default) VAR_NAME="value"
_DEFAULT_COMMENT_RE = re.compile(r"^#\s*\(default\)\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")


def _strip_quotes(value: str) -> str:
    """Remove matching single or double quotes from a value."""
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def _quote_value(value: str) -> str:
    """Wrap a value in double quotes, escaping as needed."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


class ConfigPersister:
    """Manages debounced writes to pulse.conf."""

    def __init__(
        self,
        config_path: Path | None = None,
        debounce_seconds: float = DEBOUNCE_DELAY_SECONDS,
        logger: logging.Logger | None = None,
    ) -> None:
        self._config_path = config_path or DEFAULT_CONFIG_PATH
        self._debounce_seconds = debounce_seconds
        self._logger = logger or LOGGER
        self._pending_changes: dict[str, str] = {}
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None
        self._write_lock = threading.Lock()

    def update(self, var_name: str, value: str) -> None:
        """Queue a variable update. The write will be debounced."""
        with self._lock:
            self._pending_changes[var_name] = value
            self._schedule_write()

    def _schedule_write(self) -> None:
        """Schedule a debounced write. Must be called with self._lock held."""
        if self._timer is not None:
            self._timer.cancel()
        self._timer = threading.Timer(self._debounce_seconds, self._flush)
        self._timer.daemon = True
        self._timer.start()

    def _flush(self) -> None:
        """Flush pending changes to the config file."""
        with self._lock:
            if not self._pending_changes:
                return
            changes = self._pending_changes.copy()
            self._pending_changes.clear()
            self._timer = None

        # Perform the actual write outside the lock
        try:
            self._write_changes(changes)
        except Exception as exc:
            self._logger.error("Failed to persist config changes: %s", exc)

    def _write_changes(self, changes: dict[str, str]) -> None:
        """Write multiple variable changes to the config file."""
        if not self._config_path.exists():
            self._logger.warning(
                "Config file '%s' does not exist, skipping persistence",
                self._config_path,
            )
            return

        with self._write_lock:
            self._write_changes_locked(changes)

    def _write_changes_locked(self, changes: dict[str, str]) -> None:
        """Write changes with file locking. Must be called with _write_lock held."""
        lock_path = Path(str(self._config_path) + LOCK_FILE_SUFFIX)

        try:
            # Create lock file if it doesn't exist
            lock_path.touch(exist_ok=True)
        except OSError:
            pass

        lock_fd = None
        try:
            # Acquire file lock for cross-process safety
            try:
                lock_fd = open(lock_path, "w")
                fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX)
            except OSError as exc:
                self._logger.warning("Could not acquire config lock: %s", exc)
                # Continue anyway - worst case we have a race condition

            # Read current config
            try:
                content = self._config_path.read_text(encoding="utf-8")
            except OSError as exc:
                self._logger.error("Failed to read config file: %s", exc)
                return

            # Create backup
            backup_path = Path(str(self._config_path) + ".backup")
            try:
                shutil.copy2(self._config_path, backup_path)
                # Preserve permissions
                try:
                    backup_path.chmod(0o600)
                except OSError:
                    pass
            except OSError as exc:
                self._logger.warning("Failed to create config backup: %s", exc)

            # Apply changes
            new_content = self._apply_changes(content, changes)

            # Write updated config
            try:
                self._config_path.write_text(new_content, encoding="utf-8")
                self._logger.info(
                    "Persisted %d config change(s) to '%s': %s",
                    len(changes),
                    self._config_path,
                    ", ".join(f"{k}={v!r}" for k, v in changes.items()),
                )
            except OSError as exc:
                self._logger.error("Failed to write config file: %s", exc)

        finally:
            if lock_fd is not None:
                try:
                    fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
                    lock_fd.close()
                except OSError:
                    pass

    def _apply_changes(self, content: str, changes: dict[str, str]) -> str:
        """Apply variable changes to config content."""
        lines = content.splitlines(keepends=True)
        remaining = dict(changes)
        result: list[str] = []

        for line in lines:
            stripped = line.rstrip("\n\r")

            # Check for commented-out default line: # (default) VAR="value"
            default_match = _DEFAULT_COMMENT_RE.match(stripped)
            if default_match:
                var_name = default_match.group(1)
                if var_name in remaining:
                    new_value = remaining.pop(var_name)
                    # Uncomment and set the new value
                    quoted = _quote_value(new_value)
                    new_line = f"{var_name}={quoted}"
                    # Preserve line ending
                    if line.endswith("\n"):
                        new_line += "\n"
                    result.append(new_line)
                    continue

            # Check for active assignment line: VAR="value"
            assign_match = _ASSIGNMENT_RE.match(stripped)
            if assign_match:
                var_name = assign_match.group(1)
                if var_name in remaining:
                    new_value = remaining.pop(var_name)
                    quoted = _quote_value(new_value)
                    new_line = f"{var_name}={quoted}"
                    # Preserve line ending
                    if line.endswith("\n"):
                        new_line += "\n"
                    result.append(new_line)
                    continue

            # Keep line unchanged
            result.append(line)

        # If any variables weren't found, log a warning
        # (they might be new or in a bash block - don't add them automatically)
        for var_name in remaining:
            self._logger.warning(
                "Variable '%s' not found in config file, change not persisted",
                var_name,
            )

        return "".join(result)

    def flush_sync(self) -> None:
        """Immediately flush any pending changes (blocking)."""
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            if not self._pending_changes:
                return
            changes = self._pending_changes.copy()
            self._pending_changes.clear()

        try:
            self._write_changes(changes)
        except Exception as exc:
            self._logger.error("Failed to persist config changes: %s", exc)

    def stop(self) -> None:
        """Stop the persister and flush any pending changes."""
        self.flush_sync()


# Global singleton instance
_persister: ConfigPersister | None = None
_persister_lock = threading.Lock()


def get_persister(
    config_path: Path | None = None,
    logger: logging.Logger | None = None,
) -> ConfigPersister:
    """Get or create the global ConfigPersister instance."""
    global _persister
    with _persister_lock:
        if _persister is None:
            _persister = ConfigPersister(config_path=config_path, logger=logger)
        return _persister


def update_config(var_name: str, value: str, *, logger: logging.Logger | None = None) -> None:
    """Update a single variable in pulse.conf (debounced).

    This is the primary API for persisting config changes. Changes are batched
    and written after a short delay to avoid excessive disk I/O.

    Args:
        var_name: The configuration variable name (e.g., "PULSE_SOUND_ALARM")
        value: The new value to set
        logger: Optional logger for status messages
    """
    persister = get_persister(logger=logger)
    persister.update(var_name, value)


# Mapping from MQTT preference keys to (config_var_name, value_transformer).
#
# MQTT preference keys are short, API-friendly names used in topics like:
#   pulse/<hostname>/assistant/preferences/<key>/set
#   pulse/<hostname>/assistant/preferences/<key>/state
#
# Config variable names are the full uppercase names used in pulse.conf.
# The mapping allows some naming divergence for readability on both sides:
#
#   MQTT Key          -> Config Variable               -> Notes
#   ─────────────────────────────────────────────────────────────────────
#   wake_sound        -> PULSE_ASSISTANT_WAKE_SOUND    -> on/off -> true/false
#   speaking_style    -> PULSE_ASSISTANT_SPEAKING_STYLE
#   wake_sensitivity  -> PULSE_ASSISTANT_WAKE_SENSITIVITY
#   ha_pipeline       -> HOME_ASSISTANT_ASSIST_PIPELINE  (ha_ is shorthand for HOME_ASSISTANT_)
#   llm_provider      -> PULSE_ASSISTANT_PROVIDER        (llm_ prefix clarifies context)
#   log_llm           -> PULSE_ASSISTANT_LOG_LLM       -> on/off -> true/false
#   overlay_font      -> PULSE_OVERLAY_FONT_FAMILY       (font -> FONT_FAMILY for CSS context)
#   sound_alarm       -> PULSE_SOUND_ALARM
#   sound_timer       -> PULSE_SOUND_TIMER
#   sound_reminder    -> PULSE_SOUND_REMINDER
#   sound_notification-> PULSE_SOUND_NOTIFICATION
#   day_brightness    -> PULSE_DAY_BRIGHTNESS
#   night_brightness  -> PULSE_NIGHT_BRIGHTNESS
#
PREFERENCE_TO_CONFIG: dict[str, tuple[str, Callable[[str], str]]] = {
    # Assistant preferences
    "wake_sound": ("PULSE_ASSISTANT_WAKE_SOUND", lambda v: "true" if v == "on" else "false"),
    "speaking_style": ("PULSE_ASSISTANT_SPEAKING_STYLE", str),
    "wake_sensitivity": ("PULSE_ASSISTANT_WAKE_SENSITIVITY", str),
    "ha_response_mode": ("PULSE_ASSISTANT_HA_RESPONSE_MODE", str),
    "ha_tone_sound": ("PULSE_ASSISTANT_HA_TONE_SOUND", str),
    # Home Assistant integration (ha_ is shorthand for HOME_ASSISTANT_)
    "ha_pipeline": ("HOME_ASSISTANT_ASSIST_PIPELINE", str),
    # LLM settings (llm_ prefix clarifies the MQTT key refers to LLM provider choice)
    "llm_provider": ("PULSE_ASSISTANT_PROVIDER", str),
    "log_llm": ("PULSE_ASSISTANT_LOG_LLM", lambda v: "true" if v == "on" else "false"),
    # Overlay settings (font -> FONT_FAMILY matches CSS terminology)
    "overlay_font": ("PULSE_OVERLAY_FONT_FAMILY", str),
    # Sound preferences (sound_<kind> -> PULSE_SOUND_<KIND>)
    "sound_alarm": ("PULSE_SOUND_ALARM", str),
    "sound_timer": ("PULSE_SOUND_TIMER", str),
    "sound_reminder": ("PULSE_SOUND_REMINDER", str),
    "sound_notification": ("PULSE_SOUND_NOTIFICATION", str),
    # Display brightness targets (day/night)
    "day_brightness": ("PULSE_DAY_BRIGHTNESS", str),
    "night_brightness": ("PULSE_NIGHT_BRIGHTNESS", str),
}


def persist_preference(
    preference_key: str,
    value: str,
    *,
    logger: logging.Logger | None = None,
) -> bool:
    """Persist a preference change using its logical key.

    Args:
        preference_key: The preference key (e.g., "wake_sound", "sound_alarm")
        value: The MQTT/HA value to persist
        logger: Optional logger for status messages

    Returns:
        True if the preference was recognized and queued for persistence,
        False if the preference key is unknown.
    """
    mapping = PREFERENCE_TO_CONFIG.get(preference_key)
    if mapping is None:
        if logger:
            logger.warning("Unknown preference key '%s', not persisting", preference_key)
        return False

    var_name, transformer = mapping
    config_value = transformer(value)
    update_config(var_name, config_value, logger=logger)
    return True
