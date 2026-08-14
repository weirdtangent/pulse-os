"""Detect whether the room's configured speaker is actually reachable.

A speaker that has been powered off (Bluetooth) or unplugged (USB/analog) fails
*silently* on Pulse. Playback still "succeeds", snapclient stays connected to the
server, the overlay looks perfectly healthy — the room just stops making sound. The
only trace is bluetoothd retrying in the journal, roughly every 15s forever:

    profiles/audio/avdtp.c:avdtp_connect_cb() connect to AA:BB:CC:DD:EE:FF: Host is down (112)

Nobody reads the journal, so this module turns that into something the notification
bar can show as a badge (see ``_build_speaker_pill`` in ``pulse.overlay``).

Only the *configured* speaker counts. BlueZ pairings are not a usable signal on their
own: a display that has been re-purposed keeps stale pairings to speakers that now
live in another room, and alerting on those would mean a badge that never clears. So
the source of truth is what ``pulse.conf`` says this display is supposed to drive --
``PULSE_BT_MAC``/``PULSE_BLUETOOTH_AUTOCONNECT`` for Bluetooth, ``PULSE_SPEAKER_SINK``
for a wired one. A display with neither configured is never checked.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess  # nosec B404 - fixed command arrays, no shell
from dataclasses import dataclass

_LOGGER = logging.getLogger(__name__)

# BlueZ prints "Device 11:22:33:44:55:66 Name" from `bluetoothctl devices`.
_MAC_RE = re.compile(r"^Device\s+((?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2})\s*(.*)$")
_MAC_ONLY_RE = re.compile(r"^(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")

_SUBPROCESS_TIMEOUT = 10


@dataclass(frozen=True)
class SpeakerConfig:
    """What this display is configured to play through."""

    bt_autoconnect: bool = True
    bt_mac: str = ""
    # Substring matched against `pactl list sinks short` names. Empty disables the
    # wired check entirely — there is no way to tell "unplugged" from "never had one"
    # without being told which sink to expect.
    wired_sink: str = ""


@dataclass(frozen=True)
class SpeakerStatus:
    """Result of one reachability check."""

    offline: bool
    name: str
    kind: str  # "bluetooth" | "wired"


def _runtime_env() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    return env


def _run(args: list[str]) -> str | None:
    """Run a command and return stdout, or None if it failed.

    Never raises: every caller is on a background poll loop where a missing binary or
    a wedged daemon must degrade to "unknown" rather than take the loop down.
    """
    try:
        result = subprocess.run(  # nosec B603 B607 - fixed command array, no shell
            args,
            capture_output=True,
            text=True,
            check=True,
            timeout=_SUBPROCESS_TIMEOUT,
            env=_runtime_env(),
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        _LOGGER.debug("[speaker] %s failed: %s", " ".join(args), exc)
        return None
    return result.stdout


def _bluetooth_devices(kind: str) -> list[tuple[str, str]]:
    """(mac, name) pairs from `bluetoothctl devices <Paired|Connected>`."""
    out = _run(["bluetoothctl", "devices", kind])
    if out is None:
        return []
    devices: list[tuple[str, str]] = []
    for line in out.splitlines():
        match = _MAC_RE.match(line.strip())
        if match:
            devices.append((match.group(1), match.group(2).strip()))
    return devices


def resolve_bt_mac(configured: str) -> tuple[str, str]:
    """The MAC to watch, plus its friendly name. ("", "") when there is nothing to watch.

    Mirrors the discovery order in ``bin/bt-autoconnect.sh`` so the badge and the
    reconnect loop can never disagree about *which* speaker this room owns: an
    explicit ``PULSE_BT_MAC`` wins, otherwise the connected device, otherwise the
    first paired one.
    """
    candidate = (configured or "").strip()
    if candidate:
        if not _MAC_ONLY_RE.match(candidate):
            _LOGGER.warning("[speaker] PULSE_BT_MAC is not a MAC address, ignoring: %r", candidate)
            return "", ""
        name = ""
        for mac, device_name in _bluetooth_devices("Paired"):
            if mac.upper() == candidate.upper():
                name = device_name
                break
        return candidate.upper(), name
    for kind in ("Connected", "Paired"):
        for mac, device_name in _bluetooth_devices(kind):
            return mac.upper(), device_name
    return "", ""


def bt_connected(mac: str) -> bool | None:
    """Is this MAC currently connected? None when BlueZ could not be asked.

    Reads cached device state, so this stays a few-millisecond call even when the
    speaker is powered off — unlike `bluetoothctl connect`, which blocks ~60s against
    an absent device.
    """
    out = _run(["bluetoothctl", "info", mac])
    if out is None:
        return None
    for line in out.splitlines():
        stripped = line.strip()
        if stripped.startswith("Connected:"):
            return stripped.split(":", 1)[1].strip().lower() == "yes"
    # `bluetoothctl info` on an unknown MAC prints "Device <mac> not available".
    return None


def wired_sink_present(token: str) -> bool | None:
    """Is a non-monitor sink whose name contains ``token`` present?

    None when pactl failed, and also when ``token`` is blank: an empty needle is a
    substring of every sink name, so answering True there would report "speaker fine"
    for a display nobody has actually told us what to look for. Silently healthy is
    the one answer this module must never invent.
    """
    needle = token.strip().lower()
    if not needle:
        return None
    out = _run(["pactl", "list", "sinks", "short"])
    if out is None:
        return None
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        sink = parts[1]
        if sink.endswith(".monitor"):
            continue
        if needle in sink.lower():
            return True
    return False


def check_speaker(config: SpeakerConfig) -> SpeakerStatus | None:
    """Reachability of the configured speaker.

    Returns None — meaning "no opinion, show nothing" — when this display has no
    speaker configured, or when the check itself could not run. An unanswerable check
    must not render as "offline": a momentarily busy BlueZ would otherwise flash a
    badge telling someone to go power-cycle a speaker that is playing fine.
    """
    if config.bt_autoconnect:
        mac, name = resolve_bt_mac(config.bt_mac)
        if mac:
            connected = bt_connected(mac)
            if connected is None:
                return None
            return SpeakerStatus(offline=not connected, name=name or "Speaker", kind="bluetooth")
    # Strip here too, not just inside wired_sink_present: a whitespace-only setting is
    # an unconfigured display, and must fall through to "no opinion" rather than count
    # as a wired speaker to watch.
    if config.wired_sink.strip():
        present = wired_sink_present(config.wired_sink)
        if present is None:
            return None
        return SpeakerStatus(offline=not present, name="Speaker", kind="wired")
    return None
