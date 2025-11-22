#!/usr/bin/env bash
set -u

CONFIG_FILE="/opt/pulse-os/pulse.conf"
[ -f "$CONFIG_FILE" ] && source "$CONFIG_FILE"

export DISPLAY=:0
export XAUTHORITY=/home/$PULSE_USER/.Xauthority

SAFE_REBOOT="/opt/pulse-os/bin/safe-reboot.sh"
LOG="/home/$PULSE_USER/revive.log"

# quick ping to see if HA itself is reachable
if ! curl -sf --max-time 10 "$PULSE_URL" >/dev/null; then
  echo "$(date): HA unreachable" >> "$LOG"
  /usr/bin/pkill -f 'chromium.*--kiosk' || true
  sleep 5
  /usr/bin/systemctl restart kiosk.service 2>/dev/null || \
  /usr/bin/chromium --kiosk "$PULSE_URL" &
  exit 0
fi

# Wayland/X11 agnostic health check
LOG="/home/$PULSE_USER/revive.log"

# Ask Chromium's DevTools if it's alive
if curl -sf http://localhost:9222/json/version | grep -q '"Browser"'; then
  echo "$(date): Chromium healthy" >> "$LOG"
else
  echo "$(date): Chromium unhealthy — restarting" >> "$LOG"
  pkill -f 'chromium.*--kiosk' || true
  sleep 5
  systemctl restart kiosk.service 2>/dev/null || \
  chromium --kiosk "$PULSE_URL" --remote-debugging-port=9222 --remote-debugging-address=0.0.0.0 --allow-running-insecure-content &
  exit 0
fi

# now check if Chromium is alive and rendering properly
PID=$(pgrep -f 'chromium.*--kiosk' || true)

if [ -n "$PID" ]; then
  # look for 'Aw, Snap' in the window title or X window name
  TITLE=$(XAUTHORITY=/home/$PULSE_USER/.Xauthority xdotool getwindowname "$(xdotool search --pid "$PID" | head -1)" 2>/dev/null || echo "")
  if echo "$TITLE" | grep -q "Aw, Snap"; then
    echo "$(date): Chrome is showing 'Aw, Snap' — restarting" >> "$LOG"
    pkill -f 'chromium.*--kiosk'
    sleep 5
    systemctl restart kiosk.service 2>/dev/null || \
    chromium --kiosk "$PULSE_URL" &
  fi
else
  echo "$(date): Chromium not running — requesting reboot" >> "$LOG"
  if command -v "$SAFE_REBOOT" >/dev/null 2>&1; then
    if [[ $EUID -eq 0 ]]; then
      "$SAFE_REBOOT" "revive-pulse: chromium missing"
    else
      sudo "$SAFE_REBOOT" "revive-pulse: chromium missing"
    fi
  else
    /sbin/reboot
  fi
fi

