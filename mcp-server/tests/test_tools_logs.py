"""Log tools: the journalctl command they build, and how they report results."""

from __future__ import annotations

import shlex


async def test_unknown_device_is_refused_before_any_ssh(call, ssh):
    out = await call("get_device_logs", device="pulse-garage")

    assert "Unknown device 'pulse-garage'" in out
    assert "pulse-office" in out  # tells the caller what is configured
    assert ssh.calls == []


async def test_line_count_is_clamped_to_the_ceiling(call, ssh):
    await call("get_device_logs", device="pulse-office", lines=100_000)

    assert "-n 500" in ssh.commands()[-1]


async def test_line_count_floors_at_one(call, ssh):
    await call("get_device_logs", device="pulse-office", lines=0)

    assert "-n 1" in ssh.commands()[-1]


async def test_service_name_is_normalised(call, ssh):
    """'pulse-assistant.service' and 'pulse-assistant' mean the same unit."""
    await call("get_device_logs", device="pulse-office", service="pulse-assistant.service")

    assert "-u pulse-assistant.service" in ssh.commands()[-1]


async def test_filters_are_shell_quoted(call, ssh):
    """`since` and `grep` reach a shell, so they must not be interpolated raw."""
    nasty = "1 hour ago; rm -rf /"
    await call("get_device_logs", device="pulse-office", since=nasty, grep="oops'; reboot #")

    cmd = ssh.commands()[-1]
    assert f"--since {shlex.quote(nasty)}" in cmd
    assert "--since 1 hour ago; rm -rf /" not in cmd
    assert "; reboot" not in cmd.replace(shlex.quote("oops'; reboot #"), "")


async def test_optional_filters_are_omitted_when_empty(call, ssh):
    await call("get_device_logs", device="pulse-office")

    cmd = ssh.commands()[-1]
    assert "--since" not in cmd
    assert "--grep" not in cmd
    assert "-p " not in cmd


async def test_empty_output_reads_as_no_entries(call):
    out = await call("get_device_logs", device="pulse-office", service="pulse-assistant")

    assert "No log entries found for pulse-assistant on pulse-office" in out


async def test_ssh_failure_is_reported_not_raised(call, ssh):
    ssh.unreachable.add("pulse-office")

    out = await call("get_device_logs", device="pulse-office")

    assert out.startswith("Failed to read logs from pulse-office:")


async def test_get_all_errors_defaults_to_the_last_hour(call, ssh):
    await call("get_all_errors", device="pulse-office")

    cmd = ssh.commands()[-1]
    assert "-p err" in cmd
    assert "--since '1 hour ago'" in cmd
    assert "-u 'pulse-*'" in cmd


async def test_get_all_errors_treats_no_entries_as_clean(call, ssh):
    ssh.responses["-p err"] = "-- No entries --"

    out = await call("get_all_errors", device="pulse-office", since="today")

    assert "No errors found across Pulse services on pulse-office since today." == out


async def test_get_all_errors_returns_what_it_found(call, ssh):
    ssh.responses["-p err"] = "Sep 21 10:00:00 pulse-office pulse-assistant[1]: boom"

    out = await call("get_all_errors", device="pulse-office")

    assert "boom" in out


async def test_search_logs_spans_all_services_by_default(call, ssh):
    await call("search_logs", device="pulse-office", pattern="snapclient")

    cmd = ssh.commands()[-1]
    assert "-u 'pulse-*'" in cmd
    assert "--grep snapclient" in cmd


async def test_search_logs_can_scope_to_one_service(call, ssh):
    await call("search_logs", device="pulse-office", pattern="x", service="pulse-snapclient")

    assert "-u pulse-snapclient.service" in ssh.commands()[-1]


async def test_search_pattern_is_quoted(call, ssh):
    await call("search_logs", device="pulse-office", pattern="a b; whoami")

    assert f"--grep {shlex.quote('a b; whoami')}" in ssh.commands()[-1]


async def test_search_reports_no_matches(call):
    out = await call("search_logs", device="pulse-office", pattern="nothing-here")

    assert "No matches for 'nothing-here'" in out
