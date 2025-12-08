#!/usr/bin/env python3
"""Minimal on-screen overlay for assistant responses."""

from __future__ import annotations

import argparse
import json
import logging
import os
import queue
import ssl
import tkinter as tk
from datetime import datetime

import paho.mqtt.client as mqtt
from pulse.utils import parse_bool

LOGGER = logging.getLogger("pulse-assistant-display")


def _int_from_env(value: str | None, fallback: int, minimum: int) -> int:
    if value is None:
        return max(minimum, fallback)
    try:
        parsed = int(value)
        return max(minimum, parsed)
    except ValueError:
        return max(minimum, fallback)


class AssistantDisplay:
    def __init__(
        self,
        mqtt_host: str,
        mqtt_port: int,
        topic: str,
        alarms_topic: str,
        timers_topic: str,
        command_topic: str,
        schedules_topic: str,
        timeout: int,
        font_size: int,
        client_id: str | None = None,
    ) -> None:
        self.topic = topic
        self._alarm_topic = alarms_topic
        self._timer_topic = timers_topic
        self._command_topic = command_topic
        self._schedules_topic = schedules_topic
        self._subscribed_topics = [topic, alarms_topic, timers_topic, schedules_topic]
        self.timeout_ms = max(1000, timeout * 1000)
        self.queue: queue.Queue[str] = queue.Queue()
        self._schedule_queue: queue.Queue[tuple[str, dict[str, object]]] = queue.Queue()
        self._state_queue: queue.Queue[dict[str, object]] = queue.Queue()
        self._hide_job: str | None = None
        callback_kwargs: dict[str, object] = {}
        if hasattr(mqtt, "CallbackAPIVersion"):
            callback_kwargs["callback_api_version"] = mqtt.CallbackAPIVersion.VERSION2
        self._client = mqtt.Client(client_id=client_id or "pulse-assistant-display", **callback_kwargs)
        raw_username = os.environ.get("MQTT_USER") or os.environ.get("MQTT_USERNAME")
        raw_password = os.environ.get("MQTT_PASS") or os.environ.get("MQTT_PASSWORD")
        username = raw_username.strip() if raw_username else ""
        password = raw_password.strip() if raw_password else ""
        if username:
            self._client.username_pw_set(username, password)
        tls_enabled = parse_bool(os.environ.get("MQTT_TLS_ENABLED"), False)
        certfile = (os.environ.get("MQTT_CERT") or "").strip() or None
        keyfile = (os.environ.get("MQTT_KEY") or "").strip() or None
        ca_cert = (os.environ.get("MQTT_CA_CERT") or "").strip() or None
        if tls_enabled:
            tls_kwargs: dict[str, object] = {"tls_version": getattr(ssl, "PROTOCOL_TLS_CLIENT", ssl.PROTOCOL_TLS)}
            if ca_cert:
                tls_kwargs["ca_certs"] = ca_cert
            if certfile:
                tls_kwargs["certfile"] = certfile
            if keyfile:
                tls_kwargs["keyfile"] = keyfile
            self._client.tls_set(**tls_kwargs)
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
        self.root.after(250, self._poll_schedule_queue)
        self.root.after(250, self._poll_state_queue)

        self.alarm_overlay = AlarmOverlay(self.root, self._client, command_topic)
        self.timer_panel = TimerPanel(self.root)

    def _on_connect(self, client, _userdata, _flags, reason_code, properties=None):  # type: ignore[no-untyped-def]
        if self._is_connect_success(reason_code):
            for topic in self._subscribed_topics:
                client.subscribe(topic)
        else:
            LOGGER.error("Failed to connect to MQTT (reason=%s, properties=%s)", reason_code, properties)

    @staticmethod
    def _is_connect_success(reason_code) -> bool:
        try:
            if hasattr(reason_code, "is_success"):
                return bool(reason_code.is_success())
            if hasattr(reason_code, "is_good"):
                return bool(reason_code.is_good())
            return int(reason_code) == 0
        except Exception:  # pragma: no cover - defensive
            return False

    def _on_message(self, _client, _userdata, message):  # type: ignore[no-untyped-def]
        try:
            payload = message.payload.decode("utf-8", errors="ignore")
            topic = getattr(message, "topic", "")
            if topic == self.topic:
                text = payload
                try:
                    parsed = json.loads(payload)
                    if isinstance(parsed, dict) and "text" in parsed:
                        text = str(parsed["text"])
                except json.JSONDecodeError:
                    pass
                if text:
                    self.queue.put(text.strip())
                return
            if topic == self._alarm_topic:
                data = self._decode_schedule_payload(payload)
                if data is not None:
                    self._schedule_queue.put(("alarm", data))
                return
            if topic == self._timer_topic:
                data = self._decode_schedule_payload(payload)
                if data is not None:
                    self._schedule_queue.put(("timer", data))
                return
            if topic == self._schedules_topic:
                data = self._decode_schedule_payload(payload)
                if data is not None:
                    self._state_queue.put(data)
                return
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

    def _poll_schedule_queue(self) -> None:
        try:
            while True:
                event_type, payload = self._schedule_queue.get_nowait()
                self.alarm_overlay.update(event_type, payload)
        except queue.Empty:
            pass
        self.root.after(200, self._poll_schedule_queue)

    def _poll_state_queue(self) -> None:
        try:
            while True:
                state = self._state_queue.get_nowait()
                timers = state.get("timers") if isinstance(state, dict) else None
                if isinstance(timers, list):
                    self.timer_panel.update(timers)
        except queue.Empty:
            pass
        self.root.after(500, self._poll_state_queue)

    @staticmethod
    def _decode_schedule_payload(payload: str) -> dict[str, object] | None:
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            return None
        if isinstance(data, dict):
            return data
        return None

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


