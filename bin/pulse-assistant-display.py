#!/usr/bin/env python3
"""Minimal on-screen overlay for assistant responses."""

from __future__ import annotations

import argparse
import json
import logging
import os
import queue
import ssl
import threading
import tkinter as tk
import urllib.error
import urllib.request

import paho.mqtt.client as mqtt

LOGGER = logging.getLogger("pulse-assistant-display")


def _is_truthy(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int_from_env(value: str | None, fallback: int, minimum: int) -> int:
    if value is None:
        return max(minimum, fallback)
    try:
        parsed = int(value)
        return max(minimum, parsed)
    except ValueError:
        return max(minimum, fallback)


class AssistantDisplay:
    def __init__(self, mqtt_host: str, mqtt_port: int, topic: str, timeout: int, font_size: int) -> None:
        self.topic = topic
        self.timeout_ms = max(1000, timeout * 1000)
        self.queue: queue.Queue[str] = queue.Queue()
        self._now_playing_queue: queue.Queue[str] | None = None
        self._hide_job: str | None = None
        self._client = mqtt.Client(client_id="pulse-assistant-display")
        username = os.environ.get("MQTT_USERNAME")
        if username:
            self._client.username_pw_set(username, os.environ.get("MQTT_PASSWORD") or "")
        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message
        self._client.connect_async(mqtt_host, mqtt_port, keepalive=30)
        self._client.loop_start()

        self.root = tk.Tk()
        self.root.withdraw()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self._screen_width = self.root.winfo_screenwidth()
        self._screen_height = self.root.winfo_screenheight()
        frame_height = int(min(260, self._screen_height * 0.25))
        self.root.geometry(f"{self._screen_width}x{frame_height}+0+{self._screen_height - frame_height}")
        self.root.configure(bg="#000000")
        self.root.attributes("-alpha", 0.82)

        self.label = tk.Label(
            self.root,
            text="",
            font=("Helvetica", font_size),
            fg="#FFFFFF",
            bg="#000000",
            wraplength=self._screen_width - 80,
            justify=tk.LEFT,
        )
        self.label.pack(expand=True, fill=tk.BOTH, padx=40, pady=40)
        self.root.after(250, self._poll_queue)

        self.now_playing_window: tk.Toplevel | None = None
        self.now_playing_canvas: tk.Canvas | None = None
        self.now_playing_text_id: int | None = None
        self._now_playing_stop = threading.Event()
        self._now_playing_active = False
        self._now_playing_interval = 5
        self._now_playing_entity = ""
        self._ha_base_url = ""
        self._ha_token = ""
        self._ha_ssl_context: ssl.SSLContext | None = None
        self._now_playing_geometry: str | None = None
        self._init_now_playing(font_size)

    def _on_connect(self, client, _userdata, _flags, rc):  # type: ignore[no-untyped-def]
        if rc == 0:
            client.subscribe(self.topic)
            LOGGER.info("Subscribed to %s", self.topic)
        else:
            LOGGER.error("Failed to connect to MQTT (%s)", rc)

    def _on_message(self, _client, _userdata, message):  # type: ignore[no-untyped-def]
        try:
            payload = message.payload.decode("utf-8", errors="ignore")
            text = payload
            try:
                parsed = json.loads(payload)
                if isinstance(parsed, dict) and "text" in parsed:
                    text = str(parsed["text"])
            except json.JSONDecodeError:
                pass
            if text:
                self.queue.put(text.strip())
        except Exception as exc:  # pylint: disable=broad-except
            LOGGER.debug("Failed to process assistant message: %s", exc)

    def _poll_queue(self) -> None:
        try:
            while True:
                text = self.queue.get_nowait()
                self._show_text(text)
        except queue.Empty:
            pass
        self.root.after(200, self._poll_queue)

    def _poll_now_playing_queue(self) -> None:
        if not self._now_playing_queue:
            return
        try:
            while True:
                text = self._now_playing_queue.get_nowait()
                self._update_now_playing_label(text)
        except queue.Empty:
            pass
        if self._now_playing_active:
            self.root.after(200, self._poll_now_playing_queue)

    def _update_now_playing_label(self, text: str) -> None:
        if not self.now_playing_window or not self.now_playing_canvas or self.now_playing_text_id is None:
            return
        if text:
            self.now_playing_canvas.itemconfig(self.now_playing_text_id, text=f"Now Playing:\n{text}")
            if self._now_playing_geometry:
                self.now_playing_window.geometry(self._now_playing_geometry)
            self.now_playing_window.deiconify()
            self.now_playing_window.lift()
        else:
            self.now_playing_canvas.itemconfig(self.now_playing_text_id, text="")
            self.now_playing_window.withdraw()

    def _show_text(self, text: str) -> None:
        self.label.config(text=text)
        self.root.deiconify()
        if self._hide_job:
            self.root.after_cancel(self._hide_job)
        self._hide_job = self.root.after(self.timeout_ms, self._hide)

    def _hide(self) -> None:
        self._hide_job = None
        self.root.withdraw()

    def run(self) -> None:
        try:
            self.root.mainloop()
        finally:
            self._client.loop_stop()
            self._client.disconnect()
            if self._now_playing_active:
                self._now_playing_stop.set()

    def _init_now_playing(self, font_size: int) -> None:
        show = _is_truthy(os.environ.get("PULSE_DISPLAY_SHOW_NOW_PLAYING"))
        entity = (os.environ.get("PULSE_DISPLAY_NOW_PLAYING_ENTITY") or "").strip()
        base_url = (os.environ.get("HOME_ASSISTANT_BASE_URL") or "").strip()
        token = (os.environ.get("HOME_ASSISTANT_TOKEN") or "").strip()
        interval = _int_from_env(os.environ.get("PULSE_DISPLAY_NOW_PLAYING_INTERVAL_SECONDS"), fallback=5, minimum=2)
        verify_ssl = _is_truthy(os.environ.get("HOME_ASSISTANT_VERIFY_SSL"), default=True)

        if not show:
            LOGGER.debug("Now-playing overlay disabled (PULSE_DISPLAY_SHOW_NOW_PLAYING=false).")
            return
        if not entity:
            LOGGER.warning("Now-playing overlay disabled: PULSE_DISPLAY_NOW_PLAYING_ENTITY is empty.")
            return
        if not base_url or not token:
            LOGGER.warning("Now-playing overlay disabled: HOME_ASSISTANT_BASE_URL or HOME_ASSISTANT_TOKEN is not set.")
            return

        self._now_playing_active = True
        self._now_playing_interval = interval
        self._now_playing_entity = entity
        self._ha_base_url = base_url.rstrip("/")
        self._ha_token = token
        if self._ha_base_url.lower().startswith("https"):
            self._ha_ssl_context = ssl.create_default_context() if verify_ssl else ssl._create_unverified_context()
        else:
            self._ha_ssl_context = None

        self.now_playing_window = tk.Toplevel(self.root)
        self.now_playing_window.withdraw()
        self.now_playing_window.overrideredirect(True)
        self.now_playing_window.attributes("-topmost", True)
        card_color = "#1C1C1C"
        accent_color = card_color
        self.now_playing_window.configure(bg=accent_color)
        window_width = max(360, self._screen_width // 3)
        window_height = max(68, int(font_size * 2.2))
        offset_x = self._screen_width - window_width - 40
        offset_y = self._screen_height - window_height - 40
        geometry = f"{window_width}x{window_height}+{offset_x}+{offset_y}"
        self._now_playing_geometry = geometry
        self.now_playing_window.geometry(geometry)

        self.now_playing_canvas = tk.Canvas(
            self.now_playing_window,
            width=window_width,
            height=window_height,
            bg=accent_color,
            bd=0,
            highlightthickness=0,
        )
        self.now_playing_canvas.pack(fill=tk.BOTH, expand=True)
        self._set_transparent_background(accent_color)
        padding = 10
        radius = 18
        shadow_offset = 4
        shadow_color = "#050505"
        self._draw_rounded_rect(
            self.now_playing_canvas,
            padding + shadow_offset,
            padding + shadow_offset,
            window_width - padding + shadow_offset,
            window_height - padding + shadow_offset,
            radius,
            fill=shadow_color,
            outline="",
        )
        self._draw_rounded_rect(
            self.now_playing_canvas,
            padding,
            padding,
            window_width - padding,
            window_height - padding,
            radius,
            fill=card_color,
            outline="",
        )
        self.now_playing_text_id = self.now_playing_canvas.create_text(
            padding * 2,
            window_height / 2,
            text="",
            font=("Helvetica", max(16, font_size // 2)),
            fill="#FFFFFF",
            anchor="w",
            justify=tk.LEFT,
        )

        self._now_playing_queue = queue.Queue()
        self.root.after(500, self._poll_now_playing_queue)
        thread = threading.Thread(target=self._now_playing_loop, daemon=True)
        thread.start()
        LOGGER.info(
            "Now-playing overlay enabled for %s (interval=%ss)",
            self._now_playing_entity,
            self._now_playing_interval,
        )

    def _now_playing_loop(self) -> None:
        while not self._now_playing_stop.is_set():
            text = ""
            try:
                payload = self._fetch_now_playing_state()
                text = self._format_now_playing(payload)
                LOGGER.debug("Now-playing metadata: %s", text or "<idle>")
            except Exception as exc:  # pylint: disable=broad-except
                LOGGER.warning("Failed to fetch now-playing metadata: %s", exc)
            if self._now_playing_queue:
                self._now_playing_queue.put(text)
            if self._now_playing_stop.wait(self._now_playing_interval):
                break

    def _fetch_now_playing_state(self) -> dict:
        url = f"{self._ha_base_url}/api/states/{self._now_playing_entity}"
        request = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {self._ha_token}",
                "Accept": "application/json",
            },
        )
        open_kwargs: dict[str, object] = {"timeout": 6}
        if self._ha_ssl_context is not None:
            open_kwargs["context"] = self._ha_ssl_context
        try:
            with urllib.request.urlopen(request, **open_kwargs) as response:  # type: ignore[arg-type]
                data = response.read()
        except urllib.error.HTTPError as exc:
            LOGGER.warning("HA returned %s when fetching %s: %s", exc.code, url, exc.reason)
            raise
        except urllib.error.URLError as exc:
            LOGGER.warning("HA connection error: %s", exc)
            raise
        return json.loads(data.decode("utf-8"))

    def _format_now_playing(self, payload: dict | list | None) -> str:
        if not isinstance(payload, dict):
            return ""
        state = str(payload.get("state") or "").lower()
        if state not in {"playing", "on"}:
            return ""
        attributes = payload.get("attributes") or {}
        title = attributes.get("media_title") or ""
        artist = attributes.get("media_artist") or attributes.get("media_album_artist") or ""
        if title and artist:
            return f"{artist} — {title}"
        return title or artist or ""

    @staticmethod
    def _draw_rounded_rect(
        canvas: tk.Canvas,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        radius: float,
        **kwargs,
    ) -> int:
        radius = max(0, min(radius, (x2 - x1) / 2, (y2 - y1) / 2))
        points = [
            x1 + radius,
            y1,
            x2 - radius,
            y1,
            x2,
            y1,
            x2,
            y1 + radius,
            x2,
            y2 - radius,
            x2,
            y2,
            x2 - radius,
            y2,
            x1 + radius,
            y2,
            x1,
            y2,
            x1,
            y2 - radius,
            x1,
            y1 + radius,
            x1,
            y1,
        ]
        return canvas.create_polygon(points, smooth=True, splinesteps=32, **kwargs)

    def _set_transparent_background(self, transparent_color: str) -> None:
        if not self.now_playing_window:
            return
        try:
            self.now_playing_window.attributes("-transparentcolor", transparent_color)
        except tk.TclError:
            LOGGER.debug("Window transparency unsupported on this platform; falling back to solid background.")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument("--timeout", type=int, default=int(os.environ.get("PULSE_ASSISTANT_DISPLAY_SECONDS", "8")))
    parser.add_argument("--font-size", type=int, default=int(os.environ.get("PULSE_ASSISTANT_FONT_SIZE", "28")))
    args = parser.parse_args()
    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO))

    mqtt_host = os.environ.get("MQTT_HOST")
    if not mqtt_host:
        raise RuntimeError("MQTT_HOST is not set; assistant display cannot subscribe")
    mqtt_port = int(os.environ.get("MQTT_PORT", "1883"))
    hostname = os.environ.get("PULSE_HOSTNAME") or os.uname().nodename
    topic = os.environ.get("PULSE_ASSISTANT_DISPLAY_TOPIC") or f"pulse/{hostname}/assistant/response"

    display = AssistantDisplay(mqtt_host, mqtt_port, topic, timeout=args.timeout, font_size=args.font_size)
    display.run()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
