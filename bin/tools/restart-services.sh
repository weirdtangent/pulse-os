#!/usr/bin/env bash
set -euo pipefail

SCRIPT_NAME="$(basename "$0")"
log() { printf '[%s] %s\n' "$SCRIPT_NAME" "$*"; }
warn() { printf '[%s] WARN: %s\n' "$SCRIPT_NAME" "$*" >&2; }

if [[ "${EUID:-0}" -ne 0 ]]; then
    exec sudo --preserve-env=PULSE_USER "$0" "$@"
fi

PULSE_USER="${PULSE_USER:-pulse}"
if ! id "$PULSE_USER" >/dev/null 2>&1; then
    warn "User '$PULSE_USER' does not exist. Set PULSE_USER or create the account."
    exit 1
fi

PULSE_UID="$(id -u "$PULSE_USER")"
USER_RUNTIME_DIR="/run/user/$PULSE_UID"

SYSTEM_UNITS=(
    pulse-assistant.service
    pulse-kiosk-mqtt.service
    pulse-backlight-sun.service
    pulse-bt-mute.service
    pulse-snapclient.service
    pulse-daily-reboot.service
    pulse-daily-reboot.timer
)

USER_UNITS=(
    pulse-assistant-display.service
    bt-autoconnect.service
    bt-autoconnect.timer
)

unit_exists() {
    systemctl list-unit-files "$1" >/dev/null 2>&1
}

unit_enabled() {
    systemctl is-enabled "$1" >/dev/null 2>&1
}

restart_system_unit() {
    local unit="$1"
    if ! unit_exists "$unit"; then
        log "Skipping $unit (unit file not installed)"
        return
    fi
    if ! unit_enabled "$unit"; then
        log "Skipping $unit (disabled)"
        return
    fi
    log "Restarting $unit"
    systemctl restart "$unit"
}

user_systemctl() {
    sudo -u "$PULSE_USER" \
        XDG_RUNTIME_DIR="$USER_RUNTIME_DIR" \
        DBUS_SESSION_BUS_ADDRESS="unix:path=$USER_RUNTIME_DIR/bus" \
        systemctl --user "$@"
}

user_unit_exists() {
    user_systemctl list-unit-files "$1" >/dev/null 2>&1
}

user_unit_enabled() {
    user_systemctl is-enabled "$1" >/dev/null 2>&1
}

restart_user_unit() {
    local unit="$1"
    if ! user_unit_exists "$unit"; then
        log "Skipping user $unit (unit file not installed)"
        return
    fi
    if ! user_unit_enabled "$unit"; then
        log "Skipping user $unit (disabled)"
        return
    fi
    log "Restarting user $unit"
    user_systemctl restart "$unit"
}

log "Reloading systemd units"
systemctl daemon-reload

HAVE_USER_MANAGER=0
if [[ -d "$USER_RUNTIME_DIR" ]]; then
    if user_systemctl daemon-reload >/dev/null 2>&1; then
        HAVE_USER_MANAGER=1
    else
        warn "systemd --user for $PULSE_USER is not running; skipping user units"
    fi
else
    warn "User runtime dir $USER_RUNTIME_DIR missing; skipping user units"
fi

for unit in "${SYSTEM_UNITS[@]}"; do
    restart_system_unit "$unit"
done

if [[ "$HAVE_USER_MANAGER" -eq 1 ]]; then
    for unit in "${USER_UNITS[@]}"; do
        restart_user_unit "$unit"
    done
fi

log "All requested services processed."

