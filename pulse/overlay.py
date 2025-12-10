"""Shared overlay state helpers for PulseOS."""

from __future__ import annotations

import base64
import copy
import json
import threading
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from html import escape as html_escape
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pulse.assistant.schedule_service import parse_day_tokens
from pulse.overlay_assets import OVERLAY_CSS, OVERLAY_JS

DEFAULT_FONT_STACK = '"Inter", "Segoe UI", "Helvetica Neue", sans-serif, "Noto Color Emoji"'
DEFAULT_CALENDAR_LOOKAHEAD_HOURS = 72
WEATHER_ICON_DIR = Path(__file__).resolve().parent.parent / "assets" / "weather" / "icons"


@dataclass(frozen=True)
class ClockConfig:
    """Definition for a single clock overlay slot."""

    key: str
    label: str
    timezone: str | None


@dataclass(frozen=True)
class OverlaySnapshot:
    """Immutable view of the overlay state for rendering."""

    version: int
    clocks: tuple[ClockConfig, ...]
    now_playing: str
    timers: tuple[dict[str, Any], ...]
    alarms: tuple[dict[str, Any], ...]
    reminders: tuple[dict[str, Any], ...]
    calendar_events: tuple[dict[str, Any], ...]
    active_alarm: dict[str, Any] | None
    active_timer: dict[str, Any] | None
    active_reminder: dict[str, Any] | None
    notifications: tuple[dict[str, Any], ...]
    timer_positions: dict[str, str]
    info_card: dict[str, Any] | None
    last_reason: str
    generated_at: float
    schedule_snapshot: dict[str, Any] | None
    earmuffs_enabled: bool
    update_available: bool


@dataclass(frozen=True)
class OverlayChange:
    """Result metadata from a state mutation."""

    changed: bool
    version: int
    reason: str


def parse_clock_config(
    spec: str | None,
    *,
    default_label: str,
    log: Callable[[str], None] | None = None,
) -> tuple[ClockConfig, ...]:
    """Parse the overlay clock specification from an environment string.

    Only a single clock is supported. Format: `local` (or `system`) for the kiosk's
    local timezone, or `timezone=Custom Label` for a specific timezone with a label.
    If multiple entries are provided, only the first one is used.
    """

    def _log(message: str) -> None:
        if log:
            log(f"overlay: {message}")

    entries: list[tuple[str | None, str]] = []
    seen_local = False
    tokens: Iterable[str] = []
    if spec:
        tokens = (token.strip() for token in spec.split(","))
    for token in tokens:
        if not token:
            continue
        if "=" in token:
            zone, label = token.split("=", 1)
        else:
            zone, label = token, ""
        zone = zone.strip()
        label = label.strip()
        if not zone:
            continue
        if zone.lower() in {"local", "system"}:
            tz_name = None
            seen_local = True
            label = label or default_label
        else:
            tz_name = zone
            label = label or zone
            if not _is_timezone_valid(tz_name):
                _log(f"skipping invalid timezone '{zone}' in PULSE_OVERLAY_CLOCK")
                continue
        entries.append((tz_name, label))
        # Only use the first entry (single clock support)
        break
    if not seen_local:
        entries.insert(0, (None, default_label))
    # Ensure we have exactly one clock
    final_entry = entries[0] if entries else (None, default_label)
    config = ClockConfig(key="clock0", label=final_entry[1], timezone=final_entry[0])
    return (config,)


