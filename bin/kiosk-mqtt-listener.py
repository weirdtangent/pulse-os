#!/usr/bin/env python3
import paho.mqtt.client as mqtt
import subprocess
import os
import json
import urllib.request
import websocket

print("ENV DEBUG:", dict(os.environ))

MQTT_HOST = os.environ.get("MQTT_HOST", "localhost")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))

PULSE_URL = os.environ.get("PULSE_URL")

# Use device-specific topic based on hostname
HOSTNAME = os.uname().nodename
HOME_TOPIC = f"pulse/{HOSTNAME}/kiosk/home"
GOTO_TOPIC = f"pulse/{HOSTNAME}/kiosk/url/set"

def navigate(url: str):
    try:
        # 1. Fetch all DevTools pages
        with urllib.request.urlopen("http://localhost:9222/json") as resp:
            pages = json.load(resp)

        # 2. Pick the kiosk tab (the one that is not about:blank)
        target = None
        for p in pages:
            if p.get("type") == "page" and p.get("url") not in (None, "", "about:blank"):
                target = p
                break

        # If no real tab found, fall back to first page-type entry
        if not target:
            for p in pages:
                if p.get("type") == "page":
                    target = p
                    break

        if not target:
            print("No DevTools page target found, cannot navigate.")
            return

        ws_url = target["webSocketDebuggerUrl"]

        # 3. Open WebSocket and issue Page.navigate
        ws = websocket.create_connection(ws_url, timeout=2)
        msg = {
            "id": 1,
            "method": "Page.navigate",
            "params": {"url": url},
        }

        ws.send(json.dumps(msg))
        ws.close()

        print(f"Navigated to {url}")

    except Exception as e:
        print(f"Navigation error: {e}")

def on_connect(client, userdata, flags, rc):
    print(f"Connected to MQTT rc={rc}")
    client.subscribe(HOME_TOPIC)
    client.subscribe(GOTO_TOPIC)

def on_message(client, userdata, msg):
    if msg.topic.endswith("/home"):
        navigate(PULSE_URL)
    else:
        url = msg.payload.decode().strip()
        navigate(url)

client = mqtt.Client()
client.on_connect = on_connect
client.on_message = on_message

client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
client.loop_forever()
