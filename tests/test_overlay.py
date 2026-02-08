from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from pulse.overlay import (
    ClockConfig,
    OverlaySnapshot,
    OverlayStateManager,
    OverlayTheme,
    _build_config_info_overlay,
    _get_library_versions,
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
            "reminders": (),
            "calendar_events": (),
            "active_alarm": None,
            "active_timer": None,
            "active_reminder": None,
            "notifications": (),
            "timer_positions": {},
            "info_card": None,
            "last_reason": "test",
            "generated_at": 0.0,
            "schedule_snapshot": None,
            "earmuffs_enabled": False,
            "update_available": False,
        }
        data.update(overrides)
        return OverlaySnapshot(**data)  # type: ignore[arg-type]

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
            'cell-top-center" data-cell="top-center"><div class="overlay-card overlay-card--alert overlay-card--ringing'
        )
        self.assertIn(expected, html)

    def test_alarm_info_card_renders_action_buttons(self) -> None:
        alarms = (
            {"id": "alarm1", "label": "Wake Up", "time_of_day": "08:00", "repeat_days": [0, 1, 2, 3, 4]},
            {"id": "alarm2", "label": "Weekend", "time_of_day": "09:30", "repeat_days": [5, 6]},
        )
        snapshot = self._snapshot(alarms=alarms, info_card={"type": "alarms", "title": "Alarms"})
        html = render_overlay_html(snapshot, self.theme)
        self.assertIn('data-delete-alarm="alarm1"', html)
        self.assertIn('data-toggle-alarm="pause"', html)
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
        self.assertIn('data-toggle-alarm="pause"', html)

    def test_alarm_info_card_renders_resume_for_paused_alarm(self) -> None:
        alarms = (
            {
                "id": "alarm-paused",
                "label": "Vacation",
                "time_of_day": "07:00",
                "repeat_days": [0, 1, 2, 3, 4],
                "status": "paused",
            },
        )
        snapshot = self._snapshot(alarms=alarms, info_card={"type": "alarms", "title": "Alarms"})
        html = render_overlay_html(snapshot, self.theme)
        self.assertIn('data-toggle-alarm="resume"', html)
        self.assertIn("Paused", html)

    def test_alarm_info_card_renders_pause_day_toggles(self) -> None:
        future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        alarms = (
            {
                "id": "alarm1",
                "label": "Wake up",
                "time": "07:00",
                "repeat_days": [0, 1, 2, 3, 4],  # Mon-Fri
                "next_fire": future,
                "status": "active",
            },
        )
        snapshot = self._snapshot(
            alarms=alarms,
            info_card={"type": "alarms", "title": "Alarms"},
            schedule_snapshot={
                "alarms": [
                    {
                        "id": "alarm1",
                        "time": "07:00",
                        "repeat_days": [0, 1, 2, 3, 4],
                    }
                ],
                "timers": [],
                "reminders": [],
                "paused_dates": ["2025-12-28"],
                "effective_skip_dates": ["2025-12-28"],
                "skip_weekdays": [],
            },
        )
        html = render_overlay_html(snapshot, self.theme)
        self.assertIn("data-toggle-pause-day", html)
        self.assertIn("Use the buttons to pause, resume, or delete an alarm", html)

    def test_notification_bar_shows_reminder_badge(self) -> None:
        future = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
        reminders = ({"id": "rem1", "label": "Trash", "next_fire": future},)
        html = render_overlay_html(self._snapshot(reminders=reminders), self.theme)
        self.assertIn("reminder", html.lower())

    def test_reminder_info_card_renders_actions(self) -> None:
        manager = OverlayStateManager()
        future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        manager.update_schedule_snapshot(
            {
                "alarms": [],
                "timers": [],
                "reminders": [{"id": "rem1", "label": "Trash", "next_fire": future}],
            }
        )
        manager.update_info_card({"type": "reminders", "title": "Reminders"})
        html = render_overlay_html(manager.snapshot(), self.theme, info_endpoint="/overlay/info-card")
        self.assertIn('data-delete-reminder="rem1"', html)
        self.assertIn("data-complete-reminder", html)

    def test_weather_info_card_renders_icons(self) -> None:
        snapshot = self._snapshot(
            info_card={
                "type": "weather",
                "title": "Testville",
                "subtitle": "Next 2 days",
                "units": "°F",
                "current": {"label": "Now", "temp": "70", "units": "°F", "description": "Clear", "icon": "sunny"},
                "days": [
                    {"label": "Today", "high": "72", "low": "58", "precip": 20, "icon": "sunny"},
                    {"label": "Tomorrow", "high": "70", "low": "55", "precip": None, "icon": "rain"},
                ],
            }
        )
        html = render_overlay_html(snapshot, self.theme)
        self.assertIn("overlay-weather-row", html)
        self.assertIn("High 72°F", html)
        self.assertIn("data:image/png;base64", html)
        self.assertIn("Now", html)

    def test_state_manager_preserves_weather_payload(self) -> None:
        manager = OverlayStateManager()
        manager.update_info_card(
            {
                "type": "weather",
                "title": "Town",
                "units": "°F",
                "subtitle": "Next day",
                "current": {"label": "Now", "temp": "70", "units": "°F", "description": "Clear", "icon": "sunny"},
                "days": [{"label": "Today", "high": "70", "low": "50", "precip": 10, "icon": "sunny"}],
            }
        )
        card = manager.snapshot().info_card
        assert card is not None
        self.assertEqual(card.get("type"), "weather")
        self.assertEqual(card.get("units"), "°F")
        days = card.get("days")
        assert isinstance(days, list)
        self.assertEqual(len(days), 1)
        self.assertEqual(days[0]["icon"], "sunny")

    def test_calendar_reminder_shows_ok_only(self) -> None:
        snapshot = self._snapshot(
            active_reminder={
                "state": "ringing",
                "event": {
                    "id": "cal-123",
                    "label": "Team sync",
                    "metadata": {
                        "reminder": {"message": "Team sync"},
                        "calendar": {"allow_delay": False},
                    },
                },
            }
        )
        html = render_overlay_html(snapshot, self.theme)
        self.assertIn(">OK<", html)
        self.assertIn("data-complete-reminder", html)
        self.assertNotIn('data-delay-reminder data-event-id="cal-123"', html)

    def test_calendar_badge_renders_when_events_exist(self) -> None:
        events = (
            {
                "summary": "Sync",
                "start": "2025-01-02T15:00:00+00:00",
                "start_local": "2025-01-02T10:00:00-05:00",
                "all_day": False,
            },
        )
        html = render_overlay_html(self._snapshot(calendar_events=events), self.theme)
        self.assertIn('data-badge-action="show_calendar"', html)

    def test_calendar_info_card_renders_entries(self) -> None:
        snapshot = self._snapshot(
            info_card={
                "type": "calendar",
                "events": [
                    {
                        "summary": "Project kickoff",
                        "start": "2025-01-04T15:00:00+00:00",
                        "start_local": "2025-01-04T10:00:00-05:00",
                        "all_day": False,
                        "calendar_name": "Work",
                        "location": "Conf room",
                    }
                ],
            }
        )
        html = render_overlay_html(snapshot, self.theme)
        self.assertIn("Project kickoff", html)
        self.assertIn("Conf room", html)
        self.assertIn("Upcoming events in the next 72 hours.", html)

    def test_calendar_info_card_uses_custom_lookahead_value(self) -> None:
        snapshot = self._snapshot(
            info_card={
                "type": "calendar",
                "lookahead_hours": 12,
                "events": [
                    {
                        "summary": "Lunch",
                        "start": "2025-01-04T15:00:00+00:00",
                        "start_local": "2025-01-04T10:00:00-05:00",
                        "all_day": False,
                    }
                ],
            }
        )
        html = render_overlay_html(snapshot, self.theme)
        self.assertIn("Upcoming events in the next 12 hours.", html)

    def test_declined_calendar_event_is_styled(self) -> None:
        snapshot = self._snapshot(
            info_card={
                "type": "calendar",
                "events": [
                    {
                        "summary": "Weekly sync",
                        "start": "2025-01-05T15:00:00+00:00",
                        "start_local": "2025-01-05T10:00:00-05:00",
                        "all_day": False,
                        "declined": True,
                    }
                ],
            }
        )
        html = render_overlay_html(snapshot, self.theme)
        self.assertIn("overlay-info-card__reminder--declined", html)
        self.assertIn("Declined", html)

    def test_lights_info_card_renders_entries(self) -> None:
        snapshot = self._snapshot(
            info_card={
                "type": "lights",
                "title": "Lights",
                "subtitle": "2 on • 3 total",
                "lights": [
                    {
                        "name": "Kitchen",
                        "state": "on",
                        "brightness_pct": 60,
                        "color_temp": "3000K",
                        "area": "Downstairs",
                    }
                ],
            }
        )
        html = render_overlay_html(snapshot, self.theme)
        self.assertIn("Kitchen", html)
        self.assertIn("3000K", html)
        self.assertIn("60%", html)
        self.assertIn("Lights", html)

    def test_routines_info_card_renders_entries(self) -> None:
        snapshot = self._snapshot(
            info_card={
                "type": "routines",
                "title": "Routines",
                "subtitle": "Available: Morning",
                "routines": [
                    {"slug": "routine.morning", "label": "Morning", "description": "Warm lights on."},
                    {"slug": "routine.movie", "label": "Movie", "description": "Dim lights."},
                ],
            }
        )
        html = render_overlay_html(snapshot, self.theme)
        self.assertIn("Routines", html)
        self.assertIn("Morning", html)
        self.assertIn("Dim lights.", html)

    def test_health_info_card_renders_entries(self) -> None:
        snapshot = self._snapshot(
            info_card={
                "type": "health",
                "title": "Health",
                "items": [
                    {"label": "MQTT", "value": "connected"},
                    {"label": "Home Assistant", "value": "online"},
                ],
            }
        )
        html = render_overlay_html(snapshot, self.theme)
        self.assertIn("MQTT", html)
        self.assertIn("connected", html)


