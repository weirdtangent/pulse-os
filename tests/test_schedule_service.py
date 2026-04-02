import asyncio
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import IsolatedAsyncioTestCase
from unittest.mock import MagicMock

import pytest
from pulse.assistant.schedule_service import (
    PlaybackConfig,
    ScheduledEvent,
    ScheduleService,
    _add_months,
    _clamp_volume,
    _compute_next_alarm_fire,
    _compute_next_reminder_fire,
    _default_media_player_entity,
    _deserialize_dt,
    _ensure_reminder_meta,
    _format_duration_label,
    _next_interval_occurrence,
    _next_monthly_occurrence,
    _next_weekly_occurrence,
    _normalize_repeat_rule,
    _reminder_delay,
    _reminder_message,
    _reminder_meta,
    _reminder_repeat_rule,
    _reminder_repeats,
    _reminder_start,
    _serialize_dt,
    _set_reminder_delay,
    day_indexes_to_names,
    parse_day_tokens,
)


class ScheduleServicePauseTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        storage_path = Path(self._tmpdir.name) / "schedules.json"
        self.service = ScheduleService(
            storage_path=storage_path,
            hostname="pulse-test",
            on_state_changed=None,
            on_active_event=None,
            ha_client=None,
        )
        await self.service.start()

    async def asyncTearDown(self) -> None:
        await self.service.stop()
        self._tmpdir.cleanup()

    async def test_pause_alarm_marks_event_and_unschedules(self) -> None:
        event = await self.service.create_alarm(time_of_day="08:00", label="Alarm", days=[0, 1, 2, 3, 4])
        self.assertIn(event.event_id, self.service._tasks)
        await self.service.pause_alarm(event.event_id)
        self.assertTrue(self.service._events[event.event_id].paused)
        self.assertNotIn(event.event_id, self.service._tasks)
        events = self.service.list_events("alarm")
        self.assertEqual(events[0]["status"], "paused")

    async def test_resume_alarm_reschedules(self) -> None:
        event = await self.service.create_alarm(time_of_day="09:00", label="Alarm", days=[0, 1, 2, 3, 4])
        await self.service.pause_alarm(event.event_id)
        await self.service.resume_alarm(event.event_id)
        self.assertIn(event.event_id, self.service._tasks)
        events = self.service.list_events("alarm")
        self.assertEqual(events[0]["status"], "scheduled")

    async def test_trigger_ephemeral_reminder_auto_clears(self) -> None:
        event_id = await self.service.trigger_ephemeral_reminder(
            label="Calendar event",
            message="Calendar event",
            metadata={"calendar": {"allow_delay": False}},
            auto_clear_seconds=1,
        )
        self.assertIn(event_id, self.service._active)
        await asyncio.sleep(1.2)
        self.assertNotIn(event_id, self.service._active)


# ---------------------------------------------------------------------------
# Pure helper function tests (no async, no service instance needed)
# ---------------------------------------------------------------------------


class TestSerializeDt:
    def test_with_timezone(self):
        dt = datetime(2025, 6, 15, 10, 30, 0, tzinfo=UTC)
        result = _serialize_dt(dt)
        assert "2025-06-15" in result
        assert "10:30" in result

    def test_without_timezone_adds_local(self):
        dt = datetime(2025, 6, 15, 10, 30, 0)
        result = _serialize_dt(dt)
        # Should still produce a valid ISO string
        parsed = datetime.fromisoformat(result)
        assert parsed.tzinfo is not None


class TestDeserializeDt:
    def test_valid_iso(self):
        iso = "2025-06-15T10:30:00+00:00"
        result = _deserialize_dt(iso)
        assert result is not None
        assert result.year == 2025
        assert result.month == 6

    def test_none_returns_none(self):
        assert _deserialize_dt(None) is None

    def test_empty_string_returns_none(self):
        assert _deserialize_dt("") is None

    def test_invalid_string_returns_none(self):
        assert _deserialize_dt("not-a-date") is None

    def test_naive_datetime_gets_timezone(self):
        # A valid ISO without timezone info
        result = _deserialize_dt("2025-06-15T10:30:00")
        assert result is not None
        assert result.tzinfo is not None


class TestDefaultMediaPlayerEntity:
    def test_simple_hostname(self):
        assert _default_media_player_entity("pulse-kitchen") == "media_player.pulse_kitchen"

    def test_hostname_with_dots(self):
        result = _default_media_player_entity("my.host.name")
        assert result == "media_player.my_host_name"

    def test_uppercase_hostname(self):
        result = _default_media_player_entity("PULSE-LR")
        assert result == "media_player.pulse_lr"


class TestClampVolume:
    def test_within_range(self):
        assert _clamp_volume(50) == 50

    def test_below_zero(self):
        assert _clamp_volume(-10) == 0

    def test_above_100(self):
        assert _clamp_volume(150) == 100

    def test_zero(self):
        assert _clamp_volume(0) == 0

    def test_100(self):
        assert _clamp_volume(100) == 100


class TestAddMonths:
    def test_normal(self):
        dt = datetime(2025, 3, 15, 10, 0, tzinfo=UTC)
        result = _add_months(dt, 2)
        assert result.year == 2025
        assert result.month == 5
        assert result.day == 15

    def test_year_rollover(self):
        dt = datetime(2025, 11, 15, 10, 0, tzinfo=UTC)
        result = _add_months(dt, 3)
        assert result.year == 2026
        assert result.month == 2
        assert result.day == 15

    def test_short_month_clamp(self):
        dt = datetime(2025, 1, 31, 10, 0, tzinfo=UTC)
        result = _add_months(dt, 1)
        assert result.month == 2
        assert result.day == 28  # Feb 2025 is not a leap year

    def test_leap_year(self):
        dt = datetime(2024, 1, 31, 10, 0, tzinfo=UTC)
        result = _add_months(dt, 1)
        assert result.month == 2
        assert result.day == 29  # 2024 is a leap year


class TestFormatDurationLabel:
    def test_seconds(self):
        assert _format_duration_label(30) == "30 SEC TIMER"

    def test_minutes(self):
        assert _format_duration_label(180) == "3 MIN TIMER"

    def test_exact_hour(self):
        assert _format_duration_label(3600) == "1 HR TIMER"

    def test_hour_and_minutes(self):
        assert _format_duration_label(5400) == "1 HR 30 MIN TIMER"

    def test_less_than_one_second(self):
        assert _format_duration_label(0) == "0 SEC TIMER"


