#!/usr/bin/env bash
set -euo pipefail

# Script to mute Bluetooth speaker before shutdown
# This prevents the "Disconnected" announcement

# Find the Bluetooth sink dynamically (works with any Bluetooth device)
SINK=$(pactl list sinks short 2>/dev/null | grep -m1 "bluez_output" | awk '{print $2}' || true)

# Try multiple approaches to mute - PipeWire might be shutting down
# First try via PipeWire/PulseAudio
if command -v pw-cli >/dev/null 2>&1 && pw-cli info &>/dev/null; then
  if [ -n "$SINK" ] && pactl list sinks short 2>/dev/null | grep -q "$SINK"; then
    # Set volume to 0% to mute before disconnect
    pactl set-sink-volume "$SINK" 0% >/dev/null 2>&1 || true
    # Also try to mute the sink
    pactl set-sink-mute "$SINK" 1 >/dev/null 2>&1 || true
  fi
fi

