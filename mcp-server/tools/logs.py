"""Device log reading tools."""

from __future__ import annotations

import logging
import shlex

from tools import normalize_service, validate_device

logger = logging.getLogger("pulse-mcp.logs")

MAX_LINES = 500


def _register(mcp, ssh, config):
    @mcp.tool()
    async def get_device_logs(
        device: str,
        service: str = "pulse-assistant",
        lines: int = 100,
        since: str = "",
        priority: str = "",
        grep: str = "",
    ) -> str:
        """Read recent journal logs from a Pulse device service.

        Args:
            device: Hostname of the Pulse device (e.g. 'pulse-kitchen')
            service: Systemd unit name. Common values:
                     pulse-assistant, pulse-kiosk-mqtt, pulse-assistant-display,
                     pulse-backlight-sun
            lines: Maximum lines to return (default 100, max 500)
            since: Time filter, e.g. '1 hour ago', '30 min ago', 'today',
                   '2025-01-15 10:00'. Empty for no time filter.
            priority: Syslog priority filter: 'emerg', 'alert', 'crit', 'err',
                      'warning', 'notice', 'info', 'debug'. Empty for all.
            grep: Text pattern to filter log lines (journalctl --grep). Empty for all.
        """
        if err := validate_device(device, config):
            return err

        service = normalize_service(service)
        lines = min(max(1, lines), MAX_LINES)

        cmd_parts = [
            "journalctl",
            f"-u {shlex.quote(service + '.service')}",
            "--no-pager",
            f"-n {lines}",
        ]
        if since:
            cmd_parts.append(f"--since {shlex.quote(since)}")
        if priority:
            cmd_parts.append(f"-p {shlex.quote(priority)}")
        if grep:
            cmd_parts.append(f"--grep {shlex.quote(grep)}")

        cmd = " ".join(cmd_parts)

        try:
            output = await ssh.run(device, cmd, timeout=15)
            result = output.strip()
            if not result:
                return f"No log entries found for {service} on {device} with the given filters."
            return result
        except Exception as exc:
            return f"Failed to read logs from {device}: {exc}"

    @mcp.tool()
    async def get_all_errors(device: str, since: str = "1 hour ago") -> str:
        """Get all error-level log entries across all Pulse services on a device.

        Useful for quickly identifying problems without knowing which service
        is affected.

        Args:
            device: Hostname of the Pulse device (e.g. 'pulse-kitchen')
            since: Time window (default '1 hour ago'). Use '24 hours ago' for
                   a full day, 'today' for since midnight, etc.
        """
        if err := validate_device(device, config):
            return err

        cmd = f"journalctl -u 'pulse-*' --no-pager -p err --since {shlex.quote(since)} -n 200 2>&1 || true"
        try:
            output = await ssh.run(device, cmd, timeout=15)
            result = output.strip()
            if not result or "No entries" in result:
                return f"No errors found across Pulse services on {device} since {since}."
            return result
        except Exception as exc:
            return f"Failed to read error logs from {device}: {exc}"

    @mcp.tool()
    async def search_logs(
        device: str,
        pattern: str,
        since: str = "1 hour ago",
        service: str = "",
    ) -> str:
        """Search device logs for a pattern (regex supported).

        Uses journalctl --grep for efficient server-side filtering.

        Args:
            device: Hostname of the Pulse device (e.g. 'pulse-kitchen')
            pattern: Search pattern (case-insensitive regex)
            since: Time window (default '1 hour ago')
            service: Optional service filter (e.g. 'pulse-assistant').
                     Empty searches all pulse-* services.
        """
        if err := validate_device(device, config):
            return err

        service = normalize_service(service) if service else ""
        unit = f"{service}.service" if service else "pulse-*"
        cmd = (
            f"journalctl -u {shlex.quote(unit)} --no-pager -n 200"
            f" --since {shlex.quote(since)}"
            f" --grep {shlex.quote(pattern)}"
            " 2>&1 || true"
        )
        try:
            output = await ssh.run(device, cmd, timeout=15)
            result = output.strip()
            if not result or "No entries" in result:
                return f"No matches for '{pattern}' in {unit} on {device} since {since}."
            return result
        except Exception as exc:
            return f"Failed to search logs on {device}: {exc}"
