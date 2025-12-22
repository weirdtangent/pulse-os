<p align="center">
  <img src="https://raw.githubusercontent.com/weirdtangent/pulse-os/main/assets/splash/graystorm-pulse_splash.png" alt="Pulse OS social preview" width="640" />
</p>

# Pulse Kiosk — Complete Setup Guide

## Raspberry Pi 5 + Pi 7" Touch Display 2

Pulse Display Assistant is a Raspberry Pi kiosk OS purpose-built for Home Assistant dashboards. Each device self-provisions a hardened Chromium display with watchdogs, schedule-aware backlighting, and MQTT telemetry/control. A live overlay surfaces internal timers, alarms, reminders, now-playing info, plus on-demand news, weather, and sports snapshots—with clickable notification badges that stay synced to the backend schedule service. An optional Wyoming voice stack adds wake-word control, STT/TTS, and multi-turn conversations, while the LLM layer can hot-swap between OpenAI, Google Gemini, Anthropic Claude, Groq, Mistral AI, and OpenRouter so follow-up questions and automations route through whichever provider and model you prefer, all without leaving the Pulse display.

**What Pulse can do today**

- Hardened Chromium kiosk with watchdogs, self-healing restarts, and MQTT “home/update/reboot” buttons targeted at photo-frame style Lovelace dashboards.
- Overlay timeline that keeps alarms, timers, reminders, calendar events, now-playing info, and badge-driven info cards in sync with the backend scheduler.
- Full alarm/timer/reminder scheduler (manual UI, MQTT, or voice shortcuts) plus remote completion/delay actions from the overlay itself.
- Local ICS/WebCal polling with multi-`VALARM` support, “declined” attendee detection, on-screen calendar cards, and auto-suppressed pop-ups for meetings you said “No” to.
- Optional Wyoming voice stack (wake word, Whisper STT, Piper TTS) with shortcut intents for news/weather/sports and LLM routing between 6 providers: OpenAI, Gemini, Anthropic Claude, Groq, Mistral AI, and OpenRouter.
- MQTT telemetry, syslog streaming, and safe-reboot guardrails for remote monitoring, plus built-in OTA-style updates triggered from Home Assistant.
- Sunrise/sunset-aware backlight control, Bluetooth autoconnect for external speakers, and one-touch audio tests to confirm volume changes.
- Printable hardware accessories (mic stand, speaker cups, Pi 5 case) and ready-made scripts for kiosk recovery, calendar snapshots, and service restarts.

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
- [public-photo-sources](docs/public-photo-sources.md) — Open-licensed image feeds (NASA, Smithsonian, Met, etc.) for `pulse-photo-card`
- [config-reference](docs/config-reference.md) — Comprehensive `pulse.conf` option list with defaults and usage notes

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
4. **Create a Pulse dashboard in Home Assistant (recommended).**
   - From the HA sidebar, add a new dashboard named “Pulse” using the dashboard URL slug `dashboard-pulse`.
   - For the first tab/view, set the title to “Home” with the view URL slug `home` and add the Lovelace cards you want displayed.
   - Pulse defaults to `http://homeassistant.local:8123/dashboard-pulse/home`, so tweak the hostname if needed or point `PULSE_URL` elsewhere in the next step.

5. **Run the setup script with a location slug (first boot only).**
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

All keys are optional, but filling out the relevant sections keeps boot, kiosk, MQTT, and assistant services aligned with your environment. Refer to [docs/config-reference.md](docs/config-reference.md) for every available variable, its default, and practical usage notes.

> 🔒 **Security tip:** `pulse.conf` often contains API keys, MQTT credentials, and private calendar URLs. `setup.sh` automatically sets the file owner to `PULSE_USER` and applies `chmod 600`, but keep the repo staged under `/opt` (not your home directory) and never commit `pulse.conf` back to git or copy it into cloud backups without similar protections.

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

---

## Notes & Extras

All of the voice-assistant tips, MQTT knobs, Home Assistant snippets, printable accessories, and boot splash notes now live in [docs/notes-and-extras.md](docs/notes-and-extras.md). Looking for the built-in wake-word shortcuts? See [docs/assistant-commands.md](docs/assistant-commands.md).

## Troubleshooting checklist

See the dedicated [troubleshooting guide](docs/troubleshooting.md) for the full Pi 5 + Touch Display checklist (black strip, touch calibration, autologin, etc.).
<a href="https://buymeacoffee.com/weirdtangent">Buy Me A Coffee</a>

### Build & Quality Status

![Lint](https://img.shields.io/github/actions/workflow/status/weirdtangent/pulse-os/build.yaml?branch=main&label=lint&logo=python)
![Tests](https://img.shields.io/github/actions/workflow/status/weirdtangent/pulse-os/build.yaml?branch=main&label=tests&logo=pytest)
![CI](https://img.shields.io/github/actions/workflow/status/weirdtangent/pulse-os/build.yaml?branch=main&label=ci&logo=githubactions)
![Release](https://img.shields.io/github/v/release/weirdtangent/pulse-os?sort=semver)
![Python](https://img.shields.io/badge/python-3.13%20%7C%203.14-blue?logo=python&logoColor=white)
![License](https://img.shields.io/github/license/weirdtangent/pulse-os)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)
