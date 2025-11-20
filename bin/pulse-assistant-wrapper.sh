#!/usr/bin/env bash
set -euo pipefail

if [ -f /opt/pulse-os/pulse.conf ]; then
  set -a
  # shellcheck disable=SC1091
  source /opt/pulse-os/pulse.conf
  set +a
fi

if [[ -z "${MQTT_HOST:-}" ]]; then
  echo "pulse-assistant-wrapper: MQTT_HOST not set; exiting."
  exit 0
fi

export PYTHONPATH="/opt/pulse-os${PYTHONPATH:+:$PYTHONPATH}"
export PIP_USER_CONFIG=/home/${PULSE_USER:-pulse}/.config/pip/pip.conf

if [[ -z "${XDG_RUNTIME_DIR:-}" ]]; then
  export XDG_RUNTIME_DIR="/run/user/$(id -u)"
fi

if [[ -z "${DBUS_SESSION_BUS_ADDRESS:-}" && -S "${XDG_RUNTIME_DIR}/bus" ]]; then
  export DBUS_SESSION_BUS_ADDRESS="unix:path=${XDG_RUNTIME_DIR}/bus"
fi

exec /usr/bin/python3 -u /opt/pulse-os/bin/pulse-assistant.py "$@"

