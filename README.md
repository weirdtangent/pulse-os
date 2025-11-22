<p align="center">
  <img src="https://raw.githubusercontent.com/weirdtangent/pulse-os/main/assets/graystorm-pulse_splash.png" alt="Pulse OS social preview" width="640" />
</p>

# Pulse Kiosk — Complete Setup Guide

## Raspberry Pi 5 + Pi 7" Touch Display 2

A Raspberry Pi–based kiosk OS that lands on Home Assistant dashboards with a scripted setup flow per device. It bundles watchdog/backlight management, MQTT telemetry and actions, Snapcast output, remote logging, and an optional Wyoming voice assistant that can switch between OpenAI and Gemini LLMs on demand.

---

## Jump to…
- [Hardware Guide](#hardware-guide)
- [Prerequisites](#0-prerequisites)
- [First-boot Basics](#1-firstboot-basics)
- [PulseOS Configuration](#pulseos-configuration)
- [Notes & Extras](#notes--extras)
- [Troubleshooting](#troubleshooting-checklist)

### Reference Docs
- [home-assistant-photo-frame](docs/home-assistant-photo-frame.md) — Nest-style Lovelace photo frame + custom card
- [mqtt-and-telemetry](docs/mqtt-and-telemetry.md) — MQTT buttons (Home/Update/Reboot) & diagnostic sensors
- [troubleshooting](docs/troubleshooting.md) — Pi 5 + Touch Display kiosk fixes (black strip, touch, autologin, etc.)

## Hardware Guide
<details>
  <summary><strong>Supported hardware, recommended parts, and printable accessories</strong></summary>

#### As of Nov 2025, $317 plus 3d printed parts (or buy/figure out a case) to build a single Pulse. Or choose your own components - anything that will work with Linux.

### Raspberry Pi 5 — 16GB
* Specs: <https://www.raspberrypi.com/products/raspberry-pi-5/>
* Retailer: <https://www.pishop.us/product/raspberry-pi-5-16gb/?src=raspberrypi>
* Recommended add-ons:
  * Pi Active Cooler: <https://www.pishop.us/product/raspberry-pi-active-cooler/?searchid=0&search_query=374-1>
* Price (11/2025): $131 with active cooler

### Pi Touch Display 2 — 7”
* Specs: <https://www.raspberrypi.com/products/touch-display-2/>
* Retailer: <https://www.pishop.us/product/raspberry-pi-touch-display-2/>
* Price (11/2025): $82

### ReSpeaker Mic Array 3.0 (XVF3000)
* Specs: <https://wiki.seeedstudio.com/respeaker_mic_array_v3.0/>
* Retailer: <https://www.seeedstudio.com/ReSpeaker-Mic-Array-v3-0.html>
* Price (11/2025): $64

### BoomPod Zero mini speaker
* Specs: <https://boompodsusa.com/products/boompods-zero-mini-wireless-bluetooth-5-pocket-size-speaker>
* Retailer: same
* Price (11/2025): $40

### Desktop Case + Pi 5 Stand
* MakerWorld: <https://makerworld.com/en/models/789481-desktop-case-for-raspberry-pi-7-touch-display-2>

### 3d Print Models (see models/ directory)
* ReSpeaker Mic Array Plate (which I glue to the Pi case cover)
* ReSpeaker Mic Array Cover
* BoomPod Zero Cup (which I glue to one of the display leg stands - or both for two pods!)
</details>

---

## 0) Prerequisites

* Raspberry Pi 5/CM4/CM5 with **Raspberry Pi Touch Display 2** (DSI ribbon in the “closer” socket).
* Fresh **Raspberry Pi OS Lite (64-bit)** (Trixie, at the moment) written to microSD - I'm using 128GB but it's only 20% full (so far).
  * Setup "pulse-<location>" hostname and "pulse" user when imaging OS
  * Can also pre-setup networking, make sure SSH is on, enable auto-login
* Network connectivity (Ethernet or Wi‑Fi).
* SSH access (run `sudo raspi-config nonint do_ssh 0` or enable via imager advanced options).

---

## 1) First‑boot basics

1. **Update Raspberry Pi OS and install prerequisites.**
   ```bash
   sudo apt update && sudo apt full-upgrade -y
   sudo apt install -y git gh neovim
   sudo chown pulse:pulse /opt
   ```
2. **Clone PulseOS under `/opt` and open the repo.**
   ```bash
   cd /opt
   git clone https://github.com/weirdtangent/pulse-os.git
   cd pulse-os
   ```
3. **Create `pulse.conf` from the sample and edit it for this kiosk.**
   ```bash
   cp pulse.conf.sample pulse.conf
   vi pulse.conf
   ```
4. **Run the setup script with a location slug (first boot only).**
   ```bash
   ./setup.sh <location-name>
   ```
   Re-run `./setup.sh` after changing `pulse.conf` or pulling new code (omit the location on repeat runs). The MQTT “Update” button performs the same update+setup flow remotely.

---

# PulseOS Configuration

Each kiosk reads `/opt/pulse-os/pulse.conf`. Copy the sample, edit the values that matter for this device, then rerun `./setup.sh`.

```bash
cp /opt/pulse-os/pulse.conf.sample /opt/pulse-os/pulse.conf
vi /opt/pulse-os/pulse.conf
```

All keys are optional, but filling out the relevant sections keeps boot, kiosk, MQTT, and assistant services aligned with your environment.

### Quick config verification

After editing `pulse.conf`, run the connectivity check to confirm the services you referenced are reachable:

```bash
bin/tools/verify-conf.py --config /opt/pulse-os/pulse.conf
```

It loads the config (or the path you pass with `--config`) and:
- Connects to the MQTT broker using your credentials.
- Sends a single RFC5424 syslog line if remote logging is enabled.
- Calls each configured Wyoming endpoint (openWakeWord/Whisper/Piper) and, when possible, performs a short functional probe.
- Tests `HOME_ASSISTANT_BASE_URL`/`HOME_ASSISTANT_TOKEN` by calling `/api/`.

Any failure is printed with remediation text and the script exits non-zero so you can gate deployments on it if desired.

<details>
  <summary><strong>Explore pulse.conf options</strong></summary>

### Pulse version
This lets system tools know what version of Pulse you are on, keep this
in your pulse.conf so system things will work correctly

  PULSE_VERSION=<from VERSION file in repo>

### System user
The linux user with auto-login turned on and is running all of the Pulse
system. Simplier to just to keep this as "pulse"

    PULSE_USER="pulse"

### Kiosk URL
The web page the Pulse loads on boot. This is also what the "Home" Mqtt button
will always return your pulse to.

    PULSE_URL="http://homeassistant.local:8123/photo-frame/home?sidebar=hide"

### Watchdog / self-repair timing
Cron schedule to watch for a broken network, crashed X11, or dead chormium (Aw, Snap)
and restart if needed (minutes)

    PULSE_REVIVE_INTERVAL=2

Chromium’s live watchdog:

    PULSE_WATCHDOG_URL="http://homeassistant.local:8123/static/icons/favicon.ico"
    PULSE_WATCHDOG_INTERVAL=60   # seconds
    PULSE_WATCHDOG_LIMIT=5       # failures before restarting browser

### Hardware feature toggles
Automatically adjust screen brightness based on sunrise/sunset. When enabled, the display moves between day/night levels automatically while audio volume stays wherever you set it. When disabled, you can control brightness manually (via MQTT or system controls).

    PULSE_DAY_NIGHT_AUTO="true"

Play a short heartbeat-style double thump after changing the MQTT volume slider. Enabled by default so you can immediately preview the new level; set it to `false` to disable the feedback.

    PULSE_VOLUME_TEST_SOUND="true"

Autoconnect to previously-setup Bluetooth (typically for audio). When enabled, PulseOS automatically connects to your Bluetooth speaker and sends a silent keepalive every 2 minutes to prevent the speaker from auto-powering off.

    PULSE_BLUETOOTH_AUTOCONNECT="true"

See `docs/bluetooth-speakers.md` for a narrated walkthrough of pairing a speaker via `bluetoothctl`.

Send remote syslogs to remote server

    PULSE_REMOTE_LOGGING="true"

Safe reboot guard (prevents infinite reboot loops if multiple watchdogs fire back-to-back). Leave the defaults unless you have very slow boots:

    PULSE_REBOOT_MIN_UPTIME_SECONDS="300"
    PULSE_REBOOT_WINDOW_SECONDS="900"
    PULSE_REBOOT_MAX_COUNT="3"

### Remote logging target
For remote systlog monitoring, only needed if TRUE set for PULSE_REMOTE_LOGGING

    PULSE_REMOTE_LOG_HOST="192.168.1.100"
    PULSE_REMOTE_LOG_PORT="5514"

### Mqtt
Optional, for Pulse to connect to Mqtt server (for HomeAssistant integration)

    MQTT_HOST="mosquitto.local"
    MQTT_PORT="1883"

### Pulse Version Checks
For Mqtt version checks - to enable the "Upgrade" button when a new version is available
2,4,6,8,12, or 24 checks per day

    PULSE_VERSION_CHECKS_PER_DAY=12

### Pulse Telemetry Reporting
For Mqtt telemetry - how often Pulse should send stats to Mqtt for HomeAssistant (seconds)

    PULSE_TELEMETRY_INTERVAL_SECONDS=15

### Voice Assistant (Wyoming Protocol)
Enable voice assistant features including wake word detection, speech-to-text, and text-to-speech.
Requires Wyoming protocol servers (typically running as Docker containers on a NAS or server).
When `PULSE_VOICE_ASSISTANT="true"`, `setup.sh` installs the Python `wyoming` client (via `pip --break-system-packages`) so the assistant service can talk to your remote models. If you install manually, run:

```bash
sudo -u pulse python3 -m pip install --user --break-system-packages wyoming
```

    PULSE_VOICE_ASSISTANT="false"

Wyoming Whisper (Speech-to-Text) server configuration:

    WYOMING_WHISPER_HOST=""
    WYOMING_WHISPER_PORT="10300"

Wyoming Piper (Text-to-Speech) server configuration:

    WYOMING_PIPER_HOST=""
    WYOMING_PIPER_PORT="10200"

Wyoming OpenWakeWord (Wake Word Detection) server configuration:

    WYOMING_OPENWAKEWORD_HOST=""
    WYOMING_OPENWAKEWORD_PORT="10400"

</details>

---

## Notes & Extras

### Voice Assistant (Preview)

The `pulse-assistant` daemon streams wake audio to your Wyoming servers, calls the configured LLM, then speaks and displays the reply. Configure `PULSE_ASSISTANT_*`, `WYOMING_*`, and `OPENAI_*`/`GEMINI_*` in `pulse.conf`, rerun `./setup.sh`, and review [`docs/voice-assistant.md`](docs/voice-assistant.md) for deployment diagrams.

#### Dual wake-word pipelines

Map each wake word to the local pipeline or the Home Assistant pipeline:

| Variable | Description |
| --- | --- |
| `PULSE_ASSISTANT_WAKE_WORDS_PULSE` | Comma-separated list for local LLM flow (e.g., `hey_jarvis`). |
| `PULSE_ASSISTANT_WAKE_WORDS_HA` | List for HA Assist (e.g., `hey_house,hey_nabu`). |
| `PULSE_ASSISTANT_WAKE_ROUTES` | Optional explicit mapping (`hey_jarvis=pulse,hey_house=home_assistant`). |

`assistant/state` always includes the active pipeline so dashboards can display which route handled the request.

#### Wake trigger level while Pulse is speaking

When the kiosk is already playing audio we enforce a higher openWakeWord trigger level so it does not react to its own speech. Tune `PULSE_ASSISTANT_SELF_AUDIO_TRIGGER_LEVEL` (default **7**) if you need to balance false positives vs. responsiveness:

- Raise the value if music at moderate volume still wakes Jarvis.
- Lower it (minimum **2**) if you often say the wake word softly while the kiosk is making noise.
- When Home Assistant access is configured and `PULSE_MEDIA_PLAYER_ENTITY` points at your Music Assistant player, the assistant auto-pauses that entity as soon as you say the wake word and resumes playback ~2 s after the spoken response finishes.

#### Voice music controls

With `HOME_ASSISTANT_*` credentials and `PULSE_MEDIA_PLAYER_ENTITY` set, you can ask the Pulse pipeline to control or describe the active Music Assistant player directly (no custom actions required). Example phrases:

- “Pause the music”, “Stop the music”, “Next song”.
- “What song is this?” / “Who is this?” → pulls artist/title from the media player attributes and reads them back.

#### Home Assistant actions, timers, and reminders

Set `HOME_ASSISTANT_BASE_URL` and `HOME_ASSISTANT_TOKEN`, then add `HOME_ASSISTANT_TIMER_ENTITY` / `HOME_ASSISTANT_REMINDER_SERVICE` if you use those helpers. The assistant can then:

- Execute action slugs such as `ha.turn_on:light.kitchen` or `ha.turn_off:switch.projector`.
- Start timers/reminders via `timer.start` and `reminder.create`. If the HA helpers are missing, the built-in scheduler handles both locally.
- Stream wake audio through HA Assist pipelines when a wake word is mapped to `home_assistant`. Pulse falls back to your Piper endpoint if HA does not return TTS audio.

##### Troubleshooting tips
- Run `bin/tools/verify-conf.py` whenever Assist calls fail; it confirms MQTT, Wyoming services, and HA credentials.
- For self-signed HA hosts, keep TLS verification on and provide the CA: set `REQUESTS_CA_BUNDLE=/path/to/ca.pem`, install the same CA into Chromium’s profile with `certutil`, then restart `pulse-kiosk`.
- Store extra environment variables in systemd drop-ins (e.g., `/etc/systemd/system/pulse-assistant.service.d/override.conf`) because `pulse.conf` is regenerated from the sample.
- Confirm custom HA hostnames resolve to the actual HA server; mismatched DNS produces `/api/assist_pipeline/run` 404s.
- Watch `journalctl -u pulse-assistant.service -f` to see which pipeline handled each wake word and whether HA responded.

#### MQTT telemetry & knobs

Every Pulse assistant publishes real-time status and accepts config commands under `pulse/<hostname>/assistant/...`:

| Topic | Description |
| --- | --- |
| `assistant/state` | JSON payload with `state`, `pipeline`, `stage`, and `wake_word`. |
| `assistant/in_progress` | `ON` while a wake-word interaction is running; `OFF` otherwise. |
| `assistant/metrics` | JSON timing info per request (`pipeline`, `wake_word`, per-stage milliseconds). |
| `preferences/wake_sound/set` + `/state` | Turn the wake chime on/off (`on`/`off`). |
| `preferences/speaking_style/set` + `/state` | Pick `relaxed`, `normal`, or `aggressive` for the Pulse pipeline persona. |
| `preferences/wake_sensitivity/set` + `/state` | `low`, `normal`, or `high` (maps to openWakeWord trigger levels 5/3/2). |
| `preferences/llm_provider/set` + `/state` | `openai` or `gemini`; switches the active model without editing `pulse.conf`. |

Use these topics as MQTT selects/switches in Home Assistant or publish to them directly; they are retained so dashboards repopulate immediately after a reboot.

<details>
  <summary><strong>Home Assistant trusted-network example</strong></summary>
I am choosing to land my Pulse kiosk on a Home Assistant dashboard. To make it easy, so there is no login involved (and long-lived-tokens are a bit tricky with chromium), I setup HA to just trust the kiosk based on internal IP. So in my configuration.yaml, I include this - and just duplicate the IP config for each kiosk you setup:

```yaml

http:
  use_x_forwarded_for: true

homeassistant:
  auth_providers:
    - type: trusted_networks
      trusted_networks:
        - 192.168.1.150/32
        - 127.0.0.1
      trusted_users:
        192.168.1.150:
          - <HA user's real id>
      allow_bypass_login: true
    - type: homeassistant
```
</details>

<details>
  <summary><strong>Home Assistant photo frame dashboard</strong></summary>
  Follow [home-assistant-photo-frame](docs/home-assistant-photo-frame.md) for the slideshow dashboard used on the kiosk.

  * random image helper sensors (command_line + template)
  * installing the custom `pulse-photo-card` resource
  * Lovelace YAML for a full-screen panel view with double-buffered crossfades

  The card keeps time/date overlaid, prevents white flashes between photos, and handles HA reconnects.
</details>

<details>
  <summary><strong>MQTT buttons & telemetry sensors</strong></summary>
  PulseOS can optionally expose Home/Update/Reboot buttons and a full health sensor suite over MQTT discovery. The setup, sudo requirements, topics, and tuning tips live in [mqtt-and-telemetry](docs/mqtt-and-telemetry.md) so you can keep the README short and still have all the detail when needed.
</details>

<details>
  <summary><strong>Volume feedback thump sample</strong></summary>
  The notification tone that plays after each volume change (and when the assistant starts listening) lives in `assets/notification.wav`. Run `bin/tools/generate-notification-tone.py` if you ever want to regenerate or remix the WAV (for example, to tweak duration or pitch). The script copies the result into `assets/`, and the runtime automatically stages the file under the active user's `XDG_RUNTIME_DIR` as needed.
</details>

<details>
  <summary><strong>Cases & printable accessories</strong></summary>
This enclosure fits the Pi Touch Display 2 with a Pi 5 mounted on the back:
<https://makerworld.com/en/models/789481-desktop-case-for-raspberry-pi-7-touch-display-2#profileId-1868464>

The `/models` directory also includes STL/SCAD files for the ReSpeaker stand, plate, and cover; and BoomPod cup. Mount the stand behind the display to keep the microphone array out of sight. The BookPod can be glued down to one of the legs.
</details>

<details>
  <summary><strong>Boot splash assets</strong></summary>
`setup.sh` deploys the splash assets automatically:
* `assets/graystorm-pulse_splash.png` is installed as the Plymouth theme so Linux boot output stays hidden until X starts.
* `assets/boot-splash.tga` (24-bit, 1280×720) is copied to `/lib/firmware/boot-splash.tga`, and the bootloader is set to `fullscreen_logo=1 fullscreen_logo_name=boot-splash.tga`.
* `assets/boot-splash.rgb` (RGB565, 1280×720) is copied to `/boot/firmware/splash.rgb` for firmware builds that still expect the raw framebuffer format.
* Kernel args such as `quiet splash loglevel=3 vt.global_cursor_default=0 plymouth.ignore-serial-consoles` are enforced so messages and cursors stay out of sight.
* Plymouth quit units are delayed until `graphical.target`, so the splash stays up until X is ready.

Update either asset and rerun `./setup.sh <location>` to refresh the splash on an existing kiosk.
</details>

<details>
  <summary><strong>Touch Display boot config pins</strong></summary>
`setup.sh` also pins the Raspberry Pi Touch Display defaults so you don’t have to edit boot files by hand:
* Adds `dtparam=i2c_arm=on` and `display_auto_detect=0` inside `/boot/firmware/config.txt`.
* Ensures the overlay `dtoverlay=vc4-kms-dsi-ili9881-7inch,rotation=90,dsi1,swapxy,invx` is present.
* Appends `video=DSI-2:720x1280M@60` to `/boot/firmware/cmdline.txt`.
</details>

<details>
  <summary><strong>Objective / future ideas</strong></summary>
This is mostly just a fun hobby, but the direction I’m going includes:

* Running wyoming-piper / wyoming-whisper / wyoming-openwakeword containers on a Synology NAS to add more assistant-like skills.
* Landing kiosks on a Home Assistant photo frame dashboard, with space for future interactive widgets.
* Chasing a custom start-up animation and friendlier error screens when time allows.

One step at a time. 🙂
</details>

## Troubleshooting checklist

Start with the dedicated [troubleshooting guide](docs/troubleshooting.md). The quick checks below cover the most common blockers:

1. **Display issues:** reseat both DSI ribbons, confirm the Pi Touch Display 2 is configured for `DSI-2`, and reboot. The guide includes the exact `config.txt` pins if they were overwritten.
2. **Login loop or blank X session:** make sure the `pulse` user still has automatic console login enabled (`sudo raspi-config nonint do_boot_behaviour B2`), then rerun `./setup.sh`.
3. **Wake-word not triggering:** watch `journalctl -u pulse-assistant.service -f` for detection logs, verify the Wyoming endpoints with `bin/tools/verify-conf.py`, and confirm the microphone command in `pulse.conf`.
4. **MQTT buttons missing:** re-sync `pulse.conf`, rerun `./setup.sh`, and confirm the broker credentials with the verify script before reloading MQTT discovery in Home Assistant.

Document any new fixes in `docs/troubleshooting.md` so the list stays current.

---

<a href="https://buymeacoffee.com/weirdtangent">Buy Me A Coffee</a>

### Build & Quality Status

![Lint](https://img.shields.io/github/actions/workflow/status/weirdtangent/pulse-os/build.yaml?branch=main&label=lint&logo=python)
![Build & Release](https://img.shields.io/github/actions/workflow/status/weirdtangent/pulse-os/build.yaml?branch=main&label=release&logo=githubactions)
![Release](https://img.shields.io/github/v/release/weirdtangent/pulse-os?sort=semver)
![License](https://img.shields.io/github/license/weirdtangent/pulse-os)