class AlarmOverlay:
    def __init__(self, root: tk.Tk, mqtt_client: mqtt.Client, command_topic: str) -> None:
        self.root = root
        self._client = mqtt_client
        self._command_topic = command_topic
        self._current_event: dict[str, object] | None = None
        self._current_type: str | None = None

        self.window = tk.Toplevel(self.root)
        self.window.withdraw()
        self.window.overrideredirect(True)
        self.window.attributes("-topmost", True)
        self.window.configure(bg="#111111")
        width = max(420, self.root.winfo_screenwidth() // 2)
        height = 240
        offset_x = (self.root.winfo_screenwidth() - width) // 2
        offset_y = (self.root.winfo_screenheight() - height) // 2
        self.window.geometry(f"{width}x{height}+{offset_x}+{offset_y}")

        container = tk.Frame(self.window, bg="#111111", padx=20, pady=20)
        container.pack(fill=tk.BOTH, expand=True)

        self.type_label = tk.Label(container, text="", font=("Helvetica", 16, "bold"), fg="#AAAAAA", bg="#111111")
        self.type_label.pack(anchor="w")

        self.title_label = tk.Label(container, text="", font=("Helvetica", 32, "bold"), fg="#FFFFFF", bg="#111111")
        self.title_label.pack(anchor="w", pady=(8, 0))

        self.time_label = tk.Label(container, text="", font=("Helvetica", 20), fg="#DDDDDD", bg="#111111")
        self.time_label.pack(anchor="w", pady=(4, 16))

        button_row = tk.Frame(container, bg="#111111")
        button_row.pack(fill=tk.X, pady=(10, 0))

        self.stop_button = tk.Button(
            button_row,
            text="STOP",
            font=("Helvetica", 20, "bold"),
            bg="#C62828",
            fg="#FFFFFF",
            relief=tk.FLAT,
            command=self._stop_event,
        )
        self.stop_button.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 10))

        self.secondary_button = tk.Button(
            button_row,
            text="SNOOZE 5 MIN",
            font=("Helvetica", 18, "bold"),
            bg="#37474F",
            fg="#FFFFFF",
            relief=tk.FLAT,
            command=self._snooze_alarm,
        )
        self.secondary_button.pack(side=tk.LEFT, expand=True, fill=tk.X)

    def update(self, event_type: str, payload: dict[str, object]) -> None:
        state = (payload or {}).get("state")
        if state != "ringing":
            self._hide()
            return
        event = payload.get("event") if isinstance(payload, dict) else None
        if not isinstance(event, dict):
            self._hide()
            return
        event_id = event.get("id")
        if not isinstance(event_id, str):
            return
        self._current_event = event
        self._current_type = event_type
        label = str(event.get("label") or event_type.title())
        next_fire = str(event.get("next_fire") or event.get("target") or "")
        self.type_label.config(text=event_type.upper())
        self.title_label.config(text=label)
        self.time_label.config(text=self._format_time(next_fire))
        if event_type == "alarm":
            self.secondary_button.config(text="SNOOZE 5 MIN", command=self._snooze_alarm)
        else:
            self.secondary_button.config(text="ADD 3 MIN", command=self._add_timer_minutes)
        self.window.deiconify()
        self.window.lift()

    def _hide(self) -> None:
        self._current_event = None
        self._current_type = None
        self.window.withdraw()

    def _stop_event(self) -> None:
        if not self._current_event:
            return
        payload = {"action": "stop", "event_id": self._current_event.get("id")}
        self._publish_command(payload)
        self._hide()

    def _snooze_alarm(self) -> None:
        if not self._current_event or self._current_type != "alarm":
            return
        payload = {"action": "snooze", "event_id": self._current_event.get("id"), "minutes": 5}
        self._publish_command(payload)
        self._hide()

    def _add_timer_minutes(self) -> None:
        if not self._current_event:
            return
        payload = {"action": "add_time", "event_id": self._current_event.get("id"), "seconds": 180}
        self._publish_command(payload)

    def _publish_command(self, data: dict[str, object]) -> None:
        try:
            self._client.publish(self._command_topic, json.dumps(data))
        except Exception:  # pylint: disable=broad-except
            LOGGER.debug("Failed to publish schedule command", exc_info=True)

    @staticmethod
    def _format_time(next_fire: str) -> str:
        if not next_fire:
            return ""
        try:
            dt = datetime.fromisoformat(next_fire)
        except ValueError:
            return ""
        dt = dt.astimezone()
        return f"{dt.strftime('%A %I:%M %p').lstrip('0')}"


