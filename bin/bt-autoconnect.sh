#!/usr/bin/env bash
set -euo pipefail

# Bluetooth device MAC address (can be set via PULSE_BT_MAC env var or pulse.conf)
# If not set, script will attempt to find the first connected Bluetooth audio device
MAC="${PULSE_BT_MAC:-}"
BOOT_SOUND="/opt/pulse-os/sounds/pulse-revived.wav"
FLAG="/run/user/$(id -u)/pulse-boot-sound-played"
KEEPALIVE_SOUND="/tmp/pulse-bt-keepalive-v2.wav"
KEEPALIVE_INTERVAL=120  # Send keepalive every 2 minutes
LAST_KEEPALIVE="/run/user/$(id -u)/pulse-bt-last-keepalive"

# Generate the keepalive tone if it doesn't exist.
#
# This must NOT be digital silence. Speakers with auto-power-off decide they are idle
# from the *signal*, not from whether an A2DP socket is open, so a buffer of zero
# samples resets nothing and the speaker sleeps anyway -- and pw-play still exits 0,
# so the failure is completely invisible. It also has to run long enough that PipeWire
# does not suspend the bluez sink again milliseconds later.
#
# So: a 30 Hz tone at ~1.2% full scale for 2s. A small Bluetooth driver cannot
# reproduce 30 Hz audibly, but the DSP sees a real signal and restarts its idle timer.
# Amplitude stays well above the codec noise floor on purpose -- a few LSBs would risk
# SBC quantising it back to silence, which is exactly the bug this replaces.
generate_keepalive_sound() {
  if [ ! -f "$KEEPALIVE_SOUND" ]; then
    python3 -c "
import math
import struct
import sys
import wave

sample_rate = 44100
duration = 2.0
freq = 30.0        # below what a small BT speaker can reproduce
amplitude = 400    # of 32767 -- inaudible, but not zero
fade = int(sample_rate * 0.05)  # 50ms raised-cosine fade, avoids a click

num_samples = int(sample_rate * duration)
out = bytearray()
for i in range(num_samples):
    v = amplitude * math.sin(2.0 * math.pi * freq * i / sample_rate)
    if i < fade:
        v *= 0.5 * (1.0 - math.cos(math.pi * i / fade))
    elif i > num_samples - fade:
        j = num_samples - i
        v *= 0.5 * (1.0 - math.cos(math.pi * j / fade))
    out += struct.pack('<h', int(v))

with wave.open(sys.argv[1], 'wb') as wav_file:
    wav_file.setnchannels(1)  # Mono
    wav_file.setsampwidth(2)  # 16-bit
    wav_file.setframerate(sample_rate)
    wav_file.writeframes(bytes(out))
" "$KEEPALIVE_SOUND" 2>/dev/null || true
  fi
}

# Read the current sink volume percentage, if available
current_sink_volume() {
  local sink="$1"
  local volume
  volume=$(pactl get-sink-volume "$sink" 2>/dev/null | grep -m1 -oE "([0-9]+)%" || true)
  volume=${volume%\%}
  if [ -n "$volume" ]; then
    echo "$volume"
  fi
}

# If PipeWire isn't ready yet, just bail quietly and let the next run handle it
if ! pw-cli info &>/dev/null; then
  exit 0
fi

# If MAC is not set, try to find the first connected Bluetooth device
if [ -z "$MAC" ]; then
  # Get list of connected devices and find first one that looks like a MAC address
  MAC=$(bluetoothctl devices Connected 2>/dev/null | grep -m1 -oE "([0-9A-F]{2}:){5}[0-9A-F]{2}" | head -1 || true)
  if [ -z "$MAC" ]; then
    # No connected device found, try to get first paired device
    MAC=$(bluetoothctl devices Paired 2>/dev/null | grep -m1 -oE "([0-9A-F]{2}:){5}[0-9A-F]{2}" | head -1 || true)
  fi
fi

# If still no MAC, we can't proceed
if [ -z "$MAC" ]; then
  exit 0
fi

# Try to connect, but back off when the target speaker is unreachable.
#
# bt-autoconnect runs every ~15s. A `bluetoothctl connect` to a powered-off
# classic-BT speaker blocks ~60s before failing "Host is down", and each attempt
# grabs the shared 2.4GHz radio long enough (2-4s) to stall WiFi and break
# snapcast time-sync on the room display. So: if the device is already
# connected, skip the call; otherwise only retry on an exponential backoff
# (PULSE_BT_MIN_BACKOFF doubling up to PULSE_BT_MAX_BACKOFF) — a briefly-off
# speaker reconnects quickly, a long-gone one is retried rarely, and the display
# is no longer collateral damage when the speaker is off.
BT_MIN_BACKOFF="${PULSE_BT_MIN_BACKOFF:-15}"
BT_MAX_BACKOFF="${PULSE_BT_MAX_BACKOFF:-300}"
BT_BACKOFF_STATE="/run/user/$(id -u)/pulse-bt-backoff"  # "<next_attempt_epoch> <backoff_secs>"

