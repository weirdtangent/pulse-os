from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from pulse.overlay import (
    ClockConfig,
    OverlaySnapshot,
    OverlayStateManager,
    OverlayTheme,
    parse_clock_config,
    render_overlay_html,
)


class OverlayRenderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.theme = OverlayTheme(
            ambient_background="rgba(0,0,0,0.32)",
            alert_background="rgba(0,0,0,0.65)",
            text_color="#FFFFFF",
            accent_color="#88C0D0",
            show_notification_bar=True,
        )

    def _snapshot(self, **overrides) -> OverlaySnapshot:
        data = {
            "version": 1,
            "clocks": (ClockConfig("clock0", "Local", None),),
            "now_playing": "",
            "timers": (),
            "alarms": (),
            "active_alarm": None,
            "active_timer": None,
            "notifications": (),
            "timer_positions": {},
            "info_card": None,
            "last_reason": "test",
            "generated_at": 0.0,
            "schedule_snapshot": None,
        }
        data.update(overrides)
        return OverlaySnapshot(**data)

    def test_single_clock_bottom_left_cell(self) -> None:
        html = render_overlay_html(self._snapshot(), self.theme)
        self.assertIn('data-cell="bottom-left"', html)
        self.assertIn("Local", html)

    def test_only_first_clock_used_if_multiple_provided(self) -> None:
        # Even if multiple clocks are provided, only the first one is rendered
        clocks = (
            ClockConfig("clock0", "Home", None),
            ClockConfig("clock1", "NYC", "America/New_York"),
        )
        html = render_overlay_html(self._snapshot(clocks=clocks), self.theme)
        # Should only show bottom-left (single clock position)
        self.assertIn('data-cell="bottom-left"', html)
        self.assertIn("Home", html)
        # Second clock should not appear
        self.assertNotIn("NYC", html)

    def test_timer_card_rendered(self) -> None:
        target = (datetime.now(UTC) + timedelta(minutes=5)).isoformat()
        timers = ({"id": "tea", "label": "Tea", "next_fire": target},)
        html = render_overlay_html(self._snapshot(timers=timers), self.theme)
        self.assertIn("overlay-card--timer", html)
        self.assertIn('data-target-ms="', html)

    def test_notification_bar_icons(self) -> None:
        future = (datetime.now(UTC) + timedelta(minutes=10)).isoformat()
        alarms = ({"id": "alarm1", "label": "Wake Up", "next_fire": future},)
        timers = (
            {
                "id": "timer1",
                "label": "Tea",
                "next_fire": (datetime.now(UTC) + timedelta(minutes=2)).isoformat(),
            },
        )
        snapshot = self._snapshot(alarms=alarms, timers=timers, now_playing="Artist — Title")
        html = render_overlay_html(snapshot, self.theme)
        self.assertIn("overlay-notification-bar", html)
        self.assertIn("Now playing", html)

    def test_parse_clock_config_inserts_local_by_default(self) -> None:
        clocks = parse_clock_config("America/Chicago=HQ", default_label="Home", log=None)
        # Should only return 1 clock (local timezone inserted first)
        self.assertEqual(len(clocks), 1)
        self.assertEqual(clocks[0].label, "Home")
        self.assertIsNone(clocks[0].timezone)

    def test_parse_clock_config_only_uses_first_entry(self) -> None:
        # Multiple entries provided, but only first is used
        clocks = parse_clock_config(
            "local=Home,America/Chicago=HQ,Europe/London=LDN", default_label="Default", log=None
        )
        self.assertEqual(len(clocks), 1)
        self.assertEqual(clocks[0].label, "Home")
        self.assertIsNone(clocks[0].timezone)

    def test_info_card_updates_snapshot(self) -> None:
        manager = OverlayStateManager()
        change = manager.update_info_card({"text": "Hello world", "category": "news"})
        self.assertTrue(change.changed)
        snapshot = manager.snapshot()
        self.assertIsNotNone(snapshot.info_card)
        assert snapshot.info_card is not None
        self.assertEqual(snapshot.info_card["text"], "Hello world")
        no_change = manager.update_info_card({"text": "Hello world", "category": "news"})
        self.assertFalse(no_change.changed)
        cleared = manager.update_info_card(None)
        self.assertTrue(cleared.changed)
        self.assertIsNone(manager.snapshot().info_card)
        alarms_change = manager.update_info_card({"type": "alarms", "alarms": [{"id": "alarm1"}]})
        self.assertTrue(alarms_change.changed)
        alarm_card = manager.snapshot().info_card
        assert alarm_card is not None
        self.assertIn("alarms", alarm_card)

    def test_active_timer_card_uses_previous_position(self) -> None:
        snapshot = self._snapshot(
            timers=(),
            timer_positions={"tea": "top-center"},
            active_timer={"state": "ringing", "event": {"id": "tea", "label": "Tea timer"}},
        )
        html = render_overlay_html(snapshot, self.theme)
        expected = (
            'cell-top-center" data-cell="top-center"><div class="overlay-card '
            "overlay-card--alert overlay-card--ringing"
        )
        self.assertIn(expected, html)

    def test_alarm_info_card_renders_delete_buttons(self) -> None:
        alarms = (
            {"id": "alarm1", "label": "Wake Up", "time_of_day": "08:00", "repeat_days": [0, 1, 2, 3, 4]},
            {"id": "alarm2", "label": "Weekend", "time_of_day": "09:30", "repeat_days": [5, 6]},
        )
        snapshot = self._snapshot(alarms=alarms, info_card={"type": "alarms", "title": "Alarms"})
        html = render_overlay_html(snapshot, self.theme)
        self.assertIn('data-delete-alarm="alarm1"', html)
        self.assertIn("data-info-card-close", html)

    def test_alarm_info_card_can_use_payload_data(self) -> None:
        manager = OverlayStateManager()
        manager.update_schedule_snapshot({"alarms": [], "timers": []})
        manager.update_info_card(
            {
                "type": "alarms",
                "title": "Alarms",
                "alarms": [{"id": "alarm42", "label": "Test Alarm", "time": "07:30", "repeat_days": [0, 1, 2, 3, 4]}],
            }
        )
        html = render_overlay_html(manager.snapshot(), self.theme, info_endpoint="/overlay/info-card")
        self.assertIn('data-delete-alarm="alarm42"', html)
        self.assertIn("Weekdays", html)


if __name__ == "__main__":
    unittest.main()
