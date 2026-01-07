"""
Shared utility functions for parsing and data manipulation

Provides common helpers for:
- String parsing: Environment variable conversion (parse_bool, parse_int, parse_float, split_csv)
- Entity ID sanitization: Converting hostnames to Home Assistant-safe entity IDs
- Async utilities: Timeout wrappers, byte chunking
- Data coercion: Safe type conversion with fallback defaults

These utilities are used throughout PulseOS for configuration parsing and data handling.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Iterable
from typing import Any


def sanitize_hostname_for_entity_id(hostname: str) -> str:
    """Convert hostnames to Home Assistant–safe entity IDs."""
    return hostname.lower().replace("-", "_").replace(".", "_")


def parse_bool(value: str | None, default: bool = False) -> bool:
    """Interpret env-style booleans."""
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def parse_int(value: str | None, default: int) -> int:
    """Best-effort int parser with fallback."""
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def parse_float(value: str | None, default: float) -> float:
    """Best-effort float parser with fallback."""
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def split_csv(value: str | None) -> list[str]:
    """Split comma-separated strings into trimmed tokens."""
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


async def await_with_timeout(awaitable: Awaitable[Any], timeout: float | None) -> Any:
    """Await a coroutine with an optional timeout."""
    if timeout is None:
        return await awaitable
    return await asyncio.wait_for(awaitable, timeout=timeout)


def chunk_bytes(data: bytes, size: int) -> Iterable[bytes]:
    """Yield fixed-size chunks from a byte buffer."""
    if size <= 0:
        raise ValueError("Chunk size must be positive")
    for start in range(0, len(data), size):
        end = min(start + size, len(data))
        yield data[start:end]
