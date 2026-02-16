"""Tests for scheduler module."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from pulse.assistant.config import HomeAssistantConfig
from pulse.assistant.scheduler import AssistantScheduler, _format_duration

pytestmark = pytest.mark.anyio


def _make_ha_config(**overrides):
    defaults = dict(
        base_url="http://ha.local:8123",
        token="test_token",
        verify_ssl=True,
        assist_pipeline=None,
        wake_endpoint=None,
        stt_endpoint=None,
        tts_endpoint=None,
        timer_entity=None,
        reminder_service=None,
        presence_entity=None,
    )
    defaults.update(overrides)
    return HomeAssistantConfig(**defaults)


# ---------------------------------------------------------------------------
# _format_duration
# ---------------------------------------------------------------------------


class TestFormatDuration:
    def test_zero(self):
        assert _format_duration(0) == "00:00:00"

    def test_seconds_only(self):
        assert _format_duration(45) == "00:00:45"

    def test_minutes_and_seconds(self):
        assert _format_duration(125) == "00:02:05"

    def test_hours_minutes_seconds(self):
        assert _format_duration(3661) == "01:01:01"

    def test_large_duration(self):
        assert _format_duration(86400) == "24:00:00"


# ---------------------------------------------------------------------------
# AssistantScheduler
# ---------------------------------------------------------------------------


class TestAssistantScheduler:
    def _make_scheduler(self, ha_client=None, timer_entity=None, reminder_service=None):
        ha_config = _make_ha_config(timer_entity=timer_entity, reminder_service=reminder_service)
        notifier = AsyncMock()
        return AssistantScheduler(ha_client=ha_client, ha_config=ha_config, notifier=notifier), notifier

    async def test_start_timer_local_fallback(self):
        scheduler, notifier = self._make_scheduler()
        await scheduler.start_timer(0.01, label="Test timer")
        # Wait for the local timer to fire
        await asyncio.sleep(0.05)
        notifier.assert_awaited_once_with("Test timer")

    async def test_start_timer_local_default_message(self):
        scheduler, notifier = self._make_scheduler()
        await scheduler.start_timer(0.01)
        await asyncio.sleep(0.05)
        notifier.assert_awaited_once_with("Timer complete")

    async def test_start_timer_zero_duration_raises(self):
        scheduler, _ = self._make_scheduler()
        with pytest.raises(ValueError, match="positive"):
            await scheduler.start_timer(0)

    async def test_start_timer_negative_duration_raises(self):
        scheduler, _ = self._make_scheduler()
        with pytest.raises(ValueError, match="positive"):
            await scheduler.start_timer(-5)

    async def test_start_timer_via_ha(self):
        ha_client = AsyncMock()
        scheduler, _ = self._make_scheduler(ha_client=ha_client, timer_entity="timer.kitchen")
        await scheduler.start_timer(300, label="Cooking")
        ha_client.call_service.assert_awaited_once_with(
            "timer", "start", {"entity_id": "timer.kitchen", "duration": "00:05:00"}
        )

    async def test_schedule_reminder_local_fallback(self):
        scheduler, notifier = self._make_scheduler()
        when = datetime.now(UTC) + timedelta(seconds=0.01)
        await scheduler.schedule_reminder(when, "Don't forget!")
        await asyncio.sleep(0.05)
        notifier.assert_awaited_once_with("Don't forget!")

    async def test_schedule_reminder_adds_utc_if_naive(self):
        scheduler, notifier = self._make_scheduler()
        when = datetime.now(UTC).replace(tzinfo=None) + timedelta(seconds=0.01)
        await scheduler.schedule_reminder(when, "Naive datetime")
        await asyncio.sleep(0.05)
        notifier.assert_awaited_once_with("Naive datetime")

    async def test_schedule_reminder_via_ha(self):
        ha_client = AsyncMock()
        scheduler, _ = self._make_scheduler(ha_client=ha_client, reminder_service="calendar.create_event")
        when = datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC)
        await scheduler.schedule_reminder(when, "Meeting")
        ha_client.call_service.assert_awaited_once()
        call_args = ha_client.call_service.call_args
        assert call_args[0][0] == "calendar"
        assert call_args[0][1] == "create_event"
        assert call_args[0][2]["message"] == "Meeting"

    async def test_schedule_reminder_invalid_service_format(self):
        ha_client = AsyncMock()
        scheduler, _ = self._make_scheduler(ha_client=ha_client, reminder_service="no_dot_here")
        when = datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC)
        with pytest.raises(ValueError, match="domain.service"):
            await scheduler.schedule_reminder(when, "Bad format")

    async def test_task_tracking_cleanup(self):
        scheduler, _ = self._make_scheduler()
        await scheduler.start_timer(0.01)
        assert len(scheduler._tasks) == 1
        await asyncio.sleep(0.05)
        assert len(scheduler._tasks) == 0

    def test_ha_client_ready_requires_all(self):
        scheduler, _ = self._make_scheduler()
        assert scheduler._ha_client_ready() is False

        ha_client = AsyncMock()
        scheduler, _ = self._make_scheduler(ha_client=ha_client)
        assert scheduler._ha_client_ready() is True
