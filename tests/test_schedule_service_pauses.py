from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pulse.assistant.schedule_service import ScheduleService


@pytest.fixture
def fixed_now(monkeypatch):
    ts = datetime(2025, 1, 6, 7, 30, tzinfo=UTC)  # Monday
    monkeypatch.setattr("pulse.assistant.schedule_service._now", lambda: ts)
    return ts


@pytest.mark.anyio
async def test_recurring_alarm_respects_pause_day(tmp_path: Path, fixed_now: datetime) -> None:
    svc = ScheduleService(
        storage_path=tmp_path / "sched.json",
        hostname="test",
        skip_dates=set(),
        skip_weekdays=set(),
    )
    await svc.create_alarm(time_of_day="07:45", days=[fixed_now.weekday()])
    # Initially scheduled for the same day
    alarm = next(ev for ev in svc._events.values())
    assert alarm.next_fire_dt().date().isoformat() == fixed_now.date().isoformat()

    # Pause that day -> should reschedule to the next week
    await svc.set_ui_pause_date(fixed_now.date().isoformat(), True)
    alarm = next(ev for ev in svc._events.values())
    assert alarm.next_fire_dt().date().isoformat() == (fixed_now.date() + timedelta(days=7)).isoformat()


@pytest.mark.anyio
async def test_single_shot_alarm_ignores_skip_lists(tmp_path: Path, fixed_now: datetime) -> None:
    svc = ScheduleService(
        storage_path=tmp_path / "sched.json",
        hostname="test",
        skip_dates={fixed_now.date().isoformat()},
        skip_weekdays={fixed_now.weekday()},
    )
    await svc.create_alarm(time_of_day="07:45", days=None, single_shot=True)
    alarm = next(ev for ev in svc._events.values())
    # Single-shot alarms should not be forced to skip, only move to next day if already past
    assert alarm.next_fire_dt().date().isoformat() == fixed_now.date().isoformat()
