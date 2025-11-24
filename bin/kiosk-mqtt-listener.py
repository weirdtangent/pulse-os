#!/usr/bin/env python3
import atexit
import json
import os
import socket
import ssl
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
from pulse import audio, display
from pulse.mqtt_discovery import build_button_entity, build_number_entity
from pulse.overlay import (
    ClockConfig,
    OverlayChange,
    OverlayStateManager,
    OverlayTheme,
    parse_clock_config,
)
from pulse.overlay_server import OverlayHttpServer, OverlayServerConfig


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
    overlay_refresh: str


@dataclass(frozen=True)
class DevToolsConfig:
    discovery_url: str
    timeout: float


@dataclass(frozen=True)
class AssistantTopics:
    base: str
    schedules_state: str
    alarms_active: str
    timers_active: str
    reminders_active: str
    command: str
    info_card: str


@dataclass(frozen=True)
class OverlayConfig:
    enabled: bool
    bind_address: str
    port: int
    allowed_origins: tuple[str, ...]
    clocks: tuple[ClockConfig, ...]
    ambient_background: str
    alert_background: str
    text_color: str
    accent_color: str
    show_notification_bar: bool
    clock_24h: bool


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
    volume_feedback_enabled: bool
    media_player_entity: str | None
    ha_base_url: str
    ha_token: str
    ha_verify_ssl: bool
    assistant_topics: AssistantTopics
    overlay: OverlayConfig


@dataclass(frozen=True)
class TelemetryDescriptor:
    key: str
    name: str
    unit: str | None
    device_class: str | None
    state_class: str | None
    icon: str | None
    precision: int | None = None
    entity_category: str | None = "diagnostic"
    expose_sensor: bool = True


