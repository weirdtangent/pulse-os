# MQTT Buttons & Telemetry

PulseOS includes an optional MQTT integration that exposes kiosk controls (Home, Update, Reboot) and diagnostic sensors to Home Assistant via discovery. This page collects the details so the README can stay focused on hardware + install.

---

## MQTT Buttons (`pulse-kiosk-mqtt.service`)

When enabled, the systemd unit publishes three `button` entities under the topic prefix `pulse/<hostname>/kiosk/*`:

| Button | Topic | Action |
| ------ | ----- | ------ |
| `Home` | `pulse/<hostname>/kiosk/home` | Reopens the configured `PULSE_URL` in Chromium. |
| `Update` | `pulse/<hostname>/kiosk/update` | Runs `git pull`, then reruns `./setup.sh` (which restarts all Pulse services unless the kiosk was provisioned with `./setup.sh --no-restart`). |
| `Reboot` | `pulse/<hostname>/kiosk/reboot` | Requests a safe reboot (respecting the guard thresholds). |

### URL Navigation Topics

Two topics allow you to navigate the kiosk to a URL directly from Home Assistant or automations:

| Topic | Action |
| ----- | ------ |
| `pulse/<hostname>/kiosk/url/set` | Navigates Chromium to the provided URL directly, replacing the current page (including overlay). |
| `pulse/<hostname>/kiosk/url-with-overlay/set` | Navigates to the URL but keeps the Pulse overlay (clock, timers, notifications) visible on top. The target URL is displayed in a full-screen background iframe. |

**Example automation** (presence-triggered camera feed with overlay):

```yaml
automation:
  - alias: "Show front yard camera on presence"
    trigger:
      - platform: state
        entity_id: binary_sensor.great_room_presence
        to: "on"
    action:
      - service: mqtt.publish
        data:
          topic: "pulse/pulse-great-room/kiosk/url-with-overlay/set"
          payload: "http://webrtc.example.com:1984/stream.html?src=FrontYard"
```

To return to the normal photo frame view, send a message to the `home` topic or use `url/set` with your `PULSE_URL`.

### Update button requirements

- The script executes inside `/opt/pulse-os`, so the kiosk must have completed at least one manual `./setup.sh <location>` run beforehand.
- The `pulse` user needs passwordless sudo for everything `setup.sh` requires **and** for `reboot`. A minimal rule:

  ```
  # /etc/sudoers.d/pulse-update
  pulse ALL=(root) NOPASSWD: /opt/pulse-os/bin/safe-reboot.sh, /usr/bin/systemctl, /usr/bin/reboot
  ```

  If `setup.sh` already runs unattended via sudo, you typically just need to add `reboot`.

- There is no payload validation; only expose the buttons on a broker you control. The safe reboot wrapper prevents repeated reboots when multiple watchers fire inside a short window.
- Button availability is dynamic: the `Update` button only appears when the latest GitHub release is newer than the kiosk's local version (from git tag). The kiosk checks 12×/day by default (2/4/6/8/12/24 options via `PULSE_VERSION_CHECKS_PER_DAY`).
- The Update button title automatically changes to `Update to vX.Y.Z` so you know which release will be applied before clicking.

Need to restart everything locally without re-running the whole setup flow? Run `sudo bin/tools/restart-services.sh` on the kiosk to bounce the same units the setup/Update path touches.

### MQTT Number Entities (volume & brightness)

Home Assistant also discovers several `number` entities published by the same service:

- `Audio Volume` → topic `pulse/<hostname>/audio/volume/set`
- `Screen Brightness` → topic `pulse/<hostname>/display/brightness/set`
- `Day Brightness` → topic `pulse/<hostname>/display/day_brightness/set`
- `Night Brightness` → topic `pulse/<hostname>/display/night_brightness/set`

Both volume and brightness sliders publish retained telemetry updates, and the telemetry loop re-sends the live hardware levels every ~15 seconds so brightness changes from other services (like the sunrise scheduler) and any volume adjustments stay reflected in Home Assistant. The volume slider automatically plays a short notification beep after each successful adjustment so you can hear the new level immediately; set `PULSE_VOLUME_TEST_SOUND="false"` in `pulse.conf` if you prefer silent changes. The WAV lives in `assets/sounds/notification.wav`—run `bin/tools/generate-notification-tone.py` if you ever want to regenerate or remix it.

