"""Device listing and status rendering."""

from __future__ import annotations

from config import ServerConfig
from mcp.server.mcpserver import MCPServer  # type: ignore[import-not-found]
from tools.status import _register as register_status

from tools import PULSE_SERVICES

# One line per service in PULSE_SERVICES order — how `systemctl is-active a b c`
# answers for several units at once.
ALL_ACTIVE = "\n".join(["active"] * len(PULSE_SERVICES))


async def test_list_devices_reports_reachability(call, ssh):
    ssh.unreachable.add("pulse-kitchen")

    out = await call("list_devices")

    assert "pulse-office" in out
    lines = {line.split()[0]: line for line in out.splitlines() if line.startswith("pulse-")}
    assert lines["pulse-office"].split()[-1] == "yes"
    assert lines["pulse-kitchen"].split()[-1] == "NO"


async def test_list_devices_without_any_configured(ssh):
    mcp = MCPServer("t")
    register_status(mcp, ssh, ServerConfig(devices=[]))

    result = await mcp.call_tool("list_devices", {})

    assert "No devices configured" in result.content[0].text


async def test_status_converts_millidegree_temperature(call, ssh):
    ssh.responses["thermal_zone0/temp"] = "48512"

    out = await call("get_device_status", device="pulse-office")

    assert "CPU Temp:      48.5C" in out


async def test_status_tolerates_a_missing_temperature(call, ssh):
    ssh.responses["thermal_zone0/temp"] = ""

    out = await call("get_device_status", device="pulse-office")

    assert "CPU Temp:      n/a" in out


async def test_status_lists_every_pulse_service(call, ssh):
    ssh.responses["is-active"] = ALL_ACTIVE

    out = await call("get_device_status", device="pulse-office")

    for service in PULSE_SERVICES:
        assert service in out
    assert "!" not in out  # no service flagged


async def test_status_flags_a_service_that_is_not_active(call, ssh):
    states = ["active"] * len(PULSE_SERVICES)
    states[1] = "failed"
    ssh.responses["is-active"] = "\n".join(states)

    out = await call("get_device_status", device="pulse-office")

    flagged = [line for line in out.splitlines() if line.strip().startswith("!")]
    assert len(flagged) == 1
    assert PULSE_SERVICES[1] in flagged[0]
    assert "failed" in flagged[0]


async def test_status_marks_missing_service_output_as_unknown(call, ssh):
    """Short output must not silently shift states onto the wrong services."""
    ssh.responses["is-active"] = "active"

    out = await call("get_device_status", device="pulse-office")

    assert out.count("unknown") == len(PULSE_SERVICES) - 1


async def test_status_reports_a_failing_command_inline(call, ssh):
    """One broken command should not sink the whole status report."""
    ssh.responses["uptime"] = OSError("no such command")

    out = await call("get_device_status", device="pulse-office")

    assert "Uptime:        error:" in out
    assert "Kernel:" in out  # the rest still rendered


async def test_status_refuses_an_unknown_device(call, ssh):
    out = await call("get_device_status", device="pulse-attic")

    assert "Unknown device 'pulse-attic'" in out
    assert ssh.calls == []


async def test_service_status_rejects_a_service_outside_the_list(call, ssh):
    out = await call("get_service_status", device="pulse-office", service="sshd")

    assert "Unknown service 'sshd'" in out
    assert ssh.calls == []


async def test_service_status_accepts_a_dot_service_suffix(call, ssh):
    ssh.responses["systemctl status"] = "● pulse-assistant.service - Pulse Assistant"

    out = await call("get_service_status", device="pulse-office", service="pulse-assistant.service")

    assert "Pulse Assistant" in out
    assert "systemctl status pulse-assistant.service" in ssh.commands()[-1]


async def test_service_status_handles_silence(call):
    out = await call("get_service_status", device="pulse-office", service="pulse-snapclient")

    assert "No output from systemctl status pulse-snapclient" in out