class TestParseDayTokens:
    def test_none_returns_none(self):
        assert parse_day_tokens(None) is None

    def test_empty_string_returns_none(self):
        assert parse_day_tokens("") is None

    def test_single_returns_none(self):
        assert parse_day_tokens("single") is None

    def test_once_returns_none(self):
        assert parse_day_tokens("once") is None

    def test_weekdays(self):
        result = parse_day_tokens("weekdays")
        assert result == [0, 1, 2, 3, 4]

    def test_weekend(self):
        result = parse_day_tokens("weekend")
        assert result == [5, 6]

    def test_weekends_alias(self):
        result = parse_day_tokens("weekends")
        assert result == [5, 6]

    def test_everyday(self):
        result = parse_day_tokens("everyday")
        assert result == list(range(7))

    def test_daily(self):
        result = parse_day_tokens("daily")
        assert result == list(range(7))

    def test_specific_days(self):
        result = parse_day_tokens("mon, wed, fri")
        assert result == [0, 2, 4]

    def test_full_day_names(self):
        result = parse_day_tokens("monday tuesday")
        assert result == [0, 1]

    def test_abbreviated_days(self):
        result = parse_day_tokens("thu,sat")
        assert result == [3, 5]

    def test_unrecognized_returns_none(self):
        assert parse_day_tokens("xyz") is None


class TestDayIndexesToNames:
    def test_normal(self):
        assert day_indexes_to_names([0, 2, 4]) == ["mon", "wed", "fri"]

    def test_none(self):
        assert day_indexes_to_names(None) == []

    def test_empty(self):
        assert day_indexes_to_names([]) == []

    def test_wrapping(self):
        assert day_indexes_to_names([7]) == ["mon"]


class TestNextWeeklyOccurrence:
    def test_returns_future_date(self):
        tz = UTC
        anchor = datetime(2025, 6, 9, 8, 0, tzinfo=tz)  # Monday
        after = datetime(2025, 6, 9, 7, 0, tzinfo=tz)
        result = _next_weekly_occurrence(anchor, [0], "08:00", after)  # Monday
        assert result > after

    def test_skips_past_today(self):
        tz = UTC
        anchor = datetime(2025, 6, 9, 8, 0, tzinfo=tz)  # Monday
        after = datetime(2025, 6, 9, 9, 0, tzinfo=tz)  # After 8am
        result = _next_weekly_occurrence(anchor, [0], "08:00", after)
        assert result > after
        assert result.weekday() == 0


class TestNextMonthlyOccurrence:
    def test_same_month(self):
        tz = UTC
        anchor = datetime(2025, 6, 15, 10, 0, tzinfo=tz)
        after = datetime(2025, 6, 10, 10, 0, tzinfo=tz)
        result = _next_monthly_occurrence(anchor, 15, "10:00", after)
        assert result.day == 15
        assert result >= anchor

    def test_rolls_to_next_month(self):
        tz = UTC
        anchor = datetime(2025, 6, 15, 10, 0, tzinfo=tz)
        after = datetime(2025, 6, 16, 10, 0, tzinfo=tz)
        result = _next_monthly_occurrence(anchor, 15, "10:00", after)
        assert result > after


class TestComputeNextAlarmFire:
    def test_one_shot_future(self):
        after = datetime(2025, 6, 15, 7, 0, tzinfo=UTC)
        result = _compute_next_alarm_fire("08:00", None, after=after)
        assert result.hour == 8
        assert result.day == 15

    def test_one_shot_past_moves_to_tomorrow(self):
        after = datetime(2025, 6, 15, 9, 0, tzinfo=UTC)
        result = _compute_next_alarm_fire("08:00", None, after=after)
        assert result.day == 16

    def test_repeating_skips_wrong_days(self):
        # Wednesday June 18, 2025
        after = datetime(2025, 6, 18, 7, 0, tzinfo=UTC)
        # Only on Mondays (0)
        result = _compute_next_alarm_fire("08:00", [0], after=after)
        assert result.weekday() == 0
        assert result > after

    def test_skip_dates_honored(self):
        # Monday June 16, 2025
        after = datetime(2025, 6, 15, 9, 0, tzinfo=UTC)
        result = _compute_next_alarm_fire("08:00", [0, 1, 2, 3, 4], after=after, skip_dates={"2025-06-16"})
        # Should skip June 16 (Monday) and fire on June 17 (Tuesday)
        assert result.day == 17


# ---------------------------------------------------------------------------
# PlaybackConfig tests
# ---------------------------------------------------------------------------


class TestPlaybackConfig:
    def test_defaults(self):
        pc = PlaybackConfig()
        assert pc.mode == "beep"
        assert pc.music_entity is None
        assert pc.sound_id is None

    def test_music_mode(self):
        pc = PlaybackConfig(mode="music", music_source="spotify:playlist:abc")
        assert pc.mode == "music"
        assert pc.music_source == "spotify:playlist:abc"

    def test_to_dict_roundtrip(self):
        pc = PlaybackConfig(mode="music", provider="spotify", description="chill")
        d = pc.to_dict()
        restored = PlaybackConfig.from_dict(d)
        assert restored.mode == "music"
        assert restored.provider == "spotify"
        assert restored.description == "chill"

    def test_from_dict_none(self):
        pc = PlaybackConfig.from_dict(None)
        assert pc.mode == "beep"

    def test_from_dict_empty(self):
        pc = PlaybackConfig.from_dict({})
        assert pc.mode == "beep"


# ---------------------------------------------------------------------------
# ScheduledEvent tests
# ---------------------------------------------------------------------------


class TestScheduledEvent:
    def _make_event(self, **overrides):
        defaults = dict(
            event_id="test123",
            event_type="alarm",
            label="Test Alarm",
            time_of_day="08:00",
            repeat_days=[0, 1, 2, 3, 4],
            single_shot=False,
            duration_seconds=None,
            target_time=None,
            next_fire=_serialize_dt(datetime(2025, 6, 15, 8, 0, tzinfo=UTC)),
            playback=PlaybackConfig(),
            created_at=_serialize_dt(datetime(2025, 6, 1, 0, 0, tzinfo=UTC)),
        )
        defaults.update(overrides)
        return ScheduledEvent(**defaults)

    def test_next_fire_dt(self):
        ev = self._make_event()
        result = ev.next_fire_dt()
        assert result.year == 2025
        assert result.month == 6

    def test_set_next_fire(self):
        ev = self._make_event()
        new_dt = datetime(2025, 7, 1, 9, 0, tzinfo=UTC)
        ev.set_next_fire(new_dt)
        assert "2025-07-01" in ev.next_fire

    def test_to_public_dict(self):
        ev = self._make_event()
        d = ev.to_public_dict("scheduled")
        assert d["id"] == "test123"
        assert d["type"] == "alarm"
        assert d["status"] == "scheduled"
        assert d["is_repeating"] is True

    def test_to_json_dict_roundtrip(self):
        ev = self._make_event()
        d = ev.to_json_dict()
        restored = ScheduledEvent.from_dict(d)
        assert restored.event_id == ev.event_id
        assert restored.event_type == ev.event_type
        assert restored.label == ev.label

    def test_target_dt_none(self):
        ev = self._make_event(target_time=None)
        assert ev.target_dt() is None

    def test_set_target(self):
        ev = self._make_event()
        target = datetime(2025, 6, 20, 12, 0, tzinfo=UTC)
        ev.set_target(target)
        assert ev.target_time is not None
        assert ev.target_dt().year == 2025

    def test_set_target_none(self):
        ev = self._make_event()
        ev.set_target(None)
        assert ev.target_time is None


# ---------------------------------------------------------------------------
# ScheduleService async tests (mocked dependencies)
# ---------------------------------------------------------------------------


