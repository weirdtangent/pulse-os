import tempfile
from pathlib import Path
from unittest import IsolatedAsyncioTestCase

from pulse.assistant.schedule_service import ScheduleService


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
