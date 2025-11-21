"""Audio input/output helpers for the assistant."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import shutil
from asyncio.subprocess import Process

from pulse import audio as pulse_audio


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
    """Play PCM audio via ``aplay``/``pw-play``/``paplay``."""

    def __init__(self, binary: str | None = None, logger: logging.Logger | None = None) -> None:
        env_override = os.environ.get("PULSE_ASSISTANT_AUDIO_PLAYER")
        if binary is None and env_override:
            binary = env_override
        self.binary = binary or "auto"
        self._proc: Process | None = None
        self._logger = logger or logging.getLogger(__name__)

    async def start(self, rate: int, width: int, channels: int) -> None:
        await self.stop()
        player_env, sink = _player_env_with_sink()
        _send_wake_tone(player_env, sink)
        player = self._resolve_player()
        try:
            cmd = self._build_command(player, rate, width, channels)
        except ValueError as exc:
            self._logger.warning(
                "Player %s cannot handle width=%s (%s); falling back to aplay",
                player,
                width,
                exc,
            )
            player = "aplay"
            cmd = _build_aplay_command(rate, width, channels)
        sink_detail = f" (sink={sink})" if sink else ""
        self._logger.debug("Starting playback (%s): %s%s", player, " ".join(cmd), sink_detail)
        self._proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=player_env,
        )

    async def write(self, chunk: bytes) -> None:
        if not self._proc or not self._proc.stdin:
            raise RuntimeError("Playback is not active")
        try:
            self._proc.stdin.write(chunk)
            await self._proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError) as exc:
            stderr = await self._drain_stderr()
            await self.stop()
            detail = f" ({stderr})" if stderr else ""
            raise RuntimeError(f"Playback process exited unexpectedly{detail}") from exc

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

    async def _drain_stderr(self) -> str:
        if not self._proc or not self._proc.stderr:
            return ""
        try:
            data = await asyncio.wait_for(self._proc.stderr.read(), timeout=0.05)
        except (TimeoutError, RuntimeError):
            return ""
        return data.decode("utf-8", errors="ignore").strip()

    def _resolve_player(self) -> str:
        return _determine_player(self.binary, self._logger)

    @staticmethod
    def _build_command(player: str, rate: int, width: int, channels: int) -> list[str]:
        return _build_command_for_player(player, rate, width, channels)


def _alsa_format(width: int) -> str:
    return {
        1: "U8",
        2: "S16_LE",
        3: "S24_LE",
        4: "S32_LE",
    }.get(width, "S16_LE")


def _supported_player(binary: str) -> bool:
    if os.path.isabs(binary):
        return os.access(binary, os.X_OK)
    return shutil.which(binary) is not None


def _player_candidates() -> list[str]:
    return ["pw-play", "paplay", "aplay"]


def _pw_format(width: int) -> str | None:
    return {
        1: "s8",
        2: "s16",
        4: "s32",
    }.get(width)


def _paplay_format(width: int) -> str:
    return {
        1: "s8",
        2: "s16le",
        3: "s24le",
        4: "s32le",
    }.get(width, "s16le")


def _build_pw_play_command(rate: int, width: int, channels: int) -> list[str]:
    fmt = _pw_format(width)
    if not fmt:
        raise ValueError(f"pw-play has no format for width={width}")
    return [
        "pw-play",
        "--raw",
        "--rate",
        str(rate),
        "--channels",
        str(channels),
        "--format",
        fmt,
        "-",
    ]


def _build_paplay_command(rate: int, width: int, channels: int) -> list[str]:
    fmt = _paplay_format(width)
    return [
        "paplay",
        "--raw",
        "--rate",
        str(rate),
        "--channels",
        str(channels),
        f"--format={fmt}",
        "-",
    ]


def _build_aplay_command(rate: int, width: int, channels: int) -> list[str]:
    fmt = _alsa_format(width)
    return [
        "aplay",
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


def _build_command_for_player(player: str, rate: int, width: int, channels: int) -> list[str]:
    if player == "pw-play":
        return _build_pw_play_command(rate, width, channels)
    if player == "paplay":
        return _build_paplay_command(rate, width, channels)
    return _build_aplay_command(rate, width, channels)


def _determine_player(preferred: str, logger: logging.Logger) -> str:
    if preferred != "auto":
        if _supported_player(preferred):
            return preferred
        logger.warning("Requested audio player '%s' not found; falling back to auto-detection", preferred)
    for candidate in _player_candidates():
        if _supported_player(candidate):
            return candidate
    return "aplay"


def _player_env_with_sink() -> tuple[dict[str, str], str | None]:
    env = os.environ.copy()
    sink = pulse_audio.find_audio_sink()
    if sink:
        env["PULSE_SINK"] = sink
    return env, sink


def _send_wake_tone(env: dict[str, str], sink: str | None) -> None:
    pw_play = shutil.which("pw-play")
    if not pw_play or not sink:
        return
    hiss = os.urandom(1600)  # ~100ms mono s16 at 8kHz
    try:
        import subprocess

        subprocess.run(
            [pw_play, "--raw", "--rate", "8000", "--channels", "1", "--format", "s16", "-"],
            input=hiss,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
        )
    except Exception:
        pass