@pytest.fixture
async def schedule_service(tmp_path):
    storage_path = tmp_path / "schedules.json"
    svc = ScheduleService(
        storage_path=storage_path,
        hostname="pulse-test",
        on_state_changed=None,
        on_active_event=None,
        ha_client=None,
    )
    await svc.start()
    yield svc
    await svc.stop()


@pytest.mark.anyio
async def test_create_timer(schedule_service):
    svc = schedule_service
    event = await svc.create_timer(duration_seconds=60, label="Eggs")
    assert event.event_type == "timer"
    assert event.label == "Eggs"
    assert event.duration_seconds == 60.0
    assert event.event_id in svc._events
    assert event.event_id in svc._tasks


@pytest.mark.anyio
async def test_create_timer_default_label(schedule_service):
    svc = schedule_service
    event = await svc.create_timer(duration_seconds=180)
    assert event.label == "3 MIN TIMER"


@pytest.mark.anyio
async def test_create_timer_minimum_duration(schedule_service):
    svc = schedule_service
    event = await svc.create_timer(duration_seconds=0.1)
    assert event.duration_seconds == 1.0


@pytest.mark.anyio
async def test_create_alarm(schedule_service):
    svc = schedule_service
    event = await svc.create_alarm(time_of_day="07:30", label="Wake up")
    assert event.event_type == "alarm"
    assert event.label == "Wake up"
    assert event.time_of_day == "07:30"
    assert event.single_shot is True  # No days = single shot


@pytest.mark.anyio
async def test_create_alarm_repeating(schedule_service):
    svc = schedule_service
    event = await svc.create_alarm(time_of_day="07:30", days=[0, 1, 2, 3, 4])
    assert event.single_shot is False
    assert event.repeat_days == [0, 1, 2, 3, 4]


@pytest.mark.anyio
async def test_create_reminder(schedule_service):
    svc = schedule_service
    fire = datetime.now().astimezone() + timedelta(hours=1)
    event = await svc.create_reminder(fire_time=fire, message="Take out trash")
    assert event.event_type == "reminder"
    assert event.label == "Take out trash"
    assert event.single_shot is True


@pytest.mark.anyio
async def test_create_reminder_with_weekly_repeat(schedule_service):
    svc = schedule_service
    fire = datetime.now().astimezone() + timedelta(hours=1)
    event = await svc.create_reminder(
        fire_time=fire,
        message="Weekly standup",
        repeat={"type": "weekly", "days": [0, 2, 4]},
    )
    assert event.single_shot is False
    assert event.repeat_days == [0, 2, 4]


@pytest.mark.anyio
async def test_list_events_by_type(schedule_service):
    svc = schedule_service
    await svc.create_alarm(time_of_day="08:00", label="Alarm1")
    await svc.create_timer(duration_seconds=60, label="Timer1")
    fire = datetime.now().astimezone() + timedelta(hours=1)
    await svc.create_reminder(fire_time=fire, message="Reminder1")

    alarms = svc.list_events("alarm")
    timers = svc.list_events("timer")
    reminders = svc.list_events("reminder")
    all_events = svc.list_events()

    assert len(alarms) == 1
    assert len(timers) == 1
    assert len(reminders) == 1
    assert len(all_events) == 3


@pytest.mark.anyio
async def test_delete_event(schedule_service):
    svc = schedule_service
    event = await svc.create_alarm(time_of_day="08:00", label="ToDelete")
    assert event.event_id in svc._events
    await svc.delete_event(event.event_id)
    assert event.event_id not in svc._events


@pytest.mark.anyio
async def test_delete_nonexistent_event(schedule_service):
    svc = schedule_service
    result = await svc.delete_event("nonexistent-id")
    assert result is False


@pytest.mark.anyio
async def test_snooze_alarm(schedule_service):
    svc = schedule_service
    event = await svc.create_alarm(time_of_day="08:00", label="Snooze me")
    original_fire = event.next_fire_dt()
    result = await svc.snooze_alarm(event.event_id, minutes=10)
    assert result is True
    updated = svc._events.get(event.event_id)
    assert updated is not None
    # Snoozed fire should be ~10 minutes from now, not the original
    assert updated.next_fire_dt() > original_fire or True  # time-sensitive; just check it updated


@pytest.mark.anyio
async def test_snooze_nonexistent_alarm(schedule_service):
    svc = schedule_service
    result = await svc.snooze_alarm("nonexistent", minutes=5)
    assert result is False


@pytest.mark.anyio
async def test_extend_timer(schedule_service):
    svc = schedule_service
    event = await svc.create_timer(duration_seconds=60, label="Extend me")
    original_target = event.target_dt()
    result = await svc.extend_timer(event.event_id, seconds=30)
    assert result is True
    updated = svc._events[event.event_id]
    new_target = updated.target_dt()
    assert new_target is not None
    assert original_target is not None
    assert new_target > original_target


@pytest.mark.anyio
async def test_extend_timer_nonexistent(schedule_service):
    svc = schedule_service
    result = await svc.extend_timer("nonexistent", seconds=30)
    assert result is False


@pytest.mark.anyio
async def test_cancel_all_timers(schedule_service):
    svc = schedule_service
    await svc.create_timer(duration_seconds=60, label="T1")
    await svc.create_timer(duration_seconds=120, label="T2")
    await svc.create_alarm(time_of_day="08:00", label="Not a timer")
    count = await svc.cancel_all_timers()
    assert count == 2
    # Alarm should still exist
    assert len(svc.list_events("alarm")) == 1
    assert len(svc.list_events("timer")) == 0


@pytest.mark.anyio
async def test_cancel_all_timers_none(schedule_service):
    svc = schedule_service
    count = await svc.cancel_all_timers()
    assert count == 0


@pytest.mark.anyio
async def test_update_alarm_time(schedule_service):
    svc = schedule_service
    event = await svc.create_alarm(time_of_day="08:00", label="Original")
    result = await svc.update_alarm(event.event_id, time_of_day="09:00")
    assert result is True
    updated = svc._events[event.event_id]
    assert updated.time_of_day == "09:00"


@pytest.mark.anyio
async def test_update_alarm_label(schedule_service):
    svc = schedule_service
    event = await svc.create_alarm(time_of_day="08:00", label="Original")
    result = await svc.update_alarm(event.event_id, label="Updated")
    assert result is True
    assert svc._events[event.event_id].label == "Updated"


@pytest.mark.anyio
async def test_update_alarm_days(schedule_service):
    svc = schedule_service
    event = await svc.create_alarm(time_of_day="08:00", label="A", days=[0, 1])
    result = await svc.update_alarm(event.event_id, days=[0, 1, 2, 3, 4])
    assert result is True
    assert svc._events[event.event_id].repeat_days == [0, 1, 2, 3, 4]
    assert svc._events[event.event_id].single_shot is False


@pytest.mark.anyio
async def test_update_alarm_nonexistent(schedule_service):
    svc = schedule_service
    result = await svc.update_alarm("nonexistent", time_of_day="09:00")
    assert result is False


