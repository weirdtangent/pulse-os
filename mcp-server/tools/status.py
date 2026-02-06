"""Device status tools."""

from __future__ import annotations

import asyncio
import logging

from tools import PULSE_SERVICES, normalize_service, validate_device

logger = logging.getLogger("pulse-mcp.status")


def _register(mcp, ssh, config):
    @mcp.tool()
    async def list_devices() -> str:
        """List all known Pulse devices with reachability status.

        Shows each configured device and whether it responds to SSH.
        """
        if not config.devices:
            return "No devices configured. Add hostnames to pulse-devices.conf or pulse-mcp.conf."

        lines = [f"{'Device':<28} {'SSH Reachable':<15}"]
        lines.append("-" * 43)

        async def _check(hostname: str) -> str:
            reachable = await ssh.is_reachable(hostname)
            status = "yes" if reachable else "NO"
            return f"{hostname:<28} {status:<15}"

        results = await asyncio.gather(*[_check(h) for h in config.devices], return_exceptions=True)
        for r in results:
            lines.append(str(r) if not isinstance(r, Exception) else f"  error: {r}")

        return "\n".join(lines)

    @mcp.tool()
    async def get_device_status(device: str) -> str:
        """Get comprehensive status for a Pulse device.

        Shows systemd service states, OS info, pulse-os version, uptime,
        disk usage, and basic system metrics.

        Args:
            device: Hostname of the Pulse device (e.g. 'pulse-kitchen')
        """
        if err := validate_device(device, config):
            return err

        commands = {
            "services": f"systemctl is-active {' '.join(PULSE_SERVICES)} 2>/dev/null || true",
            "uptime": "uptime -p 2>/dev/null || uptime",
            "disk": "df -h / | tail -1",
            "os": "cat /etc/os-release 2>/dev/null | head -4",
            "kernel": "uname -r",
            "cpu_temp": "cat /sys/class/thermal/thermal_zone0/temp 2>/dev/null || echo ''",
            "memory": "free -h | grep Mem",
            "pulse_version": (
                f"grep -m1 '__version__' {config.ssh.remote_path}/pulse/__init__.py 2>/dev/null"
                f" || grep -m1 'version' {config.ssh.remote_path}/pyproject.toml 2>/dev/null"
                " || echo 'unknown'"
            ),
        }

        results = {}
        for key, cmd in commands.items():
            try:
                results[key] = (await ssh.run(device, cmd)).strip()
            except Exception as exc:
                results[key] = f"error: {exc}"

        # Parse service states
        svc_states = results.get("services", "").splitlines()
        svc_lines = []
        for i, svc in enumerate(PULSE_SERVICES):
            state = svc_states[i].strip() if i < len(svc_states) else "unknown"
            marker = "  " if state == "active" else "! "
            svc_lines.append(f"  {marker}{svc:<35} {state}")

        # Parse CPU temperature
        temp_raw = results.get("cpu_temp", "")
        try:
            temp_val = int(temp_raw) / 1000 if temp_raw and int(temp_raw) > 1000 else temp_raw
            temp_str = f"{temp_val:.1f}C" if isinstance(temp_val, float) else (temp_raw or "n/a")
        except (ValueError, TypeError):
            temp_str = temp_raw or "n/a"

        sections = [
            f"=== {device} ===",
            "",
            f"Uptime:        {results.get('uptime', 'n/a')}",
            f"Kernel:        {results.get('kernel', 'n/a')}",
            f"Pulse version: {results.get('pulse_version', 'n/a')}",
            f"CPU Temp:      {temp_str}",
            f"Memory:        {results.get('memory', 'n/a')}",
            f"Disk:          {results.get('disk', 'n/a')}",
            "",
            "Services:",
            *svc_lines,
        ]

        # OS info
        os_info = results.get("os", "")
        if os_info and "error" not in os_info:
            os_lines = [f"  {line}" for line in os_info.splitlines()]
            sections.extend(["", "OS:", *os_lines])

        return "\n".join(sections)

    @mcp.tool()
    async def get_service_status(device: str, service: str = "pulse-assistant") -> str:
        """Get detailed systemd status for a specific service on a Pulse device.

        Shows the full systemctl status output including recent log lines.

        Args:
            device: Hostname of the Pulse device (e.g. 'pulse-kitchen')
            service: Systemd service name. Common values:
                     pulse-kiosk-mqtt, pulse-assistant, pulse-assistant-display,
                     pulse-backlight-sun, pulse-snapclient
        """
        if err := validate_device(device, config):
            return err
        service = normalize_service(service)
        if service not in PULSE_SERVICES:
            return f"Unknown service '{service}'. Known services: {', '.join(PULSE_SERVICES)}"

        try:
            output = await ssh.run(device, f"systemctl status {service}.service --no-pager -l 2>&1 || true")
            return output.strip() or f"No output from systemctl status {service}"
        except Exception as exc:
            return f"Failed to get status for {service} on {device}: {exc}"