#### Day/Night brightness targets

Use the `Day Brightness` and `Night Brightness` number entities to set the sunrise/sunset targets (0–100%). These values are persisted to `pulse.conf` and mirrored into the generated `/etc/pulse-backlight.conf` so the `pulse-backlight-sun` service and future reboots pick them up automatically.

### Overlay font select

`pulse-kiosk-mqtt.service` also publishes a `select` entity named `Overlay Font` so you can change the on-device clock/timer font directly from Home Assistant. The command topic is `pulse/<hostname>/overlay/font/set`, the state topic is `pulse/<hostname>/overlay/font/state`, and the options list is auto-generated from every font reported by `fc-list` plus a `System default` entry that maps back to the `PULSE_OVERLAY_FONT_FAMILY` env var. Installing new fonts on the kiosk (and restarting the MQTT service so discovery is re-published) automatically adds them to the select list. Picking a font triggers an overlay refresh immediately so the change is visible on the kiosk screen within a second or two.

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
| `sensor.pulse_volume` | Current audio volume (%). |
| `sensor.pulse_brightness` | Current screen brightness (%). |
| `sensor.pulse_now_playing` | Friendly “Artist — Title” text mirrored from the kiosk’s configured `media_player`. |

- Sensors automatically expire if the kiosk stops reporting (HA shows them unavailable).
- Tune the cadence with `PULSE_TELEMETRY_INTERVAL_SECONDS` (minimum 5 s) in `pulse.conf`.
- Because the messages are retained, dashboards continue to show the last value even if HA restarts.
- The Now Playing sensor is enabled when you set `HOME_ASSISTANT_BASE_URL`, `HOME_ASSISTANT_TOKEN`, and (optionally) `PULSE_MEDIA_PLAYER_ENTITY`. Leave the entity blank to default to `media_player.<hostname>` (the Snapcast/Music Assistant player Pulse registers). If Music Assistant ends up with multiple viable players, override `PULSE_MEDIA_PLAYER_ENTITY` with the exact entity you want the kiosk to follow (see Troubleshooting for duplicate-player tips).

Use these metrics to build health dashboards, automations (e.g., alert when CPU temp > 80 °C), or long-term statistics in the recorder of your choice.

---

## Setup Summary Snapshot

Every time `setup.sh` completes it renders the configuration summary block that you see in the terminal and publishes the exact same text to a retained MQTT topic: `pulse/<hostname>/setup/summary`. This makes it easy to surface the latest “how is this kiosk configured?” snapshot right inside Home Assistant.

To mirror the persistent-notification workflow shown in the screenshot, create a new automation in the UI, switch to YAML mode, and paste the following template (replace `<hostname>` with your device’s hostname as written to `/etc/pulse-location`):

```
alias: Pulse <location> Setup Summary
description: ""
triggers:
  - trigger: mqtt
    topic: pulse/<hostname>/setup/summary
conditions: []
actions:
  - variables:
      device_name: "{{ trigger.topic.split('/')[1] | default('pulse') }}"
      summary: "{{ trigger.payload | default('PulseOS setup summary unavailable.') }}"
  - action: persistent_notification.create
    data:
      title: "PulseOS: {{ device_name }}"
      message: "{{ summary }}"
mode: single
```

Because the MQTT message is retained, Home Assistant will immediately show the most recent summary after every restart, plus whenever a kiosk reruns `setup.sh`. Feel free to swap the action for markdown cards, mobile push, or whatever flow fits your deployment.

---

## Assistant Alarms, Timers & Commands

Voice alarms and timers surface their state over MQTT so Home Assistant dashboards (and the on-device overlay) stay in sync:

