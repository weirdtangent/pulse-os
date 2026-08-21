from __future__ import annotations

import time
import unittest
from datetime import UTC, datetime, timedelta

from pulse.overlay import (
    ClockConfig,
    OverlaySnapshot,
    OverlayStateManager,
    OverlayTheme,
    _build_config_info_overlay,
    _build_help_info_overlay,
    _build_now_playing_card,
    _get_library_versions,
    parse_clock_config,
    render_overlay_html,
)
from pulse.weather_alerts import BANNER_ALWAYS


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
            "now_playing_state": "",
            "now_playing_image": "",
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

    def test_ticker_rendered_when_enabled(self) -> None:
        theme = OverlayTheme(
            ambient_background="rgba(0,0,0,0.32)",
            alert_background="rgba(0,0,0,0.65)",
            text_color="#FFFFFF",
            accent_color="#88C0D0",
            show_notification_bar=True,
            show_ticker=True,
        )
        ticker = (
            {
                "symbol": "^SPX",
                "label": "S&P 500",
                "price": 7736.61,
                "change": 13.06,
                "change_pct": 12.5,  # outsized move -> emoji accent
                "is_up": True,
                "market_state": "POST",
                "after_hours": 7740.0,
            },
        )
        html = render_overlay_html(self._snapshot(ticker=ticker), theme)
        self.assertIn('class="pulse-ticker"', html)
        self.assertIn("overlay-root--ticker", html)
        self.assertIn("data-ticker-track", html)
        self.assertIn("S&amp;P 500", html)
        self.assertIn("🚀", html)  # >= +10%
        self.assertIn("AH 7,740.00", html)

    def _ticker_theme(self) -> OverlayTheme:
        return OverlayTheme(
            ambient_background="rgba(0,0,0,0.32)",
            alert_background="rgba(0,0,0,0.65)",
            text_color="#FFFFFF",
            accent_color="#88C0D0",
            show_notification_bar=True,
            show_ticker=True,
        )

    def _quote(self, **overrides) -> dict:
        quote = {
            "symbol": "VOO",
            "label": "VOO",
            "price": 710.39,
            "change": -0.32,
            "change_pct": -0.04,
            "is_up": False,
        }
        quote.update(overrides)
        return quote

    # The class names also appear in the inlined stylesheet, so these assert on the
    # rendered markup rather than the bare class string.
    _STALE_MARKUP = '<span class="pulse-ticker__stale">'
    _DELAY_MARKUP = '<span class="pulse-ticker__delay">'

    def test_healthy_quote_shows_no_freshness_marker(self) -> None:
        html = render_overlay_html(self._snapshot(ticker=(self._quote(),)), self._ticker_theme())
        self.assertNotIn(self._STALE_MARKUP, html)
        self.assertNotIn(self._DELAY_MARKUP, html)

    def test_stale_quote_marked(self) -> None:
        ticker = (self._quote(is_stale=True),)
        html = render_overlay_html(self._snapshot(ticker=ticker), self._ticker_theme())
        self.assertIn(f"{self._STALE_MARKUP}⏱</span>", html)

    def test_delayed_feed_shows_minutes(self) -> None:
        ticker = (self._quote(delayed_by=15),)
        html = render_overlay_html(self._snapshot(ticker=ticker), self._ticker_theme())
        self.assertIn(f"{self._DELAY_MARKUP}15m</span>", html)

    def test_explicit_delay_wins_over_stale(self) -> None:
        # A known exchange delay is the more specific statement, so it should be the one
        # rendered rather than stacking two markers on one quote.
        ticker = (self._quote(delayed_by=15, is_stale=True),)
        html = render_overlay_html(self._snapshot(ticker=ticker), self._ticker_theme())
        self.assertIn(f"{self._DELAY_MARKUP}15m</span>", html)
        self.assertNotIn(self._STALE_MARKUP, html)

    def test_ticker_label_mode_ticker_shows_symbol(self) -> None:
        theme = OverlayTheme(
            ambient_background="rgba(0,0,0,0.32)",
            alert_background="rgba(0,0,0,0.65)",
            text_color="#FFFFFF",
            accent_color="#88C0D0",
            show_ticker=True,
            ticker_label_mode="ticker",
        )
        ticker = (
            {"symbol": "^SPX", "label": "S&P 500", "price": 1.0, "change": 0.0, "change_pct": 0.0, "is_up": True},
            {
                "symbol": "VTI",
                "label": "Vanguard Morningstar Total Stoc",
                "price": 380.0,
                "change": 0.6,
                "change_pct": 0.16,
                "is_up": True,
            },
        )
        html = render_overlay_html(self._snapshot(ticker=ticker), theme)
        self.assertIn(">SPX<", html)  # caret stripped
        self.assertIn(">VTI<", html)  # symbol, not the long fund name
        self.assertNotIn("Vanguard Morningstar", html)
        self.assertNotIn("S&amp;P 500", html)

    def test_ticker_label_mode_auto_names_indices_symbols_others(self) -> None:
        theme = OverlayTheme(
            ambient_background="rgba(0,0,0,0.32)",
            alert_background="rgba(0,0,0,0.65)",
            text_color="#FFFFFF",
            accent_color="#88C0D0",
            show_ticker=True,
            ticker_label_mode="auto",
        )
        ticker = (
            {"symbol": "^SPX", "label": "S&P 500", "price": 1.0, "change": 0.0, "change_pct": 0.0, "is_up": True},
            {
                "symbol": "VTI",
                "label": "Vanguard Morningstar Total Stoc",
                "price": 380.0,
                "change": 0.6,
                "change_pct": 0.16,
                "is_up": True,
            },
        )
        html = render_overlay_html(self._snapshot(ticker=ticker), theme)
        self.assertIn("S&amp;P 500", html)  # index -> friendly name
        self.assertIn(">VTI<", html)  # custom ticker -> symbol
        self.assertNotIn("Vanguard Morningstar", html)

    def test_ticker_absent_when_disabled(self) -> None:
        ticker = (
            {"symbol": "^SPX", "label": "S&P 500", "price": 1.0, "change": 0.0, "change_pct": 0.0, "is_up": True},
        )
        html = render_overlay_html(self._snapshot(ticker=ticker), self.theme)  # theme.show_ticker defaults False
        self.assertNotIn('class="pulse-ticker"', html)

    # --- Market summary pill (notification bar) ---------------------------------
    _MARKET_PILL_MARKUP = '<span class="overlay-badge overlay-badge--market"'

    def _index(self, symbol: str, label: str, pct: float) -> dict:
        return {
            "symbol": symbol,
            "label": label,
            "price": 100.0,
            "change": pct,
            "change_pct": pct,
            "is_up": pct >= 0,
        }

    def test_market_pill_shows_percents_with_direction(self) -> None:
        ticker = (
            self._index("^SPX", "S&P 500", 0.42),
            self._index("^DJI", "Dow", 0.31),
            self._index("^IXIC", "Nasdaq", -0.12),
        )
        html = render_overlay_html(self._snapshot(ticker=ticker), self._ticker_theme())
        self.assertIn(self._MARKET_PILL_MARKUP, html)
        self.assertIn(">▲0.42</span>", html)
        self.assertIn(">▲0.31</span>", html)
        self.assertIn(">▼0.12</span>", html)  # sign lives in the arrow, not the number
        self.assertIn("overlay-market__move--up", html)
        self.assertIn("overlay-market__move--down", html)
        # Names are dropped from the visible pill but kept for hover/screen readers.
        self.assertIn("Markets: S&amp;P 500 up 0.42%, Dow up 0.31%, Nasdaq down 0.12%", html)

    def test_market_pill_prefers_indices_and_caps_at_three(self) -> None:
        ticker = (
            self._index("VTI", "VTI", 1.0),
            self._index("^SPX", "S&P 500", 0.42),
            self._index("^DJI", "Dow", 0.31),
            self._index("^IXIC", "Nasdaq", -0.12),
            self._index("^RUT", "Russell 2000", 0.55),
        )
        html = render_overlay_html(self._snapshot(ticker=ticker), self._ticker_theme())
        self.assertIn(">▲0.42</span>", html)
        self.assertIn("Markets: S&amp;P 500 up 0.42%, Dow up 0.31%, Nasdaq down 0.12%", html)
        self.assertNotIn(">▲1.00</span>", html)  # equity skipped in favor of indices
        self.assertNotIn(">▲0.55</span>", html)  # fourth index trimmed

    def test_market_pill_falls_back_to_equities_when_no_indices(self) -> None:
        ticker = (self._index("VTI", "VTI", 1.0), self._index("VOO", "VOO", -0.25))
        html = render_overlay_html(self._snapshot(ticker=ticker), self._ticker_theme())
        self.assertIn(">▲1.00</span>", html)
        self.assertIn(">▼0.25</span>", html)

    def test_market_pill_absent_when_ticker_disabled(self) -> None:
        ticker = (self._index("^SPX", "S&P 500", 0.42),)
        html = render_overlay_html(self._snapshot(ticker=ticker), self.theme)  # show_ticker False
        self.assertNotIn(self._MARKET_PILL_MARKUP, html)

    def test_market_pill_absent_when_no_quotes(self) -> None:
        # The ticker thread pushes an empty list when the hours mode hides the bar, so the
        # pill must vanish with it rather than freezing the last quotes on screen.
        html = render_overlay_html(self._snapshot(ticker=()), self._ticker_theme())
        self.assertNotIn(self._MARKET_PILL_MARKUP, html)

    # --- Speaker-offline badge (notification bar) -------------------------------
    _SPEAKER_PILL_MARKUP = '<span class="overlay-badge overlay-badge--speaker-offline"'

    def test_speaker_pill_absent_when_speaker_reachable(self) -> None:
        # The healthy case is the common one and gets no badge at all — a permanent
        # "speaker OK" pill would train everyone to stop reading the bar.
        html = render_overlay_html(self._snapshot(speaker_offline=None), self.theme)
        self.assertNotIn(self._SPEAKER_PILL_MARKUP, html)

    def test_speaker_pill_names_the_bluetooth_speaker(self) -> None:
        snapshot = self._snapshot(speaker_offline={"name": "Living Room", "kind": "bluetooth"})
        html = render_overlay_html(snapshot, self.theme)
        self.assertIn(self._SPEAKER_PILL_MARKUP, html)
        self.assertIn("<span>Living Room offline</span>", html)
        self.assertIn("power-cycle the speaker", html)

    def test_speaker_pill_wired_says_check_the_cables(self) -> None:
        # A USB speaker can't be power-cycled from the couch, so the wired variant has
        # to point at the actual fix rather than reusing the Bluetooth wording.
        snapshot = self._snapshot(speaker_offline={"name": "Speaker", "kind": "wired"})
        html = render_overlay_html(snapshot, self.theme)
        self.assertIn("<span>Speaker unplugged</span>", html)
        self.assertIn("USB and power cables", html)
        self.assertNotIn("power-cycle the speaker", html)

    def test_speaker_pill_escapes_speaker_name(self) -> None:
        # Speaker names come from BlueZ, i.e. from whatever the device advertises.
        snapshot = self._snapshot(speaker_offline={"name": '<b>"Bad"</b>', "kind": "bluetooth"})
        html = render_overlay_html(snapshot, self.theme)
        self.assertNotIn("<b>", html)
        self.assertIn("&lt;b&gt;", html)

    def test_speaker_pill_falls_back_to_generic_name(self) -> None:
        # BlueZ returns an empty name for a device that has never been resolved.
        snapshot = self._snapshot(speaker_offline={"name": "", "kind": "bluetooth"})
        html = render_overlay_html(snapshot, self.theme)
        self.assertIn("<span>Speaker offline</span>", html)

    def test_speaker_pill_precedes_alarm_badge(self) -> None:
        # Ordering is load-bearing: a speaker that's off means the alarm won't be heard,
        # so the badge has to land ahead of the alarm badge, not after it.
        snapshot = self._snapshot(
            speaker_offline={"name": "Living Room", "kind": "bluetooth"},
            active_alarm={"label": "Wake up"},
        )
        html = render_overlay_html(snapshot, self.theme)
        self.assertLess(html.index(self._SPEAKER_PILL_MARKUP), html.index("Alarm ringing"))

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

    def test_set_ticker_bumps_only_on_symbol_change(self) -> None:
        manager = OverlayStateManager()
        v0 = manager.snapshot().version

        def q(symbol, price):
            return {"symbol": symbol, "label": symbol, "price": price, "change": 0.0, "change_pct": 0.0, "is_up": True}

        # First population -> bump (so the card refetches and the bar appears).
        c1 = manager.set_ticker([q("^SPX", 1.0)])
        self.assertTrue(c1.changed)
        v1 = manager.snapshot().version
        self.assertGreater(v1, v0)

        # Same symbols, new prices -> NO bump (avoids reloading the overlay every poll)...
        c2 = manager.set_ticker([q("^SPX", 2.0)])
        self.assertFalse(c2.changed)
        self.assertEqual(manager.snapshot().version, v1)
        # ...but the data is still updated for the next natural refresh.
        self.assertEqual(manager.snapshot().ticker[0]["price"], 2.0)

        # Symbol set changes -> bump again.
        c3 = manager.set_ticker([q("^SPX", 2.0), q("AAPL", 3.0)])
        self.assertTrue(c3.changed)
        self.assertGreater(manager.snapshot().version, v1)

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

    def test_pre_alarm_card_rendered_with_dismiss_button(self) -> None:
        snapshot = self._snapshot(
            active_alarm={
                "state": "pre_alarm",
                "minutes_until_fire": 20,
                "event": {"id": "alarm-xyz", "label": "Wake up", "time": "07:00"},
            },
        )
        html = render_overlay_html(snapshot, self.theme)
        self.assertIn("overlay-card--pre-alarm", html)
        self.assertIn("Alarm at 07:00 in 20 min", html)
        self.assertIn("data-dismiss-alarm", html)
        self.assertIn('data-event-id="alarm-xyz"', html)
        self.assertIn("data-keep-alarm", html)
        # Should NOT show the ringing buttons
        self.assertNotIn("Snooze 5 min", html)

    def test_pre_alarm_state_normalized_via_state_manager(self) -> None:
        manager = OverlayStateManager()
        change = manager.update_active_event(
            "alarm",
            {
                "state": "pre_alarm",
                "minutes_until_fire": 20,
                "event": {"id": "a1", "label": "Wake up"},
            },
        )
        self.assertTrue(change.changed)
        snapshot = manager.snapshot()
        assert snapshot.active_alarm is not None
        self.assertEqual(snapshot.active_alarm["state"], "pre_alarm")
        self.assertEqual(snapshot.active_alarm["minutes_until_fire"], 20)

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


