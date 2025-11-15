#!/usr/bin/env bash
set -euo pipefail

MAC="7A:5A:99:6E:50:4D"
SINK="bluez_output.7A_5A_99_6E_50_4D.1"
BOOT_SOUND="/opt/pulse-os/sounds/pulse-revived.wav"
FLAG="/run/user/$(id -u)/pulse-boot-sound-played"

# If PipeWire isn't ready yet, just bail quietly and let the next run handle it
if ! pw-cli info &>/dev/null; then
  exit 0
fi

# Try to connect (harmless if already connected)
bluetoothctl connect "$MAC" >/dev/null 2>&1 || true

# Check if the BT sink exists
if pactl list sinks short | grep -q "$SINK"; then
  # Make it default sink
  pactl set-default-sink "$SINK" >/dev/null 2>&1 || true

  # Play boot sound exactly once per boot, through BT sink
  if [ -f "$BOOT_SOUND" ] && [ ! -e "$FLAG" ]; then
    aplay -D "$SINK" "$BOOT_SOUND" >/dev/null 2>&1 || true
    touch "$FLAG"
  fi
fi