class OverlayStateManager:
    """Thread-safe container for overlay state and change detection."""

    def __init__(self, clocks: Sequence[ClockConfig] | None = None) -> None:
        self._lock = threading.Lock()
        self._clocks = tuple(clocks) if clocks else (ClockConfig("clock0", "Local", None),)
        self._timers: tuple[dict[str, Any], ...] = ()
        self._alarms: tuple[dict[str, Any], ...] = ()
        self._reminders: tuple[dict[str, Any], ...] = ()
        self._calendar_events: tuple[dict[str, Any], ...] = ()
        self._active_alarm: dict[str, Any] | None = None
        self._active_timer: dict[str, Any] | None = None
        self._active_reminder: dict[str, Any] | None = None
        self._notifications: tuple[dict[str, Any], ...] = ()
        self._schedule_snapshot: dict[str, Any] | None = None
        self._now_playing = ""
        self._info_card: dict[str, Any] | None = None
        self._timer_position_history: dict[str, str] = {}
        self._earmuffs_enabled = False
        self._update_available = False
        self._version = 0
        self._last_reason = "init"
        self._last_updated = time.time()
        self._signatures = {
            "timers": "",
            "alarms": "",
            "reminders": "",
            "calendar_events": "",
            "active_alarm": "",
            "active_timer": "",
            "active_reminder": "",
            "notifications": "",
            "schedule_snapshot": "",
            "now_playing": "",
            "info_card": "",
            "earmuffs_enabled": "",
            "update_available": "",
        }

    @property
    def clocks(self) -> tuple[ClockConfig, ...]:
        return self._clocks

    def configure_clock(self, clocks: Sequence[ClockConfig]) -> OverlayChange:
        new_clocks = tuple(clocks) if clocks else self._clocks
        with self._lock:
            if new_clocks == self._clocks:
                return OverlayChange(False, self._version, "clock")
            self._clocks = new_clocks
            return self._bump("clock")

    def update_now_playing(self, text: str) -> OverlayChange:
        normalized = text.strip()
        with self._lock:
            if normalized == self._now_playing:
                return OverlayChange(False, self._version, "now_playing")
            self._now_playing = normalized
            self._signatures["now_playing"] = normalized
            return self._bump("now_playing")

    def update_schedule_snapshot(self, snapshot: dict[str, Any]) -> OverlayChange:
        timers = _coerce_dict_list(snapshot.get("timers"))
        alarms = _coerce_dict_list(snapshot.get("alarms"))
        reminders = _coerce_dict_list(snapshot.get("reminders"))
        calendar_events = _coerce_dict_list(snapshot.get("calendar_events"))
        snapshot_signature = _signature(snapshot)
        timer_signature = _signature(timers)
        alarm_signature = _signature(alarms)
        reminder_signature = _signature(reminders)
        calendar_signature = _signature(calendar_events)
        changed = False
        with self._lock:
            if timer_signature != self._signatures["timers"]:
                self._timers = tuple(copy.deepcopy(item) for item in timers)
                self._signatures["timers"] = timer_signature
                reserved_slots = 0
                if self._active_timer:
                    reserved_slots += 1
                if self._active_alarm:
                    reserved_slots += 1
                new_positions = _compute_timer_positions(self._timers, reserved_slots=reserved_slots)
                self._refresh_timer_positions(new_positions)
                changed = True
            if alarm_signature != self._signatures["alarms"]:
                self._alarms = tuple(copy.deepcopy(item) for item in alarms)
                self._signatures["alarms"] = alarm_signature
                changed = True
            if reminder_signature != self._signatures["reminders"]:
                self._reminders = tuple(copy.deepcopy(item) for item in reminders)
                self._signatures["reminders"] = reminder_signature
                changed = True
            if calendar_signature != self._signatures["calendar_events"]:
                self._calendar_events = tuple(copy.deepcopy(item) for item in calendar_events)
                self._signatures["calendar_events"] = calendar_signature
                changed = True
            if snapshot_signature != self._signatures["schedule_snapshot"]:
                self._schedule_snapshot = copy.deepcopy(snapshot)
                self._signatures["schedule_snapshot"] = snapshot_signature
                changed = True
            if not changed:
                return OverlayChange(False, self._version, "schedules")
            return self._bump("schedules")

    def update_active_event(self, event_type: str, payload: dict[str, Any] | None) -> OverlayChange:
        normalized = _normalize_active_payload(payload)
        signature = _signature(normalized)
        if event_type == "alarm":
            field = "active_alarm"
        elif event_type == "timer":
            field = "active_timer"
        else:
            field = "active_reminder"
        previous_timer = self._active_timer if event_type == "timer" else None
        with self._lock:
            if signature == self._signatures[field]:
                return OverlayChange(False, self._version, field)
            if event_type == "alarm":
                self._active_alarm = normalized
            elif event_type == "timer":
                self._active_timer = normalized
                if normalized is None and previous_timer:
                    prev_id = _extract_event_id(previous_timer)
                    if prev_id:
                        self._timer_position_history.pop(prev_id, None)
            else:
                self._active_reminder = normalized
            self._signatures[field] = signature
            return self._bump(field)

    def update_notifications(self, notifications: Sequence[dict[str, Any]]) -> OverlayChange:
        normalized = tuple(copy.deepcopy(item) for item in notifications if isinstance(item, dict))
        signature = _signature(normalized)
        with self._lock:
            if signature == self._signatures["notifications"]:
                return OverlayChange(False, self._version, "notifications")
            self._notifications = normalized
            self._signatures["notifications"] = signature
            return self._bump("notifications")

    def _refresh_timer_positions(self, new_positions: dict[str, str]) -> None:
        active_timer_id = _extract_event_id(self._active_timer)
        refreshed: dict[str, str] = {}
        if active_timer_id and active_timer_id in self._timer_position_history:
            refreshed[active_timer_id] = self._timer_position_history[active_timer_id]
        refreshed.update(new_positions)
        self._timer_position_history = refreshed

    def update_info_card(self, card: dict[str, Any] | None) -> OverlayChange:
        normalized: dict[str, Any] | None = None
        if isinstance(card, dict):
            text = str(card.get("text") or "").strip()
            category = str(card.get("category") or "").strip()
            title = str(card.get("title") or "").strip()
            raw_card_type = str(card.get("type") or "").strip()
            card_type = raw_card_type.lower()
            state = str(card.get("state") or "").strip()
            normalized = {}
            if text:
                normalized["text"] = text
            if category:
                normalized["category"] = category
            if title:
                normalized["title"] = title
            if card_type:
                normalized["type"] = card_type
            if state:
                normalized["state"] = state.lower()
            ts_value = card.get("ts")
            if ts_value is not None:
                try:
                    normalized["ts"] = float(ts_value)
                except (TypeError, ValueError):
                    pass
            alarms_payload = card.get("alarms")
            if isinstance(alarms_payload, list):
                alarms_list = [copy.deepcopy(item) for item in alarms_payload if isinstance(item, dict)]
                if alarms_list:
                    normalized["alarms"] = alarms_list
            sounds_payload = card.get("sounds")
            if isinstance(sounds_payload, list):
                sounds_list = [copy.deepcopy(item) for item in sounds_payload if isinstance(item, dict)]
                if sounds_list:
                    normalized["sounds"] = sounds_list
            defaults_payload = card.get("defaults")
            if isinstance(defaults_payload, dict) and defaults_payload:
                normalized["defaults"] = copy.deepcopy(defaults_payload)
            events_payload = card.get("events")
            if isinstance(events_payload, list):
                events_list = [copy.deepcopy(item) for item in events_payload if isinstance(item, dict)]
                if events_list:
                    normalized["events"] = events_list
            if card_type == "weather":
                days_payload = card.get("days")
                if isinstance(days_payload, list):
                    day_entries = [
                        {
                            "label": str(day.get("label") or ""),
                            "high": day.get("high"),
                            "low": day.get("low"),
                            "precip": day.get("precip"),
                            "icon": day.get("icon"),
                        }
                        for day in days_payload
                        if isinstance(day, dict)
                    ]
                    if day_entries:
                        normalized["days"] = day_entries
                subtitle_value = card.get("subtitle")
                if subtitle_value:
                    normalized["subtitle"] = str(subtitle_value)
                units_value = card.get("units")
                if units_value is not None:
                    normalized["units"] = str(units_value)
                current_payload = card.get("current")
                if isinstance(current_payload, dict):
                    normalized["current"] = {
                        "label": str(current_payload.get("label") or ""),
                        "temp": current_payload.get("temp"),
                        "units": current_payload.get("units"),
                        "description": current_payload.get("description"),
                        "icon": current_payload.get("icon"),
                    }
            if not normalized:
                normalized = None
        signature = _signature(normalized)
        with self._lock:
            if signature == self._signatures["info_card"]:
                return OverlayChange(False, self._version, "info_card")
            self._info_card = normalized
            self._signatures["info_card"] = signature
            return self._bump("info_card")

    def update_earmuffs_enabled(self, enabled: bool) -> OverlayChange:
        signature = str(enabled)
        with self._lock:
            if signature == self._signatures["earmuffs_enabled"]:
                return OverlayChange(False, self._version, "earmuffs_enabled")
            self._earmuffs_enabled = enabled
            self._signatures["earmuffs_enabled"] = signature
            return self._bump("earmuffs_enabled")

    def update_update_available(self, available: bool) -> OverlayChange:
        signature = str(available)
        with self._lock:
            if signature == self._signatures["update_available"]:
                return OverlayChange(False, self._version, "update_available")
            self._update_available = available
            self._signatures["update_available"] = signature
            return self._bump("update_available")

    def snapshot(self) -> OverlaySnapshot:
        with self._lock:
            return OverlaySnapshot(
                version=self._version,
                clocks=self._clocks,
                now_playing=self._now_playing,
                timers=tuple(copy.deepcopy(item) for item in self._timers),
                alarms=tuple(copy.deepcopy(item) for item in self._alarms),
                reminders=tuple(copy.deepcopy(item) for item in self._reminders),
                calendar_events=tuple(copy.deepcopy(item) for item in self._calendar_events),
                active_alarm=copy.deepcopy(self._active_alarm),
                active_timer=copy.deepcopy(self._active_timer),
                active_reminder=copy.deepcopy(self._active_reminder),
                notifications=tuple(copy.deepcopy(item) for item in self._notifications),
                timer_positions=copy.deepcopy(self._timer_position_history),
                info_card=copy.deepcopy(self._info_card),
                last_reason=self._last_reason,
                generated_at=time.time(),
                schedule_snapshot=copy.deepcopy(self._schedule_snapshot),
                earmuffs_enabled=self._earmuffs_enabled,
                update_available=self._update_available,
            )

    def _bump(self, reason: str) -> OverlayChange:
        self._version += 1
        self._last_reason = reason
        self._last_updated = time.time()
        return OverlayChange(True, self._version, reason)


