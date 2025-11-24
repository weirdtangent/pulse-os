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
- [voice-assistant](docs/voice-assistant.md) — Wyoming pipelines, LLM providers, and real-time news/weather/sports intents
- [assistant-commands](docs/assistant-commands.md) — Built-in “no LLM needed” voice shortcuts (alarms, timers, news, etc.)
- [troubleshooting](docs/troubleshooting.md) — Pi 5 + Touch Display kiosk fixes (black strip, touch, autologin, etc.)
- [notes-and-extras](docs/notes-and-extras.md) — Voice assistant tips, MQTT knobs, hardware accessories, boot splash notes, and other odds & ends

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
   By default `setup.sh` finishes by calling `bin/tools/restart-services.sh` so every kiosk service reloads without a reboot. Pass `--no-restart` if you’re iterating on a single unit and want to restart things manually (you can always run the helper script yourself later).

   Re-run `./setup.sh` after changing `pulse.conf` or pulling new code (omit the location on repeat runs). The MQTT “Update” button performs the same update + setup flow remotely and inherits the automatic restart step.

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

All of the voice-assistant tips, MQTT knobs, Home Assistant snippets, printable accessories, and boot splash notes now live in [docs/notes-and-extras.md](docs/notes-and-extras.md). Looking for the built-in wake-word shortcuts? See [docs/assistant-commands.md](docs/assistant-commands.md).

## Troubleshooting checklist

See the dedicated [troubleshooting guide](docs/troubleshooting.md) for the full Pi 5 + Touch Display checklist (black strip, touch calibration, autologin, etc.).
<a href="https://buymeacoffee.com/weirdtangent">Buy Me A Coffee</a>

### Build & Quality Status

![Lint](https://img.shields.io/github/actions/workflow/status/weirdtangent/pulse-os/build.yaml?branch=main&label=lint&logo=python)
![Build & Release](https://img.shields.io/github/actions/workflow/status/weirdtangent/pulse-os/build.yaml?branch=main&label=release&logo=githubactions)
![Release](https://img.shields.io/github/v/release/weirdtangent/pulse-os?sort=semver)
![License](https://img.shields.io/github/license/weirdtangent/pulse-os)
