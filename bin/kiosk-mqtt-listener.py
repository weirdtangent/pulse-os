#!/usr/bin/env python3
import json
import os
import socket
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Union

import paho.mqtt.client as mqtt
import websocket


@dataclass(frozen=True)
class Topics:
    home: str
    goto: str
    device: str
    availability: str


@dataclass(frozen=True)
class DevToolsConfig:
    discovery_url: str
    timeout: float


@dataclass(frozen=True)
class EnvConfig:
    mqtt_host: str
    mqtt_port: int
    pulse_url: str
    hostname: str
    friendly_name: str
    manufacturer: str
    model: str
    sw_version: Optional[str]
    topics: Topics
    devtools: DevToolsConfig


def log(message: str) -> None:
    print(f"[kiosk-mqtt] {message}", flush=True)


def load_config() -> EnvConfig:
    mqtt_host = os.environ.get("MQTT_HOST", "localhost")
    mqtt_port = int(os.environ.get("MQTT_PORT", "1883"))
    pulse_url = os.environ.get("PULSE_URL", "")
    hostname = os.environ.get("PULSE_HOSTNAME") or os.uname().nodename
    friendly_name = os.environ.get("PULSE_NAME") or hostname.replace("-", " ").title()
    manufacturer = os.environ.get("PULSE_MANUFACTURER", "Pulse")
    model = os.environ.get("PULSE_MODEL", "Pulse Kiosk")
    sw_version = os.environ.get("PULSE_VERSION")

    topics = Topics(
        home=f"pulse/{hostname}/kiosk/home",
        goto=f"pulse/{hostname}/kiosk/url/set",
        device=f"homeassistant/device/{hostname}/config",
        availability=f"homeassistant/device/{hostname}/availability",
    )

    devtools = DevToolsConfig(
        discovery_url=os.environ.get("CHROMIUM_DEVTOOLS_URL", "http://localhost:9222/json"),
        timeout=float(os.environ.get("CHROMIUM_DEVTOOLS_TIMEOUT", "3")),
    )

    return EnvConfig(
        mqtt_host=mqtt_host,
        mqtt_port=mqtt_port,
        pulse_url=pulse_url,
        hostname=hostname,
        friendly_name=friendly_name,
        manufacturer=manufacturer,
        model=model,
        sw_version=sw_version,
        topics=topics,
        devtools=devtools,
    )


def _is_valid_ip(ip: Optional[str]) -> bool:
    return bool(ip) and not ip.startswith("127.") and ip != "0.0.0.0"


def detect_ip_address(hostname: str) -> Optional[str]:
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
        candidate = socket.gethostbyname(hostname)
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
    return ":".join(f"{(node >> ele) & 0xFF:02X}" for ele in range(40, -1, -8))


def build_device_info(config: EnvConfig) -> Dict[str, Any]:
    info: Dict[str, Any] = {
        "identifiers": [f"pulse:{config.hostname}"],
        "name": config.friendly_name,
        "manufacturer": config.manufacturer,
        "model": config.model,
    }

    if config.sw_version:
        info["sw_version"] = config.sw_version

    connections: List[List[str]] = [["host", config.hostname]]

    ip_address = detect_ip_address(config.hostname)
    if ip_address:
        connections.append(["ip address", ip_address])

    mac_address = detect_mac_address()
    if mac_address:
        connections.append(["mac address", mac_address])

    if connections:
        info["connections"] = connections

    return info


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