class TimerPanel:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.window = tk.Toplevel(self.root)
        self.window.withdraw()
        self.window.overrideredirect(True)
        self.window.attributes("-topmost", True)
        self.window.configure(bg="#050505")
        width = 360
        height = 160
        self.window.geometry(f"{width}x{height}+20+20")

        self.header = tk.Label(
            self.window,
            text="Timers",
            font=("Helvetica", 16, "bold"),
            fg="#88C0D0",
            bg="#050505",
            anchor="w",
        )
        self.header.pack(fill=tk.X, padx=12, pady=(8, 4))

        self.list_label = tk.Label(
            self.window,
            text="No timers running",
            font=("Helvetica", 18),
            fg="#FFFFFF",
            bg="#050505",
            justify=tk.LEFT,
            anchor="w",
        )
        self.list_label.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 12))
        self._timers: list[dict[str, object]] = []
        self._tick()

    def update(self, timers: list[dict[str, object]]) -> None:
        self._timers = timers
        self._render()

    def _tick(self) -> None:
        self._render()
        self.root.after(1000, self._tick)

    def _render(self) -> None:
        if not self._timers:
            self.list_label.config(text="No timers running")
            self.window.withdraw()
            return
        lines: list[str] = []
        now = datetime.now().astimezone()
        for timer in self._timers[:3]:
            label = str(timer.get("label") or "Timer")
            next_fire = timer.get("next_fire") or timer.get("target")
            time_left = self._format_remaining(next_fire, now)
            lines.append(f"{label}: {time_left}")
        text = "\n".join(lines)
        self.list_label.config(text=text)
        self.window.deiconify()
        self.window.lift()

    @staticmethod
    def _format_remaining(next_fire: object, now: datetime) -> str:
        try:
            dt = datetime.fromisoformat(str(next_fire))
        except (TypeError, ValueError):
            return "--:--"
        if dt.tzinfo is None:
            dt = dt.astimezone()
        remaining = int((dt - now).total_seconds())
        if remaining <= 0:
            return "00:00"
        minutes, seconds = divmod(remaining, 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        return f"{minutes:02d}:{seconds:02d}"


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
    base_topic = f"pulse/{hostname}/assistant"
    alarms_topic = f"{base_topic}/alarms/active"
    timers_topic = f"{base_topic}/timers/active"
    command_topic = f"{base_topic}/schedules/command"
    schedules_topic = f"{base_topic}/schedules/state"

    client_id = f"pulse-assistant-display-{hostname}"
    display = AssistantDisplay(
        mqtt_host,
        mqtt_port,
        topic,
        alarms_topic,
        timers_topic,
        command_topic,
        schedules_topic,
        timeout=args.timeout,
        font_size=args.font_size,
        client_id=client_id,
    )
    display.run()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