class HelpInfoCardTests(unittest.TestCase):
    """Tests for help info card."""

    def test_build_help_info_overlay_structure(self) -> None:
        """Test that the overlay has correct HTML structure."""
        html = _build_help_info_overlay()
        self.assertIn("overlay-card", html)
        self.assertIn("overlay-info-card--help", html)
        self.assertIn("overlay-info-card__header", html)
        self.assertIn("overlay-info-card__body", html)

    def test_build_help_info_overlay_has_close_button(self) -> None:
        """Test that the help overlay has an accessible close button."""
        html = _build_help_info_overlay()
        self.assertIn("data-info-card-close", html)
        self.assertIn('aria-label="Close help"', html)

    def test_build_help_info_overlay_contains_all_sections(self) -> None:
        """Test that all 9 help sections are rendered."""
        html = _build_help_info_overlay()
        expected_sections = [
            "Ask Me Anything",
            "Alarms",
            "Timers",
            "Reminders",
            "Calendar",
            "Weather, News &amp; Sports",
            "Music Controls",
            "Smart Home",
            "On-Screen Controls",
        ]
        for section in expected_sections:
            self.assertIn(section, html)

    def test_build_help_info_overlay_contains_example_phrases(self) -> None:
        """Test that example voice commands appear in the output."""
        html = _build_help_info_overlay()
        self.assertIn("Set an alarm for 7:30 AM", html)
        self.assertIn("Set a 5 minute timer", html)
        self.assertIn("Pause the music", html)
        self.assertIn("Turn on the living room lights", html)

    def test_build_help_info_overlay_has_section_structure(self) -> None:
        """Test that sections use the expected CSS classes."""
        html = _build_help_info_overlay()
        self.assertIn("overlay-help-section", html)
        self.assertIn("overlay-help-section__title", html)
        self.assertIn("overlay-help-section__desc", html)
        self.assertIn("overlay-help-section__examples", html)

    def test_build_help_info_overlay_escapes_content(self) -> None:
        """Test that content is HTML-escaped (& in section titles)."""
        html = _build_help_info_overlay()
        # "Weather, News & Sports" should be escaped to &amp;
        self.assertIn("Weather, News &amp; Sports", html)

    def test_build_help_info_overlay_title(self) -> None:
        """Test that the card title is present."""
        html = _build_help_info_overlay()
        self.assertIn("What Can I Do?", html)
        self.assertIn("Say the wake word, then try any of these", html)

    def _snapshot(self, **overrides: object) -> OverlaySnapshot:
        data: dict[str, object] = {
            "version": 1,
            "clocks": (),
            "now_playing": "",
            "now_playing_state": "",
            "now_playing_image": "",
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

    def test_help_badge_in_notification_bar(self) -> None:
        """Test that the Help badge appears in the rendered notification bar."""
        theme = OverlayTheme(
            ambient_background="rgba(0,0,0,0.32)",
            alert_background="rgba(0,0,0,0.65)",
            text_color="#FFFFFF",
            accent_color="#88C0D0",
            show_notification_bar=True,
        )
        html = render_overlay_html(self._snapshot(), theme)
        self.assertIn("Help", html)
        self.assertIn('data-badge-action="show_help"', html)

    def test_help_info_card_renders_via_state(self) -> None:
        """Test that setting info_card type=help produces help content."""
        theme = OverlayTheme(
            ambient_background="rgba(0,0,0,0.32)",
            alert_background="rgba(0,0,0,0.65)",
            text_color="#FFFFFF",
            accent_color="#88C0D0",
            show_notification_bar=True,
        )
        html = render_overlay_html(self._snapshot(info_card={"type": "help"}), theme)
        self.assertIn("overlay-info-card--help", html)
        self.assertIn("Ask Me Anything", html)


class NowPlayingCardTests(unittest.TestCase):
    def _snapshot(self, **overrides) -> OverlaySnapshot:
        data = {
            "version": 1,
            "clocks": (),
            "now_playing": "",
            "now_playing_state": "",
            "now_playing_image": "",
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

    def test_returns_none_when_no_text(self) -> None:
        result = _build_now_playing_card(self._snapshot(now_playing=""))
        self.assertIsNone(result)

    def test_returns_card_with_controls(self) -> None:
        result = _build_now_playing_card(self._snapshot(now_playing="Artist — Song"))
        self.assertIsNotNone(result)
        position, html = result  # type: ignore[misc]
        self.assertEqual(position, "bottom-right")
        self.assertIn("overlay-now-playing__controls", html)
        self.assertIn('data-media-action="media_previous_track"', html)
        self.assertIn('data-media-action="media_next_track"', html)
        self.assertIn('data-media-action="media_stop"', html)

    def test_playing_state_shows_pause_button(self) -> None:
        result = _build_now_playing_card(self._snapshot(now_playing="Song", now_playing_state="playing"))
        _, html = result  # type: ignore[misc]
        self.assertIn('data-media-action="media_pause"', html)
        self.assertNotIn('data-media-action="media_play"', html)
        self.assertIn('aria-label="Pause"', html)
        self.assertNotIn("overlay-now-playing--paused", html)

    def test_paused_state_shows_play_button_and_paused_class(self) -> None:
        result = _build_now_playing_card(self._snapshot(now_playing="Song", now_playing_state="paused"))
        _, html = result  # type: ignore[misc]
        self.assertIn('data-media-action="media_play"', html)
        self.assertNotIn('data-media-action="media_pause"', html)
        self.assertIn('aria-label="Play"', html)
        self.assertIn("overlay-now-playing--paused", html)

    def test_idle_state_shows_play_button(self) -> None:
        result = _build_now_playing_card(self._snapshot(now_playing="Song", now_playing_state="idle"))
        _, html = result  # type: ignore[misc]
        self.assertIn('data-media-action="media_play"', html)
        self.assertNotIn('data-media-action="media_pause"', html)
        self.assertIn('aria-label="Play"', html)

    def test_body_text_is_escaped(self) -> None:
        result = _build_now_playing_card(self._snapshot(now_playing="<script>alert(1)</script>"))
        _, html = result  # type: ignore[misc]
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_primary_class_on_play_pause(self) -> None:
        result = _build_now_playing_card(self._snapshot(now_playing="Song", now_playing_state="playing"))
        _, html = result  # type: ignore[misc]
        self.assertIn("overlay-now-playing__control--primary", html)


class UpdateNowPlayingTests(unittest.TestCase):
    def test_first_update_is_changed(self) -> None:
        mgr = OverlayStateManager()
        change = mgr.update_now_playing("Artist — Song", state="playing")
        self.assertTrue(change.changed)
        self.assertEqual(change.reason, "now_playing")

    def test_same_text_and_state_not_changed(self) -> None:
        mgr = OverlayStateManager()
        mgr.update_now_playing("Song", state="playing")
        change = mgr.update_now_playing("Song", state="playing")
        self.assertFalse(change.changed)

    def test_state_change_triggers_update(self) -> None:
        mgr = OverlayStateManager()
        mgr.update_now_playing("Song", state="playing")
        change = mgr.update_now_playing("Song", state="paused")
        self.assertTrue(change.changed)

    def test_state_appears_in_snapshot(self) -> None:
        mgr = OverlayStateManager()
        mgr.update_now_playing("Song", state="paused")
        snap = mgr.snapshot()
        self.assertEqual(snap.now_playing, "Song")
        self.assertEqual(snap.now_playing_state, "paused")

    def test_empty_text_clears(self) -> None:
        mgr = OverlayStateManager()
        mgr.update_now_playing("Song", state="playing")
        change = mgr.update_now_playing("", state="")
        self.assertTrue(change.changed)
        snap = mgr.snapshot()
        self.assertEqual(snap.now_playing, "")
        self.assertEqual(snap.now_playing_state, "")

    def test_image_appears_in_snapshot(self) -> None:
        mgr = OverlayStateManager()
        mgr.update_now_playing("Song", state="playing", image="data:image/jpeg;base64,abc")
        snap = mgr.snapshot()
        self.assertEqual(snap.now_playing_image, "data:image/jpeg;base64,abc")

    def test_image_change_triggers_update(self) -> None:
        mgr = OverlayStateManager()
        mgr.update_now_playing("Song", state="playing", image="data:image/jpeg;base64,abc")
        change = mgr.update_now_playing("Song", state="playing", image="data:image/jpeg;base64,xyz")
        self.assertTrue(change.changed)


class NowPlayingAlbumArtRenderTests(NowPlayingCardTests):
    def test_image_rendered_when_present(self) -> None:
        result = _build_now_playing_card(
            self._snapshot(
                now_playing="Artist — Song",
                now_playing_state="playing",
                now_playing_image="data:image/jpeg;base64,abc",
            )
        )
        self.assertIsNotNone(result)
        _, html = result  # type: ignore[misc]
        self.assertIn('class="overlay-now-playing__art"', html)
        self.assertIn("data:image/jpeg;base64,abc", html)

    def test_no_image_when_empty(self) -> None:
        result = _build_now_playing_card(
            self._snapshot(
                now_playing="Artist — Song",
                now_playing_state="playing",
                now_playing_image="",
            )
        )
        self.assertIsNotNone(result)
        _, html = result  # type: ignore[misc]
        self.assertNotIn("overlay-now-playing__art", html)


class WeatherAlertOverlayTests(OverlayRenderTests):
    """Pill, banner, and card behavior for active NWS alerts."""

    def _alert(self, **overrides) -> dict:
        alert = {
            "id": "urn:oid:test.1",
            "event": "Tornado Warning",
            "tier": "warning",
            "severity": "extreme",
            "headline": "Tornado Warning issued by NWS Test",
            "description": "At 1052 AM EDT, a severe thunderstorm was\nlocated near Salem.\n\nHAZARD...Tornado.",
            "instruction": "TAKE COVER NOW!",
            "sender": "NWS Test",
            "area": "Testville",
            "onset": "",
            "ends": "",
            "expires": "",
            "first_seen": time.time(),
        }
        alert.update(overrides)
        return alert

    @staticmethod
    def _body(html: str) -> str:
        """Markup only. The document embeds the whole stylesheet AND overlay.js, both of
        which name every class, so asserting a class is ABSENT against the full document
        always fails."""
        return html.split("</style>", 1)[-1].split("<script", 1)[0]

    def _banner_theme(self, minutes: int = 15, rotate_seconds: int = 30) -> OverlayTheme:
        return OverlayTheme(
            ambient_background="rgba(0,0,0,0.32)",
            alert_background="rgba(0,0,0,0.65)",
            text_color="#FFFFFF",
            accent_color="#88C0D0",
            show_notification_bar=True,
            weather_alert_banner_minutes=minutes,
            weather_alert_rotate_seconds=rotate_seconds,
        )

    def test_no_pill_without_alerts(self) -> None:
        body = self._body(render_overlay_html(self._snapshot(), self.theme))
        self.assertNotIn("overlay-badge--weather-alert", body)
        self.assertNotIn("overlay-weather-banner", body)

    def test_pill_names_the_most_urgent_alert(self) -> None:
        # self.theme leaves the banner window at its default, so age the alert past it.
        aged = self._alert(first_seen=time.time() - 20 * 60)
        body = self._body(render_overlay_html(self._snapshot(weather_alerts=(aged,)), self._banner_theme(15)))
        self.assertIn("overlay-badge--weather-alert-warning", body)
        self.assertIn("Tornado Warning", body)
        self.assertIn('data-badge-action="show_weather_alerts"', body)

    def test_pill_counts_the_remaining_alerts(self) -> None:
        old = time.time() - 20 * 60
        alerts = (self._alert(first_seen=old), self._alert(id="b", event="Flood Watch", tier="watch", first_seen=old))
        body = self._body(render_overlay_html(self._snapshot(weather_alerts=alerts), self._banner_theme(15)))
        self.assertIn("Tornado Warning +1", body)

    def test_advisories_get_no_color_class(self) -> None:
        """Only watches and warnings are colored; an advisory keeps the neutral badge."""
        alert = self._alert(
            event="Heat Advisory", tier="advisory", severity="moderate", first_seen=time.time() - 20 * 60
        )
        body = self._body(render_overlay_html(self._snapshot(weather_alerts=(alert,)), self._banner_theme(15)))
        self.assertIn("overlay-badge--weather-alert-advisory", body)
        self.assertNotIn("overlay-badge--weather-alert-warning", body)

    def test_banner_shows_for_a_new_alert(self) -> None:
        body = self._body(render_overlay_html(self._snapshot(weather_alerts=(self._alert(),)), self._banner_theme()))
        self.assertIn("overlay-weather-banner--warning", body)

    def test_banner_gone_once_the_window_passes(self) -> None:
        aged = self._alert(first_seen=time.time() - 20 * 60)
        body = self._body(render_overlay_html(self._snapshot(weather_alerts=(aged,)), self._banner_theme(15)))
        self.assertNotIn("overlay-weather-banner", body)
        # ...but the alert itself is still on the bar for its whole life.
        self.assertIn("overlay-badge--weather-alert", body)

    def test_zero_minutes_disables_the_banner_entirely(self) -> None:
        body = self._body(render_overlay_html(self._snapshot(weather_alerts=(self._alert(),)), self._banner_theme(0)))
        self.assertNotIn("overlay-weather-banner", body)
        self.assertIn("overlay-badge--weather-alert", body)

    def test_banner_suppresses_the_pill_while_it_is_up(self) -> None:
        """Both at once is the same sentence twice; the pill's job starts when the banner retires."""
        body = self._body(render_overlay_html(self._snapshot(weather_alerts=(self._alert(),)), self._banner_theme()))
        self.assertIn("overlay-weather-banner", body)
        self.assertNotIn("overlay-badge--weather-alert", body)

    def test_multiple_alerts_rotate_with_a_position_counter(self) -> None:
        alerts = (self._alert(), self._alert(id="b", event="Flood Watch", tier="watch"))
        body = self._body(render_overlay_html(self._snapshot(weather_alerts=alerts), self._banner_theme()))
        self.assertIn('data-alert-rotate="30"', body)
        self.assertIn("1 of 2", body)
        self.assertIn("2 of 2", body)
        # Exactly one visible at a time, so the strip stays a single row.
        self.assertEqual(body.count("overlay-weather-banner--hidden"), 1)

    def test_rotation_off_renders_only_the_most_urgent(self) -> None:
        alerts = (self._alert(), self._alert(id="b", event="Flood Watch", tier="watch"))
        body = self._body(
            render_overlay_html(self._snapshot(weather_alerts=alerts), self._banner_theme(rotate_seconds=0))
        )
        self.assertNotIn("data-alert-rotate", body)
        self.assertNotIn("overlay-weather-banner--watch", body)
        # The counter still says how many the card behind it holds.
        self.assertIn("1 of 2", body)

    def test_single_alert_has_no_counter(self) -> None:
        body = self._body(render_overlay_html(self._snapshot(weather_alerts=(self._alert(),)), self._banner_theme()))
        self.assertNotIn("overlay-weather-banner__count", body)

    def test_always_mode_keeps_the_banner_for_the_alerts_whole_life(self) -> None:
        """The default: the strip is unobtrusive enough to leave up, so it stays."""
        ancient = self._alert(first_seen=time.time() - 3 * 24 * 3600)
        body = self._body(
            render_overlay_html(self._snapshot(weather_alerts=(ancient,)), self._banner_theme(BANNER_ALWAYS))
        )
        self.assertIn("overlay-weather-banner", body)
        self.assertNotIn("overlay-badge--weather-alert", body)

    def test_banner_surfaces_the_nws_hazard_line(self) -> None:
        """ "Special Weather Statement" alone tells a passing reader nothing."""
        alert = self._alert(
            event="Special Weather Statement",
            tier="statement",
            description="At 1121 AM EDT, radar was tracking storms.\n\nHAZARD...Wind gusts up to 40 mph.",
        )
        body = self._body(render_overlay_html(self._snapshot(weather_alerts=(alert,)), self._banner_theme()))
        self.assertIn("Wind gusts up to 40 mph", body)

    def test_banner_hazard_omitted_when_the_bulletin_has_no_tag(self) -> None:
        """The first sentence of the prose is worse than saying nothing."""
        alert = self._alert(description="At 1121 AM EDT, Doppler radar was tracking strong thunderstorms.")
        body = self._body(render_overlay_html(self._snapshot(weather_alerts=(alert,)), self._banner_theme()))
        self.assertNotIn("overlay-weather-banner__hazard", body)
        self.assertNotIn("Doppler radar", body)

    def test_long_hazard_is_truncated_to_keep_the_banner_one_line(self) -> None:
        hazard = (
            "Ping pong ball size hail and destructive wind gusts of up to eighty miles "
            "per hour, plus frequent cloud to ground lightning"
        )
        alert = self._alert(description=f"HAZARD...{hazard}.")
        body = self._body(render_overlay_html(self._snapshot(weather_alerts=(alert,)), self._banner_theme()))
        self.assertIn("\u2026", body)
        self.assertNotIn(hazard, body)

    def test_card_renders_description_and_instruction(self) -> None:
        html = render_overlay_html(
            self._snapshot(weather_alerts=(self._alert(),), info_card={"type": "weather_alerts"}),
            self.theme,
        )
        self.assertIn("overlay-info-card--weather-alerts", html)
        # NWS hard-wraps at ~68 columns; single newlines rejoin into flowing paragraphs.
        self.assertIn("thunderstorm was located near Salem", html)
        self.assertIn("HAZARD...Tornado.", html)
        self.assertIn("TAKE COVER NOW!", html)

    def test_card_opens_the_alert_that_was_clicked(self) -> None:
        """Tapping the banner showing alert 2 must not open alert 1."""
        alerts = (self._alert(), self._alert(id="b", event="Flood Watch", tier="watch"))
        body = self._body(
            render_overlay_html(
                self._snapshot(weather_alerts=alerts, info_card={"type": "weather_alerts", "index": 1}),
                self.theme,
            )
        )
        self.assertIn("Flood Watch", body)
        self.assertIn("2 of 2", body)
        # One alert at a time — eight NWS bulletins in one card is a scroll nobody reads.
        self.assertNotIn('overlay-alert__event">Tornado Warning', body)

    def test_each_banner_carries_its_own_index(self) -> None:
        alerts = (self._alert(), self._alert(id="b", event="Flood Watch", tier="watch"))
        body = self._body(render_overlay_html(self._snapshot(weather_alerts=alerts), self._banner_theme()))
        self.assertIn('data-alert-index="0"', body)
        self.assertIn('data-alert-index="1"', body)

    def test_card_nav_wraps_at_both_ends(self) -> None:
        alerts = tuple(self._alert(id=str(n), event=f"Alert {n}") for n in range(3))
        body = self._body(
            render_overlay_html(
                self._snapshot(weather_alerts=alerts, info_card={"type": "weather_alerts", "index": 0}),
                self.theme,
            )
        )
        self.assertIn("overlay-alert-nav", body)
        # Prev from the first wraps to the last rather than offering a dead button.
        self.assertIn('data-alert-index="2"', body)
        self.assertIn('data-alert-index="1"', body)

    def test_card_index_past_the_end_clamps(self) -> None:
        """Alerts expire while a card is open; a stale index must not jump to the top."""
        alerts = (self._alert(), self._alert(id="b", event="Flood Watch", tier="watch"))
        body = self._body(
            render_overlay_html(
                self._snapshot(weather_alerts=alerts, info_card={"type": "weather_alerts", "index": 9}),
                self.theme,
            )
        )
        self.assertIn("Flood Watch", body)

    def test_single_alert_card_has_no_nav(self) -> None:
        body = self._body(
            render_overlay_html(
                self._snapshot(weather_alerts=(self._alert(),), info_card={"type": "weather_alerts"}),
                self.theme,
            )
        )
        self.assertNotIn("overlay-alert-nav", body)

    def test_card_reads_live_alerts_not_the_card_payload(self) -> None:
        """An open card must track the poll rather than freeze at tap time."""
        html = render_overlay_html(
            self._snapshot(weather_alerts=(), info_card={"type": "weather_alerts"}),
            self.theme,
        )
        self.assertIn("No active weather alerts.", html)

    def test_until_falls_back_to_expires_with_a_different_verb(self) -> None:
        """`ends` is when the weather stops; `expires` is when the bulletin lapses."""
        ends = datetime.now(UTC) + timedelta(hours=1)
        with_ends = self._alert(ends=ends.isoformat(), expires=ends.isoformat())
        body = self._body(render_overlay_html(self._snapshot(weather_alerts=(with_ends,)), self._banner_theme()))
        self.assertIn("until ", body)
        self.assertNotIn("expires ", body)

        without_ends = self._alert(ends="", expires=ends.isoformat())
        body = self._body(render_overlay_html(self._snapshot(weather_alerts=(without_ends,)), self._banner_theme()))
        self.assertIn("expires ", body)

    def test_card_and_banner_agree_with_the_device_clock_format(self) -> None:
        """A 24h display must not get AM/PM smuggled back in via the card."""
        ends = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        # Description deliberately free of clock text — the fixture's "1052 AM EDT" prose
        # is NWS's own wording, not the formatted time this test is about.
        snapshot = self._snapshot(
            weather_alerts=(self._alert(ends=ends, description="HAZARD...Tornado."),),
            info_card={"type": "weather_alerts"},
        )
        body = self._body(render_overlay_html(snapshot, self._banner_theme(), clock_hour12=False))
        self.assertNotIn("AM", body)
        self.assertNotIn("PM", body)

        body = self._body(render_overlay_html(snapshot, self._banner_theme(), clock_hour12=True))
        self.assertEqual(body.count("until "), 2)  # banner and card, both 12-hour
        self.assertTrue("AM" in body or "PM" in body)

    def test_no_time_phrase_when_nws_gives_neither(self) -> None:
        body = self._body(render_overlay_html(self._snapshot(weather_alerts=(self._alert(),)), self._banner_theme()))
        self.assertNotIn("overlay-weather-banner__until", body)


class WeatherAlertStateTests(unittest.TestCase):
    def _alert(self, alert_id: str = "a", **overrides) -> dict:
        alert = {"id": alert_id, "event": "Tornado Warning", "tier": "warning", "first_seen": time.time()}
        alert.update(overrides)
        return alert

    def test_first_alert_bumps_the_version(self) -> None:
        mgr = OverlayStateManager()
        change = mgr.set_weather_alerts([self._alert()], banner_minutes=15)
        self.assertTrue(change.changed)
        self.assertEqual(len(mgr.snapshot().weather_alerts), 1)

    def test_reissued_identical_alert_does_not_bump(self) -> None:
        """NWS reissues every few minutes; bumping would reload the photo card each time."""
        mgr = OverlayStateManager()
        alert = self._alert()
        mgr.set_weather_alerts([alert], banner_minutes=15)
        reissued = {**alert, "expires": "2026-08-21T23:00:00-04:00", "description": "updated text"}
        self.assertFalse(mgr.set_weather_alerts([reissued], banner_minutes=15).changed)

    def test_new_alert_bumps(self) -> None:
        mgr = OverlayStateManager()
        mgr.set_weather_alerts([self._alert("a")], banner_minutes=15)
        self.assertTrue(mgr.set_weather_alerts([self._alert("a"), self._alert("b")], banner_minutes=15).changed)

    def test_banner_expiry_bumps(self) -> None:
        """The poll that crosses the banner window has to push a refresh."""
        mgr = OverlayStateManager()
        fresh = self._alert(first_seen=time.time())
        mgr.set_weather_alerts([fresh], banner_minutes=15)
        aged = {**fresh, "first_seen": time.time() - 20 * 60}
        self.assertTrue(mgr.set_weather_alerts([aged], banner_minutes=15).changed)

    def test_clearing_alerts_bumps(self) -> None:
        mgr = OverlayStateManager()
        mgr.set_weather_alerts([self._alert()], banner_minutes=15)
        self.assertTrue(mgr.set_weather_alerts([], banner_minutes=15).changed)


if __name__ == "__main__":
    unittest.main()
