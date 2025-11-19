#!/usr/bin/env python3
"""Minimal on-screen overlay for assistant responses."""

from __future__ import annotations

import argparse
import json
import logging
import os
import queue
import tkinter as tk

import paho.mqtt.client as mqtt

LOGGER = logging.getLogger("pulse-assistant-display")


class AssistantDisplay:
    def __init__(self, mqtt_host: str, mqtt_port: int, topic: str, timeout: int, font_size: int) -> None:
        self.topic = topic
        self.timeout_ms = max(1000, timeout * 1000)
        self.queue: queue.Queue[str] = queue.Queue()
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
        width = self.root.winfo_screenwidth()
        height = self.root.winfo_screenheight()
        frame_height = int(min(260, height * 0.25))
        self.root.geometry(f"{width}x{frame_height}+0+{height - frame_height}")
        self.root.configure(bg="#000000")
        self.root.attributes("-alpha", 0.82)

        self.label = tk.Label(
            self.root,
            text="",
            font=("Helvetica", font_size),
            fg="#FFFFFF",
            bg="#000000",
            wraplength=width - 80,
            justify=tk.LEFT,
        )
        self.label.pack(expand=True, fill=tk.BOTH, padx=40, pady=40)
        self.root.after(250, self._poll_queue)

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
