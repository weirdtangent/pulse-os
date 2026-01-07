"""Reusable HTTP server for the Pulse overlay."""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Callable
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from pulse.audio import play_sound, play_volume_feedback
from pulse.sound_library import SoundLibrary, SoundSettings

from .overlay import OverlayChange, OverlayStateManager, OverlayTheme, render_overlay_html
from .overlay_assets import OVERLAY_JS

Logger = Callable[[str], None]


@dataclass(frozen=True)
class OverlayServerConfig:
    bind_address: str
    port: int
    allowed_origins: tuple[str, ...] = ("*",)
    clock_24h: bool = False
    stop_endpoint: str = "/overlay/stop"
    info_endpoint: str = "/overlay/info-card"


class OverlayHttpServer:
    """Embed the overlay renderer behind a lightweight HTTP server."""

    def __init__(
        self,
        *,
        state: OverlayStateManager,
        theme: OverlayTheme,
        config: OverlayServerConfig,
        logger: Logger | None = None,
        on_state_change: Callable[[OverlayChange], None] | None = None,
        on_stop_request: Callable[[str], None] | None = None,
        on_snooze_request: Callable[[str, int], None] | None = None,
        on_delete_alarm: Callable[[str], None] | None = None,
        on_complete_reminder: Callable[[str], None] | None = None,
        on_delay_reminder: Callable[[str, int], None] | None = None,
        on_delete_reminder: Callable[[str], None] | None = None,
        on_pause_alarm: Callable[[str], None] | None = None,
        on_resume_alarm: Callable[[str], None] | None = None,
        on_pause_day: Callable[[str], None] | None = None,
        on_resume_day: Callable[[str], None] | None = None,
        on_toggle_earmuffs: Callable[[], None] | None = None,
        on_trigger_update: Callable[[], None] | None = None,
        on_set_volume: Callable[[int], bool] | None = None,
        on_set_brightness: Callable[[int], bool] | None = None,
        get_device_levels: Callable[[], dict[str, Any]] | None = None,
    ) -> None:
        self.state = state
        self.theme = theme
        self.config = config
        self.logger = logger
        self._on_state_change = on_state_change
        self._on_stop_request = on_stop_request
        self._on_snooze_request = on_snooze_request
        self._on_delete_alarm = on_delete_alarm
        self._on_complete_reminder = on_complete_reminder
        self._on_delay_reminder = on_delay_reminder
        self._on_delete_reminder = on_delete_reminder
        self._on_pause_alarm = on_pause_alarm
        self._on_resume_alarm = on_resume_alarm
        self._on_pause_day = on_pause_day
        self._on_resume_day = on_resume_day
        self._on_toggle_earmuffs = on_toggle_earmuffs
        self._on_trigger_update = on_trigger_update
        self._on_set_volume = on_set_volume
        self._on_set_brightness = on_set_brightness
        self._get_device_levels = get_device_levels
        self._sound_settings = SoundSettings.with_defaults(
            custom_dir=(
                Path(os.environ.get("PULSE_SOUNDS_DIR")).expanduser() if os.environ.get("PULSE_SOUNDS_DIR") else None
            ),
            default_alarm=(os.environ.get("PULSE_SOUND_ALARM") or "alarm-digital-rise").strip(),
            default_timer=(
                os.environ.get("PULSE_SOUND_TIMER") or os.environ.get("PULSE_SOUND_ALARM") or "timer-woodblock"
            ).strip(),
            default_reminder=(os.environ.get("PULSE_SOUND_REMINDER") or "reminder-marimba").strip(),
            default_notification=(os.environ.get("PULSE_SOUND_NOTIFICATION") or "notify-soft-chime").strip(),
        )
        self._sound_library = SoundLibrary(custom_dir=self._sound_settings.custom_dir)
        self._sound_library.ensure_custom_dir()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def _sound_catalog(self) -> list[dict[str, Any]]:
        sounds = []
        defaults = {
            "alarm": self._sound_settings.default_alarm,
            "timer": self._sound_settings.default_timer,
            "reminder": self._sound_settings.default_reminder,
            "notification": self._sound_settings.default_notification,
        }
        for entry in [*self._sound_library.custom_sounds(), *self._sound_library.built_in_sounds()]:
            kinds = entry.kinds or ("alarm", "timer", "reminder", "notification")
            sounds.append(
                {
                    "id": entry.sound_id,
                    "label": entry.label,
                    "kinds": kinds,
                    "built_in": entry.built_in,
                    "is_default": {kind: entry.sound_id == defaults.get(kind) for kind in kinds},
                }
            )
        return sounds

    def _preview_sound(self, sound_id: str, *, kind: str, mode: str) -> None:
        sound_kind = kind if kind in {"alarm", "timer", "reminder", "notification"} else "alarm"
        target_path = self._sound_library.resolve_with_default(sound_id, kind=sound_kind, settings=self._sound_settings)
        if mode == "repeat":
            thread = threading.Thread(
                target=self._repeat_sound,
                args=(target_path, sound_kind),
                name=f"overlay-sound-preview-{sound_id}",
                daemon=True,
            )
            thread.start()
            return
        play_sound(target_path, play_volume_feedback)

    @staticmethod
    def _repeat_sound(path: Path | None, kind: str) -> None:
        loops = 3 if kind in {"alarm", "timer"} else 2
        for _ in range(loops):
            play_sound(path, play_volume_feedback)
            threading.Event().wait(0.8 if kind in {"alarm", "timer"} else 0.35)

    def _device_controls_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"type": "device_controls"}
        device_levels: dict[str, Any] = {}
        if self._get_device_levels:
            try:
                device_levels = self._get_device_levels()
            except Exception as exc:  # pragma: no cover - defensive logging
                if self.logger:
                    self.logger(f"overlay device controls: failed to fetch levels ({exc})")
                device_levels = {}
        brightness_supported = bool(device_levels.get("brightness_supported"))
        volume_supported = bool(device_levels.get("volume_supported", True))
        payload["brightness_supported"] = brightness_supported
        payload["volume_supported"] = volume_supported
        brightness_value = device_levels.get("brightness")
        volume_value = device_levels.get("volume")
        if brightness_supported and isinstance(brightness_value, (int, float)):
            payload["brightness"] = max(0, min(100, int(brightness_value)))
        if volume_supported and isinstance(volume_value, (int, float)):
            payload["volume"] = max(0, min(100, int(volume_value)))
        return payload

    def _render_framed_overlay(self, target_url: str) -> str:
        """Render an HTML page with target URL as background iframe and overlay on top."""
        from html import escape as html_escape

        snapshot = self.state.snapshot()
        overlay_html = render_overlay_html(
            snapshot,
            self.theme,
            clock_hour12=not self.config.clock_24h,
            stop_endpoint=self.config.stop_endpoint,
            info_endpoint=self.config.info_endpoint,
        )
        # Extract the body content from the overlay HTML (between <body> and </body>)
        body_start = overlay_html.find("<body>")
        body_end = overlay_html.find("</body>")
        if body_start != -1 and body_end != -1:
            overlay_body = overlay_html[body_start + 6 : body_end]
        else:
            overlay_body = ""
        # Extract the style content
        style_start = overlay_html.find("<style>")
        style_end = overlay_html.find("</style>")
        if style_start != -1 and style_end != -1:
            overlay_style = overlay_html[style_start + 7 : style_end]
        else:
            overlay_style = ""
        # Escape the target URL for use in HTML attribute
        safe_url = html_escape(target_url, quote=True)
        return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>Pulse Overlay</title>
