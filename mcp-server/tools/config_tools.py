"""Device configuration tools."""

from __future__ import annotations

import asyncio
import logging
import re

from config import mask_secrets

logger = logging.getLogger("pulse-mcp.config_tools")

# Variables expected to differ per device — excluded from compare_configs diffs
_PER_DEVICE_VARS = {
    "PULSE_BT_MAC",
    "PULSE_BLUETOOTH_AUTOCONNECT",
    "PULSE_DISPLAY_TYPE",
    "PULSE_HOSTNAME",
    "PULSE_NAME",
    "PULSE_URL",
    "PULSE_OVERLAY_CLOCK_SECONDARY_TZ",
}


def _parse_pulse_conf(text: str) -> dict[str, str]:
    """Parse bash-style key=value config, handling quoting."""
    result = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        # Skip export prefix
        if line.startswith("export "):
            line = line[7:]
        key, _, value = line.partition("=")
        key = key.strip()
        # Strip quotes
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        result[key] = value
    return result


def _register(mcp, ssh, config):
    @mcp.tool()
    async def get_device_config(device: str, section: str = "") -> str:
        """Get the configuration of a Pulse device from its pulse.conf file.

        Returns non-empty configuration values with sensitive fields
        (API keys, tokens, passwords) masked. Optionally filter by section.

        Args:
            device: Hostname of the Pulse device (e.g. 'pulse-kitchen')
            section: Optional filter prefix (e.g. 'MQTT', 'PULSE_ASSISTANT',
                     'HOME_ASSISTANT'). Empty returns all non-empty values.
        """
        try:
            raw = await ssh.read_file(device, f"{config.ssh.remote_path}/pulse.conf")
        except Exception as exc:
            return f"Failed to read config from {device}: {exc}"

        parsed = _parse_pulse_conf(raw)
        if not parsed:
            return f"No configuration found on {device} (empty or missing pulse.conf)."

        # Filter to non-empty values
        filtered = {k: v for k, v in sorted(parsed.items()) if v}

        # Apply section filter
        if section:
            prefix = section.upper()
            filtered = {k: v for k, v in filtered.items() if k.upper().startswith(prefix)}

        if not filtered:
            return f"No config values found matching section '{section}' on {device}."

        masked = mask_secrets(filtered)
        lines = [f"=== {device} pulse.conf ({len(masked)} values) ===", ""]
        for key, value in masked.items():
            lines.append(f"{key}={value}")

        return "\n".join(lines)

    @mcp.tool()
    async def compare_configs(devices: str = "") -> str:
        """Compare configuration across Pulse devices.

        Shows variables that differ between devices, excluding expected
        per-device differences (hostname, display type, BT MAC, etc.).

        Args:
            devices: Comma-separated list of hostnames to compare.
                     Empty compares all configured devices.
        """
        device_list = [d.strip() for d in devices.split(",") if d.strip()] if devices else config.devices

        if len(device_list) < 2:
            return "Need at least 2 devices to compare. Configure devices in pulse-devices.conf."

        # Fetch configs in parallel
        async def _fetch(hostname: str) -> tuple[str, dict[str, str]]:
            try:
                raw = await ssh.read_file(hostname, f"{config.ssh.remote_path}/pulse.conf")
                return hostname, _parse_pulse_conf(raw)
            except Exception as exc:
                return hostname, {"_error": str(exc)}

        results = await asyncio.gather(*[_fetch(h) for h in device_list])
        configs: dict[str, dict[str, str]] = dict(results)

        # Check for fetch errors
        errors = {h: c["_error"] for h, c in configs.items() if "_error" in c}
        if errors:
            error_lines = [f"  {h}: {e}" for h, e in errors.items()]
            if len(errors) == len(device_list):
                return "Failed to fetch config from all devices:\n" + "\n".join(error_lines)

        # Collect all keys across all configs
        all_keys: set[str] = set()
        for c in configs.values():
            all_keys.update(c.keys())
        all_keys -= {"_error"}
        all_keys -= _PER_DEVICE_VARS

        # Find keys with differing values
        diffs: dict[str, dict[str, str]] = {}
        for key in sorted(all_keys):
            values = {}
            for hostname, conf in configs.items():
                if "_error" not in conf:
                    values[hostname] = conf.get(key, "<unset>")
            unique = set(values.values())
            if len(unique) > 1:
                diffs[key] = values

        lines = [f"Comparing {len(device_list)} devices: {', '.join(device_list)}", ""]

        if errors:
            lines.append("Fetch errors:")
            lines.extend(f"  {h}: {e}" for h, e in errors.items())
            lines.append("")

        if not diffs:
            lines.append("All configurations match (excluding per-device variables).")
        else:
            masked_diffs = {k: mask_secrets(v) for k, v in diffs.items()}
            lines.append(f"Found {len(diffs)} differing variable(s):")
            lines.append("")
            for key, values in masked_diffs.items():
                lines.append(f"  {key}:")
                for hostname, val in values.items():
                    lines.append(f"    {hostname:<25} = {val}")
                lines.append("")

        return "\n".join(lines)