| Topic | Description |
| ----- | ----------- |
| `pulse/<hostname>/assistant/schedules/state` | Retained JSON snapshot with three arrays: `alarms`, `timers`, and `reminders`. Each entry includes the event `id`, `type`, `label`, next-fire timestamp (`next_fire`), duration/target info, repeat cadence, and playback metadata (`mode`, music source, etc.). Use this for list cards or history tracking. |
| `pulse/<hostname>/assistant/alarms/active` | Live updates when an alarm is ringing. Payload format: `{"state": "ringing", "event": {...}}` or `{"state": "idle"}` when cleared. |
| `pulse/<hostname>/assistant/timers/active` | Same as above but for timers (single-use duration events). |
| `pulse/<hostname>/assistant/reminders/active` | Fires when a reminder is ringing (state `ringing`/`idle`). Reminder payloads include the message text plus repeat metadata so dashboards can show “Complete” or “Delay” buttons. |
| `pulse/<hostname>/overlay/refresh` | Non-retained JSON hint published whenever the kiosk overlay layout changes. Payload includes `version`, `reason`, and `ts` (epoch seconds). Frontends (like `pulse-photo-card`) can listen to this topic and fetch the `/overlay` HTML endpoint only when something changed, with a periodic fallback refresh as backup. |

### Command topic

Publish JSON commands to `pulse/<hostname>/assistant/schedules/command` to control alarms/timers from automations, dashboards, or the new overlay buttons. Supported actions:

| Action | Required fields | Notes |
| ------ | ---------------- | ----- |
| `{"action": "create_alarm", "time": "8:30am", "label": "Weekdays", "days": "weekdays", "playback": {"mode": "music", "source": "tidal:playlist:123"}}` | `time` | `days` accepts `weekdays`, `weekends`, `daily`, or comma-separated day names (`mon,wed`). `playback.mode` defaults to `beep`; set to `music` to trigger Music Assistant via `music_entity`/`source`. |
| `{"action": "update_alarm", "event_id": "<id>", "time": "7:00", "days": "mon,tue,wed"}` | `event_id` | Any omitted field stays unchanged. |
| `{"action": "delete_alarm", "event_id": "<id>"}` | `event_id` | Removes the alarm entirely. |
| `{"action": "pause_alarm", "event_id": "<id>"}` | `event_id` | Disables the alarm without deleting it. The overlay’s ⏸️ button issues this command. |
| `{"action": "resume_alarm", "event_id": "<id>"}` | `event_id` | Re-enables a paused alarm. The overlay’s ▶️ button issues this command. |
| `{"action": "start_timer", "duration": "15m", "label": "Bread"}` | `duration` | Duration accepts `90s`, `15m`, `2h`, or raw seconds. |
| `{"action": "add_time", "event_id": "<id>", "seconds": 180}` | `event_id`, `seconds` | Adds time to an existing timer (overlay uses +3 min by default). |
| `{"action": "stop", "event_id": "<id>"}` | `event_id` | Works for either alarms or timers. |
| `{"action": "snooze", "event_id": "<id>", "minutes": 5}` | `event_id` | Snoozes an active alarm, default 5 min. |
| `{"action": "cancel_all", "event_type": "timer"}` | — | Cancels every outstanding timer. |
| `{"action": "create_reminder", "when": "2025-01-01T09:00:00-05:00", "message": "Turn off humidifier", "repeat": {"type": "weekly", "days": [0], "time": "09:00"}}` | `when`, `message` | `repeat` is optional and mirrors the structure persisted in `schedules/state` (`type`: `weekly`, `monthly`, or `interval`). |
| `{"action": "complete_reminder", "event_id": "<id>"}` | `event_id` | Marks the current occurrence complete (repeating reminders advance to the next cadence). |
| `{"action": "delay_reminder", "event_id": "<id>", "seconds": 3600}` | `event_id`, `seconds` | Pushes the active reminder out by the requested offset (the base schedule remains untouched). |
| `{"action": "delete_reminder", "event_id": "<id>"}` | `event_id` | Removes the reminder entirely. |

Responses are implicit—the kiosk publishes the updated state snapshot immediately after every successful command.

### On-screen overlay

`bin/pulse-assistant-display.py` listens to the `alarms/active`, `timers/active`, and `reminders/active` topics. When something is ringing a fullscreen overlay appears with the appropriate action buttons (SNOOZE/+3 MIN for alarms/timers, Complete/+delay for reminders). Each button posts the matching command JSON back to the `schedules/command` topic, so your physical display, automations, and voice assistant all stay coordinated.