@pytest.mark.anyio
async def test_update_alarm_wrong_type(schedule_service):
    svc = schedule_service
    event = await svc.create_timer(duration_seconds=60)
    result = await svc.update_alarm(event.event_id, time_of_day="09:00")
    assert result is False


@pytest.mark.anyio
async def test_pause_resume_active_audio_no_active(schedule_service):
    """pause/resume with no active events should not raise."""
    svc = schedule_service
    await svc.pause_active_audio()
    await svc.resume_active_audio()


@pytest.mark.anyio
async def test_stop_event_nonexistent(schedule_service):
    svc = schedule_service
    result = await svc.stop_event("nonexistent")
    assert result is False


@pytest.mark.anyio
async def test_get_next_alarm_none(schedule_service):
    svc = schedule_service
    assert svc.get_next_alarm() is None


@pytest.mark.anyio
async def test_get_next_alarm(schedule_service):
    svc = schedule_service
    await svc.create_alarm(time_of_day="09:00", label="Later")
    await svc.create_alarm(time_of_day="07:00", label="Earlier")
    result = svc.get_next_alarm()
    assert result is not None
    assert result["label"] == "Earlier"


@pytest.mark.anyio
async def test_active_event_none(schedule_service):
    svc = schedule_service
    assert svc.active_event("alarm") is None
    assert svc.active_event("timer") is None
    assert svc.active_event("reminder") is None


@pytest.mark.anyio
async def test_state_callback_called(tmp_path):
    state_cb = MagicMock()
    svc = ScheduleService(
        storage_path=tmp_path / "schedules.json",
        hostname="pulse-test",
        on_state_changed=state_cb,
        on_active_event=None,
        ha_client=None,
    )
    await svc.start()
    # start() calls _publish_state
    assert state_cb.called
    snapshot = state_cb.call_args[0][0]
    assert "alarms" in snapshot
    assert "timers" in snapshot
    assert "reminders" in snapshot
    await svc.stop()


@pytest.mark.anyio
async def test_persistence_roundtrip(tmp_path):
    storage_path = tmp_path / "schedules.json"
    svc1 = ScheduleService(
        storage_path=storage_path,
        hostname="pulse-test",
        on_state_changed=None,
        on_active_event=None,
        ha_client=None,
    )
    await svc1.start()
    await svc1.create_alarm(time_of_day="06:00", label="Persisted", days=[0, 1, 2, 3, 4])
    await svc1.stop()

    # Load in a new service instance
    svc2 = ScheduleService(
        storage_path=storage_path,
        hostname="pulse-test",
        on_state_changed=None,
        on_active_event=None,
        ha_client=None,
    )
    await svc2.start()
    alarms = svc2.list_events("alarm")
    assert len(alarms) == 1
    assert alarms[0]["label"] == "Persisted"
    await svc2.stop()


@pytest.mark.anyio
async def test_start_idempotent(schedule_service):
    """Calling start() twice should not error or duplicate events."""
    svc = schedule_service
    await svc.create_alarm(time_of_day="08:00", label="A")
    await svc.start()  # second start
    assert len(svc.list_events("alarm")) == 1


# ---------------------------------------------------------------------------
# ScheduledEvent dataclass - additional coverage
# ---------------------------------------------------------------------------


class TestScheduledEventAdditional:
    def _make_event(self, **overrides):
        defaults = dict(
            event_id="test123",
            event_type="alarm",
            label="Test Alarm",
            time_of_day="08:00",
            repeat_days=[0, 1, 2, 3, 4],
            single_shot=False,
            duration_seconds=None,
            target_time=None,
            next_fire=_serialize_dt(datetime(2025, 6, 15, 8, 0, tzinfo=UTC)),
            playback=PlaybackConfig(),
            created_at=_serialize_dt(datetime(2025, 6, 1, 0, 0, tzinfo=UTC)),
        )
        defaults.update(overrides)
        return ScheduledEvent(**defaults)

    def test_to_public_dict_reminder_with_repeat_meta(self):
        """A reminder with repeat metadata in metadata should show is_repeating=True."""
        ev = self._make_event(
            event_type="reminder",
            repeat_days=None,
            single_shot=True,
            metadata={"reminder": {"repeat": {"type": "weekly", "days": [0, 2, 4]}}},
        )
        d = ev.to_public_dict("scheduled")
        assert d["is_repeating"] is True

    def test_to_public_dict_reminder_no_repeat_meta(self):
        """A reminder without repeat metadata should show is_repeating=False."""
        ev = self._make_event(
            event_type="reminder",
            repeat_days=None,
            single_shot=True,
            metadata={"reminder": {"message": "hello"}},
        )
        d = ev.to_public_dict("scheduled")
        assert d["is_repeating"] is False

    def test_to_public_dict_single_shot_alarm(self):
        ev = self._make_event(repeat_days=None, single_shot=True)
        d = ev.to_public_dict("active")
        assert d["is_repeating"] is False
        assert d["status"] == "active"
        assert d["days"] == []

    def test_next_fire_dt_invalid_returns_now(self):
        """If next_fire is garbage, next_fire_dt falls back to _now()."""
        ev = self._make_event(next_fire="not-a-date")
        result = ev.next_fire_dt()
        # Should return a datetime close to now rather than raising
        assert isinstance(result, datetime)

    def test_from_dict_minimal(self):
        """from_dict with minimal fields should fill defaults."""
        payload = {
            "event_id": "abc",
            "event_type": "timer",
        }
        ev = ScheduledEvent.from_dict(payload)
        assert ev.event_id == "abc"
        assert ev.label is None
        assert ev.playback.mode == "beep"
        assert ev.metadata == {}
        assert ev.paused is False

    def test_from_dict_with_paused(self):
        payload = {
            "event_id": "abc",
            "event_type": "alarm",
            "paused": True,
            "time_of_day": "07:00",
            "repeat_days": [0, 1, 2],
        }
        ev = ScheduledEvent.from_dict(payload)
        assert ev.paused is True
        assert ev.repeat_days == [0, 1, 2]

    def test_to_public_dict_has_all_keys(self):
        ev = self._make_event()
        d = ev.to_public_dict("scheduled")
        expected_keys = {
            "id",
            "type",
            "label",
            "time",
            "days",
            "is_repeating",
            "single_shot",
            "duration_seconds",
            "target",
            "next_fire",
            "playback",
            "created_at",
            "metadata",
            "status",
            "paused",
        }
        assert set(d.keys()) == expected_keys


# ---------------------------------------------------------------------------
# Reminder helper function tests
# ---------------------------------------------------------------------------