class KioskMqttListener:
    def __init__(self, config: EnvConfig):
        self.config = config
        self.device_info = build_device_info(config)
        self.origin = self._build_origin()

    def log(self, message: str) -> None:
        log(message)

    def _build_origin(self) -> Dict[str, Any]:
        origin: Dict[str, Any] = {
            "name": "PulseOS",
            "support_url": "https://github.com/weirdtangent/pulse-os",
        }
        if self.config.sw_version:
            origin["sw"] = self.config.sw_version
        return origin

    def fetch_page_targets(self) -> List[Dict[str, Any]]:
        with urllib.request.urlopen(self.config.devtools.discovery_url, timeout=self.config.devtools.timeout) as resp:
            payload = json.load(resp)
        return [item for item in payload if item.get("type") == "page"]

    @staticmethod
    def pick_primary_target(pages: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        for page in pages:
            url = page.get("url") or ""
            if url not in ("", "about:blank", "chrome://newtab/"):
                return page
        return pages[0] if pages else None

    def navigate(self, url: str) -> bool:
        if not url:
            self.log("navigate: empty url, ignoring request")
            return False

        try:
            pages = self.fetch_page_targets()
        except urllib.error.URLError as exc:
            self.log(
                f"navigate: cannot reach DevTools endpoint {self.config.devtools.discovery_url}: {exc}",
            )
            return False
        except json.JSONDecodeError as exc:
            self.log(f"navigate: invalid JSON from DevTools endpoint: {exc}")
            return False

        target = self.pick_primary_target(pages)
        if not target:
            self.log("navigate: no Chromium page targets available")
            return False

        ws_url = target.get("webSocketDebuggerUrl")
        if not ws_url:
            self.log("navigate: selected target is missing webSocketDebuggerUrl")
            return False

        try:
            ws = websocket.create_connection(ws_url, timeout=self.config.devtools.timeout)
        except Exception as exc:
            self.log(f"navigate: failed to open DevTools websocket: {exc}")
            return False

        try:
            msg = {
                "id": 1,
                "method": "Page.navigate",
                "params": {"url": url},
            }
            ws.send(json.dumps(msg))
            self.log(f"navigate: directed tab {target.get('id')} -> {url}")
            return True
        except Exception as exc:
            self.log(f"navigate: websocket send failed: {exc}")
            return False
        finally:
            ws.close()

    def handle_home(self) -> None:
        if not self.config.pulse_url:
            self.log("HOME command received but PULSE_URL is not set")
            return
        self.navigate(self.config.pulse_url)

    def handle_goto(self, payload: bytes) -> None:
        url = normalize_url(payload)
        if not url:
            self.log("GOTO command ignored: empty payload")
            return
        self.navigate(url)

    def build_device_definition(self) -> Dict[str, Any]:
        availability = {
            "topic": self.config.topics.availability,
            "pl_avail": "online",
            "pl_not_avail": "offline",
        }
        home_button = {
            "platform": "button",
            "obj_id": "home",
            "name": "Home",
            "cmd_t": self.config.topics.home,
            "pl_press": "press",
            "unique_id": f"{self.config.hostname}_home",
        }

        return {
            "device": self.device_info,
            "origin": self.origin,
            "availability": availability,
            "cmps": {
                "Home": home_button,
            },
        }

    def publish_device_definition(self, client: mqtt.Client) -> None:
        payload = json.dumps(self.build_device_definition())
        result = client.publish(self.config.topics.device, payload=payload, qos=1, retain=True)
        if result.rc != mqtt.MQTT_ERR_SUCCESS:
            self.log(f"Failed to publish device definition (rc={result.rc})")

    def publish_availability(self, client: mqtt.Client, state: str) -> None:
        result = client.publish(self.config.topics.availability, payload=state, qos=1, retain=True)
        if result.rc != mqtt.MQTT_ERR_SUCCESS:
            self.log(f"Failed to publish availability '{state}' (rc={result.rc})")

    def on_connect(self, client, _userdata, _flags, rc):
        self.log(f"Connected to MQTT (rc={rc}); subscribing to topics")
        client.subscribe(self.config.topics.home)
        client.subscribe(self.config.topics.goto)
        self.publish_device_definition(client)
        self.publish_availability(client, "online")

    def on_message(self, _client, _userdata, msg):
        if msg.topic == self.config.topics.home:
            self.handle_home()
        elif msg.topic == self.config.topics.goto:
            self.handle_goto(msg.payload)
        else:
            self.log(f"Received message on unexpected topic {msg.topic}")


def main():
    config = load_config()
    listener = KioskMqttListener(config)

    client = mqtt.Client()
    client.on_connect = listener.on_connect
    client.on_message = listener.on_message
    client.will_set(
        config.topics.availability,
        payload="offline",
        qos=1,
        retain=True,
    )

    listener.log(f"Connecting to MQTT broker {config.mqtt_host}:{config.mqtt_port}")
    client.connect(config.mqtt_host, config.mqtt_port, keepalive=60)
    client.loop_forever()


if __name__ == "__main__":
    main()