def _signature(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    except (TypeError, ValueError):
        return repr(value)


def _coerce_dict_list(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def _normalize_active_payload(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    state = str(payload.get("state") or "").lower()
    if state not in {"ringing", "active"}:
        return None
    normalized: dict[str, Any] = {"state": state}
    event = payload.get("event")
    if isinstance(event, dict):
        normalized["event"] = copy.deepcopy(event)
    return normalized


def _is_timezone_valid(zone_name: str) -> bool:
    try:
        ZoneInfo(zone_name)
        return True
    except ZoneInfoNotFoundError:
        return False


@dataclass(frozen=True)
class OverlayTheme:
    """Styling knobs for the rendered overlay."""

    ambient_background: str
    alert_background: str
    text_color: str
    accent_color: str
    show_notification_bar: bool = True
    font_family: str = DEFAULT_FONT_STACK


CELL_ORDER = (
    "top-left",
    "top-center",
    "top-right",
    "middle-left",
    "center",
    "middle-right",
    "bottom-left",
    "bottom-center",
    "bottom-right",
)

CLOCK_POSITION = "bottom-left"

INFO_CARD_BLOCKED_CELLS = {
    "top-center",
    "top-right",
    "center",
    "middle-right",
    "bottom-center",
    "bottom-right",
}

TIMER_POSITION_MAP = {
    1: ("center",),
    2: ("middle-left", "middle-right"),
    3: ("middle-left", "middle-right", "top-center"),
    4: ("top-left", "top-right", "middle-left", "middle-right"),
}

ICON_MAP = {
    "config": "&#9881;",  # ⚙️
    "alarm": "&#128276;",  # 🔔
    "alarm_ringing": "&#128276;",
    "timer": "&#9201;",
    "music": "&#9835;",
    "reminder": "&#128221;",
    "calendar": "&#128197;",
    "earmuffs": "&#127911;",  # 🎧
    "update": "&#128260;",  # 🔄
    "sound": "&#127925;",  # 🎵
}


def _pick_available_cell(occupied: set[str], preferred: Sequence[str], fallback: str) -> str:
    for cell in preferred:
        if cell not in occupied:
            return cell
    return fallback


def render_overlay_html(
    snapshot: OverlaySnapshot,
    theme: OverlayTheme,
    *,
    clock_hour12: bool = True,
    stop_endpoint: str | None = None,
    info_endpoint: str | None = None,
) -> str:
    """Render the overlay snapshot into an HTML document."""

    cells: dict[str, list[str]] = {cell: [] for cell in CELL_ORDER}
    occupied_cells: set[str] = set()

    def _add_card(cell: str, markup: str) -> None:
        cells[cell].append(markup)
        occupied_cells.add(cell)

    for cell, card in _build_clock_card(snapshot):
        _add_card(cell, card)
    for cell, card in _build_timer_cards(snapshot):
        _add_card(cell, card)
    for cell, card in _build_active_event_cards(snapshot, occupied_cells):
        _add_card(cell, card)
    now_playing_card = _build_now_playing_card(snapshot)
    if now_playing_card and now_playing_card[0] not in occupied_cells:
        _add_card(now_playing_card[0], now_playing_card[1])

    info_card_markup = ""
    if snapshot.info_card:
        candidate = _build_info_overlay(snapshot)
        if candidate:
            # Clear blocked cells before rendering to prevent visual artifacts
            for cell in INFO_CARD_BLOCKED_CELLS:
                cells[cell] = []
                occupied_cells.add(cell)
            info_card_markup = candidate

    # Only render cells that have content to avoid empty cell artifacts
    grid_markup = "".join(
        f'<div class="overlay-cell cell-{cell}" data-cell="{cell}">{"".join(cards)}</div>'
        for cell, cards in cells.items()
        if cards  # Only render cells with actual content
    )
    if info_card_markup:
        grid_markup += info_card_markup

    notification_html = _build_notification_bar(snapshot) if theme.show_notification_bar else ""

    stop_endpoint = stop_endpoint or "/overlay/stop"
    info_endpoint = info_endpoint or "/overlay/info-card"
    stop_endpoint_attr = html_escape(stop_endpoint, quote=True)
    info_endpoint_attr = html_escape(info_endpoint, quote=True)
    root_attrs = (
        f'id="pulse-overlay-root" '
        f'class="overlay-root" '
        f'data-version="{snapshot.version}" '
        f'data-generated-at="{int(snapshot.generated_at * 1000)}" '
        f'data-clock-hour12="{"true" if clock_hour12 else "false"}" '
        f'data-stop-endpoint="{stop_endpoint_attr}" '
        f'data-info-endpoint="{info_endpoint_attr}"'
    )

    css_block = f"{_theme_css(theme)}\n{OVERLAY_CSS}"
    html_document = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<style>
{css_block}
</style>
</head>
<body>
<div {root_attrs}>
{notification_html}
<div class="overlay-grid">
{grid_markup}
</div>
</div>
<script>
{OVERLAY_JS}
</script>
</body>
</html>
"""
    return html_document


def _theme_css(theme: OverlayTheme) -> str:
    return (
        ":root {\n"
        f"  --overlay-text-color: {theme.text_color};\n"
        f"  --overlay-ambient-bg: {theme.ambient_background};\n"
        f"  --overlay-alert-bg: {theme.alert_background};\n"
        f"  --overlay-accent-color: {theme.accent_color};\n"
        f"  --overlay-font-family: {theme.font_family};\n"
        "}"
    )


def _build_clock_card(snapshot: OverlaySnapshot) -> list[tuple[str, str]]:
    clocks = snapshot.clocks or ()
    if not clocks:
        return []
    # Only support single clock
    clock = clocks[0]
    tz_attr = clock.timezone or ""
    label = html_escape(clock.label or "Clock")
    tz_attr_escaped = html_escape(tz_attr, quote=True)
    card = f"""
<div class="overlay-card overlay-card--clock" data-clock data-tz="{tz_attr_escaped}">
  <div class="overlay-card__title">{label}</div>
  <div class="overlay-clock__time" data-clock-time>--:--</div>
  <div class="overlay-clock__date" data-clock-date></div>
</div>
""".strip()
    return [(CLOCK_POSITION, card)]


def _build_timer_cards(snapshot: OverlaySnapshot) -> list[tuple[str, str]]:
    entries = _extract_active_timers(snapshot)
    if not entries:
        return []
    count = min(len(entries), max(TIMER_POSITION_MAP))
    positions = TIMER_POSITION_MAP.get(count, TIMER_POSITION_MAP[max(TIMER_POSITION_MAP)])
    cards: list[tuple[str, str]] = []
    for idx, entry in enumerate(entries[: len(positions)]):
        position = positions[idx]
        label = html_escape(entry["label"])
        target_ms = int(entry["target"] * 1000)
        card = f"""
<div class="overlay-card overlay-card--alert overlay-card--timer" data-timer data-target-ms="{target_ms}">
  <div class="overlay-card__title">{label}</div>
  <div class="overlay-timer__remaining" data-timer-remaining>00:00</div>
</div>
""".strip()
        cards.append((position, card))
    return cards


def _build_active_event_cards(snapshot: OverlaySnapshot, occupied_cells: set[str]) -> list[tuple[str, str]]:
    cards: list[tuple[str, str]] = []
    timer_positions = snapshot.timer_positions or {}
    ringing_cards: list[tuple[str, str | None]] = []
    if snapshot.active_alarm:
        ringing_cards.append((_build_alarm_ringing_card(snapshot.active_alarm), "center"))
    if snapshot.active_timer:
        event_data = snapshot.active_timer.get("event") if isinstance(snapshot.active_timer, dict) else None
        event_id = (
            str(event_data.get("id")) if isinstance(event_data, dict) and event_data.get("id") is not None else None
        )
        preferred_cell = timer_positions.get(event_id) if event_id else None
        ringing_cards.append((_build_timer_ringing_card(snapshot.active_timer), preferred_cell))
    if snapshot.active_reminder:
        label = _event_label(snapshot.active_reminder, default="Reminder")
        event_data = snapshot.active_reminder.get("event") if isinstance(snapshot.active_reminder, dict) else None
        event_id = event_data.get("id") if isinstance(event_data, dict) else None
        message = ""
        allow_delay = True
        button_label = "Complete"
        calendar_hint = None
        if isinstance(event_data, dict):
            metadata = event_data.get("metadata") or {}
            reminder_meta = metadata.get("reminder") if isinstance(metadata, dict) else {}
            message = str(reminder_meta.get("message") or event_data.get("label") or "Reminder")
            if isinstance(metadata, dict):
                calendar_hint = metadata.get("calendar")
        if isinstance(calendar_hint, dict):
            allow_delay = bool(calendar_hint.get("allow_delay", True))
            if not allow_delay:
                button_label = "OK"
        button_html = ""
        if event_id:
            event_id_escaped = html_escape(str(event_id), quote=True)
            primary_button = (
                f'<button class="overlay-button overlay-button--primary" '
                f'data-complete-reminder data-event-id="{event_id_escaped}">{button_label}</button>'
            )
            if allow_delay:
                button_html = (
                    f'<div class="overlay-reminder__actions">'
                    f"{primary_button}"
                    f'<div class="overlay-reminder__delays">'
                    f'<button class="overlay-button" data-delay-reminder data-delay-seconds="3600" '
                    f'data-event-id="{event_id_escaped}">+1h</button>'
                    f'<button class="overlay-button" data-delay-reminder data-delay-seconds="86400" '
                    f'data-event-id="{event_id_escaped}">+1d</button>'
                    f'<button class="overlay-button" data-delay-reminder data-delay-seconds="604800" '
                    f'data-event-id="{event_id_escaped}">+1w</button>'
                    f"</div></div>"
                )
            else:
                button_html = f'<div class="overlay-reminder__actions">{primary_button}</div>'
        body_text = html_escape(message or label)
        preferred_cells = ("top-center", "middle-right", "bottom-center")
        reminder_cell = _pick_available_cell(occupied_cells, preferred_cells, "top-center")
        occupied_cells.add(reminder_cell)
        cards.append(
            (
                reminder_cell,
                f"""
<div class="overlay-card overlay-card--alert overlay-card--reminder">
  <div class="overlay-card__title">{html_escape(label)}</div>
  <div class="overlay-card__body--reminder">{body_text}</div>
  {button_html}
</div>
""".strip(),
            )
        )
    ringing_layout = _allocate_ringing_cells(occupied_cells, [preferred for _, preferred in ringing_cards])
    for (card_html, _), target_cell in zip(ringing_cards, ringing_layout, strict=False):
        cards.append((target_cell, card_html))
        occupied_cells.add(target_cell)
    return cards


def _build_alarm_ringing_card(active_alarm: dict[str, Any]) -> str:
    label = _event_label(active_alarm, default="Alarm ringing")
    event_data = active_alarm.get("event") if isinstance(active_alarm, dict) else None
    event_id = event_data.get("id") if isinstance(event_data, dict) else None
    button_html = ""
    if event_id:
        event_id_escaped = html_escape(str(event_id), quote=True)
        stop_button = (
            f'<button class="overlay-button overlay-button--primary" data-stop-timer '
            f'data-event-id="{event_id_escaped}">Stop</button>'
        )
        snooze_button = (
            f'<button class="overlay-button" data-snooze-alarm data-event-id="{event_id_escaped}" '
            f'data-snooze-minutes="5">Snooze 5 min</button>'
        )
        button_html = (
            '<div class="overlay-card__actions overlay-card__actions--split">' f"{stop_button}{snooze_button}" "</div>"
        )
    return f"""
<div class="overlay-card overlay-card--alert overlay-card--ringing">
  <div class="overlay-card__title">{html_escape(label)}</div>
  {button_html}
</div>
""".strip()


def _build_timer_ringing_card(active_timer: dict[str, Any]) -> str:
    label = _event_label(active_timer, default="Timer complete")
    event_data = active_timer.get("event") if isinstance(active_timer, dict) else None
    event_id = event_data.get("id") if isinstance(event_data, dict) else None
    button_html = ""
    if event_id:
        event_id_escaped = html_escape(str(event_id), quote=True)
        stop_button = (
            f'<button class="overlay-button overlay-button--primary" data-stop-timer '
            f'data-event-id="{event_id_escaped}">Stop</button>'
        )
        button_html = f'<div class="overlay-card__actions">{stop_button}</div>'
    return f"""
<div class="overlay-card overlay-card--alert overlay-card--ringing">
  <div class="overlay-card__title">{html_escape(label)}</div>
  {button_html}
</div>
""".strip()


def _allocate_ringing_cells(occupied_cells: set[str], preferred: list[str | None]) -> list[str]:
    count = len(preferred)
    if count <= 0:
        return []
    layouts = {
        1: ("center",),
        2: ("top-center", "bottom-center", "center"),
        3: ("top-center", "bottom-center", "center", "middle-right"),
    }
    fallback_order = list(
        layouts.get(
            count,
            (
                "top-center",
                "bottom-center",
                "center",
                "middle-right",
                "top-left",
                "top-right",
                "bottom-left",
                "bottom-right",
            ),
        )
    )
    assignments: list[str | None] = []
    occupied = set(occupied_cells)
    for pref in preferred:
        if pref and pref not in occupied:
            assignments.append(pref)
            occupied.add(pref)
        else:
            assignments.append(None)
    fallback_iter = iter(fallback_order)
    for idx, cell in enumerate(assignments):
        if cell:
            continue
        chosen = None
        while True:
            try:
                candidate = next(fallback_iter)
            except StopIteration:
                candidate = "center"
            if candidate == "center" or candidate not in occupied:
                chosen = candidate
                break
        assignments[idx] = chosen
        occupied.add(chosen)
    return [cell or "center" for cell in assignments]


def _build_now_playing_card(snapshot: OverlaySnapshot) -> tuple[str, str] | None:
    text = snapshot.now_playing.strip()
    if not text:
        return None
    body = html_escape(text)
    card = f"""
<div class="overlay-card overlay-card--ambient overlay-card--now-playing">
  <div class="overlay-card__title">Now Playing</div>
  <div class="overlay-now-playing__content">
    <div class="overlay-now-playing__indicator" aria-hidden="true">
      <div class="overlay-now-playing__bar"></div>
      <div class="overlay-now-playing__bar"></div>
      <div class="overlay-now-playing__bar"></div>
      <div class="overlay-now-playing__bar"></div>
      <div class="overlay-now-playing__bar"></div>
    </div>
    <div class="overlay-now-playing__body">{body}</div>
  </div>
</div>
""".strip()
    return "bottom-right", card


def _build_notification_bar(snapshot: OverlaySnapshot) -> str:
    badges: list[str] = []
    badges.append(_render_badge("config", "Config"))
    upcoming = _filter_upcoming_alarms(snapshot.alarms)
    if snapshot.active_alarm:
        badges.append(_render_badge("alarm_ringing", "Alarm ringing"))
    elif upcoming:
        count = len(upcoming)
        label = f"{count} alarm{'s' if count != 1 else ''}"
        badges.append(_render_badge("alarm", label))
    active_timers = _extract_active_timers(snapshot)
    if active_timers:
        count = len(active_timers)
        label = f"{count} timer{'s' if count != 1 else ''}"
        badges.append(_render_badge("timer", label))
    reminders = _filter_upcoming_reminders(snapshot.reminders)
    if snapshot.active_reminder:
        badges.append(_render_badge("reminder", "Reminder active"))
    elif reminders:
        count = len(reminders)
        label = f"{count} reminder{'s' if count != 1 else ''}"
        badges.append(_render_badge("reminder", label))
    calendar_events = tuple(snapshot.calendar_events or ())
    if calendar_events:
        count = len(calendar_events)
        label = f"{count} calendar event{'s' if count != 1 else ''}"
        badges.append(_render_badge("calendar", label))
    if snapshot.now_playing.strip():
        badges.append(_render_badge("music", "Now playing"))
    if snapshot.update_available:
        badges.append(_render_badge("update", "Update available"))
    badges.append(_render_earmuffs_badge(snapshot.earmuffs_enabled))
    classes = ["overlay-notification-bar"]
    if not badges:
        classes.append("overlay-notification-bar--empty")
    class_attr = " ".join(classes)
    content = "".join(badges)
    return f'<div class="{class_attr}">{content}</div>'


def _build_info_overlay(snapshot: OverlaySnapshot) -> str:
    card = snapshot.info_card or {}
    card_type = str(card.get("type") or "").lower()
    if card_type == "alarms":
        return _build_alarm_info_overlay(snapshot, card)
    if card_type == "reminders":
        return _build_reminder_info_overlay(snapshot, card)
    if card_type == "calendar":
        return _build_calendar_info_overlay(snapshot, card)
    if card_type == "weather":
        return _build_weather_info_overlay(snapshot, card)
    if card_type == "update":
        return _build_update_info_overlay(snapshot, card)
    if card_type == "lights":
        return _build_lights_info_overlay(card)
    if card_type == "routines":
        return _build_routines_info_overlay(card)
    if card_type == "health":
        return _build_health_info_overlay(card)
    if card_type == "config":
        return _build_config_info_overlay()
    if card_type == "sounds":
        return _build_sounds_info_overlay(card)
    text = str(card.get("text") or "").strip()
    if not text:
        return ""
    title = str(card.get("title") or "").strip()
    category = str(card.get("category") or "").strip()
    label = title or category.title() or "Assistant"
    safe_label = html_escape(label)
    safe_text = _format_info_text(text)
    return f"""
<div class="overlay-card overlay-info-card">
  <div class="overlay-info-card__header">
    <div class="overlay-info-card__title">{safe_label}</div>
    <button class="overlay-info-card__close" data-info-card-close aria-label="Close info card">&times;</button>
  </div>
  <div class="overlay-info-card__text">{safe_text}</div>
</div>
""".strip()


def _build_alarm_info_overlay(snapshot: OverlaySnapshot, card: dict[str, Any]) -> str:
    payload_alarms = card.get("alarms")
    if isinstance(payload_alarms, list) and payload_alarms:
        alarms: Iterable[dict[str, Any]] = tuple(item for item in payload_alarms if isinstance(item, dict))
    else:
        alarms = snapshot.alarms or ()
    entries = _format_alarm_info_entries(alarms)
    title = str(card.get("title") or "Alarms").strip() or "Alarms"
    subtitle = card.get("text") or "Use the buttons to pause, resume, or delete an alarm."
    safe_title = html_escape(title)
    safe_subtitle = html_escape(subtitle)
    if not entries:
        body = '<div class="overlay-info-card__empty">No alarms scheduled.</div>'
    else:
        body_rows = []
        for entry in entries:
            raw_label = entry["label"]
            label = html_escape(raw_label)
            meta = html_escape(entry["meta"])
            delete_id = html_escape(entry["id"], quote=True)
            status = entry.get("status")
            if status == "active":
                status_text = "Active"
            elif status == "paused":
                status_text = "Paused"
            else:
                status_text = ""
            status_html = f'<span class="overlay-info-card__alarm-status">{status_text}</span>' if status_text else ""
            toggle_action = "resume" if status == "paused" else "pause"
            toggle_label = "Resume" if toggle_action == "resume" else "Pause"
            toggle_emoji = "▶️" if toggle_action == "resume" else "⏸️"
            aria_label = html_escape(raw_label, quote=True)
            body_rows.append(
                f"""
  <div class="overlay-info-card__alarm">
    <div class="overlay-info-card__alarm-body">
      <div class="overlay-info-card__alarm-label">{label}</div>
      <div class="overlay-info-card__alarm-meta">{meta}{status_html}</div>
    </div>
    <div class="overlay-info-card__alarm-actions">
      <button class="overlay-info-card__alarm-toggle"
        data-toggle-alarm="{toggle_action}"
        data-event-id="{delete_id}"
        aria-label="{toggle_label} {aria_label}">{toggle_emoji}</button>
      <button class="overlay-info-card__alarm-delete"
        data-delete-alarm="{delete_id}"
        aria-label="Delete {aria_label}">🗑️</button>
    </div>
  </div>
                """.strip()
            )
        body = '<div class="overlay-info-card__alarm-list">' + "".join(body_rows) + "</div>"
    return f"""
<div class="overlay-card overlay-info-card overlay-info-card--reminders">
  <div class="overlay-info-card__header">
    <div>
      <div class="overlay-info-card__title">{safe_title}</div>
      <div class="overlay-info-card__subtitle">{safe_subtitle}</div>
    </div>
    <button class="overlay-info-card__close" data-info-card-close aria-label="Close alarms list">&times;</button>
  </div>
  <div class="overlay-info-card__body">
    {body}
  </div>
</div>
""".strip()


def _build_reminder_info_overlay(snapshot: OverlaySnapshot, card: dict[str, Any]) -> str:
    payload_reminders = card.get("reminders")
    if isinstance(payload_reminders, list) and payload_reminders:
        reminders: Iterable[dict[str, Any]] = tuple(item for item in payload_reminders if isinstance(item, dict))
    else:
        reminders = snapshot.reminders or ()
    entries = _format_reminder_info_entries(reminders)
    title = str(card.get("title") or "Reminders").strip() or "Reminders"
    subtitle = card.get("text") or "Complete or delete a reminder."
    safe_title = html_escape(title)
    safe_subtitle = html_escape(subtitle)
    if not entries:
        body = '<div class="overlay-info-card__empty">No reminders scheduled.</div>'
    else:
        body_rows = []
        for entry in entries:
            label = html_escape(entry["label"])
            meta = html_escape(entry["meta"])
            reminder_id = html_escape(entry["id"], quote=True)
            body_rows.append(
                f"""
  <div class="overlay-info-card__reminder">
    <div class="overlay-info-card__reminder-body">
      <div class="overlay-info-card__reminder-label">{label}</div>
      <div class="overlay-info-card__reminder-meta">{meta}</div>
    </div>
    <div class="overlay-info-card__reminder-actions">
      <button class="overlay-button overlay-button--small"
        data-complete-reminder data-event-id="{reminder_id}">Complete</button>
      <button class="overlay-info-card__alarm-delete"
        data-delete-reminder="{reminder_id}"
        aria-label="Delete {label}">🗑️</button>
    </div>
  </div>
                """.strip()
            )
        body = '<div class="overlay-info-card__alarm-list">' + "".join(body_rows) + "</div>"
    return f"""
<div class="overlay-card overlay-info-card overlay-info-card--alarms">
  <div class="overlay-info-card__header">
    <div>
      <div class="overlay-info-card__title">{safe_title}</div>
      <div class="overlay-info-card__subtitle">{safe_subtitle}</div>
    </div>
    <button class="overlay-info-card__close" data-info-card-close aria-label="Close reminders list">&times;</button>
  </div>
  <div class="overlay-info-card__body">
    {body}
  </div>
</div>
""".strip()


def _calendar_lookahead_hours(card: dict[str, Any]) -> int:
    """Return the look-ahead window (hours) for the calendar info card."""

    value = card.get("lookahead_hours")
    try:
        hours = int(value)
    except (TypeError, ValueError):
        hours = DEFAULT_CALENDAR_LOOKAHEAD_HOURS
    if hours <= 0:
        return DEFAULT_CALENDAR_LOOKAHEAD_HOURS
    return hours


def _build_update_info_overlay(snapshot: OverlaySnapshot, card: dict[str, Any]) -> str:
    """Build update progress overlay with animated spinner."""
    title = str(card.get("title") or "Updating Pulse").strip() or "Updating Pulse"
    text = str(card.get("text") or "Updating PulseOS...").strip()
    safe_title = html_escape(title)
    safe_text = html_escape(text)
    return f"""
<div class="overlay-card overlay-info-card overlay-info-card--update">
  <div class="overlay-info-card__header">
    <div class="overlay-info-card__title">{safe_title}</div>
  </div>
  <div class="overlay-info-card__text">
    <div class="overlay-update-spinner" aria-hidden="true"></div>
    <div>{safe_text}</div>
  </div>
</div>
""".strip()


def _build_config_info_overlay() -> str:
    return """
<div class="overlay-card overlay-info-card overlay-info-card--config">
  <div class="overlay-info-card__header">
    <div class="overlay-info-card__title">Config</div>
    <button class="overlay-info-card__close" data-info-card-close aria-label="Close config">&times;</button>
  </div>
  <div class="overlay-info-card__body">
    <div class="overlay-card__actions">
      <button class="overlay-button" data-config-action="show_sounds">Sound picker</button>
    </div>
  </div>
</div>
""".strip()


def _build_sounds_info_overlay(card: dict[str, Any]) -> str:
    sounds = card.get("sounds") or []
    entries: list[str] = []
    for entry in sounds:
        if not isinstance(entry, dict):
            continue
        sound_id = str(entry.get("id") or "").strip()
        if not sound_id:
            continue
        label = str(entry.get("label") or sound_id).strip() or sound_id
        kinds = tuple(kind for kind in entry.get("kinds") or () if isinstance(kind, str)) or ("alarm",)
        built_in = bool(entry.get("built_in"))
        is_default: dict[str, bool] = entry.get("is_default") or {}
        kind_badge = ", ".join(kind.title() for kind in kinds)
        source_badge = "Built-in" if built_in else "Custom"
        default_labels = [
            f"Default {kind}"
            for kind, is_def in is_default.items()
            if is_def and kind in {"alarm", "timer", "reminder", "notification"}
        ]
        default_label = " · ".join(default_labels)
        meta_parts = [kind_badge, source_badge]
        if default_label:
            meta_parts.append(default_label)
        meta = " · ".join(meta_parts)
        primary_kind = kinds[0]
        safe_id = html_escape(sound_id, quote=True)
        entries.append(
            f"""
  <div class="overlay-sound-row">
    <div class="overlay-sound-row__body">
      <div class="overlay-sound-row__label">{html_escape(label)}</div>
      <div class="overlay-sound-row__meta">{html_escape(meta)}</div>
    </div>
    <div class="overlay-sound-row__actions">
      <button
        class="overlay-button overlay-button--small"
        data-play-sound="once"
        data-sound-id="{safe_id}"
        data-sound-kind="{primary_kind}"
      >Listen</button>
      <button
        class="overlay-button overlay-button--small overlay-button--ghost"
        data-play-sound="repeat"
        data-sound-id="{safe_id}"
        data-sound-kind="{primary_kind}"
      >Alarm loop</button>
    </div>
  </div>
            """.strip()
        )
    if not entries:
        body = '<div class="overlay-info-card__empty">No sounds found.</div>'
    else:
        body = '<div class="overlay-sound-list">' + "".join(entries) + "</div>"
    return f"""
<div class="overlay-card overlay-info-card overlay-info-card--sounds">
  <div class="overlay-info-card__header">
    <div>
      <div class="overlay-info-card__title">Sound picker</div>
      <div class="overlay-info-card__subtitle">Tap to preview once or as an alarm loop.</div>
    </div>
    <button class="overlay-info-card__close" data-info-card-close aria-label="Close sound picker">&times;</button>
  </div>
  <div class="overlay-info-card__body">
    {body}
  </div>
</div>
""".strip()


def _build_calendar_info_overlay(snapshot: OverlaySnapshot, card: dict[str, Any]) -> str:
    payload_events = card.get("events")
    if isinstance(payload_events, list) and payload_events:
        events: Iterable[dict[str, Any]] = tuple(item for item in payload_events if isinstance(item, dict))
    else:
        events = snapshot.calendar_events or ()
    entries = _format_calendar_event_entries(events)
    title = str(card.get("title") or "Calendar").strip() or "Calendar"
    lookahead_hours = _calendar_lookahead_hours(card)
    subtitle = card.get("text") or f"Upcoming events in the next {lookahead_hours} hours."
    safe_title = html_escape(title)
    safe_subtitle = html_escape(subtitle)
    if not entries:
        body = '<div class="overlay-info-card__empty">No upcoming calendar events.</div>'
    else:
        body_rows = []
        current_date = None
        for entry in entries:
            entry_date = entry.get("date_only")
            if entry_date and entry_date != current_date:
                if current_date is not None:
                    body_rows.append('<div class="overlay-info-card__date-divider"></div>')
                current_date = entry_date
            row_class = "overlay-info-card__reminder"
            if entry.get("declined"):
                row_class += " overlay-info-card__reminder--declined"
            label = html_escape(entry["label"])
            meta = html_escape(entry["meta"])
            subtext = entry.get("subtext")
            subtext_html = ""
            if subtext:
                subtext_html = f'<div class="overlay-info-card__reminder-meta">{html_escape(subtext)}</div>'
            month_abbr = html_escape(entry.get("month_abbr", ""))
            day_num = html_escape(entry.get("day_num", ""))
            month = entry.get("month", 1)
            month_color = _get_month_color(month)
            icon_html = f"""
    <div class="overlay-info-card__calendar-icon" style="background: {month_color};">
      <div class="overlay-info-card__calendar-icon-month">{month_abbr}</div>
      <div class="overlay-info-card__calendar-icon-day">{day_num}</div>
    </div>
            """.strip()
            body_rows.append(
                f"""
  <div class="{row_class}">
    {icon_html}
    <div class="overlay-info-card__reminder-body">
      <div class="overlay-info-card__reminder-label">{label}</div>
      <div class="overlay-info-card__reminder-meta">{meta}</div>
      {subtext_html}
    </div>
  </div>
                """.strip()
            )
        body = '<div class="overlay-info-card__alarm-list">' + "".join(body_rows) + "</div>"
    return f"""
<div class="overlay-card overlay-info-card overlay-info-card--alarms">
  <div class="overlay-info-card__header">
    <div>
      <div class="overlay-info-card__title">{safe_title}</div>
      <div class="overlay-info-card__subtitle">{safe_subtitle}</div>
    </div>
    <button class="overlay-info-card__close" data-info-card-close aria-label="Close calendar list">&times;</button>
  </div>
  <div class="overlay-info-card__body">
    {body}
  </div>
</div>
""".strip()


def _build_weather_info_overlay(snapshot: OverlaySnapshot, card: dict[str, Any]) -> str:
    units = str(card.get("units") or "").strip()
    raw_days = card.get("days")
    entries = [entry for entry in raw_days if isinstance(entry, dict)] if isinstance(raw_days, list) else []
    title = str(card.get("title") or "Weather").strip() or "Weather"
    subtitle = str(card.get("subtitle") or card.get("text") or "").strip()
    safe_title = html_escape(title)
    subtitle_html = f'<div class="overlay-info-card__subtitle">{html_escape(subtitle)}</div>' if subtitle else ""
    if not entries:
        body = '<div class="overlay-info-card__empty">No forecast available.</div>'
    else:
        rows = []
        current_entry = card.get("current") if isinstance(card.get("current"), dict) else None
        current_html = ""
        if current_entry:
            current_icon = _weather_icon_uri(str(current_entry.get("icon") or ""))
            if current_icon:
                current_icon_html = f'<img src="{current_icon}" alt="Current conditions icon" loading="lazy" />'
            else:
                current_icon_html = '<div class="overlay-weather-row__icon-placeholder" aria-hidden="true">☁️</div>'
            current_meta = (
                f"{html_escape(current_entry.get('temp') or '—')}{html_escape(current_entry.get('units') or '')}"
            )
            current_desc = current_entry.get("description")
            if current_desc:
                current_meta = f"{current_meta} · {html_escape(str(current_desc))}"
            current_label = html_escape(str(current_entry.get("label") or "Now"))
            current_html = (
                '<div class="overlay-weather__current">'
                '<div class="overlay-weather-row">'
                f'<div class="overlay-weather-row__icon">{current_icon_html}</div>'
                '<div class="overlay-weather-row__details">'
                f'<div class="overlay-weather-row__label">{current_label}</div>'
                f'<div class="overlay-weather-row__meta">{current_meta}</div>'
                "</div></div></div>"
                '<div class="overlay-weather__divider"></div>'
            )
        for entry in entries:
            label = html_escape(str(entry.get("label") or "—"))
            high = entry.get("high")
            low = entry.get("low")
            precip = entry.get("precip")
            icon_key = str(entry.get("icon") or "")
            icon_uri = _weather_icon_uri(icon_key)
            if icon_uri:
                icon_html = f'<img src="{icon_uri}" alt="{html_escape(icon_key)} icon" loading="lazy" />'
            else:
                icon_html = '<div class="overlay-weather-row__icon-placeholder" aria-hidden="true">☁️</div>'
            meta_parts = []
            if high:
                meta_parts.append(f"High {html_escape(str(high))}{html_escape(units)}")
            if low:
                meta_parts.append(f"Low {html_escape(str(low))}{html_escape(units)}")
            if precip is not None:
                meta_parts.append(f"Precip {int(precip)}%")
            meta_text = " · ".join(meta_parts)
            rows.append(
                f"""
  <div class="overlay-weather-row">
    <div class="overlay-weather-row__icon">{icon_html}</div>
    <div class="overlay-weather-row__details">
      <div class="overlay-weather-row__label">{label}</div>
      <div class="overlay-weather-row__meta">{meta_text}</div>
    </div>
  </div>
                """.strip()
            )
        body = '<div class="overlay-weather">' + current_html + "".join(rows) + "</div>"
    return f"""
<div class="overlay-card overlay-info-card overlay-info-card--weather">
  <div class="overlay-info-card__header">
    <div>
      <div class="overlay-info-card__title">{safe_title}</div>
      {subtitle_html}
    </div>
    <button class="overlay-info-card__close" data-info-card-close aria-label="Close weather list">&times;</button>
  </div>
  <div class="overlay-info-card__body">
    {body}
  </div>
</div>
""".strip()


def _build_lights_info_overlay(card: dict[str, Any]) -> str:
    entries = card.get("lights")
    title = str(card.get("title") or "Lights").strip() or "Lights"
    subtitle = str(card.get("subtitle") or "").strip()
    safe_title = html_escape(title)
    subtitle_html = f'<div class="overlay-info-card__subtitle">{html_escape(subtitle)}</div>' if subtitle else ""
    if not isinstance(entries, list) or not entries:
        body = '<div class="overlay-info-card__empty">No lights to show.</div>'
    else:
        rows: list[str] = []
        for light in entries:
            if not isinstance(light, dict):
                continue
            name = html_escape(str(light.get("name") or light.get("entity_id") or "Light"))
            state = str(light.get("state") or "unknown").title()
            brightness = light.get("brightness_pct")
            color_temp = light.get("color_temp")
            area = str(light.get("area") or "").strip()
            meta_parts = [state]
            if isinstance(brightness, (int, float)):
                meta_parts.append(f"{int(brightness)}%")
            if color_temp:
                meta_parts.append(str(color_temp))
            if area:
                meta_parts.append(area)
            meta = " · ".join(meta_parts)
            rows.append(
                f"""
  <div class="overlay-info-card__reminder">
    <div class="overlay-info-card__reminder-body">
      <div class="overlay-info-card__reminder-label">{name}</div>
      <div class="overlay-info-card__reminder-meta">{html_escape(meta)}</div>
    </div>
  </div>
                """.strip()
            )
        body = '<div class="overlay-info-card__alarm-list">' + "".join(rows) + "</div>"
    return f"""
<div class="overlay-card overlay-info-card overlay-info-card--lights">
  <div class="overlay-info-card__header">
    <div>
      <div class="overlay-info-card__title">{safe_title}</div>
      {subtitle_html}
    </div>
    <button class="overlay-info-card__close" data-info-card-close aria-label="Close lights list">&times;</button>
  </div>
  <div class="overlay-info-card__body">
    {body}
  </div>
</div>
""".strip()


def _build_routines_info_overlay(card: dict[str, Any]) -> str:
    routines = card.get("routines")
    title = str(card.get("title") or "Routines").strip() or "Routines"
    subtitle = str(card.get("subtitle") or "").strip()
    safe_title = html_escape(title)
    subtitle_html = f'<div class="overlay-info-card__subtitle">{html_escape(subtitle)}</div>' if subtitle else ""
    if not isinstance(routines, list) or not routines:
        body = '<div class="overlay-info-card__empty">No routines available.</div>'
    else:
        rows: list[str] = []
        for routine in routines:
            if not isinstance(routine, dict):
                continue
            label = html_escape(str(routine.get("label") or routine.get("slug") or "Routine"))
            desc = html_escape(str(routine.get("description") or ""))
            rows.append(
                f"""
  <div class="overlay-info-card__reminder">
    <div class="overlay-info-card__reminder-body">
      <div class="overlay-info-card__reminder-label">{label}</div>
      <div class="overlay-info-card__reminder-meta">{desc}</div>
    </div>
  </div>
                """.strip()
            )
        body = '<div class="overlay-info-card__alarm-list">' + "".join(rows) + "</div>"
    return f"""
<div class="overlay-card overlay-info-card overlay-info-card--routines">
  <div class="overlay-info-card__header">
    <div>
      <div class="overlay-info-card__title">{safe_title}</div>
      {subtitle_html}
    </div>
    <button class="overlay-info-card__close" data-info-card-close aria-label="Close routines list">&times;</button>
  </div>
  <div class="overlay-info-card__body">
    {body}
  </div>
</div>
""".strip()


def _build_health_info_overlay(card: dict[str, Any]) -> str:
    items = card.get("items")
    title = str(card.get("title") or "Health").strip() or "Health"
    subtitle = str(card.get("subtitle") or "").strip()
    safe_title = html_escape(title)
    subtitle_html = f'<div class="overlay-info-card__subtitle">{html_escape(subtitle)}</div>' if subtitle else ""
    if not isinstance(items, list) or not items:
        body = '<div class="overlay-info-card__empty">No health details.</div>'
    else:
        rows: list[str] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            label = html_escape(str(item.get("label") or "Status"))
            value = html_escape(str(item.get("value") or ""))
            rows.append(
                f"""
  <div class="overlay-info-card__reminder">
    <div class="overlay-info-card__reminder-body">
      <div class="overlay-info-card__reminder-label">{label}</div>
      <div class="overlay-info-card__reminder-meta">{value}</div>
    </div>
  </div>
                """.strip()
            )
        body = '<div class="overlay-info-card__alarm-list">' + "".join(rows) + "</div>"
    return f"""
<div class="overlay-card overlay-info-card overlay-info-card--health">
  <div class="overlay-info-card__header">
    <div>
      <div class="overlay-info-card__title">{safe_title}</div>
      {subtitle_html}
    </div>
    <button class="overlay-info-card__close" data-info-card-close aria-label="Close health status">&times;</button>
  </div>
  <div class="overlay-info-card__body">
    {body}
  </div>
</div>
""".strip()


def _format_alarm_info_entries(alarms: Iterable[dict[str, Any]]) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for alarm in alarms:
        if not isinstance(alarm, dict):
            continue
        event_id = alarm.get("id")
        if not event_id:
            continue
        label = str(alarm.get("label") or "Alarm")
        time_phrase = _format_alarm_time_phrase(alarm)
        days_phrase = _format_alarm_days_phrase(alarm)
        meta = f"{time_phrase} · {days_phrase}" if days_phrase else time_phrase
        status = str(alarm.get("status") or "").lower()
        entries.append({"id": str(event_id), "label": label, "meta": meta, "status": status})
    return entries


def _format_reminder_info_entries(reminders: Iterable[dict[str, Any]]) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for reminder in reminders:
        if not isinstance(reminder, dict):
            continue
        event_id = reminder.get("id")
        if not event_id:
            continue
        label = str(reminder.get("label") or "Reminder")
        meta = _format_reminder_meta_text(reminder)
        entries.append({"id": str(event_id), "label": label, "meta": meta})
    return entries


def _get_month_color(month: int) -> str:
    """Return a color for the given month (1-12), rotating through 4 colors."""
    colors = [
        "rgba(52, 199, 89, 0.3)",  # Green
        "rgba(0, 122, 255, 0.3)",  # Blue
        "rgba(255, 149, 0, 0.3)",  # Orange
        "rgba(255, 45, 85, 0.3)",  # Red/Pink
    ]
    return colors[(month - 1) % len(colors)]


def _format_calendar_event_entries(events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        label = str(event.get("summary") or "Calendar event")
        start_dt = _parse_datetime_value(event.get("start_local") or event.get("start"))
        if not start_dt:
            continue
        all_day = bool(event.get("all_day"))
        date_text = start_dt.strftime("%a, %b %-d")
        month_abbr = start_dt.strftime("%b")
        day_num = start_dt.strftime("%-d")
        if all_day:
            time_text = "All day"
        else:
            time_text = start_dt.strftime("%-I:%M %p")
        meta = f"{date_text} · {time_text}"
        calendar_name = event.get("calendar_name")
        if calendar_name:
            meta = f"{meta} — {calendar_name}"
        declined = bool(event.get("declined"))
        if declined:
            meta = f"{meta} · Declined"
        location = str(event.get("location") or "").strip()
        entry: dict[str, Any] = {
            "label": label,
            "meta": meta,
            "declined": declined,
            "date_only": start_dt.date(),
            "date_text": date_text,
            "month_abbr": month_abbr,
            "day_num": day_num,
            "month": start_dt.month,
        }
        if location:
            entry["subtext"] = location
        entries.append(entry)
    return entries


@lru_cache(maxsize=32)
def _load_weather_icon_data(icon_key: str) -> str | None:
    if not icon_key:
        return None
    path = WEATHER_ICON_DIR / f"{icon_key}.png"
    if not path.exists():
        return None
    try:
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    except OSError:
        return None
    return f"data:image/png;base64,{encoded}"


def _weather_icon_uri(icon_key: str) -> str | None:
    return _load_weather_icon_data(icon_key)


def _format_alarm_time_phrase(alarm: dict[str, Any]) -> str:
    time_text = str(alarm.get("time_of_day") or alarm.get("time") or "").strip()
    if time_text:
        try:
            dt = datetime.strptime(time_text, "%H:%M").replace(year=1900, month=1, day=1)
            return dt.strftime("%-I:%M %p")
        except ValueError:
            pass
    next_fire = _parse_timestamp(alarm.get("next_fire"))
    if next_fire:
        dt = datetime.fromtimestamp(next_fire)
        return dt.strftime("%-I:%M %p")
    return "—"


def _format_alarm_days_phrase(alarm: dict[str, Any]) -> str:
    days = _normalize_repeat_day_indexes(alarm)
    if not days:
        return "One-time"
    normalized = sorted(set(days))
    if normalized == [0, 1, 2, 3, 4]:
        return "Weekdays"
    if normalized == [5, 6]:
        return "Weekends"
    if normalized == list(range(7)):
        return "Every day"
    names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    return ", ".join(names[idx % 7] for idx in normalized)


def _format_reminder_meta_text(reminder: dict[str, Any]) -> str:
    next_fire = _parse_timestamp(reminder.get("next_fire"))
    if next_fire:
        dt = datetime.fromtimestamp(next_fire)
        meta = dt.strftime("%b %-d · %-I:%M %p")
    else:
        meta = "—"
    metadata = reminder.get("metadata") or {}
    reminder_meta = metadata.get("reminder") if isinstance(metadata, dict) else {}
    repeat_rule = reminder_meta.get("repeat") if isinstance(reminder_meta, dict) else None
    if repeat_rule:
        repeat_type = str(repeat_rule.get("type") or "").title()
        meta = f"{meta} · {repeat_type}"
    return meta


def _normalize_repeat_day_indexes(alarm: dict[str, Any]) -> list[int]:
    repeat_days = alarm.get("repeat_days")
    normalized = _coerce_day_index_list(repeat_days)
    if normalized:
        return normalized
    days_value = alarm.get("days")
    return _coerce_day_index_list(days_value)


def _coerce_day_index_list(value: Any) -> list[int]:
    if value is None:
        return []
    if isinstance(value, list):
        indexes: list[int] = []
        for item in value:
            if isinstance(item, int):
                indexes.append(item % 7)
            else:
                parsed = parse_day_tokens(str(item))
                if parsed:
                    indexes.extend(parsed)
        return sorted({idx % 7 for idx in indexes})
    if isinstance(value, str):
        parsed = parse_day_tokens(value)
        return parsed or []
    if isinstance(value, (int, float)):
        return [int(value) % 7]
    return []


def _format_info_text(text: str) -> str:
    paragraphs: list[str] = []
    blocks = text.split("\n\n")
    for block in blocks:
        lines = [line.strip() for line in block.split("\n") if line.strip()]
        if not lines:
            continue
        safe_block = "<br />".join(html_escape(line) for line in lines)
        paragraphs.append(f"<p>{safe_block}</p>")
    if not paragraphs:
        paragraphs.append(f"<p>{html_escape(text)}</p>")
    return "".join(paragraphs)


def _render_badge(icon_key: str, label: str) -> str:
    icon = ICON_MAP.get(icon_key, "&#9679;")
    safe_label = html_escape(label)
    interactive = icon_key in {
        "config",
        "alarm",
        "alarm_ringing",
        "reminder",
        "reminder_active",
        "calendar",
        "update",
    }
    action = None
    if icon_key == "config":
        action = "show_config"
    elif icon_key.startswith("alarm"):
        action = "show_alarms"
    elif icon_key.startswith("reminder"):
        action = "show_reminders"
    elif icon_key == "calendar":
        action = "show_calendar"
    elif icon_key == "update":
        action = "trigger_update"
    attrs = ['class="overlay-badge"', f'aria-label="{safe_label}"']
    if interactive and action:
        attrs.append('role="button"')
        attrs.append('tabindex="0"')
        attrs.append(f'data-badge-action="{action}"')
    badge_html = (
        f"<span {' '.join(attrs)}>"
        f'<span class="overlay-badge__icon" aria-hidden="true">{icon}</span>'
        f"<span>{safe_label}</span>"
        "</span>"
    )
    return badge_html


def _render_earmuffs_badge(enabled: bool) -> str:
    icon = ICON_MAP.get("earmuffs", "&#127911;")
    safe_label = html_escape("Earmuffs")
    classes = ["overlay-badge"]
    if enabled:
        classes.append("overlay-badge--earmuffs-enabled")
    class_attr = " ".join(classes)
    badge_html = (
        f'<span class="{class_attr}" role="button" tabindex="0" '
        f'data-badge-action="toggle_earmuffs" aria-label="{safe_label}">'
        f'<span class="overlay-badge__icon" aria-hidden="true">{icon}</span>'
        f"<span>{safe_label}</span>"
        "</span>"
    )
    return badge_html


def _extract_active_timers(snapshot: OverlaySnapshot, limit: int = 4) -> list[dict[str, float | str]]:
    now = time.time()
    timers: list[dict[str, float | str]] = []
    for item in snapshot.timers:
        if not isinstance(item, dict):
            continue
        target_raw = item.get("next_fire") or item.get("target")
        target_ts = _parse_timestamp(target_raw)
        if target_ts is None:
            continue
        if target_ts <= now:
            continue
        label = str(item.get("label") or item.get("name") or "Timer")
        timers.append({"label": label, "target": target_ts})
    timers.sort(key=lambda entry: entry["target"])
    return timers[:limit]


def _extract_event_id(payload: dict[str, Any] | None) -> str | None:
    if not isinstance(payload, dict):
        return None
    event = payload.get("event")
    if isinstance(event, dict) and event.get("id") is not None:
        return str(event["id"])
    event_id = payload.get("id")
    if event_id is None:
        return None
    return str(event_id)


def _compute_timer_positions(
    timers: Sequence[dict[str, Any]],
    *,
    reserved_slots: int = 0,
) -> dict[str, str]:
    now = time.time()
    entries: list[tuple[str, float]] = []
    for item in timers:
        if not isinstance(item, dict):
            continue
        target_raw = item.get("next_fire") or item.get("target")
        target_ts = _parse_timestamp(target_raw)
        if target_ts is None or target_ts <= now:
            continue
        event_id = item.get("id")
        if event_id is None:
            continue
        entries.append((str(event_id), target_ts))
    if not entries:
        return {}
    entries.sort(key=lambda entry: entry[1])
    total_slots = len(entries) + max(0, reserved_slots)
    count = min(max(total_slots, len(entries)), max(TIMER_POSITION_MAP))
    positions = TIMER_POSITION_MAP.get(count, TIMER_POSITION_MAP[max(TIMER_POSITION_MAP)])
    mapping: dict[str, str] = {}
    for idx, (event_id, _) in enumerate(entries[: len(positions)]):
        mapping[event_id] = positions[idx]
    return mapping


def _filter_upcoming_alarms(alarms: Iterable[dict[str, Any]] | None) -> list[dict[str, Any]]:
    if not alarms:
        return []
    now = time.time()
    upcoming: list[dict[str, Any]] = []
    for item in alarms:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or "").lower()
        if status == "paused":
            continue
        next_fire = item.get("next_fire")
        ts = _parse_timestamp(next_fire)
        if ts is None:
            continue
        if ts <= now:
            continue
        upcoming.append(item)
    upcoming.sort(key=lambda entry: _parse_timestamp(entry.get("next_fire")) or 0)
    return upcoming


def _filter_upcoming_reminders(reminders: Iterable[dict[str, Any]] | None) -> list[dict[str, Any]]:
    if not reminders:
        return []
    now = time.time()
    entries: list[dict[str, Any]] = []
    for item in reminders:
        if not isinstance(item, dict):
            continue
        next_fire = item.get("next_fire")
        ts = _parse_timestamp(next_fire)
        if ts is None or ts <= now:
            continue
        entries.append(item)
    entries.sort(key=lambda entry: _parse_timestamp(entry.get("next_fire")) or 0)
    return entries


def _parse_timestamp(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.timestamp()


def _parse_datetime_value(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    return None


def _event_label(payload: dict[str, Any] | None, *, default: str) -> str:
    if not isinstance(payload, dict):
        return default
    event = payload.get("event")
    if isinstance(event, dict):
        label = event.get("label") or event.get("name")
        if label:
            return str(label)
    return default
