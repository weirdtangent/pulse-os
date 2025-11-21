#!/usr/bin/env bash
set -euo pipefail

if [ -f /opt/pulse-os/pulse.conf ]; then
  set -a
  # shellcheck disable=SC1091
  source /opt/pulse-os/pulse.conf
  set +a
fi

if [[ -z "${MQTT_HOST:-}" ]]; then
  echo "pulse-assistant-display-wrapper: MQTT_HOST not set; exiting."
  exit 0
fi

wait_for_display() {
  local max_attempts=30
  local attempt=0
  while true; do
    if DISPLAY="${DISPLAY:-:0}" XAUTHORITY="${XAUTHORITY:-/home/${PULSE_USER:-pulse}/.Xauthority}" xset q >/dev/null 2>&1; then
      return 0
    fi
    attempt=$((attempt + 1))
    if [ "$attempt" -ge "$max_attempts" ]; then
      echo "pulse-assistant-display-wrapper: DISPLAY ${DISPLAY:-:0} unavailable after ${max_attempts}s; exiting." >&2
      exit 1
    fi
    sleep 1
  done
}

export PYTHONPATH="/opt/pulse-os${PYTHONPATH:+:$PYTHONPATH}"
export DISPLAY="${DISPLAY:-:0}"
export XAUTHORITY="${XAUTHORITY:-/home/${PULSE_USER:-pulse}/.Xauthority}"

wait_for_display

exec /usr/bin/python3 -u /opt/pulse-os/bin/pulse-assistant-display.py "$@"

