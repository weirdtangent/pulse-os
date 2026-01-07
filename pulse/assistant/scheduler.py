"""
Timer and reminder scheduling with Home Assistant fallback

Provides timer/reminder functionality that can route to Home Assistant entities
or use local asyncio timers as fallback.

Features:
- Start timers with duration and optional label
- Schedule reminders for specific datetime
- Home Assistant integration: Uses timer.start and reminder services when available
- Local fallback: asyncio-based timers when HA unavailable
- Automatic routing: Checks HA client availability before choosing strategy

The scheduler maintains a set of running tasks and notifies via callback when
timers/reminders fire.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from .config import HomeAssistantConfig
from .home_assistant import HomeAssistantClient

NotifyCallback = Callable[[str], Awaitable[None]]


@dataclass
class AssistantScheduler:
    ha_client: HomeAssistantClient | None
    ha_config: HomeAssistantConfig
    notifier: NotifyCallback
    _tasks: set[asyncio.Task] = field(default_factory=set, init=False)

    async def start_timer(self, duration_seconds: float, label: str | None = None) -> None:
        duration_seconds = max(0.0, float(duration_seconds))
        if duration_seconds <= 0:
            raise ValueError("Timer duration must be positive")
        if self._ha_client_ready() and self.ha_config.timer_entity:
            payload = {
                "entity_id": self.ha_config.timer_entity,
                "duration": _format_duration(duration_seconds),
            }
            await self.ha_client.call_service("timer", "start", payload)
            return
        message = label or "Timer complete"
        task = asyncio.create_task(self._local_timer(duration_seconds, message))
        self._track(task)

    async def schedule_reminder(self, when: datetime, message: str) -> None:
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        if self._ha_client_ready() and self.ha_config.reminder_service:
            domain, _, service = self.ha_config.reminder_service.partition(".")
            if not service:
                raise ValueError("HOME_ASSISTANT_REMINDER_SERVICE must be in 'domain.service' format")
            payload = {"message": message, "when": when.isoformat()}
            await self.ha_client.call_service(domain, service, payload)
            return
        delay = max(0.0, (when - datetime.now(UTC)).total_seconds())
        task = asyncio.create_task(self._local_timer(delay, message))
        self._track(task)

    async def _local_timer(self, delay: float, message: str) -> None:
        await asyncio.sleep(delay)
        await self.notifier(message)

    def _track(self, task: asyncio.Task) -> None:
        self._tasks.add(task)

        def _cleanup(_task: asyncio.Task) -> None:
            self._tasks.discard(_task)

        task.add_done_callback(_cleanup)

    def _ha_client_ready(self) -> bool:
        return bool(self.ha_client and self.ha_config.base_url and self.ha_config.token)


def _format_duration(duration_seconds: float) -> str:
    duration = timedelta(seconds=duration_seconds)
    total_seconds = int(duration.total_seconds())
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
