"""Lightweight systemd sd_notify helper (no external dependencies).

Sends notifications to systemd via the ``$NOTIFY_SOCKET`` environment
variable.  All functions are safe no-ops when the variable is unset
(e.g. during development or in non-systemd environments).
"""

from __future__ import annotations

import logging
import os
import socket

_logger = logging.getLogger(__name__)


def _notify(message: str) -> None:
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return
    if addr.startswith("@"):
        addr = "\0" + addr[1:]  # abstract socket
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            sock.sendto(message.encode(), addr)
    except OSError as exc:
        _logger.debug("[sd_notify] Failed to send '%s': %s", message, exc)


def ready() -> None:
    """Tell systemd the service has finished starting up."""
    _notify("READY=1")


def watchdog() -> None:
    """Reset the systemd watchdog timer."""
    _notify("WATCHDOG=1")
