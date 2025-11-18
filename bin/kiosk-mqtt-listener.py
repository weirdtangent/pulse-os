#!/usr/bin/env python3
import atexit
import json
import os
import re
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import paho.mqtt.client as mqtt
import psutil
import websocket
from packaging.version import InvalidVersion, Version


@dataclass(frozen=True)
class Topics:
    home: str
    goto: str
    update: str
    reboot: str
    volume: str
    brightness: str
    device: str
    availability: str
    update_availability: str
    telemetry: str


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
    sw_version: str | None
    topics: Topics
    devtools: DevToolsConfig
    version_source_url: str
    version_checks_per_day: int
    telemetry_interval_seconds: int


@dataclass(frozen=True)
class TelemetryDescriptor:
    key: str
    name: str
    unit: str | None
    device_class: str | None
    state_class: str | None
    icon: str | None
    precision: int | None = None


DEFAULT_VERSION_SOURCE_URL = "https://raw.githubusercontent.com/weirdtangent/pulse-os/main/VERSION"
DEFAULT_VERSION_CHECKS_PER_DAY = 12
ALLOWED_VERSION_CHECK_COUNTS = {2, 4, 6, 8, 12, 24}
DEFAULT_TELEMETRY_INTERVAL_SECONDS = 15
MIN_TELEMETRY_INTERVAL_SECONDS = 5
TELEMETRY_SENSORS: list[TelemetryDescriptor] = [
    TelemetryDescriptor(
        key="uptime_seconds",
        name="Uptime",
        unit="s",
        device_class="duration",
        state_class="total_increasing",
        icon="mdi:timer",
    ),
    TelemetryDescriptor(
        key="cpu_percent",
        name="CPU Usage",
        unit="%",
        device_class=None,
        state_class="measurement",
        icon="mdi:cpu-64-bit",
        precision=1,
    ),
    TelemetryDescriptor(
        key="cpu_temperature_c",
        name="CPU Temperature",
        unit="°C",
        device_class="temperature",
        state_class="measurement",
        icon="mdi:thermometer",
        precision=1,
    ),
    TelemetryDescriptor(
        key="memory_percent",
        name="Memory Usage",
        unit="%",
        device_class=None,
        state_class="measurement",
        icon="mdi:memory",
        precision=1,
    ),
    TelemetryDescriptor(
        key="disk_percent",
        name="Disk Usage",
        unit="%",
        device_class=None,
        state_class="measurement",
        icon="mdi:harddisk",
        precision=1,
    ),
    TelemetryDescriptor(
        key="load_avg_1m",
        name="Load Avg (1m)",
        unit=None,
        device_class=None,
        state_class="measurement",
        icon="mdi:chart-line",
        precision=2,
    ),
    TelemetryDescriptor(
        key="load_avg_5m",
        name="Load Avg (5m)",
        unit=None,
        device_class=None,
        state_class="measurement",
        icon="mdi:chart-line",
        precision=2,
    ),
    TelemetryDescriptor(
        key="load_avg_15m",
        name="Load Avg (15m)",
        unit=None,
        device_class=None,
        state_class="measurement",
        icon="mdi:chart-line",
        precision=2,
    ),
]


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
        volume=f"pulse/{hostname}/audio/volume/set",
        brightness=f"pulse/{hostname}/display/brightness/set",
        device=f"homeassistant/device/{hostname}/config",
        availability=f"homeassistant/device/{hostname}/availability",
        update_availability=f"pulse/{hostname}/kiosk/update/availability",
        telemetry=f"pulse/{hostname}/telemetry",
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

    telemetry_interval_seconds = max(
        MIN_TELEMETRY_INTERVAL_SECONDS,
        int(os.environ.get("PULSE_TELEMETRY_INTERVAL_SECONDS", DEFAULT_TELEMETRY_INTERVAL_SECONDS)),
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
        version_source_url=version_source_url,
        version_checks_per_day=version_checks_per_day,
        telemetry_interval_seconds=telemetry_interval_seconds,
    )


