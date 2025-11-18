#!/usr/bin/env bash
set -euo pipefail

MAC="7A:5A:99:6E:50:4D"
SINK="bluez_output.7A_5A_99_6E_50_4D.1"
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

