"""Shared overlay state helpers for PulseOS."""

from __future__ import annotations

import copy
import json
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from html import escape as html_escape
from typing import Any, Callable, Iterable, Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


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
    active_alarm: dict[str, Any] | None
    active_timer: dict[str, Any] | None
    notifications: tuple[dict[str, Any], ...]
    last_reason: str
    generated_at: float
    schedule_snapshot: dict[str, Any] | None


@dataclass(frozen=True)
class OverlayChange:
    """Result metadata from a state mutation."""

    changed: bool
    version: int
    reason: str


DEFAULT_MAX_CLOCKS = 4


def parse_clock_config(
    spec: str | None,
    *,
    default_label: str,
    log: Callable[[str], None] | None = None,
    max_clocks: int = DEFAULT_MAX_CLOCKS,
) -> tuple[ClockConfig, ...]:
    """Parse the overlay clock specification from an environment string.

    Format: comma-separated entries where each entry is either `timezone`
    or `timezone=Custom Label`. The special timezone value `local` (or `system`)
    maps to the kiosk's local timezone.
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
                _log(f"skipping invalid timezone '{zone}' in PULSE_OVERLAY_CLOCKS")
                continue
        entries.append((tz_name, label))
        if len(entries) >= max_clocks:
            break
    if not seen_local and len(entries) < max_clocks:
        entries.insert(0, (None, default_label))
    final_entries = entries[:max_clocks]
    if not final_entries:
        final_entries = [(None, default_label)]
    configs = tuple(
        ClockConfig(key=f"clock{idx}", label=label, timezone=tz_name)
        for idx, (tz_name, label) in enumerate(final_entries)
    )
    return configs


class OverlayStateManager:
    """Thread-safe container for overlay state and change detection."""

    def __init__(self, clocks: Sequence[ClockConfig] | None = None) -> None:
        self._lock = threading.Lock()
        self._clocks = tuple(clocks) if clocks else (ClockConfig("clock0", "Local", None),)
        self._timers: tuple[dict[str, Any], ...] = ()
        self._alarms: tuple[dict[str, Any], ...] = ()
        self._active_alarm: dict[str, Any] | None = None
        self._active_timer: dict[str, Any] | None = None
        self._notifications: tuple[dict[str, Any], ...] = ()
        self._schedule_snapshot: dict[str, Any] | None = None
        self._now_playing = ""
        self._version = 0
        self._last_reason = "init"
        self._last_updated = time.time()
        self._signatures = {
            "timers": "",
            "alarms": "",
            "active_alarm": "",
            "active_timer": "",
            "notifications": "",
            "schedule_snapshot": "",
            "now_playing": "",
        }

    @property
    def clocks(self) -> tuple[ClockConfig, ...]:
        return self._clocks

    def configure_clocks(self, clocks: Sequence[ClockConfig]) -> OverlayChange:
        new_clocks = tuple(clocks) if clocks else self._clocks
        with self._lock:
            if new_clocks == self._clocks:
                return OverlayChange(False, self._version, "clocks")
            self._clocks = new_clocks
            return self._bump("clocks")

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
        snapshot_signature = _signature(snapshot)
        timer_signature = _signature(timers)
        alarm_signature = _signature(alarms)
        changed = False
        with self._lock:
            if timer_signature != self._signatures["timers"]:
                self._timers = tuple(copy.deepcopy(item) for item in timers)
                self._signatures["timers"] = timer_signature
                changed = True
            if alarm_signature != self._signatures["alarms"]:
                self._alarms = tuple(copy.deepcopy(item) for item in alarms)
                self._signatures["alarms"] = alarm_signature
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
        field = "active_alarm" if event_type == "alarm" else "active_timer"
        with self._lock:
            if signature == self._signatures[field]:
                return OverlayChange(False, self._version, field)
            if event_type == "alarm":
                self._active_alarm = normalized
            else:
                self._active_timer = normalized
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

    def snapshot(self) -> OverlaySnapshot:
        with self._lock:
            return OverlaySnapshot(
                version=self._version,
                clocks=self._clocks,
                now_playing=self._now_playing,
                timers=tuple(copy.deepcopy(item) for item in self._timers),
                alarms=tuple(copy.deepcopy(item) for item in self._alarms),
                active_alarm=copy.deepcopy(self._active_alarm),
                active_timer=copy.deepcopy(self._active_timer),
                notifications=tuple(copy.deepcopy(item) for item in self._notifications),
                last_reason=self._last_reason,
                generated_at=time.time(),
                schedule_snapshot=copy.deepcopy(self._schedule_snapshot),
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

CLOCK_POSITION_MAP = {
    1: ("center",),
    2: ("middle-left", "middle-right"),
    3: ("top-left", "top-right", "middle-left"),
    4: ("top-left", "top-right", "middle-left", "middle-right"),
}

TIMER_POSITION_MAP = {
    1: ("center",),
    2: ("middle-left", "middle-right"),
    3: ("top-left", "top-right", "center"),
    4: ("top-left", "top-right", "middle-left", "middle-right"),
}

ICON_MAP = {
    "alarm": "&#128276;",  # 🔔
    "alarm_ringing": "&#128276;",
    "timer": "&#9201;",
    "music": "&#9835;",
}

OVERLAY_JS = """
<script>
(function () {
  const root = document.getElementById('pulse-overlay-root');
  if (!root) {
    return;
  }
  const clockNodes = root.querySelectorAll('[data-clock]');
  const timerNodes = root.querySelectorAll('[data-timer]');
  const timeOptions = { hour: 'numeric', minute: '2-digit', second: '2-digit' };
  const dateOptions = { weekday: 'short', month: 'short', day: 'numeric' };

  const formatWithZone = (date, tz, options) => {
    try {
      return new Intl.DateTimeFormat(undefined, { ...options, timeZone: tz || undefined }).format(date);
    } catch (error) {
      return new Intl.DateTimeFormat(undefined, options).format(date);
    }
  };

  const tick = () => {
    const now = new Date();
    clockNodes.forEach((node) => {
      const tz = node.dataset.tz || undefined;
      const timeEl = node.querySelector('[data-clock-time]');
      const dateEl = node.querySelector('[data-clock-date]');
      if (timeEl) {
        timeEl.textContent = formatWithZone(now, tz, timeOptions);
      }
      if (dateEl) {
        dateEl.textContent = formatWithZone(now, tz, dateOptions);
      }
    });

    const nowMs = now.getTime();
    timerNodes.forEach((node) => {
      const targetMs = Number(node.dataset.targetMs || 0);
      if (!Number.isFinite(targetMs) || targetMs <= 0) {
        return;
      }
      let remaining = Math.max(0, Math.round((targetMs - nowMs) / 1000));
      const hours = Math.floor(remaining / 3600);
      remaining -= hours * 3600;
      const minutes = Math.floor(remaining / 60);
      const seconds = remaining % 60;
      const formatted =
        hours > 0
          ? `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`
          : `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
      const remainingEl = node.querySelector('[data-timer-remaining]');
      if (remainingEl) {
        remainingEl.textContent = formatted;
      }
      if (targetMs - nowMs <= 1000) {
        node.classList.add('overlay-card--expired');
      } else {
        node.classList.remove('overlay-card--expired');
      }
    });
  };

  tick();
  window.setInterval(tick, 1000);
})();
</script>
""".strip()


def render_overlay_html(snapshot: OverlaySnapshot, theme: OverlayTheme) -> str:
    """Render the overlay snapshot into an HTML document."""

    cells: dict[str, list[str]] = {cell: [] for cell in CELL_ORDER}

    for cell, card in _build_clock_cards(snapshot):
        cells[cell].append(card)
    for cell, card in _build_timer_cards(snapshot):
        cells[cell].append(card)
    for card in _build_active_event_cards(snapshot):
        cells[card[0]].append(card[1])
    now_playing_card = _build_now_playing_card(snapshot)
    if now_playing_card:
        cells[now_playing_card[0]].append(now_playing_card[1])

    grid_markup = "".join(
        f'<div class="overlay-cell cell-{cell}" data-cell="{cell}">{"".join(cards)}</div>'
        for cell, cards in cells.items()
        if cards
    )

    notification_html = (
        _build_notification_bar(snapshot)
        if theme.show_notification_bar
        else ""
    )

    root_attrs = (
        f'id="pulse-overlay-root" '
        f'class="overlay-root" '
        f'data-version="{snapshot.version}" '
        f'data-generated-at="{int(snapshot.generated_at * 1000)}"'
    )

    html_document = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<style>
:root {{
  color-scheme: dark;
}}
body {{
  margin: 0;
  width: 100vw;
  height: 100vh;
  background: transparent;
  font-family: "Inter", "Segoe UI", "Helvetica Neue", sans-serif;
  color: {theme.text_color};
}}
.overlay-root {{
  width: 100%;
  height: 100%;
  padding: 3vh;
  box-sizing: border-box;
  color: {theme.text_color};
}}
.overlay-notification-bar {{
  display: flex;
  gap: 0.6rem;
  align-items: center;
  margin-bottom: 1rem;
  font-size: 0.95rem;
}}
.overlay-badge {{
  display: inline-flex;
  gap: 0.35rem;
  align-items: center;
  padding: 0.35rem 0.65rem;
  border-radius: 999px;
  background: {theme.ambient_background};
  backdrop-filter: blur(12px);
}}
.overlay-grid {{
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  grid-template-rows: repeat(3, minmax(0, 1fr));
  grid-template-areas:
    "top-left top-center top-right"
    "middle-left center middle-right"
    "bottom-left bottom-center bottom-right";
  gap: 2vh;
  width: 100%;
  height: 100%;
}}
.overlay-cell {{
  display: flex;
  flex-direction: column;
  gap: 1.2vh;
}}
.cell-top-left {{ grid-area: top-left; }}
.cell-top-center {{ grid-area: top-center; }}
.cell-top-right {{ grid-area: top-right; }}
.cell-middle-left {{ grid-area: middle-left; }}
.cell-center {{ grid-area: center; }}
.cell-middle-right {{ grid-area: middle-right; }}
.cell-bottom-left {{ grid-area: bottom-left; }}
.cell-bottom-center {{ grid-area: bottom-center; }}
.cell-bottom-right {{ grid-area: bottom-right; }}
.overlay-card {{
  padding: 1rem 1.2rem;
  border-radius: 1.2rem;
  backdrop-filter: blur(14px);
  color: inherit;
  box-shadow: 0 0.6rem 1.8rem rgba(0, 0, 0, 0.35);
}}
.overlay-card__title {{
  font-size: 0.95rem;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  margin-bottom: 0.25rem;
  color: {theme.accent_color};
}}
.overlay-clock__time {{
  font-size: 2.75rem;
  font-weight: 600;
}}
.overlay-clock__date {{
  font-size: 0.95rem;
  opacity: 0.85;
}}
.overlay-card--ambient {{
  background: {theme.ambient_background};
}}
.overlay-card--alert {{
  background: {theme.alert_background};
  border: 1px solid rgba(255, 255, 255, 0.2);
}}
.overlay-card--ringing {{
  animation: overlayPulse 1.2s ease-in-out infinite alternate;
}}
.overlay-card--timer .overlay-timer__remaining {{
  font-size: 2.4rem;
  font-weight: 600;
}}
.overlay-card--expired {{
  opacity: 0.75;
}}
.overlay-card--now-playing {{
  min-width: 16rem;
}}
.overlay-now-playing__body {{
  font-size: 1.1rem;
}}
@keyframes overlayPulse {{
  from {{
    box-shadow: 0 0 0 rgba(255, 0, 0, 0.35);
  }}
  to {{
    box-shadow: 0 0 25px rgba(255, 0, 0, 0.65);
  }}
}}
@media (max-width: 720px) {{
  .overlay-clock__time {{
    font-size: 2rem;
  }}
  .overlay-card {{
    padding: 0.85rem;
  }}
}}
</style>
</head>
<body>
<div {root_attrs}>
{notification_html}
<div class="overlay-grid">
{grid_markup}
</div>
</div>
{OVERLAY_JS}
</body>
</html>
"""
    return html_document


def _build_clock_cards(snapshot: OverlaySnapshot) -> list[tuple[str, str]]:
    clocks = snapshot.clocks or ()
    count = min(len(clocks), max(CLOCK_POSITION_MAP))
    positions = CLOCK_POSITION_MAP.get(count, CLOCK_POSITION_MAP[max(CLOCK_POSITION_MAP)])
    cards: list[tuple[str, str]] = []
    for idx, clock in enumerate(clocks[: len(positions)]):
        position = positions[idx]
        tz_attr = clock.timezone or ""
        label = html_escape(clock.label or "Clock")
        card = f"""
<div class="overlay-card overlay-card--ambient overlay-card--clock" data-clock data-tz="{html_escape(tz_attr, quote=True)}">
  <div class="overlay-card__title">{label}</div>
  <div class="overlay-clock__time" data-clock-time>--:--</div>
  <div class="overlay-clock__date" data-clock-date></div>
</div>
""".strip()
        cards.append((position, card))
    return cards


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


def _build_active_event_cards(snapshot: OverlaySnapshot) -> list[tuple[str, str]]:
    cards: list[tuple[str, str]] = []
    if snapshot.active_alarm:
        label = _event_label(snapshot.active_alarm, default="Alarm ringing")
        cards.append(
            (
                "center",
                f"""
<div class="overlay-card overlay-card--alert overlay-card--ringing">
  <div class="overlay-card__title">{html_escape(label)}</div>
  <div>Tap the physical controls or say “Stop” to dismiss.</div>
</div>
""".strip(),
            )
        )
    if snapshot.active_timer:
        label = _event_label(snapshot.active_timer, default="Timer complete")
        cards.append(
            (
                "bottom-center",
                f"""
<div class="overlay-card overlay-card--alert overlay-card--ringing">
  <div class="overlay-card__title">{html_escape(label)}</div>
  <div>Timer finished.</div>
</div>
""".strip(),
            )
        )
    return cards


def _build_now_playing_card(snapshot: OverlaySnapshot) -> tuple[str, str] | None:
    text = snapshot.now_playing.strip()
    if not text:
        return None
    body = html_escape(text)
    card = f"""
<div class="overlay-card overlay-card--ambient overlay-card--now-playing">
  <div class="overlay-card__title">Now Playing</div>
  <div class="overlay-now-playing__body">{body}</div>
</div>
""".strip()
    return "bottom-right", card


def _build_notification_bar(snapshot: OverlaySnapshot) -> str:
    badges: list[str] = []
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
    if snapshot.now_playing.strip():
        badges.append(_render_badge("music", "Now playing"))
    if not badges:
        return ""
    return f'<div class="overlay-notification-bar">{"".join(badges)}</div>'


def _render_badge(icon_key: str, label: str) -> str:
    icon = ICON_MAP.get(icon_key, "&#9679;")
    safe_label = html_escape(label)
    return (
        f'<span class="overlay-badge" aria-label="{safe_label}">'
        f'<span class="overlay-badge__icon" aria-hidden="true">{icon}</span>'
        f"<span>{safe_label}</span>"
        "</span>"
    )


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


def _filter_upcoming_alarms(alarms: Iterable[dict[str, Any]] | None) -> list[dict[str, Any]]:
    if not alarms:
        return []
    now = time.time()
    upcoming: list[dict[str, Any]] = []
    for item in alarms:
        if not isinstance(item, dict):
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
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _event_label(payload: dict[str, Any] | None, *, default: str) -> str:
    if not isinstance(payload, dict):
        return default
    event = payload.get("event")
    if isinstance(event, dict):
        label = event.get("label") or event.get("name")
        if label:
            return str(label)
    return default


