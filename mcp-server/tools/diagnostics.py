"""Device diagnostic and management tools."""

from __future__ import annotations

import logging

logger = logging.getLogger("pulse-mcp.diagnostics")

_ALLOWED_SERVICES = {
    "pulse-kiosk-mqtt",
    "pulse-assistant",
    "pulse-assistant-display",
    "pulse-backlight-sun",
    "pulse-snapclient",
}


def _register(mcp, ssh, config):
    @mcp.tool()
    async def run_diagnostics(device: str) -> str:
        """Run the Pulse OS configuration verification tool on a remote device.

        Checks MQTT connectivity, Wyoming STT/TTS/wake endpoints,
        Home Assistant API access, and more. This is the same verify-conf.py
        tool that runs during device setup.

        Args:
            device: Hostname of the Pulse device (e.g. 'pulse-kitchen')
        """
        cmd = (
            f"cd {config.ssh.remote_path} &&"
            f" {config.ssh.remote_path}/.venv/bin/python"
            f" {config.ssh.remote_path}/bin/tools/verify-conf.py"
            " --timeout 10 2>&1 || true"
        )
        try:
            output = await ssh.run(device, cmd, timeout=30)
            result = output.strip()
            if not result:
                return f"verify-conf.py produced no output on {device}. Check if the tool exists."
            return f"=== Diagnostics for {device} ===\n\n{result}"
        except Exception as exc:
            return f"Failed to run diagnostics on {device}: {exc}"

    @mcp.tool()
    async def restart_service(device: str, service: str = "pulse-assistant") -> str:
        """Restart a systemd service on a Pulse device.

        Only known Pulse services can be restarted for safety.

        Args:
            device: Hostname of the Pulse device (e.g. 'pulse-kitchen')
            service: Service to restart. Allowed values:
                     pulse-kiosk-mqtt, pulse-assistant, pulse-assistant-display,
                     pulse-backlight-sun, pulse-snapclient
        """
        if service not in _ALLOWED_SERVICES:
            return (
                f"Service '{service}' is not in the allowed list.\n"
                f"Allowed services: {', '.join(sorted(_ALLOWED_SERVICES))}"
            )

        try:
            await ssh.run(device, f"sudo systemctl restart {service}.service", timeout=15)
            # Check the new status
            status = await ssh.run(device, f"systemctl is-active {service}.service 2>/dev/null || true")
            state = status.strip()
            if state == "active":
                return f"Successfully restarted {service} on {device}. Service is now active."
            else:
                return f"Restarted {service} on {device}, but current state is: {state}"
        except Exception as exc:
            return f"Failed to restart {service} on {device}: {exc}"

    @mcp.tool()
    async def check_connectivity(device: str) -> str:
        """Check basic connectivity and health of a Pulse device.

        Tests SSH reachability, then checks systemd services, network
        connectivity to the MQTT broker, and recent error count.

        Args:
            device: Hostname of the Pulse device (e.g. 'pulse-kitchen')
        """
        results = []

        # SSH check
        reachable = await ssh.is_reachable(device)
        if not reachable:
            return f"{device} is NOT reachable via SSH."

        results.append(f"SSH:  OK (connected to {device})")

        # Service states
        try:
            svc_output = await ssh.run(
                device,
                "systemctl is-active pulse-kiosk-mqtt pulse-assistant pulse-assistant-display 2>/dev/null || true",
            )
            services = ["pulse-kiosk-mqtt", "pulse-assistant", "pulse-assistant-display"]
            states = svc_output.strip().splitlines()
            for i, svc in enumerate(services):
                state = states[i].strip() if i < len(states) else "unknown"
                marker = "OK" if state == "active" else "FAIL"
                results.append(f"  {svc}: {marker} ({state})")
        except Exception as exc:
            results.append(f"  Services: error ({exc})")

        # MQTT broker reachability (from the device's perspective, using device config)
        try:
            remote_conf = config.ssh.remote_path
            mqtt_check = await ssh.run(
                device,
                f"source {remote_conf}/pulse.conf 2>/dev/null;"
                " timeout 3 bash -c '</dev/tcp/${{MQTT_HOST:-localhost}}/${{MQTT_PORT:-1883}}' 2>&1"
                " && echo 'OK' || echo 'FAIL'",
                timeout=10,
            )
            mqtt_status = mqtt_check.strip().splitlines()[-1] if mqtt_check.strip() else "unknown"
            results.append(f"MQTT: {mqtt_status} (from device to broker)")
        except Exception:
            results.append("MQTT: could not check")

        # Recent error count
        try:
            err_output = await ssh.run(
                device,
                "journalctl -u 'pulse-*' -p err --since '1 hour ago' --no-pager 2>&1 | wc -l || echo 0",
                timeout=10,
            )
            err_count = err_output.strip()
            results.append(f"Errors (last hour): {err_count} lines")
        except Exception:
            results.append("Errors: could not check")

        return f"=== Connectivity check: {device} ===\n" + "\n".join(results)