### Transcript logging switch

Local transcript/response logging to `journalctl` is controlled by `PULSE_ASSISTANT_LOG_TRANSCRIPTS` (default false). Publishing transcript/response JSON to MQTT is controlled by `PULSE_ASSISTANT_LOG_LLM` or the MQTT switch `pulse/<hostname>/assistant/preferences/log_llm` (`state` mirrors current value, `set` accepts `on`/`off`).

---

## Assistant Preferences (MQTT ↔ Config)

Several assistant and kiosk settings can be changed at runtime via MQTT and are automatically persisted to `pulse.conf`. Home Assistant discovers these as `select` or `switch` entities.

### Preference topics

All assistant preferences use the topic pattern:

```
pulse/<hostname>/assistant/preferences/<key>/set    # Command (publish new value)
pulse/<hostname>/assistant/preferences/<key>/state  # State (retained, reflects current value)
```

### MQTT preference key to config variable mapping

MQTT preference keys are short, API-friendly names. Config variables are the full uppercase names in `pulse.conf`. The table below shows how they map:

| MQTT Preference Key | Config Variable | Value Transform | Notes |
| ------------------- | --------------- | --------------- | ----- |
| `wake_sound` | `PULSE_ASSISTANT_WAKE_SOUND` | `on`/`off` → `true`/`false` | Chime when wake word fires |
| `speaking_style` | `PULSE_ASSISTANT_SPEAKING_STYLE` | passthrough | `relaxed`, `normal`, `aggressive` |
| `wake_sensitivity` | `PULSE_ASSISTANT_WAKE_SENSITIVITY` | passthrough | `low`, `normal`, `high` |
| `ha_pipeline` | `HOME_ASSISTANT_ASSIST_PIPELINE` | passthrough | `ha_` is shorthand for `HOME_ASSISTANT_` |
| `llm_provider` | `PULSE_ASSISTANT_PROVIDER` | passthrough | `llm_` prefix clarifies LLM context |
| `log_llm` | `PULSE_ASSISTANT_LOG_LLM` | `on`/`off` → `true`/`false` | Publish transcripts to MQTT |
| `overlay_font` | `PULSE_OVERLAY_FONT_FAMILY` | passthrough | `font` → `FONT_FAMILY` (CSS terminology) |
| `sound_alarm` | `PULSE_SOUND_ALARM` | passthrough | Sound ID for alarm events |
| `sound_timer` | `PULSE_SOUND_TIMER` | passthrough | Sound ID for timer events |
| `sound_reminder` | `PULSE_SOUND_REMINDER` | passthrough | Sound ID for reminder events |
| `sound_notification` | `PULSE_SOUND_NOTIFICATION` | passthrough | Sound ID for notifications/volume chime |

### Kiosk preferences (via kiosk-mqtt service)

The kiosk MQTT service exposes additional preferences that are persisted the same way:

| MQTT Topic | Config Variable | Notes |
| ---------- | --------------- | ----- |
| `pulse/<hostname>/display/day_brightness/set` | `PULSE_DAY_BRIGHTNESS` | Daytime brightness target (%) |
| `pulse/<hostname>/display/night_brightness/set` | `PULSE_NIGHT_BRIGHTNESS` | Nighttime brightness target (%) |
| `pulse/<hostname>/overlay/font/set` | `PULSE_OVERLAY_FONT_FAMILY` | Overlay font selection |

### Naming rationale

The MQTT keys intentionally differ slightly from config variable names to be shorter and more intuitive for API/automation use:

- **`ha_pipeline`** uses the common `ha_` abbreviation instead of the full `HOME_ASSISTANT_ASSIST_` prefix
- **`llm_provider`** adds the `llm_` prefix (the config var is just `PULSE_ASSISTANT_PROVIDER`) to clarify context in MQTT topics
- **`overlay_font`** maps to `PULSE_OVERLAY_FONT_FAMILY` since the config follows CSS `font-family` terminology

Changes made via MQTT are debounced (2 second delay) before being written to `pulse.conf`, so rapid adjustments don't cause excessive disk I/O. A backup is created automatically before each write.

