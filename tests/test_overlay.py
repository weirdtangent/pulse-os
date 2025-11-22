from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from pulse.overlay import (
    ClockConfig,
    OverlaySnapshot,
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
            "last_reason": "test",
            "generated_at": 0.0,
            "schedule_snapshot": None,
        }
        data.update(overrides)
        return OverlaySnapshot(**data)

    def test_single_clock_center_cell(self) -> None:
        html = render_overlay_html(self._snapshot(), self.theme)
        self.assertIn('data-cell="center"', html)
        self.assertIn("Local", html)

    def test_two_clocks_split_left_right(self) -> None:
        clocks = (
            ClockConfig("clock0", "Home", None),
            ClockConfig("clock1", "NYC", "America/New_York"),
        )
        html = render_overlay_html(self._snapshot(clocks=clocks), self.theme)
        self.assertIn('data-cell="middle-left"', html)
        self.assertIn('data-cell="middle-right"', html)

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
        self.assertEqual(clocks[0].label, "Home")
        self.assertIsNone(clocks[0].timezone)
        self.assertEqual(clocks[1].timezone, "America/Chicago")


if __name__ == "__main__":
    unittest.main()
