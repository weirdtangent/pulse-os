"""Async SSH connection pool for Pulse devices."""

from __future__ import annotations

import asyncio
import logging
import shlex
from pathlib import Path

import asyncssh
from config import SshConfig

logger = logging.getLogger("pulse-mcp.ssh")


class PulseSSH:
    """Manages async SSH connections to Pulse devices."""

    def __init__(self, config: SshConfig) -> None:
        self._config = config
        self._connections: dict[str, asyncssh.SSHClientConnection] = {}

    async def run(self, hostname: str, command: str, timeout: int | None = None) -> str:
        """Execute a command on a device and return output.

        Returns stdout on success. On non-zero exit, returns stderr if stdout
        is empty so that error messages are surfaced to callers.
        """
        conn = await self._get_connection(hostname)
        t = timeout or self._config.timeout
        try:
            result = await asyncio.wait_for(conn.run(command, check=False), timeout=t)
            if result.stderr and result.stderr.strip():
                logger.debug("[%s] stderr: %s", hostname, result.stderr.strip()[:200])
            # Surface stderr when command failed and stdout is empty
            if result.exit_status not in (0, None):
                logger.warning("[%s] Command exited with status %s: %s", hostname, result.exit_status, command[:80])
                stdout_text = (result.stdout or "").strip()
                if not stdout_text and result.stderr:
                    return result.stderr
            return result.stdout or ""
        except TimeoutError:
            logger.warning("[%s] Command timed out after %ds: %s", hostname, t, command[:80])
            self._connections.pop(hostname, None)
            raise
        except (asyncssh.Error, OSError) as exc:
            logger.warning("[%s] SSH error: %s", hostname, exc)
            self._connections.pop(hostname, None)
            raise

    async def read_file(self, hostname: str, path: str) -> str:
        """Read a file from a device."""
        return await self.run(hostname, f"cat {shlex.quote(path)}")

    async def is_reachable(self, hostname: str) -> bool:
        """Check if a device is reachable via SSH."""
        try:
            result = await self.run(hostname, "echo ok", timeout=5)
            return result.strip() == "ok"
        except Exception:
            return False

    async def close_all(self) -> None:
        """Close all cached SSH connections."""
        for host, conn in list(self._connections.items()):
            try:
                conn.close()
                await conn.wait_closed()
            except Exception as exc:
                logger.debug("[%s] Error closing SSH connection during shutdown: %s", host, exc)
        self._connections.clear()

    async def _get_connection(self, hostname: str) -> asyncssh.SSHClientConnection:
        """Get or create a connection to a device."""
        conn = self._connections.get(hostname)
        if conn is not None:
            # Verify the connection is still alive
            try:
                await asyncio.wait_for(conn.run("true", check=False), timeout=3)
                return conn
            except Exception:
                logger.debug("[%s] Stale connection, reconnecting", hostname)
                self._connections.pop(hostname, None)

        logger.info("[%s] Opening SSH connection (user=%s)", hostname, self._config.user)
        key_path = Path(self._config.key_path).expanduser()
        known_hosts: object = None  # default: use system known_hosts
        if self._config.disable_host_key_check:
            known_hosts = ()  # accept any host key
        conn = await asyncssh.connect(
            hostname,
            username=self._config.user,
            client_keys=[str(key_path)] if key_path.exists() else [],
            known_hosts=known_hosts,
            connect_timeout=self._config.timeout,
        )
        self._connections[hostname] = conn
        return conn
