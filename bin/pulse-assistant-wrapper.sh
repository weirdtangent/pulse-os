#!/usr/bin/env bash
set -euo pipefail

if [ -f /opt/pulse-os/pulse.conf ]; then
  # shellcheck disable=SC1091
  source /opt/pulse-os/pulse.conf
fi

exec /usr/bin/python3 -u /opt/pulse-os/bin/pulse-assistant.py "$@"

