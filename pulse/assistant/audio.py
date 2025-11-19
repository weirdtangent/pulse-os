"""Audio input/output helpers for the assistant."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from asyncio.subprocess import Process


class ArecordStream:
    """Capture PCM audio by shelling out to ``arecord`` (ALSA)."""

    def __init__(
        self,
        command: list[str],
        bytes_per_chunk: int,
        logger: logging.Logger | None = None,
    ) -> None:
        self.command = command
        self.bytes_per_chunk = bytes_per_chunk
        self._proc: Process | None = None
        self._logger = logger or logging.getLogger(__name__)

    async def start(self) -> None:
        if self._proc:
            return
        self._logger.debug("Starting microphone capture: %s", " ".join(self.command))
        self._proc = await asyncio.create_subprocess_exec(
            *self.command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

    async def read_chunk(self) -> bytes:
        if not self._proc or not self._proc.stdout:
            raise RuntimeError("Microphone stream is not running")
        try:
            return await self._proc.stdout.readexactly(self.bytes_per_chunk)
        except asyncio.IncompleteReadError as exc:
            stderr = ""
            if self._proc.stderr:
                try:
                    stderr = (await self._proc.stderr.read()).decode("utf-8", errors="ignore").strip()
                except Exception:  # pragma: no cover - best effort
                    stderr = ""
            message = "Microphone stream ended unexpectedly"
            if stderr:
                message = f"{message} ({stderr})"
            raise RuntimeError(message) from exc

    async def stop(self) -> None:
        if not self._proc:
            return
        self._logger.debug("Stopping microphone capture")
        if self._proc.stdout:
            self._proc.stdout.feed_eof()
        if self._proc.stderr:
            self._proc.stderr.feed_eof()
        self._proc.terminate()
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self._proc.wait(), timeout=2)
        self._proc = None


class AplaySink:
    """Play PCM audio via ``aplay``."""

    def __init__(self, binary: str = "aplay", logger: logging.Logger | None = None) -> None:
        self.binary = binary
        self._proc: Process | None = None
        self._logger = logger or logging.getLogger(__name__)

    async def start(self, rate: int, width: int, channels: int) -> None:
        await self.stop()
        fmt = _alsa_format(width)
        cmd = [
            self.binary,
            "-q",
            "-t",
            "raw",
            "-f",
            fmt,
            "-c",
            str(channels),
            "-r",
            str(rate),
            "-",
        ]
        self._logger.debug("Starting playback: %s", " ".join(cmd))
        self._proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

    async def write(self, chunk: bytes) -> None:
        if not self._proc or not self._proc.stdin:
            raise RuntimeError("Playback is not active")
        self._proc.stdin.write(chunk)
        await self._proc.stdin.drain()

    async def stop(self) -> None:
        if not self._proc:
            return
        self._logger.debug("Stopping playback")
        if self._proc.stdin:
            self._proc.stdin.close()
            with contextlib.suppress(BrokenPipeError):
                await self._proc.stdin.wait_closed()
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self._proc.wait(), timeout=2)
        self._proc = None


def _alsa_format(width: int) -> str:
    return {
        1: "U8",
        2: "S16_LE",
        3: "S24_LE",
        4: "S32_LE",
    }.get(width, "S16_LE")