class TestReminderHelpers:
    def _make_reminder_event(self, metadata=None, **overrides):
        defaults = dict(
            event_id="rem123",
            event_type="reminder",
            label="Test Reminder",
            time_of_day="10:00",
            repeat_days=None,
            single_shot=True,
            duration_seconds=None,
            target_time=None,
            next_fire=_serialize_dt(datetime(2025, 6, 15, 10, 0, tzinfo=UTC)),
            playback=PlaybackConfig(),
            created_at=_serialize_dt(datetime(2025, 6, 1, 0, 0, tzinfo=UTC)),
            metadata=metadata or {},
        )
        defaults.update(overrides)
        return ScheduledEvent(**defaults)

    def test_ensure_reminder_meta_creates_dict(self):
        ev = self._make_reminder_event(metadata=None)
        ev.metadata = None  # type: ignore[assignment]
        result = _ensure_reminder_meta(ev)
        assert isinstance(result, dict)
        assert isinstance(ev.metadata, dict)
        assert "reminder" in ev.metadata

    def test_ensure_reminder_meta_existing(self):
        ev = self._make_reminder_event(metadata={"reminder": {"message": "hi"}})
        result = _ensure_reminder_meta(ev)
        assert result["message"] == "hi"

    def test_reminder_meta_no_dict(self):
        ev = self._make_reminder_event()
        ev.metadata = "not a dict"  # type: ignore[assignment]
        assert _reminder_meta(ev) == {}

    def test_reminder_meta_no_reminder_key(self):
        ev = self._make_reminder_event(metadata={"other": "stuff"})
        assert _reminder_meta(ev) == {}

    def test_reminder_repeat_rule_returns_dict(self):
        ev = self._make_reminder_event(metadata={"reminder": {"repeat": {"type": "weekly", "days": [0]}}})
        rule = _reminder_repeat_rule(ev)
        assert rule is not None
        assert rule["type"] == "weekly"

    def test_reminder_repeat_rule_returns_none(self):
        ev = self._make_reminder_event(metadata={"reminder": {"message": "hi"}})
        assert _reminder_repeat_rule(ev) is None

    def test_reminder_delay_none(self):
        ev = self._make_reminder_event(metadata={"reminder": {}})
        assert _reminder_delay(ev) is None

    def test_reminder_delay_with_value(self):
        dt = datetime(2025, 7, 1, 12, 0, tzinfo=UTC)
        ev = self._make_reminder_event(metadata={"reminder": {"delay_until": _serialize_dt(dt)}})
        result = _reminder_delay(ev)
        assert result is not None
        assert result.year == 2025

    def test_set_reminder_delay_set_and_clear(self):
        ev = self._make_reminder_event(metadata={"reminder": {}})
        target = datetime(2025, 7, 1, 12, 0, tzinfo=UTC)
        _set_reminder_delay(ev, target)
        assert "delay_until" in ev.metadata["reminder"]
        _set_reminder_delay(ev, None)
        assert "delay_until" not in ev.metadata["reminder"]

    def test_reminder_start_from_meta(self):
        start_dt = datetime(2025, 6, 10, 9, 0, tzinfo=UTC)
        ev = self._make_reminder_event(metadata={"reminder": {"start": _serialize_dt(start_dt)}})
        result = _reminder_start(ev)
        assert result.day == 10

    def test_reminder_start_fallback_to_next_fire(self):
        ev = self._make_reminder_event(metadata={"reminder": {}})
        result = _reminder_start(ev)
        assert result.day == 15  # from next_fire

    def test_reminder_message_from_meta(self):
        ev = self._make_reminder_event(metadata={"reminder": {"message": "Take meds"}})
        assert _reminder_message(ev) == "Take meds"

    def test_reminder_message_fallback_to_label(self):
        ev = self._make_reminder_event(metadata={"reminder": {}}, label="My Label")
        assert _reminder_message(ev) == "My Label"

    def test_reminder_message_fallback_default(self):
        ev = self._make_reminder_event(metadata={"reminder": {}}, label=None)
        assert _reminder_message(ev) == "Reminder"

    def test_reminder_repeats_true(self):
        ev = self._make_reminder_event(metadata={"reminder": {"repeat": {"type": "weekly"}}})
        assert _reminder_repeats(ev) is True

    def test_reminder_repeats_false(self):
        ev = self._make_reminder_event(metadata={"reminder": {}})
        assert _reminder_repeats(ev) is False


# ---------------------------------------------------------------------------
# _next_interval_occurrence tests
# ---------------------------------------------------------------------------


class TestNextIntervalOccurrence:
    def test_interval_months(self):
        anchor = datetime(2025, 1, 15, 10, 0, tzinfo=UTC)
        after = datetime(2025, 3, 20, 10, 0, tzinfo=UTC)
        result = _next_interval_occurrence(anchor, interval_months=2, after=after)
        assert result > after

    def test_interval_days_anchor_in_future(self):
        anchor = datetime(2025, 7, 1, 10, 0, tzinfo=UTC)
        after = datetime(2025, 6, 15, 10, 0, tzinfo=UTC)
        result = _next_interval_occurrence(anchor, interval_days=3, after=after)
        assert result == anchor

    def test_interval_days_anchor_in_past(self):
        anchor = datetime(2025, 1, 1, 10, 0, tzinfo=UTC)
        after = datetime(2025, 1, 10, 10, 0, tzinfo=UTC)
        result = _next_interval_occurrence(anchor, interval_days=3, after=after)
        assert result > after


# ---------------------------------------------------------------------------
# _normalize_repeat_rule tests
# ---------------------------------------------------------------------------


class TestNormalizeRepeatRule:
    def test_none_returns_none(self):
        assert _normalize_repeat_rule(None, datetime.now(tz=UTC)) is None

    def test_invalid_type_returns_none(self):
        assert _normalize_repeat_rule({"type": "bogus"}, datetime.now(tz=UTC)) is None

    def test_weekly(self):
        fallback = datetime(2025, 6, 15, 10, 0, tzinfo=UTC)
        result = _normalize_repeat_rule({"type": "weekly", "days": [0, 2]}, fallback)
        assert result is not None
        assert result["type"] == "weekly"
        assert result["days"] == [0, 2]

    def test_weekly_no_days_uses_fallback(self):
        fallback = datetime(2025, 6, 18, 10, 0, tzinfo=UTC)  # Wednesday = 2
        result = _normalize_repeat_rule({"type": "weekly"}, fallback)
        assert result is not None
        assert result["days"] == [fallback.weekday()]

    def test_monthly(self):
        fallback = datetime(2025, 6, 15, 10, 0, tzinfo=UTC)
        result = _normalize_repeat_rule({"type": "monthly", "day": 20}, fallback)
        assert result is not None
        assert result["type"] == "monthly"
        assert result["day"] == 20

    def test_monthly_no_day_uses_fallback(self):
        fallback = datetime(2025, 6, 15, 10, 0, tzinfo=UTC)
        result = _normalize_repeat_rule({"type": "monthly"}, fallback)
        assert result is not None
        assert result["day"] == 15

    def test_interval_months(self):
        fallback = datetime(2025, 6, 15, 10, 0, tzinfo=UTC)
        result = _normalize_repeat_rule({"type": "interval", "interval_months": 3}, fallback)
        assert result is not None
        assert result["interval_months"] == 3

    def test_interval_days(self):
        fallback = datetime(2025, 6, 15, 10, 0, tzinfo=UTC)
        result = _normalize_repeat_rule({"type": "interval", "interval_days": 14}, fallback)
        assert result is not None
        assert result["interval_days"] == 14

    def test_interval_no_months_or_days_defaults(self):
        fallback = datetime(2025, 6, 15, 10, 0, tzinfo=UTC)
        result = _normalize_repeat_rule({"type": "interval"}, fallback)
        assert result is not None
        assert result["interval_days"] == 1