DEFAULT_VERSION_SOURCE_URL = "https://raw.githubusercontent.com/weirdtangent/pulse-os/main/VERSION"
DEFAULT_VERSION_CHECKS_PER_DAY = 12
ALLOWED_VERSION_CHECK_COUNTS = {2, 4, 6, 8, 12, 24}
DEFAULT_TELEMETRY_INTERVAL_SECONDS = 15
MIN_TELEMETRY_INTERVAL_SECONDS = 5
TELEMETRY_SENSORS: list[TelemetryDescriptor] = [
    TelemetryDescriptor(
        key="uptime_seconds",
        name="Uptime",
        unit=None,
        device_class="duration",
        state_class="total_increasing",
        icon="mdi:timer",
    ),
    TelemetryDescriptor(
        key="cpu_usage",
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
        key="memory_usage",
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
    TelemetryDescriptor(
        key="volume",
        name="Audio Volume",
        unit="%",
        device_class=None,
        state_class="measurement",
        icon="mdi:volume-high",
        expose_sensor=False,
    ),
    TelemetryDescriptor(
        key="brightness",
        name="Screen Brightness",
        unit="%",
        device_class=None,
        state_class="measurement",
        icon="mdi:brightness-6",
        expose_sensor=False,
    ),
    TelemetryDescriptor(
        key="now_playing",
        name="Now Playing",
        unit=None,
        device_class=None,
        state_class=None,
        icon="mdi:music-note",
        precision=None,
        entity_category=None,
    ),
]


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def log(message: str) -> None:
    print(f"[kiosk-mqtt] {message}", flush=True)


def load_config() -> EnvConfig:
    mqtt_host = os.environ.get("MQTT_HOST", "localhost")
    mqtt_port = int(os.environ.get("MQTT_PORT", "1883"))
    hostname = os.environ.get("PULSE_HOSTNAME") or os.uname().nodename
    pulse_url = _ensure_pulse_host_param(os.environ.get("PULSE_URL", ""), hostname)
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
        overlay_refresh=f"pulse/{hostname}/overlay/refresh",
    )

    devtools = DevToolsConfig(
        discovery_url=os.environ.get("CHROMIUM_DEVTOOLS_URL", "http://localhost:9222/json"),
        timeout=float(os.environ.get("CHROMIUM_DEVTOOLS_TIMEOUT", "3")),
    )

    overlay_enabled = _as_bool(os.environ.get("PULSE_OVERLAY_ENABLED"), True)
    overlay_port = int(os.environ.get("PULSE_OVERLAY_PORT", "8800"))
    overlay_bind = (os.environ.get("PULSE_OVERLAY_BIND") or "0.0.0.0").strip() or "0.0.0.0"
    overlay_allowed_raw = os.environ.get("PULSE_OVERLAY_ALLOWED_ORIGINS", "*")
    overlay_allowed_origins = tuple(origin.strip() for origin in overlay_allowed_raw.split(",") if origin.strip()) or (
        "*",
    )
    overlay_clock_spec = os.environ.get("PULSE_OVERLAY_CLOCK")
    overlay_clocks = parse_clock_config(
        overlay_clock_spec,
        default_label=friendly_name,
        log=log,
    )
    overlay_config = OverlayConfig(
        enabled=overlay_enabled,
        bind_address=overlay_bind,
        port=overlay_port,
        allowed_origins=overlay_allowed_origins,
        clocks=overlay_clocks,
        ambient_background=os.environ.get("PULSE_OVERLAY_AMBIENT_BG", "rgba(0, 0, 0, 0.32)"),
        alert_background=os.environ.get("PULSE_OVERLAY_ALERT_BG", "rgba(0, 0, 0, 0.65)"),
        text_color=os.environ.get("PULSE_OVERLAY_TEXT_COLOR", "#FFFFFF"),
        accent_color=os.environ.get("PULSE_OVERLAY_ACCENT_COLOR", "#88C0D0"),
        show_notification_bar=_as_bool(os.environ.get("PULSE_OVERLAY_NOTIFICATION_BAR"), True),
        clock_24h=_as_bool(os.environ.get("PULSE_OVERLAY_CLOCK_24H"), False),
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

    volume_feedback_enabled = _as_bool(os.environ.get("PULSE_VOLUME_TEST_SOUND"), default=True)

    media_player_entity = (os.environ.get("PULSE_MEDIA_PLAYER_ENTITY") or "").strip()
    if not media_player_entity:
        sanitized = hostname.lower().replace("-", "_").replace(".", "_")
        media_player_entity = f"media_player.{sanitized}_2"

    ha_base_url = (os.environ.get("HOME_ASSISTANT_BASE_URL") or "").strip()
    ha_token = (os.environ.get("HOME_ASSISTANT_TOKEN") or "").strip()
    ha_verify_ssl = _as_bool(os.environ.get("HOME_ASSISTANT_VERIFY_SSL"), default=True)
    if not ha_base_url or not ha_token:
        media_player_entity = None

    assistant_base = f"pulse/{hostname}/assistant"
    assistant_topics = AssistantTopics(
        base=assistant_base,
        schedules_state=f"{assistant_base}/schedules/state",
        alarms_active=f"{assistant_base}/alarms/active",
        timers_active=f"{assistant_base}/timers/active",
        reminders_active=f"{assistant_base}/reminders/active",
        command=f"{assistant_base}/schedules/command",
        info_card=f"{assistant_base}/info_card",
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
        volume_feedback_enabled=volume_feedback_enabled,
        media_player_entity=media_player_entity,
        ha_base_url=ha_base_url,
        ha_token=ha_token,
        ha_verify_ssl=ha_verify_ssl,
        assistant_topics=assistant_topics,
        overlay=overlay_config,
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


def _ensure_pulse_host_param(url: str, hostname: str | None) -> str:
    if not url or not hostname:
        return url
    parsed = urllib.parse.urlparse(url)
    query_items = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    if any(key == "pulse_host" for key, _ in query_items):
        return url
    query_items.append(("pulse_host", hostname))
    new_query = urllib.parse.urlencode(query_items)
    return urllib.parse.urlunparse(parsed._replace(query=new_query))


def _is_mqtt_success(reason_code) -> bool:
    try:
        if hasattr(reason_code, "is_success"):
            return bool(reason_code.is_success())
        if hasattr(reason_code, "is_good"):
            return bool(reason_code.is_good())
        candidate = reason_code
        if hasattr(reason_code, "value"):
            candidate = reason_code.value
        if isinstance(candidate, str):
            normalized = candidate.strip().lower()
            if normalized in {"success", "granted qos0", "granted qos 0"}:
                return True
            try:
                candidate = int(normalized, 0)
            except ValueError:
                return False
        return int(candidate) == 0
    except Exception:  # pragma: no cover - defensive guard
        return False


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
        self._ha_ssl_context = self._build_ha_ssl_context()
        self._last_now_playing_error: float = 0.0
        self.assistant_topics = config.assistant_topics
        self.overlay_config = config.overlay
        self.overlay_state: OverlayStateManager | None = None
        self._overlay_theme: OverlayTheme | None = None
        self._overlay_http: OverlayHttpServer | None = None
        self._overlay_topic_handlers: dict[str, Any] = {}
        overlay_host = self.overlay_config.bind_address
        if overlay_host in {"0.0.0.0", "::"}:
            overlay_host = "localhost"
        if ":" in overlay_host and not overlay_host.startswith("["):
            overlay_host = f"[{overlay_host}]"
        base_overlay_url = f"http://{overlay_host}:{self.overlay_config.port}"
        self._overlay_stop_endpoint = f"{base_overlay_url}/overlay/stop"
        self._overlay_info_endpoint = f"{base_overlay_url}/overlay/info-card"

        if self.overlay_config.enabled:
            self.overlay_state = OverlayStateManager(self.overlay_config.clocks)
            self._overlay_theme = OverlayTheme(
                ambient_background=self.overlay_config.ambient_background,
                alert_background=self.overlay_config.alert_background,
                text_color=self.overlay_config.text_color,
                accent_color=self.overlay_config.accent_color,
                show_notification_bar=self.overlay_config.show_notification_bar,
            )
            self._overlay_topic_handlers = {
                self.assistant_topics.schedules_state: self._handle_overlay_schedule_state,
                self.assistant_topics.alarms_active: lambda payload: self._handle_overlay_active_event(
                    "alarm", payload
                ),
                self.assistant_topics.timers_active: lambda payload: self._handle_overlay_active_event(
                    "timer", payload
                ),
                self.assistant_topics.reminders_active: lambda payload: self._handle_overlay_active_event(
                    "reminder", payload
                ),
            }
            server_config = OverlayServerConfig(
                bind_address=self.overlay_config.bind_address,
                port=self.overlay_config.port,
                allowed_origins=self.overlay_config.allowed_origins,
                clock_24h=self.overlay_config.clock_24h,
                stop_endpoint=self._overlay_stop_endpoint,
                info_endpoint=self._overlay_info_endpoint,
            )
            self._overlay_http = OverlayHttpServer(
                state=self.overlay_state,
                theme=self._overlay_theme,
                config=server_config,
                logger=self.log,
                on_state_change=self._handle_overlay_change,
                on_stop_request=self._handle_overlay_stop_request,
                on_snooze_request=self._handle_overlay_snooze_request,
                on_delete_alarm=self._handle_overlay_delete_alarm_request,
                on_pause_alarm=self._handle_overlay_pause_alarm_request,
                on_resume_alarm=self._handle_overlay_resume_alarm_request,
                on_complete_reminder=self._handle_overlay_complete_reminder_request,
                on_delay_reminder=self._handle_overlay_delay_reminder_request,
                on_delete_reminder=self._handle_overlay_delete_reminder_request,
            )

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

    def _collect_telemetry_metrics(self) -> dict[str, int | float | str]:
        metrics: dict[str, int | float | str] = {}
        now = time.time()
        uptime_seconds = max(0, int(now - psutil.boot_time()))
        metrics["uptime_seconds"] = uptime_seconds

        cpu_percent = psutil.cpu_percent(interval=None)
        metrics["cpu_usage"] = round(cpu_percent, 1)

        mem = psutil.virtual_memory()
        metrics["memory_usage"] = round(mem.percent, 1)

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

        now_playing = self._collect_now_playing_text()
        metrics["now_playing"] = now_playing

        if self.overlay_state:
            change = self.overlay_state.update_now_playing(now_playing)
            self._handle_overlay_change(change)

        return metrics

    def _get_current_volume(self) -> int | None:
        """Get current volume percentage from audio sink."""
        return audio.get_current_volume()

    def _get_current_brightness(self) -> int | None:
        """Get current screen brightness percentage."""
        return display.get_current_brightness()

    def _collect_now_playing_text(self) -> str:
        if not self.config.media_player_entity or not self.config.ha_base_url or not self.config.ha_token:
            return ""
        payload = self._fetch_media_player_state()
        if payload is None:
            return ""
        return self._format_now_playing(payload)

    def _build_ha_ssl_context(self) -> ssl.SSLContext | None:
        base_url = self.config.ha_base_url.lower()
        if not base_url.startswith("https"):
            return None
        if self.config.ha_verify_ssl:
            return ssl.create_default_context()
        return ssl._create_unverified_context()

    def _fetch_media_player_state(self) -> dict[str, Any] | None:
        entity = self.config.media_player_entity
        if not entity:
            return None
        url = f"{self.config.ha_base_url.rstrip('/')}/api/states/{entity}"
        request = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {self.config.ha_token}",
                "Accept": "application/json",
            },
        )
        open_kwargs: dict[str, Any] = {"timeout": 6}
        if self._ha_ssl_context is not None:
            open_kwargs["context"] = self._ha_ssl_context
        try:
            with urllib.request.urlopen(request, **open_kwargs) as response:  # type: ignore[arg-type]
                data = response.read()
        except urllib.error.HTTPError as exc:
            self._log_now_playing_error(f"now-playing: HA returned {exc.code} for {entity}: {exc.reason}")
            return None
        except urllib.error.URLError as exc:
            self._log_now_playing_error(f"now-playing: HA connection error: {exc}")
            return None
        except Exception as exc:  # pylint: disable=broad-except
            self._log_now_playing_error(f"now-playing: unexpected error: {exc}")
            return None
        try:
            return json.loads(data.decode("utf-8"))
        except json.JSONDecodeError:
            self._log_now_playing_error("now-playing: invalid JSON response from HA")
            return None

    def _log_now_playing_error(self, message: str) -> None:
        now = time.monotonic()
        if now - self._last_now_playing_error < 60:
            return
        self._last_now_playing_error = now
        self.log(message)

    def _handle_overlay_change(self, change: OverlayChange) -> None:
        if not self.overlay_state or not change.changed:
            return
        self._emit_overlay_refresh(change.version, change.reason)

    def _emit_overlay_refresh(self, version: int, reason: str, *, client: mqtt.Client | None = None) -> None:
        payload = json.dumps(
            {
                "version": version,
                "reason": reason,
                "ts": int(time.time()),
            }
        )
        self._safe_publish(client, self.config.topics.overlay_refresh, payload, qos=0, retain=False)

    def _handle_overlay_schedule_state(self, payload: bytes) -> None:
        if not self.overlay_state:
            return
        data = self._decode_json_bytes(payload)
        if not isinstance(data, dict):
            self.log("overlay: ignoring malformed schedules payload")
            return
        change = self.overlay_state.update_schedule_snapshot(data)
        # Also update now playing to ensure it's current when overlay refreshes
        now_playing = self._collect_now_playing_text()
        now_playing_change = self.overlay_state.update_now_playing(now_playing)
        # Handle schedule change (always triggers refresh if changed)
        if change.changed:
            self._handle_overlay_change(change)
        # Handle now playing change separately (only if schedule didn't change)
        elif now_playing_change.changed:
            self._handle_overlay_change(now_playing_change)

    def _handle_overlay_active_event(self, event_type: str, payload: bytes) -> None:
        if not self.overlay_state:
            return
        data = self._decode_json_bytes(payload)
        change = self.overlay_state.update_active_event(event_type, data if isinstance(data, dict) else None)
        self._handle_overlay_change(change)

    def _handle_overlay_info_card(self, payload: bytes) -> None:
        if not self.overlay_state:
            return
        data = self._decode_json_bytes(payload)
        if not isinstance(data, dict):
            return
        state = str(data.get("state") or "").lower()
        if state == "clear":
            change = self.overlay_state.update_info_card(None)
        else:
            card_payload: dict[str, Any] = {}
            for key in ("text", "category", "title", "type", "state"):
                value = data.get(key)
                if value is not None:
                    card_payload[key] = value
            alarms_payload = data.get("alarms")
            if isinstance(alarms_payload, list):
                card_payload["alarms"] = alarms_payload
            card_payload["ts"] = data.get("ts") or time.time()
            change = self.overlay_state.update_info_card(card_payload)
        self._handle_overlay_change(change)

    def stop_overlay_server(self) -> None:
        if self._overlay_http:
            self._overlay_http.stop()

    def _handle_overlay_stop_request(self, event_id: str) -> None:
        payload = json.dumps({"action": "stop", "event_id": event_id})
        self._safe_publish(None, self.assistant_topics.command, payload, qos=1, retain=False)

    def _handle_overlay_snooze_request(self, event_id: str, minutes: int) -> None:
        payload = json.dumps({"action": "snooze", "event_id": event_id, "minutes": minutes})
        self._safe_publish(None, self.assistant_topics.command, payload, qos=1, retain=False)

    def _handle_overlay_delete_alarm_request(self, event_id: str) -> None:
        payload = json.dumps({"action": "delete_alarm", "event_id": event_id})
        self._safe_publish(None, self.assistant_topics.command, payload, qos=1, retain=False)
        if self.overlay_state:
            snapshot = self.overlay_state.snapshot()
            if not snapshot.alarms:
                change = self.overlay_state.update_info_card(None)
                self._handle_overlay_change(change)

    def _handle_overlay_pause_alarm_request(self, event_id: str) -> None:
        payload = json.dumps({"action": "pause_alarm", "event_id": event_id})
        self._safe_publish(None, self.assistant_topics.command, payload, qos=1, retain=False)

    def _handle_overlay_resume_alarm_request(self, event_id: str) -> None:
        payload = json.dumps({"action": "resume_alarm", "event_id": event_id})
        self._safe_publish(None, self.assistant_topics.command, payload, qos=1, retain=False)

    def _handle_overlay_complete_reminder_request(self, event_id: str) -> None:
        payload = json.dumps({"action": "complete_reminder", "event_id": event_id})
        self._safe_publish(None, self.assistant_topics.command, payload, qos=1, retain=False)

    def _handle_overlay_delay_reminder_request(self, event_id: str, seconds: int) -> None:
        payload = json.dumps({"action": "delay_reminder", "event_id": event_id, "seconds": seconds})
        self._safe_publish(None, self.assistant_topics.command, payload, qos=1, retain=False)

    def _handle_overlay_delete_reminder_request(self, event_id: str) -> None:
        payload = json.dumps({"action": "delete_reminder", "event_id": event_id})
        self._safe_publish(None, self.assistant_topics.command, payload, qos=1, retain=False)

    @staticmethod
    def _decode_json_bytes(payload: bytes) -> Any:
        try:
            return json.loads(payload.decode("utf-8"))
        except json.JSONDecodeError:
            return None

    @staticmethod
    def _format_now_playing(payload: dict[str, Any] | None) -> str:
        if not isinstance(payload, dict):
            return ""
        state = str(payload.get("state") or "").lower()
        entity_id = str(payload.get("entity_id") or "")
        attributes = payload.get("attributes") or {}

        def normalize(value: Any) -> str:
            if value is None:
                return ""
            if isinstance(value, str):
                return value.strip()
            return str(value).strip()

        if entity_id.startswith("sensor."):
            clean_state = normalize(payload.get("state"))
            if clean_state and clean_state not in {"unknown", "unavailable"}:
                return clean_state
            return ""

        if state not in {"playing", "on", "buffering", "paused"}:
            return ""

        title = (
            normalize(attributes.get("media_title"))
            or normalize(attributes.get("media_episode_title"))
            or normalize(attributes.get("media_album_name"))
            or normalize(attributes.get("media_content_id"))
        )
        artist = (
            normalize(attributes.get("media_artist"))
            or normalize(attributes.get("media_album_artist"))
            or normalize(attributes.get("media_series_title"))
            or normalize(attributes.get("app_name"))
        )

        if title and artist:
            return f"{artist} — {title}"
        return title or artist or ""

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
    def _format_metric_value(value: int | float | str, precision: int | None) -> str:
        if isinstance(value, (int, float)) and precision is not None:
            format_str = f"{{:.{precision}f}}"
            return format_str.format(value)
        return str(value)

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
        self._publish_version_metadata()

    def _publish_version_metadata(self) -> None:
        """Publish combined version info for HA button attributes."""
        topic = f"{self.config.topics.telemetry}/version_meta"
        payload = json.dumps(
            {
                "installed_version": self.local_version or self.config.sw_version or "unknown",
                "latest_version": self.latest_remote_version or "unknown",
            }
        )
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
        home_button = build_button_entity(
            "Home",
            f"{self.config.hostname}_home",
            self.config.topics.home,
            sanitized_hostname,
        )
        reboot_button = build_button_entity(
            "Reboot",
            f"{self.config.hostname}_reboot",
            self.config.topics.reboot,
            sanitized_hostname,
            entity_category="config",
        )
        update_button = build_button_entity(
            "Update",
            f"{self.config.hostname}_update",
            self.config.topics.update,
            sanitized_hostname,
            entity_category="config",
            availability={
                "topic": self.config.topics.update_availability,
                "pl_avail": "online",
                "pl_not_avail": "offline",
            },
            json_attr_topic=f"{self.config.topics.telemetry}/version_meta",
        )

        volume_control = build_number_entity(
            "Audio Volume",
            f"{self.config.hostname}_control_volume",
            self.config.topics.volume,
            f"{self.config.topics.telemetry}/volume",
            sanitized_hostname,
            min_value=0,
            max_value=100,
            step=1,
            unit_of_measurement="%",
            icon="mdi:volume-high",
            entity_category="config",
        )

        brightness_control = build_number_entity(
            "Screen Brightness",
            f"{self.config.hostname}_control_brightness",
            self.config.topics.brightness,
            f"{self.config.topics.telemetry}/brightness",
            sanitized_hostname,
            min_value=0,
            max_value=100,
            step=1,
            unit_of_measurement="%",
            icon="mdi:brightness-6",
            entity_category="config",
        )

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
            if not descriptor.expose_sensor:
                continue
            cmps_entry: dict[str, Any] = {
                "platform": "sensor",
                "name": descriptor.name,
                "default_entity_id": f"sensor.{sanitized_hostname}_{descriptor.key}",
                "unique_id": f"{self.config.hostname}_telemetry_{descriptor.key}",
                "stat_t": f"{base_topic}/{descriptor.key}",
                "expire_after": expire_after,
            }
            if descriptor.entity_category:
                cmps_entry["entity_category"] = descriptor.entity_category
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

    def on_connect(self, client, _userdata, _flags, reason_code, properties=None):
        if not _is_mqtt_success(reason_code):
            self.log(f"MQTT connection failed (reason={reason_code}, properties={properties})")
            return
        self.log(f"Connected to MQTT (reason={reason_code}); subscribing to topics")
        self._mqtt_client = client
        client.subscribe(self.config.topics.home)
        client.subscribe(self.config.topics.goto)
        client.subscribe(self.config.topics.update)
        client.subscribe(self.config.topics.reboot)
        client.subscribe(self.config.topics.volume)
        client.subscribe(self.config.topics.brightness)
        if self.overlay_state:
            client.subscribe(self.assistant_topics.schedules_state)
            client.subscribe(self.assistant_topics.alarms_active)
            client.subscribe(self.assistant_topics.timers_active)
            client.subscribe(self.assistant_topics.reminders_active)
            client.subscribe(self.assistant_topics.info_card)
        self.publish_device_definition(client)
        self.publish_availability(client, "online")
        self._publish_version_metadata()
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
        if self.overlay_state:
            self._emit_overlay_refresh(self.overlay_state.snapshot().version, "boot", client=client)
            if self._overlay_http:
                self._overlay_http.start()

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
        elif self.overlay_state and msg.topic == self.assistant_topics.info_card:
            self._handle_overlay_info_card(msg.payload)
        elif self.overlay_state and msg.topic in self._overlay_topic_handlers:
            handler = self._overlay_topic_handlers.get(msg.topic)
            if handler:
                handler(msg.payload)
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

    def handle_volume(self, payload: bytes) -> None:
        """Handle volume control command from MQTT."""
        try:
            volume_str = payload.decode("utf-8", errors="ignore").strip()
            volume = int(float(volume_str))
        except (ValueError, TypeError):
            self.log(f"volume: invalid payload '{payload}', expected 0-100")
            return

        if audio.set_volume(volume, play_feedback=self.config.volume_feedback_enabled):
            sink = audio.find_audio_sink()
            self.log(f"volume: set to {volume}% on {sink or 'default sink'}")
            # Publish current volume state
            self._safe_publish(
                None,
                f"{self.config.topics.telemetry}/volume",
                str(volume),
                qos=0,
                retain=True,
            )
        else:
            self.log("volume: failed to set volume - no audio sink found")

    def handle_brightness(self, payload: bytes) -> None:
        """Handle brightness control command from MQTT."""
        try:
            brightness_str = payload.decode("utf-8", errors="ignore").strip()
            brightness = int(float(brightness_str))
        except (ValueError, TypeError):
            self.log(f"brightness: invalid payload '{payload}', expected 0-100")
            return

        if display.set_brightness(brightness):
            device_path = display.find_backlight_device()
            self.log(f"brightness: set to {brightness}% on {device_path or 'default device'}")
            # Publish current brightness state
            self._safe_publish(
                None,
                f"{self.config.topics.telemetry}/brightness",
                str(brightness),
                qos=0,
                retain=True,
            )
        else:
            self.log("brightness: failed to set brightness - no backlight device found")

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
        ]

        try:
            self.log("update: starting update cycle")
            for description, command, cwd in steps:
                if not self._run_step(description, command, cwd):
                    self.log(f"update: aborted during {description}")
                    return
            self.log("update: finished successfully; services restarted by setup.sh")
        finally:
            self.update_lock.release()
            try:
                self.refresh_update_availability()
            except Exception as exc:
                self.log(f"update-check: failed to refresh availability after update: {exc}")

    def _perform_reboot(self) -> None:
        try:
            self.log("reboot: requesting safe reboot")
            subprocess.run(self._safe_reboot_command("mqtt: manual reboot"), check=True)
        except FileNotFoundError as exc:
            self.log(f"reboot: command not found: {exc}")
        except subprocess.CalledProcessError as exc:
            self.log(f"reboot: command failed with exit code {exc.returncode}")
        finally:
            self.reboot_lock.release()

    @staticmethod
    def _safe_reboot_command(reason: str) -> list[str]:
        script = "/opt/pulse-os/bin/safe-reboot.sh"
        if Path(script).exists():
            return ["sudo", script, reason]
        return ["sudo", "reboot", "now"]


def main():
    config = load_config()
    listener = KioskMqttListener(config)
    atexit.register(listener.stop_update_checker)
    atexit.register(listener.stop_telemetry)
    atexit.register(listener.stop_overlay_server)

    callback_kwargs: dict[str, object] = {}
    if hasattr(mqtt, "CallbackAPIVersion"):
        callback_kwargs["callback_api_version"] = mqtt.CallbackAPIVersion.VERSION2
    client = mqtt.Client(**callback_kwargs)
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
