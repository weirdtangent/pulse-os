"""PulseSSH behaviour around command results, failures and connection reuse.

These drive the real PulseSSH against a stub connection seeded into its pool,
so no network is involved and `asyncssh.connect` is never reached.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from config import SshConfig
from ssh import PulseSSH


@dataclass
class FakeResult:
    stdout: str = ""
    stderr: str = ""
    exit_status: int = 0


class FakeConn:
    """Minimal asyncssh connection stub."""

    def __init__(self, results: dict[str, FakeResult] | None = None, raises: Exception | None = None) -> None:
        self.results = results or {}
        self.raises = raises
        self.commands: list[str] = []
        self.closed = False

    async def run(self, command: str, check: bool = False) -> FakeResult:
        self.commands.append(command)
        if command == "true":  # liveness probe in _get_connection
            return FakeResult(stdout="")
        if self.raises is not None:
            raise self.raises
        for needle, result in self.results.items():
            if needle in command:
                return result
        return FakeResult(stdout="")

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None


@pytest.fixture
def pool() -> PulseSSH:
    return PulseSSH(SshConfig(user="pulse", timeout=10))


def _seed(pool: PulseSSH, host: str, conn: FakeConn) -> FakeConn:
    pool._connections[host] = conn
    return conn


async def test_returns_stdout(pool):
    _seed(pool, "pulse-office", FakeConn({"uname": FakeResult(stdout="6.12.0\n")}))

    assert await pool.run("pulse-office", "uname -r") == "6.12.0\n"


async def test_surfaces_stderr_when_the_command_failed_with_no_stdout(pool):
    """Otherwise a failing command looks identical to one that printed nothing."""
    _seed(
        pool,
        "pulse-office",
        FakeConn({"systemctl": FakeResult(stdout="", stderr="Unit not found.", exit_status=4)}),
    )

    assert await pool.run("pulse-office", "systemctl status nope") == "Unit not found."


async def test_prefers_stdout_even_on_failure(pool):
    _seed(
        pool,
        "pulse-office",
        FakeConn({"journalctl": FakeResult(stdout="some output", stderr="a warning", exit_status=1)}),
    )

    assert await pool.run("pulse-office", "journalctl -u x") == "some output"


async def test_read_file_quotes_the_path(pool):
    conn = _seed(pool, "pulse-office", FakeConn({"cat": FakeResult(stdout="KEY=value")}))

    await pool.read_file("pulse-office", "/opt/pulse os/pulse.conf")

    assert conn.commands[-1] == "cat '/opt/pulse os/pulse.conf'"


async def test_is_reachable_true_on_echo(pool):
    _seed(pool, "pulse-office", FakeConn({"echo ok": FakeResult(stdout="ok\n")}))

    assert await pool.is_reachable("pulse-office") is True


async def test_is_reachable_false_on_error(pool):
    _seed(pool, "pulse-office", FakeConn(raises=OSError("host is down")))

    assert await pool.is_reachable("pulse-office") is False


async def test_failed_connection_is_dropped_from_the_pool(pool):
    """A dead socket must not be handed to the next call."""
    _seed(pool, "pulse-office", FakeConn(raises=OSError("connection reset")))

    with pytest.raises(OSError):
        await pool.run("pulse-office", "uptime")

    assert "pulse-office" not in pool._connections


async def test_timeout_also_drops_the_connection(pool):
    _seed(pool, "pulse-office", FakeConn(raises=TimeoutError()))

    with pytest.raises((TimeoutError, OSError)):
        await pool.run("pulse-office", "sleep 60")

    assert "pulse-office" not in pool._connections


async def test_live_connection_is_reused(pool):
    conn = _seed(pool, "pulse-office", FakeConn({"uptime": FakeResult(stdout="up 3 days")}))

    await pool.run("pulse-office", "uptime")
    await pool.run("pulse-office", "uptime")

    assert pool._connections["pulse-office"] is conn
    assert conn.commands.count("uptime") == 2


async def test_close_all_closes_and_clears(pool):
    conn = _seed(pool, "pulse-office", FakeConn())

    await pool.close_all()

    assert conn.closed is True
    assert pool._connections == {}
