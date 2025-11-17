# Troubleshooting Checklist

Collected fixes for the Raspberry Pi 5 + Touch Display 2 kiosk build. These are the recurring issues we’ve hit when imaging fresh installs; add more as needed.

---

## Black vertical strip / half-screen black

You must clear panning before setting framebuffer size and rotation:

```bash
DISPLAY=:0 xrandr --output DSI-2 --panning 0x0
DISPLAY=:0 xrandr --fb 720x1280
DISPLAY=:0 xrandr --output DSI-2 --mode 720x1280 --rotate right
```

## Touch input inaccurate

Flip the kernel overlay flag in `/boot/firmware/config.txt`: change `invx` ↔ `invy` on the `dtoverlay=vc4-kms-dsi-ili9881-7inch,...` line. Reboot afterwards.

## Launching X from SSH fails

`startx` over SSH won’t work; Chromium kiosk needs a real TTY and a logged-in user. Stick with the console autologin path configured by `setup.sh`.

## “Can’t open display :0”

Display server isn’t up yet or `$DISPLAY` isn’t exported. Wait for the autologin session to start X, or explicitly `export DISPLAY=:0` once X is running.

## Chromium GCM/Vulkan warnings

Expected on minimal builds; harmless in kiosk mode and safe to ignore.

## Autologin lost after apt upgrade

Re-pin console autologin:

```bash
sudo raspi-config nonint do_boot_behaviour B2
```

## Which display connector am I on?

Run:

```bash
ls -1 /sys/class/drm | grep DSI   # expect card0-DSI-2
```

## Misc notes

- For rotation glitches, confirm `dtoverlay=vc4-kms-dsi-ili9881-7inch,rotation=90,dsi1,swapxy,invx` still exists in `/boot/firmware/config.txt`.
- Plymouth/boot splash misbehavior usually means the units `plymouth-quit.service` and `plymouth-quit-wait.service` lost their overrides; rerun `./setup.sh` to restore them.

Send PRs with any other gotchas so future builders don’t have to rediscover them.

