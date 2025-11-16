#!/usr/bin/env bash
set -a
source /opt/pulse-os/pulse.conf
set +a

echo "WRAPPER: launching python…"

exec /usr/bin/python3 -u /opt/pulse-os/bin/kiosk-mqtt-listener.py