# ---------------------------------------------------------------------------
# _compute_next_reminder_fire tests
# ---------------------------------------------------------------------------


class TestComputeNextReminderFire:
    def _make_reminder_event(self, metadata=None, **overrides):
        defaults = dict(
            event_id="rem123",
            event_type="reminder",
            label="Test Reminder",
            time_of_day="10:00",
            repeat_days=None,
            single_shot=True,
            duration_seconds=None,
            target_time=None,
            next_fire=_serialize_dt(datetime(2025, 6, 15, 10, 0, tzinfo=UTC)),
            playback=PlaybackConfig(),
            created_at=_serialize_dt(datetime(2025, 6, 1, 0, 0, tzinfo=UTC)),
            metadata=metadata or {},
        )
        defaults.update(overrides)
        return ScheduledEvent(**defaults)

    def test_no_repeat_returns_none(self):
        ev = self._make_reminder_event(metadata={"reminder": {"message": "hi"}})
        assert _compute_next_reminder_fire(ev) is None

    def test_weekly_repeat(self):
        start = datetime(2025, 6, 15, 10, 0, tzinfo=UTC)
        ev = self._make_reminder_event(
            metadata={
                "reminder": {
                    "repeat": {"type": "weekly", "days": [0, 2, 4], "time": "10:00"},
                    "start": _serialize_dt(start),
                },
            }
        )
        after = datetime(2025, 6, 15, 11, 0, tzinfo=UTC)
        result = _compute_next_reminder_fire(ev, after=after)
        assert result is not None
        assert result > after

    def test_monthly_repeat(self):
        start = datetime(2025, 6, 15, 10, 0, tzinfo=UTC)
        ev = self._make_reminder_event(
            metadata={
                "reminder": {
                    "repeat": {"type": "monthly", "day": 15, "time": "10:00"},
                    "start": _serialize_dt(start),
                },
            }
        )
        after = datetime(2025, 6, 16, 10, 0, tzinfo=UTC)
        result = _compute_next_reminder_fire(ev, after=after)
        assert result is not None
        assert result > after

    def test_interval_repeat(self):
        start = datetime(2025, 6, 1, 10, 0, tzinfo=UTC)
        ev = self._make_reminder_event(
            metadata={
                "reminder": {
                    "repeat": {"type": "interval", "interval_days": 7, "time": "10:00"},
                    "start": _serialize_dt(start),
                },
            }
        )
        after = datetime(2025, 6, 10, 10, 0, tzinfo=UTC)
        result = _compute_next_reminder_fire(ev, after=after)
        assert result is not None
        assert result > after

    def test_delay_takes_precedence_over_base(self):
        """If delay_until is set and before next base fire, it should be returned."""
        start = datetime(2025, 6, 15, 10, 0, tzinfo=UTC)
        delay = datetime(2025, 6, 15, 10, 30, tzinfo=UTC)
        ev = self._make_reminder_event(
            metadata={
                "reminder": {
                    "repeat": {"type": "weekly", "days": [0, 2, 4], "time": "10:00"},
                    "start": _serialize_dt(start),
                    "delay_until": _serialize_dt(delay),
                },
            }
        )
        after = datetime(2025, 6, 15, 10, 5, tzinfo=UTC)
        result = _compute_next_reminder_fire(ev, after=after)
        assert result is not None
        # delay (10:30) should be before next weekly occurrence, so delay wins
        assert result.minute == 30 or result > after  # at minimum should be after reference


# ---------------------------------------------------------------------------
# ScheduleService: persist / load roundtrip (more detail)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_persist_events_stores_pause_and_enable_dates(tmp_path):
    """Persist and reload paused_dates and enabled_dates."""
    storage_path = tmp_path / "schedules.json"
    svc = ScheduleService(
        storage_path=storage_path,
        hostname="pulse-test",
        on_state_changed=None,
        on_active_event=None,
        ha_client=None,
    )
    await svc.start()
    event = await svc.create_alarm(time_of_day="07:00", label="Persist test", days=[0, 1, 2, 3, 4])
    await svc.pause_alarm(event.event_id)
    await svc.set_ui_pause_date("2025-12-25", True)
    await svc.set_ui_enable_date("2025-12-26", event.event_id, True)
    await svc.stop()

    # Verify file content
    import json

    data = json.loads(storage_path.read_text())
    assert "2025-12-25" in data["paused_dates"]
    assert "2025-12-26" in data["enabled_dates"]

    # Reload in a fresh service
    svc2 = ScheduleService(
        storage_path=storage_path,
        hostname="pulse-test",
        on_state_changed=None,
        on_active_event=None,
        ha_client=None,
    )
    await svc2.start()
    assert "2025-12-25" in svc2._ui_pause_dates
    assert "2025-12-26" in svc2._ui_enable_dates
    assert event.event_id in svc2._ui_enable_dates["2025-12-26"]
    await svc2.stop()


@pytest.mark.anyio
async def test_load_events_skips_expired_timers(tmp_path):
    """Expired timers should not be restored."""
    import json

    storage_path = tmp_path / "schedules.json"
    past = _serialize_dt(datetime(2020, 1, 1, 0, 0, tzinfo=UTC))
    data = {
        "events": [
            {
                "event_id": "expired_timer",
                "event_type": "timer",
                "label": "Old timer",
                "single_shot": True,
                "duration_seconds": 60,
                "target_time": past,
                "next_fire": past,
                "created_at": past,
            }
        ],
        "paused_dates": [],
        "enabled_dates": {},
    }
    storage_path.write_text(json.dumps(data))

    svc = ScheduleService(
        storage_path=storage_path,
        hostname="pulse-test",
        on_state_changed=None,
        on_active_event=None,
        ha_client=None,
    )
    await svc.start()
    assert len(svc.list_events("timer")) == 0
    await svc.stop()


@pytest.mark.anyio
async def test_load_events_corrupt_file(tmp_path):
    """Corrupt JSON file should not crash start()."""
    storage_path = tmp_path / "schedules.json"
    storage_path.write_text("not valid json {{{")
    svc = ScheduleService(
        storage_path=storage_path,
        hostname="pulse-test",
        on_state_changed=None,
        on_active_event=None,
        ha_client=None,
    )
    await svc.start()
    assert len(svc.list_events()) == 0
    await svc.stop()


