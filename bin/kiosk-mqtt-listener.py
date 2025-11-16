#!/usr/bin/env python3
import json
import os
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple, Union

import paho.mqtt.client as mqtt
import websocket


@dataclass(frozen=True)
class Topics:
    home: str
    goto: str
    update: str
    reboot: str
    device: str
    availability: str
    update_availability: str


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
    version_source_url: str
    version_checks_per_day: int


DEFAULT_VERSION_SOURCE_URL = "https://raw.githubusercontent.com/weirdtangent/pulse-os/main/VERSION"
DEFAULT_VERSION_CHECKS_PER_DAY = 4
ALLOWED_VERSION_CHECK_COUNTS = {2, 4, 6}


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
        update=f"pulse/{hostname}/kiosk/update",
        reboot=f"pulse/{hostname}/kiosk/reboot",
        device=f"homeassistant/device/{hostname}/config",
        availability=f"homeassistant/device/{hostname}/availability",
        update_availability=f"pulse/{hostname}/kiosk/update/availability",
    )

    devtools = DevToolsConfig(
        discovery_url=os.environ.get("CHROMIUM_DEVTOOLS_URL", "http://localhost:9222/json"),
        timeout=float(os.environ.get("CHROMIUM_DEVTOOLS_TIMEOUT", "3")),
    )

    version_source_url = os.environ.get("PULSE_VERSION_SOURCE_URL", DEFAULT_VERSION_SOURCE_URL)
    raw_checks = os.environ.get("PULSE_VERSION_CHECKS_PER_DAY")
    version_checks_per_day = DEFAULT_VERSION_CHECKS_PER_DAY
    if raw_checks is not None:
        try:
            candidate = int(raw_checks)
        except ValueError:
            candidate = DEFAULT_VERSION_CHECKS_PER_DAY
        if candidate in ALLOWED_VERSION_CHECK_COUNTS:
            version_checks_per_day = candidate

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
        version_source_url=version_source_url,
        version_checks_per_day=version_checks_per_day,
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
        self.update_lock = threading.Lock()
        self.reboot_lock = threading.Lock()
        self.repo_dir = "/opt/pulse-os"
        self.local_version = self._detect_local_version()
        self.latest_remote_version: Optional[str] = None
        self.update_available = False
        self._update_state_lock = threading.Lock()
        self._update_interval_seconds = self._calculate_update_interval_seconds(config.version_checks_per_day)
        self._update_checker_thread: Optional[threading.Thread] = None
        self._update_checker_lock = threading.Lock()
        self._mqtt_client: Optional[mqtt.Client] = None

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

    @staticmethod
    def _calculate_update_interval_seconds(checks_per_day: int) -> float:
        checks = max(1, checks_per_day)
        interval_hours = max(1.0, 24 / checks)
        return interval_hours * 3600

    def _detect_local_version(self) -> Optional[str]:
        if self.config.sw_version:
            return self.config.sw_version
        version_path = os.path.join(self.repo_dir, "VERSION")
        try:
            with open(version_path, "r", encoding="utf-8") as handle:
                value = handle.read().strip()
                return value or None
        except OSError:
            return None

    @staticmethod
    def _normalize_version_parts(value: Optional[str]) -> Tuple[int, ...]:
        if not value:
            return ()
        parts: List[int] = []
        for chunk in value.replace("-", ".").split("."):
            chunk = chunk.strip()
            if not chunk:
                continue
            numeric = "".join(ch for ch in chunk if ch.isdigit())
            if numeric:
                parts.append(int(numeric))
                continue
            try:
                parts.append(int(chunk))
            except ValueError:
                break
        return tuple(parts)

    def _remote_version_is_newer(self, remote: Optional[str], local: Optional[str]) -> bool:
        if remote is None:
            return False
        remote_parts = self._normalize_version_parts(remote)
        if not remote_parts:
            return False
        local_parts = self._normalize_version_parts(local)
        if not local_parts:
            return True
        length = max(len(remote_parts), len(local_parts))
        remote_seq = remote_parts + (0,) * (length - len(remote_parts))
        local_seq = local_parts + (0,) * (length - len(local_parts))
        return remote_seq > local_seq

    def _fetch_remote_version(self) -> Optional[str]:
        url = self.config.version_source_url
        try:
            with urllib.request.urlopen(url, timeout=10) as resp:
                value = resp.read().decode("utf-8", errors="ignore").strip()
                return value or None
        except urllib.error.URLError as exc:
            self.log(f"update-check: failed to fetch remote version from {url}: {exc}")
        except Exception as exc:
            self.log(f"update-check: unexpected error while fetching remote version: {exc}")
        return None

    def _set_update_availability(self, available: bool, *, force: bool = False) -> None:
        should_publish = force
        with self._update_state_lock:
            if force or self.update_available != available:
                self.update_available = available
                should_publish = True
        if should_publish:
            self.publish_update_button_availability(None, available)

    def publish_update_button_availability(self, client: Optional[mqtt.Client], available: bool) -> None:
        target_client = client or self._mqtt_client
        if not target_client:
            return
        topic = self.config.topics.update_availability
        payload = "online" if available else "offline"
        result = target_client.publish(topic, payload=payload, qos=1, retain=True)
        if result.rc != mqtt.MQTT_ERR_SUCCESS:
            self.log(f"Failed to publish update availability '{payload}' (rc={result.rc})")

    def start_update_checker(self, client: mqtt.Client) -> None:
        self._mqtt_client = client
        with self._update_checker_lock:
            if self._update_checker_thread and self._update_checker_thread.is_alive():
                return
            thread = threading.Thread(target=self._update_checker_loop, name="pulse-version-check", daemon=True)
            self._update_checker_thread = thread
            thread.start()

    def _update_checker_loop(self) -> None:
        interval = self._update_interval_seconds
        while True:
            try:
                self.refresh_update_availability()
            except Exception as exc:
                self.log(f"update-check: unexpected error during cycle: {exc}")
            time.sleep(interval)

    def refresh_update_availability(self) -> None:
        local_version = self._detect_local_version()
        remote_version = self._fetch_remote_version()
        if remote_version is None:
            self._set_update_availability(False)
            return
        update_available = self._remote_version_is_newer(remote_version, local_version)
        self.local_version = local_version
        self.latest_remote_version = remote_version
        self._set_update_availability(update_available, force=True)
        state = "available" if update_available else "not available"
        self.log(
            f"update-check: remote={remote_version}, local={local_version or 'unknown'} -> update {state}",
        )

    def is_update_available(self) -> bool:
        with self._update_state_lock:
            return self.update_available

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
            "name": "Home",
            "default_entity_id": "button.home",
            "cmd_t": self.config.topics.home,
            "pl_press": "press",
            "unique_id": f"{self.config.hostname}_home",
        }
        reboot_button = {
            "platform": "button",
            "name": "Reboot",
            "default_entity_id": "button.reboot",
            "cmd_t": self.config.topics.reboot,
            "pl_press": "press",
            "unique_id": f"{self.config.hostname}_reboot",
            "entity_category": "config",
        }
        update_button = {
            "platform": "button",
            "name": "Update",
            "default_entity_id": "button.update",
            "cmd_t": self.config.topics.update,
            "pl_press": "press",
            "unique_id": f"{self.config.hostname}_update",
            "entity_category": "config",
            "availability": {
                "topic": self.config.topics.update_availability,
                "pl_avail": "online",
                "pl_not_avail": "offline",
            },
        }

        return {
            "device": self.device_info,
            "origin": self.origin,
            "availability": availability,
            "cmps": {
                "Home": home_button,
                "Reboot": reboot_button,
                "Update": update_button,
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
        client.subscribe(self.config.topics.update)
        client.subscribe(self.config.topics.reboot)
        self.publish_device_definition(client)
        self.publish_availability(client, "online")
        self.publish_update_button_availability(client, self.is_update_available())
        self.start_update_checker(client)

    def on_message(self, _client, _userdata, msg):
        if msg.topic == self.config.topics.home:
            self.handle_home()
        elif msg.topic == self.config.topics.goto:
            self.handle_goto(msg.payload)
        elif msg.topic == self.config.topics.update:
            self.handle_update()
        elif msg.topic == self.config.topics.reboot:
            self.handle_reboot()
        else:
            self.log(f"Received message on unexpected topic {msg.topic}")

    def handle_update(self) -> None:
        if not self.is_update_available():
            self.log("update: request ignored because no update is available")
            return
        if not self.update_lock.acquire(blocking=False):
            self.log("update: request ignored because another update is running")
            return
        self._set_update_availability(False, force=True)
        thread = threading.Thread(target=self._perform_update, name="pulse-update", daemon=True)
        thread.start()

    def handle_reboot(self) -> None:
        if not self.reboot_lock.acquire(blocking=False):
            self.log("reboot: request ignored because another reboot is running")
            return
        thread = threading.Thread(target=self._perform_reboot, name="pulse-reboot", daemon=True)
        thread.start()

    def _run_step(self, description: str, command: List[str], cwd: Optional[str]) -> bool:
        display_cmd = " ".join(command)
        self.log(f"update: running {description}: {display_cmd}")
        try:
            subprocess.run(command, cwd=cwd, check=True)
            return True
        except FileNotFoundError as exc:
            self.log(f"update: command for {description} not found: {exc}")
        except subprocess.CalledProcessError as exc:
            self.log(f"update: {description} failed with exit code {exc.returncode}")
        return False

    def _perform_update(self) -> None:
        repo_dir = self.repo_dir
        steps: List[Tuple[str, List[str], Optional[str]]] = [
            ("git pull", ["git", "pull", "--ff-only"], repo_dir),
            ("setup.sh", ["./setup.sh"], repo_dir),
            ("reboot", ["sudo", "reboot", "now"], repo_dir),
        ]

        try:
            self.log("update: starting full update cycle")
            for description, command, cwd in steps:
                if not self._run_step(description, command, cwd):
                    self.log(f"update: aborted during {description}")
                    return
            self.log("update: finished successfully; reboot command issued")
        finally:
            self.update_lock.release()
            try:
                self.refresh_update_availability()
            except Exception as exc:
                self.log(f"update-check: failed to refresh availability after update: {exc}")

    def _perform_reboot(self) -> None:
        try:
            self.log("reboot: issuing sudo reboot now")
            subprocess.run(["sudo", "reboot", "now"], check=True)
        except FileNotFoundError as exc:
            self.log(f"reboot: command not found: {exc}")
        except subprocess.CalledProcessError as exc:
            self.log(f"reboot: command failed with exit code {exc.returncode}")
        finally:
            self.reboot_lock.release()


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
