#!/usr/bin/env bash
set -euo pipefail

# Script to mute Bluetooth speaker before shutdown
# This prevents the "Disconnected" announcement

MAC="7A:5A:99:6E:50:4D"
SINK="bluez_output.7A_5A_99_6E_50_4D.1"

# Try multiple approaches to mute - PipeWire might be shutting down
# First try via PipeWire/PulseAudio
if command -v pw-cli >/dev/null 2>&1 && pw-cli info &>/dev/null; then
  if pactl list sinks short 2>/dev/null | grep -q "$SINK"; then
    # Set volume to 0% to mute before disconnect
    pactl set-sink-volume "$SINK" 0% >/dev/null 2>&1 || true
    # Also try to mute the sink
    pactl set-sink-mute "$SINK" 1 >/dev/null 2>&1 || true
  fi
fi

# Also try direct bluetoothctl command as fallback
# Some speakers respect volume commands via bluetoothctl
bluetoothctl set-alias "$MAC" >/dev/null 2>&1 || true