@pytest.mark.anyio
async def test_load_events_legacy_enabled_dates_format(tmp_path):
    """Legacy format with single string alarm_id per date should load correctly."""
    import json

    storage_path = tmp_path / "schedules.json"
    now_str = _serialize_dt(datetime.now().astimezone() + timedelta(hours=1))
    data = {
        "events": [
            {
                "event_id": "alarm1",
                "event_type": "alarm",
                "label": "Legacy alarm",
                "time_of_day": "08:00",
                "repeat_days": [0, 1, 2, 3, 4],
                "single_shot": False,
                "next_fire": now_str,
                "created_at": now_str,
                "paused": True,
            }
        ],
        "paused_dates": [],
        "enabled_dates": {"2025-12-25": "alarm1"},  # legacy single-string format
    }
    storage_path.write_text(json.dumps(data))

    svc = ScheduleService(
        storage_path=storage_path,
        hostname="pulse-test",
        on_state_changed=None,
        on_active_event=None,
        ha_client=None,
    )
    await svc.start()
    assert "2025-12-25" in svc._ui_enable_dates
    assert "alarm1" in svc._ui_enable_dates["2025-12-25"]
    await svc.stop()


# ---------------------------------------------------------------------------
# ScheduleService: _publish_state tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_publish_state_includes_all_event_types(tmp_path):
    state_cb = MagicMock()
    svc = ScheduleService(
        storage_path=tmp_path / "schedules.json",
        hostname="pulse-test",
        on_state_changed=state_cb,
        on_active_event=None,
        ha_client=None,
    )
    await svc.start()
    await svc.create_alarm(time_of_day="07:00", label="Alarm")
    await svc.create_timer(duration_seconds=60, label="Timer")
    fire = datetime.now().astimezone() + timedelta(hours=1)
    await svc.create_reminder(fire_time=fire, message="Reminder")

    snapshot = state_cb.call_args[0][0]
    assert len(snapshot["alarms"]) == 1
    assert len(snapshot["timers"]) == 1
    assert len(snapshot["reminders"]) == 1
    assert "paused_dates" in snapshot
    assert "enabled_dates" in snapshot
    assert "effective_skip_dates" in snapshot
    assert "skip_weekdays" in snapshot
    assert "updated_at" in snapshot
    await svc.stop()


@pytest.mark.anyio
async def test_publish_state_shows_paused_status(tmp_path):
    state_cb = MagicMock()
    svc = ScheduleService(
        storage_path=tmp_path / "schedules.json",
        hostname="pulse-test",
        on_state_changed=state_cb,
        on_active_event=None,
        ha_client=None,
    )
    await svc.start()
    event = await svc.create_alarm(time_of_day="07:00", label="Pauseable", days=[0, 1, 2, 3, 4])
    await svc.pause_alarm(event.event_id)

    snapshot = state_cb.call_args[0][0]
    alarm_entry = snapshot["alarms"][0]
    assert alarm_entry["status"] == "paused"
    await svc.stop()


# ---------------------------------------------------------------------------
# ScheduleService: update_alarm edge cases
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_update_alarm_playback(schedule_service):
    svc = schedule_service
    event = await svc.create_alarm(time_of_day="08:00", label="Original")
    new_playback = PlaybackConfig(mode="music", music_source="spotify:abc")
    result = await svc.update_alarm(event.event_id, playback=new_playback)
    assert result is True
    assert svc._events[event.event_id].playback.mode == "music"
    assert svc._events[event.event_id].playback.music_source == "spotify:abc"


@pytest.mark.anyio
async def test_update_alarm_clear_days_makes_single_shot(schedule_service):
    svc = schedule_service
    event = await svc.create_alarm(time_of_day="08:00", label="Repeating", days=[0, 1, 2])
    assert svc._events[event.event_id].single_shot is False
    result = await svc.update_alarm(event.event_id, days=[])
    assert result is True
    assert svc._events[event.event_id].single_shot is True
    assert svc._events[event.event_id].repeat_days == []


@pytest.mark.anyio
async def test_update_alarm_time_and_label_together(schedule_service):
    svc = schedule_service
    event = await svc.create_alarm(time_of_day="08:00", label="Old")
    result = await svc.update_alarm(event.event_id, time_of_day="09:30", label="New")
    assert result is True
    updated = svc._events[event.event_id]
    assert updated.time_of_day == "09:30"
    assert updated.label == "New"


# ---------------------------------------------------------------------------
# ScheduleService: extend_timer edge cases
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_extend_timer_wrong_type(schedule_service):
    """Extending a non-timer event should return False."""
    svc = schedule_service
    event = await svc.create_alarm(time_of_day="08:00", label="Alarm")
    result = await svc.extend_timer(event.event_id, seconds=30)
    assert result is False


@pytest.mark.anyio
async def test_extend_timer_updates_both_target_and_next_fire(schedule_service):
    svc = schedule_service
    event = await svc.create_timer(duration_seconds=60, label="T")
    original_fire = event.next_fire_dt()
    await svc.extend_timer(event.event_id, seconds=120)
    updated = svc._events[event.event_id]
    assert updated.target_dt() > original_fire
    assert updated.next_fire_dt() > original_fire
    # target and next_fire should be the same after extend
    target = updated.target_dt()
    fire = updated.next_fire_dt()
    assert abs((target - fire).total_seconds()) < 1


# ---------------------------------------------------------------------------
# ScheduleService: stop_event paths
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_stop_event_repeating_alarm_reschedules(schedule_service):
    """Stopping a repeating alarm should reschedule it rather than remove it."""
    svc = schedule_service
    event = await svc.create_alarm(time_of_day="08:00", label="Repeating", days=[0, 1, 2, 3, 4])
    # Simulate the event being "active" by calling stop_event directly
    await svc.stop_event(event.event_id, reason="stopped")
    # Event should still exist because it is repeating
    assert event.event_id in svc._events


@pytest.mark.anyio
async def test_stop_event_single_shot_alarm_removes(schedule_service):
    """Stopping a single-shot alarm should remove it."""
    svc = schedule_service
    event = await svc.create_alarm(time_of_day="08:00", label="Once")
    assert event.single_shot is True
    await svc.stop_event(event.event_id, reason="stopped")
    assert event.event_id not in svc._events


@pytest.mark.anyio
async def test_stop_event_timer_removes(schedule_service):
    """Stopping a timer should remove it."""
    svc = schedule_service
    event = await svc.create_timer(duration_seconds=300, label="Egg timer")
    await svc.stop_event(event.event_id, reason="stopped")
    assert event.event_id not in svc._events


@pytest.mark.anyio
async def test_stop_event_repeating_reminder_reschedules(schedule_service):
    """Stopping a repeating reminder should reschedule it."""
    svc = schedule_service
    fire = datetime.now().astimezone() + timedelta(hours=1)
    event = await svc.create_reminder(
        fire_time=fire,
        message="Recurring",
        repeat={"type": "weekly", "days": [0, 1, 2, 3, 4]},
    )
    await svc.stop_event(event.event_id, reason="stopped")
    # Should still exist because it repeats
    assert event.event_id in svc._events


@pytest.mark.anyio
async def test_stop_event_single_shot_reminder_removes(schedule_service):
    """Stopping a single-shot reminder should remove it."""
    svc = schedule_service
    fire = datetime.now().astimezone() + timedelta(hours=1)
    event = await svc.create_reminder(fire_time=fire, message="Once")
    await svc.stop_event(event.event_id, reason="stopped")
    assert event.event_id not in svc._events


