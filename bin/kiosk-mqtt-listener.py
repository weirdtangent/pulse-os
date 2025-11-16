#!/usr/bin/env python3
import atexit
import json
import os
import socket
import subprocess
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple, Union

import paho.mqtt.client as mqtt
import websocket
from packaging.version import InvalidVersion, Version


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
DEFAULT_VERSION_CHECKS_PER_DAY = 12
ALLOWED_VERSION_CHECK_COUNTS = {2, 4, 6, 8, 12, 24}


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
        self._mqtt_publish_lock = threading.Lock()
        self.repo_dir = "/opt/pulse-os"
        self.local_version = self._detect_local_version()
        self.latest_remote_version: Optional[str] = None
        self.update_available = False
        self._update_state_lock = threading.Lock()
        self._update_interval_seconds = self._calculate_update_interval_seconds(config.version_checks_per_day)
        self._update_checker_thread: Optional[threading.Thread] = None
        self._update_checker_lock = threading.Lock()
        self._update_checker_stop_event = threading.Event()
        self._mqtt_client: Optional[mqtt.Client] = None
        self._last_update_button_name = "Update"

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
    def _parse_version(value: Optional[str]) -> Optional[Version]:
        if not value:
            return None
        try:
            return Version(value.strip())
        except (InvalidVersion, AttributeError):
            return None

    def _remote_version_is_newer(self, remote: Optional[str], local: Optional[str]) -> bool:
        remote_version = self._parse_version(remote)
        if remote_version is None:
            return False
        local_version = self._parse_version(local)
        if local_version is None:
            return True
        return remote_version > local_version

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

    def _format_version_label(self, version: str) -> str:
        return version if version.lower().startswith("v") else f"v{version}"

    def _compute_update_button_name(self, *, available: Optional[bool] = None) -> str:
        if available is None:
            available = self.is_update_available()
        if available and self.latest_remote_version:
            label = self._format_version_label(self.latest_remote_version)
            return f"Update to {label}"
        return "Update"

    def _maybe_publish_update_button_definition(self) -> None:
        desired_name = self._compute_update_button_name()
        if desired_name == self._last_update_button_name:
            return
        client = self._mqtt_client
        if client:
            self.publish_device_definition(client)
        else:
            self._last_update_button_name = desired_name

    def _set_update_availability(self, available: bool, *, force: bool = False) -> None:
        should_publish = force
        with self._update_state_lock:
            if force or self.update_available != available:
                self.update_available = available
                should_publish = True
        if should_publish:
            self.publish_update_button_availability(None, available)
        self._maybe_publish_update_button_definition()

    def _safe_publish(
        self,
        client: Optional[mqtt.Client],
        topic: str,
        payload: str,
        *,
        qos: int = 1,
        retain: bool = True,
    ) -> None:
        target_client = client or self._mqtt_client
        if not target_client:
            return
        with self._mqtt_publish_lock:
            result = target_client.publish(topic, payload=payload, qos=qos, retain=retain)
        if result.rc != mqtt.MQTT_ERR_SUCCESS:
            self.log(f"Failed to publish topic '{topic}' payload '{payload}' (rc={result.rc})")

    def publish_update_button_availability(self, client: Optional[mqtt.Client], available: bool) -> None:
        topic = self.config.topics.update_availability
        payload = "online" if available else "offline"
        self._safe_publish(client, topic, payload, qos=1, retain=True)

    def start_update_checker(self, client: mqtt.Client) -> None:
        self._mqtt_client = client
        with self._update_checker_lock:
            if self._update_checker_thread and self._update_checker_thread.is_alive():
                return
            self._update_checker_stop_event.clear()
            thread = threading.Thread(target=self._update_checker_loop, name="pulse-version-check", daemon=True)
            self._update_checker_thread = thread
            thread.start()

    def stop_update_checker(self) -> None:
        with self._update_checker_lock:
            if not self._update_checker_thread:
                return
            self._update_checker_stop_event.set()
            self._update_checker_thread.join(timeout=self._update_interval_seconds)
            self._update_checker_thread = None

    def _update_checker_loop(self) -> None:
        interval = self._update_interval_seconds
        while not self._update_checker_stop_event.is_set():
            try:
                self.refresh_update_availability()
            except Exception as exc:
                self.log(f"update-check: unexpected error during cycle: {exc}")
            if self._update_checker_stop_event.wait(interval):
                break

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
            "name": self._compute_update_button_name(),
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
        self._safe_publish(client, self.config.topics.device, payload, qos=1, retain=True)
        self._last_update_button_name = self._compute_update_button_name()

    def publish_availability(self, client: mqtt.Client, state: str) -> None:
        self._safe_publish(client, self.config.topics.availability, state, qos=1, retain=True)

    def on_connect(self, client, _userdata, _flags, rc):
        self.log(f"Connected to MQTT (rc={rc}); subscribing to topics")
        self._mqtt_client = client
        client.subscribe(self.config.topics.home)
        client.subscribe(self.config.topics.goto)
        client.subscribe(self.config.topics.update)
        client.subscribe(self.config.topics.reboot)
        self.publish_device_definition(client)
        self.publish_availability(client, "online")
        try:
            self.refresh_update_availability()
        except Exception as exc:  # pylint: disable=broad-except
            self.log(f"update-check: initial refresh failed: {exc}")
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
    atexit.register(listener.stop_update_checker)

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
