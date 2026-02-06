"""Configuration loading for the Pulse OS MCP server."""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("pulse-mcp.config")

_CONFIG_SEARCH_PATHS = [
    Path("pulse-mcp.conf"),  # relative to cwd (repo root)
    Path(__file__).resolve().parent.parent / "pulse-mcp.conf",  # repo root from mcp-server/
]

# Fields whose values should be masked in output
_SECRET_PATTERNS = re.compile(r"(TOKEN|PASS|PASSWORD|SECRET|API_KEY|APIKEY)", re.IGNORECASE)


@dataclass
class SshConfig:
    user: str = "pulse"
    key_path: str = "~/.ssh/id_ed25519"
    remote_path: str = "/opt/pulse-os"
    timeout: int = 10


@dataclass
class MqttConfig:
    host: str = ""
    port: int = 1883
    username: str = ""
    password: str = ""
    tls_enabled: bool = False


@dataclass
class ServerConfig:
    ssh: SshConfig = field(default_factory=SshConfig)
    mqtt: MqttConfig = field(default_factory=MqttConfig)
    devices: list[str] = field(default_factory=list)
    devices_file: str = "pulse-devices.conf"
    auto_discover: bool = True


def _find_config_path() -> Path | None:
    env_path = os.environ.get("PULSE_MCP_CONFIG")
    if env_path:
        p = Path(env_path).expanduser()
        if p.exists():
            return p
        logger.warning("PULSE_MCP_CONFIG=%s does not exist", env_path)
        return None

    for candidate in _CONFIG_SEARCH_PATHS:
        resolved = candidate.expanduser().resolve()
        if resolved.exists():
            return resolved
    return None


def _load_devices_file(path: Path) -> list[str]:
    if not path.exists():
        return []
    devices = []
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            devices.append(stripped)
    return devices


def load_config() -> ServerConfig:
    config = ServerConfig()

    config_path = _find_config_path()
    if config_path:
        logger.info("Loading config from %s", config_path)
        raw = json.loads(config_path.read_text())

        if "ssh" in raw:
            for k, v in raw["ssh"].items():
                if hasattr(config.ssh, k):
                    setattr(config.ssh, k, v)

        if "mqtt" in raw:
            for k, v in raw["mqtt"].items():
                if hasattr(config.mqtt, k):
                    setattr(config.mqtt, k, v)

        if "devices" in raw:
            config.devices = raw["devices"]
        if "devices_file" in raw:
            config.devices_file = raw["devices_file"]
        if "auto_discover" in raw:
            config.auto_discover = raw["auto_discover"]
    else:
        logger.info("No pulse-mcp.conf found; using defaults")

    # Load devices from file (resolve relative to config file or repo root)
    if config.devices_file:
        devices_path = Path(config.devices_file)
        if not devices_path.is_absolute():
            if config_path:
                devices_path = config_path.parent / devices_path
            else:
                devices_path = Path(__file__).resolve().parent.parent / devices_path
        file_devices = _load_devices_file(devices_path)
        # Merge: config.devices + file devices, deduplicated, order preserved
        seen = set(config.devices)
        for d in file_devices:
            if d not in seen:
                config.devices.append(d)
                seen.add(d)

    logger.info("Configured devices: %s", config.devices)
    return config


def mask_secrets(config_vars: dict[str, str]) -> dict[str, str]:
    """Mask sensitive values in a config dictionary."""
    masked = {}
    for key, value in config_vars.items():
        if _SECRET_PATTERNS.search(key) and value:
            masked[key] = "***"
        else:
            masked[key] = value
    return masked