bt_is_connected() {
  bluetoothctl info "$MAC" 2>/dev/null | grep -q "Connected: yes"
}

if bt_is_connected; then
  # Already connected — clear any backoff state and carry on.
  rm -f "$BT_BACKOFF_STATE" 2>/dev/null || true
else
  now=$(date +%s)
  next_attempt=0
  backoff="$BT_MIN_BACKOFF"
  if [ -f "$BT_BACKOFF_STATE" ]; then
    read -r next_attempt backoff < "$BT_BACKOFF_STATE" 2>/dev/null || true
  fi
  # Guard against an empty/garbled state file.
  case "$next_attempt" in ''|*[!0-9]*) next_attempt=0 ;; esac
  case "$backoff" in ''|*[!0-9]*) backoff="$BT_MIN_BACKOFF" ;; esac

  if [ "$now" -ge "$next_attempt" ]; then
    if bluetoothctl connect "$MAC" >/dev/null 2>&1 && bt_is_connected; then
      rm -f "$BT_BACKOFF_STATE" 2>/dev/null || true
    else
      next_backoff=$(( backoff * 2 ))
      if [ "$next_backoff" -gt "$BT_MAX_BACKOFF" ]; then
        next_backoff="$BT_MAX_BACKOFF"
      fi
      # Schedule the next attempt relative to a *fresh* timestamp: the connect
      # above can block ~60s on a powered-off speaker, so reusing the pre-connect
      # "now" would put next_attempt in the past and defeat short backoffs.
      echo "$(( $(date +%s) + backoff )) $next_backoff" > "$BT_BACKOFF_STATE" 2>/dev/null || true
    fi
  fi
fi

# Ensure we know our XDG_RUNTIME_DIR (needed for pactl/pw-cli)
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"

# Find the Bluetooth sink dynamically (works with any Bluetooth device)
# Sink name format: bluez_output.XX_XX_XX_XX_XX_XX.1
SINK=$(pactl list sinks short 2>/dev/null | grep -m1 "bluez_output" | awk '{print $2}' || true)
CARD=$(pactl list cards short 2>/dev/null | grep -m1 "bluez_card" | awk '{print $2}' || true)

# Check if the BT sink exists
if [ -n "$SINK" ] && pactl list sinks short | grep -q "$SINK"; then
  CURRENT_VOL=$(current_sink_volume "$SINK" || true)
  # Prefer high-quality audio profile when available
  if [ -n "$CARD" ]; then
    if pactl list cards | grep -A10 "$CARD" | grep -q "Profiles:.*a2dp-sink"; then
      pactl set-card-profile "$CARD" a2dp-sink >/dev/null 2>&1 || true
    else
      pactl set-card-profile "$CARD" headset-head-unit >/dev/null 2>&1 || true
    fi
  fi

  # Make it default sink
  pactl set-default-sink "$SINK" >/dev/null 2>&1 || true

  DEFAULT_VOL="${PULSE_BT_DEFAULT_VOLUME:-50}"
  pactl set-sink-mute "$SINK" 0 >/dev/null 2>&1 || true
  TARGET_VOL="$DEFAULT_VOL"
  if [ -n "${CURRENT_VOL:-}" ]; then
    TARGET_VOL="$CURRENT_VOL"
  fi
  pactl set-sink-volume "$SINK" "${TARGET_VOL}%" >/dev/null 2>&1 || true

  # Play boot sound exactly once per boot, through BT sink
  if [ -f "$BOOT_SOUND" ] && [ ! -e "$FLAG" ]; then
    pw-play --target "$SINK" "$BOOT_SOUND" >/dev/null 2>&1 || true
    touch "$FLAG"
  fi

  # Send keepalive to prevent speaker from turning off
  generate_keepalive_sound
  if [ -f "$KEEPALIVE_SOUND" ]; then
    current_time=$(date +%s)
    last_time=0
    if [ -f "$LAST_KEEPALIVE" ]; then
      last_time=$(cat "$LAST_KEEPALIVE" 2>/dev/null || echo "0")
    fi
    time_diff=$((current_time - last_time))

    # Send keepalive if enough time has passed
    if [ "$time_diff" -ge "$KEEPALIVE_INTERVAL" ]; then
      # Play silent keepalive to prevent speaker from auto-powering off
      # The silent sound keeps the audio connection active
      pw-play --target "$SINK" "$KEEPALIVE_SOUND" >/dev/null 2>&1 || true
      echo "$current_time" > "$LAST_KEEPALIVE" 2>/dev/null || true
    fi
  fi
fi
