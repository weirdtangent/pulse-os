"""Diagnostics, service restarts, and the connectivity check.

restart_service is the only tool here that changes state on a device, so its
guard rails get the most attention.
"""

from __future__ import annotations

from tools import PULSE_SERVICES


async def test_restart_refuses_a_service_outside_the_allowlist(call, ssh):
    """The allowlist is the safety boundary: nothing may reach systemctl."""
    out = await call("restart_service", device="pulse-office", service="sshd")

    assert "not in the allowed list" in out
    assert ssh.calls == []


async def test_restart_refuses_before_touching_an_unknown_device(call, ssh):
    out = await call("restart_service", device="pulse-attic", service="pulse-assistant")

    assert "Unknown device 'pulse-attic'" in out
    assert ssh.calls == []


async def test_restart_lists_what_is_allowed(call):
    out = await call("restart_service", device="pulse-office", service="nginx")

    for service in PULSE_SERVICES:
        assert service in out


async def test_restart_reports_success_when_the_unit_comes_back(call, ssh):
    ssh.responses["is-active"] = "active"

    out = await call("restart_service", device="pulse-office", service="pulse-snapclient")

    assert "Successfully restarted pulse-snapclient on pulse-office" in out
    assert "sudo systemctl restart pulse-snapclient.service" in ssh.commands()


async def test_restart_reports_a_unit_that_did_not_come_back(call, ssh):
    ssh.responses["is-active"] = "failed"

    out = await call("restart_service", device="pulse-office", service="pulse-assistant")

    assert "current state is: failed" in out


async def test_restart_accepts_a_dot_service_suffix(call, ssh):
    ssh.responses["is-active"] = "active"

    out = await call("restart_service", device="pulse-office", service="pulse-assistant.service")

    assert "Successfully restarted pulse-assistant" in out


async def test_restart_reports_ssh_failure(call, ssh):
    ssh.unreachable.add("pulse-office")

    out = await call("restart_service", device="pulse-office", service="pulse-assistant")

    assert out.startswith("Failed to restart pulse-assistant on pulse-office:")


async def test_diagnostics_runs_verify_conf_from_the_remote_path(call, ssh):
    ssh.responses["verify-conf.py"] = "MQTT: OK\nWyoming STT: OK"

    out = await call("run_diagnostics", device="pulse-office")

    assert "=== Diagnostics for pulse-office ===" in out
    assert "Wyoming STT: OK" in out
    assert "/opt/pulse-os/bin/tools/verify-conf.py" in ssh.commands()[-1]


async def test_diagnostics_reports_silence_as_a_missing_tool(call):
    out = await call("run_diagnostics", device="pulse-office")

    assert "produced no output" in out


async def test_connectivity_stops_at_an_unreachable_device(call, ssh):
    """No point probing services on a box that will not answer SSH."""
    ssh.unreachable.add("pulse-office")

    out = await call("check_connectivity", device="pulse-office")

    assert out == "pulse-office is NOT reachable via SSH."


async def test_connectivity_reports_each_probe(call, ssh):
    ssh.responses["is-active"] = "active\nactive\nactive"
    ssh.responses["/dev/tcp/"] = "OK"
    ssh.responses["wc -l"] = "3"

    out = await call("check_connectivity", device="pulse-office")

    assert "SSH:  OK (connected to pulse-office)" in out
    assert "pulse-kiosk-mqtt: OK (active)" in out
    assert "MQTT: OK" in out
    assert "Errors (last hour): 3 lines" in out


async def test_connectivity_flags_a_dead_service(call, ssh):
    ssh.responses["is-active"] = "active\ninactive\nactive"

    out = await call("check_connectivity", device="pulse-office")

    assert "pulse-assistant: FAIL (inactive)" in out


async def test_connectivity_survives_a_failing_probe(call, ssh):
    """One probe erroring must not lose the results of the others."""
    ssh.responses["wc -l"] = OSError("command not found")

    out = await call("check_connectivity", device="pulse-office")

    assert "SSH:  OK" in out
    assert "Errors: could not check" in out
