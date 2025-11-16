#!/usr/bin/env python3
import json
import os
import socket
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any, Dict, List, Optional, Union

import paho.mqtt.client as mqtt
import websocket

MQTT_HOST = os.environ.get("MQTT_HOST", "localhost")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
PULSE_URL = os.environ.get("PULSE_URL", "")

DEVTOOLS_DISCOVERY_URL = os.environ.get("CHROMIUM_DEVTOOLS_URL", "http://localhost:9222/json")
DEVTOOLS_TIMEOUT = float(os.environ.get("CHROMIUM_DEVTOOLS_TIMEOUT", "3"))

HOSTNAME = os.environ.get("PULSE_HOSTNAME") or os.uname().nodename
FRIENDLY_NAME = os.environ.get("PULSE_NAME") or HOSTNAME.replace("-", " ").title()
HOME_TOPIC = f"pulse/{HOSTNAME}/kiosk/home"
GOTO_TOPIC = f"pulse/{HOSTNAME}/kiosk/url/set"
DEVICE_TOPIC = f"homeassistant/device/{HOSTNAME}"
AVAILABILITY_TOPIC = f"{DEVICE_TOPIC}/availability"


def _is_valid_ip(ip: Optional[str]) -> bool:
    return bool(ip) and not ip.startswith("127.") and ip != "0.0.0.0"


def detect_ip_address() -> Optional[str]:
    ip_env = os.environ.get("PULSE_IP_ADDRESS")
    if _is_valid_ip(ip_env):
        return ip_env

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("1.1.1.1", 80))
        candidate = sock.getsockname()[0]
        sock.close()
        if _is_valid_ip(candidate):
            return candidate
    except OSError:
        pass

    try:
        candidate = socket.gethostbyname(HOSTNAME)
        if _is_valid_ip(candidate):
            return candidate
    except socket.gaierror:
        pass

    return None


def detect_mac_address() -> Optional[str]:
    mac_env = os.environ.get("PULSE_MAC_ADDRESS")
    if mac_env:
        return mac_env
    node = uuid.getnode()
    if (node >> 40) % 2:
        return None
    mac = ":".join(f"{(node >> ele) & 0xFF:02X}" for ele in range(40, -1, -8))
    return mac

DEVICE_INFO: Dict[str, Any] = {
    "identifiers": [f"pulse:{HOSTNAME}"],
    "name": FRIENDLY_NAME,
    "manufacturer": os.environ.get("PULSE_MANUFACTURER", "Pulse"),
    "model": os.environ.get("PULSE_MODEL", "Pulse Kiosk"),
}

_sw_version = os.environ.get("PULSE_VERSION")
if _sw_version:
    DEVICE_INFO["sw_version"] = _sw_version

connections: List[List[str]] = [["host", HOSTNAME]]
_ip_address = detect_ip_address()
if _ip_address:
    connections.append(["ip address", _ip_address])
_mac_address = detect_mac_address()
if _mac_address:
    connections.append(["mac address", _mac_address])

if connections:
    DEVICE_INFO["connections"] = connections


def log(message: str) -> None:
    print(f"[kiosk-mqtt] {message}", flush=True)


def fetch_page_targets() -> List[Dict[str, Any]]:
    with urllib.request.urlopen(DEVTOOLS_DISCOVERY_URL, timeout=DEVTOOLS_TIMEOUT) as resp:
        payload = json.load(resp)
    return [item for item in payload if item.get("type") == "page"]


def pick_primary_target(pages: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    for page in pages:
        url = page.get("url") or ""
        if url not in ("", "about:blank", "chrome://newtab/"):
            return page
    return pages[0] if pages else None


def normalize_url(raw: Union[str, bytes]) -> Optional[str]:
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="ignore")
    url = (raw or "").strip()
    if not url:
        return None

    parsed = urllib.parse.urlparse(url)
    if parsed.scheme in ("http", "https"):
        return url

    if not parsed.scheme:
        return f"http://{url}"

    return url


def navigate(url: str) -> bool:
    if not url:
        log("navigate: empty url, ignoring request")
        return False

    try:
        pages = fetch_page_targets()
    except urllib.error.URLError as exc:
        log(f"navigate: cannot reach DevTools endpoint {DEVTOOLS_DISCOVERY_URL}: {exc}")
        return False
    except json.JSONDecodeError as exc:
        log(f"navigate: invalid JSON from DevTools endpoint: {exc}")
        return False

    target = pick_primary_target(pages)
    if not target:
        log("navigate: no Chromium page targets available")
        return False

    ws_url = target.get("webSocketDebuggerUrl")
    if not ws_url:
        log("navigate: selected target is missing webSocketDebuggerUrl")
        return False

    try:
        ws = websocket.create_connection(ws_url, timeout=DEVTOOLS_TIMEOUT)
    except Exception as exc:
        log(f"navigate: failed to open DevTools websocket: {exc}")
        return False

    try:
        msg = {
            "id": 1,
            "method": "Page.navigate",
            "params": {"url": url},
        }
        ws.send(json.dumps(msg))
        log(f"navigate: directed tab {target.get('id')} -> {url}")
        return True
    except Exception as exc:
        log(f"navigate: websocket send failed: {exc}")
        return False
    finally:
        ws.close()


def on_connect(client, _userdata, _flags, rc):
    log(f"Connected to MQTT (rc={rc}); subscribing to topics")
    client.subscribe(HOME_TOPIC)
    client.subscribe(GOTO_TOPIC)
    publish_device_definition(client)
    publish_availability(client, "online")


def handle_home():
    if not PULSE_URL:
        log("HOME command received but PULSE_URL is not set")
        return
    navigate(PULSE_URL)


def handle_goto(payload: bytes):
    url = normalize_url(payload)
    if not url:
        log("GOTO command ignored: empty payload")
        return
    navigate(url)


def on_message(_client, _userdata, msg):
    if msg.topic == HOME_TOPIC:
        handle_home()
    elif msg.topic == GOTO_TOPIC:
        handle_goto(msg.payload)
    else:
        log(f"Received message on unexpected topic {msg.topic}")


def build_device_definition() -> Dict[str, Any]:
    availability = {
        "topic": AVAILABILITY_TOPIC,
        "payload_available": "online",
        "payload_not_available": "offline",
    }
    home_button = {
        "platform": "button",
        "object_id": "home",
        "name": "Home",
        "command_topic": HOME_TOPIC,
        "payload_press": "",
        "availability": [availability],
        "unique_id": f"{HOSTNAME}_home",
    }

    return {
        "device": DEVICE_INFO,
        "availability": availability,
        "cmps": {"Home": [home_button]},
    }


def publish_device_definition(client: mqtt.Client) -> None:
    payload = json.dumps(build_device_definition())
    result = client.publish(DEVICE_TOPIC, payload=payload, qos=1, retain=True)
    if result.rc != mqtt.MQTT_ERR_SUCCESS:
        log(f"Failed to publish device definition (rc={result.rc})")


def publish_availability(client: mqtt.Client, state: str) -> None:
    result = client.publish(AVAILABILITY_TOPIC, payload=state, qos=1, retain=True)
    if result.rc != mqtt.MQTT_ERR_SUCCESS:
        log(f"Failed to publish availability '{state}' (rc={result.rc})")


def main():
    client = mqtt.Client()
    client.on_connect = on_connect
    client.on_message = on_message
    client.will_set(AVAILABILITY_TOPIC, payload="offline", qos=1, retain=True)

    log(f"Connecting to MQTT broker {MQTT_HOST}:{MQTT_PORT}")
    client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
    client.loop_forever()


if __name__ == "__main__":
    main()
