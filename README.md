<p align="center">
  <img src="https://raw.githubusercontent.com/weirdtangent/pulse-os/main/assets/graystorm-pulse_low.jpg" alt="Pulse OS social preview" width="640" />
</p>

# Pulse Kiosk — Complete Setup Guide

### Raspberry Pi 5 + Pi Touch Display 2

This is the end-to-end recipe we validated together for a **Raspberry Pi OS Lite (64‑bit, Bookworm)** system driving the **Raspberry Pi Touch Display 2** as a **portrait** Home Assistant kiosk. It includes correct rotation, touch alignment, Chromium kiosk, and sunrise/sunset backlight control.

> TL;DR features: clean boot → autologin → X/Openbox → Chromium kiosk, no “half-screen black bar”, accurate touch, auto day/night brightness.

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

* Raspberry Pi 5/CM4/CM5 with **Raspberry Pi Touch Display 2** (DSI ribbon in the “other” socket; overlay uses `dsi1`, DRM connector shows up as `DSI-2`).
* Fresh **Raspberry Pi OS Lite (64-bit)** (Trixie, at the moment) written to microSD.
* Network connectivity (Ethernet or Wi‑Fi).
* SSH access (run `sudo raspi-config nonint do_ssh 0` or enable via imager advanced options).

---

## 1) First‑boot basics

```bash
# Get to the starting line
sudo apt update && sudo apt full-upgrade -y
sudo apt install git gh neovim -y
sudo chown pulse:pulse /opt

# Clone and install PulseOS
cd /opt
git clone git@github.com:weirdtangent/pulse-os.git
cd pulse-os

# Create pulse.conf (from pulse.conf.sample, see below for help) and then, when ready
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

## What's Inside pulse.conf

### Kiosk URL
The web page the Pulse loads on boot:

    PULSE_URL="http://homeassistant.local:8123/photo-frame/home?sidebar=hide"

### Watchdog / self-repair timing

    PULSE_REVIVE_INTERVAL=2

Chromium’s live watchdog:

    PULSE_WATCHDOG_URL="http://homeassistant.local:8123/static/icons/favicon.ico"
    PULSE_WATCHDOG_INTERVAL=60   # seconds
    PULSE_WATCHDOG_LIMIT=5       # failures before restarting browser

### Hardware feature toggles

    PULSE_BACKLIGHT_SUN="true"
    PULSE_BLUETOOTH_AUTOCONNECT="true"
    PULSE_REMOTE_LOGGING="true"

### Remote logging target

    PULSE_REMOTE_LOG_HOST="192.168.1.100"
    PULSE_REMOTE_LOG_PORT="5514"

### System user

    PULSE_USER="pulse"


---

## Notes

**HomeAssistant setup**
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

**Case**
I am using a fantastic model I found for the Raspberry Pi Touch Display 2 - with attached Pi 5 case:
https://makerworld.com/en/models/789481-desktop-case-for-raspberry-pi-7-touch-display-2#profileId-1868464
I encourage you to use this model, rate it, and boost it!

Also, I'm including in /models the ReSpeaker case and cover that I figured out. At the moment I just glue the stands of that to the cover of the Pi case behind the display, so everything is fairly hidden. One day I'll include some pics.

**Boot splash**
`setup.sh` deploys the splash assets automatically:
* `assets/graystorm-pulse_splash.png` is installed as the Plymouth theme so Linux boot output stays hidden until X starts.
* `assets/boot-splash.tga` (24-bit, 1280×720) is copied to `/lib/firmware/boot-splash.tga`, and the bootloader is set to `fullscreen_logo=1 fullscreen_logo_name=boot-splash.tga`.
* `assets/boot-splash.rgb` (RGB565, 1280×720) is copied to `/boot/firmware/splash.rgb` for firmware builds that still expect the raw framebuffer format.
* Kernel args such as `quiet splash loglevel=3 vt.global_cursor_default=0 plymouth.ignore-serial-consoles` are enforced so messages and cursors stay out of sight.

Update either asset and rerun `./setup.sh <location>` to refresh the splash on an existing kiosk.

**Objective**

This is buried down here because this is mostly just a fun hobby, and not more serious. But let me explain the direction I'm going.

I have a Synology NAS when i run multiple docker containers, and the plan (already in place, actually) is to run wyoming-piper, wyoming-whisper, and wyoming-openwakeword as containers there, and then try to get my kiosks closer to becoming an interactive Google Nest Display or Amazon Echo Display - perhaps backed by some LLM - especially if there is one specifically great at short, kiosk-like interaction.

I am also a HomeAssitant nerd and amateur photographer, so that explains landing the Pulse kiosks on a PhoteFrame dashboard with a click-to-see basic house dashboard. I can post some of that if people want, but I imagine they can design whatever landing page they want their Pulse to open up.

I would love a start-up screen/animation (rather than scrolling linux), some custom error screens etc.

One step at a time .

## Troubleshooting checklist

These are based on what I've found with the specific hardware setup and Raspberry Pi image I've used - this may or may not be helpful for you, but I welcome suggestions of more to add here!

**Black vertical strip (half screen black):**
You must clear panning before setting fb/rotate:

```bash

DISPLAY=:0 xrandr --output DSI-2 --panning 0x0

DISPLAY=:0 xrandr --fb 720x1280

DISPLAY=:0 xrandr --output DSI-2 --mode 720x1280 --rotate right
```

**Touch inaccurate:**
Flip kernel overlay flag: `invx` ↔ `invy` in `config.txt` overlay line. Reboot.

**X not starting from SSH test:**
X needs a real TTY and a logged‑in user. Use the console autologin path; don’t `startx` over SSH.

**“Can’t open display :0”:**
Display server isn’t running yet or `$DISPLAY` unset. Wait for autologin to start X, or set `DISPLAY=:0` after X is up.

**Chromium warnings (GCM/Vulkan):**
Harmless on minimal builds; ignored in kiosk mode.

**Autologin lost after updates:**

```bash
sudo raspi-config nonint do_boot_behaviour B2
```

**Which connector am I actually using?**

```bash
ls -1 /sys/class/drm | grep DSI   # expect card0-DSI-2
```


