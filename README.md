<p align="center">
  <img src="https://raw.githubusercontent.com/weirdtangent/pulse-os/main/assets/graystorm-pulse_splash.png" alt="Pulse OS social preview" width="640" />
</p>

# Pulse Kiosk — Complete Setup Guide

### Raspberry Pi 5 + Pi Touch Display 2

This is the end-to-end recipe we validated together for a **Raspberry Pi OS Lite (64‑bit, Bookworm)** system driving the **Raspberry Pi Touch Display 2** as a **portrait** Home Assistant kiosk. It includes correct rotation, touch alignment, Chromium kiosk, and sunrise/sunset backlight control.

> TL;DR features: clean boot → autologin → X/Openbox → Chromium kiosk, no “half-screen black bar”, accurate touch, auto day/night brightness.

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

### Desktop Case + Pi 5 Stand
* MakerWorld: <https://makerworld.com/en/models/789481-desktop-case-for-raspberry-pi-7-touch-display-2>

### ReSpeaker Mic Array Plate + Cover (3D printed)
**Plate (PETG green)**
**Cover (PETG translucent)**
* both included in `models/`.

</details>

---

## 0) Prerequisites

* Raspberry Pi 5/CM4/CM5 with **Raspberry Pi Touch Display 2** (DSI ribbon in the “closer” socket).
* Fresh **Raspberry Pi OS Lite (64-bit)** (Trixie, at the moment) written to microSD - I'm using 128GB but it's only 20% full.
  * Setup user "pulse" when imaging OS
  * Can also pre-setup networking, make sure SSH is on, enable auto-login
* Network connectivity (Ethernet or Wi‑Fi).
* SSH access (run `sudo raspi-config nonint do_ssh 0` or enable via imager advanced options).

---

## 1) First‑boot basics

```bash
# Get to the starting line
# login/ssh into your Pulse as "pulse" user
sudo apt update && sudo apt full-upgrade -y
sudo apt install git gh neovim -y
sudo chown pulse:pulse /opt

# Clone and install PulseOS
cd /opt
git clone git@github.com:weirdtangent/pulse-os.git
cd pulse-os

# Create pulse.conf (from pulse.conf.sample, see below for help)
cp pulse.conf.sample pulse.conf
vi pulse.conf
# run the setup script, when ready
#   setup.sh should be also run each time you update or
#   if you change conf file - you don't need <location> on re-runs
#   the Mqtt "Update" button upgrades and re-runs setup for you
./setup.sh <location-name>
```

---

# PulseOS Configuration

PulseOS is configured through a small plain-text file called **pulse.conf**.
This file is unique to each device.

The repository ships with a template named:

    pulse.conf.sample

To configure your device, copy the template:

```bash
cp /opt/pulse-os/pulse.conf.sample /opt/pulse-os/pulse.conf
vi /opt/pulse-os/pulse.conf
```

Every option in this file is optional; PulseOS has safe defaults for all behavior.
But configuring it lets you customize how your Pulse boots, what it displays,
and what services it runs.

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
Dim the screen automatically based on the sunrise/sunset

    PULSE_BACKLIGHT_SUN="true"

Autoconnect to previously-setup Bluetooth (typically for audio)

    PULSE_BLUETOOTH_AUTOCONNECT="true"

Send remote syslogs to remote server
  
    PULSE_REMOTE_LOGGING="true"

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

    PULSE_VOICE_ASSISTANT="false"

Wyoming Whisper (Speech-to-Text) server configuration:

    WYOMING_WHISPER_HOST=""
    WYOMING_WHISPER_PORT="10300"

Wyoming Piper (Text-to-Speech) server configuration:

    WYOMING_PIPER_HOST=""
    WYOMING_PIPER_PORT="10300"

Wyoming OpenWakeWord (Wake Word Detection) server configuration:

    WYOMING_OPENWAKEWORD_HOST=""
    WYOMING_OPENWAKEWORD_PORT="10300"

</details>

---

## Notes & Extras

<details>
  <summary><strong>Home Assistant trusted-network example</strong></summary>
I am choosing to land my Pulse kiosk on a Home Assistant dashboard. To make it easy, so there is no login involved (and long-lived-tokens are a bit tricky when chromium), I setup HA to just trust the kiosk based on internal IP. So in my configuration.yaml, I include this - and just duplicate the IP config for each kiosk you setup:

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
  Want the Nest-style slideshow with fades + clock overlay that the Pulse kiosk now uses? Follow the step-by-step guide in
  
  [host-assistant-photo-frame](docs/home-assistant-photo-frame.md)
  
  * random image helper sensors (command_line + template)
  * installing the custom `pulse-photo-card` resource
  * Lovelace YAML for a full-screen panel view with double-buffered crossfades

  The card never flashes white between photos, keeps the current time/date overlaid, and falls back cleanly if HA loses connection.
</details>

<details>
  <summary><strong>MQTT buttons & telemetry sensors</strong></summary>
  PulseOS can optionally expose Home/Update/Reboot buttons and a full health sensor suite over MQTT discovery. The setup, sudo requirements, topics, and tuning tips now live in [mqtt-and-telemetry](docs/mqtt-and-telemetry.md) so you can keep the README short and still have all the detail when needed.
</details>

<details>
  <summary><strong>Cases & printable accessories</strong></summary>
I am using a fantastic model I found for the Raspberry Pi Touch Display 2 - with attached Pi 5 case:
https://makerworld.com/en/models/789481-desktop-case-for-raspberry-pi-7-touch-display-2#profileId-1868464
I encourage you to use this model, rate it, and boost it!

Also, I'm including in /models the ReSpeaker case and cover that I figured out. At the moment I just glue the stands of that to the cover of the Pi case behind the display, so everything is fairly hidden. One day I'll include some pics.
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
`setup.sh` now also pins the Raspberry Pi Touch Display defaults so you don’t have to edit boot files by hand:
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

Common fixes for this build now live in [troubleshooting](docs/troubleshooting.md) (black-half-screen issues, touch alignment, autologin resets, etc.). Check that file first; send PRs with any new gotchas so we can keep the list growing without bloating the README.

---

<a href="https://buymeacoffee.com/weirdtangent">Buy Me A Coffee</a>

### Build & Quality Status

![Lint](https://img.shields.io/github/actions/workflow/status/weirdtangent/pulse-os/build.yaml?branch=main&label=lint&logo=python)
![Build & Release](https://img.shields.io/github/actions/workflow/status/weirdtangent/pulse-os/build.yaml?branch=main&label=release&logo=githubactions)
![Release](https://img.shields.io/github/v/release/weirdtangent/pulse-os?sort=semver)
![License](https://img.shields.io/github/license/weirdtangent/pulse-os)
