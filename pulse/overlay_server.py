"""Reusable HTTP server for the Pulse overlay."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .overlay import OverlayChange, OverlayStateManager, OverlayTheme, render_overlay_html

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
        on_toggle_earmuffs: Callable[[], None] | None = None,
        on_trigger_update: Callable[[], None] | None = None,
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
        self._on_toggle_earmuffs = on_toggle_earmuffs
        self._on_trigger_update = on_trigger_update
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

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
                self._serve_overlay(include_body=False)

            def do_GET(self) -> None:  # noqa: N802
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

            def _log(self, message: str) -> None:
                if outer.logger:
                    outer.logger(message)

        return OverlayRequestHandler

    def _allowed_origin(self, origin: str | None) -> str | None:
        allowed = self.config.allowed_origins
        if not allowed or allowed == ("*",):
            return "*"
        if origin and origin in allowed:
            return origin
        return None
