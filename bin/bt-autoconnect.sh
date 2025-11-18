#!/usr/bin/env bash
set -euo pipefail

# Bluetooth device MAC address (can be set via PULSE_BT_MAC env var or pulse.conf)
# If not set, script will attempt to find the first connected Bluetooth audio device
MAC="${PULSE_BT_MAC:-}"
BOOT_SOUND="/opt/pulse-os/sounds/pulse-revived.wav"
FLAG="/run/user/$(id -u)/pulse-boot-sound-played"
KEEPALIVE_SOUND="/tmp/pulse-bt-keepalive.wav"
KEEPALIVE_INTERVAL=120  # Send keepalive every 2 minutes
LAST_KEEPALIVE="/run/user/$(id -u)/pulse-bt-last-keepalive"

# Generate keepalive sound file if it doesn't exist (very short, very quiet silence)
generate_keepalive_sound() {
  if [ ! -f "$KEEPALIVE_SOUND" ]; then
    # Generate a 0.1 second silent WAV file using Python
    python3 -c "
import wave
import struct
import sys

sample_rate = 44100
duration = 0.1  # 100ms
num_samples = int(sample_rate * duration)
# Generate silence (all zeros)
samples = b''.join([struct.pack('<h', 0) for _ in range(num_samples)])

with wave.open(sys.argv[1], 'wb') as wav_file:
    wav_file.setnchannels(1)  # Mono
    wav_file.setsampwidth(2)  # 16-bit
    wav_file.setframerate(sample_rate)
    wav_file.writeframes(samples)
" "$KEEPALIVE_SOUND" 2>/dev/null || true
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

# Try to connect (harmless if already connected)
bluetoothctl connect "$MAC" >/dev/null 2>&1 || true

# Find the Bluetooth sink dynamically (works with any Bluetooth device)
# Sink name format: bluez_output.XX_XX_XX_XX_XX_XX.1
SINK=$(pactl list sinks short 2>/dev/null | grep -m1 "bluez_output" | awk '{print $2}' || true)

# Check if the BT sink exists
if [ -n "$SINK" ] && pactl list sinks short | grep -q "$SINK"; then
  # Make it default sink
  pactl set-default-sink "$SINK" >/dev/null 2>&1 || true

  # Restore volume after connection (muted during shutdown to avoid "Disconnected" announcement)
  # Keep volume at 0 for an extended period to avoid "Connected" announcement
  # The speaker may wait for volume to be set before announcing, or announce late
  VOLUME_RESTORED_FLAG="/run/user/$(id -u)/pulse-bt-volume-restored"
  if [ ! -e "$VOLUME_RESTORED_FLAG" ]; then
    # Ensure volume starts at 0 on boot (in case speaker remembers last volume)
    # This prevents "Connected" announcement from being audible
    pactl set-sink-volume "$SINK" 0% >/dev/null 2>&1 || true
    pactl set-sink-mute "$SINK" 1 >/dev/null 2>&1 || true
    # Start volume restoration in background to avoid blocking autoconnect script
    (
      # Wait a reasonable amount of time before restoring volume
      # Some speakers may have volume-controlled announcements, so we keep it muted briefly
      # Wait up to 30 seconds, checking every 5 seconds if X is up
      # Once X is up, wait an additional 10 seconds before restoring volume
      X_UP=false
      i=0
      while [ $i -lt 6 ]; do
        sleep 5
        i=$((i + 1))
        if [ -n "${DISPLAY:-}" ] || [ -S "/tmp/.X11-unix/X0" ] 2>/dev/null; then
          X_UP=true
          break
        fi
      done
      
      # If X is up, wait additional 10 seconds. If not, wait anyway (max 30 seconds total)
      if [ "$X_UP" = true ]; then
        sleep 10  # Additional 10 seconds after X is detected
      else
        # X not detected, wait a bit more anyway
        sleep 5
      fi
      
      # Restore to 50% volume and unmute (adjust volume as needed)
      if pactl list sinks short 2>/dev/null | grep -q "$SINK"; then
        pactl set-sink-mute "$SINK" 0 >/dev/null 2>&1 || true
        pactl set-sink-volume "$SINK" 50% >/dev/null 2>&1 || true
      fi
      touch "$VOLUME_RESTORED_FLAG"
    ) &
  fi

  # Play boot sound exactly once per boot, through BT sink
  if [ -f "$BOOT_SOUND" ] && [ ! -e "$FLAG" ]; then
    aplay -D "$SINK" "$BOOT_SOUND" >/dev/null 2>&1 || true
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
      aplay -D "$SINK" "$KEEPALIVE_SOUND" >/dev/null 2>&1 || true
      echo "$current_time" > "$LAST_KEEPALIVE" 2>/dev/null || true
    fi
  fi
fi

