#!/usr/bin/env bash
set -euo pipefail

if [ -f /opt/pulse-os/pulse.conf ]; then
  set -a
  # shellcheck disable=SC1091
  source /opt/pulse-os/pulse.conf
  set +a
fi

if [[ -z "${MQTT_HOST:-}" ]]; then
  echo "kiosk-mqtt-wrapper: MQTT_HOST not set; exiting."
  exit 0
fi

export PYTHONPATH="/opt/pulse-os${PYTHONPATH:+:$PYTHONPATH}"

echo "WRAPPER: launching python…"

exec /usr/bin/python3 -u /opt/pulse-os/bin/kiosk-mqtt-listener.py
