# Pulse Kiosk — Complete Setup Guide

### Raspberry Pi 5 + Pi Touch Display 2

This is the end-to-end recipe we validated together for a **Raspberry Pi OS Lite (64‑bit, Bookworm)** system driving the **Raspberry Pi Touch Display 2** as a **portrait** Home Assistant kiosk. It includes correct rotation, touch alignment, Chromium kiosk, and sunrise/sunset backlight control. Plymouth/splash is intentionally **not** used (keeps boot simple and reliable).

> TL;DR features: clean boot → autologin → X/Openbox → Chromium kiosk, no “half-screen black bar”, accurate touch, auto day/night brightness.


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

cp /opt/pulse-os/pulse.conf.sample /opt/pulse-os/pulse.conf
nano /opt/pulse-os/pulse.conf

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