def _is_valid_ip(ip: str | None) -> bool:
    return bool(ip) and not ip.startswith("127.") and ip != "0.0.0.0"


def detect_ip_address(hostname: str) -> str | None:
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


def detect_mac_address() -> str | None:
    mac_env = os.environ.get("PULSE_MAC_ADDRESS")
    if mac_env:
        return mac_env
    node = uuid.getnode()
    if (node >> 40) % 2:
        return None
    return ":".join(f"{(node >> ele) & 0xFF:02X}" for ele in range(40, -1, -8))


def build_device_info(config: EnvConfig) -> dict[str, Any]:
    info: dict[str, Any] = {
        "identifiers": [f"pulse:{config.hostname}"],
        "name": config.friendly_name,
        "manufacturer": config.manufacturer,
        "model": config.model,
    }

    if config.sw_version:
        info["sw_version"] = config.sw_version

    connections: list[list[str]] = [["host", config.hostname]]

    ip_address = detect_ip_address(config.hostname)
    if ip_address:
        connections.append(["ip address", ip_address])

    mac_address = detect_mac_address()
    if mac_address:
        connections.append(["mac address", mac_address])

    if connections:
        info["connections"] = connections

    return info


def normalize_url(raw: str | bytes) -> str | None:
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
        self.latest_remote_version: str | None = None
        self.update_available = False
        self._update_state_lock = threading.Lock()
        self._update_interval_seconds = self._calculate_update_interval_seconds(config.version_checks_per_day)
        self._update_checker_thread: threading.Thread | None = None
        self._update_checker_lock = threading.Lock()
        self._update_checker_stop_event = threading.Event()
        self._mqtt_client: mqtt.Client | None = None
        self._telemetry_lock = threading.Lock()
        self._telemetry_thread: threading.Thread | None = None
        self._telemetry_stop_event = threading.Event()

    def log(self, message: str) -> None:
        log(message)

    def _build_origin(self) -> dict[str, Any]:
        origin: dict[str, Any] = {
            "name": "PulseOS",
            "support_url": "https://github.com/weirdtangent/pulse-os",
        }
        if self.config.sw_version:
            origin["sw"] = self.config.sw_version
        return origin

    def _sanitize_hostname_for_entity_id(self, hostname: str) -> str:
        """Convert hostname to a format suitable for Home Assistant entity IDs.

        Converts to lowercase and replaces hyphens/dots with underscores.
        Example: 'pulse-office' -> 'pulse_office'
        """
        return hostname.lower().replace("-", "_").replace(".", "_")

    def start_telemetry(self) -> None:
        with self._telemetry_lock:
            if self._telemetry_thread and self._telemetry_thread.is_alive():
                return
            self._telemetry_stop_event.clear()
            thread = threading.Thread(target=self._telemetry_loop, name="pulse-telemetry", daemon=True)
            self._telemetry_thread = thread
            thread.start()

    def stop_telemetry(self) -> None:
        with self._telemetry_lock:
            if not self._telemetry_thread:
                return
            self._telemetry_stop_event.set()
            self._telemetry_thread.join(timeout=self.config.telemetry_interval_seconds * 2)
            self._telemetry_thread = None

    def _telemetry_loop(self) -> None:
        interval = self.config.telemetry_interval_seconds
        # Prime CPU percent measurement
        try:
            psutil.cpu_percent(interval=None)
        except Exception:
            pass

        while not self._telemetry_stop_event.is_set():
            try:
                metrics = self._collect_telemetry_metrics()
                self._publish_telemetry(metrics)
            except Exception as exc:  # pylint: disable=broad-except
                self.log(f"telemetry: failed to publish metrics: {exc}")
            if self._telemetry_stop_event.wait(interval):
                break

    def _collect_telemetry_metrics(self) -> dict[str, int | float]:
        metrics: dict[str, int | float] = {}
        now = time.time()
        uptime_seconds = max(0, int(now - psutil.boot_time()))
        metrics["uptime_seconds"] = uptime_seconds

        cpu_percent = psutil.cpu_percent(interval=None)
        metrics["cpu_percent"] = round(cpu_percent, 1)

        mem = psutil.virtual_memory()
        metrics["memory_percent"] = round(mem.percent, 1)

        disk = psutil.disk_usage("/")
        metrics["disk_percent"] = round(disk.percent, 1)

        cpu_temp = self._read_cpu_temperature()
        if cpu_temp is not None:
            metrics["cpu_temperature_c"] = round(cpu_temp, 1)

        load1, load5, load15 = os.getloadavg()
        metrics["load_avg_1m"] = round(load1, 2)
        metrics["load_avg_5m"] = round(load5, 2)
        metrics["load_avg_15m"] = round(load15, 2)

        # Get current audio volume
        volume = self._get_current_volume()
        if volume is not None:
            metrics["volume"] = volume

        # Get current screen brightness
        brightness = self._get_current_brightness()
        if brightness is not None:
            metrics["brightness"] = brightness

        return metrics

    def _get_current_volume(self) -> int | None:
        """Get current volume percentage from audio sink."""
        sink = self._find_audio_sink()
        if not sink:
            return None

        try:
            # Use get-sink-volume for more reliable parsing
            result = subprocess.run(
                ["pactl", "get-sink-volume", sink],
                capture_output=True,
                text=True,
                check=True,
            )
            # Output format: "Volume: front-left: 32768 /  50% / -18.06 dB,   front-right: 32768 /  50% / -18.06 dB"
            # or simpler: "Volume: 0:  50%  1:  50%"
            match = re.search(r"(\d+)%", result.stdout)
            if match:
                return int(match.group(1))
        except (subprocess.CalledProcessError, FileNotFoundError):
            # Fallback: try list sinks if get-sink-volume fails
            try:
                result = subprocess.run(
                    ["pactl", "list", "sinks"],
                    capture_output=True,
                    text=True,
                    check=True,
                )
                lines = result.stdout.split("\n")
                in_sink = False
                for line in lines:
                    if f"Name: {sink}" in line:
                        in_sink = True
                    if in_sink and "Volume:" in line:
                        match = re.search(r"(\d+)%", line)
                        if match:
                            return int(match.group(1))
                    if in_sink and line.strip() == "" and "Volume:" in result.stdout[: result.stdout.find(line)]:
                        break
            except (subprocess.CalledProcessError, FileNotFoundError):
                pass
        return None

    def _get_current_brightness(self) -> int | None:
        """Get current screen brightness percentage."""
        device_path = self._find_backlight_device()
        if not device_path:
            return None

        try:
            # Try brightnessctl first
            result = subprocess.run(
                ["brightnessctl", "get"],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode == 0:
                # brightnessctl get returns raw value, need max to calculate %
                max_result = subprocess.run(
                    ["brightnessctl", "max"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if max_result.returncode == 0:
                    current = int(result.stdout.strip())
                    max_val = int(max_result.stdout.strip())
                    if max_val > 0:
                        return int((current * 100) / max_val)
        except (subprocess.CalledProcessError, FileNotFoundError, ValueError):
            pass

        # Fallback: read from sysfs
        try:
            device = Path(device_path)
            max_path = device / "max_brightness"
            brightness_path = device / "brightness"
            if max_path.exists() and brightness_path.exists():
                max_brightness = int(max_path.read_text(encoding="utf-8").strip())
                current_brightness = int(brightness_path.read_text(encoding="utf-8").strip())
                if max_brightness > 0:
                    return int((current_brightness * 100) / max_brightness)
        except (OSError, ValueError):
            pass
        return None

    @staticmethod
    def _read_cpu_temperature() -> float | None:
        try:
            temps = psutil.sensors_temperatures()
        except (NotImplementedError, AttributeError):
            temps = {}
        except Exception:
            temps = {}

        for key in ("cpu-thermal", "soc_thermal", "gpu", "coretemp", "arm"):
            entries = temps.get(key)
            if entries:
                current = entries[0].current
                if current is not None:
                    return float(current)

        candidate_paths = [
            "/sys/class/thermal/thermal_zone0/temp",
            "/sys/devices/virtual/thermal/thermal_zone0/temp",
        ]
        for path in candidate_paths:
            try:
                with open(path, encoding="utf-8") as handle:
                    raw = handle.read().strip()
                    value = float(raw) / (1000 if len(raw) > 3 else 1)
                    return value
            except (OSError, ValueError):
                continue
        return None

    def _publish_telemetry(self, metrics: dict[str, int | float]) -> None:
        base_topic = self.config.topics.telemetry
        for descriptor in TELEMETRY_SENSORS:
            value = metrics.get(descriptor.key)
            if value is None:
                continue
            topic = f"{base_topic}/{descriptor.key}"
            payload = self._format_metric_value(value, descriptor.precision)
            self._safe_publish(None, topic, payload, qos=0, retain=True)

    @staticmethod
    def _format_metric_value(value: int | float, precision: int | None) -> str:
        if precision is None:
            return str(value)
        format_str = f"{{:.{precision}f}}"
        return format_str.format(value)

    @staticmethod
    def _calculate_update_interval_seconds(checks_per_day: int) -> float:
        checks = max(1, checks_per_day)
        interval_hours = max(1.0, 24 / checks)
        return interval_hours * 3600

    def _detect_local_version(self) -> str | None:
        if self.config.sw_version:
            return self.config.sw_version
        version_path = os.path.join(self.repo_dir, "VERSION")
        try:
            with open(version_path, encoding="utf-8") as handle:
                value = handle.read().strip()
                return value or None
        except OSError:
            return None

    @staticmethod
    def _parse_version(value: str | None) -> Version | None:
        if not value:
            return None
        try:
            return Version(value.strip())
        except (InvalidVersion, AttributeError):
            return None

    def _remote_version_is_newer(self, remote: str | None, local: str | None) -> bool:
        remote_version = self._parse_version(remote)
        if remote_version is None:
            return False
        local_version = self._parse_version(local)
        if local_version is None:
            return True
        return remote_version > local_version

    def _fetch_remote_version(self) -> str | None:
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

    def _publish_latest_version(self, version: str | None) -> None:
        """Publish the latest available version to the sensor topic."""
        topic = f"{self.config.topics.telemetry}/latest_version"
        payload = version if version else "unknown"
        self._safe_publish(None, topic, payload, qos=0, retain=True)

    def _set_update_availability(self, available: bool, *, force: bool = False) -> None:
        should_publish = force
        with self._update_state_lock:
            if force or self.update_available != available:
                self.update_available = available
                should_publish = True
        if should_publish:
            self.publish_update_button_availability(None, available)

    def _safe_publish(
        self,
        client: mqtt.Client | None,
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

    def publish_update_button_availability(self, client: mqtt.Client | None, available: bool) -> None:
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
            self._publish_latest_version(None)
            return
        update_available = self._remote_version_is_newer(remote_version, local_version)
        self.local_version = local_version
        self.latest_remote_version = remote_version
        self._set_update_availability(update_available, force=True)
        self._publish_latest_version(remote_version)
        state = "available" if update_available else "not available"
        self.log(
            f"update-check: remote={remote_version}, local={local_version or 'unknown'} -> update {state}",
        )

    def is_update_available(self) -> bool:
        with self._update_state_lock:
            return self.update_available

    def fetch_page_targets(self) -> list[dict[str, Any]]:
        with urllib.request.urlopen(self.config.devtools.discovery_url, timeout=self.config.devtools.timeout) as resp:
            payload = json.load(resp)
        return [item for item in payload if item.get("type") == "page"]

    @staticmethod
    def pick_primary_target(pages: list[dict[str, Any]]) -> dict[str, Any] | None:
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
        # Add cache-busting parameter to force hard reload
        cache_buster = int(time.time() * 1000)  # milliseconds timestamp
        url = self.config.pulse_url
        # Parse URL to add/update cache-busting parameter
        parsed = urllib.parse.urlparse(url)
        query_params = urllib.parse.parse_qs(parsed.query)
        query_params["_reload"] = [str(cache_buster)]
        new_query = urllib.parse.urlencode(query_params, doseq=True)
        new_url = urllib.parse.urlunparse(
            (
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                parsed.params,
                new_query,
                parsed.fragment,
            )
        )
        self.navigate(new_url)

    def handle_goto(self, payload: bytes) -> None:
        url = normalize_url(payload)
        if not url:
            self.log("GOTO command ignored: empty payload")
            return
        self.navigate(url)

    def build_device_definition(self) -> dict[str, Any]:
        availability = {
            "topic": self.config.topics.availability,
            "pl_avail": "online",
            "pl_not_avail": "offline",
        }
        sanitized_hostname = self._sanitize_hostname_for_entity_id(self.config.hostname)
        home_button = {
            "platform": "button",
            "name": "Home",
            "default_entity_id": f"button.{sanitized_hostname}.home",
            "cmd_t": self.config.topics.home,
            "pl_press": "press",
            "unique_id": f"{self.config.hostname}_home",
        }
        reboot_button = {
            "platform": "button",
            "name": "Reboot",
            "default_entity_id": f"button.{sanitized_hostname}.reboot",
            "cmd_t": self.config.topics.reboot,
            "pl_press": "press",
            "unique_id": f"{self.config.hostname}_reboot",
            "entity_category": "config",
        }
        update_button = {
            "platform": "button",
            "name": "Update",
            "default_entity_id": f"button.{sanitized_hostname}.update",
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

        volume_control = {
            "platform": "number",
            "name": "Audio Volume",
            "default_entity_id": f"number.{sanitized_hostname}_volume",
            "cmd_t": self.config.topics.volume,
            "stat_t": f"{self.config.topics.telemetry}/volume",
            "unique_id": f"{self.config.hostname}_volume",
            "min": 0,
            "max": 100,
            "step": 1,
            "unit_of_meas": "%",
            "icon": "mdi:volume-high",
            "entity_category": "config",
        }

        brightness_control = {
            "platform": "number",
            "name": "Screen Brightness",
            "default_entity_id": f"number.{sanitized_hostname}_brightness",
            "cmd_t": self.config.topics.brightness,
            "stat_t": f"{self.config.topics.telemetry}/brightness",
            "unique_id": f"{self.config.hostname}_brightness",
            "min": 0,
            "max": 100,
            "step": 1,
            "unit_of_meas": "%",
            "icon": "mdi:brightness-6",
            "entity_category": "config",
        }

        latest_version_sensor = {
            "platform": "sensor",
            "name": "Latest version",
            "default_entity_id": f"sensor.{sanitized_hostname}_latest_version",
            "unique_id": f"{self.config.hostname}_latest_version",
            "stat_t": f"{self.config.topics.telemetry}/latest_version",
            "entity_category": "diagnostic",
            "icon": "mdi:package-up",
        }

        telemetry_components = self._build_telemetry_components()

        return {
            "device": self.device_info,
            "origin": self.origin,
            "availability": availability,
            "cmps": {
                "Home": home_button,
                "Reboot": reboot_button,
                "Update": update_button,
                "Audio Volume": volume_control,
                "Screen Brightness": brightness_control,
                "Latest version": latest_version_sensor,
                **telemetry_components,
            },
        }

    def _build_telemetry_components(self) -> dict[str, dict[str, Any]]:
        base_topic = self.config.topics.telemetry
        expire_after = max(self.config.telemetry_interval_seconds * 3, self.config.telemetry_interval_seconds + 5)
        sanitized_hostname = self._sanitize_hostname_for_entity_id(self.config.hostname)
        components: dict[str, dict[str, Any]] = {}
        for descriptor in TELEMETRY_SENSORS:
            cmps_entry: dict[str, Any] = {
                "platform": "sensor",
                "name": descriptor.name,
                "default_entity_id": f"sensor.{sanitized_hostname}_{descriptor.key}",
                "unique_id": f"{self.config.hostname}_{descriptor.key}",
                "stat_t": f"{base_topic}/{descriptor.key}",
                "entity_category": "diagnostic",
                "expire_after": expire_after,
            }
            if descriptor.unit:
                cmps_entry["unit_of_meas"] = descriptor.unit
            if descriptor.device_class:
                cmps_entry["dev_cla"] = descriptor.device_class
            if descriptor.state_class:
                cmps_entry["stat_cla"] = descriptor.state_class
            if descriptor.icon:
                cmps_entry["ic"] = descriptor.icon
            components[descriptor.name] = cmps_entry
        return components

    def publish_device_definition(self, client: mqtt.Client) -> None:
        payload = json.dumps(self.build_device_definition())
        self._safe_publish(client, self.config.topics.device, payload, qos=1, retain=True)

    def publish_availability(self, client: mqtt.Client, state: str) -> None:
        self._safe_publish(client, self.config.topics.availability, state, qos=1, retain=True)

    def on_connect(self, client, _userdata, _flags, rc):
        self.log(f"Connected to MQTT (rc={rc}); subscribing to topics")
        self._mqtt_client = client
        client.subscribe(self.config.topics.home)
        client.subscribe(self.config.topics.goto)
        client.subscribe(self.config.topics.update)
        client.subscribe(self.config.topics.reboot)
        client.subscribe(self.config.topics.volume)
        client.subscribe(self.config.topics.brightness)
        self.publish_device_definition(client)
        self.publish_availability(client, "online")
        # Publish cached latest version if available
        if self.latest_remote_version:
            self._publish_latest_version(self.latest_remote_version)
        try:
            self.refresh_update_availability()
        except Exception as exc:  # pylint: disable=broad-except
            self.log(f"update-check: initial refresh failed: {exc}")
        self.publish_update_button_availability(client, self.is_update_available())
        self.start_update_checker(client)
        self.start_telemetry()

    def on_message(self, _client, _userdata, msg):
        if msg.topic == self.config.topics.home:
            self.handle_home()
        elif msg.topic == self.config.topics.goto:
            self.handle_goto(msg.payload)
        elif msg.topic == self.config.topics.update:
            self.handle_update()
        elif msg.topic == self.config.topics.reboot:
            self.handle_reboot()
        elif msg.topic == self.config.topics.volume:
            self.handle_volume(msg.payload)
        elif msg.topic == self.config.topics.brightness:
            self.handle_brightness(msg.payload)
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

    @staticmethod
    def _find_audio_sink() -> str | None:
        """Find the audio sink to use for volume control.
        
        Works with any audio output: Bluetooth, USB, analog (ReSpeaker), etc.
        Prefers the default sink, falls back to any available sink.
        """
        try:
            # First, try to get the default sink (works for any audio type)
            result = subprocess.run(
                ["pactl", "get-default-sink"],
                capture_output=True,
                text=True,
                check=True,
            )
            default_sink = result.stdout.strip()
            if default_sink:
                return default_sink

            # Fallback: get the first available sink if no default is set
            result = subprocess.run(
                ["pactl", "list", "sinks", "short"],
                capture_output=True,
                text=True,
                check=True,
            )
            for line in result.stdout.split("\n"):
                if line.strip():
                    # Format: "<index> <name> <description>"
                    # Extract sink name (second field, index 1)
                    parts = line.split()
                    if len(parts) > 1:
                        sink_name = parts[1]
                        # Skip monitor sinks (they're for recording, not playback)
                        if not sink_name.endswith(".monitor"):
                            return sink_name
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass
        return None

    def handle_volume(self, payload: bytes) -> None:
        """Handle volume control command from MQTT."""
        try:
            volume_str = payload.decode("utf-8", errors="ignore").strip()
            volume = int(float(volume_str))
            # Clamp to valid range
            volume = max(0, min(100, volume))
        except (ValueError, TypeError):
            self.log(f"volume: invalid payload '{payload}', expected 0-100")
            return

        # Find audio sink dynamically (works with Bluetooth, USB, analog, etc.)
        sink = self._find_audio_sink()
        if not sink:
            self.log("volume: no audio sink found")
            return

        try:
            # Set volume using pactl
            subprocess.run(
                ["pactl", "set-sink-volume", sink, f"{volume}%"],
                check=True,
                capture_output=True,
            )
            # Unmute if volume > 0
            if volume > 0:
                subprocess.run(
                    ["pactl", "set-sink-mute", sink, "0"],
                    check=False,
                    capture_output=True,
                )
            self.log(f"volume: set to {volume}% on {sink}")
            # Publish current volume state
            self._safe_publish(
                None,
                f"{self.config.topics.telemetry}/volume",
                str(volume),
                qos=0,
                retain=True,
            )
        except subprocess.CalledProcessError as exc:
            self.log(f"volume: failed to set volume: {exc}")
        except FileNotFoundError:
            self.log("volume: pactl command not found")

    @staticmethod
    def _find_backlight_device() -> str | None:
        """Find the backlight device path."""
        # Try reading from config file first
        try:
            with open("/etc/pulse-backlight.conf", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if line.startswith("BACKLIGHT="):
                        device_path = line.split("=", 1)[1].strip()
                        if Path(device_path).exists():
                            return device_path
        except (OSError, IndexError):
            pass

        # Fallback: find any backlight device
        backlight_dir = Path("/sys/class/backlight")
        if backlight_dir.exists():
            for device in backlight_dir.iterdir():
                if (device / "brightness").exists() and (device / "max_brightness").exists():
                    return str(device)
        return None

    def handle_brightness(self, payload: bytes) -> None:
        """Handle brightness control command from MQTT."""
        try:
            brightness_str = payload.decode("utf-8", errors="ignore").strip()
            brightness = int(float(brightness_str))
            # Clamp to valid range
            brightness = max(0, min(100, brightness))
        except (ValueError, TypeError):
            self.log(f"brightness: invalid payload '{payload}', expected 0-100")
            return

        device_path = self._find_backlight_device()
        if not device_path:
            self.log("brightness: no backlight device found")
            return

        try:
            # Try brightnessctl first (easier and more portable)
            result = subprocess.run(
                ["brightnessctl", "set", f"{brightness}%"],
                check=False,
                capture_output=True,
            )
            if result.returncode == 0:
                self.log(f"brightness: set to {brightness}% using brightnessctl")
            else:
                # Fallback: write directly to sysfs
                device = Path(device_path)
                max_path = device / "max_brightness"
                brightness_path = device / "brightness"
                if max_path.exists() and brightness_path.exists():
                    max_brightness = int(max_path.read_text(encoding="utf-8").strip())
                    scaled = max(0, min(max_brightness, int(max_brightness * brightness / 100)))
                    brightness_path.write_text(f"{scaled}\n", encoding="utf-8")
                    self.log(f"brightness: set to {brightness}% via sysfs")
                else:
                    self.log("brightness: failed to set - device files not found")
                    return

            # Publish current brightness state
            self._safe_publish(
                None,
                f"{self.config.topics.telemetry}/brightness",
                str(brightness),
                qos=0,
                retain=True,
            )
        except (subprocess.CalledProcessError, OSError) as exc:
            self.log(f"brightness: failed to set brightness: {exc}")

    def _run_step(self, description: str, command: list[str], cwd: str | None) -> bool:
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
        steps: list[tuple[str, list[str], str | None]] = [
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
    atexit.register(listener.stop_telemetry)

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
