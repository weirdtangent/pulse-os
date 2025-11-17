# MQTT Buttons & Telemetry

PulseOS includes an optional MQTT integration that exposes kiosk controls (Home, Update, Reboot) and diagnostic sensors to Home Assistant via discovery. This page collects the details so the README can stay focused on hardware + install.

---

## MQTT Buttons (`pulse-kiosk-mqtt.service`)

When enabled, the systemd unit publishes three `button` entities under the topic prefix `pulse/<hostname>/kiosk/*`:

| Button | Topic | Action |
| ------ | ----- | ------ |
| `Home` | `pulse/<hostname>/kiosk/home` | Reopens the configured `PULSE_URL` in Chromium. |
| `Update` | `pulse/<hostname>/kiosk/update` | Runs `git pull`, reruns `./setup.sh`, then issues `sudo reboot now`. |
| `Reboot` | `pulse/<hostname>/kiosk/reboot` | Plain `sudo reboot now` with no code pull. |

### Update button requirements

- The script executes inside `/opt/pulse-os`, so the kiosk must have completed at least one manual `./setup.sh <location>` run beforehand.
- The `pulse` user needs passwordless sudo for everything `setup.sh` requires **and** for `reboot`. A minimal rule:

  ```
  # /etc/sudoers.d/pulse-update
  pulse ALL=(root) NOPASSWD: /usr/bin/reboot
  ```

  If `setup.sh` already runs unattended via sudo, you typically just need to add `reboot`.

- There is no payload validation; only expose the buttons on a broker you control.
- Button availability is dynamic: the `Update` button only appears when GitHub’s `VERSION` file is newer than the kiosk’s local version. The kiosk checks 12×/day by default (2/4/6/8/12/24 options via `PULSE_VERSION_CHECKS_PER_DAY`).
- The Update button title automatically changes to `Update to vX.Y.Z` so you know which release will be applied before clicking.

---

## Diagnostic Telemetry Sensors

At ~15‑second intervals (configurable), each kiosk publishes retained MQTT sensors tagged as `diagnostic` entities:

| Entity | Description |
| ------ | ----------- |
| `sensor.pulse_uptime` | Seconds since boot. |
| `sensor.pulse_cpu_usage` | CPU utilization %. |
| `sensor.pulse_cpu_temperature` | SoC temperature °C. |
| `sensor.pulse_memory_usage` | RAM usage %. |
| `sensor.pulse_disk_usage` | Root disk usage %. |
| `sensor.pulse_load_avg_1m` / `_5m` / `_15m` | Standard Linux load averages. |

- Sensors automatically expire if the kiosk stops reporting (HA shows them unavailable).
- Tune the cadence with `PULSE_TELEMETRY_INTERVAL_SECONDS` (minimum 5 s) in `pulse.conf`.
- Because the messages are retained, dashboards continue to show the last value even if HA restarts.

Use these metrics to build health dashboards, automations (e.g., alert when CPU temp > 80 °C), or long-term statistics in the recorder of your choice.

