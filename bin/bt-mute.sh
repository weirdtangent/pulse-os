#!/usr/bin/env bash
set -euo pipefail

# Script to mute Bluetooth speaker before shutdown
# This prevents the "Disconnected" announcement

MAC="7A:5A:99:6E:50:4D"
SINK="bluez_output.7A_5A_99_6E_50_4D.1"

# Try to mute if PipeWire is available and sink exists
if pw-cli info &>/dev/null; then
  if pactl list sinks short 2>/dev/null | grep -q "$SINK"; then
    # Set volume to 0% to mute before disconnect
    pactl set-sink-volume "$SINK" 0% >/dev/null 2>&1 || true
  fi
fi