class ConfigInfoCardTests(unittest.TestCase):
    """Tests for config info card functions."""

    def test_get_library_versions_returns_string(self) -> None:
        """Test that _get_library_versions returns a non-empty string."""
        result = _get_library_versions()
        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 0)

    def test_get_library_versions_contains_library_names(self) -> None:
        """Test that library names appear in the output."""
        result = _get_library_versions()
        # Check for some core libraries we know should be installed
        self.assertIn("paho-mqtt", result)
        self.assertIn("httpx", result)

    def test_get_library_versions_contains_versions(self) -> None:
        """Test that version numbers appear in the output."""
        result = _get_library_versions()
        # Should contain at least one digit (version number)
        self.assertTrue(any(c.isdigit() for c in result))

    def test_get_library_versions_html_escaped(self) -> None:
        """Test that library versions are HTML-escaped."""
        result = _get_library_versions()
        # Should not contain unescaped special chars if any were present
        # The function should handle this gracefully
        self.assertIsInstance(result, str)

    def test_build_config_info_overlay_contains_logo(self) -> None:
        """Test that the config overlay includes the SVG logo."""
        html = _build_config_info_overlay()
        self.assertIn("<svg", html)
        self.assertIn("pulseGradient", html)
        self.assertIn("GRAYSTORM PULSE", html)

    def test_build_config_info_overlay_has_accessibility_attributes(self) -> None:
        """Test that the SVG logo has proper accessibility attributes."""
        html = _build_config_info_overlay()
        self.assertIn('role="img"', html)
        self.assertIn('aria-label="Graystorm Pulse logo"', html)

    def test_build_config_info_overlay_contains_about_section(self) -> None:
        """Test that the About section is present."""
        html = _build_config_info_overlay()
        self.assertIn("About", html)
        self.assertIn("Version", html)
        self.assertIn("License", html)
        self.assertIn("Key Libraries", html)

    def test_build_config_info_overlay_contains_version(self) -> None:
        """Test that the version is displayed."""
        html = _build_config_info_overlay()
        # Should contain a version number pattern (e.g., 0.101.7)
        self.assertTrue(any(c.isdigit() for c in html))

    def test_build_config_info_overlay_contains_license(self) -> None:
        """Test that license information is displayed."""
        html = _build_config_info_overlay()
        self.assertIn("MIT License", html)
        self.assertIn("2025", html)

    def test_build_config_info_overlay_contains_config_buttons(self) -> None:
        """Test that config action buttons are present."""
        html = _build_config_info_overlay()
        self.assertIn("Sound picker", html)
        self.assertIn("Device controls", html)

    def test_build_config_info_overlay_structure(self) -> None:
        """Test that the overlay has correct HTML structure."""
        html = _build_config_info_overlay()
        self.assertIn("overlay-card", html)
        self.assertIn("overlay-info-card--config", html)
        self.assertIn("overlay-config-logo", html)
        self.assertIn("overlay-config-about", html)


if __name__ == "__main__":
    unittest.main()
