#!/usr/bin/env bash
set -euo pipefail

log() {
    echo "[pulse-snapclient] $*" >&2
}

SNAPCLIENT_BIN="${SNAPCLIENT_BIN:-/usr/bin/snapclient}"
if [ ! -x "$SNAPCLIENT_BIN" ]; then
    log "snapclient binary not found at $SNAPCLIENT_BIN"
    exit 1
fi

SNAPCAST_HOST="${SNAPCAST_HOST:-}"
if [ -z "$SNAPCAST_HOST" ]; then
    log "SNAPCAST_HOST is required (set in /etc/default/pulse-snapclient)"
    exit 1
fi

SNAPCAST_PORT="${SNAPCAST_PORT:-1704}"
SNAPCAST_CONTROL_PORT="${SNAPCAST_CONTROL_PORT:-1705}"
SNAPCLIENT_SOUNDCARD="${SNAPCLIENT_SOUNDCARD:-pulse}"
SNAPCLIENT_LATENCY_MS="${SNAPCLIENT_LATENCY_MS:-}"
SNAPCLIENT_EXTRA_ARGS="${SNAPCLIENT_EXTRA_ARGS:-}"
SNAPCLIENT_HOST_ID="${SNAPCLIENT_HOST_ID:-$(hostname -s 2>/dev/null || hostname)}"

# Ensure PipeWire/Pulse targets are reachable when running as a system service
if [ -z "${XDG_RUNTIME_DIR:-}" ]; then
    XDG_RUNTIME_DIR="/run/user/$UID"
    export XDG_RUNTIME_DIR
fi
if [ -d "$XDG_RUNTIME_DIR" ] && [ -S "$XDG_RUNTIME_DIR/bus" ] && [ -z "${DBUS_SESSION_BUS_ADDRESS:-}" ]; then
    export DBUS_SESSION_BUS_ADDRESS="unix:path=${XDG_RUNTIME_DIR}/bus"
fi

extra_args=()
if [ -n "$SNAPCLIENT_EXTRA_ARGS" ]; then
    # shellcheck disable=SC2206 # we intentionally split on spaces for additional flags
    extra_args=($SNAPCLIENT_EXTRA_ARGS)
fi

cmd=(
    "$SNAPCLIENT_BIN"
    --host "$SNAPCAST_HOST"
    --port "$SNAPCAST_PORT"
    --controlPort "$SNAPCAST_CONTROL_PORT"
    --hostID "$SNAPCLIENT_HOST_ID"
    --soundcard "$SNAPCLIENT_SOUNDCARD"
)

if [ -n "$SNAPCLIENT_LATENCY_MS" ]; then
    cmd+=(--latency "$SNAPCLIENT_LATENCY_MS")
fi

if [ "${#extra_args[@]}" -gt 0 ]; then
    cmd+=("${extra_args[@]}")
fi

exec "${cmd[@]}"

