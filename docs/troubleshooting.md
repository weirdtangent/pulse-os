# Troubleshooting Checklist

Collected fixes for the Raspberry Pi 5 + Touch Display 2 kiosk build. These are the recurring issues we’ve hit when imaging fresh installs; add more as needed.

---

## Black vertical strip / half-screen black

**Problem**: Display shows a black vertical strip or half the screen is black after boot.

**Solution**: You must clear panning before setting framebuffer size and rotation:

```bash
DISPLAY=:0 xrandr --output DSI-2 --panning 0x0
DISPLAY=:0 xrandr --fb 720x1280
DISPLAY=:0 xrandr --output DSI-2 --mode 720x1280 --rotate right
```

## Touch input inaccurate

**Problem**: Touch input doesn't align with where you tap on the screen.

**Solution**: Flip the kernel overlay flag in `/boot/firmware/config.txt`: change `invx` ↔ `invy` on the `dtoverlay=vc4-kms-dsi-ili9881-7inch,...` line. Reboot afterwards.

## Launching X from SSH fails

**Problem**: Trying to launch X server or Chromium from an SSH session fails.

**Solution**: `startx` over SSH won't work; Chromium kiosk needs a real TTY and a logged-in user. Stick with the console autologin path configured by `setup.sh`.

## "Can't open display :0"

**Problem**: Applications fail with "Can't open display :0" error.

**Solution**: Display server isn't up yet or `$DISPLAY` isn't exported. Wait for the autologin session to start X, or explicitly `export DISPLAY=:0` once X is running.

## Chromium GCM/Vulkan warnings

**Problem**: Chromium shows warnings about GCM (Google Cloud Messaging) or Vulkan in the logs.

**Solution**: Expected on minimal builds; harmless in kiosk mode and safe to ignore.

## Autologin lost after apt upgrade

**Problem**: After running `apt upgrade`, the system no longer auto-logs in the pulse user.

**Solution**: Re-pin console autologin:

```bash
sudo raspi-config nonint do_boot_behaviour B2
```

## Which display connector am I on?

**Problem**: Need to identify which display connector is being used.

**Solution**: Run:

```bash
ls -1 /sys/class/drm | grep DSI   # expect card0-DSI-2
```

## Bluetooth speaker not connecting or auto-powering off

**Problem**: Bluetooth speaker won't connect, or it keeps turning off after periods of inactivity.

**Solution**: The `bt-autoconnect.sh` script automatically handles this:

1. **Manual connection**: Run the script manually to force a connection:
   ```bash
   /home/pulse/bin/bt-autoconnect.sh
   ```

2. **Check service status**: Verify the autoconnect timer is running:
   ```bash
   systemctl --user status bt-autoconnect.timer
   ```

3. **Restart service**: If the timer isn't running:
   ```bash
   systemctl --user restart bt-autoconnect.timer
   systemctl --user enable --now bt-autoconnect.timer
   ```

4. **Speaker auto-power-off**: Many Bluetooth speakers automatically power off after a period of inactivity. PulseOS includes a keepalive mechanism that sends a silent audio signal every 2 minutes to prevent this. The keepalive runs automatically when `PULSE_BLUETOOTH_AUTOCONNECT="true"` is enabled.

5. **If speaker is off**: Make sure the speaker is powered on. The autoconnect script will connect once the speaker is turned on and the script runs (every 15 seconds).

## Snapcast / Music Assistant silent but beeps work

**Problem**: Wake-word beeps and `pactl set-sink-volume` feedback come through the speakers, but Music Assistant or Snapcast streams are silent. `journalctl -u pulse-snapclient.service` shows `PulsePlayer` connecting and then timing out (`No chunk received for 5000ms, disconnecting from pulse.`), and `pactl list sink-inputs` is empty while music is “playing”.

**Solution**: Snapclient uses the PulseAudio/PipeWire backend (`--player pulse`). If the `pipewire-pulse` user services aren’t running, Snapclient silently falls back to ALSA and no audio reaches the kiosk sink.

> **Heads-up about extra IPv6 errors:** Installing the Debian/Ubuntu `snapclient` package auto-enables the distro’s generic `snapclient.service`, which immediately tries to connect to whatever host string it finds (often an IPv6 address you don’t use) and floods the logs with lines like:
>
> ```
> (Connection) Resolving host IP for: fd4b:9231:5453:fb5d:...
> (Connection) Connecting to [fd4b:...]:1704
> (Connection) Failed to connect ..., error: Connection refused
> ```
>
> That noise isn’t our managed service—it’s the stock unit that ships with the package. Disable it once (or rerun `./setup.sh`) so only `pulse-snapclient.service` stays active:
>
> ```bash
> sudo systemctl disable --now snapclient.service
> ./setup.sh <location>   # optional, re-enables pulse-snapclient.service
> ```
> After that, only the Pulse-managed Snapclient will run.

