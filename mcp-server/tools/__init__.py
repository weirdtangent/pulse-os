"""Shared helpers for MCP tool modules."""

from __future__ import annotations

PULSE_SERVICES = [
    "pulse-kiosk-mqtt",
    "pulse-assistant",
    "pulse-assistant-display",
    "pulse-backlight-sun",
    "pulse-snapclient",
]


def validate_device(device: str, config) -> str | None:
    """Return an error message if device is not in the configured allowlist, else None."""
    if not config.devices:
        return None  # no allowlist configured, allow any device
    if device in config.devices:
        return None
    return (
        f"Unknown device '{device}'. Configured devices: {', '.join(config.devices)}.\n"
        "Add it to pulse-devices.conf or pulse-mcp.conf to allow access."
    )


def normalize_service(service: str) -> str:
    """Strip trailing .service suffix if present."""
    if service.endswith(".service"):
        return service[: -len(".service")]
    return service
