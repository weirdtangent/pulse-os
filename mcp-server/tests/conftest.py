"""Shared fixtures for the MCP server tests.

Every tool in this server talks to devices over SSH, so the tests swap in a
`FakeSSH` that records the commands it is asked to run and replays canned
output. That makes two things testable without touching a real kiosk: the
command a tool *builds* (quoting, clamping, unit names) and how it renders the
output it gets back.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from config import ServerConfig, SshConfig
from mcp.server.mcpserver import MCPServer  # type: ignore[import-not-found]

# mcp-server/ itself, so `import config` / `import tools` resolve the same way
# they do when server.py runs.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class FakeSSH:
    """Stand-in for PulseSSH that records commands instead of running them.

    `responses` maps a substring of the command to what `run` should return —
    either a string, or an exception instance to raise. First match wins, so
    register the most specific substring first.
    """

    def __init__(
        self,
        responses: dict[str, object] | None = None,
        unreachable: tuple[str, ...] = (),
    ) -> None:
        self.responses: dict[str, object] = {"echo ok": "ok"}
        self.responses.update(responses or {})
        self.unreachable = set(unreachable)
        self.calls: list[tuple[str, str, int | None]] = []

    async def run(self, hostname: str, command: str, timeout: int | None = None) -> str:
        self.calls.append((hostname, command, timeout))
        if hostname in self.unreachable:
            raise OSError(f"[Errno 64] Host is down: {hostname}")
        for needle, value in self.responses.items():
            if needle in command:
                if isinstance(value, Exception):
                    raise value
                return str(value)
        return ""

    async def read_file(self, hostname: str, path: str) -> str:
        import shlex

        return await self.run(hostname, f"cat {shlex.quote(path)}")

    async def is_reachable(self, hostname: str) -> bool:
        try:
            result = await self.run(hostname, "echo ok", timeout=5)
            return result.strip() == "ok"
        except Exception:
            return False

    # -- assertions helpers -------------------------------------------------

    def commands(self, hostname: str | None = None) -> list[str]:
        """Every command issued, optionally filtered to one host."""
        return [cmd for host, cmd, _ in self.calls if hostname is None or host == hostname]

    @property
    def hosts(self) -> list[str]:
        return [host for host, _, _ in self.calls]


@pytest.fixture
def config() -> ServerConfig:
    return ServerConfig(
        ssh=SshConfig(user="pulse", remote_path="/opt/pulse-os", timeout=10),
        devices=["pulse-office", "pulse-kitchen"],
    )


@pytest.fixture
def ssh() -> FakeSSH:
    return FakeSSH()


@pytest.fixture
def server(config: ServerConfig, ssh: FakeSSH) -> MCPServer:
    """An MCPServer with the real tool modules registered against FakeSSH."""
    from tools.config_tools import _register as reg_config
    from tools.diagnostics import _register as reg_diag
    from tools.logs import _register as reg_logs
    from tools.status import _register as reg_status

    mcp = MCPServer("pulse-os-test")
    reg_status(mcp, ssh, config)
    reg_logs(mcp, ssh, config)
    reg_config(mcp, ssh, config)
    reg_diag(mcp, ssh, config)
    return mcp


@pytest.fixture
def call(server: MCPServer):
    """Call a tool the way a client would and return its text output."""

    async def _call(name: str, **arguments: object) -> str:
        result = await server.call_tool(name, arguments)
        return "\n".join(block.text for block in result.content if hasattr(block, "text"))

    return _call