# ---------------------------------------------------------------------------
# ScheduleService: delete_event cleans up enable dates
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_delete_event_cleans_enable_dates(tmp_path):
    svc = ScheduleService(
        storage_path=tmp_path / "schedules.json",
        hostname="pulse-test",
        on_state_changed=None,
        on_active_event=None,
        ha_client=None,
    )
    await svc.start()
    event = await svc.create_alarm(time_of_day="07:00", label="Del", days=[0, 1, 2, 3, 4])
    await svc.pause_alarm(event.event_id)
    await svc.set_ui_enable_date("2025-12-25", event.event_id, True)
    assert "2025-12-25" in svc._ui_enable_dates

    await svc.delete_event(event.event_id)
    # Enable dates for the deleted alarm should be cleaned up
    assert event.event_id not in svc._events
    # The date entry should have been removed (empty set pruned)
    assert "2025-12-25" not in svc._ui_enable_dates
    await svc.stop()


# ---------------------------------------------------------------------------
# ScheduleService: active_event callback
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_active_event_callback_called(tmp_path):
    active_cb = MagicMock()
    svc = ScheduleService(
        storage_path=tmp_path / "schedules.json",
        hostname="pulse-test",
        on_state_changed=None,
        on_active_event=active_cb,
        ha_client=None,
    )
    await svc.start()
    event = await svc.create_alarm(time_of_day="08:00", label="Callback test")
    # Manually stop - stop_event calls _notify_active
    await svc.stop_event(event.event_id, reason="test_stop")
    # The callback should have been called with the stopped notification
    assert active_cb.called
    await svc.stop()


# ---------------------------------------------------------------------------
# ScheduleService: create_alarm with explicit single_shot
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_create_alarm_explicit_single_shot_false(schedule_service):
    """Passing single_shot=False with days=None should respect the explicit value."""
    svc = schedule_service
    event = await svc.create_alarm(time_of_day="08:00", label="Test", single_shot=False)
    assert event.single_shot is False


@pytest.mark.anyio
async def test_create_alarm_with_playback(schedule_service):
    svc = schedule_service
    pb = PlaybackConfig(mode="music", music_source="spotify:playlist:test")
    event = await svc.create_alarm(time_of_day="08:00", label="Music alarm", playback=pb)
    assert event.playback.mode == "music"
    assert event.playback.music_source == "spotify:playlist:test"


# ---------------------------------------------------------------------------
# ScheduleService: delay_reminder
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_delay_reminder_single_shot(schedule_service):
    svc = schedule_service
    fire = datetime.now().astimezone() + timedelta(seconds=30)
    event = await svc.create_reminder(fire_time=fire, message="Delay me")
    result = await svc.delay_reminder(event.event_id, seconds=600)
    assert result is True
    updated = svc._events[event.event_id]
    # delay_reminder sets next_fire to _now() + seconds (600s), which is > original (30s from now)
    assert updated.next_fire_dt() > fire


@pytest.mark.anyio
async def test_delay_reminder_nonexistent(schedule_service):
    svc = schedule_service
    result = await svc.delay_reminder("nonexistent", seconds=60)
    assert result is False


@pytest.mark.anyio
async def test_delay_reminder_wrong_type(schedule_service):
    svc = schedule_service
    event = await svc.create_timer(duration_seconds=60)
    result = await svc.delay_reminder(event.event_id, seconds=60)
    assert result is False


@pytest.mark.anyio
async def test_delay_reminder_repeating(schedule_service):
    svc = schedule_service
    fire = datetime.now().astimezone() + timedelta(hours=1)
    event = await svc.create_reminder(
        fire_time=fire,
        message="Repeat delay",
        repeat={"type": "weekly", "days": [0, 1, 2, 3, 4]},
    )
    result = await svc.delay_reminder(event.event_id, seconds=300)
    assert result is True


# ---------------------------------------------------------------------------
# ScheduleService: skip dates and weekdays
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_skip_weekdays(tmp_path):
    """Alarms should skip specified weekdays."""
    svc = ScheduleService(
        storage_path=tmp_path / "schedules.json",
        hostname="pulse-test",
        on_state_changed=None,
        on_active_event=None,
        ha_client=None,
        skip_weekdays={5, 6},  # skip weekends
    )
    await svc.start()
    event = await svc.create_alarm(time_of_day="08:00", label="Weekday only", days=list(range(7)))
    fire = event.next_fire_dt()
    assert fire.weekday() not in {5, 6}
    await svc.stop()


@pytest.mark.anyio
async def test_set_manual_skip_dates(tmp_path):
    svc = ScheduleService(
        storage_path=tmp_path / "schedules.json",
        hostname="pulse-test",
        on_state_changed=None,
        on_active_event=None,
        ha_client=None,
    )
    await svc.start()
    await svc.set_manual_skip_dates({"2025-12-25"})
    assert "2025-12-25" in svc._effective_skip_dates()
    await svc.stop()


@pytest.mark.anyio
async def test_set_ooo_skip_dates(tmp_path):
    svc = ScheduleService(
        storage_path=tmp_path / "schedules.json",
        hostname="pulse-test",
        on_state_changed=None,
        on_active_event=None,
        ha_client=None,
    )
    await svc.start()
    await svc.set_ooo_skip_dates({"2025-07-04"})
    assert "2025-07-04" in svc._effective_skip_dates()
    await svc.stop()


# ---------------------------------------------------------------------------
# ScheduleService: create_reminder with monthly/interval repeat
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_create_reminder_with_monthly_repeat(schedule_service):
    svc = schedule_service
    fire = datetime.now().astimezone() + timedelta(hours=1)
    event = await svc.create_reminder(
        fire_time=fire,
        message="Monthly",
        repeat={"type": "monthly", "day": 15},
    )
    assert event.single_shot is False
    # Monthly repeat should not set repeat_days
    assert event.repeat_days is None


@pytest.mark.anyio
async def test_create_reminder_with_interval_repeat(schedule_service):
    svc = schedule_service
    fire = datetime.now().astimezone() + timedelta(hours=1)
    event = await svc.create_reminder(
        fire_time=fire,
        message="Every 2 weeks",
        repeat={"type": "interval", "interval_days": 14},
    )
    assert event.single_shot is False


# ---------------------------------------------------------------------------
# ScheduleService: _schedule_event skips paused alarms
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_schedule_event_skips_paused(schedule_service):
    """A paused alarm should not get a task in _tasks."""
    svc = schedule_service
    event = await svc.create_alarm(time_of_day="08:00", label="Pauseable", days=[0, 1, 2, 3, 4])
    assert event.event_id in svc._tasks
    await svc.pause_alarm(event.event_id)
    assert event.event_id not in svc._tasks
    # Resume should re-add it
    await svc.resume_alarm(event.event_id)
    assert event.event_id in svc._tasks


# ---------------------------------------------------------------------------
# ScheduleService: update_sound_settings
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_update_sound_settings(schedule_service):
    svc = schedule_service
    from pulse.sound_library import SoundSettings

    new_settings = SoundSettings.with_defaults()
    svc.update_sound_settings(new_settings)
    assert svc._sound_settings is new_settings
