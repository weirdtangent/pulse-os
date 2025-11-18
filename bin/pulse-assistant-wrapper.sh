#!/usr/bin/env bash
set -euo pipefail

if [ -f /opt/pulse-os/pulse.conf ]; then
  # shellcheck disable=SC1091
  source /opt/pulse-os/pulse.conf
fi

if [[ -z "${MQTT_HOST:-}" ]]; then
  echo "pulse-assistant-wrapper: MQTT_HOST not set; exiting."
  exit 0
fi

export PYTHONPATH="/opt/pulse-os${PYTHONPATH:+:$PYTHONPATH}"

exec /usr/bin/python3 -u /opt/pulse-os/bin/pulse-assistant.py "$@"