<style>
* {{
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}}
html, body {{
  width: 100%;
  height: 100%;
  overflow: hidden;
}}
.frame-container {{
  position: fixed;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  z-index: 1;
}}
.frame-container iframe {{
  width: 100%;
  height: 100%;
  border: none;
}}
.overlay-container {{
  position: fixed;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  z-index: 10;
  pointer-events: none;
}}
.overlay-container > * {{
  pointer-events: auto;
}}
/* Ensure interactive elements receive pointer events */
.overlay-card, .overlay-info-card, .overlay-badge, .overlay-button {{
  pointer-events: auto;
}}
{overlay_style}
</style>
</head>
<body>
<div class="frame-container">
  <iframe src="{safe_url}" allow="autoplay; fullscreen" allowfullscreen></iframe>
</div>
<div class="overlay-container">
{overlay_body}
</div>
<script>
{OVERLAY_JS}
</script>
</body>
</html>
"""

    def start(self) -> None:
        if self._server:
            return
        handler_cls = self._build_handler()
        try:
            server = ThreadingHTTPServer((self.config.bind_address, self.config.port), handler_cls)
        except OSError as exc:  # pragma: no cover - dependant on environment
            if self.logger:
                self.logger(f"overlay http: failed to bind {self.config.bind_address}:{self.config.port} ({exc})")
            raise
        self._server = server
        thread = threading.Thread(target=server.serve_forever, name="pulse-overlay-http", daemon=True)
        thread.start()
        self._thread = thread
        if self.logger:
            host = self.config.bind_address
            port = self.config.port
            origins = ", ".join(self.config.allowed_origins)
            self.logger(f"overlay http: serving on http://{host}:{port}/overlay (allowed origins: {origins})")

    def stop(self) -> None:
        server = self._server
        if not server:
            return
        if self.logger:
            self.logger("overlay http: shutting down")
        server.shutdown()
        server.server_close()
        if self._thread:
            self._thread.join(timeout=2)
        self._server = None
        self._thread = None

    def _build_handler(self):
        outer = self

        class OverlayRequestHandler(BaseHTTPRequestHandler):
            def log_message(self, _format, *_args):  # noqa: D401
                return

            def _set_common_headers(self) -> None:
                origin = self.headers.get("Origin")
                allowed_origin = outer._allowed_origin(origin)
                if allowed_origin:
                    self.send_header("Access-Control-Allow-Origin", allowed_origin)
                    if allowed_origin != "*":
                        self.send_header("Vary", "Origin")
                self.send_header("Access-Control-Allow-Methods", "GET, HEAD, OPTIONS, POST")
                self.send_header("Access-Control-Allow-Headers", "Accept, Content-Type")
                self.send_header("Cache-Control", "no-store, max-age=0")

            def do_OPTIONS(self) -> None:  # noqa: N802
                self.send_response(HTTPStatus.NO_CONTENT)
                self._set_common_headers()
                self.end_headers()

            def do_HEAD(self) -> None:  # noqa: N802
                path = self.path.split("?", 1)[0]
                if path == "/overlay/frame":
                    self._serve_frame(include_body=False)
                else:
                    self._serve_overlay(include_body=False)

            def do_GET(self) -> None:  # noqa: N802
                path = self.path.split("?", 1)[0]
                if path == "/overlay/frame":
                    self._serve_frame(include_body=True)
                else:
                    self._serve_overlay(include_body=True)

            def do_POST(self) -> None:  # noqa: N802
                path = self.path.split("?", 1)[0]
                if path == "/overlay/stop":
                    self._handle_stop()
                elif path == "/overlay/info-card":
                    self._handle_info_card()
                else:
                    self.send_error(HTTPStatus.NOT_FOUND, "Not Found")

            def _handle_stop(self) -> None:
                try:
                    data = self._read_json()
                except ValueError as exc:
                    self._log(f"overlay stop: invalid request: {exc}")
                    self.send_error(HTTPStatus.BAD_REQUEST, "Invalid JSON")
                    return
                action = (data.get("action") or "").strip().lower()
                event_id = data.get("event_id")
                if not event_id:
                    self.send_error(HTTPStatus.BAD_REQUEST, "Missing event_id")
                    return
                if action == "stop":
                    if not outer._on_stop_request:
                        self.send_error(HTTPStatus.SERVICE_UNAVAILABLE, "Stop command unavailable")
                        return
                    outer._on_stop_request(str(event_id))
                elif action == "snooze":
                    if not outer._on_snooze_request:
                        self.send_error(HTTPStatus.SERVICE_UNAVAILABLE, "Snooze command unavailable")
                        return
                    minutes = data.get("minutes")
                    try:
                        snooze_minutes = max(1, int(minutes))
                    except (TypeError, ValueError):
                        snooze_minutes = 5
                    outer._on_snooze_request(str(event_id), snooze_minutes)
                else:
                    self.send_error(HTTPStatus.BAD_REQUEST, "Invalid request")
                    return
                self.send_response(HTTPStatus.NO_CONTENT)
                self._set_common_headers()
                self.end_headers()

            def _handle_info_card(self) -> None:
                try:
                    data = self._read_json()
                except ValueError as exc:
                    self._log(f"overlay info card: invalid request: {exc}")
                    self.send_error(HTTPStatus.BAD_REQUEST, "Invalid JSON")
                    return
                action = (data.get("action") or "").strip().lower()
                self._log(f"overlay info card: received action '{action}'")
                if action == "clear":
                    change = outer.state.update_info_card(None)
                    if outer._on_state_change:
                        outer._on_state_change(change)
                elif action == "delete_alarm":
                    event_id = data.get("event_id")
                    if not event_id or not outer._on_delete_alarm:
                        self.send_error(HTTPStatus.BAD_REQUEST, "Missing event_id")
                        return
                    outer._on_delete_alarm(str(event_id))
                elif action in {"pause_alarm", "resume_alarm"}:
                    event_id = data.get("event_id")
                    handler = outer._on_pause_alarm if action == "pause_alarm" else outer._on_resume_alarm
                    if not event_id or not handler:
                        self.send_error(HTTPStatus.BAD_REQUEST, "Missing event_id")
                        return
                    handler(str(event_id))
                elif action in {"pause_day", "resume_day"}:
                    target_date = data.get("date")
                    handler = outer._on_pause_day if action == "pause_day" else outer._on_resume_day
                    if not target_date or not handler:
                        self.send_error(HTTPStatus.BAD_REQUEST, "Missing date")
                        return
                    handler(str(target_date))
                elif action == "complete_reminder":
                    event_id = data.get("event_id")
                    if not event_id or not outer._on_complete_reminder:
                        self.send_error(HTTPStatus.BAD_REQUEST, "Missing event_id")
                        return
                    outer._on_complete_reminder(str(event_id))
                elif action == "delay_reminder":
                    event_id = data.get("event_id")
                    seconds = data.get("seconds")
                    if not event_id or not outer._on_delay_reminder:
                        self.send_error(HTTPStatus.BAD_REQUEST, "Missing event_id")
                        return
                    try:
                        delay_seconds = max(1, int(seconds))
                    except (TypeError, ValueError):
                        delay_seconds = 3600
                    outer._on_delay_reminder(str(event_id), delay_seconds)
                elif action == "delete_reminder":
                    event_id = data.get("event_id")
                    if not event_id or not outer._on_delete_reminder:
                        self.send_error(HTTPStatus.BAD_REQUEST, "Missing event_id")
                        return
                    outer._on_delete_reminder(str(event_id))
                elif action == "show_alarms":
                    change = outer.state.update_info_card({"type": "alarms"})
                    if outer._on_state_change:
                        outer._on_state_change(change)
                elif action == "show_reminders":
                    change = outer.state.update_info_card({"type": "reminders"})
                    if outer._on_state_change:
                        outer._on_state_change(change)
                elif action == "show_calendar":
                    self._log("overlay: show_calendar requested")
                    change = outer.state.update_info_card({"type": "calendar"})
                    if outer._on_state_change:
                        outer._on_state_change(change)
                    self._log(f"overlay: calendar info card updated (changed={change.changed})")
                elif action == "show_config":
                    change = outer.state.update_info_card({"type": "config"})
                    if outer._on_state_change:
                        outer._on_state_change(change)
                elif action == "show_device_controls":
                    change = outer.state.update_info_card(outer._device_controls_payload())
                    if outer._on_state_change:
                        outer._on_state_change(change)
                elif action == "show_sounds":
                    payload = {
                        "type": "sounds",
                        "sounds": outer._sound_catalog(),
                        "defaults": {
                            "alarm": outer._sound_settings.default_alarm,
                            "timer": outer._sound_settings.default_timer,
                            "reminder": outer._sound_settings.default_reminder,
                            "notification": outer._sound_settings.default_notification,
                        },
                    }
                    change = outer.state.update_info_card(payload)
                    if outer._on_state_change:
                        outer._on_state_change(change)
                elif action == "play_sound":
                    sound_id = str(data.get("sound_id") or "").strip()
                    if not sound_id:
                        self.send_error(HTTPStatus.BAD_REQUEST, "Missing sound_id")
                        return
                    mode = (data.get("mode") or "once").strip().lower()
                    kind = (data.get("kind") or "alarm").strip().lower()
                    outer._preview_sound(sound_id, kind=kind, mode=mode)
                elif action in {"set_volume", "set_brightness"}:
                    raw_value = data.get("value")
                    try:
                        target_value = max(0, min(100, int(float(raw_value))))
                    except (TypeError, ValueError):
                        self.send_error(HTTPStatus.BAD_REQUEST, "Missing or invalid value")
                        return
                    if action == "set_volume":
                        if not outer._on_set_volume:
                            self.send_error(HTTPStatus.SERVICE_UNAVAILABLE, "Volume control unavailable")
                            return
                        success = outer._on_set_volume(target_value)
                    else:
                        if not outer._on_set_brightness:
                            self.send_error(HTTPStatus.SERVICE_UNAVAILABLE, "Brightness control unavailable")
                            return
                        success = outer._on_set_brightness(target_value)
                    if not success:
                        self.send_error(HTTPStatus.SERVICE_UNAVAILABLE, "Control request failed")
                        return
                    change = outer.state.update_info_card(outer._device_controls_payload())
                    if outer._on_state_change:
                        outer._on_state_change(change)
                elif action == "toggle_earmuffs":
                    self._log("overlay: toggle_earmuffs requested")
                    if not outer._on_toggle_earmuffs:
                        self._log("overlay: earmuffs toggle handler not available")
                        self.send_error(HTTPStatus.SERVICE_UNAVAILABLE, "Earmuffs toggle unavailable")
                        return
                    outer._on_toggle_earmuffs()
                    self._log("overlay: earmuffs toggle handler called")
                elif action == "trigger_update":
                    self._log("overlay: trigger_update requested")
                    if not outer._on_trigger_update:
                        self._log("overlay: update trigger handler not available")
                        self.send_error(HTTPStatus.SERVICE_UNAVAILABLE, "Update unavailable")
                        return
                    outer._on_trigger_update()
                    self._log("overlay: update trigger handler called")
                else:
                    self.send_error(HTTPStatus.BAD_REQUEST, "Invalid action")
                    return
                self.send_response(HTTPStatus.NO_CONTENT)
                self._set_common_headers()
                self.end_headers()

            def _read_json(self) -> dict[str, Any]:
                content_length = int(self.headers.get("Content-Length", 0))
                if content_length <= 0:
                    raise ValueError("Empty body")
                body = self.rfile.read(content_length)
                return json.loads(body.decode("utf-8"))

            def _serve_overlay(self, *, include_body: bool) -> None:
                snapshot = outer.state.snapshot()
                html = render_overlay_html(
                    snapshot,
                    outer.theme,
                    clock_hour12=not outer.config.clock_24h,
                    stop_endpoint=outer.config.stop_endpoint,
                    info_endpoint=outer.config.info_endpoint,
                ).encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self._set_common_headers()
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(html)))
                self.end_headers()
                if include_body:
                    self.wfile.write(html)

            def _serve_frame(self, *, include_body: bool) -> None:
                """Serve a page with target URL as background iframe and overlay on top."""
                parsed = urlparse(self.path)
                query_params = parse_qs(parsed.query)
                raw_url = query_params.get("url", [""])[0]
                target_url = unquote(raw_url) if raw_url else ""
                if not target_url:
                    self.send_error(HTTPStatus.BAD_REQUEST, "Missing 'url' parameter")
                    return
                html = outer._render_framed_overlay(target_url).encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self._set_common_headers()
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(html)))
                self.end_headers()
                if include_body:
                    self.wfile.write(html)

            def _log(self, message: str) -> None:
                if outer.logger:
                    outer.logger(message)

        return OverlayRequestHandler

    def _sanitize_header_value(self, value: str | None) -> str | None:
        """Prevent response-splitting by blocking CR/LF in header values."""
        if not value:
            return None
        if "\r" in value or "\n" in value:
            if self.logger:
                self.logger("overlay http: dropped header value containing CR/LF")
            return None
        return value

    def _allowed_origin(self, origin: str | None) -> str | None:
        allowed = self.config.allowed_origins
        if not allowed or allowed == ("*",):
            return "*"
        if origin and origin in allowed:
            return self._sanitize_header_value(origin)
        return None