1. **Rerun** `./setup.sh <location>` so the new `configure_snapclient()` path calls `ensure_user_systemd_session`, which starts `pipewire.service`, `pipewire-pulse.service`, and `wireplumber.service` for the `pulse` user even when Bluetooth autoconnect is disabled.
2. **Manual fix** (if you can’t rerun setup right away):
   ```bash
   sudo loginctl enable-linger pulse
   sudo -u pulse \
     XDG_RUNTIME_DIR=/run/user/$(id -u pulse) \
     DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$(id -u pulse)/bus \
     systemctl --user enable --now pipewire.service pipewire-pulse.service wireplumber.service
   sudo systemctl restart pulse-snapclient.service
   ```
3. Verify with `pactl list sink-inputs | grep -B3 snapclient` while Music Assistant plays; you should now see a `snapclient` sink input and the speakers will output the stream.

## Duplicate Music Assistant players or blank Now Playing

**Problem**: Music Assistant shows two players for the same Pulse (for example `media_player.pulse_office` and `media_player.pulse_office_2`), and the default Now Playing entity points at the unavailable one so the overlay/MQTT sensor never updates.

**Solution**: The duplication usually happens when Music Assistant’s **Home Assistant MediaPlayers** provider is enabled. That provider re-imports every HA media player— including the Snapcast player that Pulse already exposed—so MA creates a second entity and Home Assistant renames the duplicate with `_2`.

1. Open Music Assistant → Settings → **Player providers** and disable (or delete) the **Home Assistant MediaPlayers** provider.
2. In Home Assistant → Settings → Devices & Services → Music Assistant, remove any orphaned entities the provider created and keep only the Snapcast player for each kiosk.
3. Rename the remaining entity back to `media_player.<hostname>` if Home Assistant added `_2`.
4. If you *want* to keep multiple Music Assistant players (for multi-protocol hardware), set `PULSE_MEDIA_PLAYER_ENTITY="media_player.whichever_one_you_prefer"` in `pulse.conf`. The kiosk overlay, Now Playing sensor, and alarm music playback will use that entity instead of the auto-detected one.

After either disabling the provider loop or overriding `PULSE_MEDIA_PLAYER_ENTITY`, the Now Playing sensor and `pulse-photo-card` will track the expected Music Assistant player again.

## Display rotation glitches

**Problem**: Display rotation is incorrect or glitchy after boot.

**Solution**: Confirm `dtoverlay=vc4-kms-dsi-ili9881-7inch,rotation=90,dsi1,swapxy,invx` still exists in `/boot/firmware/config.txt`. If missing or incorrect, rerun `./setup.sh` to restore it.

## Plymouth/boot splash misbehavior

**Problem**: Boot splash screen doesn't display correctly or shows errors.

**Solution**: Usually means the units `plymouth-quit.service` and `plymouth-quit-wait.service` lost their overrides. Rerun `./setup.sh` to restore them.

## Reboot loop / watchdog storm

**Problem**: The device keeps rebooting as soon as it finishes booting (watchdogs or the MQTT update button keep firing).

**Solution**: Automatic reboots route through `/opt/pulse-os/bin/safe-reboot.sh`, which enforces:

1. A minimum uptime (`PULSE_REBOOT_MIN_UPTIME_SECONDS`, default 300 s) before any auto-reboot is honored.
2. A rolling window limit (`PULSE_REBOOT_MAX_COUNT` inside `PULSE_REBOOT_WINDOW_SECONDS`, default 3 attempts per 900 s).

When the guard declines a reboot, you'll see `pulse-safe-reboot` entries in syslog explaining why it was skipped. Fix the underlying issue (bad kiosk URL, network outage, runaway MQTT automation), then either wait for the window to clear or reboot manually once you're ready.

If you prefer not to schedule a reboot at 03:00 every day, leave `PULSE_DAILY_REBOOT_ENABLED="false"` (the default) or set it explicitly to `false` and rerun `./setup.sh` to disable the timer.

Send PRs with any other gotchas so future builders don't have to rediscover them.

